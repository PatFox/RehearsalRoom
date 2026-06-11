"""Shared small widgets: WaveformWidget, FaderSlider, MSSButton, ArtWidget."""

import math
import random
from typing import Optional

from PySide6.QtCore import Qt, Signal, QRect, QPointF, QRectF, QEvent
from PySide6.QtGui import QPainter, QColor, QPainterPath, QLinearGradient, QBrush, QPen, QFont
from PySide6.QtWidgets import (
    QWidget, QSizePolicy, QLabel, QLineEdit, QStackedLayout, QApplication
)


# ---------------------------------------------------------------------------
# InlineEditLabel — a label that becomes an editable field on click
# ---------------------------------------------------------------------------

class InlineEditLabel(QWidget):
    """Displays as a QLabel; clicking activates an inline QLineEdit.

    Keyboard behaviour:
      Enter / Return  → commit the new value and revert to label
      Escape          → discard changes and revert to label
      Focus lost      → commit (same as Enter)

    Signals:
      committed(str)  → emitted when a new value is saved
    """
    committed = Signal(str)

    def __init__(self, text: str = "", label_style: str = "",
                 edit_style: str = "", placeholder: str = "", parent=None):
        super().__init__(parent)
        self._current = text
        self._editing = False

        # Use a stacked layout so only the active widget consumes space.
        stack = QStackedLayout(self)
        stack.setContentsMargins(0, 0, 0, 0)
        self._stack = stack

        self._label = QLabel(text)
        if label_style:
            self._label.setStyleSheet(label_style)
        self._label.setCursor(Qt.CursorShape.IBeamCursor)
        self._label.setToolTip("Click to edit")

        self._edit = QLineEdit(text)
        if edit_style:
            self._edit.setStyleSheet(edit_style)
        elif label_style:
            # Derive edit style from label style — keep font, remove colour extras
            self._edit.setStyleSheet(label_style)
        if placeholder:
            self._edit.setPlaceholderText(placeholder)

        stack.addWidget(self._label)   # index 0
        stack.addWidget(self._edit)    # index 1
        stack.setCurrentIndex(0)

        # Wire up editing events
        self._edit.returnPressed.connect(self._commit)
        self._edit.installEventFilter(self)

        # Click on label to start editing
        self._label.mousePressEvent = lambda e: self._start_edit()

    # ------------------------------------------------------------------ API

    def text(self) -> str:
        return self._current

    def setText(self, text: str):
        self._current = text
        self._label.setText(text)
        if not self._editing:
            self._edit.setText(text)

    def setStyleSheet(self, style: str):
        """Forward style to both inner widgets."""
        self._label.setStyleSheet(style)
        self._edit.setStyleSheet(style)

    # ------------------------------------------------------------------ internal

    def _start_edit(self):
        self._edit.setText(self._current)
        self._edit.selectAll()
        self._stack.setCurrentIndex(1)
        self._editing = True
        self._edit.setFocus()

    def _commit(self):
        if not self._editing:
            return
        new_val = self._edit.text().strip()
        if not new_val:
            new_val = self._current   # don't allow blanking out
        self._current = new_val
        self._label.setText(new_val)
        self._stack.setCurrentIndex(0)
        self._editing = False
        self.committed.emit(new_val)

    def _cancel(self):
        self._edit.setText(self._current)
        self._stack.setCurrentIndex(0)
        self._editing = False

    def eventFilter(self, obj, event):
        if obj is self._edit:
            if event.type() == QEvent.Type.FocusOut:
                self._commit()
            elif event.type() == QEvent.Type.KeyPress:
                if event.key() == Qt.Key.Key_Escape:
                    self._cancel()
                    return True
        return super().eventFilter(obj, event)


# ---------------------------------------------------------------------------
# Procedural waveform generation (mirrors design JS implementation)
# ---------------------------------------------------------------------------

def _mulberry32(seed: int):
    a = seed & 0xFFFFFFFF
    def rng():
        nonlocal a
        a = (a + 0x6D2B79F5) & 0xFFFFFFFF
        t = (a ^ (a >> 15)) & 0xFFFFFFFF
        t = (t * (1 | a)) & 0xFFFFFFFF
        t = (t ^ (t >> 7)) & 0xFFFFFFFF
        t = (t * (61 | t)) & 0xFFFFFFFF
        t = (t ^ t >> 14) & 0xFFFFFFFF
        return t / 4294967296
    return rng


def _smooth(t: float) -> float:
    return t * t * (3 - 2 * t)


def _song_energy(n: int) -> list:
    sections = [0.32, 0.6, 0.95, 0.62, 0.98, 0.5, 1.0, 0.34]
    env = []
    for i in range(n):
        p = i / n
        si = min(len(sections) - 1, int(p * len(sections)))
        local = (p * len(sections)) % 1
        nxt = sections[min(len(sections) - 1, si + 1)]
        e = sections[si] + (nxt - sections[si]) * _smooth(local)
        env.append(e)
    return env


def gen_waveform(song_seed: int, stem_id: str, n: int = 320) -> list:
    r = _mulberry32(song_seed * 31 + ord(stem_id[0]) * 7 + len(stem_id))
    energy = _song_energy(n)
    out = []
    for i in range(n):
        p = i / n
        e = energy[i]
        if stem_id == "vocals":
            phrase = math.sin(p * math.pi * 26) * 0.5 + 0.5
            gate = 1 if phrase > 0.34 else 0.06
            intro = 0.04 if p < 0.08 else 1
            v = e * gate * intro * (0.55 + r() * 0.5)
        elif stem_id == "drums":
            beat = 1 if i % 4 == 0 else (0.62 if i % 2 == 0 else 0.4)
            intro = 0.12 if p < 0.06 else 1
            v = e * intro * beat * (0.6 + r() * 0.45)
        elif stem_id == "bass":
            wob = math.sin(p * math.pi * 40) * 0.12 + 0.78
            intro = 0.1 if p < 0.05 else 1
            v = e * intro * wob * (0.7 + r() * 0.2)
        else:
            tex = math.sin(p * math.pi * 60) * 0.18 + 0.7
            v = e * tex * (0.55 + r() * 0.4)
        out.append(max(0.03, min(1.0, v)))
    return out


# ---------------------------------------------------------------------------
# Waveform canvas widget
# ---------------------------------------------------------------------------

_HANDLE_RADIUS = 8   # px — hit area for dragging a loop handle
_LOOP_FILL   = QColor(46, 107, 255, 38)   # semi-transparent blue fill
_LOOP_BORDER = QColor(46, 107, 255, 160)  # border / handle colour
_ZOOM_MIN    = 1.0
_ZOOM_MAX    = 32.0
_ZOOM_STEP   = 1.3   # multiplier per wheel click
_BAR_W       = 2.0   # fixed screen px per bar (constant at all zoom levels)
_BAR_GAP     = 0.8   # gap between bars


class WaveformWidget(QWidget):
    seeked              = Signal(float)        # fraction 0-1
    loop_set            = Signal(float, float) # start_frac, end_frac
    loop_cleared        = Signal()            # user started a new drag — clear old loop
    handle_moved        = Signal(str, float)   # "start"|"end", new_frac
    zoom_scroll_changed = Signal(float, float) # zoom, scroll_frac (broadcast to siblings)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data: list = []
        self._color: str = "#2E6BFF"
        self._progress: float = 0.0
        self._muted: bool = False
        self._loop_start: float = -1.0
        self._loop_end:   float = -1.0
        self._loop_end_placeholder: bool = False
        self._drag_mode: str = ""
        self._drag_origin: float = 0.0
        self._loop_preview_start: float = -1.0
        self._loop_preview_end:   float = -1.0
        # Zoom / scroll
        self._zoom: float = 1.0
        self._scroll_frac: float = 0.0   # 0 = leftmost, 1 = rightmost
        self.setMinimumHeight(40)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)

    # ------------------------------------------------------------------ data
    def set_data(self, data: list, color: str):
        self._data = data
        self._color = color
        self.update()

    def set_progress(self, p: float):
        if abs(p - self._progress) > 0.0005:
            self._progress = p
            self.update()

    def set_muted(self, m: bool):
        self._muted = m
        self.update()

    def set_loop_region(self, start: float, end: float, end_is_placeholder: bool = False):
        self._loop_start          = start
        self._loop_end            = end
        self._loop_end_placeholder = end_is_placeholder
        self.update()

    def set_zoom_scroll(self, zoom: float, scroll_frac: float):
        self._zoom = max(_ZOOM_MIN, zoom)
        self._scroll_frac = max(0.0, min(1.0, scroll_frac))
        self.update()

    # -------------------------------------------- coordinate helpers
    def _scroll_px(self) -> float:
        return self._scroll_frac * self.width() * (self._zoom - 1)

    def _total_w(self) -> float:
        return self.width() * self._zoom

    def _screen_x(self, frac: float) -> float:
        """Song fraction → screen x coordinate."""
        return frac * self._total_w() - self._scroll_px()

    def _frac(self, screen_x: float) -> float:
        """Screen x → song fraction."""
        tw = self._total_w()
        if tw <= 0:
            return 0.0
        return max(0.0, min(1.0, (screen_x + self._scroll_px()) / tw))

    # --------------------------------------------------------------- painting
    def paintEvent(self, event):
        if not self._data:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        mid, max_bar = h / 2, h * 0.42

        # --- visible window in song-fraction space ---
        vis_frac   = 1.0 / max(self._zoom, 1e-9)
        start_frac = self._scroll_frac * (1.0 - vis_frac)

        # Slice the high-res data to the visible portion
        n_total    = len(self._data)
        start_idx  = int(start_frac * n_total)
        end_idx    = min(n_total, max(start_idx + 1, int((start_frac + vis_frac) * n_total)))
        src        = self._data[start_idx:end_idx]

        # How many fixed-width bars fit across the widget?
        step   = _BAR_W + _BAR_GAP
        n_bars = max(1, int(w / step))
        n_src  = len(src)

        # Downsample/resample src → n_bars display buckets (peak per bucket)
        bars: list[float] = []
        for i in range(n_bars):
            s = int(i * n_src / n_bars)
            e = max(s + 1, int((i + 1) * n_src / n_bars))
            bars.append(max(src[s:e]))

        play_x = self._screen_x(self._progress)

        if self._muted:
            c   = QColor(self._color)
            lum = int(c.red() * 0.30 + c.green() * 0.59 + c.blue() * 0.11)
            col = QColor(lum, lum, lum)
        else:
            col = QColor(self._color)

        for i, amp_norm in enumerate(bars):
            x   = i * step
            amp = amp_norm * max_bar
            if self._muted:
                col.setAlphaF(0.22)
            else:
                col.setAlphaF(1.0 if x < play_x else 0.28)
            painter.fillRect(QRectF(x, mid - amp, _BAR_W, amp * 2), col)

        # --- playhead line ---
        if 0.0 <= play_x <= w:
            painter.setPen(QColor(180, 180, 180, 220))
            painter.drawLine(QPointF(play_x, 0), QPointF(play_x, h))

        # --- loop region ---
        ls, le = self._loop_start, self._loop_end
        if self._drag_mode == "loop_new" and self._loop_preview_start >= 0:
            ls, le = self._loop_preview_start, self._loop_preview_end

        end_placeholder = self._loop_end_placeholder
        if self._drag_mode == "loop_new":
            end_placeholder = False   # real drag — always show both ends
        if ls >= 0 and le > ls:
            x1, x2 = self._screen_x(ls), self._screen_x(le)
            painter.fillRect(QRectF(x1, 0, x2 - x1, h), _LOOP_FILL)
            painter.setPen(_LOOP_BORDER)
            painter.drawLine(QPointF(x1, 0), QPointF(x1, h))
            if not end_placeholder:
                painter.drawLine(QPointF(x2, 0), QPointF(x2, h))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_LOOP_BORDER)
            handles = (x1,) if end_placeholder else (x1, x2)
            for hx in handles:
                painter.drawEllipse(QPointF(hx, 10), 5, 5)

        painter.end()

    # --------------------------------------------------------------- mouse
    def _near_handle(self, screen_x: float) -> str:
        if self._loop_start >= 0 and abs(screen_x - self._screen_x(self._loop_start)) <= _HANDLE_RADIUS:
            return "start"
        if self._loop_end >= 0 and abs(screen_x - self._screen_x(self._loop_end)) <= _HANDLE_RADIUS:
            return "end"
        return ""

    def mouseMoveEvent(self, e):
        x = e.position().x()
        handle = self._near_handle(x)
        # Cursor hint: show resize cursor near handles, crosshair elsewhere over waveform
        if handle:
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif self._loop_start >= 0 and self._loop_end >= 0:
            self.setCursor(Qt.CursorShape.CrossCursor)
        else:
            self.setCursor(Qt.CursorShape.PointingHandCursor)

        if self._drag_mode == "seek":
            self.seeked.emit(self._frac(x))
        elif self._drag_mode == "handle_start":
            f = min(self._frac(x), self._loop_end - 0.001)
            self._loop_start = f
            self.handle_moved.emit("start", f)
            self.update()
        elif self._drag_mode == "handle_end":
            f = max(self._frac(x), self._loop_start + 0.001)
            self._loop_end = f
            self.handle_moved.emit("end", f)
            self.update()
        elif self._drag_mode == "loop_new" and e.buttons() & Qt.MouseButton.RightButton:
            cur = self._frac(x)
            self._loop_preview_start = min(self._drag_origin, cur)
            self._loop_preview_end   = max(self._drag_origin, cur)
            self.update()

    def mousePressEvent(self, e):
        x = e.position().x()
        if e.button() == Qt.MouseButton.RightButton:
            # Right button: set/drag loop region or move handles
            handle = self._near_handle(x)
            if handle == "start":
                self._drag_mode = "handle_start"
            elif handle == "end":
                self._drag_mode = "handle_end"
            else:
                self._drag_mode = "loop_new"
                self._drag_origin = self._frac(x)
                self._loop_preview_start = self._loop_preview_end = self._drag_origin
                # Clear committed loop immediately so old overlay doesn't show during drag
                self._loop_start = -1.0
                self._loop_end   = -1.0
                self.loop_cleared.emit()
        elif e.button() == Qt.MouseButton.LeftButton:
            self._drag_mode = "seek"
            self.seeked.emit(self._frac(x))

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.RightButton:
            if self._drag_mode == "loop_new":
                s, end = self._loop_preview_start, self._loop_preview_end
                # Require at least 4 screen pixels — works at any zoom level or song length
                min_frac = 4.0 / max(1, self.width() * self._zoom)
                if end - s > min_frac:
                    self._loop_start = s
                    self._loop_end   = end
                    self.loop_set.emit(s, end)
                self._loop_preview_start = self._loop_preview_end = -1.0
                self.update()
            self._drag_mode = ""
        elif e.button() == Qt.MouseButton.LeftButton:
            self._drag_mode = ""

    def contextMenuEvent(self, e):
        # Suppress the default right-click context menu
        e.accept()

    def wheelEvent(self, e):
        delta = e.angleDelta().y()
        if delta == 0:
            return
        factor = _ZOOM_STEP if delta > 0 else 1.0 / _ZOOM_STEP
        cursor_x = e.position().x()
        cursor_frac = self._frac(cursor_x)   # song fraction under cursor
        new_zoom = max(_ZOOM_MIN, min(_ZOOM_MAX, self._zoom * factor))

        if new_zoom <= _ZOOM_MIN:
            self.zoom_scroll_changed.emit(_ZOOM_MIN, 0.0)
            e.accept()
            return

        # Keep the song fraction under the cursor fixed on screen
        w = max(1, self.width())
        new_scroll_px = cursor_frac * w * new_zoom - cursor_x
        max_scroll_px = w * (new_zoom - 1)
        new_scroll_px = max(0.0, min(new_scroll_px, max_scroll_px))
        new_scroll_frac = new_scroll_px / max_scroll_px if max_scroll_px > 0 else 0.0
        self.zoom_scroll_changed.emit(new_zoom, new_scroll_frac)
        e.accept()


# ---------------------------------------------------------------------------
# Waveform scroll bar  (shown below lanes when zoom > 1)
# ---------------------------------------------------------------------------

class WaveformScrollBar(QWidget):
    scrolled = Signal(float)   # new scroll_frac 0-1

    def __init__(self, theme=None, parent=None):
        super().__init__(parent)
        self._zoom: float = 1.0
        self._scroll_frac: float = 0.0
        self._dragging = False
        self._drag_start_x = 0.0
        self._drag_start_frac = 0.0
        self._theme = theme
        self.setFixedHeight(10)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.hide()

    def set_zoom_scroll(self, zoom: float, scroll_frac: float):
        self._zoom = zoom
        self._scroll_frac = scroll_frac
        self.setVisible(zoom > 1.001)
        self.update()

    def _handle_rect(self) -> QRectF:
        w = self.width()
        hw = max(20.0, w / self._zoom)
        hx = self._scroll_frac * (w - hw)
        return QRectF(hx, 1, hw, self.height() - 2)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        track_col = QColor("#E4E4EC") if (self._theme and not self._theme.dark) else QColor("#2A2A32")
        handle_col = QColor("#A0A0B0") if (self._theme and not self._theme.dark) else QColor("#5A5A6A")
        p.fillRect(0, 0, w, h, track_col)
        r = self._handle_rect()
        path = QPainterPath()
        path.addRoundedRect(r, 3, 3)
        p.fillPath(path, handle_col)
        p.end()

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        x = e.position().x()
        r = self._handle_rect()
        if r.left() <= x <= r.right():
            self._dragging = True
            self._drag_start_x    = x
            self._drag_start_frac = self._scroll_frac
        else:
            # Click on track — jump
            hw = r.width()
            new_frac = (x - hw / 2) / max(1, self.width() - hw)
            self.scrolled.emit(max(0.0, min(1.0, new_frac)))

    def mouseMoveEvent(self, e):
        if not self._dragging:
            return
        hw  = self._handle_rect().width()
        max_x = max(1, self.width() - hw)
        dx = e.position().x() - self._drag_start_x
        new_frac = self._drag_start_frac + dx / max_x
        self.scrolled.emit(max(0.0, min(1.0, new_frac)))

    def mouseReleaseEvent(self, e):
        self._dragging = False


# ---------------------------------------------------------------------------
# Stem colour bar (vertical bar on the left of lane header)
# ---------------------------------------------------------------------------

class ColorBar(QWidget):
    def __init__(self, color: str, parent=None):
        super().__init__(parent)
        self._color = color
        self.setFixedWidth(4)

    def set_color(self, c: str):
        self._color = c
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        r = 2.0
        path.addRoundedRect(QRectF(0, 0, self.width(), self.height()), r, r)
        p.fillPath(path, QColor(self._color))
        p.end()


# ---------------------------------------------------------------------------
# ArtThumbnail — gradient square with animated bars (like the design)
# ---------------------------------------------------------------------------

class ArtThumbnail(QWidget):
    def __init__(self, grad_start: str = "#2E6BFF", grad_end: str = "#7C5CFF",
                 seed: int = 42, size: int = 44, parent=None):
        super().__init__(parent)
        self._gs = grad_start
        self._ge = grad_end
        self._seed = seed
        self.setFixedSize(size, size)
        rnd = random.Random(seed)
        self._bar_heights = [0.30 + rnd.random() * 0.70 for _ in range(7)]

    def update_song(self, grad_start: str, grad_end: str, seed: int):
        self._gs = grad_start
        self._ge = grad_end
        self._seed = seed
        rnd = random.Random(seed)
        self._bar_heights = [0.30 + rnd.random() * 0.70 for _ in range(7)]
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        r = 9.0

        grad = QLinearGradient(0, 0, w, h)
        grad.setColorAt(0, QColor(self._gs))
        grad.setColorAt(1, QColor(self._ge))
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, w, h), r, r)
        p.fillPath(path, grad)

        # bars
        n = len(self._bar_heights)
        bar_w = 2.5
        gap = 2.0
        total = n * bar_w + (n - 1) * gap
        x0 = (w - total) / 2
        p.setBrush(QColor(255, 255, 255, 230))
        p.setPen(Qt.PenStyle.NoPen)
        bar_area = h * 0.5
        for i, bh in enumerate(self._bar_heights):
            bh_px = bh * bar_area
            x = x0 + i * (bar_w + gap)
            y = h / 2 - bh_px / 2
            rect_path = QPainterPath()
            rect_path.addRoundedRect(QRectF(x, y, bar_w, bh_px), 1.5, 1.5)
            p.drawPath(rect_path)
        p.end()
