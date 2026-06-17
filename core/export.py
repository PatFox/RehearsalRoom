"""Background audio-export worker (runs in a QThread).

Renders the current mix or extracts the embedded original, then transcodes
to the chosen format — all off the UI thread.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal


class ExportWorker(QThread):
    progress = Signal(str)   # stage message
    done     = Signal(str)   # destination path
    error    = Signal(str)

    def __init__(self, mode: str, dest, player=None, stems_path=None,
                 url=None, parent=None):
        super().__init__(parent)
        self._mode = mode               # "current" | "original" | "youtube"
        self._dest = str(dest)
        self._player = player
        self._stems_path = stems_path
        self._url = url

    def run(self):
        try:
            from core.project import transcode_audio, extract_original
            from core.tempdirs import make_temp_dir
            tmp = make_temp_dir("export_")

            if self._mode == "current":
                self.progress.emit("Rendering current mix…")
                src = tmp / "mix.wav"
                self._player.render_current_mix(src)
            elif self._mode == "youtube":
                # Original wasn't embedded — re-fetch it from the source URL.
                from core.downloader import fetch_audio
                self.progress.emit("Re-fetching original from YouTube…")
                wav, _info = fetch_audio(
                    self._url, str(tmp),
                    progress=lambda pct, msg: self.progress.emit(msg))
                src = wav
            else:
                self.progress.emit("Extracting original audio…")
                src = extract_original(self._stems_path, tmp)
                if src is None:
                    self.error.emit("No original audio embedded in this track.")
                    return

            self.progress.emit("Encoding…")
            transcode_audio(src, self._dest)

            # If we re-fetched from YouTube, backfill the original into the
            # .stems file so it never has to be downloaded again.
            if self._mode == "youtube" and self._stems_path:
                try:
                    from core.project import set_original
                    self.progress.emit("Saving original into track…")
                    set_original(self._stems_path, src)
                except Exception:
                    pass   # best-effort — the export itself already succeeded

            self.done.emit(self._dest)

        except Exception as exc:
            import traceback
            self.error.emit(f"{exc}\n\n{traceback.format_exc()}")
