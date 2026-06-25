"""yt-dlp YouTube audio downloader (runs in a QThread)."""

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QThread, Signal

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _clean_error(msg: str) -> str:
    """Strip yt-dlp's ANSI colour codes and its 'ERROR:' prefix for display."""
    msg = _ANSI_RE.sub("", str(msg)).strip()
    return re.sub(r"^ERROR:\s*", "", msg)


def _is_age_restricted(msg: str) -> bool:
    m = msg.lower()
    return "confirm your age" in m or "age-restricted" in m or "inappropriate for some" in m


def _get_ffmpeg() -> tuple[str, str]:
    """Return (ffmpeg_exe_path, ffmpeg_dir) for the best available ffmpeg."""
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        return exe, str(Path(exe).parent)
    except Exception:
        pass
    import shutil
    found = shutil.which("ffmpeg")
    if found:
        return found, str(Path(found).parent)
    raise FileNotFoundError("ffmpeg not found. Run: pip install imageio-ffmpeg")


class _Cancelled(Exception):
    """Raised internally to unwind run() cleanly when the worker is cancelled."""


class DownloadError(Exception):
    """Raised by fetch_audio() on an unrecoverable download/convert failure."""


def fetch_audio(url, output_dir=None, progress=None, on_info=None, should_cancel=None):
    """Download a YouTube URL to a WAV file.

    Returns (wav_path, yt_info_dict). Raises DownloadError on failure or
    _Cancelled if *should_cancel* returns True at a checkpoint.

    progress(pct:int, msg:str)  — optional progress callback
    on_info(title:str, artist:str) — optional, fired once metadata is known
    should_cancel() -> bool     — optional cooperative-cancel check
    """
    try:
        import yt_dlp
    except Exception:
        # A user-updated wheel failed to import (e.g. needs an unbundled dep).
        # Fall back to the bundled copy so downloads still work.
        from core import ytdlp_updater
        ytdlp_updater.deactivate()
        import yt_dlp

    if output_dir is None:
        from core.tempdirs import make_temp_dir
        output_dir = str(make_temp_dir("dl_"))

    def _progress(pct, msg):
        if progress:
            progress(pct, msg)

    def _check_cancel():
        if should_cancel and should_cancel():
            raise _Cancelled

    ffmpeg_exe, ffmpeg_dir = _get_ffmpeg()
    _progress(5, "Connecting to YouTube…")

    raw_template = os.path.join(output_dir, "raw.%(ext)s")
    _ydl_errors: list[str] = []

    class _Logger:
        def debug(self, msg):   pass
        def info(self, msg):    pass
        def warning(self, msg): _ydl_errors.append(f"WARNING: {msg}")
        def error(self, msg):   _ydl_errors.append(f"ERROR: {msg}")

    def _hook(d: dict):
        _check_cancel()
        if d["status"] == "downloading":
            try:
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 1
                pct = d.get("downloaded_bytes", 0) / total
                _progress(int(10 + pct * 6), f"Downloading… {int(pct * 100)}%")
            except Exception:
                pass

    ydl_opts = {
        "format": "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio",
        "outtmpl": raw_template,
        "quiet": True,
        "no_warnings": False,
        "no_color": True,          # don't leak ANSI colour codes into messages
        "socket_timeout": 30,
        "retries": 5,
        "fragment_retries": 5,
        "ffmpeg_location": ffmpeg_dir,
        "progress_hooks": [_hook],
        "logger": _Logger(),
    }

    from yt_dlp.utils import DownloadError as _YtDLError

    def _run(extra: dict | None = None) -> dict:
        opts = {**ydl_opts, **(extra or {})}
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=True) or {}

    _progress(10, "Fetching audio info…")
    try:
        yt_info = _run()
    except _YtDLError as exc:
        msg = _clean_error(exc)
        if _is_age_restricted(msg):
            # Best effort: some age-restricted videos are still reachable via
            # YouTube's embedded players, which don't require sign-in.
            try:
                yt_info = _run({"extractor_args":
                               {"youtube": {"player_client":
                                            ["tv_embedded", "web_embedded", "default"]}}})
            except _YtDLError:
                raise DownloadError(
                    "This video is age-restricted and YouTube requires signing "
                    "in to download it, so it can't be fetched automatically.\n\n"
                    "Try a different source for this track.")
        else:
            raise DownloadError(msg)

    if not yt_info:
        detail = "\n".join(_ydl_errors) if _ydl_errors else "No details available."
        raise DownloadError(f"yt-dlp could not fetch the video.\n\n{detail}")

    if on_info:
        from core.metadata import from_yt_info
        meta = from_yt_info(yt_info)
        if meta.get("title"):
            on_info(meta["title"], meta.get("artist", ""))

    raw_path = None
    for f in sorted(os.listdir(output_dir)):
        if f.startswith("raw."):
            raw_path = os.path.join(output_dir, f)
            break
    if not raw_path:
        detail = "\n".join(_ydl_errors) if _ydl_errors else ""
        raise DownloadError(
            "Download finished but no audio file was found.\n\n"
            + (detail or "Check that the URL is a public YouTube video."))
    if os.path.getsize(raw_path) < 1024:
        raise DownloadError(f"Downloaded file is too small and likely corrupt: {raw_path}")

    _progress(16, "Converting to WAV…")
    wav_path = os.path.join(output_dir, "audio.wav")
    no_window = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
    result = subprocess.run(
        [ffmpeg_exe, "-y", "-i", raw_path, "-ac", "2", "-ar", "44100", wav_path],
        capture_output=True, text=True, **no_window,
    )
    if result.returncode != 0:
        raise DownloadError(f"ffmpeg conversion failed:\n{result.stderr[-800:]}")

    _check_cancel()
    return wav_path, yt_info


class DownloaderWorker(QThread):
    progress = Signal(int, str)
    info_ready = Signal(str, str)  # title, artist — emitted once metadata is known
    finished = Signal(str, dict)   # path to WAV file, yt-dlp info dict
    error = Signal(str)

    def __init__(self, url: str, output_dir: str | None = None):
        super().__init__()
        self.url = url
        if output_dir is None:
            from core.tempdirs import make_temp_dir
            output_dir = str(make_temp_dir("dl_"))
        self.output_dir = output_dir

    def run(self):
        try:
            wav_path, yt_info = fetch_audio(
                self.url, self.output_dir,
                progress=lambda pct, msg: self.progress.emit(pct, msg),
                on_info=lambda title, artist: self.info_ready.emit(title, artist),
                should_cancel=self.isInterruptionRequested,
            )
            self.progress.emit(18, "Download complete. Starting separation…")
            self.finished.emit(wav_path, yt_info)
        except _Cancelled:
            return   # cancelled — exit quietly, emit nothing
        except DownloadError as exc:
            if self.isInterruptionRequested():
                return   # error caused by teardown during cancel — ignore
            # Already a clean, user-facing message — no traceback needed.
            self.error.emit(str(exc))
        except Exception as exc:
            if self.isInterruptionRequested():
                return   # error caused by teardown during cancel — ignore
            import traceback
            self.error.emit(f"{exc}\n\n{traceback.format_exc()}")
