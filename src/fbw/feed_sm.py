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

from .adapters import fifa_event_to_input, espn_stats_to_input
from .config import get_config
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


# --- Format StateOutput for feed ---

def format_output(out: StateOutput, sm: MatchStateMachine) -> str | None:
    """Format a StateOutput for the feed stream."""
    minute = out.minute
    data = out.data
    flags = out.flags

    # Phase prefix
    phase_prefix = minute.phase_prefix if minute.base > 0 else ""
    minute_str = minute.display if minute.base > 0 else ""
    time_col = f"{phase_prefix:>3} {minute_str:<8}" if phase_prefix else f"    {minute_str:<8}"

    # Score
    score = sm.score
    score_str = f"[{score[0]}-{score[1]}]"

    event_type = data.get("type", data.get("action", ""))

    if out.kind == OutputKind.EVENT:
        return _format_event_output(time_col, data, score_str, sm, flags)
    elif out.kind == OutputKind.CORRECTION:
        return _format_correction_output(time_col, data, score_str, sm)
    elif out.kind == OutputKind.STATS:
        return None  # handled separately
    elif out.kind == OutputKind.ANNOTATION:
        text = data.get("text", "")
        return f"{time_col}  NOTE  {text}"
    return None


def _format_event_output(
    time_col: str, data: dict, score_str: str,
    sm: MatchStateMachine, flags: list[str],
) -> str | None:
    event_type = data.get("type", data.get("action", ""))
    raw_type = data.get("event_type_raw")

    # Period events
    if event_type in ("start", "end"):
        phase = data.get("phase", "")
        if event_type == "start":
            return f"{time_col}--- PERIOD              The referee signals the start. {score_str}"
        else:
            return f"{time_col}--- PERIOD END          The referee brings the period to an end. {score_str}"

    # Goals
    if event_type == "goal":
        player_id = data.get("player_id", "")
        team = sm._team_for_id(data.get("team_id", ""))
        team_abbr = team.abbreviation if team else "???"
        shot = data.get("shot_data")
        shot_str = ""
        if shot:
            shot_str = _format_shot_data(shot)

        player_name = _resolve_player_name(player_id, sm)
        is_pen = " (pen)" if data.get("is_penalty") else ""
        og = " (OG)" if data.get("own_goal") else ""
        flag_str = " [SUSPECT]" if flags else ""

        return (f"{time_col}>>GOAL<<{is_pen}{og}            "
                f"{team_abbr} | {player_name} scores!! "
                f"{shot_str}{score_str}{flag_str}")

    # Assist
    if event_type == "assist":
        player_id = data.get("player_id", "")
        team = sm._team_for_id(data.get("team_id", ""))
        team_abbr = team.abbreviation if team else "???"
        player_name = _resolve_player_name(player_id, sm)
        return f"{time_col}ASSIST                  {team_abbr} | Assisted by {player_name}. {score_str}"

    # Substitutions
    if event_type == "sub":
        team_abbr = data.get("team_abbr", "???")
        on_id = data.get("on", "")
        off_id = data.get("off", "")
        on_name = _resolve_player_name(on_id, sm)
        off_name = _resolve_player_name(off_id, sm)
        flag_str = ""
        if "both_players_on_pitch" in flags:
            flag_str = " [SUSPECT: both on pitch]"
        elif "neither_player_on_pitch" in flags:
            flag_str = " [SUSPECT: neither on pitch]"
        return (f"{time_col}SUB                     {team_abbr} | "
                f"ON: {on_name}, OFF: {off_name} {score_str}{flag_str}")

    # Cards
    if event_type in ("yellow", "red", "second_yellow", "second_yellow_red"):
        player_id = data.get("player_id", "")
        team = sm._team_for_id(data.get("team_id", ""))
        team_abbr = team.abbreviation if team else "???"
        player_name = _resolve_player_name(player_id, sm)
        card_map = {"yellow": "YELLOW", "red": "RED",
                    "second_yellow": "SECOND YELLOW/RED",
                    "second_yellow_red": "SECOND YELLOW/RED"}
        card_label = card_map.get(event_type, event_type.upper())
        return f"{time_col}{card_label:<24}{team_abbr} | {player_name} {score_str}"

    # Fouls
    if event_type == "foul":
        player_id = data.get("player_id", "")
        team = sm._team_for_id(data.get("team_id", ""))
        team_abbr = team.abbreviation if team else "???"
        player_name = _resolve_player_name(player_id, sm)
        return f"{time_col}FOUL                    {team_abbr} | {player_name} commits a foul. {score_str}"

    # Offside
    if event_type == "offside":
        player_id = data.get("player_id", "")
        team = sm._team_for_id(data.get("team_id", ""))
        team_abbr = team.abbreviation if team else "???"
        player_name = _resolve_player_name(player_id, sm)
        return f"{time_col}OFFSIDE                 {team_abbr} | {player_name} is ruled offside. {score_str}"

    # Shots
    if event_type == "shot":
        player_id = data.get("player_id", "")
        team = sm._team_for_id(data.get("team_id", ""))
        team_abbr = team.abbreviation if team else "???"
        player_name = _resolve_player_name(player_id, sm)
        shot_str = _format_shot_data(data)
        on_target = data.get("on_target", False)
        confidence = "(on target)" if on_target else ""
        return (f"{time_col}SHOT {confidence:<14}     "
                f"{team_abbr} | {player_name} attempts an effort on goal. "
                f"{shot_str}{score_str}")

    # Saves
    if event_type == "save":
        team = sm._team_for_id(data.get("team_id", ""))
        team_name = "unknown"
        if team:
            # Save is by the goalkeeper, display as team
            if team.team_id == sm.home.team_id:
                team_name = "home"
            else:
                team_name = "away"
        return f"{time_col}SAVE                    The goalkeeper pulls off a save. {score_str}"

    # Corners
    if event_type == "corner":
        player_id = data.get("player_id", "")
        team = sm._team_for_id(data.get("team_id", ""))
        team_abbr = team.abbreviation if team else "???"
        player_name = _resolve_player_name(player_id, sm)
        return f"{time_col}CORNER                  {team_abbr} | {player_name} takes a corner kick. {score_str}"

    # Interruptions
    if event_type in ("pause", "resume", "delay", "injury", "var_review"):
        action_map = {
            "pause": "PAUSE                   Match paused",
            "resume": "RESUME                  Match resumed after interruption",
            "delay": "DELAY                   Match paused for unspecified reasons",
            "injury": "INJURY                  Play stopped for injury",
            "var_review": "VAR                     VAR review in progress",
        }
        label = action_map.get(event_type, event_type.upper())
        return f"{time_col}{label} {score_str}"

    # Period changes that aren't start/end (coin toss etc)
    if event_type in ("coin_toss", "coin_side"):
        return None  # skip these

    # Fallback
    desc = data.get("description", "")
    if desc:
        return f"{time_col}{desc} {score_str}"
    return None


def _format_correction_output(
    time_col: str, data: dict, score_str: str,
    sm: MatchStateMachine,
) -> str | None:
    corr_type = data.get("type", "")
    if corr_type == "goal_enriched":
        shot_data = data.get("shot_data", {})
        shot_str = _format_shot_data(shot_data)
        player_name = _resolve_player_name(data.get("player_id", ""), sm)
        return f"{time_col}>>GOAL<< (ENRICHED)     {player_name} {shot_str}{score_str}"
    elif corr_type == "goal_voided":
        player_name = _resolve_player_name(data.get("player_id", ""), sm)
        reason = data.get("reason", "")
        return f"{time_col}~~GOAL VOIDED~~         {player_name} goal disallowed. {reason} {score_str}"
    return None


def _format_shot_data(data: dict) -> str:
    """Format shot position/placement data into a readable string."""
    parts = []
    if "position_x" in data and "position_y" in data:
        from .model import ShotPosition
        sp = ShotPosition.from_raw(data["position_x"], data["position_y"])
        parts.append(f"from {sp.distance_m:.0f}m, {sp.zone}, {sp.side} "
                     f"({data['position_x']:.0f},{data['position_y']:.0f})")
    if "gate_x" in data and "gate_y" in data:
        gx, gy = data["gate_x"], data["gate_y"]
        # Simple placement description
        height = "low" if gy < 30 else "high" if gy > 70 else "mid-height"
        side = "left" if gx < 40 else "right" if gx > 60 else "centre"
        parts.append(f"placed {height}, {side} ({gx:.0f},{gy:.0f})")
    if parts:
        return "| " + " ".join(parts) + " "
    return ""


# Player name cache — built from match data
_player_names: dict[str, str] = {}


def init_player_names(match_data: dict) -> None:
    """Build player ID → name lookup from match data."""
    _player_names.clear()
    for side in ("HomeTeam", "AwayTeam", "Home", "Away"):
        team = match_data.get(side)
        if not team or not isinstance(team, dict):
            continue
        for p in (team.get("Players") or []):
            pid = str(p.get("IdPlayer", ""))
            if not pid:
                continue
            # Name from localized fields
            name = ""
            for field in ("ShortName", "PlayerName"):
                v = p.get(field, [])
                if isinstance(v, list) and v and isinstance(v[0], dict):
                    name = v[0].get("Description", "")
                    if name:
                        break
            if name:
                _player_names[pid] = name


def _resolve_player_name(player_id: str, sm: MatchStateMachine) -> str:
    """Resolve a FIFA player ID to a name."""
    if player_id in _player_names:
        return _player_names[player_id]
    return f"Player({player_id})"


# --- State Machine Feed Engine ---

class SMFeedEngine:
    """Feed engine powered by the state machine."""

    def __init__(
        self,
        sm: MatchStateMachine,
        events_path: Path,
        enrichments_path: Path,
        match_data: dict,
        delay: int = 0,
        cycle_interval: int = 10,
        stats_interval: int = 15,
        log_path: Path | None = None,
    ):
        self.sm = sm
        self.events_path = events_path
        self.enrichments_path = enrichments_path
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

        # Emit key events only (compact catchup)
        key_types = {"goal", "sub", "yellow", "red", "second_yellow",
                     "period", "goal_voided", "goal_enriched"}
        key_outputs = [o for o in outputs
                       if o.data.get("type") in key_types
                       or o.data.get("action") in ("start", "end")]

        if key_outputs:
            self.emit("[Catchup]")
            for out in key_outputs:
                text = format_output(out, self.sm)
                if text:
                    self.emit(text)

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
                        if result.kind == OutputKind.NOTHING:
                            continue

                        # Buffer with delay
                        now = time.time()
                        self._buffer.append(BufferedOutput(
                            output=result,
                            raw_event_type=raw.get("Type"),
                            buffered_at=now,
                            emit_after=now + self.delay if self.delay > 0 else 0,
                        ))

                    except json.JSONDecodeError:
                        pass
                self.events_pos = f.tell()
        except OSError:
            pass

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

                        # Re-process buffered events with new enrichment
                        for buf in self._buffer:
                            if buf.output.data.get("source_id") == eid:
                                # Re-enrich — would need raw event to re-process
                                # For now, just note the enrichment
                                pass

                    except json.JSONDecodeError:
                        pass
                self.enrichments_pos = f.tell()
        except OSError:
            pass

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
        return lines

    def check_stats(self) -> str | None:
        if self.stats_interval <= 0:
            return None
        current = self._last_minute
        if current <= 0 or current - self._last_stats_minute < self.stats_interval:
            return None

        minute_str = f"{int(current)}'"
        config = get_config()
        if config.sources.espn:
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

    def run_cycle(self, events_changed: bool, enrichments_changed: bool):
        if events_changed:
            self.read_new_events()
        if enrichments_changed:
            self.read_new_enrichments()

        event_lines = self.collect_ready()
        stats_block = self.check_stats()

        output = []
        if event_lines:
            output.extend(event_lines)
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
