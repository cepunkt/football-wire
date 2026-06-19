--- Data Notes ---
Team tags: three-letter code (e.g. SCO/MAR) before each player event,
  resolved from match roster. More reliable than API descriptions.
Shot confidence: shot + position + goal placement = on target (high confidence).
  Shot + position only = off target (medium). Shot only = attempt/blocked (low).
Coordinates: 0-100 scale on both axes, mapped to 105m x 68m pitch.
  Enriched with distance from goal, pitch zone, and attacker-relative side.
Data quality: the source API has known issues:
  - Event ordering: events may arrive out of sequence. !! marks late arrivals.
  - Substitutions: ON/OFF tags are resolved from roster tracking.
    The API description text may show inverted (in)/(out) markers.
  - Team names in descriptions: occasionally attributed to the wrong team.
    Trust the team tag prefix, not the description text.
  - Scores on events: may be stale or inconsistent across events at the same minute.
  When data seems contradictory, ask the user what actually happened.
---
