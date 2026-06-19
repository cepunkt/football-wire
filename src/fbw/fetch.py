"""
Data fetcher for football-wire.

Pulls from configured data sources and writes to raw/.
Raw data is stored unchanged — exactly what the API returned.

The fetcher is dumb by design. It doesn't validate, transform, or
enrich. It just stores. The processing layer handles all of that.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import requests

from .config import get_config, Config


# --- HTTP session ---

_session: requests.Session | None = None

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36")


def _get_session() -> requests.Session:
    """Lazy-init HTTP session with browser UA (FIFA blocks bot UAs)."""
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({
            "User-Agent": UA,
            "Accept": "application/json",
        })
    return _session


# --- File I/O ---

def _ensure_dirs(config: Config) -> None:
    """Create raw data directories if they don't exist."""
    for d in [config.paths.raw_api_dir,
              config.paths.raw_matches_dir,
              config.paths.raw_events_dir]:
        d.mkdir(parents=True, exist_ok=True)


def _write_json(filepath: Path, data: dict) -> None:
    """Atomic JSON write via temp file + rename."""
    tmp = filepath.with_suffix(f".{os.getpid()}.tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    tmp.rename(filepath)


def _append_jsonl(filepath: Path, record: dict) -> None:
    """Append a single JSON line to a JSONL file."""
    with open(filepath, "a") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _read_json(filepath: Path) -> dict | None:
    """Read JSON file, return None if missing."""
    if filepath.exists():
        try:
            with open(filepath) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None
    return None


# --- Localization helper ---

def _get_name(name_list) -> str:
    if not name_list:
        return "?"
    for entry in name_list:
        if isinstance(entry, dict) and entry.get("Locale") in ("en-GB", "en"):
            return entry.get("Description", "?")
    if isinstance(name_list[0], dict):
        return name_list[0].get("Description", "?")
    return str(name_list[0])


# --- API calls ---

def api_get(path: str, config: Config | None = None) -> dict:
    """Fetch from the FIFA API. Raises on HTTP errors."""
    if config is None:
        config = get_config()
    url = f"{config.source.base_url}/{path.lstrip('/')}"
    resp = _get_session().get(url, timeout=15)
    resp.raise_for_status()
    return resp.json()


def fetch_schedule(config: Config | None = None) -> list[dict]:
    """Fetch full tournament schedule."""
    if config is None:
        config = get_config()
    data = api_get(f"calendar/matches?idCompetition={config.source.competition_id}"
                   f"&idSeason={config.source.season_id}&count=200", config)
    if isinstance(data, dict) and "Results" in data:
        return data["Results"]
    return data if isinstance(data, list) else []


def fetch_match(match_id: str, config: Config | None = None) -> dict:
    """Fetch live match state."""
    if config is None:
        config = get_config()
    return api_get(
        f"live/football/{config.source.competition_id}/"
        f"{config.source.season_id}/{config.source.stage_id}/{match_id}",
        config,
    )


def fetch_timeline(match_id: str, config: Config | None = None) -> dict:
    """Fetch match timeline (events)."""
    if config is None:
        config = get_config()
    return api_get(
        f"timelines/{config.source.competition_id}/"
        f"{config.source.season_id}/{config.source.stage_id}/{match_id}",
        config,
    )


# --- Raw data storage ---

def store_schedule(schedule: list[dict], config: Config | None = None) -> Path:
    """Write schedule to raw/."""
    if config is None:
        config = get_config()
    _ensure_dirs(config)
    path = config.paths.raw_api_dir / "schedule.json"
    _write_json(path, schedule)
    return path


def store_match(match_id: str, data: dict, config: Config | None = None) -> Path:
    """Write match state to raw/."""
    if config is None:
        config = get_config()
    _ensure_dirs(config)
    path = config.paths.raw_matches_dir / f"{match_id}.json"
    _write_json(path, data)
    return path


def store_events(match_id: str, timeline_data: dict,
                 config: Config | None = None) -> tuple[Path, int]:
    """Append new timeline events to raw JSONL. Returns (path, new_count).

    Deduplicates by EventId against what's already on disk.
    This is the ONLY dedup in the fetch layer — just preventing
    duplicate raw storage. Content dedup happens in processing.
    """
    if config is None:
        config = get_config()
    _ensure_dirs(config)

    filepath = config.paths.raw_events_dir / f"{match_id}.jsonl"

    # Load existing event IDs
    seen: set[str] = set()
    if filepath.exists():
        with open(filepath) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        ev = json.loads(line)
                        eid = ev.get("EventId", "")
                        if eid:
                            seen.add(eid)
                    except json.JSONDecodeError:
                        pass

    # Append new events
    new_count = 0
    for ev in timeline_data.get("Event", []):
        eid = ev.get("EventId", "")
        if eid and eid not in seen:
            seen.add(eid)
            ev["_logged_at"] = datetime.now(timezone.utc).isoformat()
            _append_jsonl(filepath, ev)
            new_count += 1

    return filepath, new_count


# --- High-level operations ---

def pull_match(match_id: str, config: Config | None = None,
               log_fn=None) -> dict | None:
    """Fetch and store a single match (state + events).

    Returns the match data dict, or None on error.
    """
    if config is None:
        config = get_config()
    if log_fn is None:
        log_fn = lambda msg: None

    # Fetch match state
    try:
        match_data = fetch_match(match_id, config)
    except requests.RequestException as e:
        log_fn(f"API error (match) #{match_id}: {e}")
        return None

    store_match(match_id, match_data, config)

    # Fetch and store timeline
    try:
        timeline = fetch_timeline(match_id, config)
        _, new_count = store_events(match_id, timeline, config)
        if new_count > 0:
            log_fn(f"#{match_id}: {new_count} new events")
    except requests.RequestException as e:
        log_fn(f"API error (timeline) #{match_id}: {e}")

    return match_data


def pull_schedule(config: Config | None = None, log_fn=None) -> list[dict]:
    """Fetch and store the tournament schedule."""
    if config is None:
        config = get_config()
    if log_fn is None:
        log_fn = lambda msg: None

    try:
        schedule = fetch_schedule(config)
        store_schedule(schedule, config)
        log_fn(f"Schedule: {len(schedule)} matches")
        return schedule
    except requests.RequestException as e:
        log_fn(f"Schedule fetch failed: {e}")
        # Try cached
        cached = _read_json(config.paths.raw_api_dir / "schedule.json")
        if cached:
            log_fn(f"Using cached schedule")
            return cached if isinstance(cached, list) else []
        return []


def backfill(config: Config | None = None, force: bool = False,
             log_fn=None) -> int:
    """Fetch detailed data for all finished matches.

    Returns number of matches fetched.
    """
    if config is None:
        config = get_config()
    if log_fn is None:
        log_fn = lambda msg: None

    schedule = pull_schedule(config, log_fn)
    finished = [m for m in schedule if m.get("MatchStatus") == 0]
    log_fn(f"Backfill: {len(finished)} finished matches (force={force})")

    count = 0
    for i, m in enumerate(finished, 1):
        mid = m.get("IdMatch", "?")

        if not force:
            match_file = config.paths.raw_matches_dir / f"{mid}.json"
            events_file = config.paths.raw_events_dir / f"{mid}.jsonl"
            if (match_file.exists() and match_file.stat().st_size > 10000
                    and events_file.exists()):
                continue

        result = pull_match(mid, config, log_fn)
        if result:
            count += 1
            log_fn(f"  [{i}/{len(finished)}] #{mid} OK")
        else:
            log_fn(f"  [{i}/{len(finished)}] #{mid} FAILED")

        import time
        time.sleep(0.3)  # rate limit

    log_fn(f"Backfill complete: {count} matches fetched")
    return count
