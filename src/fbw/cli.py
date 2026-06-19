"""
CLI entry points for football-wire.

Usage:
    python -m fbw fetch              # pull schedule + today's matches
    python -m fbw fetch --backfill   # pull all historical matches
    python -m fbw process            # process raw/ -> processed/
    python -m fbw process --flush    # clear processed, rebuild from raw
    python -m fbw process --match ID # process a single match
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from .config import init_config, get_config
from .fetch import pull_schedule, pull_match, backfill
from .process import process_match, read_raw_json


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"  [{ts}] {msg}", flush=True)


# --- Commands ---

def cmd_fetch(args):
    """Fetch raw data from APIs."""
    config = get_config()

    if args.backfill:
        backfill(config, force=args.force, log_fn=log)
        return

    # Pull schedule
    schedule = pull_schedule(config, log_fn=log)

    if args.match:
        for mid in args.match:
            pull_match(mid, config, log_fn=log)
        return

    # Auto: pull today's matches
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_matches = [m for m in schedule if (m.get("Date") or "")[:10] == today]
    log(f"Today: {len(today_matches)} matches")

    for m in today_matches:
        mid = m.get("IdMatch", "?")
        pull_match(mid, config, log_fn=log)


def cmd_process(args):
    """Process raw/ -> processed/."""
    config = get_config()
    processed_dir = config.paths.processed_dir
    processed_matches = config.paths.processed_matches_dir
    processed_events = config.paths.processed_events_dir

    # Flush if requested
    if args.flush:
        import shutil
        if processed_dir.exists():
            shutil.rmtree(processed_dir)
            log("Flushed processed/")

    processed_matches.mkdir(parents=True, exist_ok=True)
    processed_events.mkdir(parents=True, exist_ok=True)

    # Find raw matches to process
    raw_matches_dir = config.paths.raw_matches_dir
    raw_events_dir = config.paths.raw_events_dir

    if args.match:
        match_ids = args.match
    else:
        # Process all available raw matches
        match_ids = []
        if raw_matches_dir.exists():
            match_ids = [f.stem for f in raw_matches_dir.glob("*.json")]

    log(f"Processing {len(match_ids)} matches")

    for mid in sorted(match_ids):
        raw_match_path = raw_matches_dir / f"{mid}.json"
        raw_events_path = raw_events_dir / f"{mid}.jsonl"

        result = process_match(raw_match_path, raw_events_path)
        if result is None:
            log(f"  {mid}: no raw data, skipping")
            continue

        match, events = result

        # Write processed match state
        match_out = {
            "match_id": match.match_id,
            "home": {
                "abbreviation": match.home.abbreviation,
                "name": match.home.name,
                "score": match.home_score,
                "starters": len(match.home.starters),
                "tactics": match.home.tactics,
            },
            "away": {
                "abbreviation": match.away.abbreviation,
                "name": match.away.name,
                "score": match.away_score,
                "starters": len(match.away.starters),
                "tactics": match.away.tactics,
            },
            "status": match.status.name.lower(),
            "stadium": match.stadium,
            "city": match.city,
            "attendance": match.attendance,
            "referee": match.referee,
            "stats": match.stats.counters if match.stats else {},
            "events_count": len(events),
            "subs_count": sum(1 for e in events if e.event_type.name == "SUB"),
        }
        with open(processed_matches / f"{mid}.json", "w") as f:
            json.dump(match_out, f, indent=2, ensure_ascii=False)

        # Write processed events
        with open(processed_events / f"{mid}.jsonl", "w") as f:
            for ev in events:
                ev_out = {
                    "event_id": ev.event_id,
                    "type": ev.event_type.name,
                    "minute": ev.minute.raw,
                    "minute_value": ev.minute.value,
                    "description": ev.description,
                    "team": ev.team_abbr,
                    "team_trust": ev.team_trust.value,
                    "player_name": ev.player_name,
                    "is_late": ev.is_late,
                }
                # Sub info
                if ev.event_type.name == "SUB":
                    on_p = match.player_by_id(ev.on_player_id)
                    off_p = match.player_by_id(ev.off_player_id)
                    ev_out["on"] = on_p.display_name if on_p else ""
                    ev_out["off"] = off_p.display_name if off_p else ""
                    ev_out["sub_trust"] = ev.sub_trust.value

                # Shot info
                if ev.shot_position:
                    sp = ev.shot_position
                    ev_out["shot"] = {
                        "distance_m": round(sp.distance_m, 1),
                        "zone": sp.zone,
                        "side": sp.side,
                    }
                if ev.goal_placement:
                    gp = ev.goal_placement
                    ev_out["placement"] = {
                        "height": gp.height,
                        "side": gp.side,
                    }

                # Score
                if ev.home_goals is not None:
                    ev_out["score"] = f"{ev.home_goals}-{ev.away_goals}"

                f.write(json.dumps(ev_out, ensure_ascii=False) + "\n")

        h = match.home.abbreviation
        a = match.away.abbreviation
        score = f"{match.home_score}-{match.away_score}" if match.home_score is not None else "?"
        log(f"  {mid}: {h} {score} {a} — {len(events)} events")

    log("Processing complete")


# --- Main ---

def main():
    parser = argparse.ArgumentParser(description="football-wire CLI")
    parser.add_argument("--config", help="Path to config file")

    sub = parser.add_subparsers(dest="command")

    # fetch
    fetch_p = sub.add_parser("fetch", help="Pull raw data from APIs")
    fetch_p.add_argument("--backfill", action="store_true",
                         help="Fetch all historical matches")
    fetch_p.add_argument("--force", action="store_true",
                         help="Re-fetch even if data exists")
    fetch_p.add_argument("--match", action="append",
                         help="Specific match ID (repeatable)")

    # process
    proc_p = sub.add_parser("process", help="Process raw/ -> processed/")
    proc_p.add_argument("--flush", action="store_true",
                        help="Clear processed/ before rebuilding")
    proc_p.add_argument("--match", action="append",
                        help="Process specific match ID (repeatable)")

    args = parser.parse_args()

    if args.config:
        init_config(args.config)
    else:
        init_config()

    if args.command == "fetch":
        cmd_fetch(args)
    elif args.command == "process":
        cmd_process(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
