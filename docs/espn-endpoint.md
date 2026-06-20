# ESPN Live Stats Endpoint
> Discovered: 2026-06-20, during TUR-PAR match
> Status: verified working, unauthenticated

## Endpoint

```
https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard
```

No auth required. No API key. Browser-like User-Agent recommended.

## Available Stats Per Team

```
possessionPct    ← THE stat we couldn't get from FIFA API
totalShots
shotsOnTarget
shotAssists      ← key passes / chance creation
wonCorners
foulsCommitted
goalAssists
totalGoals
```

## Usage Pattern

- One GET request returns ALL current/recent matches
- Stats update live during matches
- Post-match data persists
- Rate limit: unknown, be respectful (1 req/min is plenty)

## Integration Plan

- Daemon polls alongside FIFA API (lower frequency, every 30-60s)
- Stats block uses ESPN data when available, falls back to computed
- Query tool pulls ESPN stats for match summaries
- Store in aggregate/ (third-party data, gitignored)

## Data Hierarchy

```
ESPN stats     ← primary (tracking system, automated, accurate)
Our computed   ← fallback (event counts from FIFA timeline)
FIFA events    ← narrative (individual events, shot details, coordinates)
```

## Mapping

ESPN uses its own match IDs. Need to map FIFA match IDs to ESPN event IDs
by matching team names + date. The scoreboard endpoint returns team
abbreviations (TLA) that match FIFA codes.

## Terms

Public backend endpoint powering ESPN's own website. No published terms
of service. Non-commercial, low-frequency use. Don't abuse it.
