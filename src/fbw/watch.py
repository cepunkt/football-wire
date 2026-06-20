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


# --- Commands ---

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


def cmd_watch(config, match_id: str):
    """Live event tail — uses feed module."""
    # Delegate to feed for live watching
    from .feed import cmd_watch as feed_watch
    feed_watch(config, match_id, delay=0)


# --- Main ---

def main():
    parser = argparse.ArgumentParser(description="football-wire terminal client")
    parser.add_argument("command", nargs="?", default="now",
                        choices=["now", "live", "match", "summary", "scorers", "watch"],
                        help="Command (default: now)")
    parser.add_argument("arg", nargs="?", help="Match ID or group letter")
    parser.add_argument("--config", help="Path to config file")

    args = parser.parse_args()
    config = init_config(args.config)

    if args.command == "now":
        cmd_now(config)
    elif args.command in ("match", "summary"):
        if not args.arg:
            print("Usage: python -m fbw.watch match <match_id>")
            return
        cmd_match(config, args.arg)
    elif args.command == "scorers":
        cmd_scorers(config)
    elif args.command == "watch":
        if not args.arg:
            print("Usage: python -m fbw.watch watch <match_id>")
            return
        cmd_watch(config, args.arg)
    else:
        cmd_now(config)


if __name__ == "__main__":
    main()
