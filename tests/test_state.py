"""Tests for state.py — match state machine."""

import pytest
from datetime import datetime, timezone

from fbw.state import (
    MatchStateMachine, TeamState, StateInput, StateOutput,
    InputCategory, OutputKind, SourceTrust, PlayDirection,
)
from fbw.football import AttackEnd, MatchMinute, MatchPhase
from fbw.tournament import load_tournament
from pathlib import Path


@pytest.fixture
def rules():
    return load_tournament(Path("data/static/tournaments/wc2026.toml"))


@pytest.fixture
def now():
    return datetime.now(timezone.utc)


def _make_input(category, minute_str, data, source="fifa",
                trust=SourceTrust.EVENT, now=None):
    return StateInput(
        category=category,
        minute=MatchMinute.from_notation(minute_str),
        data=data,
        source=source,
        trust=trust,
        timestamp=now or datetime.now(timezone.utc),
    )


def _make_sm(rules, home_on_pitch=None, away_on_pitch=None):
    return MatchStateMachine(
        match_id="test",
        home=TeamState("home_id", "HOM",
                       on_pitch=home_on_pitch or {"p1", "p2", "p3", "p4", "p5",
                                                   "p6", "p7", "p8", "p9", "p10", "p11"}),
        away=TeamState("away_id", "AWY",
                       on_pitch=away_on_pitch or {"a1", "a2", "a3", "a4", "a5",
                                                   "a6", "a7", "a8", "a9", "a10", "a11"}),
        rules=rules,
    )


class TestPeriodAdvancement:
    """Period state machine progression."""

    def test_start_advances_to_first_half(self, rules):
        sm = _make_sm(rules)
        r = sm.apply(_make_input(InputCategory.PERIOD_CHANGE, "", {"action": "start"}))
        assert sm.clock.phase == MatchPhase.FIRST_HALF
        assert r.flags == []

    def test_end_first_half_goes_to_halftime(self, rules):
        sm = _make_sm(rules)
        sm.apply(_make_input(InputCategory.PERIOD_CHANGE, "", {"action": "start"}))
        sm.apply(_make_input(InputCategory.PERIOD_CHANGE, "45'+3'", {"action": "end"}))
        assert sm.clock.phase == MatchPhase.HALF_TIME

    def test_full_group_match(self, rules):
        sm = _make_sm(rules)
        sm.apply(_make_input(InputCategory.PERIOD_CHANGE, "", {"action": "start"}))
        sm.apply(_make_input(InputCategory.PERIOD_CHANGE, "45'+3'", {"action": "end"}))
        sm.apply(_make_input(InputCategory.PERIOD_CHANGE, "45'", {"action": "start"}))
        sm.apply(_make_input(InputCategory.PERIOD_CHANGE, "90'+2'", {"action": "end"}))
        assert sm.clock.phase == MatchPhase.FULL_TIME
        assert sm.clock.is_terminal

    def test_consecutive_starts_flagged(self, rules):
        """Second PERIOD_START without END should flag — can't determine next phase."""
        sm = _make_sm(rules)
        sm.apply(_make_input(InputCategory.PERIOD_CHANGE, "", {"action": "start"}))
        r = sm.apply(_make_input(InputCategory.PERIOD_CHANGE, "45'", {"action": "start"}))
        assert "cannot_determine_next_phase" in r.flags


class TestScoreTracking:
    """Goal scoring and score computation."""

    def test_goal_increments_score(self, rules):
        sm = _make_sm(rules)
        sm.apply(_make_input(InputCategory.PERIOD_CHANGE, "", {"action": "start"}))
        sm.apply(_make_input(InputCategory.SCORE_CHANGE, "30'",
                             {"type": "goal", "player_id": "p7", "team_id": "home_id",
                              "own_goal": False, "is_penalty": False}))
        assert sm.score == (1, 0)

    def test_multiple_goals(self, rules):
        sm = _make_sm(rules)
        sm.apply(_make_input(InputCategory.PERIOD_CHANGE, "", {"action": "start"}))
        sm.apply(_make_input(InputCategory.SCORE_CHANGE, "30'",
                             {"type": "goal", "player_id": "p7", "team_id": "home_id",
                              "own_goal": False, "is_penalty": False}))
        sm.apply(_make_input(InputCategory.SCORE_CHANGE, "45'",
                             {"type": "goal", "player_id": "a3", "team_id": "away_id",
                              "own_goal": False, "is_penalty": False}))
        assert sm.score == (1, 1)


class TestScoreVerification:
    """Phantom goal detection and voiding."""

    def test_matching_score_no_correction(self, rules):
        sm = _make_sm(rules)
        sm.apply(_make_input(InputCategory.PERIOD_CHANGE, "", {"action": "start"}))
        sm.apply(_make_input(InputCategory.SCORE_CHANGE, "30'",
                             {"type": "goal", "player_id": "p7", "team_id": "home_id",
                              "own_goal": False, "is_penalty": False}))
        r = sm.apply(_make_input(InputCategory.SCORE_VERIFY, "38'",
                                 {"home_score": 1, "away_score": 0}))
        assert r.kind == OutputKind.NOTHING

    def test_phantom_goal_voided(self, rules):
        """Musiala scenario: goal recorded then canonical says 0-0."""
        sm = _make_sm(rules)
        sm.apply(_make_input(InputCategory.PERIOD_CHANGE, "", {"action": "start"}))
        sm.apply(_make_input(InputCategory.SCORE_CHANGE, "30'",
                             {"type": "goal", "player_id": "p7", "team_id": "home_id",
                              "own_goal": False, "is_penalty": False}))
        assert sm.score == (1, 0)

        r = sm.apply(_make_input(InputCategory.SCORE_VERIFY, "38'",
                                 {"home_score": 0, "away_score": 0}))
        assert r.kind == OutputKind.CORRECTION
        assert sm.score == (0, 0)
        assert sm.goals[0].voided
        assert "voided_home_goal" in r.flags[0]

    def test_partial_void(self, rules):
        """Two home goals, canonical says only one."""
        sm = _make_sm(rules)
        sm.apply(_make_input(InputCategory.PERIOD_CHANGE, "", {"action": "start"}))
        sm.apply(_make_input(InputCategory.SCORE_CHANGE, "20'",
                             {"type": "goal", "player_id": "p7", "team_id": "home_id",
                              "own_goal": False, "is_penalty": False}))
        sm.apply(_make_input(InputCategory.SCORE_CHANGE, "30'",
                             {"type": "goal", "player_id": "p8", "team_id": "home_id",
                              "own_goal": False, "is_penalty": False}))
        assert sm.score == (2, 0)

        r = sm.apply(_make_input(InputCategory.SCORE_VERIFY, "38'",
                                 {"home_score": 1, "away_score": 0}))
        assert sm.score == (1, 0)
        # Most recent goal voided (p8), older kept (p7)
        assert not sm.goals[0].voided  # p7 at 20'
        assert sm.goals[1].voided      # p8 at 30'


class TestSubResolution:
    """Substitution direction resolution from on_pitch."""

    def test_starter_off_sub_on(self, rules):
        sm = _make_sm(rules)
        sm.apply(_make_input(InputCategory.PERIOD_CHANGE, "", {"action": "start"}))
        r = sm.apply(_make_input(InputCategory.PLAYER_CHANGE, "60'",
                                 {"player_a": "p5", "player_b": "sub1",
                                  "team_id": "home_id"}))
        assert r.data["off"] == "p5"
        assert r.data["on"] == "sub1"
        assert r.flags == []

    def test_on_pitch_updated_after_sub(self, rules):
        sm = _make_sm(rules)
        sm.apply(_make_input(InputCategory.PERIOD_CHANGE, "", {"action": "start"}))
        sm.apply(_make_input(InputCategory.PLAYER_CHANGE, "60'",
                             {"player_a": "p5", "player_b": "sub1",
                              "team_id": "home_id"}))
        assert "sub1" in sm.home.on_pitch
        assert "p5" not in sm.home.on_pitch
        assert "p5" in sm.home.subbed_off

    def test_subbed_off_player_cant_return(self, rules):
        sm = _make_sm(rules)
        sm.apply(_make_input(InputCategory.PERIOD_CHANGE, "", {"action": "start"}))
        sm.apply(_make_input(InputCategory.PLAYER_CHANGE, "60'",
                             {"player_a": "p5", "player_b": "sub1",
                              "team_id": "home_id"}))
        r = sm.apply(_make_input(InputCategory.PLAYER_CHANGE, "70'",
                                 {"player_a": "sub1", "player_b": "p5",
                                  "team_id": "home_id"}))
        assert "player_already_subbed_off" in r.flags

    def test_duplicate_sub_deduped(self, rules):
        sm = _make_sm(rules)
        sm.apply(_make_input(InputCategory.PERIOD_CHANGE, "", {"action": "start"}))
        sm.apply(_make_input(InputCategory.PLAYER_CHANGE, "60'",
                             {"player_a": "p5", "player_b": "sub1",
                              "team_id": "home_id"}))
        # Same sub again with swapped order
        r = sm.apply(_make_input(InputCategory.PLAYER_CHANGE, "60'",
                                 {"player_a": "sub1", "player_b": "p5",
                                  "team_id": "home_id"}))
        assert r.kind == OutputKind.NOTHING

    def test_neither_on_pitch_flagged(self, rules):
        sm = _make_sm(rules)
        sm.apply(_make_input(InputCategory.PERIOD_CHANGE, "", {"action": "start"}))
        r = sm.apply(_make_input(InputCategory.PLAYER_CHANGE, "60'",
                                 {"player_a": "unknown1", "player_b": "unknown2",
                                  "team_id": "home_id"}))
        assert "neither_player_on_pitch" in r.flags


class TestDiscipline:
    """Card handling including second yellow."""

    def test_yellow_card_recorded(self, rules):
        sm = _make_sm(rules)
        sm.apply(_make_input(InputCategory.PERIOD_CHANGE, "", {"action": "start"}))
        r = sm.apply(_make_input(InputCategory.DISCIPLINE, "30'",
                                 {"card_type": "yellow", "player_id": "p5",
                                  "team_id": "home_id"}))
        assert r.data["type"] == "yellow"
        assert sm.home.yellows["p5"] == 1
        assert len(sm.home.on_pitch) == 11  # still on pitch

    def test_second_yellow_from_api(self, rules):
        """API sends explicit SECOND_YELLOW_RED event."""
        sm = _make_sm(rules)
        sm.apply(_make_input(InputCategory.PERIOD_CHANGE, "", {"action": "start"}))
        r = sm.apply(_make_input(InputCategory.DISCIPLINE, "60'",
                                 {"card_type": "second_yellow", "player_id": "p5",
                                  "team_id": "home_id"}))
        assert r.data["type"] == "second_yellow_red"
        assert "p5" in sm.home.reds
        assert "p5" not in sm.home.on_pitch
        assert len(sm.home.on_pitch) == 10

    def test_two_yellows_auto_detected(self, rules):
        """Two separate yellow events → auto second-yellow-red."""
        sm = _make_sm(rules)
        sm.apply(_make_input(InputCategory.PERIOD_CHANGE, "", {"action": "start"}))
        sm.apply(_make_input(InputCategory.DISCIPLINE, "30'",
                             {"card_type": "yellow", "player_id": "p5",
                              "team_id": "home_id"}))
        r = sm.apply(_make_input(InputCategory.DISCIPLINE, "60'",
                                 {"card_type": "yellow", "player_id": "p5",
                                  "team_id": "home_id"}))
        assert r.data["type"] == "second_yellow_red"
        assert "p5" in sm.home.reds
        assert len(sm.home.on_pitch) == 10

    def test_straight_red(self, rules):
        sm = _make_sm(rules)
        sm.apply(_make_input(InputCategory.PERIOD_CHANGE, "", {"action": "start"}))
        r = sm.apply(_make_input(InputCategory.DISCIPLINE, "30'",
                                 {"card_type": "red", "player_id": "p5",
                                  "team_id": "home_id"}))
        assert "p5" in sm.home.reds
        assert len(sm.home.on_pitch) == 10


class TestGoalShotMerge:
    """Shot events merging into existing goals."""

    def test_shot_merges_into_goal(self, rules):
        """Shot arriving after goal at same minute/player → enrichment."""
        sm = _make_sm(rules)
        sm.apply(_make_input(InputCategory.PERIOD_CHANGE, "", {"action": "start"}))
        sm.apply(_make_input(InputCategory.SCORE_CHANGE, "68'",
                             {"type": "goal", "player_id": "p7", "team_id": "home_id",
                              "own_goal": False, "is_penalty": False}))
        r = sm.apply(_make_input(InputCategory.ATTEMPT, "68'",
                                 {"type": "shot", "player_id": "p7", "team_id": "home_id",
                                  "position_x": 95.0, "position_y": 50.0,
                                  "gate_x": 45.0, "gate_y": 20.0,
                                  "on_target": True}))
        assert r.kind == OutputKind.CORRECTION
        assert r.data["type"] == "goal_enriched"
        assert sm.goals[0].shot_data is not None

    def test_shot_before_goal_stored_as_pending(self, rules):
        """Shot arriving before goal → stored, goal picks it up."""
        sm = _make_sm(rules)
        sm.apply(_make_input(InputCategory.PERIOD_CHANGE, "", {"action": "start"}))
        sm.apply(_make_input(InputCategory.ATTEMPT, "68'",
                             {"type": "shot", "player_id": "p7", "team_id": "home_id",
                              "position_x": 95.0, "position_y": 50.0,
                              "on_target": True}))
        sm.apply(_make_input(InputCategory.SCORE_CHANGE, "68'",
                             {"type": "goal", "player_id": "p7", "team_id": "home_id",
                              "own_goal": False, "is_penalty": False}))
        assert sm.goals[0].shot_data is not None


class TestAssists:
    """Assist attachment to goals."""

    def test_assist_attaches_to_goal(self, rules):
        sm = _make_sm(rules)
        sm.apply(_make_input(InputCategory.PERIOD_CHANGE, "", {"action": "start"}))
        sm.apply(_make_input(InputCategory.SCORE_CHANGE, "68'",
                             {"type": "goal", "player_id": "p7", "team_id": "home_id",
                              "own_goal": False, "is_penalty": False}))
        sm.apply(_make_input(InputCategory.ATTEMPT, "68'",
                             {"type": "assist", "player_id": "p9",
                              "team_id": "home_id"}))
        assert sm.goals[0].assist_player_id == "p9"


class TestInterruptions:
    """Match interruptions — hydration breaks, delays."""

    def test_pause_and_resume(self, rules):
        sm = _make_sm(rules)
        sm.apply(_make_input(InputCategory.PERIOD_CHANGE, "", {"action": "start"}))
        sm.apply(_make_input(InputCategory.MATCH_INTERRUPT, "24'",
                             {"action": "pause"}))
        assert sm.clock.phase == MatchPhase.INTERRUPTED

        sm.apply(_make_input(InputCategory.MATCH_INTERRUPT, "27'",
                             {"action": "resume"}))
        assert sm.clock.phase == MatchPhase.FIRST_HALF


class TestPlayDirection:
    """Play direction inference from shot coordinates."""

    def _shot_input(self, minute, team_id, raw_x, raw_y=50.0):
        return _make_input(InputCategory.ATTEMPT, minute, {
            "type": "shot", "player_id": "p1",
            "team_id": team_id,
            "position_x": raw_x, "position_y": raw_y,
        })

    def test_no_direction_before_evidence(self, rules):
        """Direction is None until enough shots observed."""
        sm = _make_sm(rules)
        sm.apply(_make_input(InputCategory.PERIOD_CHANGE, "", {"action": "start"}))
        assert sm.direction is None

    def test_single_shot_not_enough(self, rules):
        """One shot is below commit threshold."""
        sm = _make_sm(rules)
        sm.apply(_make_input(InputCategory.PERIOD_CHANGE, "", {"action": "start"}))
        sm.apply(self._shot_input("4'", "home_id", 85.0))
        assert sm.direction is None

    def test_two_agreeing_shots_commits(self, rules):
        """Two shots from same team toward same end → direction committed."""
        sm = _make_sm(rules)
        sm.apply(_make_input(InputCategory.PERIOD_CHANGE, "", {"action": "start"}))
        sm.apply(self._shot_input("2'", "home_id", 88.0))
        sm.apply(self._shot_input("4'", "home_id", 95.0))
        assert sm.direction is not None
        assert sm.direction.home_end == AttackEnd.HIGH_X
        assert sm.direction.away_end == AttackEnd.LOW_X
        assert sm.direction.confidence == 2

    def test_direction_from_away_team(self, rules):
        """Direction can also be inferred from away team shots."""
        sm = _make_sm(rules)
        sm.apply(_make_input(InputCategory.PERIOD_CHANGE, "", {"action": "start"}))
        sm.apply(self._shot_input("10'", "away_id", 15.0))
        sm.apply(self._shot_input("12'", "away_id", 8.0))
        assert sm.direction is not None
        assert sm.direction.away_end == AttackEnd.LOW_X
        assert sm.direction.home_end == AttackEnd.HIGH_X

    def test_mixed_team_evidence(self, rules):
        """One shot from each team, both agreeing on layout."""
        sm = _make_sm(rules)
        sm.apply(_make_input(InputCategory.PERIOD_CHANGE, "", {"action": "start"}))
        # Home shoots toward high X
        sm.apply(self._shot_input("2'", "home_id", 88.0))
        # Away shoots toward low X — same layout
        sm.apply(self._shot_input("5'", "away_id", 12.0))
        # Each team has 1 observation — neither reaches threshold alone
        assert sm.direction is None
        # Another home shot tips it
        sm.apply(self._shot_input("8'", "home_id", 82.0))
        assert sm.direction is not None
        assert sm.direction.home_end == AttackEnd.HIGH_X

    def test_direction_swaps_at_halftime(self, rules):
        """Direction reverses when second half starts."""
        sm = _make_sm(rules)
        sm.apply(_make_input(InputCategory.PERIOD_CHANGE, "", {"action": "start"}))
        sm.apply(self._shot_input("2'", "home_id", 88.0))
        sm.apply(self._shot_input("4'", "home_id", 95.0))
        assert sm.direction.home_end == AttackEnd.HIGH_X

        # Half-time
        sm.apply(_make_input(InputCategory.PERIOD_CHANGE, "45'", {"action": "end"}))
        # Second half
        sm.apply(_make_input(InputCategory.PERIOD_CHANGE, "46'", {"action": "start"}))
        assert sm.direction.home_end == AttackEnd.LOW_X
        assert sm.direction.away_end == AttackEnd.HIGH_X

    def test_direction_swaps_for_extra_time(self, rules):
        """Direction swaps at every half boundary including ET."""
        sm = MatchStateMachine(
            match_id="test",
            home=TeamState("home_id", "HOM",
                           on_pitch={"p1", "p2", "p3", "p4", "p5",
                                     "p6", "p7", "p8", "p9", "p10", "p11"}),
            away=TeamState("away_id", "AWY",
                           on_pitch={"a1", "a2", "a3", "a4", "a5",
                                     "a6", "a7", "a8", "a9", "a10", "a11"}),
            rules=rules,
            is_knockout=True,
        )
        # 1H: home → HIGH_X
        sm.apply(_make_input(InputCategory.PERIOD_CHANGE, "", {"action": "start"}))
        sm.apply(self._shot_input("2'", "home_id", 88.0))
        sm.apply(self._shot_input("4'", "home_id", 95.0))
        assert sm.direction.home_end == AttackEnd.HIGH_X

        # 2H: swap → home → LOW_X
        sm.apply(_make_input(InputCategory.PERIOD_CHANGE, "45'", {"action": "end"}))
        sm.apply(_make_input(InputCategory.PERIOD_CHANGE, "46'", {"action": "start"}))
        assert sm.direction.home_end == AttackEnd.LOW_X

        # ET1: swap → home → HIGH_X (same as 1H)
        sm.apply(_make_input(InputCategory.PERIOD_CHANGE, "90'", {"action": "end"}))
        sm.apply(_make_input(InputCategory.PERIOD_CHANGE, "91'",
                             {"action": "start", "phase": "ET1"}))
        assert sm.direction.home_end == AttackEnd.HIGH_X

        # ET2: swap → home → LOW_X (same as 2H)
        sm.apply(_make_input(InputCategory.PERIOD_CHANGE, "105'", {"action": "end"}))
        sm.apply(_make_input(InputCategory.PERIOD_CHANGE, "106'",
                             {"action": "start", "phase": "ET2"}))
        assert sm.direction.home_end == AttackEnd.LOW_X

    def test_saves_dont_contribute_evidence(self, rules):
        """Saves should not be used for direction inference."""
        sm = _make_sm(rules)
        sm.apply(_make_input(InputCategory.PERIOD_CHANGE, "", {"action": "start"}))
        # Two saves — should not commit direction
        sm.apply(_make_input(InputCategory.ATTEMPT, "10'", {
            "type": "save", "team_id": "home_id",
            "position_x": 5.0, "position_y": 50.0,
        }))
        sm.apply(_make_input(InputCategory.ATTEMPT, "15'", {
            "type": "save", "team_id": "home_id",
            "position_x": 8.0, "position_y": 45.0,
        }))
        assert sm.direction is None

    def test_direction_in_get_state(self, rules):
        """Direction is exposed through get_state()."""
        sm = _make_sm(rules)
        sm.apply(_make_input(InputCategory.PERIOD_CHANGE, "", {"action": "start"}))
        sm.apply(self._shot_input("2'", "home_id", 88.0))
        sm.apply(self._shot_input("4'", "home_id", 95.0))
        state = sm.get_state()
        assert state.direction is not None
        assert state.direction.home_end == AttackEnd.HIGH_X

    def test_direction_not_recommitted_after_lock(self, rules):
        """Once committed, new shots don't change the direction."""
        sm = _make_sm(rules)
        sm.apply(_make_input(InputCategory.PERIOD_CHANGE, "", {"action": "start"}))
        sm.apply(self._shot_input("2'", "home_id", 88.0))
        sm.apply(self._shot_input("4'", "home_id", 95.0))
        assert sm.direction.home_end == AttackEnd.HIGH_X
        # Contradictory shots after commitment
        sm.apply(self._shot_input("30'", "home_id", 10.0))
        sm.apply(self._shot_input("32'", "home_id", 15.0))
        # Direction unchanged
        assert sm.direction.home_end == AttackEnd.HIGH_X
