# football-wire

Live football match data pipeline. Bridges public API data into structured event feeds for LLM companions, terminal clients, and multi-agent watch parties.

## What It Does

Polls live match data from public APIs, processes unreliable event streams into trustworthy feeds, and delivers them to consumers. The API is the source. The wire is the pipeline. What listens on the other end is up to you.

```
API (untrusted) → fetch → validate → transform → store → present
                  raw/                 processed/         clients
```

## Architecture

```
data source (FIFA API, others)
    ↓
fetcher          writes to data/raw/ (untouched API responses)
    ↓
processor        reads raw, writes to data/processed/ (cleaned, sorted, deduped)
    ↓
clients          read from processed/, present to human or LLM
  ├── terminal client (human)
  ├── monitor client (LLM via stdout → Monitor notifications)
  └── query tool (one-shot lookups)
```

### Data Layers

| Layer | Path | Git | Purpose |
|-------|------|-----|---------|
| Static | `data/static/` | tracked | Team profiles, venues, preamble. Maintained by hand. |
| Raw | `data/raw/` | ignored | Untouched API responses. Evidence of what the source sent. |
| Processed | `data/processed/` | ignored | Cleaned, sorted, deduped, enriched. What clients consume. |
| Fixtures | `fixtures/` | tracked | Saved API failures for regression testing. |

### Data Quality

The FIFA public API is the only free live data source for the World Cup. It has known issues:

- **Event ordering:** dual pipeline delivers system events on time, manual play events minutes late.
- **Team attribution:** event descriptions sometimes name the wrong team.
- **Substitution direction:** ON/OFF markers in descriptions are inconsistent across matches.
- **Score fields:** can be stale or inconsistent across events at the same minute.
- **Duplicate events:** same event resent with different IDs.

The processing layer handles all of these. Raw data is preserved unchanged for debugging.

## Data Sources

Defined in config. Each source has a trust level that determines validation strictness.

```json
{
  "sources": {
    "fifa": {
      "type": "poll",
      "trust": "low"
    }
  }
}
```

## Status

Parallel rebuild of the [WC26 companion PoC](../tools/ws-tools/wc26/). The PoC is in active use during the 2026 World Cup. This rebuild targets proper data model, testing, and package structure while maintaining feature parity.

## Requirements

- Python 3.11+
- `requests` (API polling)
- `watchdog` (file-system event monitoring for clients)

## License

MIT
