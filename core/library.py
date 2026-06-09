"""Scan the library directory and build song dicts from .stems files."""

import time
from pathlib import Path

from core.project import read_manifest
from core.settings import library_path as get_library_path

_GRAD_PALETTE = [
    ["#FF5A5F", "#7C5CFF"],
    ["#15B6A4", "#2E6BFF"],
    ["#F2A23A", "#FF5A5F"],
    ["#7C5CFF", "#15B6A4"],
    ["#2E6BFF", "#F2A23A"],
    ["#E2456B", "#F2A23A"],
    ["#0E9F6E", "#2E6BFF"],
]


def _added_label(mtime: float) -> str:
    age = time.time() - mtime
    days = age / 86400
    if days < 1:
        return "Today"
    if days < 2:
        return "Yesterday"
    if days < 7:
        return f"{int(days)} days ago"
    if days < 14:
        return "Last week"
    if days < 30:
        return f"{int(days / 7)} weeks ago"
    return f"{int(days / 30)} months ago"


def song_from_stems_file(stems_path: Path) -> dict | None:
    """Read a .stems file and return a song dict suitable for the library, or None on error."""
    try:
        manifest = read_manifest(stems_path)
        seed = abs(hash(manifest.title or stems_path.stem)) % 9000 + 1000
        grad = _GRAD_PALETTE[seed % len(_GRAD_PALETTE)]
        mtime = stems_path.stat().st_mtime
        file_size = stems_path.stat().st_size
        return {
            "id": str(stems_path),
            "title": manifest.title or stems_path.stem,
            "artist": manifest.artist or "Unknown artist",
            "seed": seed,
            "durationMs": manifest.duration_ms,
            "addedLabel": _added_label(mtime),
            "source": manifest.source_url and "youtube" or "file",
            "source_url": manifest.source_url or "",
            "grad": grad,
            "stems_path": str(stems_path),
            "file_size": file_size,
            "_mtime": mtime,
        }
    except Exception:
        return None


def scan(directory: Path | None = None) -> list[dict]:
    """Return song dicts for all .stems files in the library directory, newest first."""
    lib = Path(directory) if directory else get_library_path()
    if not lib.exists():
        return []
    songs = []
    for p in lib.glob("*.stems"):
        song = song_from_stems_file(p)
        if song:
            songs.append(song)
    songs.sort(key=lambda s: s.get("_mtime", 0), reverse=True)
    return songs
