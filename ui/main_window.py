"""Main window — sidebar + stacked content area (library / player)."""

from PySide6.QtCore import Qt, Signal, QObject, QEvent
from PySide6.QtGui import QColor, QPalette, QFont
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QLabel, QPushButton, QFrame, QLineEdit, QStackedWidget,
    QSizePolicy, QDialog
)

from ui.theme import Theme, STEM_IDS
from ui.library_panel import LibraryPanel
from ui.player_panel import PlayerPanel
from ui.import_dialog import ImportDialog, ProcessingDialog
from ui.settings_dialog import SettingsDialog
from core.separator import SeparatorWorker
from core.downloader import DownloaderWorker
from core.project import save_stems, load_stems
from core import settings as S
from core.library import scan as scan_library, song_from_stems_file
from pathlib import Path
import tempfile, os


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
                border-radius: 8px;
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
                border-radius: 8px;
                padding: 12px;
            }
        """)


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
        text = f"{self._icon}  {self._label}"
        if self._count >= 0:
            text += f"   {self._count}"
        self.setText(text)


class Sidebar(QFrame):
    nav_changed = Signal(str)
    import_clicked = Signal()

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
            "background: #17171B; border-radius: 10px;"
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

        # nav label
        nav_lbl = QLabel("Library")
        nav_lbl.setStyleSheet(
            "font-size: 10px; font-weight: 600; letter-spacing: 0.13em; text-transform: uppercase; "
            "color: #93939C; padding: 8px 10px 2px;"
        )
        lay.addWidget(nav_lbl)

        self._nav_buttons: dict[str, SidebarButton] = {}
        for key, icon, label, cnt in [
            ("library", "⊞", "All tracks", song_count),
            ("recent",  "⏱", "Recent", -1),
            ("fav",     "☆", "Favorites", -1),
        ]:
            btn = SidebarButton(icon, label, cnt)
            btn.clicked.connect(lambda checked, k=key: self._on_nav(k))
            lay.addWidget(btn)
            self._nav_buttons[key] = btn

        self._nav_buttons["library"].setChecked(True)

        lay.addStretch()

        # import CTA
        self._import_btn = QPushButton("+ Import track")
        self._import_btn.setFixedHeight(40)
        self._import_btn.clicked.connect(self.import_clicked)
        lay.addWidget(self._import_btn)
        lay.addSpacing(10)

        # storage
        storage = QFrame()
        storage.setStyleSheet(
            f"QFrame {{ border: 1px solid #E2E2DC; border-radius: 10px; background: transparent; }}"
        )
        stor_lay = QVBoxLayout(storage)
        stor_lay.setContentsMargins(11, 10, 11, 10)
        stor_lay.setSpacing(3)
        stor_top = QLabel("Library storage")
        stor_top.setStyleSheet("font-size: 11px; font-weight: 500;")
        self._stor_lbl = QLabel("")
        self._stor_lbl.setStyleSheet("font-size: 11px; color: #93939C;")
        stor_lay.addWidget(stor_top)
        stor_lay.addWidget(self._stor_lbl)
        lay.addWidget(storage)

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

    def _apply_theme(self):
        t = self._theme
        self.setStyleSheet(f"""
            QFrame {{
                background: {t.surface};
                border-right: 1px solid {t.border};
            }}
            QPushButton {{
                background: transparent;
                color: {t.ink2};
                border-radius: 8px;
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
                border-radius: 10px;
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


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Rehearsal Room")
        self.setMinimumSize(1100, 700)
        self.resize(1400, 860)

        self._theme = Theme()
        self._songs: list[dict] = []
        self._current_song: dict | None = None
        self._worker: SeparatorWorker | None = None
        self._dl_worker: DownloaderWorker | None = None
        self._proc_dlg: ProcessingDialog | None = None
        self._pending_job: dict | None = None

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

        settings_btn = QPushButton("⚙")
        settings_btn.setProperty("role", "icon")
        settings_btn.setFixedSize(36, 36)
        settings_btn.setToolTip("Settings")
        settings_btn.clicked.connect(self._open_settings)
        tb_lay.addWidget(settings_btn)

        import_top_btn = QPushButton("+ Import")
        import_top_btn.setProperty("role", "primary")
        import_top_btn.setFixedHeight(36)
        import_top_btn.clicked.connect(self._open_import)
        tb_lay.addWidget(import_top_btn)
        main_lay.addWidget(self._topbar)

        # stacked: library / player
        self._stack = QStackedWidget()

        self._library = LibraryPanel(self._theme)
        self._library.song_opened.connect(self._open_song)
        self._library.import_requested.connect(self._open_import)
        self._library.set_songs(self._songs)
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

    def _open_song(self, song: dict):
        self._current_song = song
        # Attach loops from manifest if not already present
        if song.get("stems_path") and "loops" not in song:
            try:
                from core.project import read_manifest
                manifest = read_manifest(Path(song["stems_path"]))
                song["loops"] = manifest.loops
            except Exception:
                song["loops"] = []
        audio_player = None
        stems_path = song.get("stems_path")
        if stems_path:
            try:
                from core.project import load_stems
                from core.player import StemPlayer
                project = load_stems(Path(stems_path))
                audio_player = StemPlayer()
                audio_player.load(project.stem_paths)
            except Exception as exc:
                import traceback
                _ErrorDialog(f"Could not load audio:\n\n{exc}\n\n{traceback.format_exc()}", self).exec()

        self._player.load_song(song, audio_player)
        self._stack.setCurrentIndex(1)
        self._topbar.hide()
        self._player.setFocus()

    def _on_nav(self, key: str):
        self._go_library()
        nav_names = {"library": "Library", "recent": "Recent", "fav": "Favorites"}
        self._topbar_title.setText(nav_names.get(key, "Library"))

    def _on_search(self, text: str):
        self._library.filter(text)

    # ------------------------------------------------------------------
    # Import
    # ------------------------------------------------------------------

    def _open_import(self):
        dlg = ImportDialog(self._theme, self)
        dlg.import_started.connect(self._on_import_started)
        dlg.exec()

    def _on_import_started(self, job: dict):
        self._pending_job = job
        self._proc_dlg = ProcessingDialog(job, self._theme, self)
        self._proc_dlg.completed.connect(self._on_processing_complete)
        self._proc_dlg.cancelled.connect(self._cancel_job)
        self._proc_dlg.show()

        if job["kind"] == "youtube":
            self._start_download(job)
        else:
            self._start_separation(job["path"], job)

    def _start_download(self, job: dict):
        from core.downloader import DownloaderWorker
        self._dl_worker = DownloaderWorker(job["url"])
        self._dl_worker.progress.connect(lambda pct, msg: self._proc_dlg and self._proc_dlg.update_progress(pct, msg))
        self._dl_worker.finished.connect(lambda path, info: self._start_separation(path, {**job, "yt_info": info}))
        self._dl_worker.error.connect(self._on_job_error)
        self._dl_worker.start()

    def _start_separation(self, audio_path: str, job: dict):
        self._pending_job = {**job, "audio_path": audio_path}
        out_dir = Path(tempfile.mkdtemp(prefix="rehearsalroom_sep_"))
        self._worker = SeparatorWorker(Path(audio_path), job.get("model", "htdemucs"), out_dir)
        self._worker.progress.connect(lambda pct, msg: self._proc_dlg and self._proc_dlg.update_progress(pct, msg))
        self._worker.finished.connect(self._on_separation_done)
        self._worker.error.connect(self._on_job_error)
        self._worker.start()

    def _on_separation_done(self, stem_paths: dict):
        if not self._proc_dlg:
            return
        job = self._pending_job or {}

        # --- Resolve metadata (strategies 1–3) ---
        from core.metadata import from_file_tags, from_yt_info, from_acoustid, merge

        tags = from_file_tags(Path(job.get("audio_path", job.get("path", ""))))
        yt   = from_yt_info(job.get("yt_info", {}))

        # AcoustID fingerprinting (only if key is configured and no metadata yet)
        acoustid_meta: dict = {}
        api_key = S.get("acoustid_api_key") or ""
        if api_key and not (tags.get("title") or yt.get("title")):
            self._proc_dlg.update_progress(92, "Identifying song via AcoustID…")
            audio_path = job.get("audio_path") or job.get("path", "")
            acoustid_meta = from_acoustid(Path(audio_path), api_key)

        # Merge: file tags beat yt-dlp beat AcoustID (tags are most reliable)
        meta = merge(acoustid_meta, yt, tags)

        name = job.get("name", "")
        fallback_title = os.path.splitext(name)[0] if name else "New Track"
        title  = meta.get("title")  or fallback_title
        artist = meta.get("artist") or "Unknown artist"

        lib_dir = S.library_path()
        lib_dir.mkdir(parents=True, exist_ok=True)
        safe = "".join(c for c in title if c.isalnum() or c in " _-").strip() or "track"
        out_path = _unique_stems_path(lib_dir, safe)

        project = save_stems(
            {k: Path(v) for k, v in stem_paths.items()},
            out_path, title=title, artist=artist,
            source_url=job.get("url", ""),
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

        # Add to top of list (avoid duplicate if already scanned)
        self._songs = [s for s in self._songs if s.get("stems_path") != str(out_path)]
        self._songs.insert(0, new_song)
        self._library.set_songs(self._songs)
        self._refresh_counts()

        self._proc_dlg.on_finished()
        self._proc_dlg.completed.connect(lambda: self._open_song(new_song))

    def _on_processing_complete(self):
        pass  # handled in _on_separation_done via completed signal chain

    def _cancel_job(self):
        if self._worker and self._worker.isRunning():
            self._worker.terminate()
        if self._dl_worker and self._dl_worker.isRunning():
            self._dl_worker.terminate()

    def _on_job_error(self, msg: str):
        if self._proc_dlg:
            self._proc_dlg.reject()
        _ErrorDialog(f"Processing failed:\n\n{msg}", self).exec()

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

    def _open_settings(self):
        dlg = SettingsDialog(self._theme, self)
        dlg.library_changed.connect(lambda _: self._load_library())
        dlg.exec()

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

    def _on_export(self, song: dict):
        stems_path = song.get("stems_path")
        if not stems_path:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, "Export", "This is a demo track — no .stems file to export.")
            return
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        import shutil
        dest, _ = QFileDialog.getSaveFileName(self, "Export .stems file", f"{song['title']}.stems", "Stems files (*.stems)")
        if dest:
            shutil.copy2(stems_path, dest)
            QMessageBox.information(self, "Exported", f"Saved to {dest}")

    # ------------------------------------------------------------------
    # Theme
    # ------------------------------------------------------------------

    def _apply_theme(self):
        qss = self._theme.qss()
        self.setStyleSheet(qss)
