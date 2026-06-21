# Usage Guide (Human)

## Quick Start

```bash
# From the football-wire repo directory:
source /opt/ws-venvs/ml/bin/activate   # or your Python venv
export PYTHONPATH=src
```

## Schedule & Live Feed

```bash
# What's on? Recent results + upcoming matches
fbw watch

# Start live feed for a match (any of these work):
fbw watch #400021475          # copy-paste from schedule
fbw watch TUN-JPN             # team codes
fbw watch TUN                 # latest/next match for team
fbw watch TUN --delay 30      # with anti-spoiler delay
```

## Query Tool

```bash
# All groups at a glance
fbw query groups

# Group detail with standings + results
fbw query group F
fbw query group AUT           # find by team code

# Full 26-man squad with positions, ages, clubs
fbw query squad JPN
fbw query squad AUT

# Match detail (cards, subs, stats)
fbw query match GER-CIV
fbw query match ECU           # latest match for team

# Top scorers
fbw query scorers

# Find a player across all 48 squads
fbw query player Musiala
fbw query player Room
fbw query player Messi
```

## Daemon (Host)

The daemon polls APIs and writes raw data. Run on the host:

```bash
# Auto-track matches (1h ago to 6h ahead)
python -m fbw.daemon

# Track a specific match
python -m fbw.daemon --match 400021475

# Custom poll interval
python -m fbw.daemon --interval 15
```

Daemon handles midnight UTC boundary. Stays alive until all tracked
matches finish, then checks for upcoming ones.

## Fetch & Process

```bash
# Pull today's matches
python -m fbw fetch

# Pull all historical matches
python -m fbw fetch --backfill

# Process raw → validated data
python -m fbw process
python -m fbw process --flush    # rebuild from scratch
```

## Configuration

`fbw.config.toml`:
```toml
[source.fifa]
poll_interval = 10

[display]
delay = 90          # anti-spoiler seconds
stats_interval = 15 # match minutes between stats blocks
preamble = true
```

Local overrides in `fbw.config.local.toml` (gitignored):
```toml
[sources]
espn = true
espn_interval = 60

[display]
delay = 30
```

## Data Layout

```
data/
  static/                          ← git-tracked
    preamble/                      ← feed context notes
    tournaments/
      wc2026.toml                  ← tournament rules
      wc2026-data/                 ← canonical data (worldcup.json, CC0)
      wc2026-enrichment/           ← structured facts (coaches, heights)
      wc2026-lore/                 ← narrative context (teams, groups, matches)
        teams/GER.md
        groups/E.md
        matches/GER-CIV-postgame.md
  raw/                             ← gitignored (API responses)
  processed/                       ← gitignored (validated data)
  feeds/                           ← gitignored (feed session logs)
  aggregate/                       ← gitignored (ESPN, multi-source)
```
