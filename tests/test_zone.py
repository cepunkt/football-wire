"""Tests for zone classification with width constraints."""

import pytest
from fbw.model import ShotPosition


class TestZoneClassification:
    """Zone classification uses both depth AND width."""

    def test_six_yard_box_central(self):
        """Shot from 5m, central — 6-yard box."""
        sp = ShotPosition.from_raw(95, 54)
        assert sp.zone == "6-yard box"

    def test_six_yard_box_extreme_width_not_box(self):
        """Corner flag (100,100) — near goal line but outside any box."""
        sp = ShotPosition.from_raw(100, 100)
        assert sp.zone != "6-yard box"

    def test_goal_line_extreme_right_not_box(self):
        """(100,2) — goal line but far right of pitch."""
        sp = ShotPosition.from_raw(100, 2)
        assert sp.zone != "6-yard box"

    def test_inside_box_central(self):
        """Shot from 11m, central — inside box."""
        sp = ShotPosition.from_raw(90, 52)
        assert sp.zone == "inside box"

    def test_inside_box_wide_but_in_area(self):
        """Shot from inside penalty area but wide angle."""
        sp = ShotPosition.from_raw(90, 25)
        assert sp.zone == "inside box"

    def test_inside_box_too_wide(self):
        """Position at penalty depth but outside penalty area width."""
        sp = ShotPosition.from_raw(90, 10)
        assert sp.zone != "inside box"

    def test_edge_of_box(self):
        sp = ShotPosition.from_raw(80, 50)
        assert sp.zone == "edge of box"

    def test_outside_box(self):
        sp = ShotPosition.from_raw(70, 50)
        assert sp.zone == "outside box"

    def test_long_range(self):
        sp = ShotPosition.from_raw(55, 50)
        assert sp.zone == "long range"

    def test_other_end_central(self):
        """Shot from the other end of the pitch."""
        sp = ShotPosition.from_raw(6, 58)
        assert sp.zone == "inside box"

    def test_distance_calculation(self):
        """4m header from Valencia — (97,52)."""
        sp = ShotPosition.from_raw(97, 52)
        assert sp.distance_m < 5.0
        assert sp.zone == "6-yard box"
