"""Tablature editor — a QPainter tab timeline that shares the waveform's
zoom/scroll, plus a docked panel with the editing toolbar.

Phase 1 / MVP: one tab track at a time, Tier-1 notation (string+fret,
common techniques), bars anchored to the audio by millisecond start/end,
drag-to-resize bar ends, keyboard + palette note entry, playback highlight.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QRectF, QPointF
from PySide6.QtGui import QPainter, QColor, QPen, QFont
from PySide6.QtWidgets import (
    QFrame, QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton,
    QComboBox, QSpinBox,
)

from ui.widgets import TimelineCoords
from core.tab import (TabTrack, Bar, Beat, Note, default_tuning,
                      beat_ms, find_bar, active_bar_beat)

GUTTER_W = 244        # match Ruler / lane head width
ROW_GAP  = 18         # px between string lines
TOP_PAD  = 24         # room for time-signature labels
BOT_PAD  = 18
_HANDLE_HIT = 6       # px hit radius for a bar-end resize handle
_SUBDIV = 4           # editing-grid columns per beat (16th-note grid in 4/4)

# In-app bar clipboard: cloned Bar objects (content + original length; ms
# anchors are ignored on paste-into, used as default lengths on paste-insert).
# Module-level so bars can be copied between tracks/songs within a session.
_BAR_CLIPBOARD: list = []


class TabTimeline(TimelineCoords, QWidget):
    """The drawable/editable tab canvas for one TabTrack."""

    seek_requested      = Signal(float)          # fraction 0-1
    changed             = Signal()                # tab data mutated → persist
    zoom_scroll_changed = Signal(float, float)    # zoom, scroll_frac

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
        self._sel_anchor = None        # bar index where a range selection began
        self._fret_session = False     # True while consecutive digits build one fret
        self._drag_bar = -1            # bar whose end-handle is being dragged
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._apply_height()

    # ------------------------------------------------------------------ state
    def set_track(self, track: TabTrack | None):
        self._track = track
        self._caret = (-1, 0, 0)
        self._apply_height()
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
        sel = self._selection() if self._sel_anchor is not None else None

        for bi, bar in enumerate(self._track.bars):
            x0 = self._x_for_ms(bar.start_ms)
            x1 = self._x_for_ms(bar.end_ms)

            # active-bar wash
            if bi == act_bar:
                wash = QColor(accent); wash.setAlpha(20)
                p.fillRect(QRectF(x0, 0, x1 - x0, h), wash)

            # selection wash (explicit range)
            if sel and sel[0] <= bi <= sel[1]:
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

            # barline + time signature
            p.setPen(QPen(QColor(t.ink3), 1))
            p.drawLine(QPointF(x0, TOP_PAD - 6), QPointF(x0, self._row_y(nstr - 1) + 6))
            p.setFont(QFont("Consolas", 8))
            p.setPen(QColor(t.ink2))
            # show time sig only when it changes from the previous bar
            prev = self._track.bars[bi - 1] if bi > 0 else None
            if prev is None or (prev.ts_num, prev.ts_den) != (bar.ts_num, bar.ts_den):
                p.drawText(QPointF(x0 + 3, TOP_PAD - 9), f"{bar.ts_num}/{bar.ts_den}")

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

            # bar-end resize handle
            p.setPen(QPen(accent if bi == self._drag_bar else QColor(t.ink3), 2))
            p.drawLine(QPointF(x1, TOP_PAD - 6), QPointF(x1, self._row_y(nstr - 1) + 6))

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

    def _end_handle_at(self, x: float) -> int:
        if not self._track:
            return -1
        for bi, bar in enumerate(self._track.bars):
            if abs(x - self._x_for_ms(bar.end_ms)) <= _HANDLE_HIT:
                return bi
        return -1

    def mousePressEvent(self, e):
        self.setFocus()
        x = e.position().x()
        if x < GUTTER_W or not self._track:
            return
        # bar-end drag-resize takes priority
        bi = self._end_handle_at(x)
        if bi >= 0:
            self._drag_bar = bi
            return
        # otherwise place the caret
        bi = self._bar_at_x(x)
        if bi < 0:
            return
        bar = self._track.bars[bi]
        ncols = self._ncols(bar)
        frac_in_bar = (self._frac(x) * self._duration - bar.start_ms) / max(1, bar.length_ms)
        col = max(0, min(ncols - 1, round(frac_in_bar * ncols)))
        row = max(0, min(self._strings() - 1, round((e.position().y() - TOP_PAD) / ROW_GAP)))
        if e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            if self._sel_anchor is None:
                self._sel_anchor = self._caret[0] if self._caret[0] >= 0 else bi
        else:
            self._sel_anchor = None
        self._caret = (bi, col, row)
        self._fret_session = False
        self.update()

    def mouseMoveEvent(self, e):
        x = e.position().x()
        if self._drag_bar >= 0 and self._track:
            ms = max(self._track.bars[self._drag_bar].start_ms + 50,
                     int(self._frac(x) * self._duration))
            self._set_bar_end(self._drag_bar, ms)
            self.update()
            return
        # cursor hint over a resize handle
        self.setCursor(Qt.CursorShape.SizeHorCursor if self._end_handle_at(x) >= 0
                       else Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, e):
        if self._drag_bar >= 0:
            self._drag_bar = -1
            self.changed.emit()

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

    def _set_bar_end(self, bi: int, ms: int):
        bars = self._track.bars
        bars[bi].end_ms = ms
        if bi + 1 < len(bars):
            bars[bi + 1].start_ms = ms
            if bars[bi + 1].end_ms < ms:
                bars[bi + 1].end_ms = ms + 50

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
            self._sel_anchor = None; self.update(); return
        if shift and key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            self._extend_selection(-1 if key == Qt.Key.Key_Left else 1); return

        if key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            self._sel_anchor = None
            self._move_col(-1 if key == Qt.Key.Key_Left else 1)
        elif key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            d = -1 if key == Qt.Key.Key_Up else 1
            self._caret = (bi, col, max(0, min(self._strings() - 1, row + d)))
            self._fret_session = False
            self.update()
        elif Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
            self._type_digit(key - Qt.Key.Key_0)
        elif key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self._clear_caret_note()
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
        if bars:
            prev = bars[-1]
            start = prev.end_ms
            length = prev.length_ms or default_len_ms
            bar = Bar(ts_num=prev.ts_num, ts_den=prev.ts_den,
                      start_ms=start, end_ms=start + length)
        else:
            bar = Bar(ts_num=4, ts_den=4, start_ms=0, end_ms=default_len_ms)
        bars.append(bar)
        self._caret = (len(bars) - 1, 0, self._caret[2])
        self.changed.emit()
        self.update()

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

    def set_time_signature(self, num: int, den: int):
        bi = self._caret[0]
        if bi < 0:
            bi = len(self._track.bars) - 1
        if bi >= 0:
            self._track.bars[bi].ts_num = num
            self._track.bars[bi].ts_den = den
            self.changed.emit()
            self.update()

    # ------------------------------------------------------------- selection + clipboard
    def _selection(self):
        """(lo, hi) inclusive bar range, or None. Caret bar when no anchor."""
        cbar = self._caret[0]
        if cbar < 0:
            return None
        if self._sel_anchor is None:
            return (cbar, cbar)
        return (min(self._sel_anchor, cbar), max(self._sel_anchor, cbar))

    def select_all(self):
        if not self._track or not self._track.bars:
            return
        self._sel_anchor = 0
        self._caret = (len(self._track.bars) - 1, 0, self._caret[2])
        self.update()

    def _extend_selection(self, d: int):
        if not self._track:
            return
        if self._sel_anchor is None:
            self._sel_anchor = self._caret[0]
        nb = max(0, min(len(self._track.bars) - 1, self._caret[0] + d))
        self._caret = (nb, 0, self._caret[2])
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

    def copy_selection(self):
        sel = self._selection()
        if not sel or not self._track:
            return
        lo, hi = sel
        _BAR_CLIPBOARD[:] = [Bar.from_dict(b.to_dict()) for b in self._track.bars[lo:hi + 1]]

    def cut_selection(self):
        sel = self._selection()
        if not sel or not self._track:
            return
        self.copy_selection()
        lo, hi = sel
        del self._track.bars[lo:hi + 1]
        self._sel_anchor = None
        self._caret = (min(lo, len(self._track.bars) - 1), 0, self._caret[2])
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
        start = self._caret[0]
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
        """Insert clipboard bars as new bars after the caret bar, shifting
        every later bar's anchors by the inserted total length."""
        bars = self._track.bars
        nstr = self._strings()
        idx = self._caret[0] + 1            # insert after the caret bar
        boundary = bars[idx].start_ms if idx < len(bars) else bars[-1].end_ms
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
        self._caret = (idx, 0, self._caret[2])
        self.changed.emit()
        self.update()


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

        bar = QHBoxLayout()
        bar.setSpacing(8)

        self._track_combo = QComboBox()
        self._track_combo.currentIndexChanged.connect(self._on_track_selected)
        bar.addWidget(QLabel("Tab:"))
        bar.addWidget(self._track_combo)

        new_btn = QPushButton("＋ New")
        new_btn.clicked.connect(self._add_track)
        bar.addWidget(new_btn)

        bar.addSpacing(8)
        bar.addWidget(QLabel("Lane:"))
        self._lane_combo = QComboBox()
        self._lane_combo.currentIndexChanged.connect(self._on_lane_changed)
        bar.addWidget(self._lane_combo)

        bar.addWidget(QLabel("Strings:"))
        self._strings_combo = QComboBox()
        self._strings_combo.addItems(["4", "5", "6", "7"])
        self._strings_combo.currentTextChanged.connect(self._on_strings_changed)
        bar.addWidget(self._strings_combo)

        bar.addWidget(QLabel("Time sig:"))
        self._ts_num = QSpinBox(); self._ts_num.setRange(1, 16); self._ts_num.setValue(4)
        self._ts_den = QComboBox(); self._ts_den.addItems(["1", "2", "4", "8", "16"])
        self._ts_den.setCurrentText("4")
        self._ts_num.valueChanged.connect(self._apply_ts)
        self._ts_den.currentTextChanged.connect(self._apply_ts)
        bar.addWidget(self._ts_num); bar.addWidget(QLabel("/")); bar.addWidget(self._ts_den)

        add_bar_btn = QPushButton("Add bar")
        add_bar_btn.clicked.connect(self._add_bar)
        bar.addWidget(add_bar_btn)

        end_btn = QPushButton("Set bar end → playhead")
        end_btn.setToolTip("Anchor the current bar's end to the playback position")
        end_btn.clicked.connect(self._set_bar_end_playhead)
        bar.addWidget(end_btn)

        bar.addSpacing(8)
        for label, tip, fn in [
            ("Copy", "Copy selected bar(s)  (Ctrl+C)", lambda: self._timeline.copy_selection()),
            ("Cut", "Cut selected bar(s)  (Ctrl+X)", lambda: self._timeline.cut_selection()),
            ("Paste", "Add copied bar(s) after the current bar  (Ctrl+V)",
             lambda: self._timeline.paste(insert=True)),
            ("Paste into", "Overwrite bars from the caret, keeping their anchors  (Ctrl+Shift+V)",
             lambda: self._timeline.paste(insert=False)),
        ]:
            b = QPushButton(label); b.setToolTip(tip); b.clicked.connect(fn)
            bar.addWidget(b)

        bar.addStretch(1)
        root.addLayout(bar)

        # technique palette
        pal = QHBoxLayout(); pal.setSpacing(4)
        pal.addWidget(QLabel("Technique:"))
        for label, tech in [("H", "h"), ("P", "p"), ("Bend", "b"), ("/", "/"),
                            ("\\", "\\"), ("~", "~"), ("PM", "PM"), ("×", "x")]:
            b = QPushButton(label)
            b.setFixedHeight(24)
            b.clicked.connect(lambda _=False, tk=tech: self._palette_tech(tk))
            pal.addWidget(b)
        hint = QLabel("Click a cell, type fret digits; ↑↓ string, ←→ beat, Del clears.")
        hint.setStyleSheet(f"color: {self._theme.ink3}; font-size: 11px;")
        pal.addSpacing(10); pal.addWidget(hint)
        pal.addStretch(1)
        root.addLayout(pal)

        self._timeline = TabTimeline(self._theme)
        self._timeline.changed.connect(self.changed)
        self._timeline.seek_requested.connect(self.seek_requested)
        self._timeline.zoom_scroll_changed.connect(self.zoom_scroll_changed)
        root.addWidget(self._timeline)

    # ------------------------------------------------------------- public API
    def set_duration(self, ms: int):
        self._duration = max(1, int(ms))
        self._timeline.set_duration(ms)

    def set_lane_ids(self, lane_ids: list[str]):
        self._lane_ids = list(lane_ids)
        self._lane_combo.blockSignals(True)
        self._lane_combo.clear()
        self._lane_combo.addItems(self._lane_ids)
        self._lane_combo.blockSignals(False)

    def set_tabs(self, tracks: list[TabTrack]):
        self._tracks = list(tracks or [])
        self._refresh_track_combo()
        self._cur = 0 if self._tracks else -1
        self._show_current()

    def tabs(self) -> list[TabTrack]:
        return self._tracks

    def set_zoom_scroll(self, zoom: float, scroll_frac: float):
        self._timeline.set_zoom_scroll(zoom, scroll_frac)

    def set_progress(self, frac: float):
        self._timeline.set_progress(frac)

    # ------------------------------------------------------------- internals
    def _refresh_track_combo(self):
        self._track_combo.blockSignals(True)
        self._track_combo.clear()
        self._track_combo.addItems([t.name for t in self._tracks] or ["—"])
        self._track_combo.blockSignals(False)

    def _show_current(self):
        track = self._tracks[self._cur] if 0 <= self._cur < len(self._tracks) else None
        self._timeline.set_track(track)
        if track:
            self._strings_combo.blockSignals(True)
            self._strings_combo.setCurrentText(str(track.strings))
            self._strings_combo.blockSignals(False)
            if track.stem_id in self._lane_ids:
                self._lane_combo.blockSignals(True)
                self._lane_combo.setCurrentText(track.stem_id)
                self._lane_combo.blockSignals(False)

    def _add_track(self):
        # Default 6-string guitar; name auto-incremented.
        n = len(self._tracks) + 1
        stem = self._lane_combo.currentText() or ""
        strings = 4 if stem == "bass" else 6
        track = TabTrack(id=f"tab{n}", stem_id=stem, name=f"Tab {n}",
                         strings=strings, tuning=default_tuning(strings))
        self._tracks.append(track)
        self._refresh_track_combo()
        self._cur = len(self._tracks) - 1
        self._track_combo.setCurrentIndex(self._cur)
        self._show_current()
        self.changed.emit()

    def _on_track_selected(self, idx: int):
        if 0 <= idx < len(self._tracks):
            self._cur = idx
            self._show_current()

    def _on_lane_changed(self, _idx: int):
        track = self._cur_track()
        if track:
            track.stem_id = self._lane_combo.currentText()
            self.changed.emit()

    def _on_strings_changed(self, text: str):
        track = self._cur_track()
        if not track:
            return
        track.strings = int(text)
        track.tuning = default_tuning(track.strings)
        self._timeline.set_track(track)   # re-measures height
        self.changed.emit()

    def _apply_ts(self, *_):
        track = self._cur_track()
        if track and track.bars:
            self._timeline.set_time_signature(self._ts_num.value(), int(self._ts_den.currentText()))

    def _add_bar(self):
        track = self._cur_track()
        if not track:
            self._add_track()
            track = self._cur_track()
        default_len = min(2000, self._duration) or 2000
        self._timeline.add_bar(default_len)

    def _set_bar_end_playhead(self):
        track = self._cur_track()
        if track and track.bars:
            self._timeline.set_caret_bar_end(int(self._timeline._progress * self._duration))

    def _palette_tech(self, tech: str):
        self._timeline._toggle_technique(tech)

    def _cur_track(self) -> TabTrack | None:
        return self._tracks[self._cur] if 0 <= self._cur < len(self._tracks) else None
