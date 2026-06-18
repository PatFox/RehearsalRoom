"""Tablature editor — a QPainter tab timeline that shares the waveform's
zoom/scroll, plus a docked panel with the editing toolbar.

Phase 1 / MVP: one tab track at a time, Tier-1 notation (string+fret,
common techniques), bars anchored to the audio by millisecond start/end,
drag-to-resize bar ends, keyboard + palette note entry, playback highlight.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QRectF, QPointF, QPoint
from PySide6.QtGui import QPainter, QColor, QPen, QFont
from PySide6.QtWidgets import (
    QFrame, QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton,
    QComboBox, QSpinBox, QDialog, QLineEdit, QFormLayout, QDialogButtonBox,
    QMenu, QMessageBox,
)

from ui.widgets import TimelineCoords, InlineEditLabel
from core.tab import (TabTrack, Bar, Beat, Note, default_tuning,
                      beat_ms, find_bar, active_bar_beat)

GUTTER_W = 244        # match Ruler / lane head width
ROW_GAP  = 18         # px between string lines
TOP_PAD  = 24         # room for time-signature labels
BOT_PAD  = 18
_HANDLE_H = 11        # px height of the per-bar handle below the grid
_DRAG_SLOP = 4        # px of movement before a handle press becomes a resize drag
_SUBDIV = 4           # editing-grid columns per beat (16th-note grid in 4/4)

# In-app bar clipboard: cloned Bar objects (content + original length; ms
# anchors are ignored on paste-into, used as default lengths on paste-insert).
# Module-level so bars can be copied between tracks/songs within a session.
_BAR_CLIPBOARD: list = []


class TabSetupDialog(QDialog):
    """Modal dialog to create a tab: name, string count, default time sig."""

    def __init__(self, default_name: str, default_strings: int, theme, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add tab")
        self.setModal(True)
        form = QFormLayout(self)

        self._name = QLineEdit(default_name)
        form.addRow("Name", self._name)

        self._strings = QSpinBox()
        self._strings.setRange(3, 8)
        self._strings.setValue(default_strings)
        form.addRow("Strings", self._strings)

        ts_row = QHBoxLayout()
        self._ts_num = QSpinBox(); self._ts_num.setRange(1, 16); self._ts_num.setValue(4)
        self._ts_den = QComboBox(); self._ts_den.addItems(["1", "2", "4", "8", "16"])
        self._ts_den.setCurrentText("4")
        ts_row.addWidget(self._ts_num)
        ts_row.addWidget(QLabel("/"))
        ts_row.addWidget(self._ts_den)
        ts_row.addStretch(1)
        ts_w = QWidget(); ts_w.setLayout(ts_row)
        form.addRow("Default time signature", ts_w)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self._name.setFocus()
        self._name.selectAll()

    def values(self) -> tuple[str, int, int, int]:
        name = self._name.text().strip() or "Tab"
        return name, self._strings.value(), self._ts_num.value(), int(self._ts_den.currentText())


class _TimeSigPopup(QFrame):
    """Small floating editor shown next to a bar's time signature."""
    chosen = Signal(int, int)   # numerator, denominator (emitted live)

    def __init__(self, num: int, den: int, theme, parent=None):
        super().__init__(parent, Qt.WindowType.Popup)
        self.setStyleSheet(
            f"QFrame {{ background: {theme.surface}; "
            f"border: 1px solid {theme.border_strong}; border-radius: 6px; }}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(4)
        self._num = QSpinBox()
        self._num.setRange(1, 16)
        self._num.setValue(num)
        self._den = QComboBox()
        self._den.addItems(["1", "2", "4", "8", "16"])
        self._den.setCurrentText(str(den) if str(den) in ("1", "2", "4", "8", "16") else "4")
        slash = QLabel("/")
        lay.addWidget(self._num)
        lay.addWidget(slash)
        lay.addWidget(self._den)
        self._num.valueChanged.connect(self._emit)
        self._den.currentTextChanged.connect(self._emit)

    def _emit(self, *_):
        self.chosen.emit(self._num.value(), int(self._den.currentText()))


class TabTimeline(TimelineCoords, QWidget):
    """The drawable/editable tab canvas for one TabTrack."""

    seek_requested      = Signal(float)          # fraction 0-1
    changed             = Signal()                # tab data mutated → persist
    zoom_scroll_changed = Signal(float, float)    # zoom, scroll_frac
    tab_renamed         = Signal(str)             # new name for the current tab
    tab_switch          = Signal(int)             # switch to tab at this index

    _gutter = GUTTER_W

    def __init__(self, theme, parent=None):
        super().__init__(parent)
        self._theme = theme
        self._track: TabTrack | None = None
        self._duration = 1
        self._zoom = 1.0
        self._scroll_frac = 0.0
        self._progress = 0.0
        # caret = (bar_index, column, row)  row 0 = top string line
        self._caret = (-1, 0, 0)
        self._sel_bars: set[int] = set()   # selected bar indices (via start handles)
        self._sel_pivot = -1               # last handle clicked (for Shift range)
        self._fret_session = False     # True while consecutive digits build one fret
        # Handle press state: (bar_index, press_x, modifiers, is_dragging) or None
        self._press = None
        self._ts_rects = []            # (bar_index, QRectF) time-sig click hotspots
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # Gutter header: editable tab name + (when >1 tab) a switch dropdown.
        self._others: list = []        # [(index, name)] of the other tabs
        self._gutter_bar = QWidget(self)
        gl = QHBoxLayout(self._gutter_bar)
        gl.setContentsMargins(10, 2, 6, 0)
        gl.setSpacing(4)
        _name_style = ("font-size: 14px; font-weight: 600; background: transparent; border: none;")
        self._name_edit = InlineEditLabel(
            "", label_style=_name_style,
            edit_style=_name_style + " border-bottom: 1px solid #2E6BFF;")
        self._name_edit.committed.connect(lambda v: self.tab_renamed.emit(v))
        self._switch_btn = QPushButton("▾")
        self._switch_btn.setFixedSize(20, 20)
        self._switch_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._switch_btn.setToolTip("Switch to another tab")
        self._switch_btn.setStyleSheet(
            "QPushButton { border: none; background: transparent; font-size: 12px; "
            f"color: {theme.ink2}; border-radius: 4px; }}"
            f"QPushButton:hover {{ background: {theme.surface3}; }}")
        self._switch_btn.clicked.connect(self._open_switch_menu)
        gl.addWidget(self._name_edit, 1)
        gl.addWidget(self._switch_btn)
        self._gutter_bar.hide()

        self._apply_height()

    def resizeEvent(self, e):
        self._gutter_bar.setGeometry(0, 2, GUTTER_W, 22)
        super().resizeEvent(e)

    def set_header(self, name: str | None, others: list):
        """Show the current tab's name in the gutter; *others* = [(idx, name)]
        of the tabs you can switch to (empty hides the switch button)."""
        if not name:
            self._gutter_bar.hide()
            return
        self._name_edit.setText(name)
        self._others = list(others)
        self._switch_btn.setVisible(bool(others))
        self._gutter_bar.setGeometry(0, 2, GUTTER_W, 22)
        self._gutter_bar.show()
        self._gutter_bar.raise_()

    def _open_switch_menu(self):
        if not self._others:
            return
        menu = QMenu(self)
        for idx, nm in self._others:
            act = menu.addAction(nm)
            act.triggered.connect(lambda _=False, i=idx: self.tab_switch.emit(i))
        menu.exec(self._switch_btn.mapToGlobal(QPoint(0, self._switch_btn.height())))

    # ------------------------------------------------------------------ state
    def set_track(self, track: TabTrack | None):
        self._track = track
        self._caret = (-1, 0, 0)
        self._sel_bars = set()
        self._sel_pivot = -1
        if track is not None and self._sanitize(track):
            self.changed.emit()        # persist the repair
        self._apply_height()
        self.update()

    def _sanitize(self, track: TabTrack) -> bool:
        """Repair bar anchors that fall outside the song timeline (e.g. from an
        earlier bug that let bars pile up past the end). Preserves note content
        and relative timing by proportionally fitting the tab into [0, duration].
        Returns True if anything changed."""
        if self._duration <= 1 or not track.bars:
            return False
        lo = min(b.start_ms for b in track.bars)
        hi = max(b.end_ms for b in track.bars)
        if lo >= 0 and hi <= self._duration:
            return False               # already within the song — leave it alone
        span = (hi - lo) or 1
        scale = self._duration / span
        for b in track.bars:
            b.start_ms = int(round((b.start_ms - lo) * scale))
            b.end_ms = max(b.start_ms + 1, int(round((b.end_ms - lo) * scale)))
        return True

    def set_duration(self, ms: int):
        self._duration = max(1, int(ms))
        self.update()

    def set_duration(self, ms: int):
        self._duration = max(1, int(ms))
        self.update()

    def set_zoom_scroll(self, zoom: float, scroll_frac: float):
        self._zoom = max(1.0, zoom)
        self._scroll_frac = max(0.0, min(1.0, scroll_frac))
        self.update()

    def set_progress(self, frac: float):
        self._progress = frac
        self.update()

    def _strings(self) -> int:
        return self._track.strings if self._track else 6

    def _apply_height(self):
        self.setFixedHeight(TOP_PAD + (self._strings() - 1) * ROW_GAP + BOT_PAD)

    # ------------------------------------------------------------- geometry
    def _row_y(self, row: int) -> float:
        return TOP_PAD + row * ROW_GAP

    def _ncols(self, bar: Bar) -> int:
        return max(1, bar.ts_num * _SUBDIV)

    def _col_pos(self, bar: Bar, col: int) -> float:
        return col / self._ncols(bar)

    def _col_ms(self, bar: Bar, col: int) -> float:
        return bar.start_ms + self._col_pos(bar, col) * bar.length_ms

    def _x_for_ms(self, ms: float) -> float:
        return self._screen_x(ms / self._duration)

    # ------------------------------------------------------------- painting
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = self._theme
        w, h = self.width(), self.height()
        nstr = self._strings()

        # gutter (string/tuning labels)
        p.fillRect(0, 0, GUTTER_W, h, QColor(t.surface))
        tuning = (self._track.tuning if self._track else default_tuning(nstr))
        p.setFont(QFont("Consolas", 9))
        for r in range(nstr):
            y = self._row_y(r)
            # high string on top: row 0 → highest tuning entry
            idx = nstr - 1 - r
            lbl = tuning[idx] if idx < len(tuning) else ""
            p.setPen(QColor(t.ink3))
            p.drawText(QRectF(GUTTER_W - 30, y - ROW_GAP / 2, 22, ROW_GAP),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, lbl)

        p.setClipRect(GUTTER_W, 0, max(0, w - GUTTER_W), h)

        # string lines across the track
        line_col = QColor(t.border_strong)
        p.setPen(QPen(line_col, 1))
        for r in range(nstr):
            y = self._row_y(r)
            p.drawLine(QPointF(GUTTER_W, y), QPointF(w, y))

        if self._track and self._track.bars:
            self._paint_bars(p)

        # playhead
        px = self._x_for_ms(self._progress * self._duration)
        if GUTTER_W <= px <= w:
            p.setPen(QPen(QColor(180, 180, 180, 220), 1))
            p.drawLine(QPointF(px, 0), QPointF(px, h))

        p.setClipping(False)
        # bottom divider
        p.setPen(QPen(QColor(t.border), 1))
        p.drawLine(QPointF(0, h - 1), QPointF(w, h - 1))
        p.end()

    def _paint_bars(self, p: QPainter):
        t = self._theme
        h = self.height()
        nstr = self._strings()
        accent = QColor(t.accent)
        ms_now = self._progress * self._duration
        act_bar, act_beat = active_bar_beat(self._track, ms_now)
        self._ts_rects = []          # (bar_index, QRectF) clickable time-sig hotspots

        for bi, bar in enumerate(self._track.bars):
            x0 = self._x_for_ms(bar.start_ms)
            x1 = self._x_for_ms(bar.end_ms)
            selected = bi in self._sel_bars

            # active-bar wash
            if bi == act_bar:
                wash = QColor(accent); wash.setAlpha(20)
                p.fillRect(QRectF(x0, 0, x1 - x0, h), wash)

            # selection wash
            if selected:
                selc = QColor(accent); selc.setAlpha(34)
                p.fillRect(QRectF(x0, 0, x1 - x0, h), selc)

            # beat / subdivision gridlines — darker for the beat, progressively
            # lighter for 8th then 16th subdivisions.
            ncols = self._ncols(bar)
            gy0, gy1 = self._row_y(0), self._row_y(nstr - 1)
            half = max(1, _SUBDIV // 2)
            for col in range(1, ncols):
                if col % _SUBDIV == 0:
                    alpha = 80          # beat
                elif col % half == 0:
                    alpha = 38          # 8th
                else:
                    alpha = 16          # 16th
                gx = self._x_for_ms(self._col_ms(bar, col))
                gc = QColor(t.ink); gc.setAlpha(alpha)
                p.setPen(QPen(gc, 1))
                p.drawLine(QPointF(gx, gy0), QPointF(gx, gy1))

            # barline (start) — purely visual, not draggable
            p.setPen(QPen(QColor(t.ink3), 1))
            p.drawLine(QPointF(x0, TOP_PAD - 6), QPointF(x0, self._row_y(nstr - 1) + 6))

            p.setFont(QFont("Consolas", 8))

            # time signature — shown on every bar and clickable to edit
            ts_text = f"{bar.ts_num}/{bar.ts_den}"
            p.setPen(QColor(t.accent))
            p.drawText(QPointF(x0 + 3, TOP_PAD - 9), ts_text)
            tsw = p.fontMetrics().horizontalAdvance(ts_text)
            self._ts_rects.append((bi, QRectF(x0 + 1, 0, tsw + 6, TOP_PAD - 2)))

            # sequential bar number, centred over the bar (same line/font as the ts)
            num = str(bi + 1)
            tw = p.fontMetrics().horizontalAdvance(num)
            p.setPen(QColor(t.ink3))
            p.drawText(QPointF((x0 + x1) / 2 - tw / 2, TOP_PAD - 9), num)

            # notes
            for beat in bar.beats:
                bx = self._x_for_ms(beat_ms(bar, beat))
                for note in beat.notes:
                    self._paint_note(p, bx, note, bar)

            # caret
            cbar, ccol, crow = self._caret
            if cbar == bi:
                cx = self._x_for_ms(self._col_ms(bar, ccol))
                cy = self._row_y(crow)
                box = QColor(accent); box.setAlpha(60)
                p.fillRect(QRectF(cx - 8, cy - ROW_GAP / 2, 16, ROW_GAP), box)
                p.setPen(QPen(accent, 1))
                p.drawRect(QRectF(cx - 8, cy - ROW_GAP / 2, 16, ROW_GAP))

            # final bar's end line + trailing end handle (resizes the last bar's end)
            if bi == len(self._track.bars) - 1:
                p.setPen(QPen(QColor(t.ink3), 1))
                p.drawLine(QPointF(x1, TOP_PAD - 6), QPointF(x1, self._row_y(nstr - 1) + 6))
                edrag = self._press is not None and self._press[0] == len(self._track.bars) and self._press[3]
                ecol = QColor(accent) if (selected or edrag) else QColor(t.surface3)
                p.setPen(QPen(QColor(t.border_strong), 1))
                p.setBrush(ecol)
                p.drawRoundedRect(self._end_handle_rect(), 2, 2)

            # small square handle below the grid, left edge on the bar's start
            # line: click selects the bar, drag moves the start boundary.
            hr = self._handle_rect(bar)
            dragging = self._press is not None and self._press[0] == bi and self._press[3]
            hcol = QColor(accent) if (selected or dragging) else QColor(t.surface3)
            p.setPen(QPen(QColor(t.border_strong), 1))
            p.setBrush(hcol)
            p.drawRoundedRect(hr, 2, 2)

    def _paint_note(self, p: QPainter, x: float, note: Note, bar: Bar):
        t = self._theme
        nstr = self._strings()
        row = nstr - note.string            # string 1 = top row(0)? note.string 1..N, top=N
        # note.string: 1 = top line. Convert: row = note.string - 1
        row = note.string - 1
        if row < 0 or row >= nstr:
            return
        y = self._row_y(row)
        txt = "x" if "x" in note.techniques else str(note.fret)
        p.setFont(QFont("Consolas", 10, QFont.Weight.Bold))
        fm = p.fontMetrics()
        tw = fm.horizontalAdvance(txt)
        # cut the string line behind the number
        p.fillRect(QRectF(x - tw / 2 - 2, y - ROW_GAP / 2 + 1, tw + 4, ROW_GAP - 2),
                   QColor(t.surface))
        p.setPen(QColor(t.ink))
        p.drawText(QRectF(x - tw / 2 - 2, y - ROW_GAP / 2, tw + 4, ROW_GAP),
                   Qt.AlignmentFlag.AlignCenter, txt)
        # technique markers above the number
        marks = [m for m in note.techniques if m != "x"]
        if marks:
            p.setFont(QFont("Consolas", 7))
            p.setPen(QColor(t.accent))
            p.drawText(QPointF(x - tw / 2, y - ROW_GAP / 2 - 1), "".join(marks)[:4])

    # ------------------------------------------------------------- mouse
    def _bar_at_x(self, x: float) -> int:
        if not self._track:
            return -1
        for bi, bar in enumerate(self._track.bars):
            if self._x_for_ms(bar.start_ms) <= x < self._x_for_ms(bar.end_ms):
                return bi
        return -1

    def _handle_band_y(self) -> float:
        return self._row_y(self._strings() - 1) + 4

    def _handle_rect(self, bar: Bar) -> QRectF:
        # small square; its left edge sits on the bar's start line
        return QRectF(self._x_for_ms(bar.start_ms), self._handle_band_y(),
                      _HANDLE_H, _HANDLE_H)

    def _end_handle_rect(self) -> QRectF:
        # trailing square for the last bar's end; right edge on the end line
        x1 = self._x_for_ms(self._track.bars[-1].end_ms)
        return QRectF(x1 - _HANDLE_H, self._handle_band_y(), _HANDLE_H, _HANDLE_H)

    def _handle_at(self, x: float, y: float):
        """Bar index for a start handle, len(bars) for the trailing end handle,
        or -1. (len(bars) is the sentinel for 'resize the last bar's end'.)"""
        if not self._track or not self._track.bars:
            return -1
        y0 = self._handle_band_y()
        if y < y0 or y > y0 + _HANDLE_H:
            return -1
        for bi, bar in enumerate(self._track.bars):
            hx = self._x_for_ms(bar.start_ms)
            if hx <= x <= hx + _HANDLE_H:
                return bi
        er = self._end_handle_rect()
        if er.left() <= x <= er.right():
            return len(self._track.bars)
        return -1

    def _select_bar(self, bi: int, mods):
        if mods & Qt.KeyboardModifier.ControlModifier:
            self._sel_bars ^= {bi}                          # toggle
        elif mods & Qt.KeyboardModifier.ShiftModifier and self._sel_pivot >= 0:
            lo, hi = sorted((self._sel_pivot, bi))
            self._sel_bars = set(range(lo, hi + 1))         # range from pivot
        else:
            self._sel_bars = {bi}                           # select just this
        self._sel_pivot = bi
        self._caret = (bi, 0, self._caret[2])               # follow for note entry
        self.update()

    def mousePressEvent(self, e):
        self.setFocus()
        if e.button() != Qt.MouseButton.LeftButton:
            return                              # right-click → contextMenuEvent
        x, y = e.position().x(), e.position().y()
        if x < GUTTER_W or not self._track:
            return

        # clickable time signature (top of each bar) → floating editor popup
        for bi, rect in self._ts_rects:
            if rect.contains(x, y):
                self._open_ts_popup(bi, e.globalPosition().toPoint())
                return

        # handle below the grid → press (becomes click=select or drag=resize)
        bi = self._handle_at(x, y)
        if bi >= 0:
            self._press = (bi, x, e.modifiers(), False)
            return

        # otherwise place the caret in the grid (clears any bar selection)
        bi = self._bar_at_x(x)
        if bi < 0:
            return
        bar = self._track.bars[bi]
        ncols = self._ncols(bar)
        frac_in_bar = (self._frac(x) * self._duration - bar.start_ms) / max(1, bar.length_ms)
        col = max(0, min(ncols - 1, round(frac_in_bar * ncols)))
        row = max(0, min(self._strings() - 1, round((y - TOP_PAD) / ROW_GAP)))
        self._sel_bars = set()
        self._caret = (bi, col, row)
        self._fret_session = False
        self.update()

    def mouseMoveEvent(self, e):
        x, y = e.position().x(), e.position().y()
        if self._press is not None and self._track:
            bi, px, mods, dragging = self._press
            if not dragging and abs(x - px) > _DRAG_SLOP:
                dragging = True
                self._press = (bi, px, mods, True)
            if dragging:
                ms = int(self._frac(x) * self._duration)
                shift = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
                if bi >= len(self._track.bars):
                    self._set_bar_end(len(self._track.bars) - 1, ms)   # trailing handle
                elif shift:
                    self._shift_bars_from(bi, ms)   # move this bar + all following
                else:
                    self._set_bar_start(bi, ms)
                self.update()
            return
        # cursor hint: pointing hand over a handle or a time signature
        over_ts = any(rect.contains(x, y) for _, rect in self._ts_rects)
        self.setCursor(Qt.CursorShape.PointingHandCursor
                       if (over_ts or self._handle_at(x, y) >= 0)
                       else Qt.CursorShape.ArrowCursor)

    def _open_ts_popup(self, bi: int, global_pos):
        bar = self._track.bars[bi]
        pop = _TimeSigPopup(bar.ts_num, bar.ts_den, self._theme, self)
        pop.chosen.connect(lambda n, d, b=bi: self.set_time_signature(n, d, b))
        pop.move(global_pos.x() + 6, global_pos.y() + 4)
        pop.show()

    def mouseReleaseEvent(self, e):
        if self._press is None:
            return
        bi, px, mods, dragging = self._press
        self._press = None
        if dragging:
            self.changed.emit()        # resize finished
        else:
            # trailing end handle selects the last bar
            n = len(self._track.bars)
            self._select_bar(min(bi, n - 1), mods)  # plain click → select

    def contextMenuEvent(self, e):
        if not self._track:
            return
        x = e.pos().x()
        if x < GUTTER_W:
            return
        bi = self._bar_at_x(x)                       # clicked bar, or -1 if empty
        ms = self._frac(x) * self._duration

        # Target bars for copy/cut/delete: the selection if any, else the
        # clicked/caret bar. The noun reflects the current selection.
        sel = sorted(self._sel_bars)
        if sel:
            targets = sel
            noun = "selected bar" if len(sel) == 1 else f"{len(sel)} bars"
        else:
            cur = bi if bi >= 0 else self._caret[0]
            targets = [cur] if cur >= 0 else []
            noun = "this bar"
        has_clip = bool(_BAR_CLIPBOARD)

        t = self._theme
        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background: {t.surface}; border: 1px solid {t.border}; "
            f"border-radius: 8px; padding: 4px; }}"
            f"QMenu::item {{ padding: 6px 16px; color: {t.ink}; border-radius: 4px; }}"
            f"QMenu::item:selected {{ background: {t.surface2}; }}"
            f"QMenu::item:disabled {{ color: {t.ink3}; }}"
            f"QMenu::separator {{ height: 1px; background: {t.border}; margin: 4px 6px; }}")
        if bi >= 0:
            menu.addAction("Insert new bar after",
                           lambda: self.insert_bar_relative(bi, after=True))
            menu.addAction("Insert new bar before",
                           lambda: self.insert_bar_relative(bi, after=False))
        else:
            menu.addAction("Insert new bar", lambda: self.insert_bar_empty(ms))
        menu.addSeparator()

        a_copy = menu.addAction(f"Copy {noun}", lambda: self._copy_bars(targets))
        a_cut = menu.addAction(f"Cut {noun}",
                               lambda: (self._copy_bars(targets), self._delete_bars(targets)))
        a_copy.setEnabled(bool(targets))
        a_cut.setEnabled(bool(targets))
        menu.addSeparator()

        if bi >= 0:
            pb_idx, pa_idx = bi, bi + 1
        else:
            pb_idx = pa_idx = sum(1 for b in self._track.bars if b.start_ms <= ms)
        a_pb = menu.addAction("Paste before", lambda: self._paste_insert_at(pb_idx))
        a_pa = menu.addAction("Paste after", lambda: self._paste_insert_at(pa_idx))
        a_pb.setEnabled(has_clip)
        a_pa.setEnabled(has_clip)
        menu.addSeparator()

        a_del = menu.addAction(f"Delete {noun}", lambda: self._confirm_delete(targets))
        a_del.setEnabled(bool(targets))

        menu.exec(e.globalPos())

    def _confirm_delete(self, targets: list[int]):
        if not targets:
            return
        n = len(targets)
        msg = f"Delete {n} bar{'s' if n != 1 else ''}? This cannot be undone."
        if QMessageBox.question(self, "Delete bars", msg) == QMessageBox.StandardButton.Yes:
            self._delete_bars(sorted(targets))

    def wheelEvent(self, e):
        # Mirror WaveformWidget zoom, keeping the song fraction under the
        # cursor fixed; broadcast so all lanes + ruler + tab stay in sync.
        from ui.widgets import _ZOOM_MIN, _ZOOM_MAX, _ZOOM_STEP
        delta = e.angleDelta().y()
        if delta == 0:
            return
        factor = _ZOOM_STEP if delta > 0 else 1.0 / _ZOOM_STEP
        cursor_x = e.position().x()
        cursor_frac = self._frac(cursor_x)
        new_zoom = max(_ZOOM_MIN, min(_ZOOM_MAX, self._zoom * factor))
        if new_zoom <= _ZOOM_MIN:
            self.zoom_scroll_changed.emit(_ZOOM_MIN, 0.0)
            e.accept()
            return
        tw = self._track_w()
        total = tw * new_zoom
        new_scroll_px = cursor_frac * total - (cursor_x - self._gutter)
        max_scroll_px = tw * (new_zoom - 1)
        frac = new_scroll_px / max_scroll_px if max_scroll_px > 0 else 0.0
        self.zoom_scroll_changed.emit(new_zoom, max(0.0, min(1.0, frac)))
        e.accept()

    def _set_bar_start(self, bi: int, ms: int):
        """Move bar *bi*'s start boundary (= previous bar's end), clamped between
        its neighbours so bars stay ordered and within the song."""
        bars = self._track.bars
        lo = bars[bi - 1].start_ms + 50 if bi > 0 else 0
        hi = bars[bi].end_ms - 50
        ms = max(lo, min(ms, hi))
        bars[bi].start_ms = ms
        if bi > 0:
            bars[bi - 1].end_ms = ms

    def _set_bar_end(self, bi: int, ms: int):
        bars = self._track.bars
        if self._duration > 1:
            ms = min(ms, self._duration)          # never anchor past the song end
        ms = max(bars[bi].start_ms + 50, ms)      # keep end after start
        bars[bi].end_ms = ms
        if bi + 1 < len(bars):
            bars[bi + 1].start_ms = ms
            if bars[bi + 1].end_ms < ms:
                bars[bi + 1].end_ms = ms + 50

    def _shift_bars_from(self, bi: int, ms: int):
        """Shift-drag: move bar *bi* and every following bar rigidly so the start
        of bar bi lands at *ms*; the preceding bar absorbs the gap. Clamped so the
        last bar can't go past the song end and bars stay ordered."""
        bars = self._track.bars
        delta = ms - bars[bi].start_ms
        # left limit: bar bi can't cross into the previous bar (keep its ≥50 ms),
        # and the first bar can't go before 0.
        lo_start = (bars[bi - 1].start_ms + 50) if bi > 0 else 0
        if bars[bi].start_ms + delta < lo_start:
            delta = lo_start - bars[bi].start_ms
        # right limit: the last bar's end can't pass the song duration.
        if self._duration > 1 and bars[-1].end_ms + delta > self._duration:
            delta = self._duration - bars[-1].end_ms
        if delta == 0:
            return
        if bi > 0:
            bars[bi - 1].end_ms += delta          # preceding bar stretches/shrinks
        for b in bars[bi:]:
            b.start_ms += delta
            b.end_ms += delta

    # ------------------------------------------------------------- keyboard
    def keyPressEvent(self, e):
        if not self._track or self._caret[0] < 0:
            return super().keyPressEvent(e)
        key = e.key()
        mods = e.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        bi, col, row = self._caret

        # selection / clipboard
        if ctrl and key == Qt.Key.Key_C:
            self.copy_selection(); return
        if ctrl and key == Qt.Key.Key_X:
            self.cut_selection(); return
        if ctrl and key == Qt.Key.Key_V:
            # Ctrl+V adds bars (insert); Ctrl+Shift+V overwrites keeping anchors.
            self.paste(insert=not shift); return
        if ctrl and key == Qt.Key.Key_A:
            self.select_all(); return
        if key == Qt.Key.Key_Escape:
            self._sel_bars = set(); self.update(); return

        if key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            self._sel_bars = set()
            self._move_col(-1 if key == Qt.Key.Key_Left else 1)
        elif key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            d = -1 if key == Qt.Key.Key_Up else 1
            self._caret = (bi, col, max(0, min(self._strings() - 1, row + d)))
            self._fret_session = False
            self.update()
        elif Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
            self._type_digit(key - Qt.Key.Key_0)
        elif key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            if self._sel_bars:
                self.delete_selection()      # bars selected → delete them
            else:
                self._clear_caret_note()      # otherwise clear the note at the caret
        else:
            tech = {Qt.Key.Key_H: "h", Qt.Key.Key_P: "p", Qt.Key.Key_B: "b",
                    Qt.Key.Key_Slash: "/", Qt.Key.Key_Backslash: "\\",
                    Qt.Key.Key_AsciiTilde: "~", Qt.Key.Key_X: "x",
                    Qt.Key.Key_M: "PM"}.get(key)
            if tech:
                self._toggle_technique(tech)
            else:
                return super().keyPressEvent(e)

    def _move_col(self, d: int):
        bi, col, row = self._caret
        bar = self._track.bars[bi]
        ncols = self._ncols(bar)
        col += d
        if col < 0:
            if bi > 0:
                bi -= 1
                col = self._ncols(self._track.bars[bi]) - 1
            else:
                col = 0
        elif col >= ncols:
            if bi + 1 < len(self._track.bars):
                bi += 1; col = 0
            else:
                col = ncols - 1
        self._caret = (bi, col, row)
        self._fret_session = False
        self.update()

    def _beat_at_caret(self, create: bool) -> Beat | None:
        bi, col, row = self._caret
        bar = self._track.bars[bi]
        target = self._col_pos(bar, col)
        tol = 0.5 / self._ncols(bar)
        for beat in bar.beats:
            if abs(beat.pos - target) < tol:
                return beat
        if not create:
            return None
        beat = Beat(pos=target, dur=str(_SUBDIV * 4))
        bar.beats.append(beat)
        bar.beats.sort(key=lambda b: b.pos)
        return beat

    def _note_at_caret(self, beat: Beat | None) -> Note | None:
        if not beat:
            return None
        string = self._caret[2] + 1
        for n in beat.notes:
            if n.string == string:
                return n
        return None

    def _type_digit(self, d: int):
        beat = self._beat_at_caret(create=True)
        note = self._note_at_caret(beat)
        if note is None:
            note = Note(string=self._caret[2] + 1, fret=d)
            beat.notes.append(note)
        elif self._fret_session:
            note.fret = min(36, note.fret * 10 + d)
        else:
            note.fret = d
        self._fret_session = True
        self.changed.emit()
        self.update()

    def _toggle_technique(self, tech: str):
        beat = self._beat_at_caret(create=False)
        note = self._note_at_caret(beat)
        if note is None:
            return
        if tech in note.techniques:
            note.techniques.remove(tech)
        else:
            note.techniques.append(tech)
        self.changed.emit()
        self.update()

    def _clear_caret_note(self):
        beat = self._beat_at_caret(create=False)
        note = self._note_at_caret(beat)
        if note and beat:
            beat.notes.remove(note)
            if not beat.notes:
                self._track.bars[self._caret[0]].beats.remove(beat)
            self._fret_session = False
            self.changed.emit()
            self.update()

    # ------------------------------------------------------------- editing API (from panel)
    def caret_bar(self) -> int:
        return self._caret[0]

    def add_bar(self, default_len_ms: int):
        bars = self._track.bars
        dur = self._duration if self._duration > 1 else None
        if bars:
            prev = bars[-1]
            length = prev.length_ms or default_len_ms
            start = prev.end_ms
            if dur:
                if start >= dur:                       # no room left in the song
                    length = min(length, dur)
                    start = max(0, dur - length)
                end = min(start + length, dur)
            else:
                end = start + length
            bar = Bar(ts_num=prev.ts_num, ts_den=prev.ts_den, start_ms=start, end_ms=end)
        else:
            end = min(default_len_ms, dur) if dur else default_len_ms
            bar = Bar(ts_num=self._track.def_ts_num, ts_den=self._track.def_ts_den,
                      start_ms=0, end_ms=end)
        bars.append(bar)
        self._caret = (len(bars) - 1, 0, self._caret[2])
        self.changed.emit()
        self._reveal_bar(len(bars) - 1)
        self.update()

    def _reveal_bar(self, idx: int):
        """Zoom/scroll the shared timeline so bar *idx* is comfortably visible.
        A 3 s bar in a long song is a sliver at zoom 1, so framing it is what
        makes 'Add bar' actually show something."""
        from ui.widgets import _ZOOM_MAX
        bars = self._track.bars
        if not (0 <= idx < len(bars)) or self._duration <= 0:
            return
        bar = bars[idx]
        span = max(bar.length_ms * 4, 1)            # show ~4 bars' worth of context
        zoom = max(1.0, min(_ZOOM_MAX, self._duration / span))
        if zoom <= 1.0:
            self.zoom_scroll_changed.emit(1.0, 0.0)
            return
        center = ((bar.start_ms + bar.end_ms) / 2) / self._duration
        vis = 1.0 / zoom
        scroll = (center - vis / 2) / max(1e-9, 1.0 - vis)
        self.zoom_scroll_changed.emit(zoom, max(0.0, min(1.0, scroll)))

    def set_caret_bar_end(self, ms: int):
        bi = self._caret[0]
        if bi < 0:
            bi = len(self._track.bars) - 1
        if bi < 0:
            return
        ms = max(self._track.bars[bi].start_ms + 50, int(ms))
        self._set_bar_end(bi, ms)
        self.changed.emit()
        self.update()

    def set_time_signature(self, num: int, den: int, bi: int | None = None):
        if bi is None:
            bi = self._caret[0]
            if bi < 0:
                bi = len(self._track.bars) - 1
        if bi < 0 or bi >= len(self._track.bars):
            return
        bar = self._track.bars[bi]
        old_n = self._ncols(bar)            # columns under the current grid
        bar.ts_num = num
        bar.ts_den = den
        new_n = self._ncols(bar)            # columns under the new grid
        # Re-quantise notes: keep each beat's column (its position from the bar
        # start), snap to the new grid, and drop any that fall outside the bar.
        kept = []
        for beat in bar.beats:
            col = round(beat.pos * old_n)
            if col < new_n:
                beat.pos = col / new_n
                kept.append(beat)
        bar.beats = kept
        self.changed.emit()
        self.update()

    # ------------------------------------------------------------- selection + clipboard
    def _selected_bars(self) -> list[int]:
        """Sorted selected bar indices; falls back to the caret bar if none."""
        if self._sel_bars:
            return sorted(self._sel_bars)
        return [self._caret[0]] if self._caret[0] >= 0 else []

    def select_all(self):
        if not self._track or not self._track.bars:
            return
        self._sel_bars = set(range(len(self._track.bars)))
        self.update()

    def _clone_beats(self, beats, max_strings):
        out = []
        for be in beats:
            notes = [Note(string=n.string, fret=n.fret,
                          techniques=list(n.techniques), bend=list(n.bend))
                     for n in be.notes if 1 <= n.string <= max_strings]
            out.append(Beat(pos=be.pos, dur=be.dur, dotted=be.dotted,
                            tuplet=be.tuplet, rest=be.rest, notes=notes))
        return out

    def _copy_bars(self, idx: list[int]):
        if self._track and idx:
            _BAR_CLIPBOARD[:] = [Bar.from_dict(self._track.bars[i].to_dict()) for i in idx]

    def copy_selection(self):
        if self._track:
            self._copy_bars(self._selected_bars())

    def cut_selection(self):
        if not self._track:
            return
        idx = self._selected_bars()
        if idx:
            self._copy_bars(idx)
            self._delete_bars(idx)

    def delete_selection(self):
        """Remove the selected bars (no clipboard). Anchors of remaining bars
        are left untouched — they stay aligned to the recording."""
        if not self._track:
            return
        idx = self._selected_bars()
        if idx:
            self._delete_bars(idx)

    def _delete_bars(self, idx: list[int]):
        for i in sorted(idx, reverse=True):     # delete high→low to keep indices valid
            del self._track.bars[i]
        self._sel_bars = set()
        first = idx[0] if idx else 0
        self._caret = (min(first, len(self._track.bars) - 1), 0, self._caret[2])
        self.changed.emit()
        self.update()

    def paste(self, insert: bool = True):
        """insert=True (default): add the copied bars after the caret, shifting
        later bars. insert=False: overwrite consecutive bars keeping anchors."""
        if not _BAR_CLIPBOARD or not self._track or self._caret[0] < 0:
            return
        if insert:
            self._paste_insert()
        else:
            self._paste_into()

    def _paste_into(self):
        """Overwrite consecutive bars from the caret, keeping their anchors.
        Appends new bars (default length) only if the clipboard runs past the end."""
        bars = self._track.bars
        sel = self._selected_bars()
        start = sel[0] if sel else self._caret[0]
        nstr = self._strings()
        for i, cb in enumerate(_BAR_CLIPBOARD):
            tgt = start + i
            beats = self._clone_beats(cb.beats, nstr)
            if tgt < len(bars):
                bars[tgt].ts_num = cb.ts_num
                bars[tgt].ts_den = cb.ts_den
                bars[tgt].beats = beats
            else:
                prev = bars[-1]
                length = cb.length_ms or prev.length_ms or 2000
                bars.append(Bar(ts_num=cb.ts_num, ts_den=cb.ts_den,
                                start_ms=prev.end_ms, end_ms=prev.end_ms + length,
                                beats=beats))
        self.changed.emit()
        self.update()

    def _paste_insert(self):
        """Insert clipboard bars as new bars after the caret/selected bar."""
        sel = self._selected_bars()
        idx = (sel[-1] if sel else self._caret[0]) + 1
        self._paste_insert_at(idx)

    def _paste_insert_at(self, idx: int):
        """Insert the clipboard bars at *idx* (before the bar currently there),
        shifting every later bar's anchors by the inserted total length."""
        if not _BAR_CLIPBOARD or not self._track:
            return
        bars = self._track.bars
        nstr = self._strings()
        idx = max(0, min(idx, len(bars)))
        if idx < len(bars):
            boundary = bars[idx].start_ms
        else:
            boundary = bars[-1].end_ms if bars else 0
        lengths = [(cb.length_ms or 2000) for cb in _BAR_CLIPBOARD]
        total = sum(lengths)
        for b in bars[idx:]:
            b.start_ms += total
            b.end_ms += total
        new = []
        cur = boundary
        for cb, ln in zip(_BAR_CLIPBOARD, lengths):
            new.append(Bar(ts_num=cb.ts_num, ts_den=cb.ts_den,
                           start_ms=cur, end_ms=cur + ln,
                           beats=self._clone_beats(cb.beats, nstr)))
            cur += ln
        bars[idx:idx] = new
        self._sel_bars = set()
        self._caret = (idx, 0, self._caret[2])
        self.changed.emit()
        self.update()

    # ------------------------------------------------------------- bar insertion
    def _insert_bar(self, idx: int, start_ms: int, length: int,
                    ts_num: int, ts_den: int, shift: bool):
        bars = self._track.bars
        if shift:
            for b in bars[idx:]:
                b.start_ms += length
                b.end_ms += length
        bar = Bar(ts_num=ts_num, ts_den=ts_den, start_ms=start_ms, end_ms=start_ms + length)
        bars[idx:idx] = [bar]
        self._sel_bars = set()
        self._caret = (idx, 0, self._caret[2])
        self.changed.emit()
        self._reveal_bar(idx)
        self.update()

    def insert_bar_relative(self, bi: int, after: bool):
        """Insert a new empty bar before/after bar *bi*, matching its length
        and time signature; shifts later bars to make room."""
        bars = self._track.bars
        if not (0 <= bi < len(bars)):
            return
        ref = bars[bi]
        length = ref.length_ms or (min(3000, self._duration) or 3000)
        if after:
            self._insert_bar(bi + 1, ref.end_ms, length, ref.ts_num, ref.ts_den, shift=True)
        else:
            self._insert_bar(bi, ref.start_ms, length, ref.ts_num, ref.ts_den, shift=True)

    def insert_bar_empty(self, ms: float):
        """Insert a new bar into empty space at *ms* without overlapping any
        existing bar (fills the gap; no shifting)."""
        bars = self._track.bars
        default_len = min(3000, self._duration) or 3000
        if not bars:
            end = min(default_len, self._duration) if self._duration > 1 else default_len
            self._insert_bar(0, 0, end, self._track.def_ts_num,
                             self._track.def_ts_den, shift=False)
            return
        # gap around ms: right edge of the nearest bar on the left, left edge of
        # the nearest bar on the right.
        gap_start = 0
        gap_end = self._duration
        for b in bars:
            if b.end_ms <= ms:
                gap_start = max(gap_start, b.end_ms)
            if b.start_ms > ms:
                gap_end = min(gap_end, b.start_ms)
        end = min(gap_start + default_len, gap_end)
        if end - gap_start < 50:
            return                               # no room to fit a bar here
        idx = sum(1 for b in bars if b.start_ms < gap_start)
        self._insert_bar(idx, gap_start, end - gap_start,
                         self._track.def_ts_num, self._track.def_ts_den, shift=False)


# --------------------------------------------------------------------------- #
# Panel
# --------------------------------------------------------------------------- #

class TabEditorPanel(QFrame):
    """Docked tab editor: toolbar + TabTimeline. Manages the tab tracks."""

    changed             = Signal()        # tab data changed → persist
    seek_requested      = Signal(float)
    zoom_scroll_changed = Signal(float, float)

    def __init__(self, theme, parent=None):
        super().__init__(parent)
        self._theme = theme
        self._tracks: list[TabTrack] = []
        self._lane_ids: list[str] = []
        self._duration = 1
        self._cur = -1
        self.setStyleSheet(
            f"TabEditorPanel {{ background: {theme.surface}; "
            f"border-top: 1px solid {theme.border}; }}")
        self._build()
        self.hide()   # toggled on demand

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 8, 12, 8)
        root.setSpacing(8)

        # No toolbar buttons: tabs are created from each lane's "Tab" button;
        # bars are added/copied/pasted/deleted via the grid's right-click menu
        # (and Ctrl+C/X/V, Del); bar length is set by dragging the handles; time
        # signature is edited by clicking it. The current tab's name + switch
        # dropdown live in the gutter to the left of the grid.

        # technique palette
        pal = QHBoxLayout(); pal.setSpacing(4)
        pal.addWidget(QLabel("Technique:"))
        for label, tech in [("H", "h"), ("P", "p"), ("Bend", "b"), ("/", "/"),
                            ("\\", "\\"), ("~", "~"), ("PM", "PM"), ("×", "x")]:
            b = QPushButton(label)
            b.setFixedHeight(24)
            b.clicked.connect(lambda _=False, tk=tech: self._palette_tech(tk))
            pal.addWidget(b)
        hint = QLabel("Click a cell, type fret digits; ↑↓ string, ←→ beat, Del clears.  "
                      "Click a bar's top handle to select it (Ctrl/Shift for multiple).")
        hint.setStyleSheet(f"color: {self._theme.ink3}; font-size: 11px;")
        pal.addSpacing(10); pal.addWidget(hint)
        pal.addStretch(1)
        root.addLayout(pal)

        self._timeline = TabTimeline(self._theme)
        self._timeline.changed.connect(self.changed)
        self._timeline.seek_requested.connect(self.seek_requested)
        self._timeline.zoom_scroll_changed.connect(self.zoom_scroll_changed)
        self._timeline.tab_renamed.connect(self._on_tab_renamed)
        self._timeline.tab_switch.connect(self._on_tab_switch)
        root.addWidget(self._timeline)

    # ------------------------------------------------------------- public API
    def set_duration(self, ms: int):
        self._duration = max(1, int(ms))
        self._timeline.set_duration(ms)

    def set_lane_ids(self, lane_ids: list[str]):
        self._lane_ids = list(lane_ids)

    def set_tabs(self, tracks: list[TabTrack]):
        self._tracks = list(tracks or [])
        self._cur = 0 if self._tracks else -1
        self._show_current()

    def tabs(self) -> list[TabTrack]:
        return self._tracks

    def set_zoom_scroll(self, zoom: float, scroll_frac: float):
        self._timeline.set_zoom_scroll(zoom, scroll_frac)

    def set_progress(self, frac: float):
        self._timeline.set_progress(frac)

    # ------------------------------------------------------------- internals
    def _show_current(self):
        track = self._tracks[self._cur] if 0 <= self._cur < len(self._tracks) else None
        self._timeline.set_track(track)
        self._update_header()

    def _update_header(self):
        track = self._cur_track()
        others = [(i, t.name) for i, t in enumerate(self._tracks) if i != self._cur]
        self._timeline.set_header(track.name if track else None, others)

    def create_tab(self, stem_id: str, name: str, strings: int,
                   ts_num: int, ts_den: int):
        """Create a tab from the lane 'Add tab' dialog, select and show it."""
        existing = {t.id for t in self._tracks}
        n = 1
        while f"tab{n}" in existing:
            n += 1
        track = TabTrack(id=f"tab{n}", stem_id=stem_id, name=name,
                         strings=strings, tuning=default_tuning(strings),
                         def_ts_num=ts_num, def_ts_den=ts_den)
        self._tracks.append(track)
        self._cur = len(self._tracks) - 1
        self._show_current()
        self.changed.emit()

    def _on_tab_switch(self, idx: int):
        if 0 <= idx < len(self._tracks):
            self._cur = idx
            self._show_current()

    def _on_tab_renamed(self, name: str):
        track = self._cur_track()
        if track and name:
            track.name = name
            self.changed.emit()
            self._update_header()

    def _palette_tech(self, tech: str):
        self._timeline._toggle_technique(tech)

    def _cur_track(self) -> TabTrack | None:
        return self._tracks[self._cur] if 0 <= self._cur < len(self._tracks) else None
