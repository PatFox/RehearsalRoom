"""Persistent application settings stored in ~/.rehearsalroom/settings.json."""

import builtins
import json
from pathlib import Path

_SETTINGS_DIR = Path.home() / ".rehearsalroom"
_SETTINGS_FILE = _SETTINGS_DIR / "settings.json"
_DEFAULT_LIBRARY = Path.home() / "Music" / "RehearsalRoom"

_DEFAULTS = {
    "library_path": str(_DEFAULT_LIBRARY),
    "acoustid_api_key": "",
    "vidami_enabled": False,
}


def load() -> dict:
    if _SETTINGS_FILE.exists():
        try:
            with open(_SETTINGS_FILE, encoding="utf-8") as f:
                data = json.load(f)
            return {**_DEFAULTS, **data}
        except Exception:
            pass
    return dict(_DEFAULTS)


def save(settings: dict) -> None:
    import os
    _SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    # Write-then-rename so a crash mid-write can't corrupt the file
    # (it also holds favourites and play history).
    tmp = _SETTINGS_FILE.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)
    os.replace(tmp, _SETTINGS_FILE)


def get(key: str):
    return load().get(key, _DEFAULTS.get(key))


def set(key: str, value) -> None:
    data = load()
    data[key] = value
    save(data)


def library_path() -> Path:
    return Path(load()["library_path"])


def get_last_viewed() -> dict:
    """Return {song_id: unix_timestamp} of last-viewed times."""
    return dict(load().get("last_viewed", {}))


def record_viewed(song_id: str) -> None:
    """Record that *song_id* was just opened (stores current time)."""
    import time
    data = load()
    lv = data.get("last_viewed", {})
    lv[song_id] = time.time()
    data["last_viewed"] = lv
    save(data)


def get_favourites() -> builtins.set:
    """Return the set of favourited song IDs (stems_path strings)."""
    return builtins.set(load().get("favourites", []))


def set_favourites(favs: set) -> None:
    """Persist the set of favourited song IDs."""
    data = load()
    data["favourites"] = list(favs)
    save(data)
