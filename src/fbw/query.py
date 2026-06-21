"""
Query tool for football-wire.

Reference lookups against static + processed data. No daemon needed.
Different from watch.py which handles live/temporal operations.

Usage:
    python -m fbw query groups             # all 12 groups at a glance
    python -m fbw query group E            # group standings + results
    python -m fbw query group AUT          # find team's group
    python -m fbw query squad JPN          # 26-man squad
    python -m fbw query match GER-CIV      # match detail
    python -m fbw query match ECU          # latest match for team
    python -m fbw query scorers            # top goalscorers
    python -m fbw query player Musiala     # player search (across squads)
"""

import argparse
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from .config import init_config, get_config


# --- Tournament data ---

def _get_tournament_data():
    from .tournament import load_tournament_data
    config = get_config()
    return load_tournament_data(config.tournament.data_path)


def _load_schedule(config):
    from .process import read_raw_json
    path = config.paths.raw_api_dir / "schedule.json"
    data = read_raw_json(path)
    return data if isinstance(data, list) else []


def _team_abbr(team: dict) -> str:
    return (team or {}).get("Abbreviation", "???")


def _get_localized(name_list, fallback="?") -> str:
    if not name_list:
        return fallback
    for entry in name_list:
        if isinstance(entry, dict) and entry.get("Locale") in ("en-GB", "en"):
            return entry.get("Description", fallback)
    if isinstance(name_list[0], dict):
        return name_list[0].get("Description", fallback)
    return str(name_list[0])


# --- Match argument resolution ---

def _resolve_match(arg: str, config) -> str | None:
    """Resolve match arg (ID, #ID, TEAM-TEAM, TEAM) to match ID."""
    arg = arg.lstrip("#")
    if arg.isdigit():
        return arg

    schedule = _load_schedule(config)
    parts = arg.upper().split("-")

    if len(parts) == 2:
        for m in schedule:
            h = _team_abbr(m.get("HomeTeam") or m.get("Home") or {})
            a = _team_abbr(m.get("AwayTeam") or m.get("Away") or {})
            if (h == parts[0] and a == parts[1]) or \
               (h == parts[1] and a == parts[0]):
                return m.get("IdMatch")
    elif len(parts) == 1:
        code = parts[0]
        finished = []
        upcoming = []
        for m in schedule:
            h = _team_abbr(m.get("HomeTeam") or m.get("Home") or {})
            a = _team_abbr(m.get("AwayTeam") or m.get("Away") or {})
            if code in (h, a):
                if m.get("MatchStatus") == 0:
                    finished.append(m)
                else:
                    upcoming.append(m)
        if finished:
            finished.sort(key=lambda m: m.get("Date", ""), reverse=True)
            return finished[0].get("IdMatch")
        if upcoming:
            upcoming.sort(key=lambda m: m.get("Date", ""))
            return upcoming[0].get("IdMatch")
    return None


# --- Commands ---

def cmd_groups(config):
    """All group standings at a glance."""
    from .process import read_raw_json
    td = _get_tournament_data()
    schedule = _load_schedule(config)
    matches_dir = config.paths.raw_matches_dir

    print()
    for letter in sorted(td.groups.keys()):
        group = td.groups[letter]
        standings = {}
        for code in group.team_codes:
            standings[code] = {"w": 0, "d": 0, "l": 0, "gf": 0, "ga": 0, "pts": 0}

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
            h_code, a_code = _team_abbr(ht), _team_abbr(at)
            h_score, a_score = ht.get("Score"), at.get("Score")
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
        teams_str = "  ".join(f"{code} {s['pts']}p" for code, s in ranked)
        print(f"  Group {letter}:  {teams_str}")
    print()


def cmd_group(config, arg: str):
    """Detailed group standings + results."""
    # Reuse watch.py's implementation
    from .watch import cmd_group as _cmd_group
    _cmd_group(config, arg)


def cmd_squad(config, arg: str):
    """Team squad with positions, ages, clubs."""
    td = _get_tournament_data()
    code = arg.upper().lstrip("#")

    team = td.team(code)
    if not team:
        print(f"  Team '{code}' not found")
        return

    print(f"\n  {team.name} ({team.code}) — Group {team.group}")
    print(f"  {team.confederation} | {team.continent}")
    print()

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
                        dob = date.fromisoformat(p.date_of_birth)
                        age = (date.today() - dob).days // 365
                        age_str = f", {age}y"
                    except (ValueError, TypeError):
                        pass
                print(f"    #{p.number:<3} {p.name}{age_str}{club_str}")
            print()


def cmd_match(config, arg: str):
    """Match detail."""
    mid = _resolve_match(arg, config)
    if not mid:
        print(f"  No match found for '{arg}'")
        return
    from .watch import cmd_match as _cmd_match
    _cmd_match(config, mid)


def cmd_scorers(config):
    """Tournament top scorers."""
    from .watch import cmd_scorers as _cmd_scorers
    _cmd_scorers(config)


def cmd_player(config, arg: str):
    """Search for a player across all squads."""
    td = _get_tournament_data()
    query = arg.lower()
    results = []

    for code, team in sorted(td.teams.items()):
        for p in team.squad:
            if query in p.name.lower():
                age_str = ""
                if p.date_of_birth:
                    try:
                        dob = date.fromisoformat(p.date_of_birth)
                        age = (date.today() - dob).days // 365
                        age_str = f", {age}y"
                    except (ValueError, TypeError):
                        pass
                club_str = f" — {p.club}" if p.club else ""
                results.append(
                    f"  #{p.number:<3} {p.name} ({p.position}{age_str}) "
                    f" {team.code} {club_str}"
                )

    if results:
        print(f"\n  Found {len(results)} player(s):")
        for r in results:
            print(r)
        print()
    else:
        print(f"  No player matching '{arg}' found")


# --- Main ---

def main():
    parser = argparse.ArgumentParser(description="football-wire query tool")
    parser.add_argument("command", nargs="?", default="groups",
                        choices=["groups", "group", "squad", "match",
                                 "scorers", "player"],
                        help="Query command (default: groups)")
    parser.add_argument("arg", nargs="?", help="Team code, group letter, match ID, or player name")
    parser.add_argument("--config", help="Path to config file")

    args = parser.parse_args()
    config = init_config(args.config)

    arg = args.arg.lstrip("#") if args.arg else args.arg

    if args.command == "groups":
        cmd_groups(config)
    elif args.command == "group":
        if not arg:
            print("Usage: fbw query group <A-L or team code>")
            return
        cmd_group(config, arg)
    elif args.command == "squad":
        if not arg:
            print("Usage: fbw query squad <team code>")
            return
        cmd_squad(config, arg)
    elif args.command == "match":
        if not arg:
            print("Usage: fbw query match <match_id or TEAM-TEAM>")
            return
        cmd_match(config, arg)
    elif args.command == "scorers":
        cmd_scorers(config)
    elif args.command == "player":
        if not arg:
            print("Usage: fbw query player <name>")
            return
        cmd_player(config, arg)
    else:
        cmd_groups(config)


if __name__ == "__main__":
    main()
