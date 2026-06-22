"""Modal dialog to export tab tracks to text/PDF, with a live preview."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QColor, QFont
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QCheckBox,
    QComboBox, QSlider, QSpinBox, QPlainTextEdit, QScrollArea, QWidget,
    QDialogButtonBox, QFrame,
)

from core import tabexport
from core.tabexport import ExportOpts


class _PdfPreview(QWidget):
    """Paints the engraved tab continuously (used inside a scroll area)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tracks: list = []
        self._opts = ExportOpts()
        self.setStyleSheet("background: white;")

    def set_content(self, tracks, opts):
        self._tracks = tracks
        self._opts = opts
        h = int(tabexport.measure_flow(tracks, opts)) + 24 if tracks else 80
        self.setMinimumHeight(h)
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#FFFFFF"))
        if self._tracks:
            p.translate(10, 12)
            tabexport.paint_flow(p, self._tracks, self._opts, self.width() - 20)
        else:
            p.setPen(QColor("#999999"))
            p.setFont(QFont("Helvetica", 10))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Nothing selected")
        p.end()


class TabExportDialog(QDialog):
    def __init__(self, tracks: list, theme, parent=None, title: str = "", artist: str = ""):
        super().__init__(parent)
        self._tracks = tracks
        self._theme = theme
        self._title = title
        self._artist = artist
        self.setWindowTitle("Export tabs")
        self.setModal(True)
        self.resize(760, 580)
        self.setStyleSheet(f"QDialog {{ background: {theme.surface}; border-radius: 4px; }}")

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(12)

        body = QHBoxLayout()
        body.setSpacing(16)
        root.addLayout(body, 1)

        # ── controls column ───────────────────────────────────────────
        ctl = QVBoxLayout()
        ctl.setSpacing(10)
        body.addLayout(ctl, 0)

        self._checks: list[QCheckBox] = []
        if len(tracks) > 1:
            ctl.addWidget(self._heading("Tabs"))
            for t in tracks:
                cb = QCheckBox(t.name)
                cb.setChecked(True)
                cb.toggled.connect(self._refresh)
                self._checks.append(cb)
                ctl.addWidget(cb)

        ctl.addWidget(self._heading("Format"))
        self._fmt = QComboBox()
        self._fmt.addItems(["Text (.txt)", "PDF (.pdf)"])
        self._fmt.currentIndexChanged.connect(self._refresh)
        ctl.addWidget(self._fmt)

        ctl.addWidget(self._heading("When several tabs"))
        self._mode = QComboBox()
        self._mode.addItems(["Combined (aligned)", "Separate sections (one file)",
                             "Separate files"])
        self._mode.currentIndexChanged.connect(self._refresh)
        ctl.addWidget(self._mode)

        ctl.addWidget(self._heading("Spacing"))
        self._spacing = QSlider(Qt.Orientation.Horizontal)
        self._spacing.setRange(1, 5)
        self._spacing.setValue(2)
        self._spacing.valueChanged.connect(self._refresh)
        ctl.addWidget(self._spacing)

        bpl_row = QHBoxLayout()
        bpl_row.addWidget(QLabel("Bars per line"))
        self._bpl = QSpinBox()
        self._bpl.setRange(1, 16)
        self._bpl.setValue(4)
        self._bpl.valueChanged.connect(self._refresh)
        bpl_row.addWidget(self._bpl)
        bpl_row.addStretch(1)
        ctl.addLayout(bpl_row)

        ctl.addWidget(self._heading("Show"))
        self._bar_numbers = QCheckBox("Bar numbers")
        self._bar_numbers.setChecked(True)
        self._bar_numbers.toggled.connect(self._refresh)
        ctl.addWidget(self._bar_numbers)
        self._time_sig = QCheckBox("Time signatures")
        self._time_sig.setChecked(True)
        self._time_sig.toggled.connect(self._refresh)
        ctl.addWidget(self._time_sig)
        ctl.addStretch(1)

        # ── preview ────────────────────────────────────────────────────
        prev = QVBoxLayout()
        prev.setSpacing(6)
        prev.addWidget(self._heading("Preview"))
        self._text_preview = QPlainTextEdit()
        self._text_preview.setReadOnly(True)
        self._text_preview.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._text_preview.setStyleSheet(
            "QPlainTextEdit { font-family: Consolas, monospace; font-size: 12px; }")
        self._pdf_scroll = QScrollArea()
        self._pdf_scroll.setWidgetResizable(True)
        self._pdf_scroll.setFrameShape(QFrame.Shape.StyledPanel)
        self._pdf_preview = _PdfPreview()
        self._pdf_scroll.setWidget(self._pdf_preview)
        prev.addWidget(self._text_preview, 1)
        prev.addWidget(self._pdf_scroll, 1)
        body.addLayout(prev, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Export…")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._refresh()

    def _heading(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"font-size: 12px; font-weight: 700; letter-spacing: 0.04em; "
            f"color: {self._theme.ink3};")
        return lbl

    # ------------------------------------------------------------------ API
    def selected_tracks(self) -> list:
        if not self._checks:
            return list(self._tracks)
        return [t for t, cb in zip(self._tracks, self._checks) if cb.isChecked()]

    def options(self) -> ExportOpts:
        mode = ("combined", "sections", "separate")[self._mode.currentIndex()]
        fmt = "txt" if self._fmt.currentIndex() == 0 else "pdf"
        return ExportOpts(mode=mode, fmt=fmt,
                          spacing=self._spacing.value(),
                          bars_per_line=self._bpl.value(),
                          show_bar_numbers=self._bar_numbers.isChecked(),
                          show_time_sig=self._time_sig.isChecked(),
                          title=self._title, artist=self._artist)

    # ------------------------------------------------------------------ preview
    def _refresh(self, *_):
        sel = self.selected_tracks()
        opts = self.options()
        multi = len(sel) > 1
        self._mode.setEnabled(multi)
        is_txt = opts.fmt == "txt"
        self._text_preview.setVisible(is_txt)
        self._pdf_scroll.setVisible(not is_txt)
        if is_txt:
            self._text_preview.setPlainText(tabexport.render_text(sel, opts) or "Nothing selected")
        else:
            self._pdf_preview.set_content(sel, opts)
