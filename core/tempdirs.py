"""Tracked temporary directories/files so the app can clean up after itself.

Windows never clears %TEMP% automatically, and the app writes large WAV data
there (extracted stems, downloads, conversions). Every helper here registers
what it creates; `cleanup_all()` is called from MainWindow.closeEvent, and
`sweep_stale()` removes leftovers from previous runs at startup.
"""

from __future__ import annotations

import shutil
import tempfile
import time
from pathlib import Path

_PREFIX = "rehearsalroom_"
_created: list[Path] = []

# Leftovers older than this are deleted by the startup sweep. Generous enough
# that another running instance's active dirs are never touched.
_STALE_AGE_S = 2 * 24 * 3600


def make_temp_dir(suffix: str = "") -> Path:
    """Create and register a temp directory (rehearsalroom_<suffix>...)."""
    path = Path(tempfile.mkdtemp(prefix=f"{_PREFIX}{suffix}"))
    _created.append(path)
    return path


def make_temp_file(suffix: str = ".wav") -> Path:
    """Reserve and register a temp file path (created empty, caller overwrites)."""
    fd, name = tempfile.mkstemp(prefix=_PREFIX, suffix=suffix)
    import os
    os.close(fd)
    path = Path(name)
    _created.append(path)
    return path


def cleanup_all() -> None:
    """Delete everything registered this session. Safe to call repeatedly."""
    while _created:
        path = _created.pop()
        try:
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            elif path.exists():
                path.unlink()
        except OSError:
            pass


def sweep_stale() -> None:
    """Remove rehearsalroom_* temp entries from previous runs (best effort)."""
    now = time.time()
    try:
        tmp_root = Path(tempfile.gettempdir())
        for entry in tmp_root.glob(f"{_PREFIX}*"):
            try:
                if now - entry.stat().st_mtime < _STALE_AGE_S:
                    continue
                if entry.is_dir():
                    shutil.rmtree(entry, ignore_errors=True)
                else:
                    entry.unlink()
            except OSError:
                continue
    except OSError:
        pass
