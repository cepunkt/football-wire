# Usage Guide (Human)

## Quick Start

```bash
# Activate Python environment with requests + watchdog
source /path/to/venv/bin/activate

# From the football-wire repo directory:
cd /path/to/football-wire
export PYTHONPATH=src
```

## Fetch Data

```bash
# Pull today's matches
python -m fbw fetch

# Pull all historical matches
python -m fbw fetch --backfill

# Pull specific match
python -m fbw fetch --match 400021454
```

## Run the Daemon (Live Matches)

```bash
# Auto-track today's matches, poll every 10 seconds
python -m fbw.daemon

# Track a specific match
python -m fbw.daemon --match 400021454

# Custom poll interval
python -m fbw.daemon --interval 15
```

The daemon writes raw API data to `data/raw/api-fifa/`. It runs on the host and stays alive until all tracked matches finish or you Ctrl-C.

## Watch a Match (Terminal)

```bash
# Live event tail
python -m fbw.watch watch 400021454

# With anti-spoiler delay (seconds)
python -m fbw.feed --delay 90 400021454
```

## Query Data

```bash
# Rolling schedule (last 6 + next 6)
python -m fbw.watch

# Match detail with key events
python -m fbw.watch match 400021454

# Top scorers
python -m fbw.watch scorers
```

## Process Raw Data

```bash
# Process all raw matches
python -m fbw process

# Re-process from scratch
python -m fbw process --flush

# Process specific match
python -m fbw process --match 400021454
```

## Configuration

Edit `fbw.config.toml`:

```toml
[paths]
data_dir = "data"

[source.fifa]
poll_interval = 10

[display]
delay = 90          # anti-spoiler seconds
stats_interval = 15 # minutes between stats blocks
preamble = true
```

Override with environment variables:
- `FBW_DATA_DIR` — data directory
- `FBW_POLL_INTERVAL` — poll interval
- `FBW_DELAY` — anti-spoiler delay
- `FBW_CONFIG` — config file path

## Data Directories

```
data/
  static/     ← git-tracked (team profiles, kits, venues, preamble)
  raw/        ← gitignored (untouched API responses)
  processed/  ← gitignored (cleaned, validated data)
```

Raw and processed data are generated. Delete and re-fetch/re-process at any time.
