# WC2026 Lore

Narrative context for football-wire match companions.
Written by Quinn (detective archetype) and enriched during sessions.

## Structure

```
teams/       Team narratives: history, style, key players, storylines
  GER.md     One file per team, keyed by FIFA code
  CIV.md
  ...

groups/      Group narratives: dynamics, key matchups, stakes
  E.md       One file per group letter
  J.md
  ...

matches/     Pre-match and post-match context
  GER-CIV.md   Keyed by home-away FIFA codes
  AUT-ARG.md
  ...

players/     Featured player profiles (not every player — notable ones)
  musiala.md
  messi.md
  ...

tournament.md   Overall tournament narrative, themes, storylines
```

## Purpose

These files are positioning material for the LM companion during
match watch sessions. The feed startup points to relevant lore files,
the model reads them on demand for context.

Lore is narrative and editorial. Factual data (height, caps, stats)
lives in wc2026-enrichment/. Lore references canonical IDs from
worldcup.json but adds the human story.

## Writing Guidelines

- Reference teams by FIFA code (GER, CIV, AUT) for machine readability
- Reference players by name + shirt number for cross-reference
- Mark speculation and predictions clearly
- Update after matches with results and observed narratives
- Pre-match files can be written before the match happens
- Post-match notes appended to match files after the game
