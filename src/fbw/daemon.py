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
from datetime import datetime, timedelta, timezone
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

def get_trackable_matches(schedule: list[dict]) -> list[dict]:
    """Get matches within a tracking window: 1 hour ago to 6 hours ahead.

    Handles midnight UTC boundary — a match at 00:00 UTC is trackable
    from 18:00 UTC the day before. American timezone tournaments commonly
    have late evening kickoffs that cross the date boundary.
    """
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(hours=1)
    window_end = now + timedelta(hours=6)
    results = []
    for m in schedule:
        date_str = m.get("Date") or ""
        if not date_str:
            continue
        try:
            # FIFA API dates: "2026-06-21T00:00:00Z"
            match_dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            if window_start <= match_dt <= window_end:
                results.append(m)
        except (ValueError, TypeError):
            continue
    return results


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
        upcoming = get_trackable_matches(schedule)
        track_ids = [m.get("IdMatch") for m in upcoming if m.get("IdMatch")]
        log(f"Auto-tracking {len(track_ids)} matches (1h ago to 6h ahead)")
        for m in upcoming:
            mid = m.get("IdMatch", "?")
            label = _match_label(m)
            status = m.get("MatchStatus", 1)
            status_str = {0: "FT", 1: "Scheduled", 3: "Live"}.get(status, "?")
            log(f"  #{mid} {label} [{status_str}]")

    # Build stage_id lookup from schedule
    stage_ids: dict[str, str] = {}
    for m in schedule:
        mid = m.get("IdMatch", "")
        sid = m.get("IdStage", "")
        if mid and sid:
            stage_ids[mid] = sid

    # Initial fetch for all tracked matches
    tracked: dict[str, dict] = {}
    finished: set[str] = set()

    for mid in track_ids:
        live_data = pull_match(mid, stage_id=stage_ids.get(mid), config=config, log_fn=log)
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

    # ESPN polling setup (opt-in, per-match timers)
    espn_enabled = config.sources.espn
    espn_interval = config.sources.espn_interval
    last_espn_poll: dict[str, float] = {}  # per match_id
    espn_post_match: dict[str, float] = {}  # match_id → finished_at (keep polling 30min)
    if espn_enabled:
        log(f"ESPN stats polling enabled (every {espn_interval}s, per match)")

    # Main poll loop
    poll_count = 0
    while _running:
        time.sleep(interval)
        if not _running:
            break

        poll_count += 1
        active_ids = [mid for mid in track_ids if mid not in finished]

        if not active_ids:
            # Post-match ESPN polling
            if espn_post_match:
                now = time.time()
                expired = [m for m, t in espn_post_match.items()
                           if now - t >= 1800]
                for m in expired:
                    del espn_post_match[m]
                    log(f"  ESPN: post-match polling ended for #{m}")

                for mid in list(espn_post_match.keys()):
                    last_poll = last_espn_poll.get(mid, 0)
                    if now - last_poll >= espn_interval:
                        try:
                            from .espn import poll_and_record
                            row = poll_and_record(mid, "?", "?", config)
                            if row:
                                log(f"  ESPN: post-match stats for #{mid}")
                            last_espn_poll[mid] = now
                        except Exception as e:
                            log(f"  ESPN post-match error: {e}")

            # Check for new matches in the window
            # Refresh schedule every idle cycle (not just every 50 polls)
            schedule = pull_schedule(config, log_fn=log)
            # Rebuild stage_ids from refreshed schedule
            for m in schedule:
                mid = m.get("IdMatch", "")
                sid = m.get("IdStage", "")
                if mid and sid:
                    stage_ids[mid] = sid

            upcoming = get_trackable_matches(schedule)
            new_ids = [m.get("IdMatch") for m in upcoming
                       if m.get("IdMatch") and m.get("IdMatch") not in track_ids]
            if new_ids:
                for mid in new_ids:
                    track_ids.append(mid)
                log(f"New matches entered window: {', '.join(new_ids)}")
            else:
                # Nothing active, nothing upcoming — idle
                # Sleep longer to avoid hammering the schedule API
                if poll_count % 30 == 0:
                    log("Idle — no active or upcoming matches. Waiting...")
                time.sleep(max(interval, 60) - interval)  # at least 60s idle cycle
            continue

        for mid in active_ids:
            if not _running:
                break

            live_data = pull_match(mid, stage_id=stage_ids.get(mid),
                                   config=config, log_fn=log)
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

                # Keep polling ESPN for 30min after FT for final stats
                if espn_enabled:
                    espn_post_match[mid] = time.time()
                    log(f"  ESPN: post-match polling for #{mid} (30min)")

            # ESPN stats polling (per-match timer)
            if espn_enabled:
                should_poll_espn = False
                if status == 3:
                    # Live match — poll on interval
                    should_poll_espn = True
                elif mid in espn_post_match:
                    # Post-match — keep polling for 30min
                    if time.time() - espn_post_match[mid] < 1800:
                        should_poll_espn = True
                    else:
                        del espn_post_match[mid]
                        log(f"  ESPN: post-match polling ended for #{mid}")

                if should_poll_espn:
                    now = time.time()
                    last_poll = last_espn_poll.get(mid, 0)
                    if now - last_poll >= espn_interval:
                        try:
                            from .espn import poll_and_record
                            home_tla = _team_abbr(_get_team(live_data, "home"))
                            away_tla = _team_abbr(_get_team(live_data, "away"))
                            row = poll_and_record(mid, home_tla, away_tla, config)
                            if row:
                                poss_h = row.get("home_possession", "?")
                                poss_a = row.get("away_possession", "?")
                                log(f"  ESPN: {home_tla} {poss_h}% - {poss_a}% {away_tla}")
                            last_espn_poll[mid] = now
                        except Exception as e:
                            log(f"  ESPN error: {e}")

        update_live_state(tracked, config)

        # Refresh schedule periodically (every 50 polls)
        if poll_count % 50 == 0:
            schedule = pull_schedule(config, log_fn=log)

            # Check for newly trackable matches (auto mode only)
            if not match_ids:
                upcoming = get_trackable_matches(schedule)
                for m in upcoming:
                    mid = m.get("IdMatch")
                    if mid and mid not in track_ids:
                        track_ids.append(mid)
                        log(f"New match in window: #{mid} {_match_label(m)}")

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
