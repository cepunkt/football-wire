"""
Source adapters for the state machine.

Each adapter converts source-specific raw data into StateInput.
The state machine never sees raw API formats — only categorized,
source-agnostic StateInput objects.

Adapters handle the mess: inconsistent event types, inverted fields,
missing data, duplicate IDs. The state machine receives clean input.
"""

from datetime import datetime, timezone
from typing import Any

from fbw.football import MatchMinute, MatchPhase
from fbw.model import EventType
from fbw.state import InputCategory, StateInput, SourceTrust


# --- FIFA API event type → InputCategory mapping ---

_FIFA_CATEGORY: dict[int, InputCategory] = {
    EventType.GOAL: InputCategory.SCORE_CHANGE,
    EventType.PENALTY_GOAL: InputCategory.SCORE_CHANGE,
    EventType.ASSIST: InputCategory.ATTEMPT,             # assist info, not a score change
    EventType.YELLOW: InputCategory.DISCIPLINE,
    EventType.RED: InputCategory.DISCIPLINE,
    EventType.SECOND_YELLOW_RED: InputCategory.DISCIPLINE,
    EventType.SUB: InputCategory.PLAYER_CHANGE,
    EventType.PENALTY_AWARDED: InputCategory.SET_PIECE,
    EventType.PERIOD_START: InputCategory.PERIOD_CHANGE,
    EventType.PERIOD_END: InputCategory.PERIOD_CHANGE,
    EventType.SHOT: InputCategory.ATTEMPT,
    EventType.SAVE: InputCategory.ATTEMPT,
    EventType.OFFSIDE: InputCategory.DISCIPLINE,        # not a card, but a ruling
    EventType.CORNER: InputCategory.SET_PIECE,
    EventType.FOUL: InputCategory.DISCIPLINE,
    EventType.INJURY: InputCategory.MATCH_INTERRUPT,
    EventType.VAR: InputCategory.MATCH_INTERRUPT,
    EventType.DELAY: InputCategory.MATCH_INTERRUPT,
    EventType.RESUME: InputCategory.MATCH_INTERRUPT,
    EventType.COIN: InputCategory.PERIOD_CHANGE,
    EventType.COIN_SIDE: InputCategory.PERIOD_CHANGE,
    EventType.PAUSE: InputCategory.MATCH_INTERRUPT,
}


# --- FIFA period mapping ---

def _fifa_period_to_phase(period: int | None, action: str) -> MatchPhase | None:
    """Map FIFA Period field to MatchPhase.

    FIFA uses Period: 3=1H, 5=2H, 7=ET1, 9=ET2, 11=PEN (approximate).
    But these are unreliable. We mainly use the event count
    (how many PERIOD_START events we've seen) to determine phase.
    """
    # Period counting is more reliable than the Period field
    return None  # let the state machine figure it out from event sequence


# --- FIFA adapter ---

def fifa_event_to_input(
    raw: dict[str, Any],
    timestamp: datetime | None = None,
) -> StateInput | None:
    """Convert a raw FIFA API timeline event to StateInput.

    Returns None if the event type is unknown or should be skipped.
    """
    # Event type
    event_type_raw = raw.get("Type")
    if event_type_raw is None:
        return None
    try:
        event_type = EventType(int(event_type_raw))
    except (ValueError, TypeError):
        return None

    category = _FIFA_CATEGORY.get(event_type)
    if category is None:
        return None

    # Description (needed early for minute context)
    description = ""
    desc_list = raw.get("EventDescription")
    if desc_list and isinstance(desc_list, list):
        for d in desc_list:
            if isinstance(d, dict) and d.get("Locale") in ("en-GB", "en"):
                description = d.get("Description", "")
                break
        if not description and isinstance(desc_list[0], dict):
            description = desc_list[0].get("Description", "")
    elif isinstance(desc_list, str):
        description = desc_list

    # Minute (with description context for empty minutes like half-time subs)
    minute_raw = raw.get("MatchMinute", "")
    minute = MatchMinute.from_notation(minute_raw, description=description)

    # Source ID
    event_id = str(raw.get("EventId", "") or "")

    # Timestamp
    if timestamp is None:
        ts_str = raw.get("Timestamp")
        if ts_str:
            try:
                timestamp = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                timestamp = datetime.now(timezone.utc)
        else:
            timestamp = datetime.now(timezone.utc)

    # Player IDs
    player_id = str(raw.get("IdPlayer", "") or "")
    sub_player_id = str(raw.get("IdSubPlayer", "") or raw.get("IdPlayerOff", "") or "")
    team_id = str(raw.get("IdTeam", "") or "")

    # Coordinates
    pos_x = raw.get("PositionX")
    pos_y = raw.get("PositionY")
    gate_x = raw.get("GoalGatePositionX")
    gate_y = raw.get("GoalGatePositionY")

    # Score from event (unreliable — state machine uses its own)
    home_goals = raw.get("HomeGoals")
    away_goals = raw.get("AwayGoals")

    # --- Build category-specific data ---

    data: dict[str, Any] = {
        "event_type_raw": int(event_type),
        "description": description,
    }

    if category == InputCategory.PERIOD_CHANGE:
        if event_type == EventType.PERIOD_START:
            data["action"] = "start"
        elif event_type == EventType.PERIOD_END:
            data["action"] = "end"
        elif event_type == EventType.COIN:
            data["action"] = "coin_toss"
        elif event_type == EventType.COIN_SIDE:
            data["action"] = "coin_side"

    elif category == InputCategory.SCORE_CHANGE:
        data["type"] = "goal"
        data["player_id"] = player_id
        data["team_id"] = team_id
        data["own_goal"] = False  # TODO: detect from description
        data["is_penalty"] = event_type == EventType.PENALTY_GOAL
        data["event_score"] = (home_goals, away_goals)

    elif category == InputCategory.PLAYER_CHANGE:
        # For subs, use player_id/sub_player_id which were read from
        # raw BEFORE this function was called. If the caller applied
        # enrichments that corrupted IdPlayer, we detect it here:
        # both IDs should not be the same player.
        pa = player_id
        pb = sub_player_id
        if pa == pb and pa:
            # Enrichment corrupted one ID to match the other.
            # Fall back to original raw values if available.
            orig_a = str(raw.get("_orig_IdPlayer", "") or "")
            orig_b = str(raw.get("_orig_IdSubPlayer", "") or "")
            if orig_a and orig_b and orig_a != orig_b:
                pa, pb = orig_a, orig_b
        data["player_a"] = pa
        data["player_b"] = pb
        data["team_id"] = team_id

    elif category == InputCategory.DISCIPLINE:
        if event_type == EventType.FOUL:
            data["card_type"] = "foul"
        elif event_type == EventType.YELLOW:
            data["card_type"] = "yellow"
        elif event_type == EventType.RED:
            data["card_type"] = "red"
        elif event_type == EventType.SECOND_YELLOW_RED:
            data["card_type"] = "second_yellow"
        elif event_type == EventType.OFFSIDE:
            data["card_type"] = "offside"
        data["player_id"] = player_id
        data["team_id"] = team_id

    elif category == InputCategory.SET_PIECE:
        if event_type == EventType.CORNER:
            data["type"] = "corner"
        elif event_type == EventType.PENALTY_AWARDED:
            data["type"] = "penalty_awarded"
        data["player_id"] = player_id
        data["team_id"] = team_id

    elif category == InputCategory.ATTEMPT:
        if event_type == EventType.SHOT:
            data["type"] = "shot"
            data["player_id"] = player_id
            data["team_id"] = team_id
            if pos_x is not None:
                data["position_x"] = pos_x
                data["position_y"] = pos_y
            if gate_x is not None:
                data["gate_x"] = gate_x
                data["gate_y"] = gate_y
                data["on_target"] = True
            else:
                # Position but no gate coords = off target or blocked
                data["on_target"] = False
        elif event_type == EventType.SAVE:
            data["type"] = "save"
            data["team_id"] = team_id
        elif event_type == EventType.ASSIST:
            data["type"] = "assist"
            data["player_id"] = player_id
            data["team_id"] = team_id

    elif category == InputCategory.MATCH_INTERRUPT:
        if event_type in (EventType.PAUSE, EventType.DELAY):
            data["action"] = "pause"
        elif event_type == EventType.RESUME:
            data["action"] = "resume"
        elif event_type == EventType.INJURY:
            data["action"] = "injury"
        elif event_type == EventType.VAR:
            data["action"] = "var_review"
        data["player_id"] = player_id

    return StateInput(
        category=category,
        minute=minute,
        data=data,
        source="fifa",
        trust=SourceTrust.EVENT,
        timestamp=timestamp,
        source_id=event_id,
    )


def fifa_match_to_score(match_data: dict) -> StateInput:
    """Convert FIFA match endpoint data to a score verification input.

    The match endpoint has the canonical score. This is used to
    cross-check event-derived scores and detect voided goals.
    """
    home_team = match_data.get("HomeTeam") or match_data.get("Home") or {}
    away_team = match_data.get("AwayTeam") or match_data.get("Away") or {}

    home_score = match_data.get("HomeTeamScore")
    if home_score is None:
        home_score = home_team.get("Score")
    away_score = match_data.get("AwayTeamScore")
    if away_score is None:
        away_score = away_team.get("Score")

    return StateInput(
        category=InputCategory.SCORE_VERIFY,
        minute=MatchMinute(base=0, added=0, raw=""),
        data={
            "type": "score_verification",
            "home_score": home_score,
            "away_score": away_score,
            "home_team_id": str(home_team.get("IdTeam", "")),
            "away_team_id": str(away_team.get("IdTeam", "")),
        },
        source="fifa",
        trust=SourceTrust.MATCH_DATA,
        timestamp=datetime.now(timezone.utc),
    )


def espn_stats_to_input(
    stats: dict[str, Any],
    minute: MatchMinute,
) -> StateInput:
    """Convert ESPN stats snapshot to StateInput."""
    return StateInput(
        category=InputCategory.STATS_SNAPSHOT,
        minute=minute,
        data=stats,
        source="espn",
        trust=SourceTrust.ESPN,
        timestamp=datetime.now(timezone.utc),
    )
