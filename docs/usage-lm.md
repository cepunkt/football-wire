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
./fbw-watch --delay 30 <match_id_or_team_code>

# Or without wrapper:
PYTHONPATH=src python -m fbw.feed --delay 30 --cycle 10 <match_id>
```

The `--delay` syncs with TV broadcast (~30s). The `--cycle` controls
how often events are batched (10s default).

## What You Receive

### 1. Preamble (data quality notes)
Inline context about how to read the data — coordinate system,
trust levels, known API issues. Read and internalise.

### 2. Match header
Score, venue, lineups. One line per squad.

### 3. Context pointers
```
Lore: data/static/tournaments/wc2026-lore/teams/{ECU,CUW}.md
```

**Read these files** for team narratives, group standings, pre-match
context. The Monitor stream stays lean — deep context is in files,
not stdout.

### 4. Live events
```
 1H  10'  >>GOAL<<            ESP | Lamine YAMAL scores!! [1-0]
 2H  68'  SHOT (on target)    GER | Deniz UNDAV | from 6m, 6-yard box, central (5,47) placed low, centre (0.4m off centre, 0.1m high) [1-1]
 2H  75'  SUB                 CIV | ON: Simon ADINGRA, OFF: Amad DIALLO [1-1]
 1H  18'  FOUL                URU | CANOBBIO commits a foul. | in opponent's half, midfield, right (70,7) [0-0]
     18'    → FREE KICK       CPV | 82m from goal — deep, no immediate threat
 1H  16'  CORNER              URU | Maxi ARAUJO takes a corner kick. | near side [0-0]
 2H  59'  SAVE                IRN | Alireza BEIRANVAND saves. [0-0]
```

### Event format: `phase  minute  MARKER  TEAM | details [score]`

- **Phase prefix**: `1H`, `2H`, `ET1`, `ET2`, `PEN`
- **Minute**: `45'` = in the 45th minute (clock shows 44:xx).
  Added time: `45'+3'` = 3rd minute of added time.
- **Markers**: `>>GOAL<<`, `SHOT`, `SHOT (on target)`, `SUB`, `YELLOW`,
  `RED`, `FOUL`, `CORNER`, `OFFSIDE`, `SAVE`, `BREAK`, `RESUME`,
  `--- PERIOD`, `--- PERIOD END`, `** VAR`, `~~GOAL VOIDED~~`, `COIN`
- **Team tag**: three-letter code from roster. Trust this over description.
- **Score**: `[home-away]` from state machine. Cross-checked against
  canonical match data — phantom goals from VAR are voided automatically.
- **`[suspect]`**: data couldn't be fully verified.

### Coordinates and Perspective

All descriptions use the **attacker's perspective** — the player
performing the action, facing the goal they're attacking.

**Pitch coordinates** `(X,Y)`: 0-100 on both axes. Fixed camera
perspective. X=0 left, X=100 right. Y=0 near side (camera), Y=100
far side. These don't change between halves — teams swap ends but
the coordinate system stays fixed.

**Play direction**: Announced in the feed when determined:
```
--- Play direction: BEL → high X, IRN → low X (inferred from 2 events) ---
```
At half-time, directions swap. A swap announcement is emitted.
When direction is known, foul/offside positions are described
relative to the team: "in own half", "in opponent's half",
"near own box", "near opponent's box".

**Shot position**: Distance from goal (metres), pitch zone, side.
```
from 17m, inside box, left (89,68)
```
Zones: `6-yard box` (<5.5m), `inside box` (<16.5m), `edge of box`
(<24m), `outside box` (<35m), `long range` (>35m). Zone checks both
depth AND width — a shot from the corner flag is near the goal line
but outside any box.

**Goal placement**: Where the ball entered the goal, from attacker's
view. Post-to-post (7.32m) and ground-to-crossbar (2.44m).
```
placed low, right (1.0m off centre, 0.1m high)
```

**Corner position**: `near side` (camera side) or `far side`.

**Free kick danger** (follow-up to fouls with position):
```
  → FREE KICK  CPV | 23m from goal — promising — edge of shooting range
```
Distance from the receiving team's attacking goal. Classifications:
`DANGEROUS` (<20m central), `promising` (<30m or crossing position),
`midfield`, `deep`.

### Post-emit corrections
```
>> ENRICHED: 65'  SHOT  ESP | Ferran TORRES | from 16m, inside box, left (12,36)
>> CORRECTED: Goal disallowed [0-0]
```
When coordinates or descriptions arrive after an event was already
emitted. Enrichments for fouls/offsides use the same direction-aware
format as live events.

### Stats blocks (ESPN, when enabled)
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
- Rebound sequences (shot → post → rebound → goal shows as two events)

**Ask the user.** They are watching on TV. Their eyes are the best
sensor. "What happened?" is always a valid question.

## What To Distrust

- **Scores on events**: the state machine tracks canonical score and
  voids phantom goals via match data cross-check. Trust the score in
  the feed over raw event scores.
- **Substitution descriptions**: the API text may invert ON/OFF.
  Trust the `ON:` / `OFF:` tags which are resolved from roster tracking.
  `[suspect]` means resolution failed — ask the user.
- **Team names in descriptions**: occasionally wrong. Trust the team
  tag prefix (GER, CIV, ECU).
- **Shot counts**: API logs more than official stats count. ESPN stats
  block has the official numbers.
- **"On target"**: the API is generous. Some "on target" shots went
  over the bar untouched. Cross-check with ESPN stats.
- **Hydration breaks**: logged as BREAK or DELAY (inconsistent between
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

See [coordinates.md](coordinates.md) for the full coordinate system
and perspective convention documentation.
