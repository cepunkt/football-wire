"""
Data ingestion for football-wire.

Parsers for manual data input — clipboard-pasted stats, observations,
and other human-sourced data that feeds into the aggregate layer.
"""

import json
import re
from pathlib import Path
from datetime import datetime, timezone

from .config import get_config


def parse_fifa_stats(text: str) -> dict:
    """Parse copy-pasted FIFA match statistics page.

    Expected format (consistent across all matches):
        Team A
        Live Statistics
        Team B

        Attacking
        Possession
        51%
        43%
        6% in contest
        ...

    Returns structured dict with home/away stats.
    """
    lines = [l.strip() for l in text.strip().split("\n") if l.strip()]

    if len(lines) < 5:
        raise ValueError("Input too short — expected FIFA stats page paste")

    # First three meaningful lines: home team, "Live Statistics", away team
    home_team = lines[0]
    away_team = lines[2] if lines[1] == "Live Statistics" else lines[1]

    result = {
        "home": home_team,
        "away": away_team,
        "parsed_at": datetime.now(timezone.utc).isoformat(),
        "stats": {},
    }

    # Parse stat sections
    # Pattern: section headers followed by stat_name, home_val, away_val
    # Some stats have a third "in contest" line

    i = 3  # skip header lines
    current_section = ""

    # Known stat labels and their expected value count
    stat_labels = {
        "Possession": "pct_contest",      # 3 values: home%, away%, contest%
        "Total": "two_int",
        "Conceded": "two_int",
        "Inside the Penalty Area": "two_int",
        "Outside the Penalty Area": "two_int",
        "Assists": "two_int",
        "On Target": "two_int",
        "Off Target": "two_int",
        "Yellow Cards": "two_int",
        "Red Cards": "two_int",
        "Fouls Against": "two_int",
        "Offsides": "two_int",
        "Passes": "two_int",
        "Passes Completed": "two_int",
        "Crosses": "two_int",
        "Crosses Completed": "two_int",
        "Corners": "two_int",
        "Free Kicks": "two_int",
        "Penalties Scored": "two_int",
        "Own Goals": "two_int",
        "Forced Turnovers": "two_int",
    }

    section_headers = {
        "Attacking", "Goal", "Attempts at Goal", "Discipline",
        "Distribution", "Set Plays", "Defending",
    }

    while i < len(lines):
        line = lines[i]

        # Section header
        if line in section_headers:
            current_section = line
            i += 1
            continue

        # Skip non-stat lines
        if line in ("Live Statistics", "Head to Head", "Recent meetings"):
            # Skip head-to-head section entirely
            if line == "Head to Head":
                break
            i += 1
            continue

        # Check if this is a known stat label
        if line in stat_labels:
            stat_type = stat_labels[line]
            stat_name = line
            key = f"{current_section}.{stat_name}" if current_section else stat_name

            if stat_type == "pct_contest" and i + 3 < len(lines):
                home_val = lines[i + 1].replace("%", "").strip()
                away_val = lines[i + 2].replace("%", "").strip()
                contest_line = lines[i + 3] if i + 3 < len(lines) else ""
                contest_val = ""
                if "in contest" in contest_line:
                    contest_val = contest_line.replace("% in contest", "").strip()
                    i += 4
                else:
                    i += 3

                result["stats"][key] = {
                    "home": _parse_num(home_val),
                    "away": _parse_num(away_val),
                }
                if contest_val:
                    result["stats"][key]["contest"] = _parse_num(contest_val)

            elif stat_type == "two_int" and i + 2 < len(lines):
                home_val = lines[i + 1].strip()
                away_val = lines[i + 2].strip()
                result["stats"][key] = {
                    "home": _parse_num(home_val),
                    "away": _parse_num(away_val),
                }
                i += 3
            else:
                i += 1
        else:
            i += 1

    return result


def _parse_num(val: str) -> int | float | None:
    """Parse a numeric string, handling percentages."""
    val = val.strip().replace("%", "").replace(",", "")
    if not val or val == "-":
        return None
    try:
        if "." in val:
            return float(val)
        return int(val)
    except ValueError:
        return None


def store_match_stats(match_id: str, stats: dict,
                      config=None) -> Path:
    """Store parsed match stats to aggregate layer."""
    if config is None:
        config = get_config()

    agg_dir = config.paths.data_dir / "aggregate" / "stats"
    agg_dir.mkdir(parents=True, exist_ok=True)

    path = agg_dir / f"{match_id}.json"
    with open(path, "w") as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)

    return path


def ingest_stats_clipboard(match_id: str, text: str, config=None) -> Path:
    """Parse and store clipboard-pasted FIFA stats for a match.

    One-stop function: parse the paste, store to aggregate/.
    """
    stats = parse_fifa_stats(text)
    stats["match_id"] = match_id
    return store_match_stats(match_id, stats, config)
