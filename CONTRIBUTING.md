# Contributing

football-wire is built at the Clockwork Taming Workshop. Contributions are welcome.

## Getting Started

```bash
git clone https://github.com/cepunkt/football-wire.git
cd football-wire
pip install -e ".[dev]"
```

Or without installing:
```bash
pip install requests watchdog pyyaml
export PYTHONPATH=src
```

## Running Tests

```bash
PYTHONPATH=src python -m pytest tests/ -v
```

Tests use real API failure fixtures from actual WC2026 matches. No mocks, no network calls.

## Project Structure

- `src/fbw/` — all source code
- `tests/` — test suite
- `fixtures/` — real API failure data for regression tests
- `data/static/` — tracked static data (teams, venues, tournament config)
- `data/raw/`, `data/processed/`, `data/feeds/`, `data/aggregate/` — runtime data (gitignored)
- `docs/` — usage guides and API documentation

## Code Style

- Python 3.11+ (uses `tomllib`, type unions with `|`)
- Dataclasses for data model, enums for categories
- Type hints throughout
- No external formatting tools enforced — just be consistent

## Architecture Principles

- **State machine is the source of truth.** All match data flows through `MatchStateMachine.apply()`. Don't bypass it.
- **Raw data is sacred.** Never modify files in `data/raw/`. Processing creates new files.
- **Trust levels are explicit.** When data quality is uncertain, mark it with `Trust.SUSPECT`, don't silently fix it.
- **Sport invariants live in `football.py`.** Tournament-specific rules live in TOML config. Don't mix them.
- **Adapters isolate source mess.** The state machine never sees raw API formats.

## Adding a New Data Source

1. Write an adapter in `adapters.py` that converts source data to `StateInput`
2. Add source config to `config.py`
3. Add polling to `daemon.py` (or a new ingest path)
4. Add tests with real data fixtures

## Adding a New Event Type

1. Add to `EventType` enum in `model.py`
2. Map to `InputCategory` in `adapters.py`
3. Handle in the appropriate `_apply_*` method in `state.py`
4. Format in `feed_sm.py` (and `format.py` for legacy)
5. Add test with fixture data
6. Document in `docs/api-fifa.md`

## Bug Reports

If you see wrong data in the feed, the most useful report includes:
- Match ID and minute
- What the feed showed vs what actually happened
- The raw event data if you can grab it from `data/raw/`

API data quality issues are documented in `docs/api-fifa.md`. If you find a new one, add it.

## License

MIT. Contributions are made under the same license.
