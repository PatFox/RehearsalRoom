"""Audio metadata fetching — three strategies in priority order:

1. Embedded file tags (mutagen) — instant, offline
2. yt-dlp info dict — already in hand for YouTube imports
3. AcoustID + MusicBrainz fingerprinting — network, needs API key
"""

from __future__ import annotations
from pathlib import Path
from typing import Optional


def from_file_tags(path: Path) -> dict:
    """Read embedded ID3 / Vorbis / MP4 tags from an audio file."""
    try:
        from mutagen import File
        f = File(str(path), easy=True)
        if f is None:
            return {}
        def _first(key: str) -> str:
            vals = f.get(key) or []
            return str(vals[0]).strip() if vals else ""
        result = {}
        for field, keys in [
            ("title",  ["title"]),
            ("artist", ["artist", "albumartist"]),
            ("album",  ["album"]),
        ]:
            for k in keys:
                v = _first(k)
                if v:
                    result[field] = v
                    break
        return result
    except Exception:
        return {}


def from_yt_info(info: dict) -> dict:
    """Extract metadata from a yt-dlp info dict."""
    if not info:
        return {}
    result: dict = {}

    title = (
        info.get("track")
        or info.get("title")
        or ""
    )
    if title:
        result["title"] = title.strip()

    artist = (
        info.get("artist")
        or info.get("uploader")
        or info.get("channel")
        or ""
    )
    if artist:
        result["artist"] = artist.strip()

    album = info.get("album") or ""
    if album:
        result["album"] = album.strip()

    return result


def from_acoustid(audio_path: Path, api_key: str) -> dict:
    """Fingerprint audio and look up title/artist via AcoustID + MusicBrainz.

    Requires pyacoustid and either:
    - the chromaprint Python extension, OR
    - fpcalc.exe in the project bin/ folder or on PATH
    """
    if not api_key:
        return {}
    try:
        import acoustid

        # Locate fpcalc if chromaprint native lib isn't available
        fpcalc = _find_fpcalc()

        results = acoustid.match(
            api_key,
            str(audio_path),
            meta="recordings",
            **({"fpcalc": str(fpcalc)} if fpcalc else {}),
        )

        best_score = 0.0
        best: dict = {}
        for score, recording_id, title, artist in results:
            if score > best_score:
                best_score = score
                best = {}
                if title:
                    best["title"] = title.strip()
                if artist:
                    best["artist"] = artist.strip()

        if best_score >= 0.5:
            return best
        return {}

    except Exception:
        return {}


def merge(*sources: dict) -> dict:
    """Merge metadata dicts left-to-right, earlier sources taking priority."""
    result: dict = {}
    for src in reversed(sources):   # later sources first, earlier override
        result.update(src)
    return result


def _find_fpcalc() -> Optional[Path]:
    """Return path to fpcalc binary, or None to let pyacoustid use chromaprint lib."""
    import shutil
    bin_dir = Path(__file__).resolve().parent.parent / "bin"
    for name in ("fpcalc.exe", "fpcalc"):
        candidate = bin_dir / name
        if candidate.exists():
            return candidate
    found = shutil.which("fpcalc")
    return Path(found) if found else None
