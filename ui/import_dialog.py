"""ImportDialog and ImportProgressWidget."""

import os
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QFrame, QWidget, QFileDialog, QRadioButton, QButtonGroup,
    QSizePolicy, QProgressBar, QMessageBox, QScrollArea, QLayout
)

from ui.theme import Theme


_AUDIO_EXTS = (".mp3", ".wav", ".flac", ".m4a", ".ogg")


class DropZone(QFrame):
    file_dropped  = Signal(str)
    files_dropped = Signal(list)   # list[str] — one or more audio file paths

    def __init__(self, theme: Theme, parent=None):
        super().__init__(parent)
        self._theme = theme
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(76)
        self._setup_ui()
        self._apply_style(hovered=False)

    def _setup_ui(self):
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.setSpacing(2)
        lay.setContentsMargins(16, 14, 16, 14)

        self._title = QLabel("Drop audio files here")
        self._title.setStyleSheet(
            "font-size: 14px; font-weight: 600; background: transparent; border: none;")
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        sub = QLabel("or click to browse")
        sub.setStyleSheet(
            f"font-size: 12px; color: {self._theme.ink3}; background: transparent; border: none;")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)

        lay.addWidget(self._title)
        lay.addWidget(sub)

    def _apply_style(self, hovered: bool):
        border = self._theme.accent if hovered else self._theme.border_strong
        bg = self._theme.accent_soft() if hovered else "transparent"
        self.setStyleSheet(
            f"DropZone {{ border: 1px solid {border}; "
            f"border-radius: 4px; background: {bg}; }}"
        )

    def mousePressEvent(self, e):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Open audio files", "",
            "Audio files (*.mp3 *.wav *.flac *.m4a *.ogg);;All files (*)"
        )
        if paths:
            self.files_dropped.emit(paths)

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._apply_style(hovered=True)

    def dragLeaveEvent(self, e):
        self._apply_style(hovered=False)

    def dropEvent(self, e: QDropEvent):
        self._apply_style(hovered=False)
        paths = []
        for url in e.mimeData().urls():
            p = url.toLocalFile()
            if p and p.lower().endswith(_AUDIO_EXTS):
                paths.append(p)
        if paths:
            self.files_dropped.emit(paths)


# ---------------------------------------------------------------------------
# _ItemListWidget — scrollable queue of labelled items with remove buttons
# ---------------------------------------------------------------------------

class _ItemListWidget(QWidget):
    """Compact scrollable list of queued items; each row has a type icon,
    label and × button. Holds both files and YouTube URLs in one queue."""

    # Leading glyph per item kind (Segoe UI renders both in the BMP)
    _KIND_ICON = {"file": "♪", "youtube": "▶", "template": "▤"}

    def __init__(self, theme, parent=None, max_height: int = 108):
        super().__init__(parent)
        self._theme = theme
        self._items: list[tuple[str, str, str]] = []   # (key, label, kind)
        self._on_change: "callable | None" = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setMaximumHeight(max_height)
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

    def add(self, key: str, label: str, kind: str = "file"):
        if any(k == key for k, _, _ in self._items):
            return   # deduplicate
        self._items.append((key, label, kind))
        self._rebuild()

    def remove(self, key: str):
        self._items = [it for it in self._items if it[0] != key]
        self._rebuild()

    def _remove_clicked(self, key: str):
        # remove() rebuilds and notifies via _on_change already.
        self.remove(key)

    def keys(self) -> list[str]:
        return [k for k, _, _ in self._items]

    def items(self) -> list[tuple[str, str]]:
        """Return (key, kind) for every queued item, in order."""
        return [(k, kind) for k, _, kind in self._items]

    def count(self) -> int:
        return len(self._items)

    def _rebuild(self):
        # Clear all rows except the trailing stretch
        while self._lay.count() > 1:
            item = self._lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        t = self._theme
        for key, label, kind in self._items:
            row = QWidget()
            row.setObjectName("queueRow")
            # Scope the fill to the row only (objectName selector) so it doesn't
            # bleed onto the child label/icon.
            row.setStyleSheet(
                f"QWidget#queueRow {{ background: {t.surface2}; border-radius: 3px; }}")
            rl = QHBoxLayout(row)
            rl.setContentsMargins(8, 4, 4, 4)
            rl.setSpacing(6)

            icon = QLabel(self._KIND_ICON.get(kind, "♪"))
            icon.setFixedWidth(14)
            icon.setStyleSheet(f"font-size: 12px; color: {t.ink3}; background: transparent; border: none;")
            icon.setToolTip("YouTube link" if kind == "youtube" else "Audio file")
            rl.addWidget(icon)

            lbl = QLabel(label)
            lbl.setStyleSheet(f"font-size: 12px; color: {t.ink}; background: transparent; border: none;")
            lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            lbl.setToolTip(key)

            rm = QPushButton("×")
            rm.setFixedSize(22, 22)
            rm.setCursor(Qt.CursorShape.PointingHandCursor)
            rm.setToolTip("Remove from queue")
            rm.setStyleSheet(
                f"QPushButton {{ border: none; border-radius: 6px; "
                f"background: transparent; font-size: 15px; font-weight: 600; "
                f"color: {t.ink2}; padding: 0; }}"
                f"QPushButton:hover {{ background: #E53E3E; color: white; }}"
            )
            rm.clicked.connect(lambda _, k=key: self._remove_clicked(k))

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
        self.setWindowTitle("Import tracks")
        self.setModal(True)
        self._setup_ui()
        self._apply_theme()

    # ------------------------------------------------------------------ build

    def _setup_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 24)
        lay.setSpacing(0)
        # Size the dialog exactly to its content (min == max == hint). This
        # avoids the QWindowsWindow::setGeometry warning that occurs when a
        # fixed-width / free-height dialog has to reconcile a changing minimum
        # height against the window-frame margins.
        lay.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)

        # ── two-column body: inputs left, queue right ────────────────
        # Pin the body width so the SetFixedSize layout resolves to a stable
        # dialog width (body + 24/24 margins). ~50% wider than the original.
        _LEFT_W, _RIGHT_W, _GAP = 429, 375, 16
        body = QWidget()
        body.setFixedWidth(_LEFT_W + _GAP + _RIGHT_W)
        # The global QWidget QSS paints a bg colour; clear it on the containers
        # so the white dialog shows through (no grey fill around the fields).
        body.setStyleSheet("background: transparent;")
        cols = QHBoxLayout(body)
        cols.setContentsMargins(0, 0, 0, 0)
        cols.setSpacing(_GAP)

        # --- left column: drop zone, URL paste row, quality ---
        left = QWidget()
        left.setFixedWidth(_LEFT_W)
        left.setStyleSheet("background: transparent;")
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(10)

        add_lbl = QLabel("Add tracks")
        add_lbl.setStyleSheet(
            f"font-size: 12px; font-weight: 700; letter-spacing: 0.04em; "
            f"color: {self._theme.ink3};")
        left_lay.addWidget(add_lbl)

        self._dropzone = DropZone(self._theme)
        self._dropzone.file_dropped.connect(self._on_file_dropped)
        self._dropzone.files_dropped.connect(self._on_files_dropped)
        left_lay.addWidget(self._dropzone)

        # YouTube paste row — input height matches the Add button
        url_row = QHBoxLayout()
        url_row.setSpacing(8)
        self._url_input = QLineEdit()
        self._url_input.setPlaceholderText("…or paste a YouTube URL")
        self._url_input.setFixedHeight(38)
        self._url_input.setStyleSheet(
            f"QLineEdit {{ background: transparent; "
            f"border: 1px solid {self._theme.border_strong}; border-radius: 4px; "
            f"padding: 0 12px; font-size: 13px; }}"
            f"QLineEdit:focus {{ border-color: {self._theme.accent}; }}"
        )
        self._url_input.returnPressed.connect(self._add_url)
        self._url_input.textChanged.connect(self._update_start_btn)
        url_row.addWidget(self._url_input, 1)
        add_btn = QPushButton("Add")
        add_btn.setFixedHeight(38)
        add_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {self._theme.ink}; "
            f"border: 1px solid {self._theme.border_strong}; border-radius: 4px; "
            f"padding: 0 16px; font-size: 13px; font-weight: 600; }}"
            f"QPushButton:hover {{ background: {self._theme.surface2}; }}"
        )
        add_btn.clicked.connect(self._add_url)
        url_row.addWidget(add_btn)
        left_lay.addLayout(url_row)

        # Load a saved template (.rrs) — re-derives the stems and restores tab/loops
        tmpl_btn = QPushButton("Load template (.rrs)…")
        tmpl_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        tmpl_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {self._theme.ink2}; "
            f"border: none; text-align: left; font-size: 12px; padding: 2px 0; }}"
            f"QPushButton:hover {{ color: {self._theme.accent}; }}")
        tmpl_btn.clicked.connect(self._add_template)
        left_lay.addWidget(tmpl_btn)

        # Quality — stacked radios under a heading
        quality_lbl = QLabel("Quality")
        quality_lbl.setStyleSheet(
            f"font-size: 12px; font-weight: 700; letter-spacing: 0.04em; "
            f"color: {self._theme.ink3}; margin-top: 4px;")
        left_lay.addWidget(quality_lbl)

        self._quality_group = QButtonGroup(self)
        left_lay.addLayout(self._make_quality_option(
            "htdemucs", "Balanced", "Fast, great for most tracks", checked=True))
        left_lay.addLayout(self._make_quality_option(
            "htdemucs_ft", "Fine-tuned", "Slower, cleaner separation"))
        left_lay.addStretch()

        cols.addWidget(left)

        # --- right column: shared queue (files + URLs) ---
        right = QWidget()
        right.setFixedWidth(_RIGHT_W)
        right.setStyleSheet("background: transparent;")
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(8)

        queue_lbl = QLabel("Queue")
        queue_lbl.setStyleSheet(
            f"font-size: 12px; font-weight: 700; letter-spacing: 0.04em; "
            f"color: {self._theme.ink3};")
        right_lay.addWidget(queue_lbl)

        # Bordered panel that stretches to match the height of the left column.
        # Scope the border to the panel (objectName) so it doesn't cascade onto
        # the inner QScrollArea, which is also a QFrame.
        queue_panel = QFrame()
        queue_panel.setObjectName("queuePanel")
        queue_panel.setStyleSheet(
            f"QFrame#queuePanel {{ border: 1px solid {self._theme.border_strong}; "
            f"border-radius: 4px; background: {self._theme.surface}; }}")
        qp_lay = QVBoxLayout(queue_panel)
        qp_lay.setContentsMargins(8, 8, 8, 8)
        qp_lay.setSpacing(0)

        # No internal cap — the panel (and thus the list) fills the column.
        self._queue = _ItemListWidget(self._theme, max_height=16_777_215)
        self._queue.setStyleSheet("background: transparent;")
        self._queue.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self._queue.set_on_change(self._update_start_btn)
        qp_lay.addWidget(self._queue, 1)

        self._queue_empty = QLabel("No tracks added yet")
        self._queue_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._queue_empty.setStyleSheet(
            f"font-size: 12px; color: {self._theme.ink3}; border: none; background: transparent;")
        qp_lay.addWidget(self._queue_empty, 1)

        right_lay.addWidget(queue_panel, 1)

        cols.addWidget(right)

        lay.addWidget(body)
        lay.addSpacing(14)

        # footer
        foot = QHBoxLayout()
        foot.setSpacing(10)
        foot.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet(
            f"QPushButton {{ background: {self._theme.surface2}; color: {self._theme.ink}; "
            f"border: none; border-radius: 4px; padding: 9px 16px; "
            f"font-size: 13px; font-weight: 600; }}"
            f"QPushButton:hover {{ background: {self._theme.surface3}; }}"
        )
        cancel_btn.clicked.connect(self.reject)
        self._start_btn = QPushButton("Separate")
        acc = self._theme.accent
        self._start_btn.setStyleSheet(
            f"QPushButton {{ background: {acc}; color: white; border: none; "
            f"border-radius: 4px; padding: 9px 16px; font-size: 13px; font-weight: 600; }}"
            f"QPushButton:hover {{ background: {Theme._lighten(acc)}; }}"
            f"QPushButton:disabled {{ background: {self._theme.surface3}; "
            f"color: {self._theme.ink3}; }}"
        )
        self._start_btn.setEnabled(False)
        self._start_btn.clicked.connect(self._on_start)
        foot.addWidget(cancel_btn)
        foot.addWidget(self._start_btn)
        lay.addLayout(foot)

    def _apply_theme(self):
        self.setStyleSheet(
            f"QDialog {{ background: {self._theme.surface}; border-radius: 4px; }}"
        )

    # ------------------------------------------------------------------ helpers

    def _make_quality_option(self, key: str, title: str, desc: str, checked: bool = False):
        """Build a stacked radio + description row for the Quality section."""
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)

        rb = QRadioButton(title)
        rb.setChecked(checked)
        rb.setCursor(Qt.CursorShape.PointingHandCursor)
        rb.setStyleSheet(f"QRadioButton {{ font-size: 13px; font-weight: 600; color: {self._theme.ink}; spacing: 8px; }}")
        rb.toggled.connect(lambda on, k=key: on and self._select_model(k))
        self._quality_group.addButton(rb)

        desc_lbl = QLabel(desc)
        desc_lbl.setStyleSheet(
            f"font-size: 11px; color: {self._theme.ink3}; margin-left: 24px;")

        col.addWidget(rb)
        col.addWidget(desc_lbl)
        if checked:
            self._model = key
        return col

    def _update_start_btn(self):
        n = self._queue.count()
        self._queue_empty.setVisible(n == 0)
        if n == 0:
            # Enable as soon as a URL is typed (Add happens implicitly on start)
            self._start_btn.setText("Separate")
            self._start_btn.setEnabled(bool(self._url_input.text().strip()))
        else:
            self._start_btn.setText(f"Separate {n} track{'s' if n != 1 else ''}")
            self._start_btn.setEnabled(True)

    def _select_model(self, key: str):
        self._model = key

    # ------------------------------------------------------------------ input handlers

    def _on_file_dropped(self, path: str):
        self._queue.add(path, os.path.basename(path), kind="file")

    def _on_files_dropped(self, paths: list):
        for path in paths:
            self._queue.add(path, os.path.basename(path), kind="file")

    def _add_url(self):
        url = self._url_input.text().strip()
        if not url:
            return
        # Truncate display label for very long URLs
        label = url if len(url) <= 60 else url[:57] + "…"
        self._queue.add(url, label, kind="youtube")
        self._url_input.clear()

    def _add_template(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Load template", "", "Rehearsal Room template (*.rrs)")
        for p in paths:
            label = os.path.splitext(os.path.basename(p))[0]
            try:
                from core.project import read_manifest
                label = read_manifest(p).title or label
            except Exception:
                pass
            self._queue.add(p, label, kind="template")

    # ------------------------------------------------------------------ start

    def _on_start(self):
        # Fold any URL still sitting in the input into the queue
        if self._url_input.text().strip():
            self._add_url()

        # Nothing queued yet — offer the file picker as the default action
        if self._queue.count() == 0:
            paths, _ = QFileDialog.getOpenFileNames(
                self, "Open audio files", "",
                "Audio files (*.mp3 *.wav *.flac *.m4a *.ogg);;All files (*)"
            )
            for p in paths:
                self._queue.add(p, os.path.basename(p), kind="file")
            if self._queue.count() == 0:
                return

        jobs: list[dict] = []
        for key, kind in self._queue.items():
            if kind == "youtube":
                jobs.append({
                    "kind": "youtube", "url": key,
                    "model": self._model, "name": "",
                })
            elif kind == "template":
                jobs.append({
                    "kind": "template", "path": key,
                    "model": self._model, "name": os.path.basename(key),
                })
            else:
                jobs.append({
                    "kind": "file", "path": key,
                    "model": self._model, "name": os.path.basename(key),
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
            f"QFrame {{ background: {t.surface2}; border-radius: 4px; border: none; }}"
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

    def set_count(self, current: int, total: int):
        """Update the N/M counter in place without resetting progress."""
        self._current = current
        self._total   = total
        self._update_header()

    def set_name(self, title: str, artist: str = ""):
        """Replace the displayed track label once the real name is known."""
        if not title:
            return
        self._track_lbl.setText(f"{title} — {artist}" if artist else title)

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
