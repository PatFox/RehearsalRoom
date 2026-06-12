"""Album-artwork resolution and caching.

Cover bytes are sourced, in priority order:
  1. embedded art in the original audio file (mutagen)
  2. iTunes Search API lookup by artist + title (free, no key)
  3. the YouTube thumbnail (for YouTube imports)

New imports embed the resolved cover into the .stems package. Existing
library tracks are backfilled via a disk cache (~/.rehearsalroom/artcache)
without modifying the user's files.
"""

from __future__ import annotations

import hashlib
import json
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

from core.log import get_logger

_log = get_logger("artwork")

_CACHE_DIR = Path.home() / ".rehearsalroom" / "artcache"
_TIMEOUT = 12


# ── individual sources ──────────────────────────────────────────────────────

def embedded_cover(audio_path: Path) -> Optional[bytes]:
    """Extract embedded cover art (ID3 APIC / FLAC picture / MP4 covr)."""
    try:
        from mutagen import File
        f = File(str(audio_path))
        if f is None:
            return None
        pics = getattr(f, "pictures", None)        # FLAC / OGG
        if pics:
            return bytes(pics[0].data)
        tags = getattr(f, "tags", None)
        if tags:
            for key in tags.keys():                # ID3 APIC:...
                if key.startswith("APIC"):
                    return bytes(tags[key].data)
            if "covr" in tags:                     # MP4 / m4a
                return bytes(tags["covr"][0])
    except Exception as exc:
        _log.warning("embedded cover read failed for %s: %s", audio_path, exc)
    return None


def _http_get(url: str) -> Optional[bytes]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "RehearsalRoom/1.0"})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return resp.read()
    except Exception as exc:
        _log.warning("download failed %s: %s", url, exc)
        return None


def itunes_cover(artist: str, title: str) -> Optional[bytes]:
    """Look up cover art via the iTunes Search API (artwork upscaled to 600px)."""
    if not title:
        return None
    term = urllib.parse.quote(f"{artist} {title}".strip())
    raw = _http_get(f"https://itunes.apple.com/search?term={term}&entity=song&limit=1")
    if not raw:
        return None
    try:
        results = (json.loads(raw.decode("utf-8")).get("results") or [])
        if not results:
            return None
        art = results[0].get("artworkUrl100") or results[0].get("artworkUrl60")
        if not art:
            return None
        art = art.replace("100x100bb", "600x600bb").replace("60x60bb", "600x600bb")
        return _http_get(art)
    except Exception as exc:
        _log.warning("iTunes lookup failed for %s — %s: %s", artist, title, exc)
        return None


def youtube_cover(yt_info: dict) -> Optional[bytes]:
    """Download the YouTube thumbnail from a yt-dlp info dict."""
    if not yt_info:
        return None
    thumb = yt_info.get("thumbnail")
    if not thumb:
        thumbs = yt_info.get("thumbnails") or []
        if thumbs:
            thumb = thumbs[-1].get("url")
    return _http_get(thumb) if thumb else None


def resolve_cover(audio_path, artist: str, title: str,
                  yt_info: Optional[dict] = None) -> Optional[bytes]:
    """Resolve cover art: embedded → iTunes → YouTube thumbnail."""
    if audio_path:
        data = embedded_cover(Path(audio_path))
        if data:
            return data
    data = itunes_cover(artist or "", title or "")
    if data:
        return data
    if yt_info:
        return youtube_cover(yt_info)
    return None


# ── disk cache (for backfilling existing tracks, non-destructively) ──────────

def _key(artist: str, title: str) -> str:
    return hashlib.sha1(f"{(artist or '').lower()}|{(title or '').lower()}".encode()).hexdigest()


def cached_cover(artist: str, title: str) -> Optional[bytes]:
    p = _CACHE_DIR / f"{_key(artist, title)}.img"
    if p.exists():
        try:
            return p.read_bytes()
        except OSError:
            return None
    return None


def store_cached(artist: str, title: str, data: bytes) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (_CACHE_DIR / f"{_key(artist, title)}.img").write_bytes(data)
    except OSError:
        pass
