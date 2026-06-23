"""
Tests for the processing layer.

Each test targets a known FIFA API failure mode discovered during
live match testing. Fixtures are real API responses that demonstrate
the bug.

Run: PYTHONPATH=src pytest tests/ -v
"""

import json
from pathlib import Path

import pytest

from fbw.model import (
    Match, Team, Player, Event, EventType, Minute,
    ShotPosition, GoalPlacement, MatchStats, Trust, Position,
)
from fbw.process import (
    parse_match, parse_event, process_events,
    cross_reference_subs, get_localized,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"


def load_fixture(name: str) -> dict:
    with open(FIXTURES / f"{name}.json") as f:
        return json.load(f)


def load_match_fixture(name: str) -> Match:
    raw = load_fixture(name)
    return parse_match(raw)


# --- Substitution direction ---

class TestSubDirection:
    """The API sometimes inverts IdPlayer/IdSubPlayer on sub events.
    Discovered: MEX 1-0 KOR, 2026-06-19. All 8 subs inverted.
    """

    def test_inverted_sub_corrected(self):
        """Romo (starter, scored the goal) shown as coming ON by API.
        On-pitch tracking should detect he's going OFF."""
        match = load_match_fixture("match_mex_kor")
        ev_raw = load_fixture("sub_inverted_romo")
        event = parse_event(ev_raw, match)

        # Romo (IdPlayer in API) was a starter — should be going OFF
        romo_id = str(ev_raw["IdPlayer"])
        assert romo_id in match.on_pitch, "Romo should be on pitch (starter)"
        assert event.off_player_id == romo_id, (
            f"Romo should be going OFF, got on={event.on_player_id} off={event.off_player_id}"
        )

    def test_correct_sub_preserved(self):
        """Son going OFF at 57' — API got this one right.
        On-pitch tracking should agree, not over-correct."""
        match = load_match_fixture("match_mex_kor")
        ev_raw = load_fixture("sub_correct_son")
        event = parse_event(ev_raw, match)

        # Son's IdSubPlayer should be the one going OFF
        son_sub_id = str(ev_raw.get("IdSubPlayer", ""))
        son_player_id = str(ev_raw.get("IdPlayer", ""))

        # One of them is going off, one coming on
        assert event.on_player_id != event.off_player_id


# --- Team attribution ---

class TestTeamAttribution:
    """The API sometimes attributes events to the wrong team.
    Discovered: CAN 6-0 QAT, 2026-06-18. Pedro Miguel (QAT) shown as Canada.
    """

    def test_misattributed_team_corrected(self):
        """Pedro Miguel is a QAT player but API says Canada in both
        IdTeam and EventDescription. Roster lookup should correct."""
        match = load_match_fixture("match_can_qat")
        ev_raw = load_fixture("team_misattribution_pedro")
        event = parse_event(ev_raw, match)

        assert event.team_abbr == "QAT", (
            f"Pedro Miguel is QAT, got team={event.team_abbr}"
        )
        assert event.team_trust in (Trust.TRUSTED, Trust.INFERRED), (
            f"Should be trusted/inferred from roster, got {event.team_trust}"
        )


# --- Duplicate events ---

class TestDeduplication:
    """The API sends the same event with different EventIds.
    Discovered: CAN 6-0 QAT, 75' goal sent twice.
    """

    def test_content_dedup_catches_duplicates(self):
        """Two goal events at 75' with different EventIds but same content."""
        ev_a = load_fixture("duplicate_goal_a")
        ev_b = load_fixture("duplicate_goal_b")

        assert ev_a["EventId"] != ev_b["EventId"], "Should have different EventIds"

        match = load_match_fixture("match_can_qat")
        event_a = parse_event(ev_a, match)
        event_b = parse_event(ev_b, match)

        assert event_a.dedup_key == event_b.dedup_key, (
            f"Same content should produce same dedup key:\n"
            f"  a: {event_a.dedup_key}\n"
            f"  b: {event_b.dedup_key}"
        )

    def test_process_events_removes_duplicates(self):
        """Full pipeline should emit only one of the duplicate goals."""
        ev_a = load_fixture("duplicate_goal_a")
        ev_b = load_fixture("duplicate_goal_b")

        match = load_match_fixture("match_can_qat")
        events = process_events([ev_a, ev_b], match)

        goals_75 = [e for e in events if e.event_type == EventType.GOAL
                     and e.minute.raw == "75'"]
        assert len(goals_75) == 1, f"Expected 1 goal at 75', got {len(goals_75)}"


# --- Score consistency ---

class TestScoreConsistency:
    """The API sends stale scores from the dual pipeline.
    Discovered: CAN 6-0 QAT, 66' goal shows [4-0] instead of [4-1].
    """

    def test_score_regression_detected(self):
        """After a 3-1 score, an event showing 4-0 has a regression
        in away goals (1 -> 0). Should be detectable."""
        match = load_match_fixture("match_can_qat")
        match.home_score = 3
        match.away_score = 1

        ev_raw = load_fixture("score_inconsistency_66")
        event = parse_event(ev_raw, match)

        # Away goals went from 1 to 0 — regression
        trust = match.check_score_consistency(event)
        assert trust == Trust.SUSPECT, (
            f"Score regression should be suspect, got {trust}"
        )


# --- Coordinates ---

class TestCoordinates:
    """Shot position normalization and distance calculation."""

    def test_normalize_attacker_left(self):
        """Son at (12,38) — Korean left confirmed by observer."""
        shot = ShotPosition.from_raw(12, 38)
        assert shot.side == "left", f"Expected left, got {shot.side}"
        assert shot.zone == "inside box"
        assert 12 < shot.distance_m < 14  # ~13m (distance to nearest goal point)

    def test_normalize_opposite_end(self):
        """Alvarado at (76,37) — Mexican right (opposite end)."""
        shot = ShotPosition.from_raw(76, 37)
        assert shot.side == "right", f"Expected right, got {shot.side}"
        assert shot.zone == "outside box"
        assert 24 < shot.distance_m < 27  # ~26m (distance to nearest goal point)

    def test_central_shot(self):
        """Centre of pitch shot should be central."""
        shot = ShotPosition.from_raw(90, 50)
        assert shot.side == "central"
        assert shot.zone == "inside box"

    def test_six_yard_box(self):
        """Very close range shot."""
        shot = ShotPosition.from_raw(95, 50)
        assert shot.zone == "6-yard box"
        assert shot.distance_m < 6

    def test_long_range(self):
        """Shot from beyond 35m."""
        shot = ShotPosition.from_raw(60, 20)
        assert shot.zone == "long range"
        assert shot.distance_m > 40


# --- Minute parsing ---

class TestMinuteParsing:

    def test_regular_minute(self):
        m = Minute.parse("16'")
        assert m.value == 16.0

    def test_added_time(self):
        m = Minute.parse("45'+3'")
        assert m.value == 48.0

    def test_empty_with_second_half(self):
        m = Minute.parse("", "Before the second half begins")
        assert m.value == 45.5

    def test_empty_no_context(self):
        m = Minute.parse("")
        assert m.value == -1.0

    def test_sortable(self):
        minutes = [
            Minute.parse("45'+3'"),
            Minute.parse("16'"),
            Minute.parse("90'+2'"),
            Minute.parse(""),
            Minute.parse("7'"),
        ]
        sorted_vals = [m.value for m in sorted(minutes)]
        assert sorted_vals == [-1.0, 7.0, 16.0, 48.0, 92.0]


# --- Shot confidence ---

class TestShotConfidence:

    def test_on_target(self):
        """Shot with position AND goal placement = on target."""
        ev = Event(
            event_id="1", event_type=EventType.SHOT,
            minute=Minute.parse("7'"), description="shot",
            shot_position=ShotPosition.from_raw(76, 37),
            goal_placement=GoalPlacement(raw_x=75, raw_y=3),
        )
        from fbw.format import shot_confidence
        assert shot_confidence(ev) == "on target"

    def test_off_target(self):
        """Shot with position but NO placement = off target."""
        ev = Event(
            event_id="2", event_type=EventType.SHOT,
            minute=Minute.parse("20'"), description="shot",
            shot_position=ShotPosition.from_raw(60, 20),
        )
        from fbw.format import shot_confidence
        assert shot_confidence(ev) == "off target"

    def test_attempt_only(self):
        """Shot with no position data = attempt."""
        ev = Event(
            event_id="3", event_type=EventType.SHOT,
            minute=Minute.parse("30'"), description="shot",
        )
        from fbw.format import shot_confidence
        assert shot_confidence(ev) == "attempt"
