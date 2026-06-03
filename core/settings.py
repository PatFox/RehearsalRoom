"""Persistent application settings stored in ~/.rehearsalroom/settings.json."""

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
    _SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    with open(_SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)


def get(key: str):
    return load().get(key, _DEFAULTS.get(key))


def set(key: str, value) -> None:
    data = load()
    data[key] = value
    save(data)


def library_path() -> Path:
    return Path(load()["library_path"])
