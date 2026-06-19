# football-wire

Live football match data pipeline. Bridges public API data into structured event feeds for LLM companions, terminal clients, and multi-agent watch parties.

## What It Does

Polls live match data from public APIs, processes unreliable event streams into trustworthy feeds, and delivers them to consumers. The API is the source. The wire is the pipeline. What listens on the other end is up to you.

```
daemon (polls API) → raw/ (untouched) → process → model → format → consumers
                                                                    ├── feed (LM)
                                                                    ├── watch (human)
                                                                    └── query (one-shot)
```

## Quick Start

```bash
# Requires Python 3.11+, requests, watchdog
cd football-wire
export PYTHONPATH=src

# Fetch historical data
python -m fbw fetch --backfill

# Start the daemon for live matches
python -m fbw.daemon

# Watch a match (terminal)
python -m fbw.watch watch <match_id>

# Feed a match to an LLM via Monitor
python -m fbw.feed <match_id>

# Query scores and standings
python -m fbw.watch
python -m fbw.watch scorers
```

## Architecture

```
src/fbw/
  config.py    ← layered TOML config (defaults → file → env → CLI)
  model.py     ← typed data model with trust levels
  fetch.py     ← API client, raw data storage
  process.py   ← validation, dedup, sub resolution, team attribution
  format.py    ← model → display strings, shot enrichment, preamble
  daemon.py    ← continuous polling loop
  feed.py      ← LM stdout client (Monitor integration)
  watch.py     ← human terminal client
  cli.py       ← fetch/process commands
```

### Data Layers

| Layer | Path | Git | Purpose |
|-------|------|-----|---------|
| Static | `data/static/` | tracked | Team profiles, kits, venues, preamble, invariants |
| Raw | `data/raw/` | ignored | Untouched API responses |
| Processed | `data/processed/` | ignored | Cleaned, validated, enriched data |
| Fixtures | `fixtures/` | tracked | Saved API failures for regression testing |

### Data Model

Events are processed through typed Python dataclasses with trust metadata:

- **Trust levels:** `trusted` (roster data), `inferred` (computed), `suspect` (contradicts invariant), `unknown`
- **Sub direction:** resolved from on-pitch tracking, not unreliable API descriptions
- **Team attribution:** roster-based player→team lookup, corrects API misattribution
- **Shot enrichment:** distance from goal (metres), pitch zone, attacker-relative side, confidence level
- **Deduplication:** content-based (catches same event resent with new IDs)
- **Ordering:** sorted by match minute, late arrivals marked with `!!`

### Data Quality

The FIFA public API is the only free live data source for the World Cup. Known issues:

- **Event ordering:** dual pipeline delivers events out of sequence
- **Team attribution:** descriptions sometimes name the wrong team
- **Substitution direction:** ON/OFF markers inconsistent across matches (8/8 inverted in MEX-KOR, correct in CAN-QAT)
- **Score fields:** stale or inconsistent from dual pipeline
- **Duplicate events:** same event resent with different IDs

The processing layer handles all of these. Raw data is preserved unchanged. Every known failure mode has a test fixture and regression test.

## Configuration

`fbw.config.toml`:

```toml
[paths]
data_dir = "data"

[source.fifa]
base_url = "https://api.fifa.com/api/v3"
poll_interval = 10
trust = "low"

[display]
delay = 90          # anti-spoiler seconds (0 = disabled)
stats_interval = 15 # match minutes between stats blocks
preamble = true     # emit context notes before match header
```

Environment overrides: `FBW_DATA_DIR`, `FBW_POLL_INTERVAL`, `FBW_DELAY`, `FBW_CONFIG`.

## Documentation

- [Usage Guide (Human)](docs/usage-human.md) — running the daemon, watching matches, querying data
- [Usage Guide (LM)](docs/usage-lm.md) — Monitor integration, reading events, what to trust and distrust

## Testing

```bash
PYTHONPATH=src python -m pytest tests/ -v
```

19 tests covering every known API failure mode, built from real match data fixtures.

## Static Data

- `data/static/teams/` — 48 team profiles with WC history, style, key players
- `data/static/kits/` — 48 team jersey schematics
- `data/static/venues.csv` — 16 stadiums with capacity, altitude, coordinates
- `data/static/preamble/` — context notes for LM companions (3 files, different lifetimes)
- `data/static/invariants.md` — football rules that are always true

## Origin

Built during the FIFA World Cup 2026 at the Clockwork Taming Workshop. First prototype (wc26 companion) built live during England 4-2 Croatia on 2026-06-17. Rebuilt as football-wire on 2026-06-19 during Mexico 1-0 Korea, USA 2-0 Australia, and Scotland vs Morocco — three matches, eight commits, one session.

## License

MIT
