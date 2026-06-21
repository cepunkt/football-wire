"""
State-machine-powered feed engine.

Drop-in replacement for the FeedEngine in feed.py. Uses the state
machine for all event processing instead of ad-hoc logic.

Raw events → FIFA adapter → StateInput → MatchStateMachine → StateOutput → emit
"""

import json
import time
import threading
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileModifiedEvent

from .adapters import fifa_event_to_input, fifa_match_to_score, espn_stats_to_input
from .config import get_config
from .display import (
    format_event as display_format,
    format_enrichment_correction,
    init_player_names,
    player_name as _resolve_player_name,
)
from .football import MatchMinute, MatchPhase
from .state import (
    MatchStateMachine, StateInput, StateOutput,
    OutputKind, TeamState, SourceTrust,
)
from .tournament import load_tournament, load_tournament_data

# Force unbuffered output
print = partial(print, flush=True)


# --- File change watcher ---

class FileChangeFlag(FileSystemEventHandler):
    def __init__(self, *watch_names: str):
        super().__init__()
        self._watch_names = set(watch_names)
        self._changed: set[str] = set()
        self._lock = threading.Lock()

    def on_modified(self, event):
        if not isinstance(event, FileModifiedEvent):
            return
        name = Path(event.src_path).name
        if name in self._watch_names:
            with self._lock:
                self._changed.add(name)

    def consume(self) -> set[str]:
        with self._lock:
            changed = set(self._changed)
            self._changed.clear()
        return changed


# --- Buffered output ---

@dataclass
class BufferedOutput:
    """A state machine output waiting to be emitted."""
    output: StateOutput
    raw_event_type: int | None
    buffered_at: float
    emit_after: float
    source_id: str = ""


# --- Format (delegated to display.py) ---

def format_output(out: StateOutput, sm: MatchStateMachine) -> str | None:
    """Format a StateOutput for the feed stream. Delegates to display module."""
    return display_format(out, sm)


# Legacy aliases removed — all formatting now in display.py
# _format_event_output, _format_correction_output, _format_shot_data,
# _format_position, _player_names, init_player_names, _resolve_player_name
# are all in display.py now.


# --- State Machine Feed Engine ---

class SMFeedEngine:
    """Feed engine powered by the state machine."""

    def __init__(
        self,
        sm: MatchStateMachine,
        events_path: Path,
        enrichments_path: Path,
        match_path: Path,
        match_data: dict,
        delay: int = 0,
        cycle_interval: int = 10,
        stats_interval: int = 15,
        log_path: Path | None = None,
    ):
        self.sm = sm
        self.events_path = events_path
        self.enrichments_path = enrichments_path
        self.match_path = match_path
        self.match_data = match_data
        self.delay = delay
        self.cycle_interval = cycle_interval
        self.stats_interval = stats_interval

        # File positions
        self.events_pos = 0
        self.enrichments_pos = 0

        # Enrichment cache
        self._enrichment_cache: dict[str, dict[str, any]] = {}

        # Output buffer (delay)
        self._buffer: list[BufferedOutput] = []
        self._last_minute: float = -1.0
        self._last_stats_minute: float = 0.0

        # Track emitted events for post-emit corrections
        self._emitted_ids: dict[str, StateOutput] = {}  # source_id → output
        self._emitted_fields: dict[str, set[str]] = {}  # source_id → enriched fields
        self._pending_corrections: list[str] = []


        # Feed log
        self.log_path = log_path
        self._log_file = None
        if log_path:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            self._log_file = open(log_path, "a")

    def emit(self, text: str):
        print(text)
        if self._log_file:
            self._log_file.write(text + "\n")
            self._log_file.flush()

    # --- Enrichment cache ---

    def load_enrichment_cache(self):
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
                    fld = e.get("field", "")
                    if eid and fld:
                        if eid not in self._enrichment_cache:
                            self._enrichment_cache[eid] = {}
                        self._enrichment_cache[eid][fld] = e.get("new")
                except json.JSONDecodeError:
                    pass
            self.enrichments_pos = f.tell()

    def verify_score(self) -> list[str]:
        """Re-read match JSON and cross-check score against SM state.

        The daemon updates the match JSON on every poll. The match
        endpoint has the canonical score. If SM's event-derived score
        disagrees, the SM voids phantom goals.
        """
        lines = []
        try:
            with open(self.match_path) as f:
                self.match_data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return lines

        inp = fifa_match_to_score(self.match_data)
        result = self.sm.apply(inp)

        if result.kind == OutputKind.CORRECTION:
            text = format_output(result, self.sm)
            if text:
                lines.append(text)

        return lines

    def _drain_side_outputs(self):
        """Pick up side-channel events from the state machine.

        The SM generates internal events (direction_determined, etc.)
        that aren't returned by apply(). They go to sm.side_outputs.
        """
        for ev in self.sm.side_outputs:
            now = time.time()
            self._buffer.append(BufferedOutput(
                output=ev,
                raw_event_type=None,
                buffered_at=now,
                emit_after=now + self.delay if self.delay > 0 else 0,
            ))
        self.sm.side_outputs.clear()

    def _enrich_raw(self, event_id: str, raw: dict) -> dict:
        if event_id in self._enrichment_cache:
            # Preserve original player IDs before enrichment overwrites them.
            # The API reshuffles IdPlayer on sub events, corrupting the pair.
            if "_orig_IdPlayer" not in raw:
                raw["_orig_IdPlayer"] = raw.get("IdPlayer")
                raw["_orig_IdSubPlayer"] = raw.get("IdSubPlayer")
            for fld, val in self._enrichment_cache[event_id].items():
                raw[fld] = val
        return raw

    # --- Catchup ---

    def catchup(self):
        """Replay all existing events through state machine. No delay."""
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
                    raw_events.append(raw)
                except json.JSONDecodeError:
                    pass
            self.events_pos = f.tell()

        # Enrich and convert to StateInput, then sort
        inputs = []
        for raw in raw_events:
            eid = str(raw.get("EventId", ""))
            if eid:
                raw = self._enrich_raw(eid, raw)
            inp = fifa_event_to_input(raw)
            if inp:
                inputs.append((inp, raw))

        inputs.sort(key=lambda x: x[0].minute.sort_value)

        # Process through state machine
        outputs = []
        for inp, raw in inputs:
            result = self.sm.apply(inp)
            if result.kind != OutputKind.NOTHING:
                outputs.append(result)
                if inp.minute.sort_value >= 0:
                    self._last_minute = max(self._last_minute, inp.minute.sort_value)

        # Drain SM side-channel events (direction, etc.)
        outputs.extend(self.sm.side_outputs)
        self.sm.side_outputs.clear()

        # Verify score against canonical match data after replaying events
        score_corrections = self.verify_score()

        # Emit key events only (compact catchup)
        key_types = {"goal", "sub", "yellow", "red", "second_yellow",
                     "period", "goal_voided", "goal_enriched",
                     "direction_determined", "var_review"}
        key_outputs = [o for o in outputs
                       if o.data.get("type") in key_types
                       or o.data.get("action") in ("start", "end")
                       or o.data.get("action") == "var_review"]

        # Filter out voided goals — score verification already ran,
        # showing the bare goal without context is confusing
        voided_players = set()
        for g in self.sm.goals:
            if g.voided:
                voided_players.add((g.minute.base, g.player_id))

        filtered = []
        for o in key_outputs:
            if o.data.get("type") == "goal":
                key = (o.minute.base, o.data.get("player_id", ""))
                if key in voided_players:
                    # Replace with voided annotation
                    o.data["type"] = "goal_voided"
                    o.data["voided"] = True
            filtered.append(o)
        key_outputs = filtered

        if key_outputs or score_corrections:
            self.emit("[Catchup]")
            for out in key_outputs:
                text = format_output(out, self.sm)
                if text:
                    self.emit(text)
            for line in score_corrections:
                self.emit(line)

        if self._last_minute > 0:
            self._last_stats_minute = self._last_minute

    # --- Live cycle ---

    def read_new_events(self):
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
                        eid = str(raw.get("EventId", ""))

                        # Apply any cached enrichments
                        if eid:
                            raw = self._enrich_raw(eid, raw)

                        # Convert and apply
                        inp = fifa_event_to_input(raw)
                        if not inp:
                            continue

                        result = self.sm.apply(inp)

                        # Pick up SM side-channel events (direction, etc.)
                        self._drain_side_outputs()

                        if result.kind == OutputKind.NOTHING:
                            continue

                        # Buffer with delay
                        now = time.time()
                        self._buffer.append(BufferedOutput(
                            output=result,
                            raw_event_type=raw.get("Type"),
                            buffered_at=now,
                            emit_after=now + self.delay if self.delay > 0 else 0,
                            source_id=eid,
                        ))

                    except json.JSONDecodeError:
                        pass
                self.events_pos = f.tell()
        except OSError:
            pass

    # Mapping from raw API field names to StateOutput data keys
    _ENRICHMENT_FIELD_MAP = {
        "PositionX": "position_x",
        "PositionY": "position_y",
        "GoalGatePositionX": "gate_x",
        "GoalGatePositionY": "gate_y",
    }

    def read_new_enrichments(self):
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
                        fld = enrichment.get("field", "")
                        new_val = enrichment.get("new")
                        if not eid or not fld:
                            continue

                        # Update cache
                        if eid not in self._enrichment_cache:
                            self._enrichment_cache[eid] = {}
                        self._enrichment_cache[eid][fld] = new_val

                        # Apply to buffered events (not yet emitted)
                        for buf in self._buffer:
                            if buf.source_id == eid:
                                self._apply_enrichment_to_output(
                                    buf.output, fld, new_val)
                                break
                        else:
                            # Already emitted — queue correction
                            if eid in self._emitted_ids:
                                self._queue_correction(eid, fld, new_val)

                    except json.JSONDecodeError:
                        pass
                self.enrichments_pos = f.tell()
        except OSError:
            pass

    def _apply_enrichment_to_output(
        self, output: StateOutput, field: str, value
    ) -> None:
        """Apply a raw API field enrichment to a StateOutput's data dict."""
        mapped = self._ENRICHMENT_FIELD_MAP.get(field)
        if mapped and value is not None:
            output.data[mapped] = value
            # Update on_target if we now have gate coordinates
            if mapped in ("gate_x", "gate_y"):
                if ("gate_x" in output.data and "gate_y" in output.data):
                    output.data["on_target"] = True

    def _queue_correction(self, eid: str, field: str, value) -> None:
        """Queue a correction line for an already-emitted event."""
        if eid not in self._emitted_fields:
            self._emitted_fields[eid] = set()
        if field in self._emitted_fields[eid]:
            return  # already corrected this field
        self._emitted_fields[eid].add(field)

        original = self._emitted_ids.get(eid)
        if not original:
            return

        # For coordinates, wait until we have both X and Y
        if field in ("PositionX", "PositionY"):
            cache = self._enrichment_cache.get(eid, {})
            if cache.get("PositionX") is None or cache.get("PositionY") is None:
                return
            # Mark both as done to prevent duplicate emission
            self._emitted_fields[eid].add("PositionX")
            self._emitted_fields[eid].add("PositionY")

        # Delegate formatting to display module
        cache = self._enrichment_cache.get(eid, {})
        text = format_enrichment_correction(original, field, cache, self.sm)
        if text:
            self._pending_corrections.append(text)

    def collect_ready(self) -> list[str]:
        now = time.time()
        ready = [b for b in self._buffer if now >= b.emit_after]
        self._buffer = [b for b in self._buffer if now < b.emit_after]

        ready.sort(key=lambda b: b.output.minute.sort_value)

        lines = []
        for buf in ready:
            if buf.output.minute.sort_value >= 0:
                self._last_minute = max(self._last_minute,
                                        buf.output.minute.sort_value)
            text = format_output(buf.output, self.sm)
            if text:
                lines.append(text)
            # Track emitted for post-emit corrections
            if buf.source_id:
                self._emitted_ids[buf.source_id] = buf.output

        # Append any pending corrections
        if self._pending_corrections:
            lines.extend(self._pending_corrections)
            self._pending_corrections.clear()

        return lines

    def check_stats(self) -> str | None:
        if self.stats_interval <= 0:
            return None
        current = self._last_minute
        if current <= 0 or current - self._last_stats_minute < self.stats_interval:
            return None

        minute_str = f"{int(current)}'"
        config = get_config()
        if config.espn.enabled or config.sources.espn:
            try:
                from .espn import get_latest_stats, format_espn_stats
                espn_row = get_latest_stats(
                    list(self.sm.events[0].data.keys())[0] if self.sm.events else "",
                    config,
                )
                # Use match_id from match_data
                match_id = self.match_data.get("IdMatch", "")
                espn_row = get_latest_stats(match_id, config)
                if espn_row:
                    self._last_stats_minute = current
                    # Also feed to state machine
                    minute = MatchMinute(base=int(current), added=0, raw=minute_str)
                    inp = espn_stats_to_input(espn_row, minute)
                    self.sm.apply(inp)
                    return format_espn_stats(espn_row, minute_str)
            except Exception:
                pass

        self._last_stats_minute = current
        return None

    def run_cycle(self, events_changed: bool, enrichments_changed: bool,
                  match_changed: bool = False):
        if events_changed:
            self.read_new_events()
        if enrichments_changed:
            self.read_new_enrichments()

        event_lines = self.collect_ready()
        stats_block = self.check_stats()

        # Score verification when match data updated by daemon
        score_lines = []
        if match_changed:
            score_lines = self.verify_score()

        output = []
        if event_lines:
            output.extend(event_lines)
        if score_lines:
            output.extend(score_lines)
        if stats_block:
            output.append(stats_block)

        if output:
            self.emit("\n".join(output))

    def flush_all(self):
        lines = []
        self._buffer.sort(key=lambda b: b.output.minute.sort_value)
        for buf in self._buffer:
            text = format_output(buf.output, self.sm)
            if text:
                lines.append(text)
        self._buffer.clear()
        if lines:
            self.emit("\n".join(lines))
