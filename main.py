"""Rehearsal Room — entry point."""

import sys
import os

# In a PyInstaller --windowed (no-console) build, sys.stdout and sys.stderr
# are None. Several libraries (torch.hub, tqdm, etc.) write to them
# unconditionally and crash with "NoneType has no attribute 'write'".
# Redirect to devnull so those writes are silently dropped.
# These handles are intentionally never closed — they must outlive every
# thread that might write to stdout/stderr, i.e. the whole process.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(__file__))

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtCore import Qt

from ui.main_window import MainWindow
from ui.theme import Theme


def main():
    # Must be set before QApplication is created
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    # Clear temp leftovers from previous runs (Windows never empties %TEMP%)
    from core.tempdirs import sweep_stale
    sweep_stale()

    # Prefer a user-updated yt-dlp over the bundled copy, if one was downloaded.
    # Must run before anything imports yt_dlp (the downloader imports it lazily).
    from core import ytdlp_updater
    ytdlp_updater.activate()

    app = QApplication(sys.argv)
    app.setApplicationName("Rehearsal Room")
    app.setApplicationVersion("0.1.0")

    # Load bundled fonts if present
    font_dir = os.path.join(os.path.dirname(__file__), "assets", "fonts")
    if os.path.isdir(font_dir):
        for fname in os.listdir(font_dir):
            if fname.endswith((".ttf", ".otf")):
                QFontDatabase.addApplicationFont(os.path.join(font_dir, fname))

    # Default font
    default_font = QFont("Segoe UI", 10)
    app.setFont(default_font)

    # First-run: download Demucs model weights if not already cached
    from core.model_cache import is_model_cached
    if not is_model_cached():
        from ui.first_run_dialog import FirstRunDialog
        dlg = FirstRunDialog(Theme())
        if not dlg.exec() or not dlg.succeeded():
            sys.exit(0)   # user cancelled or download failed — don't open main window

    win = MainWindow()
    win.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    # MUST be the first thing in the entry point. In a frozen app, multiprocessing
    # uses the 'spawn' start method (always on macOS) which re-launches the
    # executable — i.e. this .app. Without freeze_support() the relaunched child
    # falls through to main() and opens a second GUI window/instance instead of
    # acting as a worker. (Triggered by multiprocessing inside bundled libs like
    # torch/numpy during separation.)
    import multiprocessing
    multiprocessing.freeze_support()

    main()
