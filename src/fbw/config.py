"""
Configuration loader for football-wire.

Layered config: defaults → config file → env vars → CLI args.
Config file format: TOML (stdlib tomllib, Python 3.11+).

Search order for config file:
  1. Explicit path (passed to load_config)
  2. FBW_CONFIG env var
  3. ./fbw.config.toml (current directory)
  4. ~/.config/fbw/config.toml (user config)
"""

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


# --- Data structures ---

@dataclass
class SourceConfig:
    """Configuration for a data source (e.g. FIFA API)."""
    name: str = "fifa"
    base_url: str = "https://api.fifa.com/api/v3"
    competition_id: str = "17"
    season_id: str = "285023"
    stage_id: str = "289273"
    poll_interval: int = 10
    trust: str = "low"


@dataclass
class PathsConfig:
    """Data directory paths. Derived from data_dir."""
    data_dir: Path = field(default_factory=lambda: Path("data"))
    venv: str = ""              # path to Python venv (for shell wrappers)

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def raw_api_dir(self) -> Path:
        return self.data_dir / "raw" / "api-fifa"

    @property
    def raw_matches_dir(self) -> Path:
        return self.data_dir / "raw" / "api-fifa" / "matches"

    @property
    def raw_events_dir(self) -> Path:
        return self.data_dir / "raw" / "api-fifa" / "events"

    @property
    def raw_timelines_dir(self) -> Path:
        return self.data_dir / "raw" / "api-fifa" / "timelines"

    @property
    def raw_enrichments_dir(self) -> Path:
        return self.data_dir / "raw" / "api-fifa" / "enrichments"

    @property
    def processed_dir(self) -> Path:
        return self.data_dir / "processed"

    @property
    def processed_events_dir(self) -> Path:
        return self.data_dir / "processed" / "events"

    @property
    def processed_matches_dir(self) -> Path:
        return self.data_dir / "processed" / "matches"


@dataclass
class EspnConfig:
    """ESPN stats source configuration."""
    enabled: bool = False
    base_url: str = "https://site.api.espn.com/apis/site/v2/sports/soccer"
    league: str = "fifa.world"
    poll_interval: int = 60


@dataclass
class SourcesConfig:
    """Optional data source toggles (legacy — prefer EspnConfig)."""
    espn: bool = False          # kept for backward compat with old local configs
    espn_interval: int = 60


@dataclass
class TournamentConfig:
    """Which tournament data to load."""
    name: str = "wc2026"
    rules: str = "wc2026.toml"
    data_dir: str = "wc2026-data"
    lore_dir: str = "wc2026-lore"

    @property
    def base_path(self) -> Path:
        """Tournament base directory under data/static/tournaments/."""
        return Path("data/static/tournaments")

    @property
    def rules_path(self) -> Path:
        return self.base_path / self.rules

    @property
    def data_path(self) -> Path:
        return self.base_path / self.data_dir

    @property
    def lore_path(self) -> Path:
        return self.base_path / self.lore_dir


@dataclass
class DisplayConfig:
    """Presentation settings for clients."""
    delay: int = 0              # anti-spoiler delay in seconds
    stats_interval: int = 15    # match minutes between stats blocks (0 = disabled)
    preamble: bool = True       # emit preamble before match header


@dataclass
class Config:
    """Top-level application configuration."""
    paths: PathsConfig = field(default_factory=PathsConfig)
    source: SourceConfig = field(default_factory=SourceConfig)
    espn: EspnConfig = field(default_factory=EspnConfig)
    sources: SourcesConfig = field(default_factory=SourcesConfig)  # legacy compat
    tournament: TournamentConfig = field(default_factory=TournamentConfig)
    display: DisplayConfig = field(default_factory=DisplayConfig)


# --- Loader ---

def _find_config_files(explicit_path: str | Path | None = None) -> list[Path]:
    """Locate config files in layering order (first = base, last = override).

    Returns list of existing config files to apply in order.
    Later files override earlier ones.
    """
    files = []

    # 1. User config (lowest priority)
    user = Path.home() / ".config" / "fbw" / "config.toml"
    if user.exists():
        files.append(user)

    # 2. Project config
    cwd = Path.cwd() / "fbw.config.toml"
    if cwd.exists():
        files.append(cwd)

    # 3. Local override (gitignored, personal settings)
    local = Path.cwd() / "fbw.config.local.toml"
    if local.exists():
        files.append(local)

    # 4. Env var (overrides project + local)
    env_path = os.environ.get("FBW_CONFIG")
    if env_path:
        p = Path(env_path)
        if p.exists():
            files.append(p)

    # 5. Explicit path (highest priority)
    if explicit_path:
        p = Path(explicit_path)
        if p.exists():
            files.append(p)

    return files


def _apply_toml(config: Config, data: dict) -> None:
    """Apply parsed TOML data onto config dataclass."""
    # [paths]
    if "paths" in data:
        paths = data["paths"]
        if "data_dir" in paths:
            config.paths.data_dir = Path(paths["data_dir"])
        if "venv" in paths:
            config.paths.venv = paths["venv"]

    # [source] or [source.fifa]
    source_data = data.get("source", {})
    if "fifa" in source_data:
        source_data = source_data["fifa"]
    if source_data:
        for key in ("base_url", "competition_id", "season_id", "stage_id",
                     "poll_interval", "trust", "name"):
            if key in source_data:
                setattr(config.source, key, source_data[key])

    # [source.espn]
    espn_data = data.get("source", {}).get("espn", {})
    if espn_data:
        for key in ("enabled", "base_url", "league", "poll_interval"):
            if key in espn_data:
                setattr(config.espn, key, espn_data[key])

    # [sources] — legacy compat
    if "sources" in data:
        sources = data["sources"]
        if "espn" in sources:
            config.sources.espn = sources["espn"]
            config.espn.enabled = sources["espn"]  # sync to new config
        if "espn_interval" in sources:
            config.sources.espn_interval = sources["espn_interval"]
            config.espn.poll_interval = sources["espn_interval"]

    # [tournament]
    if "tournament" in data:
        tourn = data["tournament"]
        for key in ("name", "rules", "data_dir", "lore_dir"):
            if key in tourn:
                setattr(config.tournament, key, tourn[key])

    # [display]
    if "display" in data:
        display = data["display"]
        for key in ("delay", "stats_interval", "preamble"):
            if key in display:
                setattr(config.display, key, display[key])


def _apply_env(config: Config) -> None:
    """Apply environment variable overrides."""
    env_data_dir = os.environ.get("FBW_DATA_DIR")
    if env_data_dir:
        config.paths.data_dir = Path(env_data_dir)

    env_poll = os.environ.get("FBW_POLL_INTERVAL")
    if env_poll:
        config.source.poll_interval = int(env_poll)

    env_delay = os.environ.get("FBW_DELAY")
    if env_delay:
        config.display.delay = int(env_delay)


def load_config(config_path: str | Path | None = None) -> Config:
    """Load configuration with full layering.

    defaults → user config → project config → local config → env var → explicit path → env vars
    CLI args are applied by the caller after this returns.
    """
    config = Config()

    # Apply config files in order (later overrides earlier)
    for path in _find_config_files(config_path):
        with open(path, "rb") as f:
            toml_data = tomllib.load(f)
        _apply_toml(config, toml_data)

    # Env vars (override everything except CLI args)
    _apply_env(config)

    return config


# --- Singleton ---

_config: Config | None = None


def get_config() -> Config:
    """Cached config access. Call load_config() first for explicit path."""
    global _config
    if _config is None:
        _config = load_config()
    return _config


def init_config(config_path: str | Path | None = None) -> Config:
    """Initialize config with explicit path. Resets the singleton."""
    global _config
    _config = load_config(config_path)
    return _config
