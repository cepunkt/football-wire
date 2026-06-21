# Coordinate System and Perspective Conventions
> How football-wire describes positions, directions, and placements

## Pitch Coordinates (API)

The FIFA API uses a fixed camera-perspective coordinate system:

```
        X=0 (left)                         X=100 (right)
         ←————————————————————————————————→
         
    Y=0  ┌────────────────┬────────────────┐  near side (camera)
         │     GOAL       │       GOAL     │
         │                │                │
   Y=50  │ · · · · · · · ·│· · · · · · · · │  centre line
         │                │                │
         │     GOAL       │       GOAL     │
  Y=100  └────────────────┴────────────────┘  far side
         
              X=50 (halfway line)
```

- Both axes run 0-100 (not metres)
- X axis: 105m pitch length → 1 X unit = 1.05m
- Y axis: 68m pitch width → 1 Y unit = 0.68m
- Coordinates are fixed — they don't change between halves
- Teams swap ends at half-time but the coordinate system stays the same

## Play Direction

Teams attack toward one end of the X axis per half:

- **HIGH_X**: team attacks toward X=100
- **LOW_X**: team attacks toward X=0

Inferred from the first two directional events (shots, corners, offsides).
Swaps at every half boundary (2H, ET1, ET2). Self-corrects if 3+
events contradict after commitment.

## Broadcast Camera

The typical broadcast camera sits at the halfway line on the near side
(Y=0). It pivots to follow play:

- Attack going toward X=100: camera shows attackers from behind
- Attack going toward X=0: camera pivots, still shows attackers from behind

The broadcast always follows the attacking team, so the viewer
naturally sees from the attacker's perspective regardless of direction.

## Attacker Perspective Convention

All descriptions use the **attacker's perspective** — the player
performing the action, facing the goal they're attacking:

### Shot position (pitch)
- **Left**: attacker's left side of the pitch
- **Right**: attacker's right side
- **Central**: in line with the goal

### Goal placement (gate)
- **Left**: attacker's left of the goal frame
- **Right**: attacker's right of the goal frame
- **Low/mid-height/high**: ground to crossbar

Gate coordinates are 0-100 across the goal mouth (post to post,
7.32m) and 0-100 from ground to crossbar (2.44m). They appear to
be attacker-relative in the API — no flipping needed between halves.

Physical dimensions are calculated:
- Offset from centre: `abs(X - 50) / 100 * 7.32m`
- Height: `Y / 100 * 2.44m`

### Foul/offside position
When play direction is known:
- **In own half / in opponent's half**: relative to the fouling/offside team
- **Near own box / midfield / near opponent's box**: proximity to goals
- **Left / right / central**: attacker-relative side

When direction is unknown, neutral descriptions are used:
- **Midfield / near penalty area / between midfield and box**
- **Left / right / central**: raw Y-based, camera perspective

### Corner position
- **Near side**: closer to the camera (low Y)
- **Far side**: away from the camera (high Y)

### Free kick danger (from foul)
Distance measured from the free kick position to the receiving
team's attacking goal:
- **DANGEROUS** (≤20m, central): shooting range
- **Promising** (≤20m wide, or ≤30m central): crossing or edge of range
- **Midfield** (≤45m): build-up territory
- **Deep** (>45m): no immediate threat

### Saves
Keeper perspective is inverted from attacker perspective:
- Keeper's left = attacker's right
- Currently displayed from the attacker's perspective (matching shot data)

## Why Attacker Perspective

Football has no consistent spatial language. "Right side" changes
meaning depending on whether you're the attacker, the keeper, the
referee, or the TV viewer.

We chose attacker perspective because:
1. The broadcast camera follows attackers — on screen, attacker's
   right IS screen right
2. Pundits predominantly use attacker perspective for shot descriptions
3. The API gate coordinates appear to be attacker-relative already
4. Shot analysis ("he placed it to the right") universally means
   the shooter's right

This convention is consistent across the entire codebase:
`ShotPosition`, `GoalPlacement`, `format_pitch_position`, and
`format_shot` all use attacker-relative descriptions.
