#!/usr/bin/env python3
"""
LM feed client for football-wire.

Cycle-based match monitor optimised for LLM consumption via stdout.
Runs a fixed-interval cycle (default 10s) that collects events,
applies enrichments, and emits grouped notifications.

Usage:
    python -m fbw.feed                     # auto-detect live match
    python -m fbw.feed <match_id>          # watch specific match
    python -m fbw.feed --delay 30 <id>     # enrichment + TV sync delay
    python -m fbw.feed --cycle 10 <id>     # cycle interval seconds
    python -m fbw.feed --snapshot          # current state, exit
"""

import argparse
import json
import sys
import time
from functools import partial
from pathlib import Path

from watchdog.observers import Observer

from .config import init_config, get_config
from .format import format_preamble
from .process import read_raw_json

# Force unbuffered output
print = partial(print, flush=True)


def read_live(config) -> dict:
    path = config.paths.raw_api_dir / "live.json"
    data = read_raw_json(path)
    return data or {"matches": {}}


def find_live_match(config) -> str | None:
    live = read_live(config)
    for mid, info in live.get("matches", {}).items():
        if info.get("status") == 3:
            return mid
    for mid, info in live.get("matches", {}).items():
        if info.get("status") != 0:
            return mid
    return None


def cmd_snapshot(config, match_id: str | None = None):
    """One-shot: print current state and exit."""
    live = read_live(config)
    matches = live.get("matches", {})

    def _fmt_match(mid, info):
        home = info.get('home', '?')
        away = info.get('away', '?')
        hs = info.get('home_score')
        as_ = info.get('away_score')
        score = f"{hs}-{as_}" if hs is not None else "vs"
        status = info.get('status', '?')
        return f"[{status}] {home} {score} {away} (#{mid})"

    if match_id:
        if match_id in matches:
            print(_fmt_match(match_id, matches[match_id]))
        else:
            print(f"No live data for #{match_id}")
        return

    if not matches:
        print("No tracked matches")
        return
    for mid, info in matches.items():
        print(_fmt_match(mid, info))



# --- Main ---

def cmd_watch_sm(config, match_id: str, delay: int = 0, cycle_interval: int = 10,
                  parallel: str | None = None):
    """Watch a match using the state machine engine.

    parallel: None (normal), "pmin" (minimal parallel), "pfull" (full parallel).
    Parallel modes add a match-label prefix to every output line and
    pmin additionally filters to game-changing events only.
    """
    from .feed_sm import SMFeedEngine, FileChangeFlag as SMFileChangeFlag, format_output, MINIMAL_TYPES
    from .state import MatchStateMachine, TeamState
    from .tournament import load_tournament, load_tournament_data
    from .process import read_raw_json
    from pathlib import Path

    raw_match_path = config.paths.raw_matches_dir / f"{match_id}.json"
    raw_events_path = config.paths.raw_events_dir / f"{match_id}.jsonl"
    raw_enrichments_path = config.paths.raw_enrichments_dir / f"{match_id}.jsonl"

    # Load match data
    raw_match = read_raw_json(raw_match_path)
    if raw_match is None:
        print(f"No data for #{match_id}")
        sys.exit(1)

    # Load tournament rules + data
    rules = load_tournament(config.tournament.rules_path)


    # Extract team info from match data
    home_raw = raw_match.get("HomeTeam") or raw_match.get("Home") or {}
    away_raw = raw_match.get("AwayTeam") or raw_match.get("Away") or {}

    home_id = str(home_raw.get("IdTeam", ""))
    away_id = str(away_raw.get("IdTeam", ""))
    home_abbr = home_raw.get("Abbreviation", "???")
    away_abbr = away_raw.get("Abbreviation", "???")

    # Build on_pitch from starters
    home_starters = set()
    away_starters = set()
    for p in (home_raw.get("Players") or []):
        if p.get("Status") == 1:  # starter
            home_starters.add(str(p.get("IdPlayer", "")))
    for p in (away_raw.get("Players") or []):
        if p.get("Status") == 1:
            away_starters.add(str(p.get("IdPlayer", "")))

    home_state = TeamState(
        team_id=home_id,
        abbreviation=home_abbr,
        on_pitch=home_starters,
    )
    away_state = TeamState(
        team_id=away_id,
        abbreviation=away_abbr,
        on_pitch=away_starters,
    )

    # Determine if knockout from stage name
    stage_name = ""
    sn = raw_match.get("StageName")
    if sn and isinstance(sn, list) and sn:
        stage_name = sn[0].get("Description", "")
    is_knockout = stage_name != "" and stage_name != "First Stage"

    # Create state machine
    sm = MatchStateMachine(
        match_id=match_id,
        home=home_state,
        away=away_state,
        rules=rules,
        is_knockout=is_knockout,
    )

    # Build player name lookup
    from .display import init_player_names
    init_player_names(raw_match)

    # Parallel mode config
    match_label = f"{home_abbr}-{away_abbr}"
    emit_types = None
    prefix = ""
    if parallel == "pmin":
        emit_types = MINIMAL_TYPES
        prefix = f"{match_label}: "
    elif parallel == "pfull":
        prefix = f"{match_label}: "

    # Header — preamble in all modes (LM needs reading instructions),
    # lineups only in normal mode
    home_score = home_raw.get("Score", 0) or 0
    away_score = away_raw.get("Score", 0) or 0
    lore_dir = config.tournament.lore_path

    header_parts = []
    if config.display.preamble:
        header_parts.append(format_preamble())

    if parallel:
        mode_tag = "pmin" if parallel == "pmin" else "pfull"
        header_parts.append(f"{home_abbr} {home_score} - {away_score} {away_abbr} (#{match_id}) [{mode_tag}]")
    else:
        header_parts.append(f"{home_abbr} {home_score} - {away_score} {away_abbr} (# {match_id})")

        # Lineup (one line per team) — normal mode only
        for side, raw_team in [("home", home_raw), ("away", away_raw)]:
            abbr = raw_team.get("Abbreviation", "???")
            tactics = raw_team.get("Tactics", "?")
            starters = []
            for p in sorted((raw_team.get("Players") or []),
                            key=lambda x: x.get("ShirtNumber", 99)):
                if p.get("Status") == 1:
                    def _get_name(field):
                        v = p.get(field, [])
                        if isinstance(v, list) and v and isinstance(v[0], dict):
                            return v[0].get("Description", "")
                        return str(v) if v else ""
                    name = _get_name("ShortName") or _get_name("PlayerName") or "?"
                    starters.append(name)
            header_parts.append(f"{abbr} ({tactics}): {', '.join(starters)}")

    # Lore pointers — all modes
    header_parts.append("")
    header_parts.append(f"Lore: {lore_dir}/teams/{{{home_abbr},{away_abbr}}}.md")

    header_text = "\n".join(header_parts)

    print(header_text)

    # Feed log
    log_dir = config.paths.data_dir / "feeds"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{match_id}.sm.md"

    with open(log_path, "w") as lf:
        lf.write(header_text + "\n")

    # Wait for events
    if not raw_events_path.exists():
        print(f"Waiting for events on #{match_id}...")
        try:
            while not raw_events_path.exists():
                time.sleep(2)
        except KeyboardInterrupt:
            return

    # Engine
    engine = SMFeedEngine(
        sm=sm,
        events_path=raw_events_path,
        enrichments_path=raw_enrichments_path,
        match_path=raw_match_path,
        match_data=raw_match,
        delay=delay,
        cycle_interval=cycle_interval,
        stats_interval=config.display.stats_interval,
        log_path=log_path,
        emit_types=emit_types,
        prefix=prefix,
    )

    # Catchup
    engine.catchup()
    live_tag = f"-- live ({match_label}, {parallel or 'full'}) --"
    print(live_tag if parallel else "-- live (state machine) --")

    # Watchdog
    watcher = SMFileChangeFlag(
        raw_events_path.name,
        raw_enrichments_path.name,
        raw_match_path.name,
    )
    observer = Observer()
    observer.schedule(watcher, str(config.paths.raw_events_dir), recursive=False)
    observer.schedule(watcher, str(config.paths.raw_enrichments_dir), recursive=False)
    observer.schedule(watcher, str(config.paths.raw_matches_dir), recursive=False)
    observer.start()

    try:
        while True:
            time.sleep(cycle_interval)
            changed = watcher.consume()
            ev_changed = raw_events_path.name in changed
            en_changed = raw_enrichments_path.name in changed
            match_changed = raw_match_path.name in changed
            engine.run_cycle(ev_changed, en_changed, match_changed)

            # Check for full time
            live = read_live(config)
            info = live.get("matches", {}).get(match_id)
            if info and info.get("status") == 0:
                time.sleep(2)
                engine.read_new_events()
                engine.read_new_enrichments()
                engine.flush_all()
                score = sm.score
                ft_line = f"FULL TIME: {home_abbr} {score[0]}-{score[1]} {away_abbr} (#{match_id})"
                engine.emit(ft_line)
                break

    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        engine.flush_all()
        observer.stop()
        observer.join(timeout=3)




def main():
    parser = argparse.ArgumentParser(description="football-wire LM feed")
    parser.add_argument("match_id", nargs="?", help="Match ID to watch")
    parser.add_argument("--config", help="Path to config file")
    parser.add_argument("--delay", type=int, help="Delay seconds (enrichment + TV sync)")
    parser.add_argument("--cycle", type=int, help="Cycle interval seconds (default 10)")
    parser.add_argument("--snapshot", action="store_true", help="One-shot status")
    parallel_group = parser.add_mutually_exclusive_group()
    parallel_group.add_argument("--pmin", action="store_true",
                                help="Parallel minimal: goals, reds, periods only")
    parallel_group.add_argument("--pfull", action="store_true",
                                help="Parallel full: all events, prefixed")

    args = parser.parse_args()
    config = init_config(args.config)

    if args.snapshot:
        cmd_snapshot(config, args.match_id)
        return

    delay = args.delay if args.delay is not None else config.display.delay
    cycle = args.cycle if args.cycle is not None else 10

    match_id = args.match_id
    if not match_id:
        match_id = find_live_match(config)
        if not match_id:
            print("No live match found. Specify a match ID.")
            sys.exit(1)
        print(f"Auto-detected: #{match_id}")

    parallel = "pmin" if args.pmin else ("pfull" if args.pfull else None)
    cmd_watch_sm(config, match_id, delay=delay, cycle_interval=cycle, parallel=parallel)


if __name__ == "__main__":
    main()
