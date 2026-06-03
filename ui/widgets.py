"""Shared small widgets: WaveformWidget, FaderSlider, MSSButton, ArtWidget."""

import math
import random
from typing import Optional

from PySide6.QtCore import Qt, Signal, QRect, QPointF, QRectF
from PySide6.QtGui import QPainter, QColor, QPainterPath, QLinearGradient, QBrush, QPen, QFont
from PySide6.QtWidgets import QWidget, QSizePolicy


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

class WaveformWidget(QWidget):
    seeked = Signal(float)  # 0-1

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data: list = []
        self._color: str = "#2E6BFF"
        self._progress: float = 0.0
        self._muted: bool = False
        self.setMinimumHeight(40)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._dragging = False

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

    def paintEvent(self, event):
        if not self._data:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        h = self.height()
        n = len(self._data)
        gap = 1.4
        bw = w / n
        bar_w = max(1.0, bw - gap)
        mid = h / 2
        max_bar = h * 0.42
        play_x = w * self._progress

        col = QColor(self._color)
        for i, amp_norm in enumerate(self._data):
            x = i * bw + gap / 2
            amp = amp_norm * max_bar
            played = x < play_x
            col.setAlphaF(1.0 if played else 0.28)
            p.fillRect(QRectF(x, mid - amp, bar_w, amp * 2), col)

        p.end()

    def _pos_to_progress(self, x: int) -> float:
        return max(0.0, min(1.0, x / self.width()))

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self.seeked.emit(self._pos_to_progress(e.position().x()))

    def mouseMoveEvent(self, e):
        if self._dragging:
            self.seeked.emit(self._pos_to_progress(e.position().x()))

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
