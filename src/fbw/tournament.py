"""
Tournament rules and data loader.

Rules: tournament-specific configuration from TOML files
(sub limits, format, discipline, VAR, hydration).

Data: canonical tournament data from worldcup.json (or similar
structured sources). Groups, teams, squads, stadiums. This is
our internal identity space — source-specific IDs (FIFA API,
ESPN) are mapped to these canonical IDs at the adapter boundary.

The sport invariants live in football.py. This module handles
what varies between competitions.
"""

import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class SubRules:
    """Substitution rules for this tournament."""
    max_subs: int = 5
    max_windows: int = 3
    concussion_sub: bool = True
    extra_time_subs: int = 0  # additional subs allowed in ET


@dataclass(frozen=True)
class StageFormat:
    """Rules for a tournament stage (group or knockout)."""
    extra_time: bool = False
    penalties: bool = False


@dataclass(frozen=True)
class DisciplineRules:
    """Discipline and accumulation rules."""
    yellows_for_ban: int = 2
    yellow_accumulation_reset: str = "quarter_final"


@dataclass(frozen=True)
class HydrationRules:
    """Hydration break rules."""
    enabled: bool = False
    typical_minutes: tuple[int, ...] = ()


@dataclass(frozen=True)
class VARRules:
    """VAR rules."""
    enabled: bool = False
    reviewable: tuple[str, ...] = ()


@dataclass(frozen=True)
class SourceIds:
    """API identifiers for this tournament."""
    fifa_competition_id: str = ""
    fifa_season_id: str = ""
    espn_league: str = ""


@dataclass(frozen=True)
class TournamentRules:
    """Complete tournament ruleset loaded from config.

    Provides typed access to all tournament-specific rules.
    The state machine and validation logic read from this
    instead of hardcoding WC2026 assumptions.
    """
    name: str = ""
    short_name: str = ""
    teams: int = 0

    subs: SubRules = field(default_factory=SubRules)
    group_stage: StageFormat = field(default_factory=StageFormat)
    knockout: StageFormat = field(default_factory=StageFormat)
    discipline: DisciplineRules = field(default_factory=DisciplineRules)
    hydration: HydrationRules = field(default_factory=HydrationRules)
    var: VARRules = field(default_factory=VARRules)
    sources: SourceIds = field(default_factory=SourceIds)

    def stage_allows_extra_time(self, is_knockout: bool) -> bool:
        """Check if the current stage allows extra time."""
        fmt = self.knockout if is_knockout else self.group_stage
        return fmt.extra_time

    def stage_allows_penalties(self, is_knockout: bool) -> bool:
        """Check if the current stage allows penalties."""
        fmt = self.knockout if is_knockout else self.group_stage
        return fmt.penalties

    def max_subs_for_phase(self, is_extra_time: bool) -> int:
        """Total subs allowed including any ET bonus."""
        if is_extra_time:
            return self.subs.max_subs + self.subs.extra_time_subs
        return self.subs.max_subs


def load_tournament(path: Path) -> TournamentRules:
    """Load tournament rules from a TOML file."""
    with open(path, "rb") as f:
        raw = tomllib.load(f)

    t = raw.get("tournament", {})
    sub = raw.get("substitutions", {})
    group = raw.get("format", {}).get("group_stage", {})
    ko = raw.get("format", {}).get("knockout", {})
    disc = raw.get("discipline", {})
    hydr = raw.get("hydration", {})
    var = raw.get("var", {})
    src_fifa = raw.get("sources", {}).get("fifa", {})
    src_espn = raw.get("sources", {}).get("espn", {})

    return TournamentRules(
        name=t.get("name", ""),
        short_name=t.get("short_name", ""),
        teams=t.get("teams", 0),
        subs=SubRules(
            max_subs=sub.get("max_subs", 5),
            max_windows=sub.get("max_windows", 3),
            concussion_sub=sub.get("concussion_sub", True),
            extra_time_subs=ko.get("extra_time_subs", 0),
        ),
        group_stage=StageFormat(
            extra_time=group.get("extra_time", False),
            penalties=group.get("penalties", False),
        ),
        knockout=StageFormat(
            extra_time=ko.get("extra_time", True),
            penalties=ko.get("penalties", True),
        ),
        discipline=DisciplineRules(
            yellows_for_ban=disc.get("yellows_for_ban", 2),
            yellow_accumulation_reset=disc.get(
                "yellow_accumulation_reset", "quarter_final"
            ),
        ),
        hydration=HydrationRules(
            enabled=hydr.get("enabled", False),
            typical_minutes=tuple(hydr.get("typical_minutes", [])),
        ),
        var=VARRules(
            enabled=var.get("enabled", False),
            reviewable=tuple(var.get("reviewable", [])),
        ),
        sources=SourceIds(
            fifa_competition_id=src_fifa.get("competition_id", ""),
            fifa_season_id=src_fifa.get("season_id", ""),
            espn_league=src_espn.get("league", ""),
        ),
    )


# --- Canonical tournament data ---
# Our internal identity space. Source-specific IDs (FIFA API, ESPN)
# are mapped to these canonical identifiers at the adapter boundary.

@dataclass(frozen=True)
class PlayerInfo:
    """A player in the registered squad."""
    name: str
    number: int
    position: str           # "GK", "DF", "MF", "FW"
    club: str = ""
    club_country: str = ""
    date_of_birth: str = ""  # ISO 8601

    @property
    def ref(self) -> str:
        """Canonical player reference: team_code-number."""
        # Set by the squad loader, not by the player itself
        return f"{self.number}"


@dataclass(frozen=True)
class TeamInfo:
    """A team in the tournament. Canonical identity."""
    name: str
    code: str               # FIFA code: "GER", "CIV", "AUT"
    group: str              # "A" through "L"
    confederation: str      # "UEFA", "CAF", "CONCACAF", etc.
    continent: str = ""
    squad: tuple[PlayerInfo, ...] = ()

    def player_by_number(self, number: int) -> PlayerInfo | None:
        """Look up a player by shirt number."""
        for p in self.squad:
            if p.number == number:
                return p
        return None

    def player_by_name(self, name: str) -> PlayerInfo | None:
        """Look up a player by name (case-insensitive substring match)."""
        name_lower = name.lower()
        for p in self.squad:
            if name_lower in p.name.lower():
                return p
        return None


@dataclass(frozen=True)
class GroupInfo:
    """A tournament group."""
    name: str               # "Group A", "Group E"
    letter: str             # "A", "E"
    team_codes: tuple[str, ...]


@dataclass(frozen=True)
class TournamentData:
    """Complete canonical tournament data.

    Loaded from structured data sources (worldcup.json etc.).
    This is our internal identity space. Everything else maps to it.
    """
    teams: dict[str, TeamInfo]          # keyed by FIFA code
    groups: dict[str, GroupInfo]         # keyed by group letter
    # Source ID mappings — built up as we encounter source data
    source_maps: dict[str, dict[str, str]] = field(
        default_factory=dict
    )  # {"fifa": {"43948": "GER"}, "espn": {"481": "GER"}}

    def team(self, code: str) -> TeamInfo | None:
        """Look up team by FIFA code."""
        return self.teams.get(code)

    def team_for_source_id(self, source: str, source_id: str) -> TeamInfo | None:
        """Look up team by a source-specific ID."""
        mapping = self.source_maps.get(source, {})
        code = mapping.get(source_id)
        if code:
            return self.teams.get(code)
        return None

    def register_source_id(self, source: str, source_id: str, code: str) -> None:
        """Record a mapping from source ID to canonical code.

        Called by adapters when they first encounter a source ID
        and resolve it to a canonical team.
        """
        if source not in self.source_maps:
            # TournamentData is frozen, but source_maps dict is mutable
            object.__setattr__(self, 'source_maps',
                               {**self.source_maps, source: {}})
        self.source_maps[source][source_id] = code

    def group_for_team(self, code: str) -> GroupInfo | None:
        """Find which group a team is in."""
        for g in self.groups.values():
            if code in g.team_codes:
                return g
        return None

    def group_teams(self, letter: str) -> list[TeamInfo]:
        """Get all teams in a group."""
        g = self.groups.get(letter)
        if not g:
            return []
        return [self.teams[c] for c in g.team_codes if c in self.teams]


def load_tournament_data(data_dir: Path) -> TournamentData:
    """Load canonical tournament data from worldcup.json format.

    Expects a directory containing:
      worldcup.teams.json   — team list with codes, groups, confederations
      worldcup.squads.json  — full 26-player squads
      worldcup.groups.json  — group assignments
    """
    teams: dict[str, TeamInfo] = {}
    groups: dict[str, GroupInfo] = {}

    # --- Teams ---
    teams_path = data_dir / "worldcup.teams.json"
    if teams_path.exists():
        with open(teams_path) as f:
            teams_raw = json.load(f)
        for t in teams_raw:
            code = t.get("fifa_code", "")
            if code:
                teams[code] = TeamInfo(
                    name=t.get("name", ""),
                    code=code,
                    group=t.get("group", ""),
                    confederation=t.get("confed", ""),
                    continent=t.get("continent", ""),
                )

    # --- Squads ---
    squads_path = data_dir / "worldcup.squads.json"
    if squads_path.exists():
        with open(squads_path) as f:
            squads_raw = json.load(f)
        for s in squads_raw:
            code = s.get("fifa_code", "")
            if code and code in teams:
                players = []
                for p in s.get("players", []):
                    club = p.get("club", {})
                    players.append(PlayerInfo(
                        name=p.get("name", ""),
                        number=p.get("number", 0),
                        position=p.get("pos", ""),
                        club=club.get("name", "") if isinstance(club, dict) else "",
                        club_country=club.get("country", "") if isinstance(club, dict) else "",
                        date_of_birth=p.get("date_of_birth", ""),
                    ))
                # Rebuild TeamInfo with squad attached
                existing = teams[code]
                teams[code] = TeamInfo(
                    name=existing.name,
                    code=existing.code,
                    group=existing.group,
                    confederation=existing.confederation,
                    continent=existing.continent,
                    squad=tuple(players),
                )

    # --- Groups ---
    groups_path = data_dir / "worldcup.groups.json"
    if groups_path.exists():
        with open(groups_path) as f:
            groups_raw = json.load(f)
        for g in groups_raw.get("groups", []):
            name = g.get("name", "")
            letter = name.replace("Group ", "")
            # Map team names to codes
            team_codes = []
            for team_name in g.get("teams", []):
                for code, info in teams.items():
                    if info.name == team_name:
                        team_codes.append(code)
                        break
            groups[letter] = GroupInfo(
                name=name,
                letter=letter,
                team_codes=tuple(team_codes),
            )

    return TournamentData(
        teams=teams,
        groups=groups,
    )
