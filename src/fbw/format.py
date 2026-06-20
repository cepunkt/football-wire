"""
Formatting layer for football-wire.

Converts model objects into display strings. Used by all consumers
(feed, watch, query). No data logic here — just presentation.

Two output modes:
- Human: formatted for terminal readability
- LM: one line per event, optimised for Monitor notification context
"""

from pathlib import Path

from .model import (
    Match, Team, Player, Event, EventType, Minute,
    ShotPosition, GoalPlacement, MatchStats, Trust, Position,
)


# --- Preamble ---

PREAMBLE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "static" / "preamble"


def format_preamble(preamble_dir: Path | None = None) -> str:
    """Load and concatenate all preamble files.

    Reads all .md files from the preamble directory in sorted order.
    Drop a file to remove a section, add one for new context.
    """
    d = preamble_dir or PREAMBLE_DIR
    if not d.exists():
        return ""
    parts = []
    for f in sorted(d.glob("*.md")):
        content = f.read_text().strip()
        if content:
            parts.append(content)
    return "\n\n".join(parts)


# --- Event markers ---

EVENT_MARKERS = {
    EventType.GOAL: ">>GOAL<<",
    EventType.ASSIST: "ASSIST",
    EventType.YELLOW: "YELLOW",
    EventType.RED: "RED",
    EventType.SECOND_YELLOW_RED: "2ND YELLOW/RED",
    EventType.SUB: "SUB",
    EventType.PENALTY_AWARDED: "!! PENALTY",
    EventType.PERIOD_START: "--- PERIOD",
    EventType.PERIOD_END: "--- PERIOD END",
    EventType.SHOT: "SHOT",
    EventType.OFFSIDE: "OFFSIDE",
    EventType.CORNER: "CORNER",
    EventType.FOUL: "FOUL",
    EventType.PENALTY_GOAL: ">>GOAL<< (PEN)",
    EventType.SAVE: "SAVE",
    EventType.INJURY: "INJURY",
    EventType.VAR: "** VAR",
    EventType.DELAY: "DELAY",
    EventType.RESUME: "RESUME",
    EventType.COIN: "COIN",
    EventType.PAUSE: "PAUSE",
}


# --- Shot confidence ---

def shot_confidence(event: Event) -> str:
    """Determine shot confidence from available data."""
    if event.shot_position and event.goal_placement:
        return "on target"
    elif event.shot_position:
        return "off target"
    return "attempt"


# --- Event formatting ---

def format_event(event: Event, match: Match | None = None) -> str:
    """Format a single event as one line.

    Output: "  16'  >>GOAL<<              MEX | description [1-0]"
    """
    minute = event.minute.raw if event.minute.raw else ""
    marker = EVENT_MARKERS.get(event.event_type, event.event_type.name)

    desc = event.description

    # Team tag (skip for structural events)
    team_prefix = ""
    if event.team_abbr and not event.event_type.is_structural:
        team_prefix = f"{event.team_abbr} | "

    # Score
    score = ""
    if event.home_goals is not None and event.away_goals is not None:
        score = f" [{event.home_goals}-{event.away_goals}]"

    # Shot enrichment — provide all available data
    if event.event_type in (EventType.GOAL, EventType.SHOT, EventType.PENALTY_GOAL):
        shot_parts = []
        if event.shot_position:
            sp = event.shot_position
            shot_parts.append(
                f"from {sp.distance_m:.0f}m, {sp.zone}, {sp.side}"
                f" ({sp.raw_x:.0f},{sp.raw_y:.0f})"
            )
        if event.goal_placement:
            gp = event.goal_placement
            shot_parts.append(
                f"placed {gp.height}, {gp.side}"
                f" ({gp.raw_x:.0f},{gp.raw_y:.0f})"
            )

        # Confidence tag for shots
        if event.event_type == EventType.SHOT:
            conf = shot_confidence(event)
            if conf != "attempt":
                marker = f"SHOT ({conf})"

        if shot_parts:
            desc = f"{desc} | {' '.join(shot_parts)}"

    # Sub enrichment
    if event.event_type == EventType.SUB and match:
        on_player = match.player_by_id(event.on_player_id)
        off_player = match.player_by_id(event.off_player_id)
        sub_parts = []
        if on_player:
            sub_parts.append(f"ON: {on_player.display_name}")
        if off_player:
            sub_parts.append(f"OFF: {off_player.display_name}")
        if sub_parts:
            trust_tag = f" [{event.sub_trust.value}]" if event.sub_trust != Trust.TRUSTED else ""
            desc = f"{desc} | {', '.join(sub_parts)}{trust_tag}"

    # Late arrival marker
    late = "!! " if event.is_late else ""

    return f"{late}{minute:>7s}  {marker:<22s}  {team_prefix}{desc}{score}"


# --- Match header ---

def format_match_header(match: Match) -> str:
    """Format match header with teams, lineups, venue, referee."""
    lines = []

    # Score line
    if match.home_score is not None and match.away_score is not None:
        lines.append(f"{match.home.name} {match.home_score} - {match.away_score} "
                      f"{match.away.name} (# {match.match_id})")
    else:
        lines.append(f"{match.home.name} vs {match.away.name} (# {match.match_id})")

    # Venue
    if match.stadium:
        lines.append(f"Venue: {match.stadium}, {match.city}")

    # Attendance
    if match.attendance:
        lines.append(f"Attendance: {match.attendance:,}")

    # Weather
    if match.weather:
        lines.append(f"Weather: {match.weather}")

    # Kickoff
    if match.kickoff_local:
        lines.append(f"Kickoff: {match.kickoff_local} local")

    # Referee
    if match.referee:
        lines.append(f"Referee: {match.referee} ({match.referee_country})")

    # Coaches
    for team in [match.home, match.away]:
        if team.coaches:
            lines.append(f"Coach {team.name}: {team.coaches[0]}")

    # Lineups
    pos_order = [Position.GK, Position.DF, Position.MF, Position.FW]
    for team in [match.home, match.away]:
        starters = team.starters
        if not starters:
            continue

        starters.sort(key=lambda p: (p.position or Position.FW).value)

        names_by_pos: dict[str, list[str]] = {}
        for p in starters:
            pos = str(p.position) if p.position is not None else "?"
            entry = p.display_name
            if p.is_starter and any(
                getattr(p, 'captain', False) for _ in [None]
            ):
                pass  # captain handling if needed
            names_by_pos.setdefault(pos, []).append(entry)

        sections = []
        for pos in pos_order:
            pos_name = str(pos)
            if pos_name in names_by_pos:
                sections.append(", ".join(names_by_pos[pos_name]))

        formation = f" ({team.tactics})" if team.tactics else ""
        lines.append(f"{team.name}{formation}: {'; '.join(sections)}")

    return "\n".join(lines)


# --- Stats block ---

def format_stats(stats: MatchStats, minute: str = "") -> str:
    """Format compact stats summary block."""
    h = stats.home_abbr
    a = stats.away_abbr
    hs = stats.counters.get(h, {})
    as_ = stats.counters.get(a, {})

    lines = [f"--- Stats {minute} ---"]
    lines.append(f"{'':>14s}  {h:>5s}  {a:>5s}")

    for label, key in [
        ("Shots", "shots"),
        ("Goals", "goals"),
        ("Fouls", "fouls"),
        ("Offsides", "offsides"),
        ("Corners", "corners"),
        ("Yellows", "yellows"),
        ("Reds", "reds"),
        ("Saves", "saves"),
    ]:
        hv = hs.get(key, 0)
        av = as_.get(key, 0)
        if hv or av:
            lines.append(f"{label:>14s}  {hv:>5d}  {av:>5d}")

    lines.append("---")
    return "\n".join(lines)


# --- Score line ---

def format_score_line(match: Match) -> str:
    """Compact one-line score."""
    status = match.status.name
    h = match.home_score if match.home_score is not None else "?"
    a = match.away_score if match.away_score is not None else "?"
    return f"[{status}] {match.home.name} {h}-{a} {match.away.name} (#{match.match_id})"


# --- Team profile ---

TEAMS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "static" / "teams"


def format_match_summary(match: Match, events: list[Event]) -> str:
    """Deterministic match summary from processed data.

    Shows: score, key events (goals, cards, subs), then stat totals.
    No LLM needed — pure counting and filtering.
    """
    lines = []

    # Header
    h = match.home
    a = match.away
    lines.append(f"{h.name} {match.home_score}-{match.away_score} {a.name}")
    lines.append(f"  {match.stadium}, {match.city}")
    if match.attendance:
        lines.append(f"  Attendance: {match.attendance:,}")
    if match.referee:
        lines.append(f"  Referee: {match.referee} ({match.referee_country})")
    lines.append("")

    # Goals
    goals = [e for e in events if e.event_type.is_goal]
    if goals:
        lines.append("Goals:")
        for ev in goals:
            player = match.player_by_id(ev.player_id)
            name = player.display_name if player else ev.description
            shot_info = ""
            if ev.shot_position:
                shot_info = f" ({ev.shot_position.distance_m:.0f}m, {ev.shot_position.zone})"
            lines.append(f"  {ev.minute}  {ev.team_abbr}  {name}{shot_info}")
        lines.append("")

    # Cards
    cards = [e for e in events if e.event_type.is_card]
    if cards:
        lines.append("Cards:")
        for ev in cards:
            player = match.player_by_id(ev.player_id)
            name = player.display_name if player else ev.description
            card_type = "Y" if ev.event_type == EventType.YELLOW else "R"
            lines.append(f"  {ev.minute}  {card_type}  {ev.team_abbr}  {name}")
        lines.append("")

    # Substitutions
    subs = [e for e in events if e.event_type == EventType.SUB]
    if subs:
        lines.append("Substitutions:")
        for ev in subs:
            on_p = match.player_by_id(ev.on_player_id)
            off_p = match.player_by_id(ev.off_player_id)
            on_name = on_p.display_name if on_p else "?"
            off_name = off_p.display_name if off_p else "?"
            lines.append(f"  {ev.minute}  {ev.team_abbr}  ON: {on_name}, OFF: {off_name}")
        lines.append("")

    # Stat summary
    if match.stats:
        lines.append(f"{'':>14s}  {h.abbreviation:>5s}  {a.abbreviation:>5s}")

        # Compute extra stats not in MatchStats
        shots_on = {h.abbreviation: 0, a.abbreviation: 0}
        shots_off = {h.abbreviation: 0, a.abbreviation: 0}
        for ev in events:
            if ev.event_type == EventType.SHOT and ev.team_abbr in shots_on:
                if ev.shot_position and ev.goal_placement:
                    shots_on[ev.team_abbr] += 1
                elif ev.shot_position:
                    shots_off[ev.team_abbr] += 1

        for label, key in [
            ("Shots", "shots"),
            ("Goals", "goals"),
            ("Fouls", "fouls"),
            ("Offsides", "offsides"),
            ("Corners", "corners"),
            ("Yellows", "yellows"),
            ("Reds", "reds"),
            ("Saves", "saves"),
        ]:
            hv = match.stats.counters.get(h.abbreviation, {}).get(key, 0)
            av = match.stats.counters.get(a.abbreviation, {}).get(key, 0)
            if hv or av:
                lines.append(f"{label:>14s}  {hv:>5d}  {av:>5d}")

        # Shots breakdown
        if any(shots_on.values()) or any(shots_off.values()):
            lines.append(f"{'On target':>14s}  {shots_on[h.abbreviation]:>5d}  {shots_on[a.abbreviation]:>5d}")
            lines.append(f"{'Off target':>14s}  {shots_off[h.abbreviation]:>5d}  {shots_off[a.abbreviation]:>5d}")

    return "\n".join(lines)


def load_team_profile(abbreviation: str, teams_dir: Path | None = None) -> str:
    """Load team profile markdown if available."""
    d = teams_dir or TEAMS_DIR
    path = d / f"{abbreviation}.md"
    if path.exists():
        return path.read_text().strip()
    return ""
