#!/usr/bin/env python3
"""
Human terminal client for football-wire.

Reads processed data for queries and raw event files for live watching.
Designed for direct terminal use — readable formatting, subcommands
for different views.

Usage:
    python -m fbw.watch                    # rolling schedule (last 6 + next 6)
    python -m fbw.watch live               # live match scores
    python -m fbw.watch match <id>         # match detail
    python -m fbw.watch group <A-L>        # group standings
    python -m fbw.watch scorers            # top goalscorers
    python -m fbw.watch watch <id>         # live event tail
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .config import init_config, get_config
from .process import read_raw_json, read_raw_events, process_match
from .format import format_match_header, format_event, format_stats, format_score_line, format_match_summary
from .model import EventType


# --- Schedule helpers ---

def load_schedule(config) -> list[dict]:
    """Load cached schedule from raw data."""
    path = config.paths.raw_api_dir / "schedule.json"
    data = read_raw_json(path)
    if isinstance(data, list):
        return data
    return []


def get_localized(name_list, fallback="?") -> str:
    """Extract en-GB description from localized list."""
    if not name_list:
        return fallback
    for entry in name_list:
        if isinstance(entry, dict) and entry.get("Locale") in ("en-GB", "en"):
            return entry.get("Description", fallback)
    if isinstance(name_list[0], dict):
        return name_list[0].get("Description", fallback)
    return str(name_list[0])


def team_abbr(team: dict) -> str:
    return (team or {}).get("Abbreviation", "???")


# --- Match argument resolution ---

def resolve_match_arg(arg: str, config) -> str | None:
    """Resolve a match argument to a match ID.

    Accepts:
      400021469         → match ID directly
      #400021469        → match ID with # prefix (copy-paste)
      GER-CIV           → find match between these teams
      GER               → find most recent/next GER match
    """
    arg = arg.lstrip("#")

    # Pure numeric — match ID
    if arg.isdigit():
        return arg

    # Team code(s)
    schedule = load_schedule(config)
    if not schedule:
        return None

    parts = arg.upper().split("-")

    if len(parts) == 2:
        # HOME-AWAY pair
        home_code, away_code = parts
        for m in schedule:
            h = team_abbr(m.get("HomeTeam") or m.get("Home") or {})
            a = team_abbr(m.get("AwayTeam") or m.get("Away") or {})
            if (h == home_code and a == away_code) or \
               (h == away_code and a == home_code):
                return m.get("IdMatch")
    elif len(parts) == 1:
        # Single team code — find most recent finished or next upcoming
        code = parts[0]
        team_matches = []
        for m in schedule:
            h = team_abbr(m.get("HomeTeam") or m.get("Home") or {})
            a = team_abbr(m.get("AwayTeam") or m.get("Away") or {})
            if code in (h, a):
                team_matches.append(m)

        if not team_matches:
            return None

        # Prefer most recent finished, then next upcoming
        finished = [m for m in team_matches if m.get("MatchStatus") == 0]
        if finished:
            finished.sort(key=lambda m: m.get("Date", ""), reverse=True)
            return finished[0].get("IdMatch")

        upcoming = [m for m in team_matches if m.get("MatchStatus") != 0]
        if upcoming:
            upcoming.sort(key=lambda m: m.get("Date", ""))
            return upcoming[0].get("IdMatch")

    return None


# --- Tournament data helpers ---

def _get_tournament_data():
    """Load canonical tournament data."""
    from .tournament import load_tournament_data
    return load_tournament_data(Path("data/static/tournaments/wc2026-data"))


# --- Commands ---

def cmd_groups(config):
    """All group standings at a glance."""
    td = _get_tournament_data()
    schedule = load_schedule(config)
    matches_dir = config.paths.raw_matches_dir

    for letter in sorted(td.groups.keys()):
        group = td.groups[letter]
        standings = {}
        for code in group.team_codes:
            standings[code] = {"w": 0, "d": 0, "l": 0, "gf": 0, "ga": 0, "pts": 0}

        # Accumulate results
        for m in schedule:
            gn = m.get("GroupName")
            if not gn:
                continue
            g_desc = gn[0].get("Description", "") if isinstance(gn, list) and gn else ""
            if g_desc != f"Group {letter}":
                continue

            mid = m.get("IdMatch", "")
            match_path = matches_dir / f"{mid}.json"
            if not match_path.exists():
                continue

            md = read_raw_json(match_path)
            if not md:
                continue

            ht = md.get("HomeTeam") or md.get("Home") or {}
            at = md.get("AwayTeam") or md.get("Away") or {}
            h_code = team_abbr(ht)
            a_code = team_abbr(at)
            h_score = ht.get("Score")
            a_score = at.get("Score")

            if h_score is None or a_score is None:
                continue
            if h_code not in standings or a_code not in standings:
                continue

            standings[h_code]["gf"] += h_score
            standings[h_code]["ga"] += a_score
            standings[a_code]["gf"] += a_score
            standings[a_code]["ga"] += h_score
            if h_score > a_score:
                standings[h_code]["w"] += 1
                standings[h_code]["pts"] += 3
                standings[a_code]["l"] += 1
            elif h_score < a_score:
                standings[a_code]["w"] += 1
                standings[a_code]["pts"] += 3
                standings[h_code]["l"] += 1
            else:
                standings[h_code]["d"] += 1
                standings[h_code]["pts"] += 1
                standings[a_code]["d"] += 1
                standings[a_code]["pts"] += 1

        ranked = sorted(standings.items(),
                        key=lambda x: (x[1]["pts"], x[1]["gf"] - x[1]["ga"], x[1]["gf"]),
                        reverse=True)

        # Compact one-line-per-group format
        teams_str = "  ".join(
            f"{code} {s['pts']}p" for code, s in ranked
        )
        print(f"  Group {letter}:  {teams_str}")

    print()


def cmd_squad(config, arg: str):
    """Show team squad with positions and shirt numbers."""
    td = _get_tournament_data()
    code = arg.upper().lstrip("#")

    team = td.team(code)
    if not team:
        print(f"Team '{code}' not found")
        return

    print(f"\n  {team.name} ({team.code}) — Group {team.group}")
    print(f"  {team.confederation} | {team.continent}")
    print()

    # Group by position
    positions = {"GK": [], "DF": [], "MF": [], "FW": []}
    for p in team.squad:
        pos = p.position if p.position in positions else "??"
        if pos in positions:
            positions[pos].append(p)

    for pos_name, pos_label in [("GK", "Goalkeepers"), ("DF", "Defenders"),
                                 ("MF", "Midfielders"), ("FW", "Forwards")]:
        players = sorted(positions.get(pos_name, []), key=lambda p: p.number)
        if players:
            print(f"  {pos_label}:")
            for p in players:
                club_str = f" — {p.club}" if p.club else ""
                age_str = ""
                if p.date_of_birth:
                    try:
                        from datetime import date
                        dob = date.fromisoformat(p.date_of_birth)
                        age = (date.today() - dob).days // 365
                        age_str = f", {age}y"
                    except (ValueError, TypeError):
                        pass
                print(f"    #{p.number:<3} {p.name}{age_str}{club_str}")
            print()


def cmd_now(config):
    """Rolling schedule — last 6 finished + next 6 upcoming."""
    schedule = load_schedule(config)
    if not schedule:
        print("No schedule data. Run: python -m fbw fetch")
        return

    finished = [m for m in schedule if m.get("MatchStatus") == 0]
    upcoming = [m for m in schedule if m.get("MatchStatus") != 0]

    # Sort by date
    finished.sort(key=lambda m: m.get("Date", ""), reverse=True)
    upcoming.sort(key=lambda m: m.get("Date", ""))

    recent = list(reversed(finished[:6]))
    soon = upcoming[:6]

    if recent:
        print("  Recent:")
        for m in recent:
            _print_schedule_line(m)

    if recent and soon:
        print()

    if soon:
        print("  Upcoming:")
        for m in soon:
            _print_schedule_line(m)


def _print_schedule_line(m: dict):
    """Print one schedule line."""
    mid = m.get("IdMatch", "?")
    home = team_abbr(m.get("HomeTeam") or m.get("Home") or {})
    away = team_abbr(m.get("AwayTeam") or m.get("Away") or {})
    status = m.get("MatchStatus", 1)
    date_str = m.get("Date", "")

    if status == 0:
        ht = m.get("HomeTeam") or m.get("Home") or {}
        at = m.get("AwayTeam") or m.get("Away") or {}
        hs = ht.get("Score", "?")
        aws = at.get("Score", "?")
        print(f"    [FT]  {home} {hs}-{aws} {away}  #{mid}")
    elif status == 3:
        mt = m.get("MatchTime", "LIVE")
        print(f"    [{mt}] {home} vs {away}  #{mid}")
    else:
        if date_str:
            try:
                dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                time_str = dt.strftime("%Y-%m-%d %H:%M UTC")
            except (ValueError, TypeError):
                time_str = date_str[:16]
        else:
            time_str = "TBD"
        print(f"    [{time_str}] {home} vs {away}  #{mid}")


def cmd_match(config, match_id: str):
    """Detailed match view from processed data."""
    raw_match_path = config.paths.raw_matches_dir / f"{match_id}.json"
    raw_events_path = config.paths.raw_events_dir / f"{match_id}.jsonl"

    result = process_match(raw_match_path, raw_events_path)
    if result is None:
        print(f"No data for #{match_id}")
        return

    match, events = result
    print()
    print(format_match_summary(match, events))


def cmd_scorers(config):
    """Tournament-wide top scorers from processed events."""
    events_dir = config.paths.raw_events_dir
    matches_dir = config.paths.raw_matches_dir
    if not events_dir.exists():
        print("No event data. Run: python -m fbw fetch --backfill")
        return

    scorers: dict[str, dict] = {}  # player_name -> {goals, team}

    for events_file in sorted(events_dir.glob("*.jsonl")):
        match_id = events_file.stem
        result = process_match(
            matches_dir / f"{match_id}.json", events_file
        )
        if not result:
            continue
        match, events = result

        for ev in events:
            if ev.event_type.is_goal and ev.player_name:
                key = ev.player_name
                if key not in scorers:
                    scorers[key] = {"goals": 0, "team": ev.team_abbr}
                scorers[key]["goals"] += 1

    if not scorers:
        print("No goals found")
        return

    ranked = sorted(scorers.items(), key=lambda x: x[1]["goals"], reverse=True)

    print("  Top Scorers:")
    for i, (name, info) in enumerate(ranked[:20], 1):
        print(f"    {i:>2}. {info['goals']} - {name} ({info['team']})")


def cmd_group(config, group_arg: str):
    """Group standings from match data.

    Accepts group letter (A-L) or team code (GER, CIV, etc.).
    """
    from .tournament import load_tournament_data
    from pathlib import Path

    td = load_tournament_data(Path("data/static/tournaments/wc2026-data"))

    # Resolve: team code or group letter?
    letter = group_arg.upper()
    if len(letter) == 3:
        # Team code — find its group
        group = td.group_for_team(letter)
        if not group:
            print(f"Team '{letter}' not found")
            return
        letter = group.letter

    group = td.groups.get(letter)
    if not group:
        print(f"Group '{letter}' not found (use A-L or team code)")
        return

    # Build standings from match data
    standings = {}
    for code in group.team_codes:
        standings[code] = {"w": 0, "d": 0, "l": 0, "gf": 0, "ga": 0, "pts": 0}

    matches_dir = config.paths.raw_matches_dir
    results = []
    schedule = load_schedule(config)

    for m in schedule:
        gn = m.get("GroupName")
        if not gn:
            continue
        g_desc = gn[0].get("Description", "") if isinstance(gn, list) and gn else ""
        if g_desc != f"Group {letter}":
            continue

        mid = m.get("IdMatch", "")
        home_raw = m.get("HomeTeam") or m.get("Home") or {}
        away_raw = m.get("AwayTeam") or m.get("Away") or {}
        h_code = team_abbr(home_raw)
        a_code = team_abbr(away_raw)

        # Try to get score from match data
        match_path = matches_dir / f"{mid}.json"
        h_score = None
        a_score = None
        if match_path.exists():
            md = read_raw_json(match_path)
            if md:
                ht = md.get("HomeTeam") or md.get("Home") or {}
                at = md.get("AwayTeam") or md.get("Away") or {}
                h_score = ht.get("Score")
                a_score = at.get("Score")

        if h_score is not None and a_score is not None:
            results.append(f"  {h_code} {h_score}-{a_score} {a_code}  #{mid}")
            # Update standings
            if h_code in standings and a_code in standings:
                standings[h_code]["gf"] += h_score
                standings[h_code]["ga"] += a_score
                standings[a_code]["gf"] += a_score
                standings[a_code]["ga"] += h_score
                if h_score > a_score:
                    standings[h_code]["w"] += 1
                    standings[h_code]["pts"] += 3
                    standings[a_code]["l"] += 1
                elif h_score < a_score:
                    standings[a_code]["w"] += 1
                    standings[a_code]["pts"] += 3
                    standings[h_code]["l"] += 1
                else:
                    standings[h_code]["d"] += 1
                    standings[h_code]["pts"] += 1
                    standings[a_code]["d"] += 1
                    standings[a_code]["pts"] += 1
        else:
            date_str = m.get("Date", "")
            try:
                dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                time_str = dt.strftime("%Y-%m-%d %H:%M UTC")
            except (ValueError, TypeError):
                time_str = "TBD"
            results.append(f"  {h_code} vs {a_code}  [{time_str}]  #{mid}")

    # Sort standings by pts, then GD, then GF
    ranked = sorted(standings.items(),
                    key=lambda x: (x[1]["pts"], x[1]["gf"] - x[1]["ga"], x[1]["gf"]),
                    reverse=True)

    print(f"\n  Group {letter}")
    print(f"  {'Team':<5} {'P':>2} {'W':>2} {'D':>2} {'L':>2} {'GF':>3} {'GA':>3} {'GD':>4} {'Pts':>4}")
    print(f"  {'-'*30}")
    for code, s in ranked:
        p = s["w"] + s["d"] + s["l"]
        gd = s["gf"] - s["ga"]
        gd_str = f"+{gd}" if gd > 0 else str(gd)
        print(f"  {code:<5} {p:>2} {s['w']:>2} {s['d']:>2} {s['l']:>2} {s['gf']:>3} {s['ga']:>3} {gd_str:>4} {s['pts']:>4}")

    print()
    if results:
        print("  Results:")
        for r in results:
            print(f"  {r}")
    print()


def cmd_watch(config, match_id: str):
    """Live event tail — uses feed module."""
    # Delegate to feed for live watching
    from .feed import cmd_watch as feed_watch
    feed_watch(config, match_id, delay=0)


# --- Main ---

def main():
    parser = argparse.ArgumentParser(
        description="football-wire terminal client",
        epilog="For lookups (groups, squads, players): python -m fbw query",
    )
    parser.add_argument("command", nargs="?", default="now",
                        choices=["now", "live", "match", "summary", "group",
                                 "groups", "squad", "scorers", "watch"],
                        help="Command (default: now)")
    parser.add_argument("arg", nargs="?", help="Match ID, group letter, or team code")
    parser.add_argument("--config", help="Path to config file")

    args = parser.parse_args()
    config = init_config(args.config)

    # Ape-friendly: strip # from match IDs (copy-paste from schedule output)
    arg = args.arg.lstrip("#") if args.arg else args.arg

    if args.command == "now":
        cmd_now(config)
    elif args.command == "live":
        cmd_now(config)  # same view, shows live matches
    elif args.command in ("match", "summary"):
        if not arg:
            print("Usage: python -m fbw.watch match <match_id or TEAM-TEAM>")
            return
        mid = resolve_match_arg(arg, config)
        if not mid:
            print(f"No match found for '{arg}'")
            return
        cmd_match(config, mid)
    elif args.command == "group":
        if not arg:
            print("Usage: python -m fbw.watch group <A-L or team code>")
            return
        cmd_group(config, arg)
    elif args.command == "groups":
        cmd_groups(config)
    elif args.command == "squad":
        if not arg:
            print("Usage: python -m fbw.watch squad <team code>")
            return
        cmd_squad(config, arg)
    elif args.command == "scorers":
        cmd_scorers(config)
    elif args.command == "watch":
        if not arg:
            print("Usage: python -m fbw.watch watch <match_id or TEAM-TEAM>")
            return
        mid = resolve_match_arg(arg, config)
        if not mid:
            print(f"No match found for '{arg}'")
            return
        cmd_watch(config, mid)
    else:
        cmd_now(config)


if __name__ == "__main__":
    main()
