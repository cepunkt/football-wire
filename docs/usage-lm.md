# Usage Guide (LM Integration)

## What This Is

football-wire (fbw) is a live football match data pipeline. It bridges
public API data into your conversation through Monitor events. You
receive match events as they happen and discuss the match with the
user who is watching on TV.

## Starting a Feed

```bash
# In Monitor:
cd /path/to/football-wire
source /path/to/venv/bin/activate
PYTHONPATH=src python -m fbw.feed --delay 30 --cycle 10 <match_id>
```

The `--delay` syncs with TV broadcast (~30s). The `--cycle` controls
how often events are batched (10s default).

## What You Receive

### 1. Preamble (data quality notes)
Inline context about how to read the data — coordinate system,
trust levels, known API issues. Read and internalise.

### 2. Match header
Score, venue, referee, coaches, lineups. One line per squad.

### 3. Context pointers
```
Context (read for background):
  Team lore:    data/static/tournaments/wc2026-lore/teams/{ECU,CUW}.md
  Pre-match:    data/static/tournaments/wc2026-lore/matches/ECU-CUW-pregame.md
  Group:        data/static/tournaments/wc2026-lore/groups/E.md
  Match so far: data/feeds/400021465.md
```

**Read these files** for team narratives, group standings, pre-match
context, and match history. The Monitor stream stays lean — deep
context is in files, not stdout.

### 4. Live events
```
 1H  30'  >>GOAL<<            CIV | Franck KESSIE scores!! [1-1]
 2H  68'  SHOT (on target)    GER | Deniz UNDAV | from 6m, 6-yard box, central (5,47) placed mid-height, centre (51,42) [1-1]
 2H  75'  SUB                 CIV | ON: Simon ADINGRA, OFF: Amad DIALLO [1-1]
     24'  FOUL                ECU | Alan FRANCO commits a foul. | at 35m, outside box, central (38,46) [0-0]
```

### Event format: `phase  minute  MARKER  TEAM | details [score]`

- **Phase prefix**: `1H`, `2H`, `ET1`, `ET2`, `PEN`
- **Minute**: `45'` = in the 45th minute (clock shows 44:xx).
  Added time: `45'+3'` = 3rd minute of added time.
- **Marker**: `>>GOAL<<`, `SHOT`, `SHOT (on target)`, `SHOT (off target)`,
  `SUB`, `YELLOW`, `RED`, `FOUL`, `CORNER`, `OFFSIDE`, `SAVE`,
  `PAUSE`, `RESUME`, `--- PERIOD`, `--- PERIOD END`, `** VAR`
- **Team tag**: three-letter code from roster. Trust this over description.
- **Score**: `[home-away]` — may be stale on individual events.
- **`!!` prefix**: late arrival — happened earlier than previous event.
- **`[suspect]`**: data couldn't be fully verified.

### Shot enrichment
```
from 27m, outside box, right (76,37) placed low, right (75,3)
```
Distance from goal, pitch zone, attacker's side, raw coordinates,
goal gate placement. Zone uses both depth and width constraints.

### Foul/offside enrichment
```
at 35m, outside box, central (38,46)
```
Position where the foul/offside occurred.

### Post-emit corrections
```
>> ENRICHED: 77'  SHOT  ECU | Valencia | from 11m, inside box, central (90,52)
```
When coordinates arrive after an event was already emitted. Includes
original event context for mapping.

### Stats blocks (ESPN)
```
--- Stats 63' (ESPN) ---
GER    CIV
Possession  60.6%  39.4%
Shots     10      8
On target      2      2
Key passes      8      5
Corners      7      2
Fouls      2      5
Goals      0      1
---
```
Real possession and stats from ESPN. Emitted every 15 match minutes.

## What You Don't See

The event stream is ~20% of the match. The API misses:
- How shots were taken (header, volley, free kick)
- Near-misses not registered as shots
- Build-up play, pressing patterns, tactical shape
- Defensive blocks, last-ditch tackles
- Atmosphere, crowd, VAR controversy details
- Why a goal was disallowed (no VAR event detail)
- Corners are under-logged (API misses many)

**Ask the user.** They are watching on TV. Their eyes are the best
sensor. "What happened?" is always a valid question.

## What To Distrust

- **Scores on events**: may be stale. The match header score is more
  reliable. Cross-check with ESPN stats block.
- **Substitution descriptions**: the API text may invert ON/OFF.
  Trust the `ON:` / `OFF:` tags which are resolved from roster tracking.
  `[suspect]` means resolution failed — ask the user.
- **Team names in descriptions**: occasionally wrong. Trust the team
  tag prefix (GER, CIV, ECU).
- **Shot counts**: API logs more than official stats count. ESPN stats
  block has the official numbers.
- **Hydration breaks**: logged as PAUSE or DELAY (inconsistent between
  matches). These are scheduled advertising breaks, not medical events.

## Querying Data

You can read files and run commands between events:

```bash
# Group standings
PYTHONPATH=src python -m fbw.query group E

# Team squad
PYTHONPATH=src python -m fbw.query squad JPN

# Match detail
PYTHONPATH=src python -m fbw.query match GER-CIV

# Find a player
PYTHONPATH=src python -m fbw.query player Musiala

# All groups overview
PYTHONPATH=src python -m fbw.query groups
```

## Three-Source Sensor

The best match companion uses three sources:
1. **Feed** — event data, coordinates, stats blocks
2. **Lore files** — team narratives, group context, pre-match reports
3. **The user** — watching on TV, sees what the API can't

Don't guess from data alone. Don't narrate without data. Use all three.
