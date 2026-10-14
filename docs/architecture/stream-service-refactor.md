# Stream/Service Refactor
> Internal architecture contract for protocol-independent football-wire processing
>
> Date: 2026-06-25
> Status: draft
> Audience: implementers

## Purpose

football-wire is moving from "feed client prints lines" to a service-shaped architecture where live watching, replay, CLI queries, web UI, and future protocols consume the same processed match truth.

The central rule:

```text
communication protocol is an adapter, not architecture
```

Changing stdout to HTTP, SSE, WebSocket, MCP, or another client must not require redesigning match processing.

## Vocabulary Split

The old code uses `enrichment` for two different concerns. The refactor must keep them separate. It also needs a named reference-data layer so the worldcup.json corpus does not leak through every module.

| Term | Meaning | Example |
|---|---|---|
| Reference data | Tournament/team/player/venue/schedule seed data | `worldcup.teams.json`, `worldcup.squads.json`, `wc2026.toml` |
| Reference augmentation | Curated static facts/lore layered onto reference data | coaches JSON, team Markdown, hydration notes |
| Raw observation | Source-shaped live evidence as received | FIFA timeline event with `PositionX=null` |
| Source patch | Source adds or changes live evidence later | `PositionX: null -> 74`, `IdPlayer` appears |
| State decision | football-wire match truth after validation | goal accepted, phantom goal voided, sub direction resolved |
| Derived feature | football-wire computed meaning from evidence/state/reference | `near opponent box`, `low far post`, `dangerous free kick` |
| Presentation | Rendered text/UI/LM surface | `>> ENRICHED: 67' FOUL ...` |

Rules:

- Reference data is static identity/context seed material. It does not decide live match truth.
- Reference augmentation may enrich static context, but it is not a source patch and not a derived live feature.
- Source patches are live evidence changes. They may affect state.
- Derived features are recomputable projections. They do not mutate source evidence or reference data.
- Corrections are football-wire decisions that previous visible truth was wrong or incomplete.
- `enriched` may remain a user-facing feed word and existing static directory name, but internal live contracts should prefer `source_patch` and `derived_feature`.

## Target Process Split

```text
fbw up
  ├─ fbw-daemon   # public API acquisition -> raw files/source patches
  ├─ fbw-stream   # raw/source patches -> state machine -> processed stream/state
  └─ fbw-server   # read-only ape UI/API over processed data
```

`fbw up` is an ape-facing wrapper/supervisor. It may start and stop multiple subprocesses, but it does not own football truth.

### `fbw-daemon`

Responsibility:

- Poll configured data sources.
- Write source-shaped raw data under `data/raw/`.
- Detect source patches by comparing later source snapshots with earlier ones.
- Write source patch evidence under raw/source-specific paths.
- Maintain source/status hints such as raw live match tracking.
- Stay boring and restartable.

Allowed dependencies:

- configuration
- source clients/fetchers
- raw file storage helpers
- source patch detection/diffing
- lightweight daemon logging

Forbidden dependencies:

- state machine processing
- derived feature computation
- narrative/display/localisation logic
- processed repository writes
- web server request handling

### `fbw-stream`

Responsibility:

- Watch/read raw files and source patch queues for one or more matches.
- Convert raw source data through adapters into `StateInput`.
- Apply inputs to `MatchStateMachine`.
- Convert `StateOutput` plus match state into `StreamEvent` records.
- Attach derived features from geometry/timing/context/reference data.
- Write canonical processed artifacts.
- Optionally fan out to stdout for Monitor-compatible live watching.

This is the only v1 writer of processed match truth.

### `fbw-server`

Responsibility:

- Serve ape-facing HTML and JSON API from processed data, structured reference data, and narrative reference material.
- Use `MatchReadService` for dynamic match truth.
- Use `TournamentReferenceService` for static teams, squads, venues, schedule, groups, and source-ID mappings.
- Remain read-only during normal requests.

Forbidden behavior:

- parse raw FIFA/ESPN files directly for normal UI routes
- infer goals, substitutions, direction, free-kick danger, shot zones, or narrative geometry
- mutate processed truth during a browser request
- become the process supervisor

### CLI / Monitor Adapters

`fbw-query`, `fbw-watch`, and future tools are protocol adapters over the same service boundaries.

- Dynamic/live match truth reads processed data through `MatchReadService`.
- Static tournament reference data reads through `TournamentReferenceService` where practical.
- Narrative reference material may be exposed as file pointers for LM consumers.
- `fbw-watch` may remain as compatibility UX, but should delegate live feed production to stream processing/sinks.
- Monitor stdout is one sink, not the product.

## Data Flow

Reference data is loaded once and used by adapters, feature projection, query, and presentation. It is not part of the live event stream.

```text
worldcup.json + fbw curated static data
  -> ReferenceImporter
  -> TournamentReferenceService
      ├─ adapter identity resolution
      ├─ feature projection context
      ├─ CLI/static query
      ├─ UI/static views
      └─ LM context pointers
```

Live match data flows separately:

```text
raw source responses
  -> source patch detection
  -> raw observations + raw source patches
  -> adapters
  -> StateInput
  -> MatchStateMachine
  -> StateOutput + MatchState
  -> derived feature projection
  -> StreamEvent
  -> ProcessedRepository
  -> MatchReadService
  -> protocol adapters
      ├─ stdout Monitor feed
      ├─ CLI query
      ├─ stdlib HTTP ape UI
      ├─ replay/timeline tools
      └─ future MCP/SSE/WebSocket adapters
```

## Layer Rules

### Reference Layer

Reference data is the static identity and context substrate.

- Structured upstream data lives under `data/static/tournaments/{tournament}-data/` and currently comes from `/data/workspace/tmp/repos/ext/worldcup.json`.
- Tournament rules live in `data/static/tournaments/{tournament}.toml`.
- Curated structured augmentation may live under existing paths such as `data/static/tournaments/{tournament}-enrichment/`; this is static reference augmentation, not live source patching.
- Narrative reference material lives in Markdown/team lore/preamble files and is positioning material for LM consumers.
- Source-specific IDs are mapped to canonical team/player identity at adapter or reference-service boundaries.
- Reference data may be corrected by an explicit static-data update. It is not mutated by live match processing.

### Raw Layer

Raw data is source-shaped evidence.

- It is stored under `data/raw/`.
- It preserves what the source said, including lies, omissions, and contradictions.
- Source patches are raw evidence deltas, not football-wire truth.
- Normal user-facing tools should not use raw data directly except for diagnostics or reprocessing.

### Processing Layer

Processing turns evidence into football-wire decisions.

- Source-specific mess belongs in adapters.
- Adapters may use reference data to resolve source IDs into canonical team/player refs.
- Football truth belongs in the state machine.
- Late source data enters as source patches, then becomes state-machine input where relevant.
- Football meaning derived from geometry/timing/context/reference belongs in derived feature projection.
- Processed output must be replayable from raw observations and source patches.

### Presentation Layer

Presentation chooses how to deliver processed truth.

- stdout, CLI, HTTP, files, and future protocols are adapters.
- Protocol adapters may filter, paginate, serialize, template, poll, or wait.
- Protocol adapters may not decide football truth.
- String templates may render derived features; they must not recompute them.

## Timing Model

Canonical processed data should store observation timing, not consumer delivery policy.

```text
StreamEvent timing fields:
  observed_at    # when football-wire observed/created the record
  available_at   # when this information became available to processing
```

Consumer delivery is a view over the canonical stream:

```text
DeliveryEnvelope / sink-local fields:
  visible_at / emit_after   # earliest time this consumer may see it
  delivered_at              # when a sink actually delivered it
  delivery_policy           # immediate, live_delay, replay, etc.
```

Live TV sync uses a live delay policy:

```text
visible_at = available_at + anti_spoiler_delay
```

Replay uses a replay clock policy:

```text
visible_at = replay_start_wall_time + mapped_match_clock_offset
```

Formatting must not sleep. Repositories must not sleep. Sinks/timer policy decide delivery.

## V1 Non-Goals

These are intentionally out of the first implementation sequence:

- SQLite processed backend.
- WebSocket/SSE push.
- Server-side processing on normal HTTP requests.
- Daemon/server fusion.
- Rewriting all query commands at once.
- Perfect schema permanence.
- Public polished architecture documentation.

## First Implementation Target After Docs

The first code slice should prove:

```text
existing Monitor-useful stdout feed still works
AND
the same stream records are persisted to JSONL
```

Acceptance artifact:

```text
data/processed/streams/{match_id}.jsonl
```

The user-visible feed may keep current text behavior while internals gain stream/sink boundaries. Source patches and derived features should be named cleanly even if the first slice only carries a minimal feature list.
