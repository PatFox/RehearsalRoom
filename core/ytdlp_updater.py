"""Runtime self-update for yt-dlp.

YouTube changes its internals constantly, so a yt-dlp frozen into the app at
build time stops working within weeks. This module lets the app load a newer
yt-dlp downloaded into a user-writable folder, falling back to the bundled
copy when there's nothing newer (or the network is unavailable).

Mechanism
---------
yt-dlp is pure Python and ships as a zipapp, so a downloaded wheel (a zip with
``yt_dlp/`` at its root) can be imported directly. In a PyInstaller build the
bundled yt-dlp is served by a frozen importer on ``sys.meta_path``, which wins
over ``sys.path``; to override it we install our own ``sys.meta_path`` finder
(ahead of the frozen one) that routes ``yt_dlp`` imports to the wheel via
``zipimport``.

``activate()`` must run at startup **before** anything imports ``yt_dlp``.
"""

from __future__ import annotations

import json
import sys
import zipimport
from pathlib import Path

CACHE_DIR = Path.home() / ".rehearsalroom" / "ytdlp"
_PYPI_JSON = "https://pypi.org/pypi/yt-dlp/json"
# Nightly builds are published to PyPI as ".dev" pre-releases (with wheels);
# they carry fixes for new YouTube breakages before they reach a stable release.
# (The yt-dlp-nightly-builds GitHub repo only ships standalone binaries, not an
# importable wheel, so PyPI is the right source for our zipimport approach.)

_finder = None  # the installed meta-path finder, if any


# ── version helpers ──────────────────────────────────────────────────────────

def _parse_ver(s: str | None) -> tuple[int, ...]:
    """Parse a yt-dlp version like '2026.6.9' into a comparable int tuple."""
    if not s:
        return (0,)
    parts = []
    for chunk in str(s).split("."):
        try:
            parts.append(int(chunk))
        except ValueError:
            break
    return tuple(parts) or (0,)


def _ver_from_wheel(name: str) -> str:
    """'yt_dlp-2026.6.9-py3-none-any.whl' -> '2026.6.9'."""
    try:
        return name.split("-")[1]
    except IndexError:
        return ""


def _cached_wheel() -> Path | None:
    """Newest .whl present in the cache dir, or None."""
    if not CACHE_DIR.is_dir():
        return None
    wheels = sorted(
        CACHE_DIR.glob("yt_dlp-*.whl"),
        key=lambda p: _parse_ver(_ver_from_wheel(p.name)),
    )
    return wheels[-1] if wheels else None


def cached_version() -> str | None:
    w = _cached_wheel()
    return _ver_from_wheel(w.name) if w else None


def _read_bundled_version() -> str | None:
    """Read the bundled yt-dlp version without leaving it imported.

    Importing ``yt_dlp.version`` pulls in the bundled package; we purge it
    afterwards so a later ``import yt_dlp`` can still resolve through an
    override finder we may install.
    """
    import importlib
    try:
        mod = importlib.import_module("yt_dlp.version")
        return getattr(mod, "__version__", None)
    except Exception:
        return None
    finally:
        for m in [k for k in sys.modules if k == "yt_dlp" or k.startswith("yt_dlp.")]:
            del sys.modules[m]


# ── override finder ──────────────────────────────────────────────────────────

class _WheelFinder:
    """A meta-path finder that serves only ``yt_dlp`` from a wheel zip."""

    def __init__(self, wheel: Path):
        self.wheel = str(wheel)
        self._zi = zipimport.zipimporter(self.wheel)

    def find_spec(self, fullname, path=None, target=None):
        if fullname == "yt_dlp" or fullname.startswith("yt_dlp."):
            try:
                return self._zi.find_spec(fullname)
            except Exception:
                return None
        return None


def activate() -> str | None:
    """Install the override if a cached wheel is newer than the bundled copy.

    Returns the activated version string, or None if the bundled copy is used.
    Call once at startup before any ``import yt_dlp``.
    """
    global _finder
    wheel = _cached_wheel()
    if wheel is None:
        return None

    cached_v = _parse_ver(_ver_from_wheel(wheel.name))
    bundled = _read_bundled_version()
    if bundled and cached_v <= _parse_ver(bundled):
        # The app shipped a newer (or equal) yt-dlp than the cached wheel —
        # drop the stale cache so we don't downgrade.
        try:
            wheel.unlink()
        except OSError:
            pass
        return None

    try:
        _finder = _WheelFinder(wheel)
        sys.meta_path.insert(0, _finder)
        return _ver_from_wheel(wheel.name)
    except Exception:
        _finder = None
        return None


def deactivate() -> None:
    """Remove the override and purge yt_dlp so the bundled copy is used.

    Called as a safety net if importing the overridden wheel fails (e.g. a
    newer yt-dlp needs a dependency that isn't bundled).
    """
    global _finder
    if _finder is not None:
        try:
            sys.meta_path.remove(_finder)
        except ValueError:
            pass
        _finder = None
    for m in [k for k in sys.modules if k == "yt_dlp" or k.startswith("yt_dlp.")]:
        del sys.modules[m]


def active_version() -> str | None:
    """The version that would be imported now (cached override or bundled)."""
    if _finder is not None:
        return _ver_from_wheel(Path(_finder.wheel).name)
    return _read_bundled_version()


# ── update (download newest wheel) ───────────────────────────────────────────

def _pypi_latest(timeout: int = 15) -> tuple[str, str]:
    """Return (latest_version, wheel_url) from PyPI. Raises on failure."""
    import urllib.request

    req = urllib.request.Request(
        _PYPI_JSON, headers={"User-Agent": "RehearsalRoom-ytdlp-updater/1.0"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    version = data["info"]["version"]
    wheel_url = ""
    for f in data.get("urls", []):
        if f.get("packagetype") == "bdist_wheel" and f.get("filename", "").endswith(
            "-py3-none-any.whl"
        ):
            wheel_url = f["url"]
            break
    if not wheel_url:
        raise RuntimeError("PyPI returned no compatible yt-dlp wheel.")
    return version, wheel_url


def _nightly_latest(timeout: int = 15) -> tuple[str, str]:
    """Return (latest_version, wheel_url) for the newest nightly (.dev) build
    published to PyPI. Raises on failure."""
    import urllib.request

    req = urllib.request.Request(
        _PYPI_JSON, headers={"User-Agent": "RehearsalRoom-ytdlp-updater/1.0"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    releases = data.get("releases", {})
    nightlies = [v for v in releases if "dev" in v]
    if not nightlies:
        raise RuntimeError("No nightly yt-dlp builds found on PyPI.")
    version = max(nightlies, key=_parse_ver)

    wheel_url = ""
    for f in releases[version]:
        if (not f.get("yanked")
                and f.get("packagetype") == "bdist_wheel"
                and f.get("filename", "").endswith("-py3-none-any.whl")):
            wheel_url = f["url"]
            break
    if not wheel_url:
        raise RuntimeError("Latest nightly has no compatible yt-dlp wheel.")
    return version, wheel_url


def _latest(channel: str, timeout: int) -> tuple[str, str]:
    return _nightly_latest(timeout) if channel == "nightly" else _pypi_latest(timeout)


def update(progress=None, timeout: int = 30,
           channel: str = "stable") -> tuple[bool, str, str | None]:
    """Download the latest yt-dlp wheel if it's newer than what's active.

    channel: "stable" (PyPI) or "nightly" (yt-dlp-nightly-builds GitHub repo).
    progress(pct:int, msg:str) — optional callback.
    Returns (changed, message, new_version).
    """
    import urllib.request

    def _p(pct, msg):
        if progress:
            progress(pct, msg)

    src = "nightly" if channel == "nightly" else "PyPI (stable)"
    _p(5, f"Checking {src} for the latest yt-dlp…")
    latest, wheel_url = _latest(channel, timeout)

    current = active_version()
    if _parse_ver(latest) <= _parse_ver(current):
        return False, f"Already up to date (yt-dlp {current}).", current

    _p(20, f"Downloading yt-dlp {latest}…")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    dest = CACHE_DIR / f"yt_dlp-{latest}-py3-none-any.whl"
    tmp = dest.with_suffix(".whl.part")

    req = urllib.request.Request(
        wheel_url, headers={"User-Agent": "RehearsalRoom-ytdlp-updater/1.0"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        got = 0
        with open(tmp, "wb") as f:
            while True:
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                if total:
                    _p(20 + int(got / total * 70), f"Downloading yt-dlp {latest}…")
    tmp.replace(dest)

    # Drop any older cached wheels so _cached_wheel() stays unambiguous.
    for old in CACHE_DIR.glob("yt_dlp-*.whl"):
        if old != dest:
            try:
                old.unlink()
            except OSError:
                pass

    _p(100, f"yt-dlp {latest} downloaded.")
    return True, (
        f"Updated yt-dlp to {latest}. Restart Rehearsal Room to use it."
    ), latest
