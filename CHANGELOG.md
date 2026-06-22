# Changelog

## 0.2.1 (2026-06-21)

Live-tested across ESP 4-0 KSA and BEL 0-0 IRN. Architecture cleanup.

### Architecture
- **Unified display module** (`display.py`) — all text formatting in one place, replaces scattered format logic across feed_sm.py
- **Legacy feed engine removed** — 727 lines of dead code. `feed.py` is now CLI + header only (288 lines, down from 1015)
- **Swappable engine architecture** — `feed.py` points at `feed_sm.py`, future engines can be developed independently

### Features
- **Direction-aware foul/offside positions** — "in own half, near own box, right" instead of neutral zones. Uses play direction tracking from state machine.
- **Direction swap announcements** — emitted at half boundaries via side_outputs
- **Keeper names on saves** — `IRN | Alireza BEIRANVAND saves.` instead of anonymous text
- **Voided goals in catchup** — `~~GOAL VOIDED~~` instead of confusing `>>GOAL<< [0-0]`
- **Corner position** — near side / far side instead of wrong shot-position format
- **Period-keyed stats ingestion** — `fbw ingest <match_id> --period 1H`, stores 1H and FT alongside
- **Enrichment corrections direction-aware** — post-emit fouls/offsides get same directional context as live events

### Fixes
- **fbw-watch uses SM engine** — was hardwired to legacy cmd_watch
- **Duplicate enrichment emissions** — PositionX and PositionY marked as pair
- **SM event deduplication** — side_outputs channel for internal announcements, no double-counting
- **Direction announcements in feed stream** — were silently swallowed inside state machine
- **Score verification on match JSON change** — watchdog monitors match data file for VAR/phantom goal detection

### Live-verified (BEL 0-0 IRN)
- Taremi disallowed goal → score verification voided phantom goal → catchup shows ~~GOAL VOIDED~~ [0-0]
- Jahanbakhsh foul at (6,7) → "in own half, near own box" — first live directional foul
- Beiranvand saves → keeper name on every save event
- Direction swap at half-time → Belgium low X, Iran high X confirmed by shot coordinates
- No duplicate enrichment lines throughout second half

## 0.2.0 (2026-06-21)

First public release. Complete rebuild from the wc26 buddy PoC.

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
- Usage guides for humans and LM buddys
- Example local config

## 0.1.0 (2026-06-17)

Internal PoC (wc26 buddy). Daemon/client architecture built live during
England 4-2 Croatia. 25 commits across 6 matches. Deprecated in favour of
football-wire rebuild.
