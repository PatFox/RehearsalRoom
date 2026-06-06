"""Utilities for computing library disk usage."""

from pathlib import Path


def fmt_size(n_bytes: int) -> str:
    """Format a byte count as a human-readable string."""
    for unit, threshold in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if n_bytes >= threshold:
            return f"{n_bytes / threshold:.1f} {unit}"
    return f"{n_bytes} B"


def library_total_bytes(library_path: Path) -> int:
    """Return the total size in bytes of all .stems files in the library directory."""
    if not library_path.exists():
        return 0
    return sum(p.stat().st_size for p in library_path.glob("*.stems"))


def stems_file_size(stems_path: str | Path) -> int:
    """Return the size in bytes of a single .stems file, or 0 if not found."""
    try:
        return Path(stems_path).stat().st_size
    except OSError:
        return 0
