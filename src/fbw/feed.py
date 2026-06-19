#!/usr/bin/env python3
"""
LM feed client for football-wire.

Event-driven match monitor optimised for LLM consumption via stdout.
Each stdout line becomes a Monitor notification. Designed for Claude
Code's Monitor tool but works with any system that watches stdout.

Usage:
    python -m fbw.feed                     # auto-detect live match
    python -m fbw.feed <match_id>          # watch specific match
    python -m fbw.feed --delay 90 <id>     # anti-spoiler delay
    python -m fbw.feed --snapshot          # current state, exit

Output flow:
    preamble → match header → team profiles → catchup events → live events
    Stats blocks emitted on configured interval.
"""

import argparse
import json
import sys
import time
import threading
from functools import partial
from pathlib import Path

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileModifiedEvent

from .config import init_config, get_config
from .process import process_match, read_raw_json, parse_event, get_localized
from .format import (
    format_preamble, format_match_header, format_event,
    format_stats, format_score_line, load_team_profile,
)
from .model import Match, Event, EventType, Minute, Trust

# Force unbuffered output
print = partial(print, flush=True)


# --- Live state reader ---

def read_live(config) -> dict:
    """Read live.json tracking state."""
    path = config.paths.raw_api_dir / "live.json"
    data = read_raw_json(path)
    return data or {"matches": {}}


def find_live_match(config) -> str | None:
    """Find a currently live match."""
    live = read_live(config)
    for mid, info in live.get("matches", {}).items():
        if info.get("status") == 3:
            return mid
    for mid, info in live.get("matches", {}).items():
        if info.get("status") != 0:
            return mid
    return None


# --- Event file watcher ---

class EventFileHandler(FileSystemEventHandler):
    """Watches a JSONL file and emits formatted events on modification."""

    def __init__(self, filepath: Path, match: Match, delay: int = 0,
                 stats_interval: int = 15):
        super().__init__()
        self.filepath = filepath
        self.match = match
        self.delay = delay
        self.stats_interval = stats_interval
        self.position = 0
        self.buffer: list[tuple[float, str, float]] = []
        self._lock = threading.Lock()
        self._seen_event_ids: set[str] = set()
        self._seen_dedup_keys: set[str] = set()
        self._last_minute: float = -1.0
        self._last_stats_minute: float = 0.0

    def catchup(self):
        """Read all existing events, sorted by minute."""
        if not self.filepath.exists():
            return
        raw_events = []
        with open(self.filepath) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                    eid = raw.get("EventId", "")
                    if eid:
                        self._seen_event_ids.add(eid)
                    raw_events.append(raw)
                except json.JSONDecodeError:
                    pass
            self.position = f.tell()

        # Parse and process through our model
        events = []
        for raw in raw_events:
            ev = parse_event(raw, self.match)

            dk = ev.dedup_key
            if dk in self._seen_dedup_keys:
                continue
            self._seen_dedup_keys.add(dk)

            # Apply subs to match state
            if ev.event_type == EventType.SUB:
                self.match.apply_sub(ev.on_player_id, ev.off_player_id)
            if self.match.stats:
                self.match.stats.update(ev)

            events.append(ev)

        # Sort by minute
        events.sort(key=lambda e: (e.minute.value, e.logged_at))

        for ev in events:
            if ev.minute.value >= 0:
                self._last_minute = max(self._last_minute, ev.minute.value)
            print(format_event(ev, self.match))

        # Set stats baseline
        if events and self._last_minute > 0:
            self._last_stats_minute = self._last_minute

    def on_modified(self, event):
        if not isinstance(event, FileModifiedEvent):
            return
        if Path(event.src_path).name != self.filepath.name:
            return
        self._read_new_lines()

    def _read_new_lines(self):
        try:
            with open(self.filepath) as f:
                f.seek(self.position)
                for line in f:
                    self._process_line(line)
                self.position = f.tell()
        except OSError:
            pass

    def _process_line(self, line: str):
        line = line.strip()
        if not line:
            return
        try:
            raw = json.loads(line)

            # EventId dedup
            eid = raw.get("EventId", "")
            if eid:
                if eid in self._seen_event_ids:
                    return
                self._seen_event_ids.add(eid)

            # Parse through model
            ev = parse_event(raw, self.match)

            # Content dedup
            dk = ev.dedup_key
            if dk in self._seen_dedup_keys:
                return
            self._seen_dedup_keys.add(dk)

            # Late arrival detection
            if ev.minute.value >= 0 and ev.minute.value < self._last_minute:
                ev.is_late = True

            # Apply to match state
            if ev.event_type == EventType.SUB:
                self.match.apply_sub(ev.on_player_id, ev.off_player_id)
            if self.match.stats:
                self.match.stats.update(ev)

            formatted = format_event(ev, self.match)

            if self.delay > 0:
                with self._lock:
                    self.buffer.append((
                        time.time() + self.delay,
                        formatted,
                        ev.minute.value,
                    ))
            else:
                if ev.minute.value >= 0:
                    self._last_minute = max(self._last_minute, ev.minute.value)
                print(formatted)

        except json.JSONDecodeError:
            pass

    def flush_ready(self):
        if not self.buffer:
            return 0
        now = time.time()
        with self._lock:
            ready = [item for item in self.buffer if now >= item[0]]
            self.buffer = [item for item in self.buffer if now < item[0]]
        ready.sort(key=lambda x: x[2])
        for _, line_out, sk in ready:
            if sk >= 0:
                self._last_minute = max(self._last_minute, sk)
            print(line_out)
        return len(ready)

    def flush_all(self):
        with self._lock:
            items = sorted(self.buffer, key=lambda x: x[2])
            for _, line_out, _ in items:
                print(line_out)
            self.buffer.clear()

    def check_stats_interval(self):
        if self.stats_interval <= 0 or not self.match.stats:
            return
        current = self._last_minute
        if current > 0 and current - self._last_stats_minute >= self.stats_interval:
            minute_str = f"{int(current)}'"
            print(format_stats(self.match.stats, minute_str))
            self._last_stats_minute = current


# --- Commands ---

def cmd_snapshot(config, match_id: str | None = None):
    """One-shot: print current state and exit."""
    live = read_live(config)
    matches = live.get("matches", {})

    if match_id:
        if match_id in matches:
            info = matches[match_id]
            print(f"[{info.get('status', '?')}] {info.get('home', '?')} "
                  f"{info.get('home_score', '?')}-{info.get('away_score', '?')} "
                  f"{info.get('away', '?')} (#{match_id})")
        else:
            print(f"No live data for #{match_id}")
        return

    if not matches:
        print("No tracked matches")
        return
    for mid, info in matches.items():
        print(f"[{info.get('status', '?')}] {info.get('home', '?')} "
              f"{info.get('home_score', '?')}-{info.get('away_score', '?')} "
              f"{info.get('away', '?')} (#{mid})")


def cmd_watch(config, match_id: str, delay: int = 0):
    """Watch a match — catchup then live events."""
    raw_match_path = config.paths.raw_matches_dir / f"{match_id}.json"
    raw_events_path = config.paths.raw_events_dir / f"{match_id}.jsonl"

    # Build match model from raw data
    result = process_match(raw_match_path, raw_events_path)
    if result is None:
        # Try with empty events (match data only, pre-kickoff)
        from .process import parse_match, read_raw_json as read_raw
        raw_match = read_raw(raw_match_path)
        if raw_match is None:
            print(f"No data for #{match_id}")
            sys.exit(1)
        match = parse_match(raw_match)
        # Reset events since we'll process them live
        match.events = []
    else:
        match, _ = result
        # Reset for live tracking — we'll re-process in catchup
        match.events = []
        # Re-init on_pitch from starters
        match.on_pitch = set()
        for p in match.home.starters:
            match.on_pitch.add(p.id)
        for p in match.away.starters:
            match.on_pitch.add(p.id)
        # Reset stats
        from .model import MatchStats
        match.stats = MatchStats(match.home.abbreviation, match.away.abbreviation)

    # Print preamble
    if config.display.preamble:
        print(format_preamble())

    # Print match header
    print(format_match_header(match))

    # Print team profiles
    for team in [match.home, match.away]:
        profile = load_team_profile(team.abbreviation)
        if profile:
            print()
            print(profile)

    # Wait for events file
    if not raw_events_path.exists():
        print(f"Waiting for events on #{match_id}...")
        while not raw_events_path.exists():
            time.sleep(2)

    # Set up handler
    handler = EventFileHandler(
        raw_events_path, match,
        delay=delay,
        stats_interval=config.display.stats_interval,
    )

    # Catchup existing events
    handler.catchup()
    print("-- live --")

    # Watch for new events
    observer = Observer()
    observer.schedule(handler, str(config.paths.raw_events_dir), recursive=False)
    observer.start()

    try:
        while True:
            if delay > 0:
                handler.flush_ready()

            handler.check_stats_interval()

            # Check for full time
            live = read_live(config)
            info = live.get("matches", {}).get(match_id)
            if info and info.get("status") == 0:
                time.sleep(2)
                handler._read_new_lines()
                handler.flush_all()
                print(f"FULL TIME: {info.get('home', '?')} "
                      f"{info.get('home_score', '?')}-{info.get('away_score', '?')} "
                      f"{info.get('away', '?')} (#{match_id})")
                break

            time.sleep(1)

    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        handler.flush_all()
        observer.stop()
        observer.join(timeout=3)


# --- Main ---

def main():
    parser = argparse.ArgumentParser(description="football-wire LM feed")
    parser.add_argument("match_id", nargs="?", help="Match ID to watch")
    parser.add_argument("--config", help="Path to config file")
    parser.add_argument("--delay", type=int, help="Anti-spoiler delay in seconds")
    parser.add_argument("--snapshot", action="store_true", help="One-shot status")

    args = parser.parse_args()
    config = init_config(args.config)

    if args.snapshot:
        cmd_snapshot(config, args.match_id)
        return

    delay = args.delay if args.delay is not None else config.display.delay

    match_id = args.match_id
    if not match_id:
        match_id = find_live_match(config)
        if not match_id:
            print("No live match found. Specify a match ID.")
            sys.exit(1)
        print(f"Auto-detected: #{match_id}")

    cmd_watch(config, match_id, delay=delay)


if __name__ == "__main__":
    main()
