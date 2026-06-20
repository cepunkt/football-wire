"""
ESPN stats integration for football-wire.

Opt-in module. Polls ESPN's unauthenticated scoreboard endpoint
for live match statistics (possession, shots on target, key passes).

Not published — personal enrichment layer. Enable via config:
    [sources]
    espn = true

The ESPN data fills gaps the FIFA API doesn't cover:
- Possession percentage (the big one)
- Shots on target (more accurate than our event-derived count)
- Shot assists / key passes
- Total shots (includes blocked, not just operator-logged)
"""

import csv
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from .config import get_config, Config


# --- ESPN API ---

_ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard"
_session: requests.Session | None = None


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
        })
    return _session


def fetch_espn_scoreboard(date: str | None = None) -> dict:
    """Fetch ESPN scoreboard. Optional date filter (YYYYMMDD)."""
    params = {}
    if date:
        params["dates"] = date
    resp = _get_session().get(_ESPN_BASE, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


def find_espn_match(home_tla: str, away_tla: str,
                    date: str | None = None) -> dict | None:
    """Find a specific match in ESPN data by team abbreviations.

    Returns the competition dict with team stats, or None.
    """
    try:
        data = fetch_espn_scoreboard(date)
    except requests.RequestException:
        return None

    for event in data.get("events", []):
        comp = event.get("competitions", [{}])[0]
        competitors = comp.get("competitors", [])
        if len(competitors) < 2:
            continue

        tlas = set()
        for t in competitors:
            tla = t.get("team", {}).get("abbreviation", "")
            tlas.add(tla)

        if home_tla in tlas and away_tla in tlas:
            return {
                "event": event,
                "competitors": competitors,
                "status": event.get("status", {}),
            }

    return None


def extract_stats(espn_match: dict) -> dict[str, dict[str, str]]:
    """Extract per-team stats from ESPN match data.

    Returns: {team_abbr: {stat_name: value, ...}, ...}
    """
    result = {}
    for t in espn_match.get("competitors", []):
        tla = t.get("team", {}).get("abbreviation", "")
        stats = {}
        for s in t.get("statistics", []):
            stats[s["name"]] = s["displayValue"]
        if tla:
            result[tla] = stats
    return result


# --- Stats CSV tracking ---

CSV_FIELDS = [
    "timestamp", "match_minute", "clock",
    "home_team", "away_team",
    "home_possession", "away_possession",
    "home_shots", "away_shots",
    "home_on_target", "away_on_target",
    "home_corners", "away_corners",
    "home_fouls", "away_fouls",
    "home_key_passes", "away_key_passes",
    "home_goals", "away_goals",
]


def poll_and_record(match_id: str, home_tla: str, away_tla: str,
                    config: Config | None = None,
                    date: str | None = None) -> dict | None:
    """Poll ESPN for current stats and append to CSV.

    Returns the extracted stats dict, or None on failure.
    """
    if config is None:
        config = get_config()

    espn_match = find_espn_match(home_tla, away_tla, date)
    if not espn_match:
        return None

    stats = extract_stats(espn_match)
    if not stats:
        return None

    # Extract clock/status
    status = espn_match.get("status", {})
    clock = status.get("displayClock", "?")
    status_desc = status.get("type", {}).get("description", "?")

    home_stats = stats.get(home_tla, {})
    away_stats = stats.get(away_tla, {})

    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "match_minute": clock,
        "clock": status_desc,
        "home_team": home_tla,
        "away_team": away_tla,
        "home_possession": home_stats.get("possessionPct", ""),
        "away_possession": away_stats.get("possessionPct", ""),
        "home_shots": home_stats.get("totalShots", ""),
        "away_shots": away_stats.get("totalShots", ""),
        "home_on_target": home_stats.get("shotsOnTarget", ""),
        "away_on_target": away_stats.get("shotsOnTarget", ""),
        "home_corners": home_stats.get("wonCorners", ""),
        "away_corners": away_stats.get("wonCorners", ""),
        "home_fouls": home_stats.get("foulsCommitted", ""),
        "away_fouls": away_stats.get("foulsCommitted", ""),
        "home_key_passes": home_stats.get("shotAssists", ""),
        "away_key_passes": away_stats.get("shotAssists", ""),
        "home_goals": home_stats.get("totalGoals", ""),
        "away_goals": away_stats.get("totalGoals", ""),
    }

    # Write to CSV
    csv_dir = config.paths.data_dir / "aggregate" / "espn"
    csv_dir.mkdir(parents=True, exist_ok=True)
    csv_path = csv_dir / f"{match_id}.csv"

    file_exists = csv_path.exists()
    with open(csv_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)

    return row


def format_espn_stats(row: dict, minute: str = "") -> str:
    """Format ESPN stats as a stats block for the feed."""
    h = row.get("home_team", "?")
    a = row.get("away_team", "?")

    lines = [f"--- Stats {minute} (ESPN) ---"]
    lines.append(f"{'':>14s}  {h:>5s}  {a:>5s}")

    stat_rows = [
        ("Possession", "home_possession", "away_possession", "%"),
        ("Shots", "home_shots", "away_shots", ""),
        ("On target", "home_on_target", "away_on_target", ""),
        ("Key passes", "home_key_passes", "away_key_passes", ""),
        ("Corners", "home_corners", "away_corners", ""),
        ("Fouls", "home_fouls", "away_fouls", ""),
        ("Goals", "home_goals", "away_goals", ""),
    ]

    for label, h_key, a_key, suffix in stat_rows:
        hv = row.get(h_key, "")
        av = row.get(a_key, "")
        if hv or av:
            h_display = f"{hv}{suffix}" if hv else "-"
            a_display = f"{av}{suffix}" if av else "-"
            lines.append(f"{label:>14s}  {h_display:>5s}  {a_display:>5s}")

    lines.append("---")
    return "\n".join(lines)


def get_latest_stats(match_id: str, config: Config | None = None) -> dict | None:
    """Read the latest ESPN stats row from the CSV."""
    if config is None:
        config = get_config()

    csv_path = config.paths.data_dir / "aggregate" / "espn" / f"{match_id}.csv"
    if not csv_path.exists():
        return None

    last_row = None
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            last_row = row

    return last_row
