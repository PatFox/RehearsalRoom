"""yt-dlp YouTube audio downloader (runs in a QThread)."""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QThread, Signal


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


class DownloaderWorker(QThread):
    progress = Signal(int, str)
    finished = Signal(str, dict)   # path to WAV file, yt-dlp info dict
    error = Signal(str)

    def __init__(self, url: str, output_dir: str | None = None):
        super().__init__()
        self.url = url
        self.output_dir = output_dir or tempfile.mkdtemp(prefix="rehearsalroom_dl_")

    def run(self):
        try:
            import yt_dlp

            ffmpeg_exe, ffmpeg_dir = _get_ffmpeg()
            self.progress.emit(5, "Connecting to YouTube…")

            raw_template = os.path.join(self.output_dir, "raw.%(ext)s")

            # Collect yt-dlp warnings/errors so silent failures surface in our UI.
            _ydl_errors: list[str] = []

            class _Logger:
                def debug(self, msg):   pass
                def info(self, msg):    pass
                def warning(self, msg): _ydl_errors.append(f"WARNING: {msg}")
                def error(self, msg):   _ydl_errors.append(f"ERROR: {msg}")

            ydl_opts = {
                "format": "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio",
                "outtmpl": raw_template,
                "quiet": True,
                "no_warnings": False,
                "socket_timeout": 30,       # don't hang indefinitely
                "retries": 5,
                "fragment_retries": 5,
                "ffmpeg_location": ffmpeg_dir,
                "progress_hooks": [self._hook],
                "logger": _Logger(),
            }

            self.progress.emit(10, "Fetching audio info…")
            yt_info: dict = {}
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                yt_info = ydl.extract_info(self.url, download=True) or {}

            if not yt_info:
                detail = "\n".join(_ydl_errors) if _ydl_errors else "No details available."
                self.error.emit(f"yt-dlp could not fetch the video.\n\n{detail}")
                return

            # Find the downloaded file
            raw_path = None
            for f in sorted(os.listdir(self.output_dir)):
                if f.startswith("raw."):
                    raw_path = os.path.join(self.output_dir, f)
                    break

            if not raw_path:
                detail = "\n".join(_ydl_errors) if _ydl_errors else ""
                self.error.emit(
                    "Download finished but no audio file was found.\n\n"
                    + (detail or "Check that the URL is a public YouTube video.")
                )
                return

            if os.path.getsize(raw_path) < 1024:
                self.error.emit(f"Downloaded file is too small and likely corrupt: {raw_path}")
                return

            self.progress.emit(16, "Converting to WAV…")

            wav_path = os.path.join(self.output_dir, "audio.wav")
            no_window = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
            result = subprocess.run(
                [ffmpeg_exe, "-y", "-i", raw_path, "-ac", "2", "-ar", "44100", wav_path],
                capture_output=True, text=True, **no_window,
            )
            if result.returncode != 0:
                self.error.emit(f"ffmpeg conversion failed:\n{result.stderr[-800:]}")
                return

            self.progress.emit(18, "Download complete. Starting separation…")
            self.finished.emit(wav_path, yt_info)

        except Exception as exc:
            import traceback
            self.error.emit(f"{exc}\n\n{traceback.format_exc()}")

    def _hook(self, d: dict):
        if d["status"] == "downloading":
            try:
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 1
                pct = d.get("downloaded_bytes", 0) / total
                self.progress.emit(int(10 + pct * 6), f"Downloading… {int(pct * 100)}%")
            except Exception:
                pass
