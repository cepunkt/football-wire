"""
Display formatting for football-wire.

Single module for all text output from StateOutput + match state.
Every event — live, catchup, enrichment, correction — goes through
the same path. Knows about direction, event types, and perspective.

This replaces the scattered format logic in feed_sm.py.
"""

from .football import AttackEnd, MatchPhase, PITCH_LENGTH_M, PITCH_WIDTH_M
from .model import ShotPosition, GoalPlacement, get_localized
from .state import (
    MatchStateMachine, MatchState, StateOutput,
    OutputKind, PlayDirection,
)
from .strings import S


# --- Player name resolution ---

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
            name = ""
            for field in ("ShortName", "PlayerName"):
                v = p.get(field, [])
                if isinstance(v, list) and v and isinstance(v[0], dict):
                    name = v[0].get("Description", "")
                    if name:
                        break
            if name:
                _player_names[pid] = name


def player_name(player_id: str) -> str:
    """Resolve a FIFA player ID to a display name."""
    if player_id in _player_names:
        return _player_names[player_id]
    return f"Player({player_id})" if player_id else ""


# --- Time column ---

def _time_col(output: StateOutput) -> str:
    """Format the time column: phase prefix + minute."""
    minute = output.minute
    phase_prefix = minute.phase_prefix if minute.base > 0 else ""
    minute_str = minute.display if minute.base > 0 else ""
    if phase_prefix:
        return f"{phase_prefix:>3} {minute_str:<8}"
    return f"    {minute_str:<8}"


# --- Position formatting ---

def format_pitch_position(
    px: float, py: float,
    direction: PlayDirection | None,
    team_id: str,
    home_team_id: str,
    event_type: str = "",
) -> str:
    """Format a pitch position with direction awareness.

    Returns a description string like:
      "in opponent's half, near opponent's box, right (70,30)"
      "midfield, central (50,48)"

    Skips shot-style formatting for corners and other non-positional events.
    """
    # Corners: near side / far side, not shot position
    if event_type == "corner":
        return _format_corner_position(px, py, direction, team_id, home_team_id)

    # Direction-aware description
    if direction and team_id:
        attacks_high = direction.attacks_high_x(team_id, home_team_id)

        # Half
        in_own_half = (px < 50) if attacks_high else (px > 50)
        half = "in own half" if in_own_half else "in opponent's half"

        # Proximity to goals
        if attacks_high:
            dist_to_opp = (100 - px) / 100 * PITCH_LENGTH_M
            dist_to_own = px / 100 * PITCH_LENGTH_M
        else:
            dist_to_opp = px / 100 * PITCH_LENGTH_M
            dist_to_own = (100 - px) / 100 * PITCH_LENGTH_M

        if dist_to_opp <= 25:
            zone = "near opponent's box"
        elif dist_to_own <= 25:
            zone = "near own box"
        else:
            zone = "midfield"

        # Side from attacker's perspective
        ny = py if attacks_high else (100.0 - py)
        side = "left" if ny > 60 else "right" if ny < 40 else "central"

        return f"{half}, {zone}, {side} ({px:.0f},{py:.0f})"

    # Neutral fallback (no direction known)
    if 35 <= px <= 65:
        zone = "midfield"
    elif px < 20 or px > 80:
        zone = "near penalty area"
    else:
        zone = "between midfield and box"
    side = "left" if py < 35 else "right" if py > 65 else "central"
    return f"{zone}, {side} ({px:.0f},{py:.0f})"


def _format_corner_position(
    px: float, py: float,
    direction: PlayDirection | None,
    team_id: str,
    home_team_id: str,
) -> str:
    """Format corner kick position — near side or far side."""
    if not direction or not team_id:
        return f"({px:.0f},{py:.0f})"

    attacks_high = direction.attacks_high_x(team_id, home_team_id)

    # Near/far side based on Y position
    # Near side = closer to the camera (lower Y), far side = away
    if py < 30:
        side = "near side"
    elif py > 70:
        side = "far side"
    else:
        side = "central"

    return side


def format_shot(data: dict) -> str:
    """Format shot position and goal placement data."""
    parts = []
    px = data.get("position_x")
    py = data.get("position_y")
    if px is not None and py is not None:
        sp = ShotPosition.from_raw(float(px), float(py))
        parts.append(
            f"from {sp.distance_m:.0f}m, {sp.zone}, {sp.side} "
            f"({float(px):.0f},{float(py):.0f})"
        )

    gx = data.get("gate_x")
    gy = data.get("gate_y")
    if gx is not None and gy is not None:
        gp = GoalPlacement(raw_x=float(gx), raw_y=float(gy))
        parts.append(
            f"placed {gp.height}, {gp.side} "
            f"({gp.offset_m:.1f}m off centre, {gp.height_m:.1f}m high)"
        )

    if parts:
        return "| " + " ".join(parts) + " "
    return ""


# --- Event formatting ---

def format_event(output: StateOutput, sm: MatchStateMachine) -> str | None:
    """Format any StateOutput for the feed stream.

    This is the single entry point. All event types, corrections,
    enrichments, and annotations go through here.
    """
    time = _time_col(output)
    score = sm.score
    score_str = f"[{score[0]}-{score[1]}]"
    data = output.data
    flags = output.flags
    event_type = data.get("type", data.get("action", ""))

    if output.kind == OutputKind.EVENT:
        return _format_event(time, event_type, data, score_str, sm, flags)
    elif output.kind == OutputKind.CORRECTION:
        return _format_correction(time, event_type, data, score_str, sm)
    elif output.kind == OutputKind.STATS:
        return None  # handled separately by stats block
    elif output.kind == OutputKind.ANNOTATION:
        text = data.get("text", "")
        return f"{time}  NOTE  {text}"
    return None


def _team_abbr(sm: MatchStateMachine, team_id: str) -> str:
    team = sm._team_for_id(team_id)
    return team.abbreviation if team else "???"


def _format_event(
    time: str, event_type: str, data: dict, score_str: str,
    sm: MatchStateMachine, flags: list[str],
) -> str | None:
    """Format an EVENT-kind StateOutput."""
    direction = sm.direction
    home_id = sm.home.team_id
    team_id = data.get("team_id", "")
    pid = data.get("player_id", "")

    # --- Voided goals ---
    if event_type == "goal_voided" or data.get("voided"):
        abbr = _team_abbr(sm, team_id)
        name = player_name(pid)
        return f"{time}~~GOAL VOIDED~~         {abbr} | {name} -- disallowed {score_str}"

    # --- Goals ---
    if event_type == "goal":
        abbr = _team_abbr(sm, team_id)
        name = player_name(pid)
        shot_str = format_shot(data.get("shot_data") or {})
        pen = " (pen)" if data.get("is_penalty") else ""
        og = " (OG)" if data.get("own_goal") else ""
        suspect = " [SUSPECT]" if flags else ""
        return (f"{time}>>GOAL<<{pen}{og}            "
                f"{abbr} | {name} scores!! {shot_str}{score_str}{suspect}")

    # --- Assists ---
    if event_type == "assist":
        abbr = _team_abbr(sm, team_id)
        name = player_name(pid)
        return f"{time}ASSIST                  {abbr} | Assisted by {name}. {score_str}"

    # --- Substitutions ---
    if event_type == "sub":
        abbr = data.get("team_abbr", "???")
        on_name = player_name(data.get("on", ""))
        off_name = player_name(data.get("off", ""))
        suspect = ""
        if "both_players_on_pitch" in flags:
            suspect = " [SUSPECT: both on pitch]"
        elif "neither_player_on_pitch" in flags:
            suspect = " [SUSPECT: neither on pitch]"
        return (f"{time}SUB                     {abbr} | "
                f"ON: {on_name}, OFF: {off_name} {score_str}{suspect}")

    # --- Cards ---
    if event_type in ("yellow", "red", "second_yellow", "second_yellow_red"):
        abbr = _team_abbr(sm, team_id)
        name = player_name(pid)
        pos = _position_suffix(data, direction, team_id, home_id, "foul")
        card_map = {
            "yellow": "YELLOW", "red": "RED",
            "second_yellow": "SECOND YELLOW/RED",
            "second_yellow_red": "SECOND YELLOW/RED",
        }
        label = card_map.get(event_type, event_type.upper())
        card_line = f"{time}{label:<24}{abbr} | {name}{pos} {score_str}"

        # Cards are bookable fouls — free kick context applies
        fk_line = _format_free_kick(data, direction, team_id, home_id, sm)
        if fk_line:
            return f"{card_line}\n{time}  → FREE KICK             {fk_line}"
        return card_line

    # --- Fouls ---
    if event_type == "foul":
        abbr = _team_abbr(sm, team_id)
        name = player_name(pid)
        pos = _position_suffix(data, direction, team_id, home_id, "foul")
        foul_line = f"{time}FOUL                    {abbr} | {name} commits a foul.{pos} {score_str}"

        # Free kick follow-up with danger assessment
        fk_line = _format_free_kick(data, direction, team_id, home_id, sm)
        if fk_line:
            return f"{foul_line}\n{time}  → FREE KICK             {fk_line}"
        return foul_line

    # --- Offsides ---
    if event_type == "offside":
        abbr = _team_abbr(sm, team_id)
        name = player_name(pid)
        pos = _position_suffix(data, direction, team_id, home_id, "offside")
        return f"{time}OFFSIDE                 {abbr} | {name} is ruled offside.{pos} {score_str}"

    # --- Shots ---
    if event_type == "shot":
        abbr = _team_abbr(sm, team_id)
        name = player_name(pid)
        shot_str = format_shot(data)
        on_target = data.get("on_target", False)
        confidence = "(on target)" if on_target else ""
        return (f"{time}SHOT {confidence:<14}     "
                f"{abbr} | {name} attempts an effort on goal. "
                f"{shot_str}{score_str}")

    # --- Saves ---
    if event_type == "save":
        abbr = _team_abbr(sm, team_id)
        name = player_name(pid)
        if name and name != f"Player({pid})":
            return f"{time}SAVE                    {abbr} | {name} saves. {score_str}"
        return f"{time}SAVE                    {abbr} | The goalkeeper saves. {score_str}"

    # --- Corners ---
    if event_type == "corner":
        abbr = _team_abbr(sm, team_id)
        name = player_name(pid)
        pos = _position_suffix(data, direction, team_id, home_id, "corner")
        return f"{time}CORNER                  {abbr} | {name} takes a corner kick.{pos} {score_str}"

    # --- Interruptions ---
    if event_type in ("pause", "resume", "delay", "injury", "var_review"):
        action_map = {
            "pause": "BREAK                   Match paused",
            "resume": "RESUME                  Match resumed after interruption",
            "delay": "DELAY                   Match paused for unspecified reasons",
            "injury": "INJURY                  Play stopped for injury",
            "var_review": "** VAR                  VAR review in progress",
        }
        label = action_map.get(event_type, event_type.upper())
        return f"{time}{label} {score_str}"

    # --- Direction ---
    if event_type == "direction_determined":
        desc = data.get("description", "")
        return f"{time}--- {desc} ---"

    # --- Period changes ---
    action = data.get("action", "")
    if event_type == "period" or action in ("start", "end"):
        if action == "start":
            return f"{time}--- PERIOD              The referee signals the start. {score_str}"
        elif action == "end":
            return f"{time}--- PERIOD END          The referee brings the period to an end. {score_str}"

    # --- Coin toss ---
    if event_type in ("coin_toss", "coin_side"):
        desc = data.get("description", "")
        if desc:
            return f"{time}COIN                    {desc} {score_str}"

    # --- Fallback — never silently drop events ---
    desc = data.get("description", "")
    if desc:
        return f"{time}{desc} {score_str}"
    # Last resort: show what we have so nothing is invisible
    return f"{time}[{event_type or 'UNK'}]                 {score_str}"


def _format_free_kick(
    data: dict,
    direction: PlayDirection | None,
    team_id: str,
    home_team_id: str,
    sm: MatchStateMachine,
) -> str | None:
    """Format free kick context from a foul's position.

    The free kick is awarded to the OTHER team at the foul location.
    Returns danger assessment based on distance and angle to goal.
    """
    px = data.get("position_x")
    py = data.get("position_y")
    if px is None or py is None:
        return None
    if not direction or not team_id:
        return None

    px, py = float(px), float(py)

    # The free kick goes to the opposing team
    fouling_team = sm._team_for_id(team_id)
    if not fouling_team:
        return None
    if fouling_team.team_id == sm.home.team_id:
        fk_team = sm.away
    else:
        fk_team = sm.home
    fk_abbr = fk_team.abbreviation

    # Euclidean distance to goal centre (not just depth)
    # A wide free kick is further from goal than a central one
    # at the same depth — this affects danger classification.
    fk_attacks_high = direction.attacks_high_x(fk_team.team_id, sm.home.team_id)
    if fk_attacks_high:
        depth_m = (100 - px) / 100 * PITCH_LENGTH_M
    else:
        depth_m = px / 100 * PITCH_LENGTH_M
    width_m = (py - 50) / 100 * PITCH_WIDTH_M
    dist_to_goal = (depth_m ** 2 + width_m ** 2) ** 0.5

    # Centrality for danger classification (wide = crossing, central = shooting)
    centrality = abs(py - 50)

    # Danger classification
    if dist_to_goal <= 20:
        if centrality <= 20:
            danger = "DANGEROUS — shooting range, central"
        else:
            danger = "promising — crossing position"
    elif dist_to_goal <= 30:
        if centrality <= 15:
            danger = "promising — edge of shooting range"
        else:
            danger = "crossing position"
    elif dist_to_goal <= 45:
        danger = "midfield"
    else:
        danger = "deep, no immediate threat"

    return f"{fk_abbr} | {dist_to_goal:.0f}m from goal centre — {danger}"


def _position_suffix(
    data: dict,
    direction: PlayDirection | None,
    team_id: str,
    home_team_id: str,
    event_type: str,
) -> str:
    """Build position suffix for fouls, offsides, corners."""
    px = data.get("position_x")
    py = data.get("position_y")
    if px is None or py is None:
        return ""

    pos = format_pitch_position(
        float(px), float(py),
        direction, team_id, home_team_id,
        event_type=event_type,
    )
    return f" | {pos}"


def _format_correction(
    time: str, corr_type: str, data: dict, score_str: str,
    sm: MatchStateMachine,
) -> str | None:
    """Format a CORRECTION-kind StateOutput."""
    if corr_type == "goal_enriched":
        shot_data = data.get("shot_data", {})
        shot_str = format_shot(shot_data)
        name = player_name(data.get("player_id", ""))
        return f"{time}>>GOAL<< (ENRICHED)     {name} {shot_str}{score_str}"
    elif corr_type == "goal_voided":
        name = player_name(data.get("player_id", ""))
        reason = data.get("reason", "")
        return f"{time}~~GOAL VOIDED~~         {name} goal disallowed. {reason} {score_str}"
    return None


# --- Enrichment correction formatting ---

def format_enrichment_correction(
    original: StateOutput,
    field: str,
    cache: dict,
    sm: MatchStateMachine,
) -> str | None:
    """Format a post-emit enrichment correction.

    Uses the same position/shot formatting as live events,
    including direction awareness.
    """
    minute = original.minute
    phase_prefix = minute.phase_prefix if minute.base > 0 else ""
    minute_str = minute.display if minute.base > 0 else ""

    event_type = original.data.get("type", "")
    team_id = original.data.get("team_id", "")
    abbr = _team_abbr(sm, team_id)
    pid = original.data.get("player_id", "")
    name = player_name(pid)

    # Coordinate enrichment
    if field in ("PositionX", "PositionY"):
        px = cache.get("PositionX")
        py = cache.get("PositionY")
        if px is None or py is None:
            return None

        # Use direction-aware formatting for fouls/offsides
        if event_type in ("foul", "offside"):
            pos = format_pitch_position(
                float(px), float(py),
                sm.direction, team_id, sm.home.team_id,
                event_type=event_type,
            )
            marker = event_type.upper()
            return (f">> ENRICHED: {minute_str}  {marker:<20}"
                    f"{abbr} | {name} | {pos}")

        # Use shot formatting for shots/goals/saves
        elif event_type in ("shot", "goal", "save"):
            sp = ShotPosition.from_raw(float(px), float(py))
            marker = event_type.upper()
            return (f">> ENRICHED: {minute_str}  {marker:<20}"
                    f"{abbr} | {name} "
                    f"| from {sp.distance_m:.0f}m, {sp.zone}, {sp.side} "
                    f"({float(px):.0f},{float(py):.0f})")

        # Skip for corners (not useful)
        elif event_type == "corner":
            return None

        # Neutral fallback
        marker = event_type.upper() if event_type else "EVENT"
        return (f">> ENRICHED: {minute_str}  {marker:<20}"
                f"{abbr} | {name} | ({float(px):.0f},{float(py):.0f})")

    # Gate coordinate enrichment (shot placement arriving late)
    elif field in ("GoalGatePositionX", "GoalGatePositionY"):
        gx = cache.get("GoalGatePositionX")
        gy = cache.get("GoalGatePositionY")
        if gx is None or gy is None:
            return None
        gp = GoalPlacement(raw_x=float(gx), raw_y=float(gy))
        marker = event_type.upper() if event_type else "SHOT"
        return (f">> ENRICHED: {minute_str}  {marker:<20}"
                f"{abbr} | {name} "
                f"| placed {gp.height}, {gp.side} "
                f"({gp.offset_m:.1f}m off centre, {gp.height_m:.1f}m high)")

    # Description change
    elif field == "EventDescription":
        desc = cache.get("EventDescription", "")
        if isinstance(desc, list) and desc and isinstance(desc[0], dict):
            desc = desc[0].get("Description", "")
        if desc:
            score = sm.score
            return f">> CORRECTED: {desc} [{score[0]}-{score[1]}]"

    # Player ID change
    elif field == "IdPlayer":
        new_id = str(cache.get("IdPlayer", ""))
        new_name = player_name(new_id)
        if new_name:
            score = sm.score
            return f">> CORRECTED: player is {new_name} [{score[0]}-{score[1]}]"

    return None
