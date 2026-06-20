#!/usr/bin/env python3
"""
LM feed client for football-wire.

Event-driven match monitor optimised for LLM consumption via stdout.
Each stdout line becomes a Monitor notification. Designed for Claude
Code's Monitor tool but works with any system that watches stdout.

Usage:
    python -m fbw.feed                     # auto-detect live match
    python -m fbw.feed <match_id>          # watch specific match
    python -m fbw.feed --delay 60 <id>     # delay for enrichment + TV sync
    python -m fbw.feed --snapshot          # current state, exit

Enrichment flow:
    Event arrives → buffer (delay seconds)
    Enrichment arrives during delay → update buffered event silently
    Delay expires → emit enriched event
    Enrichment arrives after emit → push correction immediately

The delay serves dual purpose: TV sync AND enrichment window.
"""

import argparse
import json
import sys
import time
import threading
from dataclasses import dataclass, field
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


# --- Buffered event tracking ---

@dataclass
class BufferedEvent:
    """An event waiting to be emitted, trackable for enrichment."""
    event: Event
    raw: dict
    buffered_at: float
    emit_after: float          # time.time() when eligible for push
    minute_value: float
    enrichments_applied: set = field(default_factory=set)  # field names updated
    emitted: bool = False


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
    """Watches events JSONL and enrichments JSONL, manages buffered emission."""

    def __init__(self, events_path: Path, enrichments_path: Path,
                 match: Match, delay: int = 0, stats_interval: int = 15):
        super().__init__()
        self.events_path = events_path
        self.enrichments_path = enrichments_path
        self.match = match
        self.delay = delay
        self.stats_interval = stats_interval

        # File positions
        self.events_position = 0
        self.enrichments_position = 0

        # Event tracking
        self._buffer: dict[str, BufferedEvent] = {}   # event_id → buffered (not yet pushed)
        self._emitted: dict[str, set[str]] = {}       # event_id → set of fields already shown
        self._seen_event_ids: set[str] = set()
        self._seen_dedup_keys: set[str] = set()

        # Timing
        self._last_minute: float = -1.0
        self._last_stats_minute: float = 0.0
        self._lock = threading.Lock()

    # --- Catchup ---

    def catchup(self):
        """Read all existing events, sorted by minute. No delay on catchup."""
        if not self.events_path.exists():
            return
        raw_events = []
        with open(self.events_path) as f:
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
            self.events_position = f.tell()

        # Also catchup enrichments position (skip existing, we'll use timeline)
        if self.enrichments_path.exists():
            with open(self.enrichments_path) as f:
                f.read()  # skip to end
                self.enrichments_position = f.tell()

        # Parse and process through model
        events = []
        for raw in raw_events:
            ev = parse_event(raw, self.match)

            dk = ev.dedup_key
            if dk in self._seen_dedup_keys:
                continue
            self._seen_dedup_keys.add(dk)

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
            # Mark as emitted for enrichment tracking
            if ev.event_id:
                self._emitted[ev.event_id] = set()

        if events and self._last_minute > 0:
            self._last_stats_minute = self._last_minute

    # --- Watchdog callbacks ---

    def on_modified(self, event):
        if not isinstance(event, FileModifiedEvent):
            return
        src = Path(event.src_path).name
        if src == self.events_path.name:
            self._read_new_events()
        elif src == self.enrichments_path.name:
            self._read_new_enrichments()

    # --- Event processing ---

    def _read_new_events(self):
        try:
            with open(self.events_path) as f:
                f.seek(self.events_position)
                for line in f:
                    self._process_event_line(line)
                self.events_position = f.tell()
        except OSError:
            pass

    def _process_event_line(self, line: str):
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

            # Period boundaries reset late-arrival tracking
            if ev.event_type in (EventType.PERIOD_START,):
                self._last_minute = ev.minute.value
            elif ev.minute.value >= 0 and ev.minute.value < self._last_minute:
                ev.is_late = True

            # Apply to match state
            if ev.event_type == EventType.SUB:
                self.match.apply_sub(ev.on_player_id, ev.off_player_id)
            if self.match.stats:
                self.match.stats.update(ev)

            if self.delay > 0 and eid:
                # Buffer for enrichment window
                now = time.time()
                with self._lock:
                    self._buffer[eid] = BufferedEvent(
                        event=ev,
                        raw=raw,
                        buffered_at=now,
                        emit_after=now + self.delay,
                        minute_value=ev.minute.value,
                    )
            else:
                # No delay — emit immediately
                if ev.minute.value >= 0:
                    self._last_minute = max(self._last_minute, ev.minute.value)
                print(format_event(ev, self.match))
                if eid:
                    self._emitted[eid] = set()

        except json.JSONDecodeError:
            pass

    # --- Enrichment processing ---

    def _read_new_enrichments(self):
        if not self.enrichments_path.exists():
            return
        try:
            with open(self.enrichments_path) as f:
                f.seek(self.enrichments_position)
                for line in f:
                    self._process_enrichment_line(line)
                self.enrichments_position = f.tell()
        except OSError:
            pass

    def _process_enrichment_line(self, line: str):
        line = line.strip()
        if not line:
            return
        try:
            enrichment = json.loads(line)
            eid = enrichment.get("event_id", "")
            field_name = enrichment.get("field", "")
            if not eid or not field_name:
                return

            with self._lock:
                if eid in self._buffer:
                    # Event still buffered — update in place, no correction needed
                    buf = self._buffer[eid]
                    # Update the raw dict with new value
                    buf.raw[field_name] = enrichment.get("new")
                    buf.enrichments_applied.add(field_name)
                    # Re-parse the event from updated raw
                    buf.event = parse_event(buf.raw, self.match)

                elif eid in self._emitted:
                    # Event already pushed — emit correction if field not already shown
                    if field_name not in self._emitted[eid]:
                        self._emitted[eid].add(field_name)
                        self._emit_correction(enrichment)
                # else: unknown event, ignore

        except json.JSONDecodeError:
            pass

    def _emit_correction(self, enrichment: dict):
        """Emit a correction line for an already-pushed event."""
        eid = enrichment.get("event_id", "")
        field_name = enrichment.get("field", "")
        minute = enrichment.get("minute", "")
        new_val = enrichment.get("new")

        # For shot coordinates arriving late, re-format the full event
        # from the timeline snapshot if available
        if field_name.startswith("Position") or field_name.startswith("GoalGate"):
            # Accumulate position enrichments — only emit when we have
            # enough for a meaningful update (at least X and Y)
            if eid not in self._emitted:
                return
            applied = self._emitted[eid]
            applied.add(field_name)

            # Wait until we have at least PositionX + PositionY
            if "PositionX" in applied and "PositionY" in applied:
                # Re-read this event from the timeline snapshot
                from .model import ShotPosition
                # Build a minimal description from what we know
                print(f"  >> ENRICHED {minute}: shot coordinates now available")

        elif field_name == "EventDescription":
            old_desc = ""
            new_desc = ""
            old_raw = enrichment.get("old")
            new_raw = enrichment.get("new")
            if isinstance(old_raw, list) and old_raw and isinstance(old_raw[0], dict):
                old_desc = old_raw[0].get("Description", "")
            if isinstance(new_raw, list) and new_raw and isinstance(new_raw[0], dict):
                new_desc = new_raw[0].get("Description", "")
            if new_desc and new_desc != old_desc:
                print(f"  >> ENRICHED {minute}: {new_desc}")

        elif field_name == "IdPlayer":
            new_pid = str(new_val or "")
            player = self.match.player_by_id(new_pid)
            if player:
                print(f"  >> ENRICHED {minute}: player identified as {player.display_name}")

    # --- Flush ---

    def flush_ready(self):
        """Emit buffered events past their delay, with enrichments applied."""
        if not self._buffer:
            return 0
        now = time.time()
        with self._lock:
            ready_ids = [eid for eid, buf in self._buffer.items()
                         if now >= buf.emit_after]

        if not ready_ids:
            return 0

        # Collect and sort by minute
        ready = []
        with self._lock:
            for eid in ready_ids:
                buf = self._buffer.pop(eid)
                # Re-format the event (may have been enriched in buffer)
                formatted = format_event(buf.event, self.match)
                if buf.event.is_late:
                    formatted = f"!! {formatted.lstrip()}"
                ready.append((formatted, buf.minute_value, eid, buf.enrichments_applied))

        ready.sort(key=lambda x: x[1])
        for formatted, mv, eid, applied_fields in ready:
            if mv >= 0:
                self._last_minute = max(self._last_minute, mv)
            print(formatted)
            # Move to emitted, carrying over which enrichments were already applied
            self._emitted[eid] = applied_fields

        return len(ready)

    def flush_all(self):
        """Emit all buffered events sorted by minute."""
        with self._lock:
            items = []
            for eid, buf in self._buffer.items():
                formatted = format_event(buf.event, self.match)
                if buf.event.is_late:
                    formatted = f"!! {formatted.lstrip()}"
                items.append((formatted, buf.minute_value, eid, buf.enrichments_applied))
            self._buffer.clear()

        items.sort(key=lambda x: x[1])
        for formatted, mv, eid, applied_fields in items:
            print(formatted)
            self._emitted[eid] = applied_fields

    # --- Stats ---

    def check_stats_interval(self):
        if self.stats_interval <= 0 or not self.match.stats:
            return
        current = self._last_minute
        if current > 0 and current - self._last_stats_minute >= self.stats_interval:
            minute_str = f"{int(current)}'"

            # Try ESPN stats first (opt-in, more accurate)
            config = get_config()
            if config.sources.espn:
                try:
                    from .espn import get_latest_stats, format_espn_stats
                    espn_row = get_latest_stats(
                        self.match.match_id, config
                    )
                    if espn_row:
                        print(format_espn_stats(espn_row, minute_str))
                        self._last_stats_minute = current
                        return
                except Exception:
                    pass

            # Fallback: our computed stats
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
    """Watch a match — catchup then live events with enrichment support."""
    raw_match_path = config.paths.raw_matches_dir / f"{match_id}.json"
    raw_events_path = config.paths.raw_events_dir / f"{match_id}.jsonl"
    raw_enrichments_path = config.paths.raw_enrichments_dir / f"{match_id}.jsonl"

    # Build match model from raw data
    result = process_match(raw_match_path, raw_events_path)
    if result is None:
        from .process import parse_match, read_raw_json as read_raw
        raw_match = read_raw(raw_match_path)
        if raw_match is None:
            print(f"No data for #{match_id}")
            sys.exit(1)
        match = parse_match(raw_match)
        match.events = []
    else:
        match, _ = result
        match.events = []
        match.on_pitch = set()
        for p in match.home.starters:
            match.on_pitch.add(p.id)
        for p in match.away.starters:
            match.on_pitch.add(p.id)
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

    # Set up handler watching both events and enrichments
    handler = EventFileHandler(
        raw_events_path, raw_enrichments_path, match,
        delay=delay,
        stats_interval=config.display.stats_interval,
    )

    # Catchup existing events
    handler.catchup()
    print("-- live --")

    # Watch for changes in both events and enrichments directories
    observer = Observer()
    observer.schedule(handler, str(config.paths.raw_events_dir), recursive=False)
    observer.schedule(handler, str(config.paths.raw_enrichments_dir), recursive=False)
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
                handler._read_new_events()
                handler._read_new_enrichments()
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
    parser.add_argument("--delay", type=int, help="Delay in seconds (enrichment + TV sync)")
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
