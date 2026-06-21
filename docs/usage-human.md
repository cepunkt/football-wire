# Usage Guide (Human)

## Quick Start

```bash
# From the football-wire repo directory — no venv setup needed,
# shell wrappers handle it automatically:

./fbw-daemon                  # start the data daemon (run first!)
./fbw-watch                   # what's on
./fbw-query groups            # tournament overview
```

## The Daemon

The daemon is the data engine. Start it first, leave it running.
It polls the FIFA API and ESPN, writes raw data to disk. Everything
else reads from those files.

```bash
# Start (runs forever, picks up matches automatically):
./fbw-daemon

# Or in background:
./fbw-daemon &

# Or with nohup (survives terminal close):
nohup ./fbw-daemon > daemon.log 2>&1 &

# Track a specific match:
./fbw-daemon --match 400021475

# Custom poll interval:
./fbw-daemon --interval 15
```

The daemon:
- Runs forever until Ctrl-C (SIGINT/SIGTERM)
- Auto-discovers matches within 6 hours of kickoff
- Handles midnight UTC boundary (American timezone matches)
- Polls ESPN for live stats (opt-in via local config)
- Keeps polling ESPN 30 minutes after full time for final stats
- Idles between matches (60s+ sleep, low resource usage)
- Writes to `data/raw/` — everything downstream reads from here

**Start the daemon before your first match. Leave it running
through the tournament.**

## Schedule & Live Feed

```bash
# What's on? Recent results + upcoming matches:
./fbw-watch

# Start live feed for a match (any of these work):
./fbw-watch '#400021475'       # copy-paste from schedule
./fbw-watch TUN-JPN            # team codes
./fbw-watch TUN                # latest/next match for team
./fbw-watch TUN --delay 30     # with anti-spoiler delay (seconds)
```

The live feed shows match events as they happen — goals, shots,
fouls, cards, subs. With `--delay 30`, events are buffered for
TV sync so the ticker doesn't spoil what you're watching.

## Query Tool

```bash
# All groups at a glance:
./fbw-query groups

# Group detail with standings + results:
./fbw-query group F
./fbw-query group AUT          # find by team code

# Full 26-man squad with positions, ages, clubs:
./fbw-query squad JPN
./fbw-query squad AUT

# Match detail (goals, cards, subs, stats):
./fbw-query match GER-CIV
./fbw-query match ECU          # latest match for team

# Top scorers:
./fbw-query scorers

# Find a player across all 48 squads:
./fbw-query player Musiala
./fbw-query player Room
./fbw-query player Messi
```

## Fetch & Backfill

For initial setup or recovering missed data:

```bash
# Fetch matches in the current window:
./fbw fetch

# Backfill all finished matches (initial setup):
./fbw fetch --backfill

# Force re-fetch a specific match:
./fbw fetch --match 400021475 --force
```

Backfill automatically detects incomplete data (e.g. daemon died
mid-match) and re-fetches those matches.

## Configuration

`fbw.config.toml` (committed, shared):
```toml
[source.fifa]
poll_interval = 10

[display]
delay = 90          # anti-spoiler seconds
stats_interval = 15 # match minutes between stats blocks
preamble = true
```

`fbw.config.local.toml` (gitignored, personal overrides):
```toml
[sources]
espn = true         # enable ESPN stats (possession, key passes)
espn_interval = 60  # ESPN poll interval in seconds

[display]
delay = 30          # your TV delay preference
```

## Data Layout

```
data/
  static/                          ← git-tracked
    preamble/                      ← feed context notes
    strings/                       ← localised display strings (en, de-at)
    tournaments/
      wc2026.toml                  ← tournament rules
      wc2026-data/                 ← canonical data (worldcup.json, CC0)
      wc2026-enrichment/           ← structured facts (coaches)
      wc2026-lore/                 ← narrative context
        teams/GER.md               ← team narratives
        groups/E.md                ← group analysis
        matches/GER-CIV-postgame.md ← match reports
  raw/                             ← gitignored (API responses)
  processed/                       ← gitignored (validated data)
  feeds/                           ← gitignored (feed session logs)
  aggregate/                       ← gitignored (ESPN, multi-source)
```

## Shell Wrappers

All `fbw-*` scripts auto-detect the Python environment:
- Container (LM sessions): activates `/opt/ws-venvs/ml`
- Host (human): uses system Python directly

No manual venv activation or PYTHONPATH needed.

```
./fbw-daemon    # data poller (run first, leave running)
./fbw-watch     # schedule + live feed
./fbw-query     # lookups (groups, squads, matches, players)
./fbw           # CLI entry (fetch, process, query subcommands)
```
