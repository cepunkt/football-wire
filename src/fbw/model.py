"""
Data model for football-wire.

Core types representing match data after processing. These are what
the application works with — not raw API dicts, not display strings.
The processing layer creates these from raw data. The format layer
reads them for presentation.

Invariants are checked here, not enforced. Violations are marked with
trust levels, not silently corrected or rejected. Bad data is evidence.
"""

from dataclasses import dataclass, field
from enum import IntEnum, Enum
from typing import ClassVar


# --- FIFA API localization ---

def get_localized(name_list, fallback: str = "?") -> str:
    """Extract en-GB or first available description from FIFA API localized list.

    The FIFA API returns localized strings as:
      [{"Locale": "en-GB", "Description": "..."}, ...]
    This extracts the English description, falling back to first available.
    """
    if not name_list:
        return fallback
    for entry in name_list:
        if isinstance(entry, dict) and entry.get("Locale") in ("en-GB", "en"):
            return entry.get("Description", fallback)
    if isinstance(name_list[0], dict):
        return name_list[0].get("Description", fallback)
    return str(name_list[0])


# --- Trust levels ---

class Trust(Enum):
    """How much to trust a data point."""
    TRUSTED = "trusted"      # from roster or verified source
    INFERRED = "inferred"    # computed from events (e.g. on_pitch tracking)
    SUSPECT = "suspect"      # contradicts an invariant
    UNKNOWN = "unknown"      # missing or unresolvable


# --- Enums ---

class Position(IntEnum):
    """Player position. Values match FIFA API."""
    GK = 0
    DF = 1
    MF = 2
    FW = 3

    def __str__(self) -> str:
        return self.name


class MatchStatus(IntEnum):
    """Match lifecycle status. Values match FIFA API."""
    FINISHED = 0
    SCHEDULED = 1
    LIVE = 3

    def __str__(self) -> str:
        return self.name.lower()


class EventType(IntEnum):
    """Match event types. Values match FIFA API timeline."""
    GOAL = 0
    ASSIST = 1
    YELLOW = 2
    RED = 3
    SECOND_YELLOW_RED = 4
    SUB = 5
    PENALTY_AWARDED = 6
    PERIOD_START = 7
    PERIOD_END = 8
    SHOT = 12
    OFFSIDE = 15
    CORNER = 16
    FOUL = 18
    OWN_GOAL = 34
    PENALTY_GOAL = 41
    SAVE = 57
    INJURY = 70
    VAR = 71
    DELAY = 77
    RESUME = 78
    COIN = 79
    COIN_SIDE = 80
    PAUSE = 83

    @property
    def is_goal(self) -> bool:
        return self in (EventType.GOAL, EventType.PENALTY_GOAL, EventType.OWN_GOAL)

    @property
    def is_card(self) -> bool:
        return self in (EventType.YELLOW, EventType.RED, EventType.SECOND_YELLOW_RED)

    @property
    def is_structural(self) -> bool:
        """Events about match flow, not play."""
        return self in (EventType.PERIOD_START, EventType.PERIOD_END,
                        EventType.RESUME, EventType.COIN, EventType.COIN_SIDE,
                        EventType.PAUSE, EventType.DELAY)


# --- Minute ---

@dataclass(frozen=True)
class Minute:
    """Parsed match minute.

    raw: the original string from the API ("45'+3'", "16'", "")
    value: sortable numeric value (48.0, 16.0, -1.0)

    The minute indicates the minute being played, not minutes elapsed.
    45' means "in the 45th minute" — clock shows 44:xx.
    """
    raw: str
    value: float

    @classmethod
    def parse(cls, raw: str, description: str = "") -> "Minute":
        """Parse API MatchMinute string.

        Uses event description for context when minute is empty
        (e.g. half-time subs say "before the second half").
        """
        if not raw:
            if "second half" in description.lower():
                return cls(raw="", value=45.5)
            if "extra time" in description.lower():
                return cls(raw="", value=90.5)
            return cls(raw="", value=-1.0)

        s = raw.replace("'", "").strip()
        if "+" in s:
            parts = s.split("+")
            try:
                return cls(raw=raw, value=float(parts[0]) + float(parts[1]))
            except (ValueError, IndexError):
                pass
        try:
            return cls(raw=raw, value=float(s))
        except ValueError:
            return cls(raw=raw, value=-1.0)

    def __str__(self) -> str:
        return self.raw

    def __lt__(self, other: "Minute") -> bool:
        return self.value < other.value


# --- Coordinates ---

# FIFA World Cup standard pitch dimensions
PITCH_LENGTH = 105.0           # metres
PITCH_WIDTH = 68.0             # metres
GOAL_WIDTH_M = 7.32            # metres (post to post)
GOAL_HEIGHT_M = 2.44           # metres (ground to crossbar)
SIX_YARD_BOX_WIDTH_M = 5.5     # metres (each side of goal)
SIX_YARD_BOX_DEPTH_M = 5.5     # metres (from goal line)

# Gate coordinate frame (calibrated from WC2026 match data)
# X: 0-100 across 6-yard box width. Posts at X≈30 and X≈70.
# Y: 0-100 from ground. Crossbar at Y≈45.2.
# Calibrated from:
#   - Haaland crossbar hit NOR-SEN 58': Y=45.22 → 2.44m (confirmed by eye)
#   - Balogun post hit PAR-USA 45'+5': Y=42.44 → 2.29m = 15cm below bar (confirmed)
#   - 28/28 goals within X=30-70 (inside posts)
GATE_FRAME_WIDTH_M = SIX_YARD_BOX_WIDTH_M * 2 + GOAL_WIDTH_M  # 18.32m
GATE_FRAME_HEIGHT_M = 5.4      # calibrated, not a pitch dimension


@dataclass(frozen=True)
class ShotPosition:
    """Shot coordinates, both raw and normalized.

    Raw: 0-100 absolute grid from the API.
    Normalized: attacker's perspective looking at goal.
      X: 0 = goal line, increasing = away from goal
      Y: > 50 = attacker's left, < 50 = attacker's right
    """
    raw_x: float
    raw_y: float
    norm_x: float
    norm_y: float
    distance_m: float       # distance to nearest point of goal frame
    depth_m: float          # perpendicular distance from goal line

    @classmethod
    def from_raw(cls, x: float, y: float) -> "ShotPosition":
        """Create from raw API coordinates (0-100 scale)."""
        # Normalize to attacker's perspective
        if x > 50.0:
            nx, ny = 100.0 - x, y          # flip X, keep Y (facing same as convention)
        else:
            nx, ny = x, 100.0 - y          # keep X, flip Y (facing opposite)

        depth_m = nx / 100.0 * PITCH_LENGTH
        dy_m = abs((ny - 50.0) / 100.0 * PITCH_WIDTH)
        half_goal = GOAL_WIDTH_M / 2   # 3.66m

        # Distance to nearest point of the goal frame:
        # - Inside goal width: straight line = depth
        # - Outside goal width: triangle to nearest post
        if dy_m <= half_goal:
            distance_m = depth_m
        else:
            excess = dy_m - half_goal
            distance_m = (depth_m ** 2 + excess ** 2) ** 0.5

        return cls(raw_x=x, raw_y=y, norm_x=nx, norm_y=ny,
                   distance_m=distance_m, depth_m=depth_m)

    @property
    def zone(self) -> str:
        """Pitch zone based on depth AND width.

        The 6-yard box and penalty area have width constraints —
        a shot from the corner flag is near the goal line (low depth)
        but outside any box (extreme width).

        Width checks use normalised Y: 50 = central, 0/100 = touchlines.
        6-yard box:    18.3m wide centred → norm_y ~36.5 to ~63.5
        Penalty area:  40.3m wide centred → norm_y ~20.4 to ~79.6
        """
        # Width boundaries (normalised 0-100 scale)
        IN_SIX_YARD_Y = (36.5, 63.5)
        IN_PENALTY_Y = (20.4, 79.6)

        in_six_yard_width = IN_SIX_YARD_Y[0] <= self.norm_y <= IN_SIX_YARD_Y[1]
        in_penalty_width = IN_PENALTY_Y[0] <= self.norm_y <= IN_PENALTY_Y[1]

        from .strings import S
        if self.depth_m <= 5.5 and in_six_yard_width:
            return S.zone("six_yard")
        elif self.depth_m <= 16.5 and in_penalty_width:
            return S.zone("inside_box")
        elif self.depth_m <= 24:
            return S.zone("edge")
        elif self.depth_m <= 35:
            return S.zone("outside")
        return S.zone("long_range")

    @property
    def side(self) -> str:
        """Width from attacker's perspective. High Y = left, low Y = right."""
        if self.norm_y > 60:
            return "left"
        elif self.norm_y < 40:
            return "right"
        return "central"


@dataclass(frozen=True)
class GoalPlacement:
    """Where the ball hit the goal frame. From API GoalGatePosition.

    Coordinates map to the 6-yard box from the attacker's perspective:
      X: 0-100 across the 6-yard box width (18.32m)
         Posts at X≈30 and X≈70. Inside goal = 30-70.
      Y: 0-100 from ground up to gate frame height (5.4m, calibrated)
         Crossbar at Y≈45.2. In frame = <45.2.

    Note: gate Y on goals may reflect net impact, not line crossing.
    A low slide-in that hits the roof of the net will show high Y.
    Only non-goal shots and confirmed crossbar hits are reliable
    for crossbar calibration.

    Calibrated from WC2026 match data:
      - 28/28 goals fall within X=30-70 (inside posts)
      - Haaland crossbar hit NOR-SEN 58' Y=45.22 → 2.44m (confirmed by eye)
      - Balogun post hit PAR-USA 45'+5' Y=42.44 → 2.29m = 15cm below bar (confirmed)
      - Monteiro over URU-CPV 59' Y=47.21 → 2.55m = 11cm over bar (control)
      - Messi penalty miss X=70.3 → just wide of right post (confirmed)

    Bad data (excluded from calibration):
      - Haaland slide-in goal IRQ-NOR 29' Y=45.72: ball crossed line low,
        rose into roof of net. Gate Y reflects net impact, not line crossing.
    """
    raw_x: float
    raw_y: float

    # Gate coordinate frame (calibrated from match data)
    _FRAME_WIDTH = GATE_FRAME_WIDTH_M                       # 18.32m
    _FRAME_HEIGHT = GATE_FRAME_HEIGHT_M                     # 5.4m (calibrated)
    _POST_LEFT = SIX_YARD_BOX_WIDTH_M / _FRAME_WIDTH * 100   # ~30%
    _POST_RIGHT = (SIX_YARD_BOX_WIDTH_M + GOAL_WIDTH_M) / _FRAME_WIDTH * 100  # ~70%
    _CROSSBAR = GOAL_HEIGHT_M / _FRAME_HEIGHT * 100          # ~45.2%

    # Proximity thresholds
    _NEAR_POST_M = 0.25           # metres — "close to post"
    _NEAR_BAR_M = 0.20            # metres — "close to crossbar"

    @property
    def offset_m(self) -> float:
        """Distance from centre of goal in metres."""
        return abs(self.raw_x - 50) / 100 * self._FRAME_WIDTH

    @property
    def height_m(self) -> float:
        """Height from ground in metres."""
        return self.raw_y / 100 * self._FRAME_HEIGHT

    # Tolerance for crossbar boundary (impacts register fractionally
    # above due to measurement noise — Haaland crossbar hit Y=45.22
    # vs threshold 45.19, a 0.03 unit / 2mm gap)
    _BAR_TOLERANCE = 0.5          # raw Y units (~2.7cm real)

    @property
    def in_goal(self) -> bool:
        """Whether the ball entered the goal frame.

        Posts use exact boundary (well-calibrated, 28/28 goals confirm).
        Crossbar includes small tolerance for impact measurement noise.
        """
        in_x = self._POST_LEFT <= self.raw_x <= self._POST_RIGHT
        in_y = self.raw_y <= (self._CROSSBAR + self._BAR_TOLERANCE)
        return in_x and in_y

    @property
    def nearest_post_m(self) -> float:
        """Distance to the nearest post in metres.

        Negative if outside the goal (wide of the post).
        """
        post_half = GOAL_WIDTH_M / 2   # 3.66m
        return post_half - self.offset_m

    @property
    def crossbar_distance_m(self) -> float:
        """Distance below the crossbar in metres.

        Negative if above the crossbar (over the bar).
        """
        return GOAL_HEIGHT_M - self.height_m

    @property
    def near_post(self) -> bool:
        """Whether the ball was within 25cm of a post."""
        return abs(self.nearest_post_m) <= self._NEAR_POST_M

    @property
    def near_bar(self) -> bool:
        """Whether the ball was within 20cm of the crossbar."""
        return abs(self.crossbar_distance_m) <= self._NEAR_BAR_M

    @property
    def kreuzeck(self) -> bool:
        """Whether the ball hit near the junction of post and crossbar.

        The holy corner — Kreuzeck in Austrian German.
        """
        return self.near_post and self.near_bar

    @property
    def height(self) -> str:
        if self.raw_y > self._CROSSBAR:
            if self.in_goal:
                return "high"      # crossbar impact that went in
            return "over"
        elif self.raw_y < 15:
            return "low"
        elif self.raw_y > 35:
            return "high"
        return "mid-height"

    @property
    def side(self) -> str:
        """Side from attacker's perspective within the 6-yard box.

        Posts at X≈30 (left) and X≈70 (right).
        """
        if self.raw_x < self._POST_LEFT:
            return "wide left"
        elif self.raw_x > self._POST_RIGHT:
            return "wide right"
        elif self.raw_x < 45:
            return "left"
        elif self.raw_x > 55:
            return "right"
        return "centre"

    def side_relative(self, shooter_side: str) -> str:
        """Side as near/far post relative to shooter position.

        shooter_side: "left", "right", or "central" from ShotPosition.side.
        Returns: "near post", "far post", "centre", or "wide left"/"wide right".
        """
        base = self.side

        # Wide shots stay as-is
        if base.startswith("wide"):
            return base

        # Centre shots or central shooter — keep absolute side
        if base == "centre" or shooter_side == "central":
            return base

        # Near post = ball goes to same side as shooter
        # Far post = ball goes to opposite side
        same_side = (
            (shooter_side == "left" and base == "left") or
            (shooter_side == "right" and base == "right")
        )
        if same_side:
            return "near post"
        return "far post"


# --- Core entities ---

@dataclass
class Player:
    """A player in the match roster."""
    id: str
    name: str
    shirt_number: int | None = None
    position: Position | None = None
    is_starter: bool = False
    team_abbr: str = ""

    @property
    def display_name(self) -> str:
        """Formatted name: Name (N, POS)."""
        parts = []
        if self.shirt_number is not None:
            parts.append(str(self.shirt_number))
        if self.position is not None:
            parts.append(str(self.position))
        if parts:
            return f"{self.name} ({', '.join(parts)})"
        return self.name


@dataclass
class Team:
    """A team in the match."""
    id: str
    abbreviation: str
    name: str
    coaches: list[str] = field(default_factory=list)
    tactics: str = ""
    players: dict[str, Player] = field(default_factory=dict)  # player_id -> Player

    @property
    def starters(self) -> list[Player]:
        return [p for p in self.players.values() if p.is_starter]

    @property
    def substitutes(self) -> list[Player]:
        return [p for p in self.players.values() if not p.is_starter]


@dataclass
class Event:
    """A match event from the timeline."""
    event_id: str
    event_type: EventType
    minute: Minute
    description: str
    team_abbr: str = ""             # resolved from roster, not API
    team_trust: Trust = Trust.UNKNOWN
    player_id: str = ""
    player_name: str = ""
    sub_player_id: str = ""         # for subs: the other player
    home_goals: int | None = None
    away_goals: int | None = None
    shot_position: ShotPosition | None = None
    goal_placement: GoalPlacement | None = None

    # Sub resolution
    on_player_id: str = ""          # player coming ON (resolved)
    off_player_id: str = ""         # player going OFF (resolved)
    sub_trust: Trust = Trust.UNKNOWN

    # Processing metadata
    is_late: bool = False           # arrived after a later-minute event
    is_duplicate: bool = False      # content-matches another event
    logged_at: str = ""             # ISO timestamp from daemon

    @property
    def dedup_key(self) -> str:
        """Content-based key for duplicate detection."""
        return "|".join([
            str(self.event_type.value),
            self.minute.raw,
            self.team_abbr,
            str(self.home_goals or ""),
            str(self.away_goals or ""),
            self.player_id,
            self.description[:30],
        ])


@dataclass
class MatchStats:
    """Computed match statistics from event stream."""
    home_abbr: str
    away_abbr: str
    counters: dict[str, dict[str, int]] = field(default_factory=dict)

    def __post_init__(self):
        if not self.counters:
            empty = {"shots": 0, "goals": 0, "yellows": 0, "reds": 0,
                     "fouls": 0, "offsides": 0, "corners": 0, "saves": 0}
            self.counters = {
                self.home_abbr: dict(empty),
                self.away_abbr: dict(empty),
            }

    _STAT_TYPES: ClassVar[dict[EventType, str]] = {
        EventType.GOAL: "goals",
        EventType.PENALTY_GOAL: "goals",
        EventType.YELLOW: "yellows",
        EventType.RED: "reds",
        EventType.SECOND_YELLOW_RED: "reds",
        EventType.SHOT: "shots",
        EventType.OFFSIDE: "offsides",
        EventType.CORNER: "corners",
        EventType.FOUL: "fouls",
        EventType.SAVE: "saves",
    }

    def update(self, event: Event) -> None:
        """Update counters from an event."""
        stat_key = self._STAT_TYPES.get(event.event_type)
        if stat_key and event.team_abbr in self.counters:
            self.counters[event.team_abbr][stat_key] += 1


@dataclass
class Match:
    """Full match state."""
    match_id: str
    home: Team
    away: Team
    status: MatchStatus = MatchStatus.SCHEDULED
    home_score: int | None = None
    away_score: int | None = None

    # Venue
    stadium: str = ""
    city: str = ""
    attendance: int | None = None
    weather: str = ""
    kickoff_local: str = ""

    # Officials
    referee: str = ""
    referee_country: str = ""

    # State tracking
    on_pitch: set[str] = field(default_factory=set)  # player IDs currently playing
    events: list[Event] = field(default_factory=list)
    stats: MatchStats | None = None

    def __post_init__(self):
        if not self.on_pitch:
            # Initialize from starters
            for p in self.home.starters:
                self.on_pitch.add(p.id)
            for p in self.away.starters:
                self.on_pitch.add(p.id)
        if self.stats is None:
            self.stats = MatchStats(self.home.abbreviation, self.away.abbreviation)

    def player_by_id(self, player_id: str) -> Player | None:
        """Look up player from either team."""
        return (self.home.players.get(player_id) or
                self.away.players.get(player_id))

    def team_for_player(self, player_id: str) -> str:
        """Get team abbreviation for a player ID."""
        if player_id in self.home.players:
            return self.home.abbreviation
        if player_id in self.away.players:
            return self.away.abbreviation
        return ""

    def resolve_sub(self, pid_a: str, pid_b: str) -> tuple[str, str, Trust]:
        """Determine which player is ON and which is OFF.

        Uses on_pitch tracking (most reliable). Falls back to API
        convention (pid_a = ON, pid_b = OFF) if tracking can't determine.

        Returns (on_id, off_id, trust_level).
        """
        a_on = pid_a in self.on_pitch
        b_on = pid_b in self.on_pitch

        if a_on and not b_on:
            return pid_b, pid_a, Trust.TRUSTED
        elif b_on and not a_on:
            return pid_a, pid_b, Trust.TRUSTED
        else:
            # Both on pitch or neither — can't determine
            return pid_a, pid_b, Trust.SUSPECT

    def apply_sub(self, on_id: str, off_id: str) -> None:
        """Update on_pitch tracking after a substitution."""
        self.on_pitch.discard(off_id)
        if on_id:
            self.on_pitch.add(on_id)

    # --- Invariant checks ---

    def check_score_consistency(self, event: Event) -> Trust:
        """Check if event's score is consistent with known state."""
        if event.home_goals is None or event.away_goals is None:
            return Trust.UNKNOWN
        if self.home_score is not None and self.away_score is not None:
            # Score should be monotonic (only increase)
            if (event.home_goals < self.home_score or
                    event.away_goals < self.away_score):
                return Trust.SUSPECT
        return Trust.TRUSTED

    def check_team_attribution(self, event: Event) -> Trust:
        """Check if event's team matches roster lookup."""
        if not event.player_id:
            return Trust.UNKNOWN
        roster_team = self.team_for_player(event.player_id)
        if not roster_team:
            return Trust.UNKNOWN
        if event.team_abbr and event.team_abbr != roster_team:
            return Trust.SUSPECT
        return Trust.TRUSTED
