"""
Processing layer for football-wire.

Converts raw API data into validated, cross-referenced model objects.
This is where all FIFA API knowledge lives — every known data quality
issue has a handler here.

Design principles:
- Idempotent: same raw input always produces same processed output
- Traceable: trust levels and source tags on every data point
- Replayable: can flush processed/ and rebuild from raw/ at any time
- Cross-referencing: structural match data validates timeline events
"""

import json
from pathlib import Path

from .model import (
    Match, Team, Player, Event, EventType, Minute, Position,
    ShotPosition, GoalPlacement, MatchStats, Trust, MatchStatus,
)


# --- Raw data readers ---

def read_raw_json(filepath: Path) -> dict | None:
    """Read a raw JSON file. Returns None if missing or corrupt."""
    if not filepath.exists():
        return None
    try:
        with open(filepath) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def read_raw_events(filepath: Path) -> list[dict]:
    """Read raw JSONL event file. Skips corrupt lines."""
    events = []
    if not filepath.exists():
        return events
    with open(filepath) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return events


# --- Localization helper ---

from .model import get_localized  # noqa: F401 — re-export for existing callers


# --- Parsing: structural data (high trust) ---

def parse_player(raw: dict, team_abbr: str) -> Player:
    """Parse a player from raw API team data."""
    return Player(
        id=str(raw.get("IdPlayer", "")),
        name=get_localized(raw.get("ShortName") or raw.get("PlayerName", [])),
        shirt_number=raw.get("ShirtNumber"),
        position=_safe_position(raw.get("Position")),
        is_starter=raw.get("Status") == 1,
        team_abbr=team_abbr,
    )


def _safe_position(val) -> Position | None:
    """Convert position int to enum, None if invalid."""
    if val is None:
        return None
    try:
        return Position(val)
    except ValueError:
        return None


def parse_team(raw: dict, side: str = "") -> Team:
    """Parse a team from raw API match data.

    Args:
        raw: the HomeTeam or AwayTeam dict
        side: 'home' or 'away' for context
    """
    abbr = raw.get("Abbreviation", "???")

    # Parse players
    players = {}
    for p_raw in raw.get("Players", []):
        player = parse_player(p_raw, abbr)
        if player.id:
            players[player.id] = player

    # Parse coaches (role 0 = head coach)
    coaches = []
    for c in raw.get("Coaches", []):
        name = get_localized(c.get("Name", []))
        if name and name != "?":
            role = c.get("Role", -1)
            country = c.get("IdCountry", "")
            prefix = "Head Coach" if role == 0 else "Asst."
            coaches.append(f"{name} ({country})")

    return Team(
        id=str(raw.get("IdTeam", "")),
        abbreviation=abbr,
        name=raw.get("ShortClubName") or get_localized(raw.get("TeamName", [])),
        coaches=coaches,
        tactics=raw.get("Tactics", ""),
        players=players,
    )


def parse_match(raw: dict) -> Match:
    """Parse full match state from raw API match data.

    This is the structural ground truth — rosters, venue, officials.
    Updated on each poll but structurally stable.
    """
    # Teams
    home_raw = raw.get("HomeTeam") or raw.get("Home") or {}
    away_raw = raw.get("AwayTeam") or raw.get("Away") or {}
    home = parse_team(home_raw, "home")
    away = parse_team(away_raw, "away")

    # Status
    status_val = raw.get("MatchStatus", 1)
    try:
        status = MatchStatus(status_val)
    except ValueError:
        status = MatchStatus.SCHEDULED

    # Score
    home_score = raw.get("HomeTeamScore")
    if home_score is None:
        home_score = home_raw.get("Score")
    away_score = raw.get("AwayTeamScore")
    if away_score is None:
        away_score = away_raw.get("Score")

    # Stadium
    stadium_raw = raw.get("Stadium", {})
    stadium_name = get_localized(stadium_raw.get("Name", []), "")
    city = get_localized(stadium_raw.get("CityName", []), "")

    # Weather
    weather_raw = raw.get("Weather", {})
    weather_parts = []
    if weather_raw.get("Temperature") is not None:
        weather_parts.append(f"{weather_raw['Temperature']}°C")
    if weather_raw.get("Humidity") is not None:
        weather_parts.append(f"{weather_raw['Humidity']}% humidity")
    if weather_raw.get("WindSpeed") is not None:
        weather_parts.append(f"wind {weather_raw['WindSpeed']} km/h")
    w_type = get_localized(weather_raw.get("TypeLocalized", []), "")
    if w_type:
        weather_parts.append(w_type)

    # Attendance
    attendance = None
    att_raw = raw.get("Attendance")
    if att_raw:
        try:
            attendance = int(att_raw)
        except (ValueError, TypeError):
            pass

    # Kickoff (local)
    kickoff = ""
    local_date = raw.get("LocalDate")
    if local_date:
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(local_date.replace("Z", "+00:00"))
            kickoff = dt.strftime("%H:%M")
        except (ValueError, TypeError):
            pass

    # Referee
    referee = ""
    referee_country = ""
    for off in raw.get("Officials", []):
        role = off.get("OfficialType", 0)
        if role == 1:
            referee = get_localized(off.get("Name", []), "")
            referee_country = off.get("IdCountry", "")
            break

    return Match(
        match_id=str(raw.get("IdMatch", "")),
        home=home,
        away=away,
        status=status,
        home_score=home_score,
        away_score=away_score,
        stadium=stadium_name,
        city=city,
        attendance=attendance,
        weather=", ".join(weather_parts) if weather_parts else "",
        kickoff_local=kickoff,
        referee=referee,
        referee_country=referee_country,
    )


# --- Parsing: timeline events (low trust, needs validation) ---

def parse_event(raw: dict, match: Match) -> Event:
    """Parse a single timeline event against match context.

    Uses match roster for team attribution and sub direction.
    Assigns trust levels based on cross-referencing.
    """
    # Basic fields
    event_id = str(raw.get("EventId", ""))
    raw_type = raw.get("Type", -1)
    try:
        event_type = EventType(raw_type)
    except ValueError:
        event_type = EventType.FOUL  # fallback, will be tagged suspect

    description = get_localized(raw.get("EventDescription", []), "")
    minute = Minute.parse(raw.get("MatchMinute", ""), description)

    # Player IDs
    player_id = str(raw.get("IdPlayer", "") or "")
    sub_player_id = str(raw.get("IdSubPlayer", "") or raw.get("IdPlayerOff", "") or "")

    # Resolve team from roster (trusted) vs API (untrusted)
    team_abbr, team_trust = _resolve_team(player_id, raw, match)

    # Score
    home_goals = raw.get("HomeGoals")
    away_goals = raw.get("AwayGoals")

    # Shot position
    shot_position = None
    pos_x = raw.get("PositionX")
    pos_y = raw.get("PositionY")
    if pos_x is not None and pos_y is not None:
        shot_position = ShotPosition.from_raw(pos_x, pos_y)

    # Goal placement
    goal_placement = None
    gate_x = raw.get("GoalGatePositionX")
    gate_y = raw.get("GoalGatePositionY")
    if gate_x is not None and gate_y is not None:
        goal_placement = GoalPlacement(raw_x=gate_x, raw_y=gate_y)

    # Player name from roster
    player_name = ""
    player = match.player_by_id(player_id)
    if player:
        player_name = player.display_name

    # Sub resolution
    on_player_id = ""
    off_player_id = ""
    sub_trust = Trust.UNKNOWN
    if event_type == EventType.SUB:
        on_player_id, off_player_id, sub_trust = match.resolve_sub(
            player_id, sub_player_id
        )

    event = Event(
        event_id=event_id,
        event_type=event_type,
        minute=minute,
        description=description,
        team_abbr=team_abbr,
        team_trust=team_trust,
        player_id=player_id,
        player_name=player_name,
        sub_player_id=sub_player_id,
        home_goals=home_goals,
        away_goals=away_goals,
        shot_position=shot_position,
        goal_placement=goal_placement,
        on_player_id=on_player_id,
        off_player_id=off_player_id,
        sub_trust=sub_trust,
        logged_at=raw.get("_logged_at", ""),
    )

    return event


def _resolve_team(player_id: str, raw: dict, match: Match) -> tuple[str, Trust]:
    """Resolve team attribution — roster first, API fallback."""
    # 1. Roster lookup (trusted)
    if player_id:
        roster_team = match.team_for_player(player_id)
        if roster_team:
            # Cross-check against API
            api_team_id = str(raw.get("IdTeam", "") or "")
            api_team = ""
            if api_team_id == match.home.id:
                api_team = match.home.abbreviation
            elif api_team_id == match.away.id:
                api_team = match.away.abbreviation

            if api_team and api_team != roster_team:
                # API disagrees with roster — roster wins, mark conflict
                return roster_team, Trust.INFERRED
            return roster_team, Trust.TRUSTED

    # 2. API IdTeam fallback (lower trust)
    api_team_id = str(raw.get("IdTeam", "") or "")
    if api_team_id == match.home.id:
        return match.home.abbreviation, Trust.INFERRED
    elif api_team_id == match.away.id:
        return match.away.abbreviation, Trust.INFERRED

    return "", Trust.UNKNOWN


# --- Event processing pipeline ---

def process_events(raw_events: list[dict], match: Match) -> list[Event]:
    """Full event processing pipeline.

    1. Parse each raw event against match context
    2. Deduplicate by content
    3. Sort by match minute
    4. Detect late arrivals
    5. Apply substitutions to on_pitch tracking
    6. Cross-reference sub direction against team-level subs
    7. Update match stats

    Returns processed events in match-minute order.
    """
    # Parse all events
    events = []
    seen_ids: set[str] = set()
    seen_dedup: set[str] = set()

    for raw in raw_events:
        event = parse_event(raw, match)

        # Deduplicate by EventId
        if event.event_id and event.event_id in seen_ids:
            event.is_duplicate = True
        else:
            if event.event_id:
                seen_ids.add(event.event_id)

        # Content-based dedup
        dk = event.dedup_key
        if dk in seen_dedup:
            event.is_duplicate = True
        else:
            seen_dedup.add(dk)

        events.append(event)

    # Filter duplicates
    events = [e for e in events if not e.is_duplicate]

    # Sort by match minute (primary) and logged_at (secondary)
    events.sort(key=lambda e: (e.minute.value, e.logged_at))

    # Detect late arrivals and apply subs
    last_minute = -1.0
    for event in events:
        # Late arrival detection
        if event.minute.value >= 0 and event.minute.value < last_minute:
            event.is_late = True
        if event.minute.value >= 0:
            last_minute = max(last_minute, event.minute.value)

        # Apply substitutions to match state
        if event.event_type == EventType.SUB:
            match.apply_sub(event.on_player_id, event.off_player_id)

        # Update stats
        if match.stats:
            match.stats.update(event)

        # Score consistency check
        score_trust = match.check_score_consistency(event)
        if score_trust == Trust.SUSPECT:
            # Score went backwards — mark but don't discard
            pass  # TODO: add score_trust field to Event

        # Update match score tracking
        if event.home_goals is not None:
            match.home_score = max(match.home_score or 0, event.home_goals)
        if event.away_goals is not None:
            match.away_score = max(match.away_score or 0, event.away_goals)

    return events


# --- Cross-reference: team-level substitutions ---

def cross_reference_subs(events: list[Event], raw_match: dict, match: Match) -> None:
    """Cross-reference timeline sub events against team-level substitution list.

    The team-level Substitutions list uses IdPlayerOn/IdPlayerOff which
    are reliably labeled (unlike timeline IdPlayer/IdSubPlayer).
    Updates sub trust levels in-place.
    """
    # Build ground truth from team-level subs
    # Key by player IDs to handle multiple subs at the same minute
    truth: list[tuple[str, str, str]] = []  # (team_id, on_id, off_id)

    for side_key in ["HomeTeam", "AwayTeam"]:
        team_raw = raw_match.get(side_key, {})
        for sub in team_raw.get("Substitutions", []):
            on_id = str(sub.get("IdPlayerOn", ""))
            off_id = str(sub.get("IdPlayerOff", ""))
            team_id = str(sub.get("IdTeam", ""))
            if on_id and off_id:
                truth.append((team_id, on_id, off_id))

    # Cross-reference: match timeline subs to team-level subs by player IDs
    for event in events:
        if event.event_type != EventType.SUB:
            continue

        pid_a = event.on_player_id
        pid_b = event.off_player_id

        for team_id, true_on, true_off in truth:
            # Match if either player ID appears in the team-level sub
            if set([pid_a, pid_b]) & set([true_on, true_off]):
                if pid_a == true_on and pid_b == true_off:
                    event.sub_trust = Trust.TRUSTED
                else:
                    # Correct to team-level data
                    event.on_player_id = true_on
                    event.off_player_id = true_off
                    event.sub_trust = Trust.TRUSTED
                break


# --- Full match processing ---

def process_match(raw_match_path: Path, raw_events_path: Path) -> tuple[Match, list[Event]] | None:
    """Process a complete match from raw data files.

    This is the main entry point. Takes raw file paths, returns
    validated Match and Event objects.

    Returns None if raw data is missing or unreadable.
    """
    raw_match = read_raw_json(raw_match_path)
    if not raw_match:
        return None

    raw_events = read_raw_events(raw_events_path)

    # Parse structural match data (high trust)
    match = parse_match(raw_match)

    # Process timeline events (low trust, validated against match)
    events = process_events(raw_events, match)

    # Cross-reference subs against team-level data
    cross_reference_subs(events, raw_match, match)

    match.events = events

    return match, events
