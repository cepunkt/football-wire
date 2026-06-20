# FIFA Public API Reference
> Reverse-engineered from observation. No official documentation available.
> Last updated: 2026-06-20

## Base URL

```
https://api.fifa.com/api/v3
```

No authentication required. Requires browser-like User-Agent (bot UAs get 403).

## Endpoints

### Schedule

```
GET /calendar/matches?idCompetition={comp}&idSeason={season}&count=200
```

Returns all tournament matches with basic info: teams, date, status, group.
Each match has `IdMatch` and `IdStage` (needed for other endpoints).

### Live Match State

```
GET /live/football/{idCompetition}/{idSeason}/{idStage}/{idMatch}
```

Full match state. Overwritten each poll. Contains:

| Field | Type | Notes |
|-------|------|-------|
| HomeTeam / AwayTeam | object | Full roster (25 players), coaches, staff, tactics |
| HomeTeamScore / AwayTeamScore | int | Sometimes null — check Team.Score as fallback |
| MatchStatus | int | 0=finished, 1=scheduled, 3=live |
| MatchTime | string | Current match minute ("67'") |
| Stadium | object | Name, city, country. No capacity or dimensions. |
| Weather | object | Temperature, humidity, wind. Often null. |
| Officials | array | Referee name, country, role type |
| Attendance | string | Needs int() conversion |
| BallPossession | null | Never populated in free tier |
| TerritorialPossesion | null | Never populated in free tier |

#### Team Object

| Field | Type | Notes |
|-------|------|-------|
| IdTeam | string | Unique team ID |
| Abbreviation | string | Three-letter code (MEX, KOR, etc.) |
| ShortClubName | string | Display name |
| Tactics | string | Formation ("4-3-3") |
| Players | array | 25 players with Status (1=starter, 2=bench) |
| Coaches | array | Role 0=head coach |
| Staffs | array | Role 10=assistant staff |
| Substitutions | array | **Reliable ON/OFF** — IdPlayerOn, IdPlayerOff correctly labeled |
| Goals | array | Player, minute, type |

#### Player Object

| Field | Type | Notes |
|-------|------|-------|
| IdPlayer | string | Unique player ID |
| PlayerName / ShortName | localized | Array of {Locale, Description} |
| ShirtNumber | int | Jersey number |
| Position | int | 0=GK, 1=DF, 2=MF, 3=FW |
| Status | int | 1=starter, 2=substitute. Fixed, doesn't change during match. |
| Captain | bool | |
| PlayerPicture | object | URL with UUID (digitalhub CDN, non-guessable) |

### Timeline (Events)

```
GET /timelines/{idCompetition}/{idSeason}/{idStage}/{idMatch}
```

Returns `Event` array — the match event log.

#### Event Object

| Field | Type | Notes |
|-------|------|-------|
| EventId | string | Unique, but API sometimes resends same event with new ID |
| Type | int | See event types below |
| TypeLocalized | localized | Human-readable type name |
| MatchMinute | string | "45'+3'" format. The minute being played, not elapsed. |
| Timestamp | ISO 8601 | When the event actually occurred |
| EventDescription | localized | **Unreliable** — wrong team names, inverted sub directions |
| IdPlayer | string | Primary player. For subs: unreliable ON/OFF mapping |
| IdSubPlayer | string | Secondary player (subs only). Also unreliable. |
| IdTeam | string | **Sometimes wrong** — misattributes players to opposing team |
| HomeGoals / AwayGoals | int | Running score. May be stale from dual pipeline. |
| PositionX / PositionY | float | Shot position. 0-100 scale. Often null on initial delivery, enriched 20-40s later. |
| GoalGatePositionX / Y | float | Goal placement. Same enrichment delay. |
| Period | int | 3=first half, 5=second half |
| Qualifiers | array | Usually empty |

#### Event Types (discovered through observation)

| Type | Name | Structural | Notes |
|------|------|-----------|-------|
| 0 | Goal | no | IdPlayer sometimes null on delivery, enriched later |
| 1 | Assist | no | |
| 2 | Yellow Card | no | Can be player, head coach (named), or staff (anonymous) |
| 3 | Red Card | no | |
| 4 | Second Yellow / Red | no | |
| 5 | Substitution | no | IdPlayer/IdSubPlayer mapping unreliable. Use on-pitch tracking. |
| 6 | Penalty Awarded | no | |
| 7 | Period Start | yes | |
| 8 | Period End | yes | |
| 12 | Shot | no | Coordinates enriched 21-43s after initial delivery |
| 15 | Offside | no | IdPlayer sometimes null, enriched with player name later |
| 16 | Corner | no | |
| 18 | Foul | no | Position coordinates enriched 21-64s later |
| 41 | Penalty Goal | no | |
| 57 | Save | no | Sometimes appears without a preceding shot event |
| 70 | Injury | no | |
| 71 | VAR | no | |
| 77 | Delay | yes | Unspecified match delay. Different from type 83 (hydration). |
| 78 | Resume | yes | |
| 79 | Coin Toss (kickoff) | yes | "chose to kick off" |
| 80 | Coin Toss (side) | yes | "chose the side" |
| 83 | Pause (hydration) | yes | Hydration break |

Types not yet observed: 9-11, 13-14, 17, 19-40, 42-56, 58-69, 72-76, 81-82.

## Stage IDs (WC 2026)

| Stage | IdStage | Matches |
|-------|---------|---------|
| Group Stage | 289273 | 72 |
| Round of 32 | 289287 | 16 |
| Round of 16 | 289288 | 8 |
| Quarter-final | 289289 | 4 |
| Semi-final | 289290 | 2 |
| 3rd Place | 289291 | 1 |
| Final | 289292 | 1 |

Each match has `IdStage` in the schedule. **Must be looked up per match** —
hardcoding the group stage ID breaks knockout matches.

## Competition IDs

| Competition | ID |
|-------------|-----|
| FIFA World Cup | 17 |
| Season 2026 | 285023 |

## Enrichment Behaviour

The API enriches events after initial delivery. Measured delays:

| Field | Typical delay | Notes |
|-------|--------------|-------|
| Shot PositionX/Y | 21-43 seconds | ~2-4 daemon poll cycles |
| Shot GoalGatePosition | 21-43 seconds | Same timing as position |
| Goal coordinates | ~84 seconds | Slower than shots |
| Goal IdPlayer | 20-84 seconds | Scorer attribution arrives late |
| Goal EventDescription | Same as IdPlayer | "Brazil score!" → "CUNHA scores!!" |
| Foul coordinates | 21-64 seconds | |
| Offside IdPlayer | ~64 seconds | |

## Known Data Quality Issues

1. **Event ordering**: dual pipeline — system events on time, manual play events minutes late
2. **Team attribution**: EventDescription sometimes names wrong team. IdTeam also wrong occasionally.
3. **Sub direction**: IdPlayer/IdSubPlayer mapping inconsistent across matches (all inverted in MEX-KOR, correct in CAN-QAT, mixed in BRA-HAI)
4. **Score staleness**: HomeGoals/AwayGoals from dual pipeline can show stale values
5. **Duplicate events**: same event resent with different EventId
6. **Anonymous cards**: staff/assistant coach bookings have no IdPlayer
7. **Saves without shots**: save events sometimes appear without preceding shot event
8. **Description templates**: all descriptions are templates with name/team inserted. Team can be wrong.
9. **Possession/passes**: never available through public API. Use ESPN endpoint.
