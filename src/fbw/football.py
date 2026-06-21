"""
The sport of association football.

Domain invariants, match state machine, and validation rules.
This module knows football — not APIs, not data pipelines, not
presentation. Nothing here imports from other fbw modules.

The sport doesn't change. Tournament-specific rules (number of subs,
group stage format, etc.) live in tournament config modules.
"""

from dataclasses import dataclass
from enum import Enum


# --- Pitch ---

PITCH_LENGTH_M = 105
PITCH_WIDTH_M = 68
PENALTY_AREA_DEPTH_M = 16.5
PENALTY_AREA_WIDTH_M = 40.3
SIX_YARD_BOX_DEPTH_M = 5.5
SIX_YARD_BOX_WIDTH_M = 18.3
PENALTY_SPOT_M = 11.0
CENTRE_CIRCLE_RADIUS_M = 9.15
GOAL_WIDTH_M = 7.32
GOAL_HEIGHT_M = 2.44


# --- Players ---

PLAYERS_PER_SIDE = 11
MIN_PLAYERS_PER_SIDE = 7  # match abandoned if a team drops below 7


# --- Match structure ---

HALF_LENGTH_MIN = 45
EXTRA_TIME_HALF_LENGTH_MIN = 15


# --- Match state ---

class MatchPhase(Enum):
    """Where the match is in its lifecycle.

    These are football states, not API event types. A match always
    progresses through a subset of these in order. Some states are
    only reachable in knockout matches (extra time, penalties).
    """
    SCHEDULED = "scheduled"        # not yet started
    PRE_MATCH = "pre_match"        # teams on pitch, anthem, coin toss
    FIRST_HALF = "1H"
    HALF_TIME = "HT"
    SECOND_HALF = "2H"
    # group stage ends here if not interrupted
    EXTRA_TIME_BREAK = "ET_break"
    EXTRA_FIRST = "ET1"
    EXTRA_HALF_TIME = "ET_HT"
    EXTRA_SECOND = "ET2"
    PENALTIES = "PEN"
    FULL_TIME = "FT"
    # non-standard states
    INTERRUPTED = "interrupted"    # play stopped, may resume
    ABANDONED = "abandoned"        # match will not be completed
    SUSPENDED = "suspended"        # stopped, to be resumed later


# Which phases have the clock running (ball may be in play)
PLAYING_PHASES = frozenset({
    MatchPhase.FIRST_HALF,
    MatchPhase.SECOND_HALF,
    MatchPhase.EXTRA_FIRST,
    MatchPhase.EXTRA_SECOND,
})


# --- Play direction ---

class AttackEnd(Enum):
    """Which end of the pitch a team attacks toward.

    The API coordinate system is fixed (camera perspective).
    Teams swap ends at every half boundary — this is a sport
    invariant, not a data convention.

    HIGH_X = attacking toward X=100 (right side of camera)
    LOW_X  = attacking toward X=0   (left side of camera)
    """
    HIGH_X = "high_x"
    LOW_X = "low_x"

    @property
    def opposite(self) -> "AttackEnd":
        return AttackEnd.LOW_X if self == AttackEnd.HIGH_X else AttackEnd.HIGH_X


# Phases where teams swap ends on entry.
# Every playing phase after 1H involves a swap from the previous:
#   1H (initial) → swap → 2H → swap → ET1 → swap → ET2
# Result: 1H and ET1 share direction, 2H and ET2 share direction.
DIRECTION_SWAP_PHASES = frozenset({
    MatchPhase.SECOND_HALF,
    MatchPhase.EXTRA_FIRST,
    MatchPhase.EXTRA_SECOND,
})

# Which phases are breaks (clock stopped, no play)
BREAK_PHASES = frozenset({
    MatchPhase.HALF_TIME,
    MatchPhase.EXTRA_TIME_BREAK,
    MatchPhase.EXTRA_HALF_TIME,
})

# Valid phase transitions in football
# Each phase maps to the set of phases it can transition to.
# INTERRUPTED can be reached from any PLAYING phase and returns to one.
PHASE_TRANSITIONS: dict[MatchPhase, frozenset[MatchPhase]] = {
    MatchPhase.SCHEDULED: frozenset({
        MatchPhase.PRE_MATCH,
        MatchPhase.FIRST_HALF,     # FIFA API skips PRE_MATCH, goes straight to kickoff
        MatchPhase.ABANDONED,
    }),
    MatchPhase.PRE_MATCH: frozenset({
        MatchPhase.FIRST_HALF,
        MatchPhase.ABANDONED,
    }),
    MatchPhase.FIRST_HALF: frozenset({
        MatchPhase.HALF_TIME,
        MatchPhase.INTERRUPTED,
    }),
    MatchPhase.HALF_TIME: frozenset({
        MatchPhase.SECOND_HALF,
    }),
    MatchPhase.SECOND_HALF: frozenset({
        MatchPhase.FULL_TIME,          # group stage or decisive result
        MatchPhase.EXTRA_TIME_BREAK,   # knockout, drawn
        MatchPhase.INTERRUPTED,
    }),
    MatchPhase.EXTRA_TIME_BREAK: frozenset({
        MatchPhase.EXTRA_FIRST,
    }),
    MatchPhase.EXTRA_FIRST: frozenset({
        MatchPhase.EXTRA_HALF_TIME,
        MatchPhase.INTERRUPTED,
    }),
    MatchPhase.EXTRA_HALF_TIME: frozenset({
        MatchPhase.EXTRA_SECOND,
    }),
    MatchPhase.EXTRA_SECOND: frozenset({
        MatchPhase.FULL_TIME,          # decisive result in ET
        MatchPhase.PENALTIES,          # still drawn
        MatchPhase.INTERRUPTED,
    }),
    MatchPhase.PENALTIES: frozenset({
        MatchPhase.FULL_TIME,
        MatchPhase.INTERRUPTED,
    }),
    MatchPhase.FULL_TIME: frozenset(),  # terminal
    MatchPhase.INTERRUPTED: frozenset({
        # can resume to any playing phase or break, or be abandoned
        MatchPhase.FIRST_HALF,
        MatchPhase.SECOND_HALF,
        MatchPhase.EXTRA_FIRST,
        MatchPhase.EXTRA_SECOND,
        MatchPhase.PENALTIES,
        MatchPhase.HALF_TIME,
        MatchPhase.EXTRA_TIME_BREAK,
        MatchPhase.EXTRA_HALF_TIME,
        MatchPhase.ABANDONED,
        MatchPhase.SUSPENDED,
    }),
    MatchPhase.ABANDONED: frozenset(),   # terminal
    MatchPhase.SUSPENDED: frozenset({
        # can resume or be abandoned
        MatchPhase.FIRST_HALF,
        MatchPhase.SECOND_HALF,
        MatchPhase.EXTRA_FIRST,
        MatchPhase.EXTRA_SECOND,
        MatchPhase.PENALTIES,
        MatchPhase.ABANDONED,
    }),
}


# Regular minute ranges for each playing phase (no added time)
# These are the scheduled minutes. Added time extends beyond but
# still belongs to the same phase.
#
# Football notation:
#   1' = "in the 1st minute" (clock 0:00-0:59)
#  45' = last regular minute of first half
#  45'+1' = 1st minute of first half added time (still FIRST_HALF)
#  46' = 1st minute of second half (clock 45:00-45:59)
#  90' = last regular minute of second half
#  90'+3' = 3rd minute of second half added time (still SECOND_HALF)
#  91' = 1st minute of extra time first half
PHASE_REGULAR_MINUTES: dict[MatchPhase, tuple[int, int]] = {
    MatchPhase.FIRST_HALF:  (1, 45),
    MatchPhase.SECOND_HALF: (46, 90),
    MatchPhase.EXTRA_FIRST: (91, 105),
    MatchPhase.EXTRA_SECOND: (106, 120),
}

# The base minute that added time extends from, per phase
# 45'+X' belongs to FIRST_HALF, 90'+X' to SECOND_HALF, etc.
PHASE_ADDED_TIME_BASE: dict[int, MatchPhase] = {
    45: MatchPhase.FIRST_HALF,
    90: MatchPhase.SECOND_HALF,
    105: MatchPhase.EXTRA_FIRST,
    120: MatchPhase.EXTRA_SECOND,
}


# --- Match minute ---

@dataclass(frozen=True)
class MatchMinute:
    """A minute in football, with phase awareness.

    Football minutes have structure that a single float loses:
    - 45'+3' is first half added time (base=45, added=3, phase=1H)
    - 48' is the 3rd minute of the second half (base=48, added=0, phase=2H)
    Both would be 48.0 as a flat float. They are not the same.

    sort_value preserves correct ordering: added time sorts after its
    base minute but before the next phase's first minute. This uses
    fractional encoding: 45'+3' → 45.03, so 45.03 < 46.0.
    """
    base: int          # the regular minute (45 in "45'+3'", 48 in "48'")
    added: int         # added time minutes (3 in "45'+3'", 0 in "48'")
    raw: str           # original notation

    @property
    def sort_value(self) -> float:
        """Sortable value that preserves phase boundaries.

        Added time uses fractional encoding:
          45'+3'  → 45.03   (first half added time)
          45'+12' → 45.12   (still < 46.0, still first half)
          46'     → 46.0    (second half)
          90'+5'  → 90.05   (second half added time)
          91'     → 91.0    (extra time)
        """
        if self.added > 0:
            return self.base + self.added / 100.0
        return float(self.base)

    @property
    def phase(self) -> MatchPhase | None:
        """Which phase this minute belongs to.

        Returns None if the minute can't be mapped (e.g. pre-match).
        """
        # Added time: phase determined by the base minute
        if self.added > 0:
            return PHASE_ADDED_TIME_BASE.get(self.base)

        # Regular time: find which phase contains this minute
        for mp, (lo, hi) in PHASE_REGULAR_MINUTES.items():
            if lo <= self.base <= hi:
                return mp
        return None

    @property
    def is_added_time(self) -> bool:
        return self.added > 0

    @property
    def phase_prefix(self) -> str:
        """Short phase marker for feed display.

        Returns: "1H", "2H", "ET1", "ET2", "PEN", "" if unknown.
        Penalties are detected by phase on the MatchClock, not
        from the minute itself — penalty events may carry the
        last regular minute (120'+X') or no minute at all.
        """
        p = self.phase
        if p is None:
            return ""
        return {
            MatchPhase.FIRST_HALF: "1H",
            MatchPhase.SECOND_HALF: "2H",
            MatchPhase.EXTRA_FIRST: "ET1",
            MatchPhase.EXTRA_SECOND: "ET2",
        }.get(p, "")

    @property
    def display(self) -> str:
        """Football notation: 45'+3' or 48'."""
        if self.added > 0:
            return f"{self.base}'+{self.added}'"
        return f"{self.base}'"

    @property
    def display_with_phase(self) -> str:
        """Phase-prefixed notation: 1H 45'+3' or 2H 68'.

        The phase prefix is fixed-width (3 chars + space) for
        alignment in feed output. Penalties use PEN.
        """
        prefix = self.phase_prefix
        if prefix:
            return f"{prefix:>3} {self.display}"
        return f"    {self.display}"

    def __lt__(self, other: "MatchMinute") -> bool:
        return self.sort_value < other.sort_value

    def __le__(self, other: "MatchMinute") -> bool:
        return self.sort_value <= other.sort_value

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, MatchMinute):
            return NotImplemented
        return self.base == other.base and self.added == other.added

    def __hash__(self) -> int:
        return hash((self.base, self.added))

    def __repr__(self) -> str:
        return f"MatchMinute({self.display})"

    @classmethod
    def from_notation(cls, raw: str, description: str = "") -> "MatchMinute":
        """Parse football minute notation.

        Handles: "45'+3'", "90'+1'", "48'", "3'", ""
        Uses event description for context when minute is empty
        (e.g. half-time subs say "before the second half").
        """
        if not raw or not raw.strip():
            # Use description context for empty minutes
            desc_lower = description.lower() if description else ""
            if "second half" in desc_lower:
                return cls(base=45, added=0, raw=raw or "")
            if "extra time" in desc_lower:
                return cls(base=90, added=0, raw=raw or "")
            return cls(base=0, added=0, raw=raw or "")

        s = raw.replace("'", "").strip()
        if "+" in s:
            parts = s.split("+", 1)
            try:
                return cls(
                    base=int(parts[0]),
                    added=int(parts[1]),
                    raw=raw,
                )
            except (ValueError, IndexError):
                pass
        try:
            return cls(base=int(s), added=0, raw=raw)
        except ValueError:
            return cls(base=0, added=0, raw=raw)


# --- Match clock ---

@dataclass
class MatchClock:
    """Tracks where we are in a match.

    The clock knows the current phase and minute. It validates
    transitions against football rules and tracks added time.
    """
    phase: MatchPhase = MatchPhase.SCHEDULED
    minute: MatchMinute = None  # type: ignore[assignment]
    period_count: int = 0  # how many playing periods have started

    def __post_init__(self):
        if self.minute is None:
            self.minute = MatchMinute(base=0, added=0, raw="")

    def can_transition_to(self, next_phase: MatchPhase) -> bool:
        """Check if a phase transition is valid in football."""
        allowed = PHASE_TRANSITIONS.get(self.phase, frozenset())
        return next_phase in allowed

    def transition(self, next_phase: MatchPhase) -> bool:
        """Attempt a phase transition. Returns True if valid.

        Does not force — caller decides what to do with invalid
        transitions (flag as suspect, log, reject).
        """
        if not self.can_transition_to(next_phase):
            return False
        self.phase = next_phase
        if next_phase in PLAYING_PHASES:
            self.period_count += 1
        return True

    def minute_valid_for_phase(self, minute: MatchMinute) -> bool:
        """Check if a match minute makes sense for the current phase."""
        minute_phase = minute.phase
        if minute_phase is None:
            return True  # can't determine, don't reject
        return minute_phase == self.phase

    @property
    def is_playing(self) -> bool:
        return self.phase in PLAYING_PHASES

    @property
    def is_break(self) -> bool:
        return self.phase in BREAK_PHASES

    @property
    def is_terminal(self) -> bool:
        return self.phase in (MatchPhase.FULL_TIME, MatchPhase.ABANDONED)


# --- Validation rules ---

def validate_sub_count(subs_made: int, max_subs: int) -> bool:
    """Check if another substitution is allowed."""
    return subs_made < max_subs


def validate_player_count(on_pitch: int) -> bool:
    """Check if a team has enough players to continue."""
    return on_pitch >= MIN_PLAYERS_PER_SIDE


def validate_score_monotonic(
    prev_home: int, prev_away: int,
    curr_home: int, curr_away: int,
) -> bool:
    """Scores can only increase during a match.

    A decrease means a goal was voided — valid in football (VAR),
    but the event stream should explain it. A silent decrease is
    suspect data.
    """
    return curr_home >= prev_home and curr_away >= prev_away


def validate_player_on_pitch(player_id: str, on_pitch: set[str]) -> bool:
    """Check if a player is currently on the pitch."""
    return player_id in on_pitch


def validate_player_not_already_subbed_off(
    player_id: str, subbed_off: set[str],
) -> bool:
    """Once subbed off, a player cannot return. Football invariant."""
    return player_id not in subbed_off
