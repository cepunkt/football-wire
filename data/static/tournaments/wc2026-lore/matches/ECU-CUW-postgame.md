# ECU 0-0 CUW — Matchday 2, Group E (Post-Game)

## Result
Ecuador 0 - 0 Curaçao | Kansas City Stadium | 2026-06-21

## The Story
Eloy Room produced one of the greatest goalkeeping performances in
World Cup history. Ecuador had 28 shots (15 on target, 19 from inside
the penalty area) and could not score. Room saved everything — headers
from 4 metres, shots from 5 metres, drives from the edge of the box.
When Room wasn't saving, the crossbar was (Preciado 89').

Curaçao didn't just defend. They had 10 shots of their own, created
genuine chances through Locadia's runs, Bacuna's set pieces, and
Comenencia's directness. At 60 minutes, CUW produced three shots in
one minute and had Ecuador rattled.

## Key Moments
- 3' Valencia shot from 11m — Room's first save. Set the pattern.
- 24' Hydration break — CUW used it to regroup, emerged stronger
- 39' Mirror tactical fouls — Alcívar (ECU) and L.Bacuna (CUW) both
  booked for stopping counters. Mutual respect through cynicism.
- 60' CUW burst — three shots in one minute. Comenencia, L.Bacuna,
  Locadia. The bus left the parking lot.
- 66' The siege — four ECU shots in two minutes. Valencia header 4m,
  Pacho rebound 8m, Rodríguez 6m. All saved. All Room.
- 68' Hydration break — killed Ecuador's momentum at peak pressure
- 89' Preciado hits the crossbar from a cross
- 90'+5' VAR check for penalty — no penalty. Room survives.

## Scorers
None. 0-0.

## Stats (Final — FIFA)
| | ECU | CUW |
|---|---|---|
| Possession | 63% | 25% (12% contested) |
| Shots | 27 | 10 |
| On target | 15 | 3 |
| Inside box | 19 | 4 |
| Corners | 8 | 0 |
| Passes | 671 (600 completed) | 236 (165 completed) |
| Crosses | 25 (7 completed) | 3 (1 completed) |
| Yellows | 1 | 5 |
| Fouls | 7 | 10 |
| Forced turnovers | 15 | 24 |

## Man of the Match
**Eloy Room (GK, CUW)** — career-defining performance. 15+ saves
against 28 shots. Stopped everything from every range and angle.
The kind of game that gets a transfer to a top European league.

## Standout Performers
- **Eloy Room** (#1 CUW) — see above. Historic.
- **Enner Valencia** (#13 ECU) — 6+ shots, captain's effort, cursed
  by Room. Tried central, tried corners, nothing worked.
- **Dick Advocaat** (coach CUW) — masterclass in game management.
  Five subs all tactical: removed all three yellow-carded midfielders,
  brought on fresh forwards, managed the game clock through fouls.
- **Leandro Bacuna** (#10 CUW) — booked but brave. Shot on a yellow,
  competed in midfield against Caicedo.

## Tactical Observations
- Ecuador dominated territory but lacked creativity in the final third.
  Most shots placed centrally — Room positioned well and absorbed.
- CUW's 5-3-2 evolved during the match. Started defensive, grew into
  a genuine counter-attacking threat by 60'.
- Both hydration breaks disrupted Ecuador's momentum (24' and 68').
  The 68' break came during Ecuador's most sustained pressure — four
  shots in two minutes immediately before the break.
- CUW's tactical fouling was systematic: 10 fouls, 5 yellows, all
  calculated to break rhythm and waste time.
- Ecuador's late subs showed desperation: defenders off, forwards on.
  By 89' they were reduced to goalkeeper long balls.

## Group E Implications
| Team | P | W | D | L | GF | GA | GD | Pts |
|------|---|---|---|---|----|----|-----|-----|
| GER  | 2 | 2 | 0 | 0 | 9  | 2  | +7  | 6   |
| CIV  | 2 | 1 | 0 | 1 | 2  | 2  |  0  | 3   |
| ECU  | 2 | 0 | 1 | 1 | 0  | 1  | -1  | 1   |
| CUW  | 2 | 0 | 1 | 1 | 1  | 7  | -6  | 1   |

Germany through with 6 pts. CIV in strong position on 3 pts with
CUW to play. Ecuador and CUW both on 1 pt — need results on MD3.
Ecuador face Germany. CUW face CIV. Both face uphill battles, but
after tonight CUW know they can compete with anyone.

## The Narrative
The night Eloy Room held Ecuador to 0-0 in Curaçao's first ever
World Cup. Population 150,000. Hammered 7-1 by Germany on matchday 1.
Came back and produced the defensive performance of the tournament
so far. The Dutch royals watched from the stands as their Caribbean
territory's goalkeeper wrote himself into World Cup history.

28 shots, 15 on target, 19 from inside the box. 0-0. Room leaves
no room.

## Data Quality Notes (from live feed)
- Both hydration breaks logged as DELAY (type 77) — GER-CIV used
  PAUSE (type 83) for the same thing. Inconsistent API typing.
- Zone classification broken for extreme coordinates: (100,100) and
  (100,2) mapped to "6-yard box" — needs Y-axis check.
- Sub inversion consistent with other matches.
- Post-emit enrichment corrections working (always-poll fix) but
  inconsistent — some fire, some don't.
- Corner events under-logged: 2 in raw JSONL vs 8 per ESPN stats.
