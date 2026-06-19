# Usage Guide (LM Integration)

## What This Is

football-wire (fbw) is a live football match data pipeline. It bridges
public API data into your conversation context through stdout events.
You receive match events as they happen — goals, shots, fouls, subs,
cards — and can discuss the match with the user who is watching.

## How It Works

```
Daemon (polls API) → raw data files → Feed client (your interface) → stdout → Monitor
```

Each stdout line is one event. Your harness delivers them as notifications.

## Integration (Claude Code)

```bash
# In Monitor:
source /path/to/venv/bin/activate
cd /path/to/football-wire
PYTHONPATH=src python -m fbw.feed --delay 0 <match_id>
```

The feed emits:
1. **Preamble** — context notes about the data source and football rules
2. **Match header** — teams, lineups, venue, referee
3. **Team profiles** — background on both teams
4. **Catchup events** — everything that happened before you connected
5. **Live events** — one per line as they arrive
6. **Stats blocks** — computed every 15 match minutes

## Reading Events

```
    16'  >>GOAL<<                MEX | description [1-0]
     7'  SHOT (on target)        MEX | desc | from 27m, outside box, right (76,37) placed low, right (75,3) [0-0]
    71'  SUB                     MEX | desc | ON: Player (18, MF), OFF: Player (7, MF) [1-0]
!!  33'  RED                     QAT | desc [2-0]
```

### Format: `[!!] minute  MARKER  TEAM | description [score]`

- **`!!`** prefix: event arrived out of order (late from API). It happened earlier than the previous event.
- **Minute**: the minute being played (45' = clock shows 44:xx). See preamble.
- **Marker**: event type. `>>GOAL<<`, `SHOT`, `SHOT (on target)`, `SHOT (off target)`, `SUB`, `YELLOW`, `RED`, `FOUL`, `CORNER`, `OFFSIDE`, `** VAR`, `SAVE`, `PAUSE`, `RESUME`, `--- PERIOD`, `--- PERIOD END`.
- **Team tag**: three-letter code from roster. More reliable than the description text.
- **Score**: `[home-away]` at the time of the event. May be stale (see preamble).

### Shot Confidence

Based on data completeness, not editorial judgment:
- `SHOT (on target)` — has position AND goal placement coordinates
- `SHOT (off target)` — has position but no goal placement
- `SHOT` (plain) — no coordinate data. Could be blocked, shanked, or barely an attempt.

### Shot Enrichment

When coordinates are available:
```
from 27m, outside box, right (76,37) placed low, right (75,3)
      │         │        │    │         │    │      │    │
      │         │        │    │         │    │      │    └─ raw gate coords
      │         │        │    │         │    │      └─ gate side
      │         │        │    │         │    └─ gate height
      │         │        │    │         └─ "placed" = goal gate data
      │         │        │    └─ raw pitch coords
      │         │        └─ attacker's left/right perspective
      │         └─ zone (6-yard/inside box/edge/outside/long range)
      └─ distance from goal centre in metres
```

### Substitutions

```
ON: Player (18, MF), OFF: Player (7, MF)
```

Resolved from roster tracking, not API descriptions. The API sometimes
inverts ON/OFF in the description text — trust the tags, not the text.

## What You Don't See

The data has gaps. The user watching the match sees things the API doesn't capture:
- How a shot was taken (header, volley, bicycle kick)
- Near-misses that weren't registered as shots
- Defensive blocks, last-ditch tackles
- Atmosphere, crowd reactions, VAR controversy details
- Whether a foul was genuinely a foul or just contact sport

**Ask the user.** "What happened?" is the most valuable question when an event
seems incomplete. The data tells you what, the user tells you how.

## What To Distrust

Read the preamble at the start of each session. Key issues:
- Event ordering: `!!` marks late arrivals but can't fix them
- Substitution descriptions: ON/OFF tags are reliable, description text is not
- Team names in descriptions: occasionally wrong. Trust the team tag prefix.
- Scores: may be inconsistent across events at the same minute
- Foul counts: reflect referee interpretation as much as player behaviour

## Stats Blocks

Emitted every 15 match minutes (configurable):

```
--- Stats 31' ---
                  MEX    KOR
         Shots      3      1
         Goals      1      0
         Fouls      2      3
---
```

Computed from the event stream. Only non-zero rows shown. These are
registered events only — true attacking pressure is always higher
than shot count (blocked efforts aren't logged as shots).
