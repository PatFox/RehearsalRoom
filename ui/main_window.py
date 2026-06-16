"""Main window — sidebar + stacked content area (library / player)."""

from PySide6.QtCore import Qt, Signal, QObject, QEvent, QTimer, QThread
from PySide6.QtGui import QColor, QPalette, QFont, QAction
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QLabel, QPushButton, QFrame, QLineEdit, QStackedWidget,
    QSizePolicy, QDialog, QMenu
)

from ui.theme import Theme, STEM_IDS
from ui.library_panel import LibraryPanel
from ui.player_panel import PlayerPanel
from ui.import_dialog import ImportDialog, ImportProgressWidget
from ui.settings_dialog import SettingsDialog
from core.separator import SeparatorWorker
from core.downloader import DownloaderWorker
from core.project import save_stems, load_stems
from core import settings as S
from core.library import scan as scan_library, song_from_stems_file
from pathlib import Path
import os


def _unique_stems_path(lib_dir: Path, base_name: str) -> Path:
    """Return a .stems path that doesn't already exist, adding (2), (3)… if needed."""
    candidate = lib_dir / f"{base_name}.stems"
    if not candidate.exists():
        return candidate
    n = 2
    while True:
        candidate = lib_dir / f"{base_name} ({n}).stems"
        if not candidate.exists():
            return candidate
        n += 1


_DEMO_SONGS = []


class _ErrorDialog(QDialog):
    def __init__(self, message: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Error")
        self.setModal(True)
        self.setFixedWidth(520)
        self._message = message

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 22)
        lay.setSpacing(14)

        title = QLabel("Something went wrong")
        title.setStyleSheet("font-size: 17px; font-weight: 600;")
        lay.addWidget(title)

        hint = QLabel("Click the error text below to copy it to the clipboard.")
        hint.setStyleSheet("font-size: 12px; color: #93939C;")
        lay.addWidget(hint)

        self._text = QLabel(message)
        self._text.setWordWrap(True)
        self._text.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self._text.setStyleSheet("""
            QLabel {
                font-family: 'Consolas', monospace;
                font-size: 11.5px;
                background: #F4F4F0;
                border: 1px solid #E2E2DC;
                border-radius: 4px;
                padding: 12px;
            }
            QLabel:hover { background: #ECECE6; border-color: #2E6BFF; cursor: pointer; }
        """)
        self._text.setCursor(Qt.CursorShape.PointingHandCursor)
        self._text.mousePressEvent = lambda e: self._copy()
        lay.addWidget(self._text)

        self._confirm = QLabel("")
        self._confirm.setStyleSheet("font-size: 12px; color: #0E9F6E; font-weight: 600;")
        self._confirm.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._confirm)

        close_btn = QPushButton("Close")
        close_btn.setProperty("role", "ghost")
        close_btn.setFixedHeight(36)
        close_btn.clicked.connect(self.accept)
        lay.addWidget(close_btn, 0, Qt.AlignmentFlag.AlignRight)

    def _copy(self):
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(self._message)
        self._confirm.setText("✓ Copied to clipboard")
        self._text.setStyleSheet("""
            QLabel {
                font-family: 'Consolas', monospace;
                font-size: 11.5px;
                background: #F0FDF4;
                border: 1px solid #86EFAC;
                border-radius: 4px;
                padding: 12px;
            }
        """)


class _DeleteConfirmDialog(QDialog):
    confirmed = Signal()

    def __init__(self, title: str, theme: Theme, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Delete track")
        self.setFixedWidth(420)
        self.setModal(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(26, 22, 26, 22)
        lay.setSpacing(14)

        heading = QLabel("Delete track?")
        heading.setStyleSheet("font-size: 17px; font-weight: 600;")
        lay.addWidget(heading)

        body = QLabel(
            f"<b>{title}</b> and its stem files will be permanently deleted "
            f"from your library.<br><br>"
            f"<span style='color:#E53E3E;'>This cannot be undone.</span>"
        )
        body.setWordWrap(True)
        body.setStyleSheet(f"font-size: 13px; color: {theme.ink2};")
        lay.addWidget(body)

        foot = QHBoxLayout()
        foot.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setProperty("role", "ghost")
        cancel_btn.setFixedHeight(36)
        cancel_btn.clicked.connect(self.reject)
        foot.addWidget(cancel_btn)

        delete_btn = QPushButton("Delete")
        delete_btn.setFixedHeight(36)
        delete_btn.setStyleSheet(
            "QPushButton { background: #E53E3E; color: white; border-radius: 4px; "
            "font-weight: 600; padding: 0 18px; }"
            "QPushButton:hover { background: #C53030; }"
        )
        delete_btn.clicked.connect(self._on_confirm)
        foot.addWidget(delete_btn)

        lay.addLayout(foot)

    def _on_confirm(self):
        self.confirmed.emit()
        self.accept()


class _UpToDateDialog(QDialog):
    def __init__(self, current: str, theme: Theme, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Up to date")
        self.setFixedWidth(360)
        self.setModal(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(26, 22, 26, 22)
        lay.setSpacing(12)

        title = QLabel("You're up to date  ✓")
        title.setStyleSheet("font-size: 16px; font-weight: 600;")
        lay.addWidget(title)

        body = QLabel(f"Rehearsal Room <b>v{current}</b> is the latest version.")
        body.setWordWrap(True)
        body.setStyleSheet(f"font-size: 13px; color: {theme.ink2};")
        lay.addWidget(body)

        btn = QPushButton("Close")
        btn.setProperty("role", "ghost")
        btn.setFixedHeight(36)
        btn.clicked.connect(self.accept)
        lay.addWidget(btn, 0, Qt.AlignmentFlag.AlignRight)


class _UpdateAvailableDialog(QDialog):
    def __init__(self, current: str, latest: str, url: str, theme: Theme, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Update available")
        self.setFixedWidth(400)
        self.setModal(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(26, 22, 26, 22)
        lay.setSpacing(12)

        title = QLabel("Update available")
        title.setStyleSheet("font-size: 16px; font-weight: 600;")
        lay.addWidget(title)

        body = QLabel(
            f"A new version of Rehearsal Room is available.<br><br>"
            f"<b>Current:</b>  v{current}<br>"
            f"<b>Latest:</b>   v{latest}"
        )
        body.setWordWrap(True)
        body.setStyleSheet(f"font-size: 13px; color: {theme.ink2};")
        lay.addWidget(body)

        link = QLabel(f'<a href="{url}" style="color: #2E6BFF;">View release on GitHub</a>')
        link.setOpenExternalLinks(True)
        link.setStyleSheet("font-size: 13px;")
        lay.addWidget(link)

        btn = QPushButton("Close")
        btn.setProperty("role", "ghost")
        btn.setFixedHeight(36)
        btn.clicked.connect(self.accept)
        lay.addWidget(btn, 0, Qt.AlignmentFlag.AlignRight)


class AboutDialog(QDialog):
    def __init__(self, theme: Theme, parent=None):
        super().__init__(parent)
        self.setWindowTitle("About Rehearsal Room")
        self.setFixedWidth(440)
        self.setModal(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 26, 28, 24)
        lay.setSpacing(14)

        # Header row: mark + name
        header = QHBoxLayout()
        header.setSpacing(14)
        mark = QFrame()
        mark.setFixedSize(44, 44)
        mark.setStyleSheet("background: #17171B; border-radius: 4px;")
        mark_lbl = QLabel("〜")
        mark_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark_lbl.setStyleSheet("color: white; font-size: 16px; background: transparent;")
        from PySide6.QtWidgets import QHBoxLayout as _HBL
        mark_lay = _HBL(mark)
        mark_lay.setContentsMargins(0, 0, 0, 0)
        mark_lay.addWidget(mark_lbl)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        name_lbl = QLabel("Rehearsal Room")
        name_lbl.setStyleSheet("font-size: 18px; font-weight: 700; letter-spacing: -0.02em;")
        from core.version import __version__
        ver_lbl = QLabel(f"Version {__version__}")
        ver_lbl.setStyleSheet(f"font-size: 12px; color: {theme.ink3};")
        title_col.addWidget(name_lbl)
        title_col.addWidget(ver_lbl)

        header.addWidget(mark)
        header.addLayout(title_col)
        header.addStretch()
        lay.addLayout(header)

        div = QFrame()
        div.setFrameShape(QFrame.Shape.HLine)
        div.setStyleSheet(f"color: {theme.border};")
        lay.addWidget(div)

        body = QLabel(
            "Rehearsal Room is a music practice tool for musicians who want to "
            "slow down, loop, and isolate individual stems from any song.\n\n"
            "Import any audio file or paste a YouTube URL — Rehearsal Room uses "
            "AI-powered source separation (Demucs) to split the track into "
            "vocals, drums, bass, and other instruments, each with its own "
            "volume fader and waveform display.\n\n"
            "Built with Python, PySide6, and a lot of ☕."
        )
        body.setWordWrap(True)
        body.setStyleSheet(f"font-size: 13px; color: {theme.ink2}; line-height: 1.5;")
        lay.addWidget(body)

        lay.addSpacing(4)

        close_btn = QPushButton("Close")
        close_btn.setProperty("role", "ghost")
        close_btn.setFixedHeight(36)
        close_btn.clicked.connect(self.accept)
        lay.addWidget(close_btn, 0, Qt.AlignmentFlag.AlignRight)


class SidebarButton(QPushButton):
    def __init__(self, icon: str, label: str, count: int = -1, parent=None):
        super().__init__(parent)
        self._icon = icon
        self._label = label
        self._count = count
        self.setCheckable(True)
        self.setFixedHeight(36)
        self._refresh()

    def set_count(self, n: int):
        self._count = n
        self._refresh()

    def _refresh(self):
        text = self._label
        if self._count >= 0:
            text += f"   {self._count}"
        self.setText(text)


class Sidebar(QFrame):
    nav_changed    = Signal(str)
    import_clicked = Signal()
    abort_current  = Signal()   # skip this track, continue queue
    abort_all      = Signal()   # stop all remaining tracks

    def __init__(self, theme: Theme, song_count: int = 0, parent=None):
        super().__init__(parent)
        self._theme = theme
        self.setFixedWidth(252)
        self._setup_ui(song_count)
        self._apply_theme()

    def _setup_ui(self, song_count: int):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 22, 16, 16)
        lay.setSpacing(4)

        # brand
        brand_row = QHBoxLayout()
        brand_row.setSpacing(11)
        brand_mark = QFrame()
        brand_mark.setFixedSize(34, 34)
        brand_mark.setStyleSheet(
            "background: #17171B; border-radius: 4px;"
        )
        # wave icon (text fallback)
        mark_lbl = QLabel("〜")
        mark_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark_lbl.setStyleSheet("color: white; font-size: 14px; background: transparent;")
        mark_lay = QHBoxLayout(brand_mark)
        mark_lay.setContentsMargins(0, 0, 0, 0)
        mark_lay.addWidget(mark_lbl)

        name_col = QVBoxLayout()
        name_col.setSpacing(1)
        name_lbl = QLabel("Rehearsal Room")
        name_lbl.setStyleSheet("font-size: 16px; font-weight: 600; letter-spacing: -0.02em;")
        studio_lbl = QLabel("STUDIO")
        studio_lbl.setStyleSheet(
            "font-size: 10px; font-weight: 500; letter-spacing: 0.14em; color: #93939C;"
        )
        name_col.addWidget(name_lbl)
        name_col.addWidget(studio_lbl)

        brand_row.addWidget(brand_mark)
        brand_row.addLayout(name_col)
        brand_row.addStretch()
        lay.addLayout(brand_row)
        lay.addSpacing(14)


        self._nav_buttons: dict[str, SidebarButton] = {}
        for key, icon, label, cnt in [
            ("library", "⊞", "All tracks", song_count),
            ("recent",  "⏱", "Recent", -1),
            ("fav",     "☆", "Favorites", -1),
            ("artist",  "♪", "By artist", -1),
        ]:
            btn = SidebarButton(icon, label, cnt)
            btn.clicked.connect(lambda checked, k=key: self._on_nav(k))
            lay.addWidget(btn)
            self._nav_buttons[key] = btn

        self._nav_buttons["library"].setChecked(True)

        lay.addStretch()

        # import progress (hidden until an import is running)
        self._import_progress = ImportProgressWidget(self._theme)
        self._import_progress.abort_current.connect(self.abort_current)
        self._import_progress.abort_all.connect(self.abort_all)
        lay.addWidget(self._import_progress)

        # import CTA
        self._import_btn = QPushButton("+ Import track")
        self._import_btn.setFixedHeight(40)
        self._import_btn.clicked.connect(self.import_clicked)
        lay.addWidget(self._import_btn)
        lay.addSpacing(10)

        # storage
        storage = QFrame()
        storage.setStyleSheet("QFrame { border: none; background: transparent; }")
        stor_lay = QVBoxLayout(storage)
        stor_lay.setContentsMargins(11, 10, 11, 10)
        stor_lay.setSpacing(3)

        stor_header = QHBoxLayout()
        stor_header.setContentsMargins(0, 0, 0, 0)
        stor_header.setSpacing(6)
        stor_top = QLabel("Library storage")
        stor_top.setStyleSheet("font-size: 11px; font-weight: 500;")
        stor_header.addWidget(stor_top, 1)

        self._open_dir_btn = QPushButton("↗")
        self._open_dir_btn.setFixedSize(18, 18)
        self._open_dir_btn.setToolTip("Open library folder")
        self._open_dir_btn.setStyleSheet(
            "QPushButton { border: none; background: transparent; "
            "font-size: 11px; color: #93939C; padding: 0; }"
            "QPushButton:hover { color: #FFFFFF; }"
        )
        self._open_dir_btn.clicked.connect(self._open_library_dir)
        stor_header.addWidget(self._open_dir_btn)

        self._stor_lbl = QLabel("")
        self._stor_lbl.setStyleSheet("font-size: 11px; color: #93939C;")
        stor_lay.addLayout(stor_header)
        stor_lay.addWidget(self._stor_lbl)
        lay.addWidget(storage)

    # ── import progress helpers ───────────────────────────────────────────────

    def show_import_progress(self, name: str, current: int = 1, total: int = 1):
        self._import_progress.start(name, current, total)

    def update_import_progress(self, pct: int, message: str = ""):
        self._import_progress.update_progress(pct, message)

    def finish_import_progress(self):
        self._import_progress.finish()

    def reset_import_progress(self):
        self._import_progress.reset()

    def update_import_total(self, current: int, total: int):
        self._import_progress.set_count(current, total)

    def set_import_name(self, title: str, artist: str = ""):
        self._import_progress.set_name(title, artist)

    # ── nav ───────────────────────────────────────────────────────────────────

    def _on_nav(self, key: str):
        for k, btn in self._nav_buttons.items():
            btn.setChecked(k == key)
        self.nav_changed.emit(key)

    def update_count(self, n: int):
        self._nav_buttons["library"].set_count(n)

    def refresh_storage(self, library_path, n_tracks: int):
        from core.library_stats import library_total_bytes, fmt_size
        total = library_total_bytes(library_path)
        self._stor_lbl.setText(f"{fmt_size(total)} · {n_tracks} track{'s' if n_tracks != 1 else ''}")
        self._library_path = str(library_path)

    def _open_library_dir(self):
        import os, subprocess, sys
        path = getattr(self, "_library_path", "")
        if not path:
            from core import settings as S
            path = str(S.library_path())
        if sys.platform == "win32":
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])

    def _apply_theme(self):
        t = self._theme
        self.setStyleSheet(f"""
            QFrame {{
                background: {t.surface};
                border-right: none;
            }}
            QPushButton {{
                background: transparent;
                color: {t.ink2};
                border-radius: 4px;
                text-align: left;
                padding: 8px 10px;
                font-size: 14px;
                font-weight: 500;
            }}
            QPushButton:hover {{ background: {t.surface2}; color: {t.ink}; }}
            QPushButton:checked {{ background: {t.accent_soft()}; color: {t.accent}; }}
        """)
        self._import_btn.setStyleSheet(f"""
            QPushButton {{
                background: {t.accent};
                color: white;
                border-radius: 4px;
                font-weight: 600;
                font-size: 14px;
                text-align: center;
            }}
            QPushButton:hover {{ background: {t._lighten(t.accent)}; }}
        """)


_VIDAMI_CHARS = frozenset('{K}`};')


class _FootswitchFilter(QObject):
    """Application-level event filter that routes Vidami footswitch key presses
    to the PlayerPanel regardless of which widget currently has focus."""

    def __init__(self, player_panel, parent=None):
        super().__init__(parent)
        self._panel = player_panel

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.KeyPress and S.get("vidami_enabled"):
            # Don't intercept when a text input has focus
            from PySide6.QtWidgets import QApplication, QLineEdit, QTextEdit, QPlainTextEdit
            focused = QApplication.focusWidget()
            if isinstance(focused, (QLineEdit, QTextEdit, QPlainTextEdit)):
                return False
            char = event.text()
            if char in _VIDAMI_CHARS:
                if self._panel.handle_footswitch(char):
                    return True   # consumed — don't propagate
        return False


class _TrackLoadWorker(QThread):
    """Loads a .stems file and builds its StemPlayer off the UI thread."""
    loaded = Signal(object, object)   # StemPlayer, loops(list)
    error  = Signal(str)

    def __init__(self, stems_path: str, parent=None):
        super().__init__(parent)
        self._stems_path = stems_path

    def run(self):
        try:
            from core.project import load_stems, read_manifest
            from core.player import StemPlayer
            loops = []
            try:
                loops = read_manifest(Path(self._stems_path)).loops
            except Exception:
                loops = []
            project = load_stems(Path(self._stems_path))
            player = StemPlayer()
            player.load(project.stem_paths)
            self.loaded.emit(player, loops)
        except Exception as exc:
            import traceback
            self.error.emit(f"{exc}\n\n{traceback.format_exc()}")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Rehearsal Room")
        self.setMinimumSize(1100, 700)
        self.resize(1400, 860)

        self._theme = Theme()
        self._songs: list[dict] = []
        self._favourites: set[str] = S.get_favourites()
        self._last_viewed: dict[str, float] = S.get_last_viewed()
        self._current_song: dict | None = None
        self._worker: SeparatorWorker | None = None
        self._dl_worker: DownloaderWorker | None = None
        self._meta_worker = None
        self._load_worker: _TrackLoadWorker | None = None
        self._export_worker = None
        self._pending_job: dict | None = None
        # Multi-track import queue
        self._job_queue:  list[dict] = []
        self._job_total:  int        = 0
        self._auto_open_single: bool = False
        self._last_imported_song: dict | None = None
        # Generation token: bumped whenever workers are cancelled so any
        # in-flight (already-queued) signals from retired workers are ignored.
        self._gen: int = 0
        # Keep references to retired-but-not-yet-finished threads so Python
        # never garbage-collects a QThread whose C++ thread is still alive.
        self._retired_workers: list = []

        self._setup_ui()
        self._apply_theme()
        self._load_library()

        # Global key filter so Vidami footswitch works regardless of focus
        self._footswitch_filter = _FootswitchFilter(self._player, self)
        from PySide6.QtWidgets import QApplication
        QApplication.instance().installEventFilter(self._footswitch_filter)

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # sidebar
        self._sidebar = Sidebar(self._theme, len(self._songs))
        self._sidebar.nav_changed.connect(self._on_nav)
        self._sidebar.import_clicked.connect(self._open_import)
        self._sidebar.abort_current.connect(self._cancel_current_job)
        self._sidebar.abort_all.connect(self._cancel_all_jobs)
        root.addWidget(self._sidebar)

        # main content
        self._main = QWidget()
        main_lay = QVBoxLayout(self._main)
        main_lay.setContentsMargins(0, 0, 0, 0)
        main_lay.setSpacing(0)

        # top bar (library view only)
        self._topbar = QFrame()
        self._topbar.setFixedHeight(64)
        self._topbar.setStyleSheet(
            f"QFrame {{ border-bottom: 1px solid {self._theme.border}; background: transparent; }}"
        )
        tb_lay = QHBoxLayout(self._topbar)
        tb_lay.setContentsMargins(28, 0, 28, 0)
        tb_lay.setSpacing(12)
        self._topbar_title = QLabel("Library")
        self._topbar_title.setStyleSheet("font-size: 20px; font-weight: 600;")
        tb_lay.addWidget(self._topbar_title)
        tb_lay.addStretch()

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search tracks or artists…")
        self._search.setFixedWidth(280)
        self._search.textChanged.connect(self._on_search)
        tb_lay.addWidget(self._search)

        self._more_btn = QPushButton("⋮")
        self._more_btn.setProperty("role", "icon")
        self._more_btn.setFixedSize(36, 36)
        self._more_btn.setToolTip("More options")
        self._more_btn.setStyleSheet(
            "QPushButton { font-size: 20px; font-weight: 700; letter-spacing: 0; }"
        )
        self._more_btn.clicked.connect(self._show_more_menu)
        tb_lay.addWidget(self._more_btn)


        main_lay.addWidget(self._topbar)

        # stacked: library / player
        self._stack = QStackedWidget()

        self._library = LibraryPanel(self._theme)
        self._library.song_opened.connect(self._open_song)
        self._library.import_requested.connect(self._open_import)
        self._library.favourite_toggled.connect(self._on_favourite_toggled)
        self._library.delete_requested.connect(self._on_delete_track)
        self._library.set_songs(self._songs)
        self._library.set_favourites(self._favourites)
        self._library.set_last_viewed(self._last_viewed)
        self._stack.addWidget(self._library)  # index 0

        self._player = PlayerPanel(self._theme)
        self._player.back_clicked.connect(self._go_library)
        self._player.export_clicked.connect(self._on_export)
        self._player.save_metadata.connect(self._on_save_metadata)
        self._player.loop_save_requested.connect(self._on_loop_save)
        self._player.loop_delete_requested.connect(self._on_loop_delete)
        self._stack.addWidget(self._player)   # index 1

        main_lay.addWidget(self._stack, 1)
        root.addWidget(self._main, 1)

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _go_library(self):
        self._stack.setCurrentIndex(0)
        self._topbar.show()
        # Always push the current song list and last-viewed times so the panel
        # is never stale after a new import or a return from the player view.
        self._library.set_songs(self._songs)
        self._library.set_last_viewed(self._last_viewed)

    def _open_song(self, song: dict):
        # Ignore clicks while a track is already loading.
        if self._load_worker is not None and self._load_worker.isRunning():
            return

        self._current_song = song
        S.record_viewed(song["id"])
        self._last_viewed = S.get_last_viewed()
        self._library.set_last_viewed(self._last_viewed)

        stems_path = song.get("stems_path")
        if not stems_path:
            # No audio to load — open immediately.
            song.setdefault("loops", [])
            self._enter_player(song, None)
            return

        # Show loading feedback, then load off the UI thread so the spinner
        # animates and the rest of the list stays responsive.
        self._library.show_loading(song["id"])
        self._load_worker = _TrackLoadWorker(stems_path, self)
        self._load_worker.loaded.connect(
            lambda player, loops, s=song: self._on_track_loaded(s, player, loops))
        self._load_worker.error.connect(
            lambda msg, s=song: self._on_track_load_error(s, msg))
        self._load_worker.start()

    def _on_track_loaded(self, song: dict, audio_player, loops: list):
        self._library.clear_loading()
        self._load_worker = None
        song["loops"] = loops
        self._enter_player(song, audio_player)

    def _on_track_load_error(self, song: dict, msg: str):
        self._library.clear_loading()
        self._load_worker = None
        _ErrorDialog(f"Could not load audio:\n\n{msg}", self).exec()

    def _enter_player(self, song: dict, audio_player):
        self._player.load_song(song, audio_player)
        self._stack.setCurrentIndex(1)
        self._topbar.hide()
        self._player.setFocus()

    def _on_nav(self, key: str):
        self._go_library()
        nav_names = {
            "library": "Library",
            "recent":  "Recently played",
            "fav":     "Favourites",
            "artist":  "By artist",
        }
        self._topbar_title.setText(nav_names.get(key, "Library"))
        self._library.set_nav_filter(key if key in ("fav", "recent", "artist") else "all")

    def _on_search(self, text: str):
        self._library.filter(text)

    def _on_favourite_toggled(self, song_id: str, is_fav: bool):
        if is_fav:
            self._favourites.add(song_id)
        else:
            self._favourites.discard(song_id)
        S.set_favourites(self._favourites)
        self._library.set_favourites(self._favourites)

    # ------------------------------------------------------------------
    # Import
    # ------------------------------------------------------------------

    def _open_import(self):
        dlg = ImportDialog(self._theme, self)
        dlg.import_started.connect(self._on_import_started)
        dlg.exec()

    def _on_import_started(self, jobs: list):
        # Drop anything already queued or currently processing (same file
        # picked twice, same URL pasted again).
        def _key(j: dict) -> str:
            return j.get("path") or j.get("url") or ""
        existing = {_key(j) for j in self._job_queue}
        if self._pending_job:
            existing.add(_key(self._pending_job))
        jobs = [j for j in jobs if _key(j) not in existing]
        if not jobs:
            return
        # If a batch is already running, append to it instead of overwriting.
        in_progress = bool(self._pending_job) or bool(self._job_queue)
        if in_progress:
            self._job_queue.extend(jobs)
            self._job_total += len(jobs)
            # Adding more tracks makes this a multi-track batch — don't auto-open.
            self._auto_open_single = False
            # Refresh the N/M counter on the live progress widget.
            current = self._job_total - len(self._job_queue)
            self._sidebar.update_import_total(current, self._job_total)
            return

        self._job_queue = list(jobs)
        self._job_total = len(jobs)
        self._auto_open_single = len(jobs) == 1
        self._last_imported_song = None
        self._process_next_job()

    def _process_next_job(self):
        if not self._job_queue:
            # All done — finish the progress widget then optionally open last track
            self._sidebar.finish_import_progress()
            if self._auto_open_single and self._last_imported_song:
                if self._stack.currentIndex() == 0:
                    song = self._last_imported_song
                    QTimer.singleShot(1200, lambda: self._open_song(song))
            self._job_total = 0
            return

        job = self._job_queue.pop(0)
        self._pending_job = job
        current = self._job_total - len(self._job_queue)   # 1-based
        name = job.get("name", "") or job.get("url", "New track")
        self._sidebar.show_import_progress(name, current, self._job_total)

        if job["kind"] == "youtube":
            self._start_download(job)
        else:
            self._start_separation(job["path"], job)

    def _start_download(self, job: dict):
        from core.downloader import DownloaderWorker
        # Retire any previous download worker before replacing the reference.
        self._retire_worker(self._dl_worker)
        self._dl_worker = None
        token = self._gen
        self._dl_worker = DownloaderWorker(job["url"])
        self._dl_worker.progress.connect(
            lambda pct, msg, t=token: self._on_worker_progress(pct, msg, t))
        self._dl_worker.info_ready.connect(
            lambda title, artist, t=token: self._on_worker_info(title, artist, t))
        self._dl_worker.finished.connect(
            lambda path, info, t=token: self._on_download_done(path, info, job, t))
        self._dl_worker.error.connect(
            lambda m, t=token: self._on_job_error(m, t))
        self._dl_worker.start()

    def _on_download_done(self, path: str, info: dict, job: dict, token: int):
        if token != self._gen:
            return   # stale signal from a cancelled/retired download
        # The download thread has finished; retire it cleanly before separation.
        dl = self._dl_worker
        self._dl_worker = None
        self._start_separation(path, {**job, "yt_info": info})
        self._retire_worker(dl)

    def _start_separation(self, audio_path: str, job: dict):
        # Retire any previous separation worker before replacing the reference.
        self._retire_worker(self._worker)
        self._worker = None
        self._pending_job = {**job, "audio_path": audio_path}
        token = self._gen
        from core.tempdirs import make_temp_dir
        out_dir = make_temp_dir("sep_")
        self._worker = SeparatorWorker(Path(audio_path), job.get("model", "htdemucs"), out_dir)
        self._worker.progress.connect(
            lambda pct, msg, t=token: self._on_worker_progress(pct, msg, t))
        self._worker.finished.connect(
            lambda paths, t=token: self._on_separation_done(paths, t))
        self._worker.error.connect(
            lambda m, t=token: self._on_job_error(m, t))
        self._worker.start()

    def _on_worker_progress(self, pct: int, msg: str, token: int):
        if token != self._gen:
            return   # stale progress from a retired worker
        self._sidebar.update_import_progress(pct, msg)

    def _on_worker_info(self, title: str, artist: str, token: int):
        if token != self._gen:
            return   # stale info from a retired worker
        self._sidebar.set_import_name(title, artist)

    def _on_separation_done(self, stem_paths: dict, token: int | None = None):
        if token is not None and token != self._gen:
            return   # stale signal from a cancelled/retired worker
        if not self._pending_job:
            return   # job was cancelled
        job = self._pending_job

        # Resolve metadata (file tags / yt-dlp / AcoustID) off the UI thread —
        # fingerprinting and the AcoustID lookup can take seconds.
        from core.metadata import MetadataWorker
        self._sidebar.update_import_progress(92, "Identifying song…")
        self._retire_worker(self._meta_worker)
        audio_path = job.get("audio_path") or job.get("path", "")
        self._meta_worker = MetadataWorker(
            Path(audio_path), job.get("yt_info", {}),
            S.get("acoustid_api_key") or "",
        )
        tok = self._gen
        self._meta_worker.done.connect(
            lambda meta, t=tok, sp=stem_paths: self._finalize_import(sp, meta, t))
        self._meta_worker.start()

    def _finalize_import(self, stem_paths: dict, meta: dict, token: int):
        """Save the .stems package and update the library. Runs on the UI thread."""
        if token != self._gen:
            return   # batch was cancelled while metadata was resolving
        if not self._pending_job:
            return
        job = self._pending_job

        try:
            cover = meta.pop("_cover", None)
            name = job.get("name", "")
            fallback_title = os.path.splitext(name)[0] if name else "New Track"
            title  = meta.get("title")  or fallback_title
            artist = meta.get("artist") or "Unknown artist"

            self._sidebar.update_import_progress(96, "Saving stems package…")
            lib_dir = S.library_path()
            lib_dir.mkdir(parents=True, exist_ok=True)
            safe = "".join(c for c in title if c.isalnum() or c in " _-").strip() or "track"
            out_path = _unique_stems_path(lib_dir, safe)

            original_path = job.get("audio_path") or job.get("path", "")
            project = save_stems(
                {k: Path(v) for k, v in stem_paths.items()},
                out_path, title=title, artist=artist,
                source_url=job.get("url", ""), cover=cover,
                original_path=Path(original_path) if original_path else None,
            )

            new_song = song_from_stems_file(out_path) or {
                "id": str(out_path),
                "title": title, "artist": artist,
                "seed": 1042,
                "durationMs": project.manifest.duration_ms,
                "addedLabel": "Just now",
                "source": job.get("kind", "file"),
                "grad": ["#2E6BFF", "#7C5CFF"],
                "stems_path": str(out_path),
            }
        except Exception as exc:
            # Never let a failed save stall the queue — report and move on.
            import traceback
            self._on_job_error(f"{exc}\n\n{traceback.format_exc()}", token)
            return

        # Add to top of list (avoid duplicate if already scanned)
        self._songs = [s for s in self._songs if s.get("stems_path") != str(out_path)]
        self._songs.insert(0, new_song)
        self._library.set_songs(self._songs)
        self._refresh_counts()

        self._pending_job = None
        self._last_imported_song = new_song
        self._process_next_job()

    def _on_processing_complete(self):
        pass  # kept for compatibility

    def _retire_worker(self, worker):
        """Safely tear down a QThread worker without crashing.

        Disconnects its signals (so no late callbacks fire), stops it, and
        keeps a reference until the underlying thread has truly finished so
        Python can't garbage-collect a live QThread.
        """
        if worker is None:
            return
        sigs = [getattr(worker, name) for name in
                ("progress", "finished", "error", "info_ready", "done")
                if hasattr(worker, name)]
        for sig in sigs:
            try:
                sig.disconnect()
            except (RuntimeError, TypeError):
                pass
        # Cooperative cancel only. NEVER call terminate(): force-killing a
        # Python QThread mid-execution can leave the GIL locked and deadlock
        # the whole process. The worker checks isInterruptionRequested() at
        # safe points and exits on its own; its signals are already
        # disconnected, so its result (if any) is ignored.
        worker.requestInterruption()
        # Drop refs to threads that have fully finished, keep live ones around
        # so Python never GCs a running QThread.
        self._retired_workers = [w for w in self._retired_workers if w.isRunning()]
        self._retired_workers.append(worker)

    def _stop_workers(self):
        # Invalidate any in-flight signals from the current workers first.
        self._gen += 1
        self._retire_worker(self._worker)
        self._retire_worker(self._dl_worker)
        self._retire_worker(self._meta_worker)
        self._worker = None
        self._dl_worker = None
        self._meta_worker = None
        self._pending_job = None

    def _cancel_current_job(self):
        """Skip the current track and shrink the batch accordingly.

        The skipped track is dropped from the count, so the displayed N/M
        reflects only the tracks that will actually be imported (e.g. 1/3
        becomes 1/2 after one skip).
        """
        self._stop_workers()
        if self._job_total > 0:
            self._job_total -= 1
        self._process_next_job()

    def _cancel_all_jobs(self):
        """Abort all remaining tracks and hide the progress widget."""
        self._stop_workers()
        self._job_queue = []
        self._job_total = 0
        self._sidebar.reset_import_progress()

    def _on_job_error(self, msg: str, token: int | None = None):
        if token is not None and token != self._gen:
            return   # stale error from a cancelled/retired worker
        self._pending_job = None
        # Drop the failed track from the batch count (same as a manual skip)
        if self._job_total > 0:
            self._job_total -= 1
        # Non-modal so an unattended error doesn't pause the rest of the queue
        dlg = _ErrorDialog(f"Processing failed:\n\n{msg}", self)
        dlg.setModal(False)
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dlg.show()
        self._process_next_job()

    def closeEvent(self, event):
        """Stop any running import workers before the window is destroyed."""
        self._job_queue = []
        self._stop_workers()
        self._library.stop_background()
        if self._load_worker is not None and self._load_worker.isRunning():
            self._load_worker.wait(3000)
        if self._export_worker is not None and self._export_worker.isRunning():
            self._export_worker.wait(5000)
        # Give cooperative workers a moment to unwind so Qt doesn't report a
        # thread being destroyed while still running.
        for w in list(self._retired_workers):
            if w and w.isRunning():
                w.requestInterruption()
                w.wait(3000)
        # Remove this session's temp dirs (extracted stems, downloads, conversions)
        from core.tempdirs import cleanup_all
        cleanup_all()
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # Library scanning
    # ------------------------------------------------------------------

    def _load_library(self):
        self._songs = scan_library()
        self._library.set_songs(self._songs)
        self._refresh_counts()

    def _refresh_counts(self):
        n = len(self._songs)
        self._sidebar.update_count(n)
        self._sidebar.refresh_storage(S.library_path(), n)

    def _show_more_menu(self):
        t = self._theme
        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background: {t.surface};
                border: 1px solid {t.border};
                border-radius: 4px;
                padding: 4px;
            }}
            QMenu::item {{
                padding: 8px 18px 8px 12px;
                font-size: 13px;
                color: {t.ink};
                border-radius: 4px;
            }}
            QMenu::item:selected {{
                background: {t.surface2};
            }}
            QMenu::separator {{
                height: 1px;
                background: {t.border};
                margin: 4px 8px;
            }}
        """)

        settings_action = QAction("Settings", self)
        settings_action.triggered.connect(self._open_settings)
        menu.addAction(settings_action)

        check_action = QAction("Check for updates…", self)
        check_action.triggered.connect(self._check_for_updates)
        menu.addAction(check_action)

        menu.addSeparator()

        about_action = QAction("About", self)
        about_action.triggered.connect(self._open_about)
        menu.addAction(about_action)

        # Align menu's top-right corner to button's bottom-right corner.
        from PySide6.QtCore import QPoint
        btn_rect = self._more_btn.rect()
        pos = self._more_btn.mapToGlobal(btn_rect.bottomRight())
        menu.exec(QPoint(pos.x() - menu.sizeHint().width(), pos.y() + 4))

    def _on_delete_track(self, song: dict):
        title = song.get("title") or "this track"
        dlg = _DeleteConfirmDialog(title, self._theme, self)

        def do_delete():
            stems_path = song.get("stems_path")
            if stems_path:
                try:
                    import os
                    os.remove(stems_path)
                except OSError as exc:
                    _ErrorDialog(f"Could not delete file:\n\n{exc}", self).exec()
                    return

            # Remove from in-memory list and refresh
            song_id = song.get("id")
            self._songs = [s for s in self._songs if s.get("id") != song_id]
            # Also clean up favourites / last_viewed if present
            self._favourites.discard(song_id or "")
            self._last_viewed.pop(song_id or "", None)
            from core import settings as S
            S.set_favourites(self._favourites)

            self._library.set_songs(self._songs)
            self._library.set_favourites(self._favourites)
            self._library.set_last_viewed(self._last_viewed)
            self._refresh_counts()

        dlg.confirmed.connect(do_delete)
        dlg.exec()

    def _open_settings(self):
        dlg = SettingsDialog(self._theme, self)
        dlg.library_changed.connect(lambda _: self._load_library())
        dlg.exec()

    def _check_for_updates(self):
        from core.version import __version__, GITHUB_REPO
        from core.updater import UpdateChecker, _parse_version

        # Disable the button while checking to prevent double-clicks
        self._more_btn.setEnabled(False)

        self._update_checker = UpdateChecker(GITHUB_REPO, parent=self)

        def on_result(latest: str, url: str):
            self._more_btn.setEnabled(True)
            current_t = _parse_version(__version__)
            latest_t  = _parse_version(latest)
            if latest_t > current_t:
                _UpdateAvailableDialog(__version__, latest, url, self._theme, self).exec()
            else:
                _UpToDateDialog(__version__, self._theme, self).exec()

        def on_error(msg: str):
            self._more_btn.setEnabled(True)
            _ErrorDialog(f"Update check failed:\n\n{msg}", self).exec()

        self._update_checker.result.connect(on_result)
        self._update_checker.error.connect(on_error)
        self._update_checker.start()

    def _open_about(self):
        AboutDialog(self._theme, self).exec()

    # ------------------------------------------------------------------
    # Metadata save
    # ------------------------------------------------------------------

    def _on_save_metadata(self, data: dict):
        song = self._current_song
        if not song:
            return
        stems_path = song.get("stems_path")
        if not stems_path:
            return  # demo/unsaved song — nothing to write to

        from core.project import read_manifest, update_manifest
        try:
            manifest = read_manifest(Path(stems_path))
            manifest.title  = data.get("title",  manifest.title)
            manifest.artist = data.get("artist", manifest.artist)

            # Update stem labels in the manifest
            new_labels = data.get("stem_labels", {})
            for stem in manifest.stems:
                if stem.id in new_labels:
                    stem.label = new_labels[stem.id]

            update_manifest(Path(stems_path), manifest)

            # Keep the in-memory song list in sync so the library reflects the change
            new_title = manifest.title
            new_artist = manifest.artist
            song["title"]  = new_title
            song["artist"] = new_artist
            for s in self._songs:
                if s.get("stems_path") == stems_path:
                    s["title"]  = new_title
                    s["artist"] = new_artist
                    break
            self._library.set_songs(self._songs)

        except Exception as exc:
            _ErrorDialog(f"Could not save metadata:\n\n{exc}", self).exec()

    # ------------------------------------------------------------------
    # Loop save / delete
    # ------------------------------------------------------------------

    def _on_loop_save(self, loop):
        song = self._current_song
        if not song or not song.get("stems_path"):
            return
        from core.project import read_manifest, update_manifest
        try:
            manifest = read_manifest(Path(song["stems_path"]))
            # Replace if same name exists, otherwise append
            manifest.loops = [lp for lp in manifest.loops if lp.name != loop.name]
            manifest.loops.append(loop)
            update_manifest(Path(song["stems_path"]), manifest)
            song["loops"] = manifest.loops
            self._player.set_loops(manifest.loops)
            self._refresh_counts()
        except Exception as exc:
            _ErrorDialog(f"Could not save loop:\n\n{exc}", self).exec()

    def _on_loop_delete(self, name: str):
        song = self._current_song
        if not song or not song.get("stems_path"):
            return
        from core.project import read_manifest, update_manifest
        try:
            manifest = read_manifest(Path(song["stems_path"]))
            manifest.loops = [lp for lp in manifest.loops if lp.name != name]
            update_manifest(Path(song["stems_path"]), manifest)
            song["loops"] = manifest.loops
            self._player.set_loops(manifest.loops)
            self._refresh_counts()
        except Exception as exc:
            _ErrorDialog(f"Could not delete loop:\n\n{exc}", self).exec()

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def _on_export(self, song: dict, mode: str = "all"):
        stems_path = song.get("stems_path")
        if not stems_path:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, "Export", "This is a demo track — no .stems file to export.")
            return
        if mode == "current":
            self._export_current(song)
        elif mode == "original":
            self._export_original(song)
        else:
            self._export_all(song)

    def _export_all(self, song: dict):
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        import shutil
        dest, _ = QFileDialog.getSaveFileName(
            self, "Export .stems file", f"{song['title']}.stems", "Stems files (*.stems)")
        if dest:
            shutil.copy2(song["stems_path"], dest)
            QMessageBox.information(self, "Exported", f"Saved to {dest}")

    def _ask_export_format(self) -> str | None:
        """Prompt for an export format; returns the file extension (e.g. '.flac')."""
        from PySide6.QtWidgets import QInputDialog
        from core.project import EXPORT_FORMATS
        names = [n for n, _ in EXPORT_FORMATS]
        name, ok = QInputDialog.getItem(
            self, "Export format", "Format:", names, 0, False)
        if not ok:
            return None
        return dict(EXPORT_FORMATS)[name]

    def _export_current(self, song: dict):
        from PySide6.QtWidgets import QFileDialog
        player = self._player.audio_player()
        if player is None or not player.has_audio():
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, "Export", "No audio is loaded for this track.")
            return
        ext = self._ask_export_format()
        if not ext:
            return
        dest, _ = QFileDialog.getSaveFileName(
            self, "Export current mix", f"{song['title']} (mix){ext}", f"Audio (*{ext})")
        if not dest:
            return
        from core.export import ExportWorker
        self._start_export(ExportWorker("current", dest, player=player), "Exporting current mix")

    def _export_original(self, song: dict):
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        # Cheap manifest check before prompting — avoids extracting just to learn there's none.
        try:
            from core.project import read_manifest
            has_original = bool(read_manifest(song["stems_path"]).original)
        except Exception as exc:
            QMessageBox.warning(self, "Export failed", str(exc))
            return
        if not has_original:
            QMessageBox.information(
                self, "Export",
                "This track has no embedded original audio (it was imported "
                "before originals were stored).")
            return
        ext = self._ask_export_format()
        if not ext:
            return
        dest, _ = QFileDialog.getSaveFileName(
            self, "Export original audio", f"{song['title']} (original){ext}", f"Audio (*{ext})")
        if not dest:
            return
        from core.export import ExportWorker
        self._start_export(
            ExportWorker("original", dest, stems_path=song["stems_path"]),
            "Exporting original audio")

    def _start_export(self, worker, title: str):
        """Run an ExportWorker with a modal busy dialog; report the result."""
        from PySide6.QtWidgets import QProgressDialog, QMessageBox
        from PySide6.QtCore import Qt
        dlg = QProgressDialog("Preparing…", "", 0, 0, self)   # 0,0 = busy indicator
        dlg.setWindowTitle(title)
        dlg.setCancelButton(None)
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)

        self._retire_worker(self._export_worker)
        self._export_worker = worker
        worker.progress.connect(dlg.setLabelText)

        def _ok(dest: str):
            dlg.close()
            QMessageBox.information(self, "Exported", f"Saved to {dest}")

        def _err(msg: str):
            dlg.close()
            QMessageBox.warning(self, "Export failed", msg)

        worker.done.connect(_ok)
        worker.error.connect(_err)
        worker.finished.connect(lambda: setattr(self, "_export_worker", None))
        worker.start()
        dlg.show()

    # ------------------------------------------------------------------
    # Theme
    # ------------------------------------------------------------------

    def _apply_theme(self):
        qss = self._theme.qss()
        self.setStyleSheet(qss)
