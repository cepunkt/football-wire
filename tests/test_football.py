"""Tests for football.py — sport invariants and match state."""

import pytest
from fbw.football import (
    MatchMinute, MatchPhase, MatchClock,
    PHASE_TRANSITIONS, PLAYING_PHASES, BREAK_PHASES,
    PHASE_ADDED_TIME_BASE, PHASE_REGULAR_MINUTES,
    validate_score_monotonic, validate_player_count,
    validate_player_not_already_subbed_off,
    MIN_PLAYERS_PER_SIDE,
)


class TestMatchMinute:
    """MatchMinute parsing, phase detection, and sort values."""

    def test_regular_minute(self):
        m = MatchMinute.from_notation("48'")
        assert m.base == 48
        assert m.added == 0
        assert m.phase == MatchPhase.SECOND_HALF

    def test_added_time(self):
        m = MatchMinute.from_notation("45'+3'")
        assert m.base == 45
        assert m.added == 3
        assert m.phase == MatchPhase.FIRST_HALF

    def test_added_time_second_half(self):
        m = MatchMinute.from_notation("90'+5'")
        assert m.base == 90
        assert m.added == 5
        assert m.phase == MatchPhase.SECOND_HALF

    def test_extra_time_minute(self):
        m = MatchMinute.from_notation("91'")
        assert m.base == 91
        assert m.added == 0
        assert m.phase == MatchPhase.EXTRA_FIRST

    def test_extra_time_added(self):
        m = MatchMinute.from_notation("105'+2'")
        assert m.base == 105
        assert m.added == 2
        assert m.phase == MatchPhase.EXTRA_FIRST

    def test_extra_second_half(self):
        m = MatchMinute.from_notation("106'")
        assert m.base == 106
        assert m.phase == MatchPhase.EXTRA_SECOND

    def test_empty_minute(self):
        m = MatchMinute.from_notation("")
        assert m.base == 0
        assert m.added == 0
        assert m.phase is None

    def test_empty_with_second_half_context(self):
        m = MatchMinute.from_notation("", description="before the second half")
        assert m.base == 45
        assert m.phase == MatchPhase.FIRST_HALF

    def test_empty_with_extra_time_context(self):
        m = MatchMinute.from_notation("", description="before extra time")
        assert m.base == 90
        assert m.phase == MatchPhase.SECOND_HALF

    def test_sort_added_time_before_next_phase(self):
        """45'+7' must sort before 46' (added time before second half)."""
        added = MatchMinute.from_notation("45'+7'")
        regular = MatchMinute.from_notation("46'")
        assert added.sort_value < regular.sort_value

    def test_sort_added_time_after_base(self):
        """45'+3' must sort after 45'."""
        added = MatchMinute.from_notation("45'+3'")
        base = MatchMinute.from_notation("45'")
        assert added.sort_value > base.sort_value

    def test_sort_large_added_time(self):
        """45'+15' (extreme added time) still sorts before 46'."""
        added = MatchMinute.from_notation("45'+15'")
        next_phase = MatchMinute.from_notation("46'")
        assert added.sort_value < next_phase.sort_value

    def test_no_ambiguity_between_added_and_regular(self):
        """45'+3' and 48' must have different sort values."""
        added = MatchMinute.from_notation("45'+3'")
        regular = MatchMinute.from_notation("48'")
        assert added.sort_value != regular.sort_value
        assert added.phase != regular.phase

    def test_display(self):
        assert MatchMinute.from_notation("45'+3'").display == "45'+3'"
        assert MatchMinute.from_notation("68'").display == "68'"

    def test_phase_prefix(self):
        assert MatchMinute.from_notation("30'").phase_prefix == "1H"
        assert MatchMinute.from_notation("68'").phase_prefix == "2H"
        assert MatchMinute.from_notation("95'").phase_prefix == "ET1"
        assert MatchMinute.from_notation("110'").phase_prefix == "ET2"
        assert MatchMinute.from_notation("45'+3'").phase_prefix == "1H"
        assert MatchMinute.from_notation("90'+2'").phase_prefix == "2H"


class TestMatchClock:
    """Match clock state transitions."""

    def test_initial_state(self):
        clock = MatchClock()
        assert clock.phase == MatchPhase.SCHEDULED
        assert not clock.is_playing
        assert not clock.is_terminal

    def test_full_match_progression(self):
        """SCHEDULED → 1H → HT → 2H → FT."""
        clock = MatchClock()
        assert clock.transition(MatchPhase.FIRST_HALF)
        assert clock.phase == MatchPhase.FIRST_HALF
        assert clock.is_playing

        assert clock.transition(MatchPhase.HALF_TIME)
        assert clock.phase == MatchPhase.HALF_TIME
        assert clock.is_break

        assert clock.transition(MatchPhase.SECOND_HALF)
        assert clock.phase == MatchPhase.SECOND_HALF
        assert clock.is_playing

        assert clock.transition(MatchPhase.FULL_TIME)
        assert clock.phase == MatchPhase.FULL_TIME
        assert clock.is_terminal

    def test_knockout_with_extra_time(self):
        """Full knockout progression including ET and penalties."""
        clock = MatchClock()
        clock.transition(MatchPhase.FIRST_HALF)
        clock.transition(MatchPhase.HALF_TIME)
        clock.transition(MatchPhase.SECOND_HALF)
        clock.transition(MatchPhase.EXTRA_TIME_BREAK)
        assert clock.phase == MatchPhase.EXTRA_TIME_BREAK

        clock.transition(MatchPhase.EXTRA_FIRST)
        clock.transition(MatchPhase.EXTRA_HALF_TIME)
        clock.transition(MatchPhase.EXTRA_SECOND)
        clock.transition(MatchPhase.PENALTIES)
        assert clock.phase == MatchPhase.PENALTIES

        clock.transition(MatchPhase.FULL_TIME)
        assert clock.is_terminal

    def test_invalid_transition_rejected(self):
        clock = MatchClock()
        assert not clock.transition(MatchPhase.SECOND_HALF)  # can't skip 1H
        assert clock.phase == MatchPhase.SCHEDULED

    def test_scheduled_to_first_half_directly(self):
        """FIFA API skips PRE_MATCH — must allow SCHEDULED → 1H."""
        clock = MatchClock()
        assert clock.transition(MatchPhase.FIRST_HALF)
        assert clock.phase == MatchPhase.FIRST_HALF

    def test_interrupted_and_resume(self):
        clock = MatchClock()
        clock.transition(MatchPhase.FIRST_HALF)
        assert clock.transition(MatchPhase.INTERRUPTED)
        assert clock.transition(MatchPhase.FIRST_HALF)  # resume
        assert clock.is_playing

    def test_period_count(self):
        clock = MatchClock()
        clock.transition(MatchPhase.FIRST_HALF)
        assert clock.period_count == 1
        clock.transition(MatchPhase.HALF_TIME)
        clock.transition(MatchPhase.SECOND_HALF)
        assert clock.period_count == 2

    def test_minute_valid_for_phase(self):
        clock = MatchClock()
        clock.transition(MatchPhase.FIRST_HALF)
        assert clock.minute_valid_for_phase(MatchMinute.from_notation("30'"))
        assert clock.minute_valid_for_phase(MatchMinute.from_notation("45'+3'"))
        assert not clock.minute_valid_for_phase(MatchMinute.from_notation("60'"))


class TestValidation:
    """Football validation rules."""

    def test_score_monotonic_increase(self):
        assert validate_score_monotonic(0, 0, 1, 0)
        assert validate_score_monotonic(1, 0, 1, 1)

    def test_score_decrease_invalid(self):
        assert not validate_score_monotonic(1, 0, 0, 0)

    def test_player_count_minimum(self):
        assert validate_player_count(MIN_PLAYERS_PER_SIDE)
        assert not validate_player_count(MIN_PLAYERS_PER_SIDE - 1)

    def test_player_cant_return_after_sub(self):
        subbed_off = {"p1", "p2"}
        assert not validate_player_not_already_subbed_off("p1", subbed_off)
        assert validate_player_not_already_subbed_off("p3", subbed_off)
