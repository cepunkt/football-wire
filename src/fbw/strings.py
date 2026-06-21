"""
Localised display string loader.

Loads fbw enrichment strings from YAML files. These are OUR strings
for markers, zone names, and position descriptions — NOT API strings.
API descriptions are passed through untouched.

Usage:
    from fbw.strings import S
    S.marker("goal")         → ">>GOAL<<"  or ">>TOR!<<"
    S.zone("six_yard")       → "6-yard box" or "Fünfmeterraum"
    S.format("shot_position", distance=23, zone="inside box", ...)
"""

import yaml
from pathlib import Path

_STRINGS_DIR = Path(__file__).parent.parent.parent / "data" / "static" / "strings"
_loaded: dict = {}
_locale: str = "en"


def init_strings(locale: str = "en", strings_dir: Path | None = None):
    """Load strings for a locale. Call once at startup."""
    global _loaded, _locale
    d = strings_dir or _STRINGS_DIR
    _locale = locale

    path = d / f"{locale}.yaml"
    if not path.exists():
        # Fallback to English
        path = d / "en.yaml"

    if path.exists():
        with open(path) as f:
            _loaded = yaml.safe_load(f) or {}
    else:
        _loaded = {}


class S:
    """String lookup — thin wrapper over loaded YAML."""

    @staticmethod
    def marker(key: str) -> str:
        """Event marker string."""
        return _loaded.get("markers", {}).get(key, key.upper())

    @staticmethod
    def zone(key: str) -> str:
        """Shot zone name."""
        return _loaded.get("zones", {}).get(key, key)

    @staticmethod
    def pitch_zone(key: str) -> str:
        """Neutral pitch zone name."""
        return _loaded.get("pitch_zones", {}).get(key, key)

    @staticmethod
    def side(key: str) -> str:
        """Side name."""
        return _loaded.get("sides", {}).get(key, key)

    @staticmethod
    def placement_height(key: str) -> str:
        """Goal placement height."""
        return _loaded.get("placement", {}).get("height", {}).get(key, key)

    @staticmethod
    def placement_side(key: str) -> str:
        """Goal placement side."""
        return _loaded.get("placement", {}).get("side", {}).get(key, key)

    @staticmethod
    def fmt(key: str) -> str:
        """Format template string."""
        return _loaded.get("formats", {}).get(key, "")

    @staticmethod
    def locale() -> str:
        return _locale


# Auto-init with English on import
init_strings("en")
