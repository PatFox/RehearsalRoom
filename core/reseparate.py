"""Background re-separation worker.

Re-runs Demucs on a track's original audio (embedded, or re-fetched from its
source URL) and rewrites the existing .stems file in place, preserving title,
artist, source URL, cover, saved loops and the embedded original.
"""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QThread, Signal


class ReseparateWorker(QThread):
    progress = Signal(int, str)   # percent, message
    done     = Signal(str)        # stems_path
    error    = Signal(str)

    def __init__(self, stems_path: str, model_name: str = "htdemucs", parent=None):
        super().__init__(parent)
        self._stems_path = str(stems_path)
        self._model = model_name

    def run(self):
        try:
            from core.project import (read_manifest, read_cover, extract_original,
                                      save_stems, update_manifest)
            from core.separator import separate_audio
            from core.tempdirs import make_temp_dir

            stems_path = Path(self._stems_path)
            manifest = read_manifest(stems_path)

            # 1. Obtain the original audio.
            tmp = make_temp_dir("resep_")
            if manifest.original:
                self.progress.emit(2, "Reading original audio…")
                original = extract_original(stems_path, tmp)
            elif manifest.source_url:
                self.progress.emit(2, "Re-fetching original from source…")
                from core.downloader import fetch_audio
                original, _info = fetch_audio(
                    manifest.source_url, str(tmp),
                    progress=lambda pct, msg: self.progress.emit(min(4, pct // 25), msg))
            else:
                self.error.emit(
                    "This track has no original audio and no source URL, so it "
                    "can't be re-separated.")
                return
            if original is None:
                self.error.emit("Could not obtain the original audio to re-separate.")
                return

            # 2. Re-separate.
            out_dir = make_temp_dir("resep_out_")
            stem_paths = separate_audio(
                original, self._model, out_dir,
                progress=lambda pct, msg: self.progress.emit(pct, msg),
                should_cancel=self.isInterruptionRequested)

            # 3. Repack the .stems in place, preserving metadata/cover/original/loops.
            self.progress.emit(96, "Writing stems package…")
            cover = read_cover(stems_path)
            orig_stat = stems_path.stat()
            tmp_out = stems_path.with_suffix(".stems.tmp")
            save_stems(
                {k: Path(v) for k, v in stem_paths.items()},
                tmp_out,
                title=manifest.title, artist=manifest.artist,
                source_url=manifest.source_url, cover=cover,
                original_path=original,
            )
            if manifest.loops:
                m = read_manifest(tmp_out)
                m.loops = manifest.loops
                update_manifest(tmp_out, m)

            os.replace(tmp_out, stems_path)
            os.utime(stems_path, (orig_stat.st_atime, orig_stat.st_mtime))

            self.progress.emit(100, "Done.")
            self.done.emit(str(stems_path))

        except Exception as exc:
            if self.isInterruptionRequested():
                return
            import traceback
            self.error.emit(f"{exc}\n\n{traceback.format_exc()}")
