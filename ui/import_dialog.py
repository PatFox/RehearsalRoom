"""ImportDialog, ImportProgressWidget, and ProcessingDialog."""

import os
from PySide6.QtCore import Qt, Signal, QTimer, QUrl, QMimeData
from PySide6.QtGui import QColor, QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QFrame, QWidget, QFileDialog, QStackedWidget,
    QSizePolicy, QProgressBar, QMessageBox, QScrollArea
)

from ui.theme import Theme, STEM_IDS, STEM_LABELS


class SegmentedControl(QWidget):
    tab_changed = Signal(str)

    def __init__(self, tabs: list[tuple[str, str]], theme: Theme, parent=None):
        super().__init__(parent)
        self._theme = theme
        self._buttons: dict[str, QPushButton] = {}
        self._active: str = tabs[0][0]

        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)
        self.setFixedHeight(46)

        for key, label in tabs:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setProperty("tab_key", key)
            btn.clicked.connect(lambda checked, k=key: self._select(k))
            lay.addWidget(btn)
            self._buttons[key] = btn

        self._select(self._active)
        self._apply_theme()

    def _select(self, key: str):
        self._active = key
        for k, btn in self._buttons.items():
            active = k == key
            btn.setChecked(active)
            if active:
                btn.setStyleSheet(
                    f"background: {self._theme.surface}; color: {self._theme.ink}; "
                    f"border-radius: 9px; font-weight: 600; font-size: 13px; padding: 8px;"
                )
            else:
                btn.setStyleSheet(
                    f"background: transparent; color: {self._theme.ink2}; "
                    f"border-radius: 9px; font-size: 13px; padding: 8px;"
                )
        self.tab_changed.emit(key)

    def _apply_theme(self):
        self.setStyleSheet(f"background: {self._theme.surface2}; border-radius: 10px;")

    def active(self) -> str:
        return self._active


class DropZone(QFrame):
    file_dropped = Signal(str)

    def __init__(self, theme: Theme, parent=None):
        super().__init__(parent)
        self._theme = theme
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._setup_ui()
        self._apply_style(hovered=False)

    def _setup_ui(self):
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.setSpacing(8)
        lay.setContentsMargins(24, 42, 24, 42)

        self._title = QLabel("Drop an audio file here")
        self._title.setStyleSheet("font-size: 15px; font-weight: 600;")
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        sub = QLabel("or click to browse your computer")
        sub.setStyleSheet(f"font-size: 13px; color: {self._theme.ink3};")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)

        formats_row = QHBoxLayout()
        formats_row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        formats_row.setSpacing(6)
        for fmt in ["mp3", "wav", "flac", "m4a", "ogg"]:
            chip = QLabel(fmt.upper())
            chip.setStyleSheet(
                f"font-family: 'Consolas', monospace; font-size: 10px; font-weight: 600; "
                f"background: {self._theme.surface2}; color: {self._theme.ink3}; "
                f"padding: 3px 7px; border-radius: 5px;"
            )
            formats_row.addWidget(chip)

        formats_w = QWidget()
        formats_w.setLayout(formats_row)

        lay.addWidget(self._title)
        lay.addWidget(sub)
        lay.addSpacing(6)
        lay.addWidget(formats_w)

    def _apply_style(self, hovered: bool):
        if hovered:
            self.setStyleSheet(
                f"QFrame {{ border: 1.5px dashed {self._theme.accent}; "
                f"border-radius: 16px; background: {self._theme.accent_soft()}; }}"
            )
        else:
            self.setStyleSheet(
                f"QFrame {{ border: 1.5px dashed {self._theme.border_strong}; "
                f"border-radius: 16px; background: transparent; }}"
            )

    def mousePressEvent(self, e):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open audio file", "",
            "Audio files (*.mp3 *.wav *.flac *.m4a *.ogg);;All files (*)"
        )
        if path:
            self.file_dropped.emit(path)

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._apply_style(hovered=True)

    def dragLeaveEvent(self, e):
        self._apply_style(hovered=False)

    def dropEvent(self, e: QDropEvent):
        self._apply_style(hovered=False)
        urls = e.mimeData().urls()
        if urls:
            self.file_dropped.emit(urls[0].toLocalFile())


class ModelOption(QFrame):
    selected = Signal(str)

    def __init__(self, key: str, title: str, desc: str, theme: Theme, parent=None):
        super().__init__(parent)
        self._key = key
        self._theme = theme
        self._is_sel = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(13, 11, 13, 11)
        lay.setSpacing(3)

        top = QHBoxLayout()
        self._title_lbl = QLabel(title)
        self._title_lbl.setStyleSheet("font-size: 13px; font-weight: 600;")
        self._tick = QFrame()
        self._tick.setFixedSize(16, 16)
        top.addWidget(self._title_lbl)
        top.addStretch()
        top.addWidget(self._tick)
        lay.addLayout(top)

        self._desc_lbl = QLabel(desc)
        self._desc_lbl.setWordWrap(True)
        self._desc_lbl.setStyleSheet(f"font-size: 11px; color: {theme.ink3};")
        lay.addWidget(self._desc_lbl)

        self._set_style(False)

    def set_selected(self, sel: bool):
        self._is_sel = sel
        self._set_style(sel)

    def _set_style(self, sel: bool):
        if sel:
            self.setStyleSheet(
                f"QFrame {{ border: 1.5px solid {self._theme.accent}; border-radius: 10px; "
                f"background: {self._theme.accent_soft()}; }}"
            )
            self._tick.setStyleSheet(
                f"background: {self._theme.accent}; border-radius: 8px;"
            )
        else:
            self.setStyleSheet(
                f"QFrame {{ border: 1px solid {self._theme.border}; border-radius: 10px; background: transparent; }}"
            )
            self._tick.setStyleSheet(
                f"border: 1.5px solid {self._theme.border_strong}; border-radius: 8px; background: transparent;"
            )

    def mousePressEvent(self, e):
        self.selected.emit(self._key)


# ---------------------------------------------------------------------------
# _ItemListWidget — scrollable queue of labelled items with remove buttons
# ---------------------------------------------------------------------------

class _ItemListWidget(QWidget):
    """Compact scrollable list of queued items; each row has a label + × button."""

    def __init__(self, theme, parent=None):
        super().__init__(parent)
        self._theme = theme
        self._items: list[tuple[str, str]] = []   # (key, label)
        self._on_change: "callable | None" = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setMaximumHeight(108)
        scroll.setStyleSheet("background: transparent;")

        self._container = QWidget()
        self._container.setStyleSheet("background: transparent;")
        self._lay = QVBoxLayout(self._container)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(2)
        self._lay.addStretch()

        scroll.setWidget(self._container)
        outer.addWidget(scroll)
        self.hide()

    def set_on_change(self, fn):
        self._on_change = fn

    def add(self, key: str, label: str):
        if any(k == key for k, _ in self._items):
            return   # deduplicate
        self._items.append((key, label))
        self._rebuild()

    def remove(self, key: str):
        self._items = [(k, l) for k, l in self._items if k != key]
        self._rebuild()

    def keys(self) -> list[str]:
        return [k for k, _ in self._items]

    def count(self) -> int:
        return len(self._items)

    def _rebuild(self):
        # Clear all rows except the trailing stretch
        while self._lay.count() > 1:
            item = self._lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        t = self._theme
        for key, label in self._items:
            row = QWidget()
            row.setStyleSheet(f"background: {t.surface2}; border-radius: 6px;")
            rl = QHBoxLayout(row)
            rl.setContentsMargins(8, 4, 4, 4)
            rl.setSpacing(6)

            lbl = QLabel(label)
            lbl.setStyleSheet(f"font-size: 12px; color: {t.ink};")
            lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            lbl.setToolTip(key)

            rm = QPushButton("×")
            rm.setFixedSize(20, 20)
            rm.setStyleSheet(
                f"QPushButton {{ border: none; background: transparent; font-size: 14px; "
                f"color: {t.ink3}; }}"
                f"QPushButton:hover {{ color: #E53E3E; }}"
            )
            rm.clicked.connect(lambda _, k=key: (self.remove(k), self._on_change and self._on_change()))

            rl.addWidget(lbl, 1)
            rl.addWidget(rm)
            self._lay.insertWidget(self._lay.count() - 1, row)

        self.setVisible(len(self._items) > 0)
        if self._on_change:
            self._on_change()


# ---------------------------------------------------------------------------
# ImportDialog
# ---------------------------------------------------------------------------

class ImportDialog(QDialog):
    import_started = Signal(list)   # list of job dicts

    def __init__(self, theme: Theme, parent=None):
        super().__init__(parent)
        self._theme = theme
        self._model = "htdemucs"
        self._file_paths: list[str] = []
        self._yt_urls:    list[str] = []
        self.setWindowTitle("Import tracks")
        self.setModal(True)
        self.setFixedWidth(540)
        self._setup_ui()
        self._apply_theme()

    # ------------------------------------------------------------------ build

    def _setup_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 24)
        lay.setSpacing(0)

        # header
        head_row = QHBoxLayout()
        head_lay = QVBoxLayout()
        title_lbl = QLabel("Import tracks")
        title_lbl.setStyleSheet("font-size: 19px; font-weight: 600;")
        sub_lbl = QLabel("Split songs into vocals, drums, bass and other.")
        sub_lbl.setStyleSheet(f"font-size: 13px; color: {self._theme.ink3};")
        head_lay.addWidget(title_lbl)
        head_lay.addWidget(sub_lbl)
        close_btn = QPushButton("✕")
        close_btn.setProperty("role", "icon")
        close_btn.setFixedSize(32, 32)
        close_btn.clicked.connect(self.reject)
        head_row.addLayout(head_lay, 1)
        head_row.addWidget(close_btn, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(head_row)
        lay.addSpacing(4)

        # tabs
        self._tabs = SegmentedControl(
            [("file", "From file"), ("youtube", "From YouTube")],
            self._theme
        )
        self._tabs.tab_changed.connect(self._switch_tab)
        lay.addWidget(self._tabs)
        lay.addSpacing(4)

        # stacked content
        self._stack = QStackedWidget()

        # ── file tab ──────────────────────────────────────────────────
        file_w = QWidget()
        file_lay = QVBoxLayout(file_w)
        file_lay.setContentsMargins(0, 0, 0, 0)
        file_lay.setSpacing(6)

        self._dropzone = DropZone(self._theme)
        self._dropzone.file_dropped.connect(self._on_file_dropped)
        file_lay.addWidget(self._dropzone)

        self._file_list = _ItemListWidget(self._theme)
        self._file_list.set_on_change(self._update_start_btn)
        file_lay.addWidget(self._file_list)

        self._stack.addWidget(file_w)

        # ── youtube tab ───────────────────────────────────────────────
        yt_w = QWidget()
        yt_lay = QVBoxLayout(yt_w)
        yt_lay.setContentsMargins(0, 0, 0, 0)
        yt_lay.setSpacing(6)

        url_row = QHBoxLayout()
        url_row.setSpacing(8)
        self._url_input = QLineEdit()
        self._url_input.setPlaceholderText("https://youtube.com/watch?v=…")
        self._url_input.returnPressed.connect(self._add_url)
        url_row.addWidget(self._url_input, 1)
        add_btn = QPushButton("Add")
        add_btn.setProperty("role", "outline")
        add_btn.setFixedHeight(34)
        add_btn.clicked.connect(self._add_url)
        url_row.addWidget(add_btn)
        yt_lay.addLayout(url_row)

        self._yt_list = _ItemListWidget(self._theme)
        self._yt_list.set_on_change(self._update_start_btn)
        yt_lay.addWidget(self._yt_list)

        note = QLabel(
            "Audio is downloaded at the highest available quality, "
            "then separated locally. Nothing is uploaded."
        )
        note.setWordWrap(True)
        note.setStyleSheet(
            f"font-size: 12px; color: {self._theme.ink3}; margin-top: 4px;"
        )
        yt_lay.addWidget(note)
        yt_lay.addStretch()

        self._stack.addWidget(yt_w)

        lay.addWidget(self._stack)
        lay.addSpacing(16)

        # model options
        opts_row = QHBoxLayout()
        opts_row.setSpacing(12)
        self._opt_balanced = ModelOption(
            "htdemucs", "Balanced",
            "htdemucs · fast, great for most tracks", self._theme
        )
        self._opt_ft = ModelOption(
            "htdemucs_ft", "Fine-tuned",
            "htdemucs_ft · slower, cleaner separation", self._theme
        )
        self._opt_balanced.selected.connect(self._select_model)
        self._opt_ft.selected.connect(self._select_model)
        opts_row.addWidget(self._opt_balanced)
        opts_row.addWidget(self._opt_ft)
        lay.addLayout(opts_row)
        self._select_model("htdemucs")
        lay.addSpacing(18)

        # footer
        foot = QHBoxLayout()
        foot.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setProperty("role", "ghost")
        cancel_btn.clicked.connect(self.reject)
        self._start_btn = QPushButton("✦  Choose files & separate")
        self._start_btn.setProperty("role", "primary")
        self._start_btn.setEnabled(False)
        self._start_btn.clicked.connect(self._on_start)
        foot.addWidget(cancel_btn)
        foot.addWidget(self._start_btn)
        lay.addLayout(foot)

    def _apply_theme(self):
        self.setStyleSheet(
            f"QDialog {{ background: {self._theme.surface}; border-radius: 22px; }}"
        )

    # ------------------------------------------------------------------ helpers

    def _total_count(self) -> int:
        return self._file_list.count() + self._yt_list.count()

    def _update_start_btn(self):
        n = self._total_count()
        if n == 0:
            tab = self._tabs.active()
            if tab == "file":
                self._start_btn.setText("✦  Choose files & separate")
            else:
                self._start_btn.setText("✦  Fetch & separate")
            self._start_btn.setEnabled(False)
        else:
            label = f"Separate {n} track{'s' if n != 1 else ''}"
            self._start_btn.setText(f"✦  {label}")
            self._start_btn.setEnabled(True)

    def _switch_tab(self, key: str):
        self._stack.setCurrentIndex(0 if key == "file" else 1)
        self._update_start_btn()

    def _select_model(self, key: str):
        self._model = key
        self._opt_balanced.set_selected(key == "htdemucs")
        self._opt_ft.set_selected(key == "htdemucs_ft")

    # ------------------------------------------------------------------ input handlers

    def _on_file_dropped(self, path: str):
        label = os.path.basename(path)
        self._file_list.add(path, label)

    def _add_url(self):
        url = self._url_input.text().strip()
        if not url:
            return
        # Truncate display label for very long URLs
        label = url if len(url) <= 60 else url[:57] + "…"
        self._yt_list.add(url, label)
        self._url_input.clear()

    # ------------------------------------------------------------------ start

    def _on_start(self):
        # If file tab active and no files queued, open file picker first
        if self._tabs.active() == "file" and self._file_list.count() == 0:
            paths, _ = QFileDialog.getOpenFileNames(
                self, "Open audio files", "",
                "Audio files (*.mp3 *.wav *.flac *.m4a *.ogg);;All files (*)"
            )
            for p in paths:
                self._file_list.add(p, os.path.basename(p))
            if self._file_list.count() == 0:
                return

        # If YouTube tab active and no URLs queued, try adding current input
        if self._tabs.active() == "youtube" and self._yt_list.count() == 0:
            self._add_url()
            if self._yt_list.count() == 0:
                return

        jobs: list[dict] = []
        for path in self._file_list.keys():
            jobs.append({
                "kind":  "file",
                "path":  path,
                "model": self._model,
                "name":  os.path.basename(path),
            })
        for url in self._yt_list.keys():
            jobs.append({
                "kind":  "youtube",
                "url":   url,
                "model": self._model,
                "name":  "",
            })

        if jobs:
            self.import_started.emit(jobs)
            self.accept()


# ---------------------------------------------------------------------------
# ImportProgressWidget — compact sidebar panel shown during processing
# ---------------------------------------------------------------------------

class ImportProgressWidget(QFrame):
    """Shown in the sidebar while a track is being imported/processed.

    Replaces the modal ProcessingDialog so the user can continue using
    the rest of the application during import.
    """
    abort_current = Signal()   # skip this track, continue queue
    abort_all     = Signal()   # stop all remaining tracks

    def __init__(self, theme, parent=None):
        super().__init__(parent)
        self._theme = theme
        self._current = 1
        self._total   = 1
        self._setup_ui()
        self.hide()

    def _setup_ui(self):
        from ui.theme import Theme
        t = self._theme
        self.setStyleSheet(
            f"QFrame {{ background: {t.surface2}; border-radius: 12px; border: none; }}"
        )

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(5)

        # Header row: "IMPORTING TRACK…" + abort button
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(4)

        self._header_lbl = QLabel("IMPORTING TRACK…")
        self._header_lbl.setStyleSheet(
            f"font-size: 9px; font-weight: 700; letter-spacing: 0.1em; color: {t.ink3};"
            f" background: transparent;"
        )
        header_row.addWidget(self._header_lbl, 1)

        self._abort_btn = QPushButton("✕")
        self._abort_btn.setFixedSize(18, 18)
        self._abort_btn.setToolTip("Stop importing")
        self._abort_btn.setStyleSheet(
            f"QPushButton {{ border: none; background: transparent; font-size: 11px; "
            f"color: {t.ink3}; padding: 0; border-radius: 4px; }}"
            f"QPushButton:hover {{ background: #E53E3E22; color: #E53E3E; }}"
        )
        self._abort_btn.clicked.connect(self._on_abort_clicked)
        header_row.addWidget(self._abort_btn)
        lay.addLayout(header_row)

        # Track name
        self._track_lbl = QLabel("")
        self._track_lbl.setStyleSheet(
            f"font-size: 12px; font-weight: 600; color: {t.ink}; background: transparent;"
        )
        self._track_lbl.setWordWrap(False)
        self._track_lbl.setMaximumWidth(210)
        from PySide6.QtWidgets import QSizePolicy
        self._track_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        lay.addWidget(self._track_lbl)

        # Progress bar + percentage on the same row
        prog_row = QHBoxLayout()
        prog_row.setContentsMargins(0, 0, 0, 0)
        prog_row.setSpacing(8)

        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(5)
        self._bar.setStyleSheet(
            f"QProgressBar {{ background: {t.surface3}; border-radius: 3px; border: none; }}"
            f"QProgressBar::chunk {{ background: {t.accent}; border-radius: 3px; }}"
        )
        prog_row.addWidget(self._bar, 1)

        self._pct_lbl = QLabel("0%")
        self._pct_lbl.setStyleSheet(
            f"font-family: 'Consolas', monospace; font-size: 11px; "
            f"color: {t.ink3}; background: transparent;"
        )
        self._pct_lbl.setFixedWidth(32)
        self._pct_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        prog_row.addWidget(self._pct_lbl)
        lay.addLayout(prog_row)

        # Stage message
        self._stage_lbl = QLabel("Initialising…")
        self._stage_lbl.setStyleSheet(
            f"font-size: 10px; color: {t.ink3}; background: transparent;"
        )
        self._stage_lbl.setWordWrap(False)
        self._stage_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        lay.addWidget(self._stage_lbl)

    # ------------------------------------------------------------------ API

    def start(self, name: str, current: int = 1, total: int = 1):
        """Show the widget and reset to 0% for a new import."""
        self._current = current
        self._total   = total
        display = os.path.splitext(os.path.basename(name))[0] if name else "New track"
        self._track_lbl.setText(display)
        self._bar.setValue(0)
        self._pct_lbl.setText("0%")
        self._stage_lbl.setText("Initialising…")
        self._abort_btn.setEnabled(True)
        self._update_header()
        self.show()

    def _update_header(self):
        if self._total > 1:
            self._header_lbl.setText(
                f"IMPORTING TRACK…  {self._current}/{self._total}"
            )
        else:
            self._header_lbl.setText("IMPORTING TRACK…")

    def update_progress(self, pct: int, message: str = ""):
        self._bar.setValue(pct)
        self._pct_lbl.setText(f"{pct}%")
        if message:
            self._stage_lbl.setText(message)

    def finish(self):
        """Mark as complete and hide after a brief moment."""
        self._bar.setValue(100)
        self._pct_lbl.setText("100%")
        self._stage_lbl.setText("Done!")
        self._abort_btn.setEnabled(False)
        QTimer.singleShot(1200, self.hide)

    def reset(self):
        """Hide immediately without the done animation."""
        self.hide()

    # ------------------------------------------------------------------ abort

    def _on_abort_clicked(self):
        # Build a 3-button dialog (QMessageBox supports custom buttons)
        box = QMessageBox(self)
        box.setWindowTitle("Stop importing?")
        if self._total > 1:
            remaining = self._total - self._current
            box.setText(
                f"Processing track {self._current} of {self._total}. "
                f"What would you like to do?"
            )
            skip_btn  = box.addButton(
                f"Skip this track  ({remaining} remaining)",
                QMessageBox.ButtonRole.AcceptRole,
            )
            abort_btn = box.addButton(
                "Abort all remaining tracks",
                QMessageBox.ButtonRole.DestructiveRole,
            )
        else:
            box.setText("Are you sure you want to stop processing this track?")
            skip_btn  = None
            abort_btn = box.addButton(
                "Stop processing",
                QMessageBox.ButtonRole.DestructiveRole,
            )
        keep_btn = box.addButton("Keep processing", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(keep_btn)
        box.exec()

        clicked = box.clickedButton()
        if clicked == keep_btn or clicked is None:
            return
        if skip_btn and clicked == skip_btn:
            self.abort_current.emit()
        else:
            # abort_btn — abort all (or only track when total==1)
            self.reset()
            self.abort_all.emit()


# ---------------------------------------------------------------------------
# Processing dialog
# ---------------------------------------------------------------------------

class StemCard(QFrame):
    def __init__(self, stem_id: str, label: str, color: str, theme: Theme, parent=None):
        super().__init__(parent)
        self._color = color
        self._theme = theme
        self._done = False
        self.setStyleSheet(f"QFrame {{ border: 1px solid {theme.border}; border-radius: 10px; }}")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 11, 12, 11)
        lay.setSpacing(6)

        top = QHBoxLayout()
        dot = QFrame()
        dot.setFixedSize(9, 9)
        dot.setStyleSheet(f"background: {color}; border-radius: 4px;")
        name_lbl = QLabel(label)
        name_lbl.setStyleSheet("font-size: 12px; font-weight: 600;")
        top.addWidget(dot)
        top.addWidget(name_lbl)
        top.addStretch()
        lay.addLayout(top)

        # mini waveform bars (decorative)
        bars_row = QHBoxLayout()
        bars_row.setSpacing(2)
        self._bars: list[QFrame] = []
        import random
        rnd = random.Random(hash(stem_id))
        for i in range(10):
            bar = QFrame()
            bar.setFixedWidth(6)
            h = 4 + rnd.randint(0, 14)
            bar.setFixedHeight(h)
            bar.setStyleSheet(f"background: {color}; border-radius: 1px; opacity: 0.25;")
            bars_row.addWidget(bar)
            self._bars.append(bar)
        bars_w = QWidget()
        bars_w.setLayout(bars_row)
        bars_w.setFixedHeight(26)
        lay.addWidget(bars_w)

        self._state_lbl = QLabel("waiting…")
        self._state_lbl.setStyleSheet(
            f"font-family: 'Consolas', monospace; font-size: 11px; color: {theme.ink3};"
        )
        lay.addWidget(self._state_lbl)

    def set_done(self):
        if self._done:
            return
        self._done = True
        self._state_lbl.setText("✓ extracted")
        self._state_lbl.setStyleSheet(
            f"font-family: 'Consolas', monospace; font-size: 11px; color: {self._color}; font-weight: 600;"
        )
        self.setStyleSheet(f"QFrame {{ border: 1px solid {self._color}44; border-radius: 10px; }}")
        # animate bars to full height
        import random
        rnd = random.Random(hash(self._color))
        for i, bar in enumerate(self._bars):
            h = 14 + rnd.randint(0, 12)
            bar.setFixedHeight(h)
            bar.setStyleSheet(f"background: {self._color}; border-radius: 1px;")


PROC_STAGES = [
    (0,  "Loading htdemucs model…"),
    (14, "Decoding audio & resampling…"),
    (28, "Computing spectrogram…"),
    (42, "Separating sources (neural net)…"),
    (82, "Encoding stems to FLAC…"),
    (96, "Writing .stems package…"),
]

STEM_DONE_THRESHOLD = {"vocals": 52, "drums": 62, "bass": 72, "other": 82}


class ProcessingDialog(QDialog):
    """Shown while separation is running. Receives progress signals from SeparatorWorker."""
    completed = Signal()
    cancelled = Signal()

    def __init__(self, job: dict, theme: Theme, parent=None):
        super().__init__(parent)
        self._theme = theme
        self._job = job
        self._pct = 0
        self.setWindowTitle("Processing…")
        self.setModal(True)
        self.setFixedWidth(600)
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
        self._setup_ui()
        self._apply_theme()

    def _setup_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(30, 28, 30, 24)
        lay.setSpacing(0)

        name = self._job.get("name", "New track")
        if name:
            name = os.path.splitext(name)[0]
        if not name:
            name = "New track"

        # header
        head = QHBoxLayout()
        art = QFrame()
        art.setFixedSize(52, 52)
        art.setStyleSheet(f"background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #FF5A5F, stop:1 #7C5CFF); border-radius: 13px;")
        head.addWidget(art)
        info_lay = QVBoxLayout()
        self._name_lbl = QLabel(name)
        self._name_lbl.setStyleSheet("font-size: 17px; font-weight: 600;")
        src_lbl = QLabel("Fetched from YouTube" if self._job.get("kind") == "youtube"
                         else self._job.get("name", "Local file"))
        src_lbl.setStyleSheet(f"font-size: 13px; color: {self._theme.ink3};")
        info_lay.addWidget(self._name_lbl)
        info_lay.addWidget(src_lbl)
        head.addLayout(info_lay, 1)
        self._pct_lbl = QLabel("0%")
        self._pct_lbl.setStyleSheet("font-family: 'Consolas', monospace; font-size: 26px; font-weight: 600;")
        head.addWidget(self._pct_lbl)
        lay.addLayout(head)
        lay.addSpacing(20)

        # stage label
        self._stage_lbl = QLabel("Initialising…")
        self._stage_lbl.setStyleSheet(f"font-size: 13px; color: {self._theme.ink2};")
        lay.addWidget(self._stage_lbl)
        lay.addSpacing(8)

        # progress bar
        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(8)
        self._bar.setStyleSheet(f"""
            QProgressBar {{ background: {self._theme.surface2}; border-radius: 4px; border: none; }}
            QProgressBar::chunk {{ background: {self._theme.accent}; border-radius: 4px; }}
        """)
        lay.addWidget(self._bar)
        lay.addSpacing(22)

        # stem cards
        cards_row = QHBoxLayout()
        cards_row.setSpacing(10)
        self._cards: dict[str, StemCard] = {}
        colors = ["#FF5A5F", "#F2A23A", "#7C5CFF", "#15B6A4"]
        for sid, slbl, col in zip(STEM_IDS, STEM_LABELS, colors):
            card = StemCard(sid, slbl, col, self._theme)
            self._cards[sid] = card
            cards_row.addWidget(card)
        lay.addLayout(cards_row)
        lay.addSpacing(20)

        # footer
        foot = QHBoxLayout()
        foot.addStretch()
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setProperty("role", "ghost")
        self._cancel_btn.clicked.connect(self._on_cancel)
        foot.addWidget(self._cancel_btn)
        lay.addLayout(foot)

    def _apply_theme(self):
        self.setStyleSheet(f"QDialog {{ background: {self._theme.surface}; border-radius: 22px; }}")

    def update_progress(self, pct: int, message: str = ""):
        self._pct = pct
        self._pct_lbl.setText(f"{pct}%")
        self._bar.setValue(pct)
        if message:
            self._stage_lbl.setText(message)
        else:
            for threshold, label in reversed(PROC_STAGES):
                if pct >= threshold:
                    self._stage_lbl.setText(label)
                    break
        for sid, card in self._cards.items():
            if pct >= STEM_DONE_THRESHOLD.get(sid, 100):
                card.set_done()

    def on_finished(self):
        self._pct_lbl.setText("100%")
        self._bar.setValue(100)
        self._stage_lbl.setText("Done — opening mixer…")
        self._cancel_btn.setEnabled(False)
        for card in self._cards.values():
            card.set_done()
        QTimer.singleShot(500, self._emit_complete)

    def _emit_complete(self):
        self.completed.emit()
        self.accept()

    def _on_cancel(self):
        self.cancelled.emit()
        self.reject()
