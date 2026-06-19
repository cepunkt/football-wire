# Football Invariants
> Rules that are always true. Violations indicate data errors.

## Match Structure
- A match has exactly 2 teams.
- Each team has exactly 11 players on the pitch at kickoff.
- Minimum 7 players per team on pitch (below 7 = match abandoned).
- Two halves of 45 minutes + added time.
- Teams switch sides at half-time.
- Extra time: two periods of 15 minutes (knockout only).
- Penalty shootout follows extra time if still drawn (knockout only).

## Players
- A squad has a maximum of 26 registered players.
- 11 starters, rest are substitutes.
- A player on the pitch cannot be substituted on.
- A player who has been substituted off cannot return.
- Maximum 5 substitutions per team (in 3 windows + half-time).
- Shirt numbers are unique within a team.

## Cards
- Two yellow cards in one match = automatic red card.
- Red card = player sent off, team plays with one fewer.
- Yellow cards accumulate across group stage matches.
  Two yellows across matches = suspended for next match.
  Cards reset after the group stage (tournament-dependent).
- A player who is sent off cannot be replaced by a substitute.

## Score
- Score is monotonic — can only increase during a match.
- Own goals count for the opposing team's score.
- Goal count at full time must equal the sum of goal events.
- Penalty shootout goals are counted separately from match score.

## Substitutions
- A substitution involves exactly 2 players from the same team.
- One player goes off (was on pitch), one comes on (was on bench).
- Substitution windows: 3 opportunities during play + half-time.
  Multiple subs can be made in the same window.

## Coordinates
- Pitch coordinates are absolute (don't change with possession).
- Teams attack opposite ends.
- Teams switch attacking direction at half-time.
- Shots at goal cluster near X=0 or X=100 (the two goal lines).
