# Processed Data Contract
> Internal contract for football-wire processed artifacts
>
> Date: 2026-06-25
> Status: draft
> Audience: implementers

## Purpose

Processed data is football-wire's protocol-independent match truth. Live feeds, replay, query, web UI, and future integrations must read the same processed artifacts instead of reprocessing raw source files independently.

V1 storage is deliberately boring:

```text
JSONL append-only stream + JSON materialized state
```

SQLite may replace or augment this later behind repository interfaces.

## Reference Data Contract

Reference data is not processed match truth, but processed truth should point at it through canonical identities.

Current reference sources:

```text
data/static/
  tournaments/
    wc2026.toml                         # tournament rules and source identifiers
    wc2026-data/                        # imported worldcup.json structured data
      worldcup.teams.json
      worldcup.groups.json
      worldcup.squads.json
      worldcup.stadiums.json
      worldcup.json                     # scheduled matches / seeded results
    wc2026-enrichment/                  # curated structured reference augmentation
    wc2026-lore/                        # curated narrative reference
  teams/{CODE}.md                       # LM-readable team narrative/context
  preamble/*.md                         # LM-readable data and football notes
  venues.csv                            # fbw venue projection/augmentation
```

`wc2026-data/` currently comes from `/data/workspace/tmp/repos/ext/worldcup.json/2026`. The external source shape is an import format, not an application-wide model.

Reference-layer rules:

- Team canonical identity is the FIFA-style team code used by the reference set, e.g. `AUT`.
- Player canonical identity should be stable within the tournament, e.g. `AUT-8`, derived from team code plus shirt number when source player IDs are unavailable or unstable.
- Source-specific IDs remain source refs, e.g. FIFA `IdTeam` or `IdPlayer`. They do not replace canonical identity.
- Curated `*-enrichment/` paths are static reference augmentation. Do not confuse them with live `source_patch` records.
- Narrative reference is positioning material for LM consumers. It is not state-machine truth.

Processed stream/state records should embed compact reference snapshots for convenience, but the canonical source of static team/player/group/venue data remains the reference service/imported static data.

## Canonical Paths

```text
data/processed/
  streams/
    {match_id}.jsonl          # canonical append-only StreamEvent ledger
  state/
    {match_id}.json           # latest materialized MatchState view
  index/
    matches.json              # optional v1+ lookup/projection cache
```

Only `fbw-stream` writes `streams/` and `state/` during normal operation.

Read-only clients use `MatchReadService`, not ad hoc processed file reads.

Raw source patch queues are not processed truth. They remain source-shaped evidence under source-specific raw paths, for example:

```text
data/raw/api-fifa/source-patches/{match_id}.jsonl
```

## StreamEvent JSONL

Each line in `data/processed/streams/{match_id}.jsonl` is one JSON object.

Required v1 fields:

| Field | Type | Meaning |
|---|---:|---|
| `schema_version` | int | Stream record schema version. Start at `1`. |
| `seq` | int | Monotonic sequence number within the match stream. Starts at `1`. |
| `match_id` | str | FIFA/competition match identifier as string. |
| `kind` | str | `event`, `source_patch`, `correction`, `stats`, `annotation`, or `system`. |
| `event_type` | str | Normalized action type, e.g. `goal`, `foul`, `shot`, `direction_determined`. Empty for generic system records. |
| `source` | str | Source name or producer, e.g. `fifa`, `espn`, `ape`, `fbw`. |
| `source_id` | str | Original source event ID when available. Empty if not source-backed. |
| `target_source_id` | str | Source event ID affected by a source patch/correction. Empty if not applicable. |
| `minute` | object | Match clock/minute object. See below. |
| `observed_at` | str | ISO 8601 UTC timestamp when fbw observed/created this stream event. |
| `available_at` | str | ISO 8601 UTC timestamp when this information became available to processing. |
| `team` | object/null | Canonical team reference for team-scoped events. |
| `player` | object/null | Canonical player reference for player-scoped events. |
| `score` | object | State-machine score after applying this event. |
| `source_patch` | object/null | Source evidence changes represented by this record. |
| `features` | list | Derived football-wire features for UI/query/rendering. |
| `text` | object | Pre-rendered text views for simple consumers. |
| `flags` | list[str] | Warnings/suspect markers/data quality flags. |
| `links` | object | Optional evidence pointers to raw/source records. |

Canonical `StreamEvent` records do **not** require `emit_after`. Anti-spoiler delay and replay timing are delivery policy views, not stored match truth.

### Kind Semantics

| Kind | Meaning |
|---|---|
| `event` | A football event accepted by the state machine. |
| `source_patch` | A source added or changed evidence for an existing source record. |
| `correction` | football-wire corrected its own match truth or visible interpretation. |
| `stats` | Stats snapshot or stats delta. |
| `annotation` | Non-event note attached to match context/minute. |
| `system` | Processor/system lifecycle record. |

A late FIFA coordinate is a `source_patch`. The computed phrase `near opponent box, right` is a derived feature. A phantom goal being voided is a `correction`.

### Minute Object

```json
{
  "phase": "2H",
  "display": "67'",
  "base": 67,
  "added": 0,
  "sort": 67.0
}
```

Rules:

- `display` is for humans.
- `sort` is for ordering and range queries.
- `phase` should use the state machine's phase vocabulary where available.

### Team Object

```json
{
  "code": "AUT",
  "abbr": "AUT",
  "name": "Austria",
  "source_ids": {"fifa": "43948"}
}
```

Rules:

- `code` is the canonical tournament/reference identity.
- `abbr` is the compact display label and usually equals `code`.
- `source_ids` is optional and may be partial.
- Legacy code may still carry source IDs as `id`; new stream records should prefer `code` plus `source_ids`.

### Player Object

```json
{
  "ref": "AUT-8",
  "source_ids": {"fifa": "12345"},
  "name": "David ALABA",
  "shirt_number": 8,
  "position": "DF"
}
```

Rules:

- `ref` is the canonical tournament/reference identity where available.
- `source_ids` preserves source-specific player IDs.
- Unknown fields may be `null` or omitted.
- Do not invent names when unresolved; preserve source IDs and let display use fallback text.

### Score Object

```json
{
  "home": 1,
  "away": 0,
  "display": "[1-0]"
}
```

The score is the state-machine score after applying the event, not raw event score fields.

### Source Patch Object

`source_patch` records source evidence changes. They describe what changed in source data, not what football-wire derived from it.

```json
{
  "target_source_id": "abcdef",
  "changes": [
    {"field": "PositionX", "old": null, "new": 74},
    {"field": "PositionY", "old": null, "new": 22}
  ]
}
```

Rules:

- `field` uses source field names where the patch is source-shaped.
- The processor may convert a source patch into state-machine input.
- The same `StreamEvent` may include derived `features` that became possible after the source patch.

### Derived Features List

`features` stores structured semantic atoms produced by football-wire from evidence plus match state.

Example:

```json
[
  {
    "key": "pitch_position",
    "values": {
      "half": "opponent_half",
      "zone": "near_opponent_box",
      "side": "right",
      "x": 74,
      "y": 22
    }
  },
  {
    "key": "free_kick",
    "values": {
      "team_abbr": "JOR",
      "distance_m": 22,
      "danger": "promising_crossing_position"
    }
  }
]
```

Rules:

- `key` names fbw-generated meaning, not raw API text.
- Features may use reference data, for example team code, player role, venue altitude, or static tactical notes.
- `values` must be JSON-serializable.
- Localisation/templates consume features; they do not recompute football meaning.
- Features are recomputable projections. Reprocessing may rewrite them if feature logic improves.

### Text Object

```json
{
  "monitor": "2H 67'  FOUL  AUT | ... [1-0]",
  "plain": "AUT foul at 67' [1-0]"
}
```

Rules:

- `monitor` is the stdout-compatible line or block.
- `plain` is optional.
- Text is a convenience projection. Structured consumers should prefer `features` where possible.

### Links Object

Evidence links are optional but recommended.

```json
{
  "raw_event_id": "abcdef",
  "raw_event_path": "data/raw/api-fifa/events/400021499.jsonl",
  "source_patch_path": "data/raw/api-fifa/source-patches/400021499.jsonl",
  "match_path": "data/raw/api-fifa/matches/400021499.json",
  "team_lore_path": "data/static/teams/AUT.md"
}
```

Paths should be repo-relative where practical.

## Materialized Match State JSON

`data/processed/state/{match_id}.json` is the fast current view for UI/query.

Required v1 fields:

| Field | Type | Meaning |
|---|---:|---|
| `schema_version` | int | State schema version. Start at `1`. |
| `match_id` | str | Match identifier. |
| `updated_at` | str | ISO 8601 UTC timestamp of projection write. |
| `latest_seq` | int | Latest stream sequence included in this projection. |
| `status` | str | Match status/phase summary. |
| `clock` | object | Current phase/minute display. |
| `home` | object | Home team summary. |
| `away` | object | Away team summary. |
| `score` | object | Current state-machine score. |
| `direction` | object/null | Current play direction if known. |
| `goals` | list | Non-voided and voided goal records. |
| `cards` | list | Card summary records. |
| `subs` | list | Substitution summary records. |
| `stats` | object | Latest stats snapshot by source/period. |
| `data_quality` | list[str] | Current warnings/data quality notes. |

Minimal example:

```json
{
  "schema_version": 1,
  "match_id": "400021499",
  "updated_at": "2026-06-23T20:14:02Z",
  "latest_seq": 184,
  "status": "live",
  "clock": {"phase": "2H", "display": "67'", "sort": 67.0},
  "home": {"code": "AUT", "abbr": "AUT", "name": "Austria", "source_ids": {"fifa": "43948"}},
  "away": {"code": "JOR", "abbr": "JOR", "name": "Jordan", "source_ids": {"fifa": "43821"}},
  "score": {"home": 1, "away": 0, "display": "[1-0]"},
  "direction": null,
  "goals": [],
  "cards": [],
  "subs": [],
  "stats": {},
  "data_quality": []
}
```

## Ordering and Idempotency

- Stream `seq` is assigned by the stream processor after state-machine processing.
- `seq` order is processing/output order, not necessarily raw source order.
- `minute.sort` supports match-clock ordering queries.
- Reprocessing a match from raw may rewrite processed files from scratch.
- Appending duplicate `source_id` events should be prevented by processing/state-machine deduplication before `StreamEvent` creation.
- Source patches should target the original source event through `target_source_id`.
- Reprocessing may improve canonical reference resolution when new source-ID mappings are learned.

## Query Semantics

Common filters should operate on processed data:

```text
since_seq=N              # live polling / incremental UI
until_minute=M           # match-clock query/replay
from_minute=A            # match-clock range start
available_as_of=T        # processor-available wall-clock view
kind=goal,shot           # event kind/type filters
```

Consumer delivery views, such as anti-spoiler delay and replay pacing, are computed by services/sinks using timer policies. They should not require rewriting canonical stream records.

Static queries such as groups, squads, venues, and player reference lookups operate on reference data, not processed stream data. Dynamic match queries such as current score, goals, cards, substitutions, and live events operate on processed data.

## Compatibility Policy

During the refactor:

- Existing stdout Monitor text should remain useful.
- Existing raw data layout remains valid until a migration is explicitly made.
- Processed schema may evolve while docs are marked draft.
