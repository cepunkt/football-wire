"""
Match state machine.

Central source of truth for a match at any point in time.
All data flows through apply() — nothing reaches the feed or
query layer without passing through the state machine.

Source-agnostic: receives StateInput, not raw API data. Adapters
convert source-specific formats into StateInput before it arrives.

Validates against football.py (sport invariants) and TournamentRules
(competition-specific rules).
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from fbw.football import (
    AttackEnd,
    DIRECTION_SWAP_PHASES,
    MatchClock,
    MatchMinute,
    MatchPhase,
    PLAYING_PHASES,
    validate_player_count,
    validate_player_not_already_subbed_off,
    validate_player_on_pitch,
    validate_score_monotonic,
    validate_sub_count,
)
from fbw.tournament import TournamentRules


# --- Input categories ---

class InputCategory(Enum):
    """What kind of thing happened, regardless of source."""
    PERIOD_CHANGE = "period"         # kickoff, half-time, period end
    SCORE_CHANGE = "score"           # goal, own goal, penalty scored
    SCORE_VERIFY = "score_verify"    # canonical score cross-check (match data, ESPN)
    SCORE_VOID = "score_void"        # disallowed goal (VAR, offside, etc.)
    PLAYER_CHANGE = "player"         # substitution
    DISCIPLINE = "discipline"        # yellow, red, foul
    SET_PIECE = "set_piece"          # corner, free kick, penalty awarded
    ATTEMPT = "attempt"              # shot, save, blocked
    MATCH_INTERRUPT = "interrupt"    # pause, delay, hydration break, resume
    STATS_SNAPSHOT = "stats"         # possession, passes, shots (ESPN etc.)
    ANNOTATION = "annotation"        # ape commentary, vision description


# --- Trust ---
# Two trust concepts in the codebase:
#   model.Trust (data quality): TRUSTED/INFERRED/SUSPECT/UNKNOWN
#     — how much to trust a specific data point's correctness
#   SourceTrust (source hierarchy): APE > MATCH_DATA > ESPN > VISION > EVENT
#     — which source wins when they disagree

class SourceTrust(Enum):
    """Which source to trust when sources disagree. Higher rank wins."""
    APE = "ape"              # human observer — highest
    MATCH_DATA = "match"     # canonical match endpoint
    ESPN = "espn"            # independent secondary source
    VISION = "vision"        # automated frame analysis
    EVENT = "event"          # raw event stream — lowest
    UNKNOWN = "unknown"

    @property
    def rank(self) -> int:
        return {
            SourceTrust.APE: 50,
            SourceTrust.MATCH_DATA: 40,
            SourceTrust.ESPN: 30,
            SourceTrust.VISION: 20,
            SourceTrust.EVENT: 10,
            SourceTrust.UNKNOWN: 0,
        }[self]

    def __gt__(self, other: "SourceTrust") -> bool:
        return self.rank > other.rank

    def __ge__(self, other: "SourceTrust") -> bool:
        return self.rank >= other.rank


# --- State input ---

@dataclass(frozen=True)
class StateInput:
    """One piece of information entering the state machine.

    Source-agnostic. An adapter has already converted the raw
    API/ESPN/clipboard/vision data into this form.
    """
    category: InputCategory
    minute: MatchMinute
    data: dict[str, Any]       # category-specific payload
    source: str                # "fifa", "espn", "ape", "vision"
    trust: SourceTrust
    timestamp: datetime        # wall clock when received/detected
    source_id: str = ""        # original event ID for dedup


# --- State machine output ---

class OutputKind(Enum):
    """What the state machine decided to emit."""
    EVENT = "event"            # new valid event
    CORRECTION = "correction"  # rectification of previous state
    STATS = "stats"            # stats snapshot update
    ANNOTATION = "annotation"  # commentary attached to a minute
    NOTHING = "nothing"        # absorbed, duplicate, or invalid


@dataclass
class StateOutput:
    """What apply() returns to the consumer (feed or query)."""
    kind: OutputKind
    minute: MatchMinute
    data: dict[str, Any]       # kind-specific payload
    trust: SourceTrust
    flags: list[str] = field(default_factory=list)  # warnings, suspect markers


# --- Core state ---

@dataclass
class TeamState:
    """Per-team state tracking."""
    team_id: str
    abbreviation: str
    on_pitch: set[str] = field(default_factory=set)     # player IDs
    subbed_off: set[str] = field(default_factory=set)    # can't return
    subs_made: int = 0
    sub_windows_used: int = 0
    yellows: dict[str, int] = field(default_factory=dict)  # player_id → count
    reds: set[str] = field(default_factory=set)             # player IDs
    score: int = 0


@dataclass
class ScoreEvent:
    """A recorded goal for tracking and potential voiding."""
    minute: MatchMinute
    player_id: str
    team_id: str
    trust: SourceTrust
    voided: bool = False
    void_reason: str = ""
    shot_data: dict[str, Any] | None = None  # merged shot coordinates
    assist_player_id: str = ""


@dataclass
class DirectionEvidence:
    """One observation supporting a team's attack direction."""
    team_id: str
    raw_x: float
    event_type: str       # "shot", "offside"
    minute: MatchMinute

    @property
    def inferred_end(self) -> AttackEnd:
        """Which end this evidence suggests the team attacks toward."""
        return AttackEnd.HIGH_X if self.raw_x > 50.0 else AttackEnd.LOW_X


@dataclass
class PlayDirection:
    """Resolved attack directions for both teams in the current half.

    Once committed, this is a match invariant that swaps at half
    boundaries. The confidence is the number of agreeing observations
    that produced this commitment.
    """
    home_end: AttackEnd
    away_end: AttackEnd
    confidence: int           # number of agreeing observations
    determined_at: MatchMinute
    phase_determined: MatchPhase

    def for_team(self, team_id: str, home_team_id: str) -> AttackEnd:
        """Get the attack end for a specific team."""
        return self.home_end if team_id == home_team_id else self.away_end

    def swapped(self) -> "PlayDirection":
        """Return a new PlayDirection with ends swapped (for half change)."""
        return PlayDirection(
            home_end=self.home_end.opposite,
            away_end=self.away_end.opposite,
            confidence=self.confidence,
            determined_at=self.determined_at,
            phase_determined=self.phase_determined,
        )

    def attacks_high_x(self, team_id: str, home_team_id: str) -> bool:
        """Whether a team attacks toward the high-X end."""
        return self.for_team(team_id, home_team_id) == AttackEnd.HIGH_X


@dataclass
class MatchState:
    """The complete state of a match at a point in time.

    This is what match_state() returns. Everything the feed or
    query layer needs to present or answer questions about.
    """
    match_id: str
    clock: MatchClock
    home: TeamState
    away: TeamState
    goals: list[ScoreEvent] = field(default_factory=list)
    events: list[StateOutput] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)  # latest stats snapshot
    annotations: list[StateOutput] = field(default_factory=list)
    direction: PlayDirection | None = None

    @property
    def score(self) -> tuple[int, int]:
        """Canonical score from non-voided goals."""
        home = sum(1 for g in self.goals
                   if g.team_id == self.home.team_id and not g.voided)
        away = sum(1 for g in self.goals
                   if g.team_id == self.away.team_id and not g.voided)
        return (home, away)


# --- State machine ---

class MatchStateMachine:
    """Central source of truth for a match.

    All data flows through apply(). The state machine validates
    inputs, updates internal state, resolves conflicts by trust
    level, and returns what to emit.

    The state machine does not know about APIs, file formats, or
    presentation. It knows football (via football.py) and tournament
    rules (via TournamentRules).
    """

    def __init__(
        self,
        match_id: str,
        home: TeamState,
        away: TeamState,
        rules: TournamentRules,
        is_knockout: bool = False,
    ):
        self.match_id = match_id
        self.clock = MatchClock()
        self.home = home
        self.away = away
        self.rules = rules
        self.is_knockout = is_knockout

        self.goals: list[ScoreEvent] = []
        self.events: list[StateOutput] = []
        self.stats: dict[str, Any] = {}
        self.annotations: list[StateOutput] = []

        # Dedup tracking
        self._seen_ids: set[str] = set()
        # Pending shot data waiting for goal merge
        self._pending_shots: dict[str, dict] = {}  # key: "minute|player_id"

        # Side-channel outputs — events generated internally (direction, etc.)
        # that aren't returned by apply(). Consumers should drain this.
        self.side_outputs: list[StateOutput] = []

        # Play direction tracking
        self.direction: PlayDirection | None = None
        self._direction_evidence: list[DirectionEvidence] = []
        self._direction_committed: bool = False

    def _team_for_id(self, team_id: str) -> TeamState | None:
        """Get team state by ID."""
        if team_id == self.home.team_id:
            return self.home
        if team_id == self.away.team_id:
            return self.away
        return None

    def _team_for_abbr(self, abbr: str) -> TeamState | None:
        """Get team state by abbreviation."""
        if abbr == self.home.abbreviation:
            return self.home
        if abbr == self.away.abbreviation:
            return self.away
        return None

    def _team_for_player(self, player_id: str) -> TeamState | None:
        """Find which team a player belongs to (on pitch or subbed off)."""
        for team in (self.home, self.away):
            if player_id in team.on_pitch or player_id in team.subbed_off:
                return team
        return None

    @property
    def score(self) -> tuple[int, int]:
        """Canonical score from non-voided goals."""
        home = sum(1 for g in self.goals
                   if g.team_id == self.home.team_id and not g.voided)
        away = sum(1 for g in self.goals
                   if g.team_id == self.away.team_id and not g.voided)
        return (home, away)

    # --- Dedup ---

    def _is_duplicate(self, inp: StateInput) -> bool:
        """Check if we've already processed this input."""
        if inp.source_id and inp.source_id in self._seen_ids:
            return True
        return False

    def _mark_seen(self, inp: StateInput) -> None:
        if inp.source_id:
            self._seen_ids.add(inp.source_id)

    def _sub_dedup_key(self, player_a: str, player_b: str, minute: MatchMinute) -> str:
        """Sub dedup: minute + sorted player IDs. Direction-agnostic."""
        pair = sorted([player_a, player_b])
        return f"sub|{minute.base}|{pair[0]}|{pair[1]}"

    # --- Play direction inference ---

    _DIRECTION_COMMIT_THRESHOLD = 2    # agreeing observations to commit
    _DIRECTION_CORRECT_THRESHOLD = 3  # contradicting observations to re-commit

    def _record_direction_evidence(
        self, team_id: str, raw_x: float, event_type: str, minute: MatchMinute,
    ) -> StateOutput | None:
        """Record a directional observation and try to commit.

        Returns a StateOutput announcing the direction if we just
        committed or corrected, None otherwise.

        Evidence sources: shots, corners, offsides, penalties.
        Keeps recording after commit for self-correction.
        """
        if not team_id:
            return None

        ev = DirectionEvidence(
            team_id=team_id, raw_x=raw_x,
            event_type=event_type, minute=minute,
        )
        self._direction_evidence.append(ev)

        if not self._direction_committed:
            return self._try_commit_direction(minute)

        # Post-commit: check for contradicting evidence
        return self._check_direction_correction(minute)

    def _try_commit_direction(self, minute: MatchMinute) -> StateOutput | None:
        """Try to determine play direction from accumulated evidence.

        Counts how many observations agree per team per end.
        Commits when any team has >= threshold observations pointing
        the same direction.
        """
        # Count: team_id → {AttackEnd → count}
        counts: dict[str, dict[AttackEnd, int]] = {}
        for ev in self._direction_evidence:
            if ev.team_id not in counts:
                counts[ev.team_id] = {AttackEnd.HIGH_X: 0, AttackEnd.LOW_X: 0}
            counts[ev.team_id][ev.inferred_end] += 1

        # Check if any team has enough agreeing evidence
        for team_id, ends in counts.items():
            for end, count in ends.items():
                if count >= self._DIRECTION_COMMIT_THRESHOLD:
                    return self._commit_direction(team_id, end, count, minute)

        return None

    def _check_direction_correction(self, minute: MatchMinute) -> StateOutput | None:
        """Check if post-commit evidence contradicts the current direction.

        If 3+ observations point the opposite way from what we committed,
        flip the direction and emit a correction.
        """
        if not self.direction:
            return None

        # Count evidence that contradicts current direction
        contradictions = 0
        for ev in self._direction_evidence:
            expected_end = self.direction.for_team(ev.team_id, self.home.team_id)
            if ev.inferred_end != expected_end:
                contradictions += 1

        if contradictions >= self._DIRECTION_CORRECT_THRESHOLD:
            # Flip direction
            self.direction = self.direction.swapped()
            # Clear evidence — start fresh from the corrected state
            self._direction_evidence.clear()

            home_abbr = self.home.abbreviation
            away_abbr = self.away.abbreviation
            home_arrow = "→ high X" if self.direction.home_end == AttackEnd.HIGH_X else "→ low X"
            away_arrow = "→ high X" if self.direction.away_end == AttackEnd.HIGH_X else "→ low X"

            return StateOutput(
                kind=OutputKind.CORRECTION,
                minute=minute,
                data={
                    "type": "direction_determined",
                    "home_end": self.direction.home_end.value,
                    "away_end": self.direction.away_end.value,
                    "confidence": contradictions,
                    "description": (
                        f"Direction CORRECTED: {home_abbr} {home_arrow}, "
                        f"{away_abbr} {away_arrow} "
                        f"({contradictions} contradicting events)"
                    ),
                },
                trust=SourceTrust.EVENT,
                flags=["direction_corrected"],
            )

        return None

    def _commit_direction(
        self, team_id: str, end: AttackEnd, confidence: int, minute: MatchMinute,
    ) -> StateOutput:
        """Lock in the play direction from observed evidence."""
        is_home = (team_id == self.home.team_id)
        home_end = end if is_home else end.opposite
        away_end = home_end.opposite

        # Account for current phase — if we're in a swap phase (2H, ET2),
        # the first-half direction is the opposite of what we observe now.
        # We store the CURRENT direction, and swap on transitions.
        self.direction = PlayDirection(
            home_end=home_end,
            away_end=away_end,
            confidence=confidence,
            determined_at=minute,
            phase_determined=self.clock.phase,
        )
        self._direction_committed = True

        home_abbr = self.home.abbreviation
        away_abbr = self.away.abbreviation
        home_arrow = "→ high X" if home_end == AttackEnd.HIGH_X else "→ low X"
        away_arrow = "→ high X" if away_end == AttackEnd.HIGH_X else "→ low X"

        return StateOutput(
            kind=OutputKind.EVENT,
            minute=minute,
            data={
                "type": "direction_determined",
                "home_end": home_end.value,
                "away_end": away_end.value,
                "confidence": confidence,
                "description": (
                    f"Play direction: {home_abbr} {home_arrow}, "
                    f"{away_abbr} {away_arrow} "
                    f"(inferred from {confidence} events)"
                ),
            },
            trust=SourceTrust.EVENT,
            flags=["direction"],
        )

    def _swap_direction(self) -> None:
        """Swap play direction at half boundary."""
        if self.direction:
            self.direction = self.direction.swapped()

    # --- Apply ---

    def apply(self, inp: StateInput) -> StateOutput:
        """Process one input through the state machine.

        This is the single entry point. Every piece of data —
        regardless of source — passes through here.

        Returns a StateOutput describing what to emit.
        """
        # Source-ID level dedup
        if self._is_duplicate(inp):
            return StateOutput(
                kind=OutputKind.NOTHING,
                minute=inp.minute,
                data={"reason": "duplicate"},
                trust=inp.trust,
            )

        handler = {
            InputCategory.PERIOD_CHANGE: self._apply_period,
            InputCategory.SCORE_CHANGE: self._apply_score,
            InputCategory.SCORE_VERIFY: self._apply_score_verify,
            InputCategory.SCORE_VOID: self._apply_score_void,
            InputCategory.PLAYER_CHANGE: self._apply_sub,
            InputCategory.DISCIPLINE: self._apply_discipline,
            InputCategory.SET_PIECE: self._apply_set_piece,
            InputCategory.ATTEMPT: self._apply_attempt,
            InputCategory.MATCH_INTERRUPT: self._apply_interrupt,
            InputCategory.STATS_SNAPSHOT: self._apply_stats,
            InputCategory.ANNOTATION: self._apply_annotation,
        }.get(inp.category)

        if handler is None:
            return StateOutput(
                kind=OutputKind.NOTHING,
                minute=inp.minute,
                data={"reason": "unknown_category"},
                trust=inp.trust,
            )

        result = handler(inp)
        self._mark_seen(inp)

        # Store emitted events
        if result.kind == OutputKind.EVENT:
            self.events.append(result)
        elif result.kind == OutputKind.ANNOTATION:
            self.annotations.append(result)

        return result

    # --- Category handlers ---

    # Map from current phase to the next playing phase on "start"
    _NEXT_PLAYING_PHASE: dict[MatchPhase, MatchPhase] = {
        MatchPhase.SCHEDULED: MatchPhase.FIRST_HALF,
        MatchPhase.PRE_MATCH: MatchPhase.FIRST_HALF,
        MatchPhase.HALF_TIME: MatchPhase.SECOND_HALF,
        MatchPhase.EXTRA_TIME_BREAK: MatchPhase.EXTRA_FIRST,
        MatchPhase.EXTRA_HALF_TIME: MatchPhase.EXTRA_SECOND,
    }

    def _apply_period(self, inp: StateInput) -> StateOutput:
        """Handle period start/end events.

        Phase advancement is determined from current state, not from
        the adapter. The adapter says "start" or "end" — the state
        machine knows what comes next based on where it is.
        """
        action = inp.data.get("action")  # "start" or "end"
        flags = []

        if action == "start":
            # Determine target phase from current state
            target_phase = inp.data.get("phase")
            if target_phase and isinstance(target_phase, str):
                try:
                    target_phase = MatchPhase(target_phase)
                except ValueError:
                    target_phase = None

            # If adapter didn't specify phase, derive from current state
            if not target_phase:
                target_phase = self._NEXT_PLAYING_PHASE.get(self.clock.phase)

            if target_phase:
                # Validate against tournament rules
                if target_phase in (MatchPhase.EXTRA_FIRST, MatchPhase.EXTRA_SECOND):
                    if not self.rules.stage_allows_extra_time(self.is_knockout):
                        flags.append("extra_time_not_allowed_in_stage")

                # Swap play direction at half boundaries
                if target_phase in DIRECTION_SWAP_PHASES:
                    self._swap_direction()
                    if self.direction:
                        flags.append("direction_swapped")
                        home_abbr = self.home.abbreviation
                        away_abbr = self.away.abbreviation
                        home_arrow = "→ high X" if self.direction.home_end == AttackEnd.HIGH_X else "→ low X"
                        away_arrow = "→ high X" if self.direction.away_end == AttackEnd.HIGH_X else "→ low X"
                        self.side_outputs.append(StateOutput(
                            kind=OutputKind.EVENT,
                            minute=inp.minute,
                            data={
                                "type": "direction_determined",
                                "home_end": self.direction.home_end.value,
                                "away_end": self.direction.away_end.value,
                                "description": (
                                    f"Direction swapped: {home_abbr} {home_arrow}, "
                                    f"{away_abbr} {away_arrow}"
                                ),
                            },
                            trust=SourceTrust.EVENT,
                            flags=["direction_swapped"],
                        ))

                valid = self.clock.transition(target_phase)
                if not valid:
                    flags.append("invalid_phase_transition")
            else:
                flags.append("cannot_determine_next_phase")

        elif action == "end":
            # Determine what the next phase should be
            if self.clock.phase == MatchPhase.FIRST_HALF:
                self.clock.transition(MatchPhase.HALF_TIME)
            elif self.clock.phase == MatchPhase.SECOND_HALF:
                if self.is_knockout and self.score[0] == self.score[1]:
                    self.clock.transition(MatchPhase.EXTRA_TIME_BREAK)
                else:
                    self.clock.transition(MatchPhase.FULL_TIME)
            elif self.clock.phase == MatchPhase.EXTRA_FIRST:
                self.clock.transition(MatchPhase.EXTRA_HALF_TIME)
            elif self.clock.phase == MatchPhase.EXTRA_SECOND:
                if self.score[0] == self.score[1]:
                    self.clock.transition(MatchPhase.PENALTIES)
                else:
                    self.clock.transition(MatchPhase.FULL_TIME)
            elif self.clock.phase == MatchPhase.PENALTIES:
                self.clock.transition(MatchPhase.FULL_TIME)

        self.clock.minute = inp.minute

        return StateOutput(
            kind=OutputKind.EVENT,
            minute=inp.minute,
            data={
                "type": "period",
                "action": action,
                "phase": self.clock.phase.value,
            },
            trust=inp.trust,
            flags=flags,
        )

    def _apply_score(self, inp: StateInput) -> StateOutput:
        """Handle a goal."""
        player_id = inp.data.get("player_id", "")
        team_id = inp.data.get("team_id", "")
        own_goal = inp.data.get("own_goal", False)
        is_penalty = inp.data.get("is_penalty", False)
        flags = []

        # Validate phase
        if not self.clock.is_playing:
            flags.append("goal_outside_playing_phase")

        # Validate minute belongs to current phase
        if not self.clock.minute_valid_for_phase(inp.minute):
            flags.append("minute_phase_mismatch")

        # Check for pending shot data to merge
        shot_key = f"{inp.minute.base}|{player_id}"
        shot_data = self._pending_shots.pop(shot_key, None)

        goal = ScoreEvent(
            minute=inp.minute,
            player_id=player_id,
            team_id=team_id,
            trust=inp.trust,
            shot_data=shot_data,
        )
        self.goals.append(goal)

        return StateOutput(
            kind=OutputKind.EVENT,
            minute=inp.minute,
            data={
                "type": "goal",
                "player_id": player_id,
                "team_id": team_id,
                "own_goal": own_goal,
                "is_penalty": is_penalty,
                "shot_data": shot_data,
                "score": self.score,
            },
            trust=inp.trust,
            flags=flags,
        )

    def _apply_score_verify(self, inp: StateInput) -> StateOutput:
        """Handle canonical score verification from match data or ESPN.

        Compares the canonical score against our event-derived score.
        If they disagree, finds and voids the phantom goal(s).
        """
        canonical_home = inp.data.get("home_score")
        canonical_away = inp.data.get("away_score")
        flags = []
        corrections = []

        if canonical_home is None or canonical_away is None:
            return StateOutput(
                kind=OutputKind.NOTHING,
                minute=inp.minute,
                data={"reason": "incomplete_score_verification"},
                trust=inp.trust,
            )

        our_home, our_away = self.score
        canonical_home = int(canonical_home)
        canonical_away = int(canonical_away)

        if our_home == canonical_home and our_away == canonical_away:
            # Scores match — all good
            return StateOutput(
                kind=OutputKind.NOTHING,
                minute=inp.minute,
                data={"reason": "score_verified_ok"},
                trust=inp.trust,
            )

        # Score mismatch — find phantom goals
        home_excess = our_home - canonical_home
        away_excess = our_away - canonical_away

        # Void excess home goals (most recent first)
        if home_excess > 0:
            voided_count = 0
            for goal in reversed(self.goals):
                if voided_count >= home_excess:
                    break
                if goal.team_id == self.home.team_id and not goal.voided:
                    goal.voided = True
                    goal.void_reason = "score_verification_mismatch"
                    voided_count += 1
                    corrections.append(goal)
                    flags.append(f"voided_home_goal_at_{goal.minute.display}")

        # Void excess away goals
        if away_excess > 0:
            voided_count = 0
            for goal in reversed(self.goals):
                if voided_count >= away_excess:
                    break
                if goal.team_id == self.away.team_id and not goal.voided:
                    goal.voided = True
                    goal.void_reason = "score_verification_mismatch"
                    voided_count += 1
                    corrections.append(goal)
                    flags.append(f"voided_away_goal_at_{goal.minute.display}")

        if corrections:
            return StateOutput(
                kind=OutputKind.CORRECTION,
                minute=inp.minute,
                data={
                    "type": "score_corrected",
                    "canonical": (canonical_home, canonical_away),
                    "was": (our_home, our_away),
                    "voided_goals": len(corrections),
                    "score": self.score,
                },
                trust=inp.trust,
                flags=flags,
            )

        # Score doesn't match but we can't find goals to void
        # (canonical has MORE goals than us — we're missing goals)
        flags.append("score_mismatch_unresolvable")
        return StateOutput(
            kind=OutputKind.NOTHING,
            minute=inp.minute,
            data={
                "reason": "score_mismatch",
                "canonical": (canonical_home, canonical_away),
                "ours": (our_home, our_away),
            },
            trust=inp.trust,
            flags=flags,
        )

    def _apply_score_void(self, inp: StateInput) -> StateOutput:
        """Handle a voided goal (VAR, offside, etc.)."""
        player_id = inp.data.get("player_id", "")
        reason = inp.data.get("reason", "")
        flags = []

        # Find the goal to void — most recent by this player, or by minute
        voided = None
        for goal in reversed(self.goals):
            if not goal.voided and goal.player_id == player_id:
                goal.voided = True
                goal.void_reason = reason
                voided = goal
                break

        if voided is None:
            flags.append("no_matching_goal_to_void")

        return StateOutput(
            kind=OutputKind.CORRECTION,
            minute=inp.minute,
            data={
                "type": "goal_voided",
                "player_id": player_id,
                "reason": reason,
                "score": self.score,
            },
            trust=inp.trust,
            flags=flags,
        )

    def _apply_sub(self, inp: StateInput) -> StateOutput:
        """Handle a substitution.

        Resolution uses on_pitch as ground truth. The API's
        description of who's coming on/off is not trusted.
        """
        player_a = inp.data.get("player_a", "")
        player_b = inp.data.get("player_b", "")
        team_id = inp.data.get("team_id", "")
        flags = []

        # Content-based dedup for subs (API sends same sub multiple times)
        sub_key = self._sub_dedup_key(player_a, player_b, inp.minute)
        if sub_key in self._seen_ids:
            return StateOutput(
                kind=OutputKind.NOTHING,
                minute=inp.minute,
                data={"reason": "duplicate_sub"},
                trust=inp.trust,
            )
        self._seen_ids.add(sub_key)

        # Determine team
        team = self._team_for_id(team_id) if team_id else None
        if team is None:
            team = self._team_for_player(player_a) or self._team_for_player(player_b)
        if team is None:
            flags.append("unknown_team_for_sub")
            return StateOutput(
                kind=OutputKind.EVENT,
                minute=inp.minute,
                data={"type": "sub", "on": player_a, "off": player_b,
                      "team_id": team_id},
                trust=inp.trust,
                flags=["unknown_team_for_sub"],
            )

        # Resolve direction from on_pitch ground truth
        a_on_pitch = player_a in team.on_pitch
        b_on_pitch = player_b in team.on_pitch

        if a_on_pitch and not b_on_pitch:
            player_off, player_on = player_a, player_b
        elif b_on_pitch and not a_on_pitch:
            player_off, player_on = player_b, player_a
        elif a_on_pitch and b_on_pitch:
            # Both on pitch — shouldn't happen, suspect data
            flags.append("both_players_on_pitch")
            player_off, player_on = player_a, player_b
        else:
            # Neither on pitch — could be a data issue or mid-match restart
            flags.append("neither_player_on_pitch")
            player_off, player_on = player_a, player_b

        # Validate
        if not validate_player_not_already_subbed_off(player_on, team.subbed_off):
            flags.append("player_already_subbed_off")
        if not validate_sub_count(team.subs_made,
                                  self.rules.max_subs_for_phase(
                                      self.clock.phase in (MatchPhase.EXTRA_FIRST,
                                                           MatchPhase.EXTRA_SECOND))):
            flags.append("sub_limit_exceeded")

        # Apply to state
        team.on_pitch.discard(player_off)
        team.on_pitch.add(player_on)
        team.subbed_off.add(player_off)
        team.subs_made += 1

        if not validate_player_count(len(team.on_pitch)):
            flags.append("team_below_minimum_players")

        return StateOutput(
            kind=OutputKind.EVENT,
            minute=inp.minute,
            data={
                "type": "sub",
                "on": player_on,
                "off": player_off,
                "team_id": team.team_id,
                "team_abbr": team.abbreviation,
                "subs_made": team.subs_made,
            },
            trust=inp.trust,
            flags=flags,
        )

    def _apply_discipline(self, inp: StateInput) -> StateOutput:
        """Handle cards, fouls, and offsides.

        Offsides are definitive direction evidence — by rule, a player
        can only be offside in the opponent's half.
        """
        player_id = inp.data.get("player_id", "")
        team_id = inp.data.get("team_id", "")
        card_type = inp.data.get("card_type", "")  # "yellow", "red", "second_yellow", "foul", "offside"
        flags = []

        # Offside as direction evidence
        raw_x = inp.data.get("position_x")
        if card_type == "offside" and raw_x is not None and team_id:
            direction_output = self._record_direction_evidence(
                team_id=team_id, raw_x=float(raw_x),
                event_type="offside", minute=inp.minute,
            )
            if direction_output:
                self.side_outputs.append(direction_output)

        team = self._team_for_id(team_id) or self._team_for_player(player_id)

        if team and card_type == "yellow":
            team.yellows[player_id] = team.yellows.get(player_id, 0) + 1
            if team.yellows[player_id] >= 2:
                # Second yellow = red (detected from our own tracking)
                team.reds.add(player_id)
                team.on_pitch.discard(player_id)
                card_type = "second_yellow_red"
                if not validate_player_count(len(team.on_pitch)):
                    flags.append("team_below_minimum_players")
        elif team and card_type == "second_yellow":
            # API explicitly sent SECOND_YELLOW_RED event type
            team.yellows[player_id] = max(team.yellows.get(player_id, 0), 2)
            team.reds.add(player_id)
            team.on_pitch.discard(player_id)
            card_type = "second_yellow_red"
            if not validate_player_count(len(team.on_pitch)):
                flags.append("team_below_minimum_players")
        elif team and card_type == "red":
            team.reds.add(player_id)
            team.on_pitch.discard(player_id)
            if not validate_player_count(len(team.on_pitch)):
                flags.append("team_below_minimum_players")

        return StateOutput(
            kind=OutputKind.EVENT,
            minute=inp.minute,
            data={
                "type": card_type,
                "player_id": player_id,
                "team_id": team.team_id if team else team_id,
            },
            trust=inp.trust,
            flags=flags,
        )

    def _apply_set_piece(self, inp: StateInput) -> StateOutput:
        """Handle corners, free kicks, penalties awarded.

        Corners are definitive direction evidence — the corner flag
        is always at the attacking end. One corner = direction committed.
        """
        # Corner as direction evidence
        raw_x = inp.data.get("position_x")
        team_id = inp.data.get("team_id", "")
        if inp.data.get("type") == "corner" and raw_x is not None:
            rx = float(raw_x)
            # Corner flags are at X~0 or X~100 — definitive
            if rx > 90 or rx < 10:
                direction_output = self._record_direction_evidence(
                    team_id=team_id, raw_x=rx,
                    event_type="corner", minute=inp.minute,
                )
                if direction_output:
                    self.side_outputs.append(direction_output)

        return StateOutput(
            kind=OutputKind.EVENT,
            minute=inp.minute,
            data=inp.data,
            trust=inp.trust,
        )

    def _apply_attempt(self, inp: StateInput) -> StateOutput:
        """Handle shots, saves, blocked shots.

        Shots are checked against pending/existing goals at the
        same minute for the same player — if a match is found,
        the shot data is merged into the goal (enrichment) instead
        of being emitted as a separate event.

        Shots with coordinates are primary evidence for play
        direction inference.
        """
        player_id = inp.data.get("player_id", "")
        shot_data = {
            k: v for k, v in inp.data.items()
            if k in ("position_x", "position_y", "distance", "zone",
                      "side", "gate_x", "gate_y", "placement",
                      "confidence", "on_target")
        }

        # Direction evidence: shots with coordinates
        raw_x = inp.data.get("position_x")
        team_id = inp.data.get("team_id", "")
        if raw_x is not None and inp.data.get("type") not in ("save", "assist"):
            direction_output = self._record_direction_evidence(
                team_id=team_id, raw_x=float(raw_x),
                event_type="shot", minute=inp.minute,
            )
            if direction_output:
                # Side-channel: direction announcement isn't returned
                # by apply(). Consumer drains side_outputs separately.
                self.side_outputs.append(direction_output)

        # Check: is there a goal at this minute for this player?
        shot_key = f"{inp.minute.base}|{player_id}"
        for goal in self.goals:
            if (goal.player_id == player_id
                    and goal.minute.base == inp.minute.base
                    and not goal.voided):
                # Merge shot data into goal
                goal.shot_data = shot_data
                # Emit as correction (goal was already emitted bare)
                return StateOutput(
                    kind=OutputKind.CORRECTION,
                    minute=inp.minute,
                    data={
                        "type": "goal_enriched",
                        "player_id": player_id,
                        "shot_data": shot_data,
                        "score": self.score,
                    },
                    trust=inp.trust,
                )

        # Assists — attach to the most recent goal at this minute
        if inp.data.get("type") == "assist":
            for goal in reversed(self.goals):
                if goal.minute.base == inp.minute.base and not goal.voided:
                    goal.assist_player_id = player_id
                    break
            return StateOutput(
                kind=OutputKind.EVENT,
                minute=inp.minute,
                data=inp.data,
                trust=inp.trust,
            )

        # Saves — emit directly
        if inp.data.get("type") == "save":
            return StateOutput(
                kind=OutputKind.EVENT,
                minute=inp.minute,
                data=inp.data,
                trust=inp.trust,
            )

        # Store as pending shot in case a goal arrives later
        self._pending_shots[shot_key] = shot_data

        return StateOutput(
            kind=OutputKind.EVENT,
            minute=inp.minute,
            data=inp.data,
            trust=inp.trust,
        )

    def _apply_interrupt(self, inp: StateInput) -> StateOutput:
        """Handle match interruptions: pause, delay, hydration, resume."""
        action = inp.data.get("action")  # "pause", "resume", "delay"

        if action == "pause" and self.clock.is_playing:
            self._phase_before_interrupt = self.clock.phase
            self.clock.transition(MatchPhase.INTERRUPTED)
        elif action == "resume" and self.clock.phase == MatchPhase.INTERRUPTED:
            if hasattr(self, '_phase_before_interrupt'):
                self.clock.transition(self._phase_before_interrupt)

        return StateOutput(
            kind=OutputKind.EVENT,
            minute=inp.minute,
            data=inp.data,
            trust=inp.trust,
        )

    def _apply_stats(self, inp: StateInput) -> StateOutput:
        """Handle stats snapshot (ESPN, clipboard, etc.)."""
        # Merge into current stats, don't replace
        self.stats.update(inp.data)

        return StateOutput(
            kind=OutputKind.STATS,
            minute=inp.minute,
            data=inp.data,
            trust=inp.trust,
        )

    def _apply_annotation(self, inp: StateInput) -> StateOutput:
        """Handle ape commentary, vision descriptions, editorial."""
        return StateOutput(
            kind=OutputKind.ANNOTATION,
            minute=inp.minute,
            data=inp.data,
            trust=inp.trust,
        )

    # --- Query ---

    def get_state(self) -> MatchState:
        """Return the current match state snapshot."""
        return MatchState(
            match_id=self.match_id,
            clock=self.clock,
            home=self.home,
            away=self.away,
            goals=list(self.goals),
            events=list(self.events),
            stats=dict(self.stats),
            annotations=list(self.annotations),
            direction=self.direction,
        )


# --- Builder ---

def build_match_state(
    match_id: str,
    home: TeamState,
    away: TeamState,
    rules: TournamentRules,
    inputs: list[StateInput],
    is_knockout: bool = False,
    up_to: MatchMinute | None = None,
) -> MatchState:
    """Build match state by replaying inputs through the state machine.

    This is the universal query primitive. Used by:
    - Feed: up_to = current match minute
    - Query: up_to = None (full match) or specific minute
    - Replay: up_to = simulated minute (spoiler-free)
    """
    sm = MatchStateMachine(
        match_id=match_id,
        home=home,
        away=away,
        rules=rules,
        is_knockout=is_knockout,
    )

    for inp in inputs:
        if up_to is not None and inp.minute > up_to:
            break
        sm.apply(inp)

    return sm.get_state()
