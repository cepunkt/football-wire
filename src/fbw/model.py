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
        return self in (EventType.GOAL, EventType.PENALTY_GOAL)

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
PITCH_LENGTH = 105.0  # metres
PITCH_WIDTH = 68.0    # metres


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
    distance_m: float       # distance from goal centre in metres
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
        dy_m = (ny - 50.0) / 100.0 * PITCH_WIDTH
        distance_m = (depth_m ** 2 + dy_m ** 2) ** 0.5

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
    """Where the ball hit the goal frame. From API GoalGatePosition."""
    raw_x: float
    raw_y: float

    @property
    def height(self) -> str:
        if self.raw_y < 15:
            return "low"
        elif self.raw_y > 50:
            return "high"
        return "mid-height"

    @property
    def side(self) -> str:
        if self.raw_x < 35:
            return "left"
        elif self.raw_x > 65:
            return "right"
        return "centre"


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
