# Service Contract
> Internal protocol-independent interfaces for football-wire refactor
>
> Date: 2026-06-25
> Status: draft
> Audience: implementers

## Purpose

This document defines the stable internal contracts that keep protocol adapters from owning application logic.

```text
processed storage + services are the app boundary
CLI/HTTP/stdout/MCP are adapters
```

## Data Boundary Terms

```text
reference_data   = static tournament/team/player/venue/schedule identity and context
reference_aug    = curated static facts/lore layered onto reference data
source_patch     = live source evidence changed or became more complete
derived_feature  = fbw-computed football meaning from evidence + match state + reference
correction       = fbw changed its own accepted/visible match truth
presentation     = text/UI/LM-friendly rendering of stream records
```

These are separate contracts. Do not hide them behind one internal `enrichment` type.

## ReferenceImporter

Import boundary for external/static datasets such as worldcup.json.

Pseudocode:

```python
class ReferenceImporter(Protocol):
    def import_tournament(self, source_dir: Path) -> TournamentReferenceData: ...
    def import_augmentation(self, source_dir: Path) -> ReferenceAugmentation: ...
```

Rules:

- Knows external source shapes such as `worldcup.teams.json`, `worldcup.squads.json`, `worldcup.groups.json`, `worldcup.stadiums.json`, and `worldcup.json`.
- Converts external shapes into fbw canonical reference structures.
- Does not process live FIFA/ESPN observations.
- Does not decide live match truth.
- Does not write processed stream/state artifacts.

## TournamentReferenceService

Read API for static tournament identity, context, and source-ID mappings.

Pseudocode:

```python
class TournamentReferenceService:
    def get_tournament(self) -> TournamentReferenceData: ...
    def get_rules(self) -> TournamentRules: ...
    def get_team(self, code: str) -> TeamInfo | None: ...
    def get_group(self, letter: str) -> GroupInfo | None: ...
    def get_group_teams(self, letter: str) -> list[TeamInfo]: ...
    def get_player(
        self,
        team_code: str,
        *,
        number: int | None = None,
        name: str | None = None,
        source: str | None = None,
        source_id: str | None = None,
    ) -> PlayerInfo | None: ...
    def get_venue(self, venue_ref: str) -> VenueInfo | None: ...
    def get_scheduled_match(self, match_ref: str) -> ScheduledMatchInfo | None: ...
    def resolve_team_name(self, name: str) -> TeamInfo | None: ...
    def resolve_source_team(self, source: str, source_id: str) -> TeamInfo | None: ...
    def register_source_team(self, source: str, source_id: str, team_code: str) -> None: ...
    def register_source_player(self, source: str, source_id: str, player_ref: str) -> None: ...
    def team_lore_path(self, code: str) -> Path | None: ...
```

Rules:

- Owns canonical reference identity for teams, players, groups, venues, and scheduled matches.
- Treats worldcup.json as an imported source format, not as a global application model.
- Source-specific IDs are mappings into canonical identities, not replacements for them.
- Static `*-enrichment/` directories are reference augmentation, not live source patches.
- Narrative Markdown is positioning/context material and may be returned as paths for LM consumers.
- Normal reads should not mutate reference data except explicit source-ID mapping registration by adapters/processors.

## ProcessedRepository

File-backed repository for v1 processed artifacts.

Pseudocode:

```python
class ProcessedRepository(Protocol):
    def append_stream_event(self, event: StreamEvent) -> None: ...
    def load_stream(
        self,
        match_id: str,
        *,
        since_seq: int | None = None,
        until_minute: float | None = None,
        from_minute: float | None = None,
        available_as_of: datetime | None = None,
        kinds: set[str] | None = None,
        event_types: set[str] | None = None,
    ) -> list[StreamEvent]: ...
    def save_state(self, state: MaterializedMatchState) -> None: ...
    def load_state(self, match_id: str) -> MaterializedMatchState | None: ...
    def list_processed_matches(self) -> list[MatchSummary]: ...
```

Rules:

- Repository knows file paths and JSON serialization.
- Repository does not parse raw source formats.
- Repository does not infer football meaning.
- Repository does not sleep for delay/replay.
- Repository stores canonical processing times, not consumer `emit_after` policy.
- Future SQLite backend must satisfy equivalent behavior.

## MatchReadService

Read API used by dynamic query tools, web server, replay tools, and future protocol adapters.

Pseudocode:

```python
class MatchReadService:
    def list_live_matches(self) -> list[MatchSummary]: ...
    def get_match_state(self, match_id: str) -> MaterializedMatchState | None: ...
    def get_events(
        self,
        match_id: str,
        *,
        since_seq: int | None = None,
        until_minute: float | None = None,
        from_minute: float | None = None,
        available_as_of: datetime | None = None,
        kinds: set[str] | None = None,
        event_types: set[str] | None = None,
    ) -> list[StreamEvent]: ...
    def get_goals(self, match_id: str) -> list[StreamEvent]: ...
    def get_shots(self, match_id: str) -> list[StreamEvent]: ...
```

Rules:

- This is the primary interface for dynamic match truth in `fbw-query` and `fbw-server`.
- It reads processed data, not raw data, for normal live/dynamic operation.
- It may provide convenience filters/projections.
- It must not mutate processed truth during reads.
- Static tournament reference commands should use `TournamentReferenceService` instead of processed match truth.

## SourcePatchDetector

Source-side diffing component, usually called by `fbw-daemon`/fetching code.

Pseudocode:

```python
class SourcePatchDetector(Protocol):
    def detect_patches(
        self,
        match_id: str,
        previous_snapshot: dict,
        new_snapshot: dict,
    ) -> list[SourcePatch]: ...
```

Rules:

- Detects source evidence changes such as late player IDs, descriptions, coordinates, and gate coordinates.
- Emits source-shaped deltas.
- Does not decide football truth.
- Does not compute derived football features.

## StreamProcessor

Write-side processor for raw-to-processed flow.

Pseudocode:

```python
class StreamProcessor:
    def catchup(self) -> None: ...
    def process_new_raw_events(self) -> list[StreamEvent]: ...
    def process_source_patches(self) -> list[StreamEvent]: ...
    def process_score_verification(self) -> list[StreamEvent]: ...
    def process_stats(self) -> list[StreamEvent]: ...
    def write_state_projection(self) -> None: ...
```

Pipeline:

```text
raw source record or source patch
  -> adapter (+ TournamentReferenceService for identity resolution)
  -> StateInput
  -> MatchStateMachine.apply()
  -> StateOutput / side output
  -> derived feature projection
  -> StreamEvent
  -> sinks/repository
```

Rules:

- StreamProcessor owns state-machine runtime for a match.
- StreamProcessor is the v1 writer of processed stream/state.
- StreamProcessor may fan out to sinks.
- StreamProcessor should be usable by `fbw-stream` and compatibility feed commands.
- Source patches that affect direction, goals, player identity, or geometry should flow through the same processor path as initial events.
- StreamProcessor may register source ID mappings discovered from live data, but should not mutate static reference facts.

## FeatureProjector

Derived feature computation boundary.

Pseudocode:

```python
class FeatureProjector:
    def derive_features(
        self,
        output: StateOutput,
        state: MatchState,
        reference: TournamentReferenceService,
    ) -> list[DerivedFeature]: ...
    def project_stream_event(
        self,
        output: StateOutput,
        state: MatchState,
        reference: TournamentReferenceService,
        *,
        source_patch: SourcePatch | None = None,
    ) -> StreamEventDraft: ...
```

Rules:

- Computes football-wire meaning from evidence plus match state and relevant reference data.
- Examples: shot distance, pitch zone, near/far post, free-kick danger, direction-aware side, venue altitude context, player role labels.
- Does not read raw source files directly.
- Does not sleep, print, or write processed files.
- Does not mutate source evidence or reference data.

## TimerPolicy

Delivery timing abstraction for live delay and replay.

Pseudocode:

```python
class TimerPolicy(Protocol):
    def visible_at(self, event: StreamEvent) -> datetime: ...
    def wait_until_visible(self, event: StreamEvent) -> None: ...
```

Expected implementations:

```text
ImmediateTimerPolicy
  visible immediately; useful for catchup/backfill/tests

LiveDelayTimerPolicy
  visible_at = available_at + configured anti-spoiler delay

ReplayTimerPolicy
  visible_at = replay_start + match-clock mapping
```

Rules:

- Formatting does not sleep.
- Repository does not sleep.
- Timer policy controls visibility/delivery, not football truth.
- Timer policy must not rewrite canonical StreamEvent records.

## StreamSink

Output target for stream records.

Pseudocode:

```python
class StreamSink(Protocol):
    def write(self, event: StreamEvent) -> None: ...
    def flush(self) -> None: ...
    def close(self) -> None: ...
```

V1 sinks:

```text
JsonlSink
  writes canonical StreamEvent JSONL through ProcessedRepository

StdoutSink
  waits according to TimerPolicy, then prints event.text.monitor

TextLogSink
  optional readable .md/.sm log projection

FanoutSink
  writes one event to multiple sinks
```

Rules:

- Stdout is one sink, not the product.
- JSONL is canonical v1 processed output.
- Sinks may serialize, print, filter, prefix, or delay.
- Sinks may not infer event meaning.

## Protocol Adapters

Adapters call services/sinks. They do not own processing.

### CLI Query Adapter

```text
fbw-query dynamic command -> MatchReadService -> terminal rendering
fbw-query static command  -> TournamentReferenceService -> terminal rendering
```

Allowed:

- argument parsing
- output formatting for terminal
- service filters
- static tournament lookups for groups/squads/player/venue/schedule references via `TournamentReferenceService`

Forbidden:

- raw FIFA parsing for normal live match truth
- state-machine mutation
- duplicated goal/substitution inference

### Monitor / Watch Adapter

```text
fbw-watch or fbw-stream --stdout -> StreamProcessor/FanoutSink -> StdoutSink
```

Allowed:

- delay/replay options
- compatibility command names
- stdout rendering
- parallel mode filtering/prefixing as sink/view behavior

Forbidden:

- direct print calls from processing internals
- separate event truth from JSONL stream truth

### HTTP Ape UI Adapter

```text
HTTP request -> MatchReadService/TournamentReferenceService -> JSON/HTML response
```

Initial endpoints can be boring polling endpoints:

```text
GET /api/live
GET /api/matches/{match_id}/state
GET /api/matches/{match_id}/events?since_seq=N
GET /api/reference/teams/{code}
GET /api/reference/groups/{letter}
```

Allowed:

- request parsing
- response serialization
- static HTML/CSS/JS
- polling with `since_seq`
- reference-data views through `TournamentReferenceService`

Forbidden:

- processing raw data on request
- mutating processed stream/state on request
- reimplementing query logic inside routes

### Future MCP/SSE/WebSocket Adapters

Future protocol adapters should wrap the same read/sink interfaces.

No future adapter gets special authority over football truth.

## Source Patch, Derived Feature, and Localisation Boundary

Source patches describe source evidence changes.

```text
old source event + new source event -> SourcePatch
```

Reference data provides static identity/context.

```text
worldcup.json + fbw static augmentation -> TournamentReferenceService
```

Derived features create semantic football-wire atoms.

```text
StateOutput + MatchState + TournamentReferenceService + FeatureConfig -> DerivedFeature[]
```

Localisation/display renders features.

```text
features + locale strings/templates -> text
```

Rules:

- Teams, groups, squads, venues, scheduled matches, and team lore are reference data.
- Late FIFA coordinates, player IDs, and descriptions are source patches.
- Free-kick danger, shot zone, gate placement, near/far post, pitch location, and venue/player-context labels are derived features.
- String templates and markers are display/localisation decisions.
- Locale files do not recompute geometry.

## First Code Slice Acceptance

The next implementation slice should produce this end-to-end behavior:

```text
raw match events processed by existing SM engine
  -> stdout Monitor feed remains useful
  -> same records are written to data/processed/streams/{match_id}.jsonl
```

Minimum tests/checks:

- A fixture or existing match event can be processed into a `StreamEvent`.
- JSONL sink writes valid JSON lines with monotonic `seq`.
- Stdout sink uses the same `StreamEvent.text.monitor` content.
- Canonical `StreamEvent` does not require `emit_after`.
- No server/UI implementation required yet.
