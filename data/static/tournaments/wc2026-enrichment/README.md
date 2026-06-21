# WC2026 Enrichment Data

Structured factual data layered on top of worldcup.json canonical data.
Keyed by FIFA team code + shirt number (same identity space).

## Files

- `players.json` — physical stats, caps, goals, market value
- `coaches.json` — coaching staff with full details
- `stadiums.json` — venue details beyond worldcup.json

## Sources

Populated from scraping, manual entry, or Quinn research sessions.
All data is factual and verifiable — not narrative or editorial.

## Identity

Team code (e.g. "GER") from worldcup.json `fifa_code`.
Player key is shirt number within team (e.g. "10" for Musiala in GER).
