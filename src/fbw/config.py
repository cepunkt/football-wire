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
    def processed_dir(self) -> Path:
        return self.data_dir / "processed"

    @property
    def processed_events_dir(self) -> Path:
        return self.data_dir / "processed" / "events"

    @property
    def processed_matches_dir(self) -> Path:
        return self.data_dir / "processed" / "matches"


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
    display: DisplayConfig = field(default_factory=DisplayConfig)


# --- Loader ---

def _find_config_file(explicit_path: str | Path | None = None) -> Path | None:
    """Locate config file using search order."""
    # 1. Explicit path
    if explicit_path:
        p = Path(explicit_path)
        if p.exists():
            return p

    # 2. Env var
    env_path = os.environ.get("FBW_CONFIG")
    if env_path:
        p = Path(env_path)
        if p.exists():
            return p

    # 3. Current directory
    cwd = Path.cwd() / "fbw.config.toml"
    if cwd.exists():
        return cwd

    # 4. User config
    user = Path.home() / ".config" / "fbw" / "config.toml"
    if user.exists():
        return user

    return None


def _apply_toml(config: Config, data: dict) -> None:
    """Apply parsed TOML data onto config dataclass."""
    # [paths]
    if "paths" in data:
        paths = data["paths"]
        if "data_dir" in paths:
            config.paths.data_dir = Path(paths["data_dir"])

    # [source] or [source.fifa]
    source_data = data.get("source", {})
    if "fifa" in source_data:
        source_data = source_data["fifa"]
    if source_data:
        for key in ("base_url", "competition_id", "season_id", "stage_id",
                     "poll_interval", "trust", "name"):
            if key in source_data:
                setattr(config.source, key, source_data[key])

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

    defaults → config file → env vars
    CLI args are applied by the caller after this returns.
    """
    config = Config()

    # Config file
    path = _find_config_file(config_path)
    if path:
        with open(path, "rb") as f:
            toml_data = tomllib.load(f)
        _apply_toml(config, toml_data)

    # Env vars
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
