# Changelog

## 0.2.0 (2026-06-21)

First public release. Complete rebuild from the wc26 companion PoC.

### Architecture
- State machine (`MatchStateMachine`) as central source of truth for all match data
- Source-agnostic input via adapters (FIFA, ESPN, clipboard)
- Trust hierarchy: ape > match_data > ESPN > vision > event
- Score verification: cross-checks event-derived score against canonical match data, voids phantom goals
- Play direction inference from shot coordinates, swaps at half boundaries
- Cycle-based feed engine with anti-spoiler delay and enrichment buffering

### Data Processing
- Sub direction resolved from on-pitch roster tracking (not API descriptions)
- Team attribution from roster-based player lookup
- Content-based event deduplication
- Shot enrichment: distance, pitch zone, attacker-relative side, goal placement
- Own goal detection (API event type 34)
- Late arrival detection and marking
- Foul/offside position enrichment with neutral zone descriptions

### Consumers
- LM feed client with Monitor integration (stdout events, file-based context)
- Human terminal client (schedule, match detail, live event tail)
- Query tool (groups, squads, match detail, player search, scorers)

### Data Sources
- FIFA public API (live match data, timelines, schedules)
- ESPN scoreboard endpoint (optional — live possession and stats)
- Clipboard stats parser for manual data input

### Configuration
- Layered TOML config (defaults, project, local override, env vars, CLI)
- Tournament abstraction (configurable tournament data paths)
- Venv path in config (not hardcoded)
- Shell wrappers with config-based venv resolution

### Static Data (WC2026)
- 48 team profiles with tournament observations
- 48 team jersey schematics from Wikimedia
- 16 stadiums with capacity, altitude, coordinates
- Tournament lore layer (team narratives, group analysis, match reports)
- Localised display strings (en, de-at)

### Testing
- 89 tests covering state machine, sport invariants, coordinate normalization,
  API failure modes, play direction inference
- 12 API failure fixtures from real match data

### Documentation
- Reverse-engineered FIFA API reference (20 event types documented)
- ESPN endpoint documentation
- Usage guides for humans and LM companions
- Example local config

## 0.1.0 (2026-06-17)

Internal PoC (wc26 companion). Daemon/client architecture built live during
England 4-2 Croatia. 25 commits across 6 matches. Deprecated in favour of
football-wire rebuild.
