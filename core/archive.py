"""Archiving: move a track out of the library as a re-splittable .rrs template.

An archived track is exported with save_template() into an ``archive``
subdirectory of the library, and its .stems file is deleted. The Archived view
lists these and can restore them by re-importing the template.
"""

from __future__ import annotations

import os
from pathlib import Path

from core import settings as S
from core.project import save_template, read_manifest
from core.library import _GRAD_PALETTE, _added_label


def archive_dir() -> Path:
    return S.library_path() / "archive"


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix, parent = path.stem, path.suffix, path.parent
    i = 2
    while True:
        cand = parent / f"{stem} ({i}){suffix}"
        if not cand.exists():
            return cand
        i += 1


def archive_track(stems_path) -> Path:
    """Export *stems_path* as a .rrs template into the archive dir, then delete
    the original .stems. Returns the archive path. Raises (via save_template)
    if the track can't be regenerated (no source URL / embedded original)."""
    stems_path = Path(stems_path)
    dest_dir = archive_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = _unique(dest_dir / (stems_path.stem + ".rrs"))
    save_template(stems_path, dest)   # raises if not re-splittable → .stems kept
    os.remove(stems_path)
    return dest


def list_archived() -> list[dict]:
    """Return song-like dicts for every .rrs in the archive dir, newest first."""
    d = archive_dir()
    if not d.exists():
        return []
    out: list[dict] = []
    for p in d.glob("*.rrs"):
        try:
            m = read_manifest(p)
            mtime = p.stat().st_mtime
            size = p.stat().st_size
        except Exception:
            continue
        seed = abs(hash(m.title or p.stem)) % 9000 + 1000
        out.append({
            "id": str(p),
            "title": m.title or p.stem,
            "artist": m.artist or "Unknown artist",
            "seed": seed,
            "grad": _GRAD_PALETTE[seed % len(_GRAD_PALETTE)],
            "durationMs": m.duration_ms,
            "file_size": size,
            "addedLabel": _added_label(mtime),
            "source": "youtube" if m.source_url else "file",
            "rrs_path": str(p),
            "_mtime": mtime,
        })
    out.sort(key=lambda s: s.get("_mtime", 0), reverse=True)
    return out
