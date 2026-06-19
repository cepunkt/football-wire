#!/usr/bin/env python3
"""
Data daemon for football-wire.

Continuously polls configured data sources and writes to raw/.
Designed to run on the host. Clients read from raw/ via watchdog.

Usage:
    python -m fbw.daemon                     # auto-track today's matches
    python -m fbw.daemon --match 400021507   # track specific match
    python -m fbw.daemon --interval 20       # custom poll interval

The daemon is intentionally simple — it fetches and stores.
No validation, no transformation, no enrichment. That's the
processing layer's job. The daemon just writes what the API says.
"""

import argparse
import json
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .config import init_config, get_config
from .fetch import (
    pull_match, pull_schedule, store_match,
    _write_json, _ensure_dirs, _read_json,
)


# --- Graceful shutdown ---

_running = True


def _handle_signal(signum, frame):
    global _running
    _running = False
    log("Shutdown signal received, finishing current poll...")


signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


# --- Logging ---

def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"  [{ts}] {msg}", flush=True)


# --- Live state tracking ---

def _get_localized(name_list) -> str:
    if not name_list:
        return "?"
    for entry in name_list:
        if isinstance(entry, dict) and entry.get("Locale") in ("en-GB", "en"):
            return entry.get("Description", "?")
    if isinstance(name_list[0], dict):
        return name_list[0].get("Description", "?")
    return str(name_list[0])


def _team_abbr(team: dict) -> str:
    return (team or {}).get("Abbreviation", "???")


def _team_name(team: dict) -> str:
    if not team:
        return "TBD"
    return (team.get("ShortClubName")
            or _get_localized(team.get("TeamName", []))
            or team.get("Abbreviation")
            or "TBD")


def _get_team(match: dict, side: str) -> dict:
    if side == "home":
        return match.get("HomeTeam") or match.get("Home") or {}
    return match.get("AwayTeam") or match.get("Away") or {}


def _get_score(match: dict) -> tuple:
    ht = _get_team(match, "home")
    at = _get_team(match, "away")
    h = match.get("HomeTeamScore")
    if h is None:
        h = ht.get("Score")
    a = match.get("AwayTeamScore")
    if a is None:
        a = at.get("Score")
    return (h, a)


def _match_label(match: dict) -> str:
    home = _team_abbr(_get_team(match, "home"))
    away = _team_abbr(_get_team(match, "away"))
    return f"{home} vs {away}"


def update_live_state(tracked: dict[str, dict], config) -> None:
    """Write live.json with current tracking state."""
    state = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "matches": {},
    }
    for mid, info in tracked.items():
        h_score, a_score = info.get("score", (None, None))
        state["matches"][mid] = {
            "home": info.get("home", "?"),
            "away": info.get("away", "?"),
            "home_score": h_score,
            "away_score": a_score,
            "status": info.get("status", 1),
            "match_time": info.get("match_time", ""),
            "label": info.get("label", ""),
        }
    _write_json(config.paths.raw_api_dir / "live.json", state)


# --- Today's matches ---

def get_todays_matches(schedule: list[dict]) -> list[dict]:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return [m for m in schedule if (m.get("Date") or "")[:10] == today]


# --- Main daemon loop ---

def run_daemon(match_ids: list[str] | None = None, interval: int = 10):
    """Main daemon loop."""
    config = get_config()
    _ensure_dirs(config)

    log("football-wire daemon starting")
    log(f"Poll interval: {interval}s")
    log(f"Data dir: {config.paths.data_dir}")

    # Fetch schedule
    log("Fetching tournament schedule...")
    schedule = pull_schedule(config, log_fn=log)

    # Determine which matches to track
    if match_ids:
        track_ids = match_ids
        log(f"Tracking specific matches: {', '.join(track_ids)}")
    else:
        today = get_todays_matches(schedule)
        track_ids = [m.get("IdMatch") for m in today if m.get("IdMatch")]
        log(f"Auto-tracking today's {len(track_ids)} matches")
        for m in today:
            mid = m.get("IdMatch", "?")
            label = _match_label(m)
            status = m.get("MatchStatus", 1)
            status_str = {0: "FT", 1: "Scheduled", 3: "Live"}.get(status, "?")
            log(f"  #{mid} {label} [{status_str}]")

    # Initial fetch for all tracked matches
    tracked: dict[str, dict] = {}
    finished: set[str] = set()

    for mid in track_ids:
        live_data = pull_match(mid, config, log_fn=log)
        if live_data:
            status = live_data.get("MatchStatus", 1)
            score = _get_score(live_data)
            label = _match_label(live_data)
            tracked[mid] = {
                "home": _team_name(_get_team(live_data, "home")),
                "away": _team_name(_get_team(live_data, "away")),
                "score": score,
                "status": status,
                "match_time": live_data.get("MatchTime", ""),
                "label": label,
            }
            if status == 0:
                finished.add(mid)
                log(f"  #{mid} {label} already finished")

    update_live_state(tracked, config)
    log("Initial fetch complete. Entering poll loop.")
    log("")

    # Main poll loop
    poll_count = 0
    while _running:
        time.sleep(interval)
        if not _running:
            break

        poll_count += 1
        active_ids = [mid for mid in track_ids if mid not in finished]

        if not active_ids:
            log("All tracked matches finished. Daemon stopping.")
            break

        for mid in active_ids:
            if not _running:
                break

            live_data = pull_match(mid, config, log_fn=log)
            if not live_data:
                continue

            status = live_data.get("MatchStatus", 1)
            score = _get_score(live_data)
            match_time = live_data.get("MatchTime", "")
            label = _match_label(live_data)

            tracked[mid] = {
                "home": _team_name(_get_team(live_data, "home")),
                "away": _team_name(_get_team(live_data, "away")),
                "score": score,
                "status": status,
                "match_time": match_time,
                "label": label,
            }

            if status == 0 and mid not in finished:
                finished.add(mid)
                h, a = score
                log(f"FULL TIME: {label} {h}-{a}")

                # Refresh schedule for updated standings
                schedule = pull_schedule(config, log_fn=log)

        update_live_state(tracked, config)

        # Refresh schedule periodically (every 50 polls)
        if poll_count % 50 == 0:
            schedule = pull_schedule(config, log_fn=log)

            # Check for newly live matches (auto mode only)
            if not match_ids:
                today = get_todays_matches(schedule)
                for m in today:
                    mid = m.get("IdMatch")
                    if mid and mid not in track_ids:
                        status = m.get("MatchStatus", 1)
                        if status == 3:
                            track_ids.append(mid)
                            log(f"New live match detected: #{mid} {_match_label(m)}")

    # Final state save
    update_live_state(tracked, config)
    log(f"Daemon stopped. {poll_count} polls completed.")


# --- Main ---

def main():
    parser = argparse.ArgumentParser(description="football-wire daemon")
    parser.add_argument("--config", help="Path to config file")
    parser.add_argument("--interval", type=int,
                        help="Poll interval in seconds (default: from config)")
    parser.add_argument("--match", action="append", dest="matches",
                        help="Track specific match ID (repeatable)")

    args = parser.parse_args()
    config = init_config(args.config)

    interval = args.interval or config.source.poll_interval
    interval = max(10, min(300, interval))

    run_daemon(match_ids=args.matches, interval=interval)


if __name__ == "__main__":
    main()
