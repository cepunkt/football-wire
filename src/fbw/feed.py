#!/usr/bin/env python3
"""
LM feed client for football-wire.

Cycle-based match monitor optimised for LLM consumption via stdout.
Runs a fixed-interval cycle (default 10s) that collects events,
applies enrichments, and emits grouped notifications.

Usage:
    python -m fbw.feed                     # auto-detect live match
    python -m fbw.feed <match_id>          # watch specific match
    python -m fbw.feed --delay 30 <id>     # enrichment + TV sync delay
    python -m fbw.feed --cycle 10 <id>     # cycle interval seconds
    python -m fbw.feed --snapshot          # current state, exit

Cycle flow (every N seconds):
    1. Read new events (if flagged by watchdog)
    2. Read new enrichments (if flagged)
    3. Apply enrichments to buffered events
    4. Collect events past their delay
    5. Group by minute
    6. Check stats interval (ESPN if enabled, else computed)
    7. Emit one combined notification
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
from .process import process_match, read_raw_json, parse_event
from .format import (
    format_preamble, format_match_header, format_event,
    format_stats, load_team_profile,
)
from .model import Match, Event, EventType, MatchStats, Trust

# Force unbuffered output
print = partial(print, flush=True)


# --- Buffered event ---

@dataclass
class BufferedEvent:
    """An event waiting to be emitted."""
    event: Event
    raw: dict
    buffered_at: float
    emit_after: float
    minute_value: float
    enrichments_applied: set = field(default_factory=set)


# --- File change watcher (flag-only, no processing) ---

class FileChangeFlag(FileSystemEventHandler):
    """Watchdog handler that just sets flags. Processing happens in the cycle."""

    def __init__(self, events_name: str, enrichments_name: str):
        super().__init__()
        self.events_name = events_name
        self.enrichments_name = enrichments_name
        self.events_changed = False
        self.enrichments_changed = False
        self._lock = threading.Lock()

    def on_modified(self, event):
        if not isinstance(event, FileModifiedEvent):
            return
        name = Path(event.src_path).name
        with self._lock:
            if name == self.events_name:
                self.events_changed = True
            elif name == self.enrichments_name:
                self.enrichments_changed = True

    def consume_flags(self) -> tuple[bool, bool]:
        """Read and reset flags. Called from the cycle."""
        with self._lock:
            ev, en = self.events_changed, self.enrichments_changed
            self.events_changed = False
            self.enrichments_changed = False
        return ev, en


# --- Live state ---

def read_live(config) -> dict:
    path = config.paths.raw_api_dir / "live.json"
    data = read_raw_json(path)
    return data or {"matches": {}}


def find_live_match(config) -> str | None:
    live = read_live(config)
    for mid, info in live.get("matches", {}).items():
        if info.get("status") == 3:
            return mid
    for mid, info in live.get("matches", {}).items():
        if info.get("status") != 0:
            return mid
    return None


# --- Feed engine ---

class FeedEngine:
    """Cycle-based feed engine. One cycle = one notification max."""

    def __init__(self, events_path: Path, enrichments_path: Path,
                 match: Match, delay: int = 0, cycle_interval: int = 10,
                 stats_interval: int = 15, log_path: Path | None = None):
        self.events_path = events_path
        self.enrichments_path = enrichments_path
        self.match = match
        self.delay = delay
        self.cycle_interval = cycle_interval
        self.stats_interval = stats_interval

        # File positions
        self.events_pos = 0
        self.enrichments_pos = 0

        # Enrichment cache: event_id → {field: value, ...}
        self._enrichment_cache: dict[str, dict[str, any]] = {}

        # State tracking
        self._buffer: dict[str, BufferedEvent] = {}
        self._emitted: dict[str, set[str]] = {}
        self._seen_event_ids: set[str] = set()
        self._seen_dedup_keys: set[str] = set()
        self._last_minute: float = -1.0
        self._last_stats_minute: float = 0.0

        # Feed log file (full history, no truncation)
        self.log_path = log_path
        self._log_file = None
        if log_path:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            self._log_file = open(log_path, "a")

    def emit(self, text: str):
        """Emit to stdout (Monitor) and log file."""
        print(text)
        if self._log_file:
            self._log_file.write(text + "\n")
            self._log_file.flush()

    # --- Enrichment cache ---

    def load_enrichment_cache(self):
        """Load all available enrichments into cache. Called at startup
        and refreshed when the enrichments file changes."""
        self._enrichment_cache.clear()
        if not self.enrichments_path.exists():
            return
        with open(self.enrichments_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                    eid = e.get("event_id", "")
                    field = e.get("field", "")
                    if eid and field:
                        if eid not in self._enrichment_cache:
                            self._enrichment_cache[eid] = {}
                        self._enrichment_cache[eid][field] = e.get("new")
                except json.JSONDecodeError:
                    pass
            self.enrichments_pos = f.tell()

    def enrich_raw(self, event_id: str, raw: dict) -> dict:
        """Apply any available enrichments to a raw event dict.
        Called before parsing — works for both catchup and live."""
        if event_id in self._enrichment_cache:
            for field, value in self._enrichment_cache[event_id].items():
                raw[field] = value
        return raw

    # --- Catchup (no delay, immediate) ---

    def catchup(self):
        """Read and emit all existing events. No delay, no buffering.
        Enrichments are applied before parsing for complete data."""
        if not self.events_path.exists():
            return

        # Load enrichment cache FIRST so catchup events get enriched
        self.load_enrichment_cache()

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
            self.events_pos = f.tell()

        # Parse, dedup, process — with enrichments applied
        events = []
        for raw in raw_events:
            eid = raw.get("EventId", "")
            if eid:
                raw = self.enrich_raw(eid, raw)
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

        events.sort(key=lambda e: (e.minute.value, e.logged_at))

        # Emit catchup as compact summary + key events (not every foul)
        # This prevents Monitor truncation on mid-match reconnect
        key_types = {EventType.GOAL, EventType.PENALTY_GOAL, EventType.RED,
                     EventType.SECOND_YELLOW_RED, EventType.YELLOW,
                     EventType.SUB, EventType.VAR, EventType.PERIOD_START,
                     EventType.PERIOD_END}
        shot_types = {EventType.SHOT}

        key_events = []
        for ev in events:
            if ev.minute.value >= 0:
                self._last_minute = max(self._last_minute, ev.minute.value)
            if ev.event_id:
                self._emitted[ev.event_id] = set()

            # Key events: goals, cards, subs, VAR, periods
            if ev.event_type in key_types:
                key_events.append(ev)
            # Shots only if they have coordinates (meaningful)
            elif ev.event_type in shot_types and ev.shot_position:
                key_events.append(ev)

        if key_events:
            print("[Catchup]")
            for ev in key_events:
                self.emit(format_event(ev, self.match))

        if events and self._last_minute > 0:
            self._last_stats_minute = self._last_minute

    # --- Cycle steps ---

    def read_new_events(self):
        """Step 1: Read new event lines from JSONL."""
        if not self.events_path.exists():
            return
        try:
            with open(self.events_path) as f:
                f.seek(self.events_pos)
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        raw = json.loads(line)
                        eid = raw.get("EventId", "")
                        if eid and eid in self._seen_event_ids:
                            continue
                        if eid:
                            self._seen_event_ids.add(eid)

                        ev = parse_event(raw, self.match)
                        dk = ev.dedup_key
                        if dk in self._seen_dedup_keys:
                            continue
                        self._seen_dedup_keys.add(dk)

                        # Period boundary resets
                        if ev.event_type == EventType.PERIOD_START:
                            self._last_minute = ev.minute.value
                        elif ev.minute.value >= 0 and ev.minute.value < self._last_minute:
                            ev.is_late = True

                        # Apply to match state
                        if ev.event_type == EventType.SUB:
                            self.match.apply_sub(ev.on_player_id, ev.off_player_id)
                        if self.match.stats:
                            self.match.stats.update(ev)

                        # Buffer or mark for immediate emit
                        if self.delay > 0 and eid:
                            now = time.time()
                            self._buffer[eid] = BufferedEvent(
                                event=ev, raw=raw,
                                buffered_at=now,
                                emit_after=now + self.delay,
                                minute_value=ev.minute.value,
                            )
                        else:
                            if ev.minute.value >= 0:
                                self._last_minute = max(self._last_minute, ev.minute.value)
                            # Buffer with 0 delay — emits on next cycle
                            if eid:
                                self._buffer[eid] = BufferedEvent(
                                    event=ev, raw=raw,
                                    buffered_at=time.time(),
                                    emit_after=0,
                                    minute_value=ev.minute.value,
                                )

                    except json.JSONDecodeError:
                        pass
                self.events_pos = f.tell()
        except OSError:
            pass

    def read_new_enrichments(self):
        """Step 2: Read new enrichment lines, update cache and buffer."""
        if not self.enrichments_path.exists():
            return
        try:
            with open(self.enrichments_path) as f:
                f.seek(self.enrichments_pos)
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        enrichment = json.loads(line)
                        eid = enrichment.get("event_id", "")
                        field = enrichment.get("field", "")
                        new_val = enrichment.get("new")

                        if not eid or not field:
                            continue

                        # Update cache (available for future re-parses)
                        if eid not in self._enrichment_cache:
                            self._enrichment_cache[eid] = {}
                        self._enrichment_cache[eid][field] = new_val

                        # Apply to buffer or note for emitted events
                        self._apply_enrichment(eid, field, new_val)

                    except json.JSONDecodeError:
                        pass
                self.enrichments_pos = f.tell()
        except OSError:
            pass

    def _apply_enrichment(self, eid: str, field: str, value):
        """Apply enrichment to buffer or track for emitted events."""
        if eid in self._buffer:
            # Still buffered — update raw and re-parse
            buf = self._buffer[eid]
            buf.raw[field] = value
            buf.enrichments_applied.add(field)
            buf.event = parse_event(buf.raw, self.match)
        elif eid in self._emitted:
            # Already pushed — track (correction could be emitted later)
            if field not in self._emitted[eid]:
                self._emitted[eid].add(field)

    def collect_ready(self) -> list[str]:
        """Step 4+5: Collect ready events, group by minute, format."""
        now = time.time()
        ready_ids = [eid for eid, buf in self._buffer.items()
                     if now >= buf.emit_after]

        if not ready_ids:
            return []

        # Collect, apply final enrichments, remove from buffer
        ready = []
        for eid in ready_ids:
            buf = self._buffer.pop(eid)
            # Final enrichment check before emit
            buf.raw = self.enrich_raw(eid, buf.raw)
            buf.event = parse_event(buf.raw, self.match)
            formatted = format_event(buf.event, self.match)
            if buf.event.is_late:
                formatted = f"!! {formatted.lstrip()}"
            ready.append((formatted, buf.minute_value, eid, buf.enrichments_applied))

        # Sort by minute
        ready.sort(key=lambda x: x[1])

        lines = []
        for formatted, mv, eid, applied in ready:
            if mv >= 0:
                self._last_minute = max(self._last_minute, mv)
            lines.append(formatted)
            self._emitted[eid] = applied

        return lines

    def check_stats(self) -> str | None:
        """Step 6: Check if stats block is due."""
        if self.stats_interval <= 0 or not self.match.stats:
            return None

        current = self._last_minute
        if current <= 0 or current - self._last_stats_minute < self.stats_interval:
            return None

        minute_str = f"{int(current)}'"

        # Try ESPN first
        config = get_config()
        if config.sources.espn:
            try:
                from .espn import get_latest_stats, format_espn_stats
                espn_row = get_latest_stats(self.match.match_id, config)
                if espn_row:
                    self._last_stats_minute = current
                    return format_espn_stats(espn_row, minute_str)
            except Exception:
                pass

        # Fallback: computed
        self._last_stats_minute = current
        return format_stats(self.match.stats, minute_str)

    def run_cycle(self, events_changed: bool, enrichments_changed: bool) -> None:
        """Run one complete cycle. Emit at most one notification."""
        # 1-2: Read new data if flagged
        if events_changed:
            self.read_new_events()
        if enrichments_changed:
            self.read_new_enrichments()

        # 4-5: Collect ready events
        event_lines = self.collect_ready()

        # 6: Check stats
        stats_block = self.check_stats()

        # 7: Emit combined notification
        output = []
        if event_lines:
            output.extend(event_lines)
        if stats_block:
            output.append(stats_block)

        if output:
            self.emit("\n".join(output))

    def flush_all(self):
        """Emit everything remaining in the buffer."""
        items = []
        for eid, buf in self._buffer.items():
            formatted = format_event(buf.event, self.match)
            if buf.event.is_late:
                formatted = f"!! {formatted.lstrip()}"
            items.append((formatted, buf.minute_value, eid))
        self._buffer.clear()

        items.sort(key=lambda x: x[1])
        if items:
            lines = [fmt for fmt, _, _ in items]
            self.emit("\n".join(lines))


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


def cmd_watch(config, match_id: str, delay: int = 0, cycle_interval: int = 10):
    """Watch a match with cycle-based emission."""
    raw_match_path = config.paths.raw_matches_dir / f"{match_id}.json"
    raw_events_path = config.paths.raw_events_dir / f"{match_id}.jsonl"
    raw_enrichments_path = config.paths.raw_enrichments_dir / f"{match_id}.jsonl"

    # Build match model
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
        match.stats = MatchStats(match.home.abbreviation, match.away.abbreviation)

    # Preamble + header (emit to both stdout and log)
    header_parts = []
    if config.display.preamble:
        header_parts.append(format_preamble())
    header_parts.append(format_match_header(match))
    for team in [match.home, match.away]:
        profile = load_team_profile(team.abbreviation)
        if profile:
            header_parts.append("")
            header_parts.append(profile)
    header_text = "\n".join(header_parts)
    print(header_text)
    # Write header to log file too
    if log_path:
        with open(log_path, "w") as lf:
            lf.write(header_text + "\n")

    # Wait for events
    if not raw_events_path.exists():
        print(f"Waiting for events on #{match_id}...")
        while not raw_events_path.exists():
            time.sleep(2)

    # Feed log (full output, no truncation)
    log_dir = config.paths.data_dir / "feeds"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{match_id}.md"

    # Engine
    engine = FeedEngine(
        raw_events_path, raw_enrichments_path, match,
        delay=delay, cycle_interval=cycle_interval,
        stats_interval=config.display.stats_interval,
        log_path=log_path,
    )

    # Catchup
    engine.catchup()
    print("-- live --")

    # Watchdog (flag-only)
    watcher = FileChangeFlag(
        raw_events_path.name,
        raw_enrichments_path.name,
    )
    observer = Observer()
    observer.schedule(watcher, str(config.paths.raw_events_dir), recursive=False)
    observer.schedule(watcher, str(config.paths.raw_enrichments_dir), recursive=False)
    observer.start()

    try:
        while True:
            time.sleep(cycle_interval)

            # Consume flags
            ev_changed, en_changed = watcher.consume_flags()

            # Run cycle
            engine.run_cycle(ev_changed, en_changed)

            # Check for full time
            live = read_live(config)
            info = live.get("matches", {}).get(match_id)
            if info and info.get("status") == 0:
                time.sleep(2)
                engine.read_new_events()
                engine.read_new_enrichments()
                engine.flush_all()
                print(f"FULL TIME: {info.get('home', '?')} "
                      f"{info.get('home_score', '?')}-{info.get('away_score', '?')} "
                      f"{info.get('away', '?')} (#{match_id})")
                break

    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        engine.flush_all()
        observer.stop()
        observer.join(timeout=3)


# --- Main ---

def main():
    parser = argparse.ArgumentParser(description="football-wire LM feed")
    parser.add_argument("match_id", nargs="?", help="Match ID to watch")
    parser.add_argument("--config", help="Path to config file")
    parser.add_argument("--delay", type=int, help="Delay seconds (enrichment + TV sync)")
    parser.add_argument("--cycle", type=int, help="Cycle interval seconds (default 10)")
    parser.add_argument("--snapshot", action="store_true", help="One-shot status")

    args = parser.parse_args()
    config = init_config(args.config)

    if args.snapshot:
        cmd_snapshot(config, args.match_id)
        return

    delay = args.delay if args.delay is not None else config.display.delay
    cycle = args.cycle if args.cycle is not None else 10

    match_id = args.match_id
    if not match_id:
        match_id = find_live_match(config)
        if not match_id:
            print("No live match found. Specify a match ID.")
            sys.exit(1)
        print(f"Auto-detected: #{match_id}")

    cmd_watch(config, match_id, delay=delay, cycle_interval=cycle)


if __name__ == "__main__":
    main()
