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

        # Pending corrections for emitted events
        self._pending_corrections: list[dict] = []
        # Emitted event context for correction display
        self._emitted_context: dict[str, dict] = {}

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
            # Preserve original player IDs before enrichment overwrites them.
            # The API reshuffles IdPlayer on sub events, corrupting the pair.
            if "_orig_IdPlayer" not in raw:
                raw["_orig_IdPlayer"] = raw.get("IdPlayer")
                raw["_orig_IdSubPlayer"] = raw.get("IdSubPlayer")
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

    def catchup_silent(self):
        """Process all existing events for state tracking.

        Silent on stdout (no Monitor output). But writes full enriched
        history to the feed log file so "Match so far" pointer works.

        Builds on_pitch set, dedup keys, stats, file positions —
        everything the live cycle needs.
        """
        if not self.events_path.exists():
            return

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

            if ev.minute.value >= 0:
                self._last_minute = max(self._last_minute, ev.minute.value)
            if ev.event_id:
                self._emitted[ev.event_id] = set()
            events.append(ev)

        # Write enriched history to log file only (not stdout)
        if self._log_file and events:
            events.sort(key=lambda e: (e.minute.value, e.logged_at))
            for ev in events:
                line = format_event(ev, self.match)
                self._log_file.write(line + "\n")
            self._log_file.flush()

        if self._last_minute > 0:
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

    # _pending_corrections and _emitted_context are initialised in __init__
    # (not here — class-level mutable defaults are shared across instances)

    def _apply_enrichment(self, eid: str, field: str, value):
        """Apply enrichment to buffer or queue correction for emitted events."""
        if eid in self._buffer:
            # Still buffered — update raw and re-parse
            buf = self._buffer[eid]
            buf.raw[field] = value
            buf.enrichments_applied.add(field)
            buf.event = parse_event(buf.raw, self.match)
        elif eid in self._emitted:
            # Already pushed — queue correction if not already shown
            if field not in self._emitted[eid]:
                self._emitted[eid].add(field)
                self._pending_corrections.append({
                    "event_id": eid, "field": field, "value": value,
                    "context": self._emitted_context.get(eid, {}),
                })

    def collect_corrections(self) -> list[str]:
        """Collect and format pending corrections for emitted events."""
        if not self._pending_corrections:
            return []

        lines = []
        # Group by event_id
        by_event: dict[str, list[dict]] = {}
        for c in self._pending_corrections:
            eid = c["event_id"]
            if eid not in by_event:
                by_event[eid] = []
            by_event[eid].append(c)
        self._pending_corrections = []

        for eid, corrections in by_event.items():
            fields = [c["field"] for c in corrections]

            # Re-parse the event with all enrichments applied
            # to get the corrected version
            if eid in self._enrichment_cache:
                # Find the original raw from the events file
                # For now, emit field-level corrections
                for c in corrections:
                    field = c["field"]
                    value = c["value"]

                    if field == "EventDescription":
                        desc = value
                        if isinstance(desc, list) and desc and isinstance(desc[0], dict):
                            desc = desc[0].get("Description", "")
                        if desc:
                            lines.append(f"  >> CORRECTED: {desc}")

                    elif field == "IdPlayer":
                        player = self.match.player_by_id(str(value))
                        if player:
                            lines.append(f"  >> CORRECTED: player is {player.display_name}")

                    elif field in ("PositionX", "PositionY",
                                   "GoalGatePositionX", "GoalGatePositionY"):
                        # Wait until we have both X and Y
                        if ("PositionX" in [c2["field"] for c2 in corrections] and
                                "PositionY" in [c2["field"] for c2 in corrections]):
                            px = self._enrichment_cache.get(eid, {}).get("PositionX")
                            py = self._enrichment_cache.get(eid, {}).get("PositionY")
                            if px is not None and py is not None:
                                from .model import ShotPosition
                                sp = ShotPosition.from_raw(px, py)
                                # Include event context for mapping
                                ctx = c.get("context", {})
                                prefix = ctx.get("formatted_prefix", "")
                                # Extract minute and event info from prefix
                                context_str = prefix.strip()[:50] if prefix else ""
                                lines.append(
                                    f"  >> ENRICHED: {context_str} "
                                    f"| from {sp.distance_m:.0f}m, "
                                    f"{sp.zone}, {sp.side} ({px:.0f},{py:.0f})"
                                )
                            # Don't emit individual X/Y separately
                            break

        return lines

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
            # Store context for correction display
            buf = None
            for r in ready:
                if r[2] == eid:
                    break
            # Re-fetch event from buffer backup isn't available,
            # so store from the formatted line
            ev = None
            for r_fmt, r_mv, r_eid, r_app in ready:
                if r_eid == eid:
                    break
            # Simple context from the ready event
            self._emitted_context[eid] = {
                "minute_value": mv,
                "formatted_prefix": formatted[:60] if formatted else "",
            }

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
        # Always check enrichments — watchdog may miss changes
        # across container/host boundaries
        if events_changed:
            self.read_new_events()
        self.read_new_enrichments()

        # 3b: Collect corrections for already-emitted events
        correction_lines = self.collect_corrections()

        # 4-5: Collect ready events
        event_lines = self.collect_ready()

        # 6: Check stats
        stats_block = self.check_stats()

        # 7: Emit combined notification
        output = []
        if correction_lines:
            output.extend(correction_lines)
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

    # Preamble + lean header (no team profiles — use lore files)
    header_parts = []
    if config.display.preamble:
        header_parts.append(format_preamble())
    header_parts.append(format_match_header(match))

    # Lore pointers instead of inline profiles
    home_abbr = match.home.abbreviation
    away_abbr = match.away.abbreviation
    lore_dir = "data/static/tournaments/wc2026-lore"
    header_parts.append("")
    header_parts.append(f"Context (read for background):")
    header_parts.append(f"  Team lore:    {lore_dir}/teams/{home_abbr}.md | {lore_dir}/teams/{away_abbr}.md")
    header_parts.append(f"  Pre-match:    {lore_dir}/matches/{home_abbr}-{away_abbr}-pregame.md")
    # Determine group from tournament data if available
    try:
        from .tournament import load_tournament_data
        from pathlib import Path as _Path
        td = load_tournament_data(_Path("data/static/tournaments/wc2026-data"))
        group = td.group_for_team(home_abbr)
        group_letter = group.letter if group else "?"
    except Exception:
        group_letter = "?"
    header_parts.append(f"  Group:        {lore_dir}/groups/{group_letter}.md")

    # If mid-match (events exist), point to feed history
    if raw_events_path.exists():
        header_parts.append(f"  Match so far: data/feeds/{match_id}.md")

    header_text = "\n".join(header_parts)
    print(header_text)

    # Feed log (full output, no truncation)
    log_dir = config.paths.data_dir / "feeds"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{match_id}.md"

    # Write header to log file too
    if log_path:
        with open(log_path, "w") as lf:
            lf.write(header_text + "\n")

    # Wait for events
    if not raw_events_path.exists():
        print(f"Waiting for events on #{match_id}...")
        while not raw_events_path.exists():
            time.sleep(2)

    # Engine
    engine = FeedEngine(
        raw_events_path, raw_enrichments_path, match,
        delay=delay, cycle_interval=cycle_interval,
        stats_interval=config.display.stats_interval,
        log_path=log_path,
    )

    # Catchup — silently process events to build state, but don't emit
    # The feed log file and lore pointers handle history
    engine.catchup_silent()
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

def cmd_watch_sm(config, match_id: str, delay: int = 0, cycle_interval: int = 10):
    """Watch a match using the state machine engine."""
    from .feed_sm import SMFeedEngine, FileChangeFlag as SMFileChangeFlag, format_output
    from .state import MatchStateMachine, TeamState
    from .tournament import load_tournament, load_tournament_data
    from .process import read_raw_json
    from pathlib import Path

    raw_match_path = config.paths.raw_matches_dir / f"{match_id}.json"
    raw_events_path = config.paths.raw_events_dir / f"{match_id}.jsonl"
    raw_enrichments_path = config.paths.raw_enrichments_dir / f"{match_id}.jsonl"

    # Load match data
    raw_match = read_raw_json(raw_match_path)
    if raw_match is None:
        print(f"No data for #{match_id}")
        sys.exit(1)

    # Load tournament rules + data
    tournament_dir = Path("data/static/tournaments")
    rules = load_tournament(tournament_dir / "wc2026.toml")
    tournament_data = load_tournament_data(tournament_dir / "wc2026-data")

    # Extract team info from match data
    home_raw = raw_match.get("HomeTeam") or raw_match.get("Home") or {}
    away_raw = raw_match.get("AwayTeam") or raw_match.get("Away") or {}

    home_id = str(home_raw.get("IdTeam", ""))
    away_id = str(away_raw.get("IdTeam", ""))
    home_abbr = home_raw.get("Abbreviation", "???")
    away_abbr = away_raw.get("Abbreviation", "???")

    # Build on_pitch from starters
    home_starters = set()
    away_starters = set()
    for p in (home_raw.get("Players") or []):
        if p.get("Status") == 1:  # starter
            home_starters.add(str(p.get("IdPlayer", "")))
    for p in (away_raw.get("Players") or []):
        if p.get("Status") == 1:
            away_starters.add(str(p.get("IdPlayer", "")))

    home_state = TeamState(
        team_id=home_id,
        abbreviation=home_abbr,
        on_pitch=home_starters,
    )
    away_state = TeamState(
        team_id=away_id,
        abbreviation=away_abbr,
        on_pitch=away_starters,
    )

    # Determine if knockout from stage name
    stage_name = ""
    sn = raw_match.get("StageName")
    if sn and isinstance(sn, list) and sn:
        stage_name = sn[0].get("Description", "")
    is_knockout = stage_name != "" and stage_name != "First Stage"

    # Create state machine
    sm = MatchStateMachine(
        match_id=match_id,
        home=home_state,
        away=away_state,
        rules=rules,
        is_knockout=is_knockout,
    )

    # Build player name lookup
    from .feed_sm import init_player_names
    init_player_names(raw_match)

    # Preamble + lean header
    header_parts = []
    if config.display.preamble:
        header_parts.append(format_preamble())

    home_score = home_raw.get("Score", 0) or 0
    away_score = away_raw.get("Score", 0) or 0
    header_parts.append(f"{home_abbr} {home_score} - {away_score} {away_abbr} (# {match_id})")

    # Lineup (one line per team)
    for side, raw_team in [("home", home_raw), ("away", away_raw)]:
        abbr = raw_team.get("Abbreviation", "???")
        tactics = raw_team.get("Tactics", "?")
        starters = []
        for p in sorted((raw_team.get("Players") or []),
                        key=lambda x: x.get("ShirtNumber", 99)):
            if p.get("Status") == 1:
                def _get_name(field):
                    v = p.get(field, [])
                    if isinstance(v, list) and v and isinstance(v[0], dict):
                        return v[0].get("Description", "")
                    return str(v) if v else ""
                name = _get_name("ShortName") or _get_name("PlayerName") or "?"
                starters.append(name)
        header_parts.append(f"{abbr} ({tactics}): {', '.join(starters)}")

    # Lore pointers
    lore_dir = Path("data/static/tournaments/wc2026-lore")
    header_parts.append("")
    header_parts.append(f"Lore: {lore_dir}/teams/{{{home_abbr},{away_abbr}}}.md")

    header_text = "\n".join(header_parts)
    print(header_text)

    # Feed log
    log_dir = config.paths.data_dir / "feeds"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{match_id}.sm.md"

    with open(log_path, "w") as lf:
        lf.write(header_text + "\n")

    # Wait for events
    if not raw_events_path.exists():
        print(f"Waiting for events on #{match_id}...")
        while not raw_events_path.exists():
            time.sleep(2)

    # Engine
    engine = SMFeedEngine(
        sm=sm,
        events_path=raw_events_path,
        enrichments_path=raw_enrichments_path,
        match_path=raw_match_path,
        match_data=raw_match,
        delay=delay,
        cycle_interval=cycle_interval,
        stats_interval=config.display.stats_interval,
        log_path=log_path,
    )

    # Catchup
    engine.catchup()
    print("-- live (state machine) --")

    # Watchdog
    watcher = SMFileChangeFlag(
        raw_events_path.name,
        raw_enrichments_path.name,
        raw_match_path.name,
    )
    observer = Observer()
    observer.schedule(watcher, str(config.paths.raw_events_dir), recursive=False)
    observer.schedule(watcher, str(config.paths.raw_enrichments_dir), recursive=False)
    observer.schedule(watcher, str(config.paths.raw_matches_dir), recursive=False)
    observer.start()

    try:
        while True:
            time.sleep(cycle_interval)
            changed = watcher.consume()
            ev_changed = raw_events_path.name in changed
            en_changed = raw_enrichments_path.name in changed
            match_changed = raw_match_path.name in changed
            engine.run_cycle(ev_changed, en_changed, match_changed)

            # Check for full time
            live = read_live(config)
            info = live.get("matches", {}).get(match_id)
            if info and info.get("status") == 0:
                time.sleep(2)
                engine.read_new_events()
                engine.read_new_enrichments()
                engine.flush_all()
                score = sm.score
                print(f"FULL TIME: {home_abbr} {score[0]}-{score[1]} {away_abbr} (#{match_id})")
                break

    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        engine.flush_all()
        observer.stop()
        observer.join(timeout=3)


def main():
    parser = argparse.ArgumentParser(description="football-wire LM feed")
    parser.add_argument("match_id", nargs="?", help="Match ID to watch")
    parser.add_argument("--config", help="Path to config file")
    parser.add_argument("--delay", type=int, help="Delay seconds (enrichment + TV sync)")
    parser.add_argument("--cycle", type=int, help="Cycle interval seconds (default 10)")
    parser.add_argument("--snapshot", action="store_true", help="One-shot status")
    parser.add_argument("--legacy", action="store_true",
                        help="Use legacy feed engine (pre-state-machine)")

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

    if args.legacy:
        cmd_watch(config, match_id, delay=delay, cycle_interval=cycle)
    else:
        cmd_watch_sm(config, match_id, delay=delay, cycle_interval=cycle)


if __name__ == "__main__":
    main()
