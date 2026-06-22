"""Player/mixer panel — DAW-style stacked waveform lanes + transport bar.

Faithfully implements the design prototype (player.jsx) and adds:
- Playback speed slider (50–200 %, pitch-preserving) not in the original design.
"""

from __future__ import annotations
from typing import Optional

from PySide6.QtCore import Qt, Signal, Slot, QTimer, QRectF
from PySide6.QtGui import QColor, QPainter, QPainterPath, QLinearGradient
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QScrollArea, QSizePolicy, QDialog, QLineEdit, QSlider, QSplitter
)

from ui.theme import Theme, STEM_IDS, STEM_LABELS
from ui.widgets import WaveformWidget, WaveformScrollBar, ArtThumbnail, gen_waveform, InlineEditLabel


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _fmt_ms(ms: int) -> str:
    ms = max(0, int(ms))
    m = ms // 60000
    s = (ms % 60000) // 1000
    cs = (ms % 1000) // 10
    return f"{m}:{s:02d}.{cs:02d}"


def _fmt_clock(ms: int) -> str:
    s = max(0, ms // 1000)
    return f"{s // 60}:{s % 60:02d}"


def _cover_bytes(song: dict):
    """Resolve cover-art bytes for a song: embedded in .stems → disk cache."""
    from pathlib import Path
    from core.project import read_cover
    from core import artwork
    sp = song.get("stems_path")
    data = None
    if sp:
        try:
            data = read_cover(Path(sp))
        except Exception:
            data = None
    if not data:
        data = artwork.cached_cover(song.get("artist", ""), song.get("title", ""))
    return data


# ---------------------------------------------------------------------------
# Ruler (time markers above the lanes)
# ---------------------------------------------------------------------------

class Ruler(QWidget):
    seek_requested = Signal(float)  # 0-1
    reset_heights  = Signal()       # reset all lane heights to equal

    GUTTER_W = 244

    # Candidate tick intervals in ms, from coarse to fine
    _INTERVALS = [
        300_000, 120_000, 60_000, 30_000, 15_000,
        10_000, 5_000, 2_000, 1_000, 500
    ]
    # Minimum px between ticks before we pick a finer interval
    _MIN_TICK_PX = 60

    def __init__(self, duration_ms: int, theme: Theme, parent=None):
        super().__init__(parent)
        self._dur = max(1, duration_ms)
        self._theme = theme
        self._zoom: float = 1.0
        self._scroll_frac: float = 0.0
        self.setFixedHeight(34)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        # Reset-heights button, right-aligned within the "STEMS" gutter.
        self._reset_btn = QPushButton("⇕", self)
        self._reset_btn.setFixedSize(22, 22)
        self._reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._reset_btn.setToolTip("Reset stem heights to equal")
        self._reset_btn.setStyleSheet(
            "QPushButton { border: none; background: transparent; font-size: 14px; "
            f"color: {theme.ink3}; border-radius: 4px; padding: 0; }}"
            f"QPushButton:hover {{ background: {theme.surface3}; color: {theme.ink}; }}")
        self._reset_btn.clicked.connect(self.reset_heights)

    def resizeEvent(self, e):
        b = self._reset_btn
        b.move(self.GUTTER_W - b.width() - 8, (self.height() - b.height()) // 2)
        super().resizeEvent(e)

    def set_duration(self, ms: int):
        self._dur = max(1, ms)
        self.update()

    def set_zoom_scroll(self, zoom: float, scroll_frac: float):
        self._zoom = max(1.0, zoom)
        self._scroll_frac = scroll_frac
        self.update()

    def _visible_range_ms(self) -> tuple[float, float]:
        """Return (start_ms, end_ms) of the currently visible window."""
        vis_frac   = 1.0 / self._zoom
        start_frac = self._scroll_frac * (1.0 - vis_frac)
        return start_frac * self._dur, (start_frac + vis_frac) * self._dur

    def _ms_to_x(self, ms: float, track_w: int) -> int:
        """Convert an absolute song-time ms to screen x within the track area."""
        start_ms, end_ms = self._visible_range_ms()
        span = max(1.0, end_ms - start_ms)
        return self.GUTTER_W + int((ms - start_ms) / span * track_w)

    def _pick_interval(self, track_w: int) -> int:
        """Choose the finest tick interval where ticks are still at least _MIN_TICK_PX apart."""
        start_ms, end_ms = self._visible_range_ms()
        visible_ms = max(1.0, end_ms - start_ms)
        # Iterate finest → coarsest; return the first (finest) that has enough spacing
        chosen = self._INTERVALS[0]  # coarsest fallback
        for iv in reversed(self._INTERVALS):
            px_per_tick = iv / visible_ms * track_w
            if px_per_tick >= self._MIN_TICK_PX:
                chosen = iv
                break
        return chosen

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        w, h = self.width(), self.height()
        gw = self.GUTTER_W
        track_w = w - gw

        # backgrounds
        p.fillRect(0, 0, gw, h, QColor(self._theme.surface))
        p.fillRect(gw, 0, track_w, h, QColor(self._theme.surface))

        # gutter label
        p.setPen(QColor(self._theme.ink3))
        f = p.font(); f.setPointSize(8); f.setBold(True); p.setFont(f)
        p.drawText(16, 0, gw - 16, h, Qt.AlignmentFlag.AlignVCenter, "STEMS")

        # borders
        p.setPen(QColor(self._theme.border))
        p.drawLine(gw, 0, gw, h)
        p.drawLine(0, h - 1, w, h - 1)

        # time ticks at adaptive interval
        start_ms, end_ms = self._visible_range_ms()
        interval = self._pick_interval(track_w)

        # Start from the first tick >= start_ms
        first_tick = int(start_ms / interval + 1) * interval
        t = first_tick
        f2 = p.font(); f2.setPointSize(8); f2.setFamily("Consolas"); f2.setBold(False)
        p.setFont(f2)
        while t <= min(end_ms, self._dur):
            x = self._ms_to_x(t, track_w)
            if gw < x < w:
                p.setPen(QColor(self._theme.border))
                p.drawLine(x, 0, x, h)
                p.setPen(QColor(self._theme.ink3))
                p.drawText(x + 4, 8, 70, 18, 0, _fmt_clock(t))
            t += interval

        p.end()

    def _to_progress(self, x: int) -> float:
        """Screen x → song fraction, accounting for zoom/scroll."""
        track_w = max(1, self.width() - self.GUTTER_W)
        start_ms, end_ms = self._visible_range_ms()
        frac_in_view = (x - self.GUTTER_W) / track_w
        ms = start_ms + frac_in_view * (end_ms - start_ms)
        return max(0.0, min(1.0, ms / self._dur))

    def mousePressEvent(self, e):
        if e.position().x() > self.GUTTER_W:
            self.seek_requested.emit(self._to_progress(e.position().x()))

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MouseButton.LeftButton and e.position().x() > self.GUTTER_W:
            self.seek_requested.emit(self._to_progress(e.position().x()))


# ---------------------------------------------------------------------------
# Playhead overlay (drawn on top of all lanes)
# ---------------------------------------------------------------------------

class PlayheadOverlay(QWidget):
    seek_requested = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._progress = 0.0
        self._color = "#17171B"
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("background: transparent;")

    def set_progress(self, p: float):
        if abs(p - self._progress) > 0.0005:
            self._progress = p
            self.update()

    def set_color(self, c: str):
        self._color = c
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        x = int(self._progress * self.width())
        col = QColor(self._color)
        p.setPen(col)
        p.setBrush(col)
        p.drawLine(x, 0, x, self.height())
        # dot at top
        p.drawEllipse(x - 5, -1, 11, 11)
        p.end()

    def mousePressEvent(self, e):
        p = max(0.0, min(1.0, e.position().x() / max(1, self.width())))
        self.seek_requested.emit(p)

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MouseButton.LeftButton:
            p = max(0.0, min(1.0, e.position().x() / max(1, self.width())))
            self.seek_requested.emit(p)


# ---------------------------------------------------------------------------
# Lane (one stem row)
# ---------------------------------------------------------------------------

class MSSButton(QPushButton):
    """Mute or Solo square button styled to match the design."""
    def __init__(self, letter: str, parent=None):
        super().__init__(letter, parent)
        self.setFixedSize(25, 25)
        self.setCheckable(True)
        self._letter = letter
        self._apply()

    def nextCheckState(self):
        self.setChecked(not self.isChecked())
        self._apply()

    def _apply(self):
        # padding:0 is essential — the global QPushButton QSS sets padding that
        # would otherwise push the single letter outside this 25x25 button.
        if self.isChecked():
            if self._letter == "M":
                self.setStyleSheet(
                    "background: #FFE9C7; color: #B26B00; border: 1px solid #F2C887; "
                    "border-radius: 4px; font-size: 11px; font-weight: 700; padding: 0;"
                )
            else:
                self.setStyleSheet(
                    "background: rgba(46,107,255,0.12); color: #2E6BFF; "
                    "border: 1px solid rgba(46,107,255,0.4); border-radius: 4px; "
                    "font-size: 11px; font-weight: 700; padding: 0;"
                )
        else:
            self.setStyleSheet(
                "background: #F4F4F0; color: #93939C; border: 1px solid transparent; "
                "border-radius: 4px; font-size: 11px; font-weight: 700; padding: 0;"
            )


class FaderSlider(QWidget):
    value_changed = Signal(int)

    def __init__(self, color: str, parent=None):
        super().__init__(parent)
        self._color = color
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, 150)
        self._slider.setValue(100)
        self._slider.setStyleSheet(f"""
            QSlider::groove:horizontal {{
                height: 5px; background: #ECECE6; border-radius: 3px;
            }}
            QSlider::handle:horizontal {{
                width: 15px; height: 15px; background: white;
                border: 2px solid {color}; border-radius: 8px; margin: -5px 0;
            }}
            QSlider::sub-page:horizontal {{
                background: {color}; border-radius: 3px;
            }}
        """)
        self._val_lbl = QLabel("100%")
        self._val_lbl.setStyleSheet("font-family: 'Consolas', monospace; font-size: 11px;")
        self._val_lbl.setFixedWidth(38)
        self._val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._slider.valueChanged.connect(self._on_change)
        self._slider.mouseDoubleClickEvent = lambda e: self._reset()
        self._val_lbl.mouseDoubleClickEvent = lambda e: self._reset()
        lay.addWidget(self._slider, 1)
        lay.addWidget(self._val_lbl)

    def _on_change(self, v: int):
        self._val_lbl.setText(f"{v}%")
        self.value_changed.emit(v)

    def _reset(self):
        self._slider.setValue(100)

    def value(self) -> int:
        return self._slider.value()


class StemLane(QFrame):
    mute_toggled        = Signal(str, bool)
    solo_toggled        = Signal(str, bool)
    volume_changed      = Signal(str, int)
    seek_requested      = Signal(float)
    loop_set            = Signal(float, float)
    handle_moved        = Signal(str, float)
    zoom_scroll_changed = Signal(float, float)   # zoom, scroll_frac
    loop_cleared        = Signal()
    label_changed       = Signal(str, str)        # stem_id, new_label
    add_tab_requested   = Signal(str)             # stem_id

    LANE_HEIGHT = 65

    def __init__(self, stem_id: str, label: str, color: str, wavedata: list,
                 theme: Theme, parent=None):
        super().__init__(parent)
        self._id = stem_id
        self._color = color
        self._theme = theme
        # LANE_HEIGHT is the *minimum* (enough for the head: name + M/S + fader).
        # The splitter grows lanes to fill, and lets the user drag dividers.
        self.setMinimumHeight(self.LANE_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Ignored)
        self.setStyleSheet(f"QFrame {{ border-bottom: 1px solid {theme.border}; background: transparent; }}")
        self._setup_ui(label, color, wavedata)

    def _setup_ui(self, label: str, color: str, wavedata: list):
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # --- lane head (244px fixed) ---
        head = QFrame()
        head.setFixedWidth(244)
        head.setStyleSheet(
            f"QFrame {{ background: {self._theme.surface}; border-right: 1px solid {self._theme.border}; }}"
        )
        head_lay = QVBoxLayout(head)
        head_lay.setContentsMargins(14, 6, 14, 6)
        head_lay.setSpacing(5)

        # top row: color bar + name + M/S buttons
        top_row = QHBoxLayout()
        top_row.setSpacing(8)
        bar = QFrame()
        bar.setFixedWidth(4)
        bar.setFixedHeight(25)
        bar.setStyleSheet(f"background: {color}; border-radius: 2px;")
        self._name_lbl = InlineEditLabel(
            label,
            label_style="font-size: 14px; font-weight: 600; background: transparent; border: none;",
            edit_style=(
                "font-size: 14px; font-weight: 600; background: transparent;"
                " border: none; border-bottom: 1px solid #2E6BFF; padding: 0px;"
            ),
        )
        self._name_lbl.setFixedHeight(25)   # match the M/S buttons
        self._name_lbl.committed.connect(lambda v: self.label_changed.emit(self._id, v))

        self._mute_btn = MSSButton("M")
        self._solo_btn = MSSButton("S")
        self._mute_btn.clicked.connect(lambda: self.mute_toggled.emit(self._id, self._mute_btn.isChecked()))
        self._solo_btn.clicked.connect(lambda: self.solo_toggled.emit(self._id, self._solo_btn.isChecked()))

        top_row.addWidget(bar)
        top_row.addWidget(self._name_lbl, 1)
        # "Add tab" button, left of mute — every lane except the original mix
        if self._id != "original":
            tab_btn = QPushButton("Tab")
            tab_btn.setFixedHeight(25)
            tab_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            tab_btn.setToolTip("Add a tablature track for this lane")
            tab_btn.setStyleSheet(
                "QPushButton { background: #F4F4F0; color: #5F5E5A; "
                "border: 1px solid transparent; border-radius: 7px; padding: 0 8px; "
                "font-size: 11px; font-weight: 700; }"
                "QPushButton:hover { background: rgba(46,107,255,0.12); color: #2E6BFF; }")
            tab_btn.clicked.connect(lambda: self.add_tab_requested.emit(self._id))
            top_row.addWidget(tab_btn)
        top_row.addWidget(self._mute_btn)
        top_row.addWidget(self._solo_btn)
        head_lay.addLayout(top_row)

        # fader row — sits directly under the top row; leftover space goes below
        self._fader = FaderSlider(color)
        self._fader.value_changed.connect(lambda v: self.volume_changed.emit(self._id, v))
        self._fader.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        head_lay.addWidget(self._fader)
        head_lay.addStretch(1)

        lay.addWidget(head)

        # --- waveform area ---
        self._wave = WaveformWidget()
        self._wave.set_data(wavedata, color)
        self._wave.seeked.connect(self.seek_requested)
        self._wave.loop_set.connect(self.loop_set)
        self._wave.handle_moved.connect(self.handle_moved)
        self._wave.zoom_scroll_changed.connect(self.zoom_scroll_changed)
        self._wave.loop_cleared.connect(self.loop_cleared)
        lay.addWidget(self._wave, 1)

    def set_progress(self, p: float):
        self._wave.set_progress(p)

    def set_loop_region(self, start: float, end: float, end_is_placeholder: bool = False):
        self._wave.set_loop_region(start, end, end_is_placeholder)

    def set_zoom_scroll(self, zoom: float, scroll_frac: float):
        self._wave.set_zoom_scroll(zoom, scroll_frac)

    def set_audible(self, audible: bool):
        from PySide6.QtWidgets import QGraphicsOpacityEffect
        # One effect per lane, toggled — setGraphicsEffect(None) destroys the
        # effect, so recreating it on every mute/solo click would churn.
        if not hasattr(self, "_dim_effect"):
            self._dim_effect = QGraphicsOpacityEffect(self)
            self._dim_effect.setOpacity(0.38)
            self._dim_effect.setEnabled(False)
            self.setGraphicsEffect(self._dim_effect)
        self._dim_effect.setEnabled(not audible)
        self._wave.set_muted(not audible)
        color = "#93939C" if not audible else self._theme.ink
        self._name_lbl._label.setStyleSheet(
            f"font-size: 14px; font-weight: 600; background: transparent; border: none; color: {color};"
        )

    def update_name(self, name: str):
        self._name_lbl.setText(name)

    def reflect_muted(self, muted: bool):
        """Sync the M button + dimming to an externally-set mute state."""
        self._mute_btn.setChecked(muted)
        self._mute_btn._apply()
        self.set_audible(not muted)


# ---------------------------------------------------------------------------
# Transport bar
# ---------------------------------------------------------------------------

class TransportBar(QFrame):
    play_pause     = Signal()
    stop           = Signal()
    restart        = Signal()
    loop_clicked   = Signal()
    save_loop      = Signal()   # emitted when user clicks the save-loop button
    master_changed = Signal(int)
    tempo_changed  = Signal(float)
    pitch_changed  = Signal(int)    # semitones

    def __init__(self, duration_ms: int, theme: Theme, parent=None):
        super().__init__(parent)
        self._theme = theme
        self._duration = duration_ms
        self._current_speed = 1.0
        self._current_pitch = 0
        self.setFixedHeight(88)
        self.setStyleSheet(
            f"QFrame {{ background: {theme.surface}; border-top: 1px solid {theme.border}; }}"
        )
        self._setup_ui()

    def _setup_ui(self):
        lay = QHBoxLayout(self)
        lay.setContentsMargins(24, 0, 24, 0)
        lay.setSpacing(18)

        # time display
        self._time_lbl = QLabel("0:00.00")
        self._time_lbl.setStyleSheet(
            "font-family: 'Consolas', monospace; font-size: 15px; font-weight: 500;"
        )
        self._total_lbl = QLabel(f" / {_fmt_ms(self._duration)}")
        self._total_lbl.setStyleSheet(
            f"font-family: 'Consolas', monospace; font-size: 15px; color: {self._theme.ink3};"
        )
        time_row = QHBoxLayout()
        time_row.setSpacing(0)
        time_row.addWidget(self._time_lbl)
        time_row.addWidget(self._total_lbl)
        lay.addLayout(time_row)

        # transport controls
        ctrl = QHBoxLayout()
        ctrl.setSpacing(6)

        restart_btn = self._tbtn("⏮", "Restart")
        restart_btn.clicked.connect(self.restart)
        ctrl.addWidget(restart_btn)

        self._play_btn = QPushButton("▶")
        self._play_btn.setFixedSize(56, 56)
        self._play_btn.setStyleSheet(
            f"QPushButton {{ background: {self._theme.ink}; color: {self._theme.ink_inv}; "
            f"border-radius: 28px; font-size: 18px; }}"
            f"QPushButton:hover {{ background: {self._theme.ink2}; }}"
        )
        self._play_btn.clicked.connect(self.play_pause)
        ctrl.addWidget(self._play_btn)

        stop_btn = self._tbtn("⏹", "Stop")
        stop_btn.clicked.connect(self.stop)
        ctrl.addWidget(stop_btn)

        self._loop_btn = self._tbtn("⊙", "Click to set loop start (L)")
        self._loop_btn.clicked.connect(self.loop_clicked)
        ctrl.addWidget(self._loop_btn)

        self._save_loop_btn = self._tbtn("💾", "Save current loop")
        self._save_loop_btn.clicked.connect(self.save_loop)
        self._save_loop_btn.hide()   # only visible when loop is active
        ctrl.addWidget(self._save_loop_btn)

        lay.addLayout(ctrl)

        # --- Speed buttons ---
        speed_group = QVBoxLayout()
        speed_group.setSpacing(3)
        speed_lbl = QLabel("SPEED")
        speed_lbl.setStyleSheet(
            f"font-size: 10px; font-weight: 600; letter-spacing: 0.1em; color: {self._theme.ink3};"
        )
        speed_row = QHBoxLayout()
        speed_row.setSpacing(4)

        btn_style = (
            f"QPushButton {{ background: {self._theme.surface2}; border: 1px solid {self._theme.border}; "
            f"border-radius: 14px; font-size: 16px; font-weight: 600; color: {self._theme.ink}; }}"
            f"QPushButton:hover {{ background: {self._theme.surface3}; }}"
            f"QPushButton:disabled {{ color: {self._theme.ink3}; background: {self._theme.surface}; }}"
        )
        self._speed_down_btn = QPushButton("−")
        self._speed_down_btn.setFixedSize(28, 28)
        self._speed_down_btn.setToolTip("Slower (min 0.5×)")
        self._speed_down_btn.setStyleSheet(btn_style)
        self._speed_down_btn.clicked.connect(self._speed_down)

        self._speed_val_lbl = QLabel("1.0×")
        self._speed_val_lbl.setStyleSheet(
            "font-family: 'Consolas', monospace; font-size: 13px; font-weight: 600;"
        )
        self._speed_val_lbl.setFixedWidth(38)
        self._speed_val_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._speed_val_lbl.setToolTip("Double-click to reset to 1.0×")
        self._speed_val_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
        self._speed_val_lbl.mouseDoubleClickEvent = lambda e: self._set_speed(1.0)

        self._speed_up_btn = QPushButton("+")
        self._speed_up_btn.setFixedSize(28, 28)
        self._speed_up_btn.setToolTip("Faster (max 1.0×)")
        self._speed_up_btn.setStyleSheet(btn_style)
        self._speed_up_btn.setEnabled(False)   # start at 1.0× so + is disabled
        self._speed_up_btn.clicked.connect(self._speed_up)

        # Animated processing indicator
        self._speed_spinner = QLabel()
        self._speed_spinner.setStyleSheet(
            f"font-size: 12px; color: {self._theme.accent}; font-family: monospace;"
        )
        self._speed_spinner.setFixedWidth(14)
        self._speed_busy_timer = QTimer()
        self._speed_busy_timer.setInterval(80)
        self._speed_busy_timer.timeout.connect(self._tick_spinner)
        self._spinner_frames = "⣾⣽⣻⢿⡿⣟⣯⣷"
        self._spinner_idx = 0
        self._speed_spinner.hide()

        speed_row.addWidget(self._speed_down_btn)
        speed_row.addWidget(self._speed_val_lbl)
        speed_row.addWidget(self._speed_up_btn)
        speed_row.addWidget(self._speed_spinner)
        speed_group.addWidget(speed_lbl)
        speed_group.addLayout(speed_row)
        lay.addLayout(speed_group)

        # --- Pitch buttons (semitones, independent of speed) ---
        pitch_group = QVBoxLayout()
        pitch_group.setSpacing(3)
        pitch_lbl = QLabel("PITCH")
        pitch_lbl.setStyleSheet(
            f"font-size: 10px; font-weight: 600; letter-spacing: 0.1em; color: {self._theme.ink3};"
        )
        pitch_row = QHBoxLayout()
        pitch_row.setSpacing(4)

        self._pitch_down_btn = QPushButton("−")
        self._pitch_down_btn.setFixedSize(28, 28)
        self._pitch_down_btn.setToolTip("Down a semitone (min −12)")
        self._pitch_down_btn.setStyleSheet(btn_style)
        self._pitch_down_btn.clicked.connect(self._pitch_down)

        self._pitch_val_lbl = QLabel("0")
        self._pitch_val_lbl.setStyleSheet(
            "font-family: 'Consolas', monospace; font-size: 13px; font-weight: 600;"
        )
        self._pitch_val_lbl.setFixedWidth(38)
        self._pitch_val_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._pitch_val_lbl.setToolTip("Double-click to reset to 0")
        self._pitch_val_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pitch_val_lbl.mouseDoubleClickEvent = lambda e: self._set_pitch(0)

        self._pitch_up_btn = QPushButton("+")
        self._pitch_up_btn.setFixedSize(28, 28)
        self._pitch_up_btn.setToolTip("Up a semitone (max +12)")
        self._pitch_up_btn.setStyleSheet(btn_style)
        self._pitch_up_btn.clicked.connect(self._pitch_up)

        pitch_row.addWidget(self._pitch_down_btn)
        pitch_row.addWidget(self._pitch_val_lbl)
        pitch_row.addWidget(self._pitch_up_btn)
        pitch_group.addWidget(pitch_lbl)
        pitch_group.addLayout(pitch_row)
        lay.addLayout(pitch_group)

        lay.addStretch()

        # master fader
        master_group = QHBoxLayout()
        master_group.setSpacing(10)
        ml = QLabel("MASTER")
        ml.setStyleSheet(
            f"font-size: 10px; font-weight: 600; letter-spacing: 0.1em; color: {self._theme.ink3};"
        )
        master_group.addWidget(ml)

        vol_icon = QLabel("🔊")
        vol_icon.setStyleSheet(f"color: {self._theme.ink2}; font-size: 14px;")
        master_group.addWidget(vol_icon)

        self._master_slider = QSlider(Qt.Orientation.Horizontal)
        self._master_slider.setRange(0, 150)
        self._master_slider.setValue(100)
        self._master_slider.setFixedWidth(130)
        self._master_slider.setStyleSheet(f"""
            QSlider::groove:horizontal {{
                height: 5px; background: {self._theme.surface3}; border-radius: 3px;
            }}
            QSlider::handle:horizontal {{
                width: 15px; height: 15px; background: {self._theme.surface};
                border: 2px solid {self._theme.ink}; border-radius: 8px; margin: -5px 0;
            }}
            QSlider::sub-page:horizontal {{
                background: {self._theme.ink}; border-radius: 3px;
            }}
        """)
        self._master_val_lbl = QLabel("100")
        self._master_val_lbl.setStyleSheet("font-family: 'Consolas', monospace; font-size: 12px;")
        self._master_val_lbl.setFixedWidth(30)
        self._master_slider.valueChanged.connect(lambda v: (
            self._master_val_lbl.setText(str(v)),
            self.master_changed.emit(v)
        ))
        _reset_master = lambda e: self._master_slider.setValue(100)
        self._master_slider.mouseDoubleClickEvent = _reset_master
        self._master_val_lbl.mouseDoubleClickEvent = _reset_master
        master_group.addWidget(self._master_slider)
        master_group.addWidget(self._master_val_lbl)
        lay.addLayout(master_group)

    def _tbtn(self, icon: str, tip: str) -> QPushButton:
        btn = QPushButton(icon)
        btn.setFixedSize(42, 42)
        btn.setToolTip(tip)
        btn.setStyleSheet(
            f"QPushButton {{ background: transparent; border-radius: 21px; font-size: 16px; color: {self._theme.ink2}; }}"
            f"QPushButton:hover {{ background: {self._theme.surface2}; color: {self._theme.ink}; }}"
            f"QPushButton:checked {{ background: {self._theme.accent_soft()}; color: {self._theme.accent}; }}"
        )
        return btn

    def _speed_down(self):
        self._set_speed(round(self._current_speed - 0.1, 1))

    def _speed_up(self):
        self._set_speed(round(self._current_speed + 0.1, 1))

    def _set_speed(self, rate: float):
        rate = max(0.5, min(1.0, round(rate, 1)))
        self._current_speed = rate
        self._speed_val_lbl.setText(f"{rate:.1f}×")
        self._speed_down_btn.setEnabled(rate > 0.5)
        self._speed_up_btn.setEnabled(rate < 1.0)
        self.tempo_changed.emit(rate)

    def _pitch_down(self):
        self._set_pitch(self._current_pitch - 1)

    def _pitch_up(self):
        self._set_pitch(self._current_pitch + 1)

    def _set_pitch(self, semitones: int):
        semitones = max(-12, min(12, int(semitones)))
        self._current_pitch = semitones
        self._pitch_val_lbl.setText(
            f"+{semitones}" if semitones > 0 else str(semitones))
        self._pitch_down_btn.setEnabled(semitones > -12)
        self._pitch_up_btn.setEnabled(semitones < 12)
        self.pitch_changed.emit(semitones)

    def set_pitch_display(self, semitones: int):
        """Update the pitch display without re-emitting (e.g. on song load)."""
        semitones = max(-12, min(12, int(semitones)))
        self._current_pitch = semitones
        self._pitch_val_lbl.setText(
            f"+{semitones}" if semitones > 0 else str(semitones))
        self._pitch_down_btn.setEnabled(semitones > -12)
        self._pitch_up_btn.setEnabled(semitones < 12)

    def _tick_spinner(self):
        self._spinner_idx = (self._spinner_idx + 1) % len(self._spinner_frames)
        self._speed_spinner.setText(self._spinner_frames[self._spinner_idx])

    def set_speed(self, rate: float):
        """Update the speed display to match a given rate without re-emitting."""
        rate = max(0.25, min(4.0, rate))
        self._speed_val_lbl.setText(f"{int(round(rate * 100))}%")
        self._speed_down_btn.setEnabled(rate > 0.25)
        self._speed_up_btn.setEnabled(rate < 4.0)

    @Slot()
    def show_speed_busy(self):
        self._spinner_idx = 0
        self._speed_spinner.setText(self._spinner_frames[0])
        self._speed_spinner.show()
        self._speed_busy_timer.start()

    @Slot()
    def hide_speed_busy(self):
        self._speed_busy_timer.stop()
        self._speed_spinner.hide()

    def set_loop_state(self, state: int):
        """0 = no loop, 1 = start set, 2 = loop active."""
        icons   = ["⊙", "⊙", "⊛"]
        tips    = ["Click to set loop start (L)",
                   "Click to set loop end (L)",
                   "Click to clear loop (L)"]
        colours = [self._theme.ink2,
                   "#F2A23A",          # orange = waiting for end point
                   self._theme.accent] # blue   = loop active
        self._loop_btn.setText(icons[state])
        self._loop_btn.setToolTip(tips[state])
        self._loop_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; border-radius: 21px; "
            f"font-size: 16px; color: {colours[state]}; }}"
            f"QPushButton:hover {{ background: {self._theme.surface2}; }}"
        )
        self._save_loop_btn.setVisible(state == 2)

    def set_playing(self, playing: bool):
        self._play_btn.setText("⏸" if playing else "▶")

    def set_time(self, ms: int):
        self._time_lbl.setText(_fmt_ms(ms))

    def set_duration(self, ms: int):
        self._duration = ms
        self._total_lbl.setText(f" / {_fmt_ms(ms)}")


# ---------------------------------------------------------------------------
# Save loop dialog
# ---------------------------------------------------------------------------

class SaveLoopDialog(QDialog):
    saved = Signal(str)   # chosen name

    def __init__(self, default_name: str, theme: Theme, parent=None):
        super().__init__(parent)
        self._theme = theme
        self.setWindowTitle("Save loop")
        self.setFixedWidth(380)
        self.setModal(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 20)
        lay.setSpacing(12)

        title = QLabel("Save loop")
        title.setStyleSheet("font-size: 16px; font-weight: 600;")
        lay.addWidget(title)

        lbl = QLabel("Loop name")
        lbl.setStyleSheet(f"font-size: 12px; color: {theme.ink3};")
        lay.addWidget(lbl)

        self._input = QLineEdit(default_name)
        self._input.selectAll()
        lay.addWidget(self._input)

        foot = QHBoxLayout()
        foot.addStretch()
        cancel = QPushButton("Cancel")
        cancel.setProperty("role", "ghost")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("Save")
        ok.setProperty("role", "primary")
        ok.clicked.connect(self._on_save)
        foot.addWidget(cancel)
        foot.addWidget(ok)
        lay.addLayout(foot)

        self._input.returnPressed.connect(self._on_save)

    def _on_save(self):
        name = self._input.text().strip()
        if name:
            self.saved.emit(name)
            self.accept()


# ---------------------------------------------------------------------------
# Loop list panel (right sidebar)
# ---------------------------------------------------------------------------

class LoopListPanel(QFrame):
    loop_activated = Signal(object)   # SavedLoop
    loop_deleted   = Signal(str)      # loop name

    def __init__(self, theme: Theme, parent=None):
        super().__init__(parent)
        self._theme = theme
        self.setFixedWidth(210)
        self.setStyleSheet(
            f"QFrame {{ background: {theme.surface}; "
            f"border-left: 1px solid {theme.border}; }}"
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        hdr = QWidget()
        hdr.setFixedHeight(34)
        hdr.setStyleSheet(f"background: {theme.surface};")
        hdr_lay = QHBoxLayout(hdr)
        hdr_lay.setContentsMargins(12, 0, 12, 0)
        lbl = QLabel("SAVED LOOPS")
        lbl.setStyleSheet(
            f"font-size: 9px; font-weight: 700; letter-spacing: 0.1em; color: {theme.ink3};"
        )
        hdr_lay.addWidget(lbl)
        root.addWidget(hdr)

        div = QFrame()
        div.setFrameShape(QFrame.Shape.HLine)
        div.setStyleSheet(f"color: {theme.border};")
        root.addWidget(div)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._container = QWidget()
        self._container.setStyleSheet(f"background: {theme.surface};")
        self._list_lay = QVBoxLayout(self._container)
        self._list_lay.setContentsMargins(0, 4, 0, 4)
        self._list_lay.setSpacing(0)
        self._list_lay.addStretch()
        self._scroll.setWidget(self._container)
        root.addWidget(self._scroll, 1)

        self._empty_lbl = QLabel("No saved loops")
        self._empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_lbl.setStyleSheet(f"font-size: 12px; color: {theme.ink3}; padding: 16px;")
        self._list_lay.insertWidget(0, self._empty_lbl)

    def set_loops(self, loops):
        """Rebuild the list. loops is a list of SavedLoop objects."""
        # Remove old rows (keep stretch and empty label)
        while self._list_lay.count() > 2:
            item = self._list_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self._empty_lbl.setVisible(len(loops) == 0)
        self.setVisible(len(loops) > 0)

        for lp in loops:
            row = self._make_row(lp)
            self._list_lay.insertWidget(self._list_lay.count() - 2, row)

    def _make_row(self, lp) -> QWidget:
        row = QWidget()
        row.setStyleSheet(
            f"QWidget {{ background: transparent; }}"
            f"QWidget:hover {{ background: {self._theme.surface2}; }}"
        )
        row.setCursor(Qt.CursorShape.PointingHandCursor)
        row.setFixedHeight(52)

        lay = QHBoxLayout(row)
        lay.setContentsMargins(12, 4, 8, 4)
        lay.setSpacing(4)

        info = QVBoxLayout()
        info.setSpacing(1)
        name_lbl = QLabel(lp.name)
        name_lbl.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {self._theme.ink};")
        time_lbl = QLabel(f"{_fmt_ms(lp.start_ms)} – {_fmt_ms(lp.end_ms)}")
        time_lbl.setStyleSheet(
            f"font-family: Consolas, monospace; font-size: 10px; color: {self._theme.ink3};"
        )
        info.addWidget(name_lbl)
        info.addWidget(time_lbl)
        lay.addLayout(info, 1)

        del_btn = QPushButton("✕")
        del_btn.setFixedSize(22, 22)
        del_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; border-radius: 11px; "
            f"font-size: 11px; color: {self._theme.ink3}; border: none; }}"
            f"QPushButton:hover {{ background: {self._theme.surface3}; color: {self._theme.ink}; }}"
        )
        del_btn.clicked.connect(lambda: self.loop_deleted.emit(lp.name))
        lay.addWidget(del_btn)

        # Click anywhere on the row (except delete) to activate
        row.mousePressEvent = lambda e, _lp=lp: self.loop_activated.emit(_lp)
        return row


# ---------------------------------------------------------------------------
# Metadata edit dialog
# ---------------------------------------------------------------------------

class MetaDialog(QDialog):
    saved = Signal(dict)  # {title, artist, stem_labels: {id: label}}

    def __init__(self, meta: dict, stem_labels: dict[str, str],
                 stem_colors: dict[str, str], theme: Theme, parent=None):
        super().__init__(parent)
        self._theme = theme
        self.setWindowTitle("Edit metadata")
        self.setFixedWidth(460)
        self.setModal(True)
        self._setup_ui(meta, stem_labels, stem_colors)
        self.setStyleSheet(f"QDialog {{ background: {theme.surface}; border-radius: 4px; }}")

    def _setup_ui(self, meta: dict, stem_labels: dict, stem_colors: dict):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 24)
        lay.setSpacing(0)

        head_row = QHBoxLayout()
        head_lay = QVBoxLayout()
        QLabel("Edit metadata", self).setStyleSheet("font-size: 19px; font-weight: 600;")
        title_h = QLabel("Edit metadata")
        title_h.setStyleSheet("font-size: 19px; font-weight: 600;")
        sub_h = QLabel("Rename the track and its stems.")
        sub_h.setStyleSheet(f"font-size: 13px; color: {self._theme.ink3};")
        head_lay.addWidget(title_h)
        head_lay.addWidget(sub_h)
        close_btn = QPushButton("✕")
        close_btn.setProperty("role", "icon")
        close_btn.setFixedSize(32, 32)
        close_btn.clicked.connect(self.reject)
        head_row.addLayout(head_lay, 1)
        head_row.addWidget(close_btn, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(head_row)
        lay.addSpacing(16)

        def field(lbl_text, val):
            w = QWidget()
            fl = QVBoxLayout(w)
            fl.setContentsMargins(0, 0, 0, 12)
            fl.setSpacing(5)
            lbl = QLabel(lbl_text.upper())
            lbl.setStyleSheet(f"font-size: 11px; font-weight: 600; letter-spacing: 0.07em; color: {self._theme.ink3};")
            inp = QLineEdit(val)
            fl.addWidget(lbl)
            fl.addWidget(inp)
            return w, inp

        title_w, self._title_inp = field("Title", meta.get("title", ""))
        artist_w, self._artist_inp = field("Artist", meta.get("artist", ""))
        lay.addWidget(title_w)
        lay.addWidget(artist_w)

        # stem labels
        stems_lbl = QLabel("STEM LABELS")
        stems_lbl.setStyleSheet(f"font-size: 11px; font-weight: 600; letter-spacing: 0.07em; color: {self._theme.ink3}; margin-bottom: 5px;")
        lay.addWidget(stems_lbl)
        self._stem_inputs: dict[str, QLineEdit] = {}
        for sid in STEM_IDS:
            row = QHBoxLayout()
            row.setSpacing(10)
            dot = QFrame()
            dot.setFixedSize(12, 12)
            dot.setStyleSheet(f"background: {stem_colors.get(sid, '#888')}; border-radius: 6px;")
            inp = QLineEdit(stem_labels.get(sid, sid.capitalize()))
            self._stem_inputs[sid] = inp
            row.addWidget(dot)
            row.addWidget(inp, 1)
            rw = QWidget()
            rw.setLayout(row)
            lay.addWidget(rw)
        lay.addSpacing(18)

        foot = QHBoxLayout()
        foot.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setProperty("role", "ghost")
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("Save changes")
        save_btn.setProperty("role", "primary")
        save_btn.clicked.connect(self._on_save)
        foot.addWidget(cancel_btn)
        foot.addWidget(save_btn)
        lay.addLayout(foot)

    def _on_save(self):
        self.saved.emit({
            "title": self._title_inp.text(),
            "artist": self._artist_inp.text(),
            "stem_labels": {sid: inp.text() for sid, inp in self._stem_inputs.items()},
        })
        self.accept()


# ---------------------------------------------------------------------------
# PlayerPanel — the full view
# ---------------------------------------------------------------------------

class PlayerPanel(QWidget):
    back_clicked  = Signal()
    export_clicked = Signal(dict, str)   # song, mode ("all" | "current" | "original")
    reseparate_clicked = Signal(dict)    # song
    tab_changed        = Signal(list)    # list[TabTrack] — persist
    save_metadata  = Signal(dict)
    loop_save_requested = Signal(object)   # SavedLoop — MainWindow writes to disk
    loop_delete_requested = Signal(str)    # loop name

    def __init__(self, theme: Theme, parent=None):
        super().__init__(parent)
        self._theme = theme
        self._song: Optional[dict] = None
        self._duration = 1
        self._playing = False
        self._time_ms = 0.0
        self._tempo_rate = 1.0
        # Loop state: 0=none 1=start-set 2=active
        self._loop_state: int = 0
        self._loop_start_ms: float = -1.0
        self._loop_end_ms:   float = -1.0
        self._mutes: dict[str, bool] = {}
        self._solos: dict[str, bool] = {}
        # Zoom / scroll (shared across all lanes)
        self._zoom: float = 1.0
        self._scroll_frac: float = 0.0
        self._auto_center: bool = False
        self._volumes: dict[str, int] = {s: 100 for s in STEM_IDS}
        self._stem_labels: dict[str, str] = {}
        self._lanes: dict[str, StemLane] = {}
        self._player = None  # set externally after construction

        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(16)        # ~60 fps for smooth scrolling
        self._tick_timer.timeout.connect(self._on_tick)
        # Smoothed display position: eases toward the real audio position so the
        # playhead + auto-scroll glide instead of stepping with the ~23 ms audio
        # callback / frame quantisation. Visual only — loop logic uses _time_ms.
        self._disp_ms = 0.0

        self._setup_ui()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # top bar
        self._topbar = QFrame()
        self._topbar.setFixedHeight(70)
        self._topbar.setStyleSheet(
            f"QFrame {{ background: transparent; border-bottom: 1px solid {self._theme.border}; }}"
        )
        top_lay = QHBoxLayout(self._topbar)
        top_lay.setContentsMargins(20, 0, 20, 0)
        top_lay.setSpacing(14)

        self._back_btn = QPushButton("‹  Library")
        self._back_btn.setProperty("role", "ghost")
        self._back_btn.setFixedHeight(36)
        self._back_btn.clicked.connect(self.back_clicked)
        top_lay.addWidget(self._back_btn)

        self._art = ArtThumbnail("#2E6BFF", "#7C5CFF", 42, 42)
        top_lay.addWidget(self._art)

        meta_col = QVBoxLayout()
        meta_col.setSpacing(2)
        self._title_lbl = InlineEditLabel(
            "—",
            label_style="font-size: 16px; font-weight: 600; background: transparent; border: none;",
            edit_style=(
                "font-size: 16px; font-weight: 600; background: transparent;"
                " border: none; border-bottom: 1px solid #2E6BFF; padding: 0px;"
            ),
            placeholder="Track title",
        )
        self._title_lbl.committed.connect(self._on_title_committed)
        self._artist_lbl = InlineEditLabel(
            "",
            label_style=f"font-size: 12px; color: {self._theme.ink3}; background: transparent; border: none;",
            edit_style=(
                f"font-size: 12px; color: {self._theme.ink3}; background: transparent;"
                f" border: none; border-bottom: 1px solid #2E6BFF; padding: 0px;"
            ),
            placeholder="Artist name",
        )
        self._artist_lbl.committed.connect(self._on_artist_committed)
        self._yt_link_lbl = QLabel("")
        self._yt_link_lbl.setStyleSheet(f"font-size: 11px; color: {self._theme.ink3};")
        self._yt_link_lbl.setOpenExternalLinks(True)
        self._yt_link_lbl.hide()
        meta_col.addWidget(self._title_lbl)
        meta_col.addWidget(self._artist_lbl)
        meta_col.addWidget(self._yt_link_lbl)
        top_lay.addLayout(meta_col)
        top_lay.addStretch()

        # readout chips
        chips_row = QHBoxLayout()
        chips_row.setSpacing(16)
        for key, label_text in [("stems", "Stems"), ("model", "Model"), ("filesize", "Size")]:
            col = QVBoxLayout()
            col.setSpacing(1)
            k_lbl = QLabel(label_text.upper())
            k_lbl.setStyleSheet(f"font-size: 10px; font-weight: 600; letter-spacing: 0.1em; color: {self._theme.ink3};")
            defaults = {"stems": "4", "model": "htdemucs", "filesize": "—"}
            v_lbl = QLabel(defaults[key])
            v_lbl.setStyleSheet("font-family: 'Consolas', monospace; font-size: 13px;")
            col.addWidget(k_lbl)
            col.addWidget(v_lbl)
            setattr(self, f"_{key}_val_lbl", v_lbl)
            w = QWidget()
            w.setLayout(col)
            chips_row.addWidget(w)
        top_lay.addLayout(chips_row)

        self._tab_btn = QPushButton("Tab")
        self._tab_btn.setProperty("role", "ghost")
        self._tab_btn.setFixedHeight(34)
        self._tab_btn.setCheckable(True)
        self._tab_btn.setToolTip("Show/hide the tablature editor")
        self._tab_btn.toggled.connect(self._on_toggle_tab)
        top_lay.addWidget(self._tab_btn)

        self._resep_btn = QPushButton("Re-separate")
        self._resep_btn.setProperty("role", "ghost")
        self._resep_btn.setFixedHeight(34)
        self._resep_btn.setToolTip("Re-run stem separation from the original audio")
        self._resep_btn.clicked.connect(
            lambda: self._song and self.reseparate_clicked.emit(self._song))
        top_lay.addWidget(self._resep_btn)

        self._export_btn = QPushButton("Export  ▾")
        self._export_btn.setProperty("role", "ghost")
        self._export_btn.setFixedHeight(34)
        self._export_btn.clicked.connect(self._show_export_menu)
        top_lay.addWidget(self._export_btn)

        root.addWidget(self._topbar)

        # timeline (ruler + lanes) + loop list panel side by side
        self._timeline = QWidget()
        timeline_outer = QHBoxLayout(self._timeline)
        timeline_outer.setContentsMargins(0, 0, 0, 0)
        timeline_outer.setSpacing(0)

        # left: ruler + lanes + scrollbar
        lanes_area = QWidget()
        timeline_lay = QVBoxLayout(lanes_area)
        timeline_lay.setContentsMargins(0, 0, 0, 0)
        timeline_lay.setSpacing(0)

        self._ruler = Ruler(1, self._theme)
        self._ruler.seek_requested.connect(lambda p: self._seek(p, user_initiated=True))
        self._ruler.reset_heights.connect(self._reset_lane_heights)
        timeline_lay.addWidget(self._ruler)

        # Lanes live in a vertical splitter: they fill the available height by
        # default, the dividers between them are draggable to resize (each lane
        # has a minimum height), and they shrink when the tab editor appears.
        self._lanes_splitter = QSplitter(Qt.Orientation.Vertical)
        self._lanes_splitter.setChildrenCollapsible(False)
        self._lanes_splitter.setHandleWidth(1)
        self._lanes_splitter.setStyleSheet(
            f"QSplitter::handle {{ background: {self._theme.border}; }}")
        timeline_lay.addWidget(self._lanes_splitter, 1)

        # tab editor — docked below the lanes, shares the timeline zoom/scroll
        from ui.tab_editor import TabEditorPanel
        self._tab_editor = TabEditorPanel(self._theme)
        self._tab_editor.changed.connect(self._on_tab_changed)
        self._tab_editor.seek_requested.connect(lambda p: self._seek(p, user_initiated=True))
        self._tab_editor.zoom_scroll_changed.connect(self._on_zoom_scroll)
        timeline_lay.addWidget(self._tab_editor)

        # horizontal scrollbar at the very bottom, aligned under the waveform
        # area only (indented past the lane-head/gutter column).
        self._waveform_scrollbar = WaveformScrollBar(self._theme)
        self._waveform_scrollbar.scrolled.connect(self._on_scrollbar_scrolled)
        sb_row = QHBoxLayout()
        sb_row.setContentsMargins(0, 0, 0, 0)
        sb_row.setSpacing(0)
        sb_row.addSpacing(Ruler.GUTTER_W)          # match the lane-head width
        sb_row.addWidget(self._waveform_scrollbar, 1)
        timeline_lay.addLayout(sb_row)

        timeline_outer.addWidget(lanes_area, 1)

        # right: loop list panel
        self._loop_list = LoopListPanel(self._theme)
        self._loop_list.loop_activated.connect(self._restore_saved_loop)
        self._loop_list.loop_deleted.connect(self.loop_delete_requested)
        timeline_outer.addWidget(self._loop_list)

        root.addWidget(self._timeline, 1)

        # transport
        self._transport = TransportBar(1, self._theme)
        self._transport.play_pause.connect(self._toggle_play)
        self._transport.stop.connect(self._stop)
        self._transport.restart.connect(lambda: self._seek(0.0))
        self._transport.loop_clicked.connect(self._on_loop_button)
        self._transport.save_loop.connect(self._on_save_loop)
        self._transport.master_changed.connect(self._on_master_changed)
        self._transport.tempo_changed.connect(self._on_tempo_changed)
        self._transport.pitch_changed.connect(self._on_pitch_changed)
        root.addWidget(self._transport)

    # ------------------------------------------------------------------
    # Saved loops
    # ------------------------------------------------------------------

    def _on_save_loop(self):
        from core.project import SavedLoop
        from ui.player_panel import _fmt_ms  # already in scope

        # Build auto-generated name: times + audible stems
        start_ms = int(self._loop_start_ms)
        end_ms   = int(self._loop_end_ms)
        active = [sid for sid in STEM_IDS
                  if not self._mutes.get(sid, False) and not (
                      any(self._solos.values()) and not self._solos.get(sid, False))]
        all_active = len(active) == len(STEM_IDS)
        stems_part = "" if all_active else " · " + ", ".join(
            lbl for sid, lbl in zip(STEM_IDS, STEM_LABELS) if sid in active
        )
        default_name = f"{_fmt_ms(start_ms)} – {_fmt_ms(end_ms)}{stems_part}"

        dlg = SaveLoopDialog(default_name, self._theme, self)
        dlg.saved.connect(lambda name: self._commit_save_loop(name, start_ms, end_ms, active))
        dlg.exec()

    def _commit_save_loop(self, name: str, start_ms: int, end_ms: int, active_stems: list):
        from core.project import SavedLoop
        lp = SavedLoop(name=name, start_ms=start_ms, end_ms=end_ms, active_stems=active_stems)
        self.loop_save_requested.emit(lp)

    def set_loops(self, loops: list):
        """Called by MainWindow after the manifest is updated on disk."""
        self._loop_list.set_loops(loops)

    def _restore_saved_loop(self, lp):
        """Activate a saved loop: set region, restore mute state, start playing."""
        # Set loop region
        self._loop_start_ms = float(lp.start_ms)
        self._loop_end_ms   = float(lp.end_ms)
        self._loop_state    = 2
        self._transport.set_loop_state(2)
        self._update_loop_display()

        # Restore stem audibility
        for sid in STEM_IDS:
            should_be_active = sid in lp.active_stems
            self._mutes[sid] = not should_be_active
            self._solos[sid] = False
            if self._player:
                self._player.set_mute(sid, not should_be_active)
            if sid in self._lanes:
                self._lanes[sid].set_audible(should_be_active)

        # Seek to loop start and play
        self._seek(self._loop_start_ms / max(1, self._duration))
        if not self._playing:
            self._toggle_play()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_song(self, song: dict, player=None):
        """Load a song dict (from library) into the player panel."""
        self._song = song
        self._player = player
        if player is not None:
            from PySide6.QtCore import QMetaObject, Qt
            player.on_stretch_started = lambda: QMetaObject.invokeMethod(
                self._transport, "show_speed_busy", Qt.ConnectionType.QueuedConnection)
            player.on_stretch_done = lambda: QMetaObject.invokeMethod(
                self._transport, "hide_speed_busy", Qt.ConnectionType.QueuedConnection)
        # `or` (not .get default) so a manifest storing 0 can't divide-by-zero in the tick loop
        self._duration = song.get("durationMs") or 180_000
        self._time_ms = 0.0
        self._playing = False
        self._tick_timer.stop()
        self._mutes = {s: False for s in STEM_IDS}
        self._solos = {s: False for s in STEM_IDS}
        self._volumes = {s: 100 for s in STEM_IDS}
        self._stem_labels = {s: STEM_LABELS[i] for i, s in enumerate(STEM_IDS)}

        # An embedded original mix shows as an extra lane, muted by default.
        self._has_original = bool(player is not None and "original" in player.stem_ids())
        if self._has_original:
            self._mutes["original"] = True
            self._solos["original"] = False
            self._volumes["original"] = 100
            self._stem_labels["original"] = "Original"
        self._loop_state    = 0
        self._loop_start_ms = -1.0
        self._loop_end_ms   = -1.0
        self._transport.set_loop_state(0)
        self._pitch_semitones = 0
        self._transport.set_pitch_display(0)

        self._title_lbl.setText(song.get("title", ""))
        self._artist_lbl.setText(song.get("artist", ""))
        self._art.update_song(song["grad"][0], song["grad"][1], song.get("seed", 1))
        # Real cover art (embedded in .stems → disk cache), same source as the library
        self._art.set_cover(_cover_bytes(song))

        source_url = song.get("source_url", "")
        if source_url:
            self._yt_link_lbl.setText(f'▶ <a href="{source_url}" style="color: #FF5A5F;">Open on YouTube</a>')
            self._yt_link_lbl.show()
        else:
            self._yt_link_lbl.hide()

        # File size chip
        from core.library_stats import fmt_size as _fmt_size
        file_size = song.get("file_size", 0)
        self._filesize_val_lbl.setText(_fmt_size(file_size) if file_size else "—")

        self._ruler.set_duration(self._duration)
        self._transport.set_duration(self._duration)
        self._transport.set_playing(False)
        self._transport.set_time(0)

        self._zoom = 1.0
        self._scroll_frac = 0.0
        self._auto_center = False
        self._ruler.set_zoom_scroll(1.0, 0.0)
        loops = song.get("loops", [])
        self._loop_list.set_loops(loops)

        # Tab editor: feed duration, available lanes (excl. original) and saved tabs
        self._tab_editor.set_duration(self._duration)
        self._tab_editor.set_lane_ids([s for s in STEM_IDS])
        self._tab_editor.set_tabs(list(song.get("tabs", [])))

        # Use real waveform data from player if available, else procedural fallback
        waveforms = {}
        if player is not None:
            for sid in STEM_IDS:
                waveforms[sid] = player.waveform_data(sid, 4000)
            if self._has_original:
                waveforms["original"] = player.waveform_data("original", 4000)

        self._rebuild_lanes(song, waveforms)

    def _connect_lane(self, lane: "StemLane"):
        lane.mute_toggled.connect(self._on_mute)
        lane.solo_toggled.connect(self._on_solo)
        lane.volume_changed.connect(self._on_volume)
        lane.seek_requested.connect(lambda p: self._seek(p, user_initiated=True))
        lane.loop_set.connect(self._set_loop_from_fracs)
        lane.loop_cleared.connect(self._clear_loop)
        lane.handle_moved.connect(self._on_handle_moved)
        lane.zoom_scroll_changed.connect(self._on_zoom_scroll)
        lane.add_tab_requested.connect(self._on_add_tab)

    def _reset_lane_heights(self):
        n = self._lanes_splitter.count()
        if n:
            self._lanes_splitter.setSizes([1_000_000] * n)

    def _rebuild_lanes(self, song: dict, waveforms: dict | None = None):
        # remove old lanes from the splitter
        for lane in self._lanes.values():
            lane.setParent(None)
            lane.deleteLater()
        self._lanes.clear()

        seed = song.get("seed", 1)
        colors = [self._theme.stem_color(s) for s in STEM_IDS]

        for sid, slbl, col in zip(STEM_IDS, STEM_LABELS, colors):
            # Real waveform data if available, else procedural fallback
            wdata = (waveforms or {}).get(sid) or gen_waveform(seed, sid, 4000)
            lane = StemLane(sid, slbl, col, wdata, self._theme)
            self._connect_lane(lane)
            lane.label_changed.connect(self._on_stem_label_committed)
            self._lanes_splitter.addWidget(lane)
            self._lanes[sid] = lane
            self._stem_labels[sid] = slbl

        # Original mix lane — below the stems, black, muted. The splitter handle
        # above it provides the divider; a thicker top border sets it apart.
        if getattr(self, "_has_original", False):
            black = "#111111" if not self._theme.dark else "#E8E8E8"
            wdata = (waveforms or {}).get("original") or gen_waveform(seed, "other", 4000)
            lane = StemLane("original", "Original", black, wdata, self._theme)
            lane.setStyleSheet(
                f"QFrame {{ border-top: 2px solid {self._theme.border_strong}; "
                f"border-bottom: 1px solid {self._theme.border}; background: transparent; }}")
            self._connect_lane(lane)   # no label_changed — 'Original' isn't renamable
            self._lanes_splitter.addWidget(lane)
            self._lanes["original"] = lane
            lane.reflect_muted(True)

        # Distribute the available height equally so lanes fill the area.
        n = self._lanes_splitter.count()
        if n:
            self._lanes_splitter.setSizes([1_000_000] * n)

    # ------------------------------------------------------------------
    # Playback
    # ------------------------------------------------------------------

    def _toggle_play(self):
        if self._player:
            if self._playing:
                self._player.pause()
            else:
                if self._time_ms >= self._duration:
                    self._seek(0.0)
                self._player.play()
        self._playing = not self._playing
        self._transport.set_playing(self._playing)
        if self._playing:
            self._auto_center = True   # re-enable centering when playback starts
            self._disp_ms = self._time_ms   # start the smoothed clock in sync
            self._tick_timer.start()
        else:
            self._tick_timer.stop()

    def _stop(self):
        if self._player:
            self._player.stop()
        self._playing = False
        self._time_ms = 0.0
        self._disp_ms = 0.0
        self._transport.set_playing(False)
        self._transport.set_time(0)
        self._tick_timer.stop()
        self._update_progress(0.0)

    def _seek(self, progress: float, user_initiated: bool = False):
        self._time_ms = progress * self._duration
        self._disp_ms = self._time_ms     # snap the smoothed clock, no glide
        if user_initiated:
            self._auto_center = False
        if self._player:
            self._player.seek(self._time_ms)
        self._transport.set_time(int(self._time_ms))
        self._update_progress(progress)

    def _on_tick(self):
        if self._player:
            self._time_ms = self._player.position_ms()
        else:
            self._time_ms += 16

        # Loop-back when active (use the real position, not the smoothed one)
        if self._loop_state == 2 and self._loop_end_ms > 0:
            if self._time_ms >= self._loop_end_ms:
                self._seek(self._loop_start_ms / self._duration)
                return

        if self._time_ms >= self._duration:
            self._time_ms = self._duration
            self._playing = False
            self._transport.set_playing(False)
            self._tick_timer.stop()

        # Ease the display clock toward the real position. Snap if we're far off
        # (a big jump from a seek/loop) so it doesn't visibly glide across.
        if abs(self._time_ms - self._disp_ms) > 400:
            self._disp_ms = self._time_ms
        else:
            self._disp_ms += (self._time_ms - self._disp_ms) * 0.25

        self._transport.set_time(int(self._disp_ms))
        self._update_progress(self._disp_ms / self._duration)
        if self._loop_state == 1:
            self._update_loop_display()
        if self._auto_center:
            self._center_playhead()

    def _update_progress(self, p: float):
        for lane in self._lanes.values():
            lane.set_progress(p)
        self._tab_editor.set_progress(p)

    # ------------------------------------------------------------------
    # Loop state machine
    # ------------------------------------------------------------------

    def _on_loop_button(self):
        if self._loop_state == 0:
            self._loop_start_ms = self._time_ms
            self._loop_state = 1
            self._transport.set_loop_state(1)
            self._update_loop_display()
        elif self._loop_state == 1:
            end = self._time_ms
            if end > self._loop_start_ms + 100:
                self._loop_end_ms = end
                self._loop_state = 2
                self._transport.set_loop_state(2)
                self._update_loop_display()
        else:
            self._clear_loop()

    def _clear_loop(self):
        self._loop_start_ms = -1.0
        self._loop_end_ms   = -1.0
        self._loop_state    = 0
        self._transport.set_loop_state(0)
        self._update_loop_display()

    def _set_loop_from_fracs(self, start_frac: float, end_frac: float):
        """Called when the user drags a selection on a waveform."""
        self._loop_start_ms = start_frac * self._duration
        self._loop_end_ms   = end_frac   * self._duration
        self._loop_state    = 2
        self._transport.set_loop_state(2)
        self._update_loop_display()

    def _on_handle_moved(self, which: str, frac: float):
        if which == "start":
            self._loop_start_ms = frac * self._duration
        else:
            self._loop_end_ms = frac * self._duration
        # keep state=2 and broadcast new fracs to all other lanes
        self._update_loop_display()

    def _update_loop_display(self):
        if self._duration <= 0:
            return
        if self._loop_state >= 1:
            start_frac = self._loop_start_ms / self._duration
        else:
            start_frac = -1.0
        if self._loop_state == 2:
            end_frac         = self._loop_end_ms / self._duration
            end_placeholder  = False
        elif self._loop_state == 1:
            end_frac         = self._time_ms / self._duration   # tracks playhead
            end_placeholder  = True
        else:
            end_frac         = -1.0
            end_placeholder  = False
        for lane in self._lanes.values():
            lane.set_loop_region(start_frac, end_frac, end_placeholder)

        # If the loop is fully defined and the playhead is outside it, jump to start
        if self._loop_state == 2:
            if self._time_ms < self._loop_start_ms or self._time_ms >= self._loop_end_ms:
                self._seek(self._loop_start_ms / self._duration)

    # ------------------------------------------------------------------
    # Zoom / scroll
    # ------------------------------------------------------------------

    def _on_zoom_scroll(self, zoom: float, scroll_frac: float):
        self._zoom = zoom
        self._scroll_frac = scroll_frac
        self._auto_center = False   # user zoomed manually — don't override scroll
        self._apply_zoom_scroll()

    def _on_scrollbar_scrolled(self, scroll_frac: float):
        self._scroll_frac = scroll_frac
        self._auto_center = False
        self._apply_zoom_scroll()

    def _apply_zoom_scroll(self):
        for lane in self._lanes.values():
            lane.set_zoom_scroll(self._zoom, self._scroll_frac)
        self._waveform_scrollbar.set_zoom_scroll(self._zoom, self._scroll_frac)
        self._ruler.set_zoom_scroll(self._zoom, self._scroll_frac)
        self._tab_editor.set_zoom_scroll(self._zoom, self._scroll_frac)

    def _on_toggle_tab(self, on: bool):
        self._tab_editor.setVisible(on)

    def _on_add_tab(self, stem_id: str):
        from ui.tab_editor import TabSetupDialog
        label = self._stem_labels.get(stem_id, stem_id.capitalize())
        default_strings = 4 if stem_id == "bass" else 6
        dlg = TabSetupDialog(label, default_strings, self._theme, self)
        if dlg.exec():
            name, strings, ts_num, ts_den = dlg.values()
            self._tab_editor.create_tab(stem_id, name, strings, ts_num, ts_den)
            self._tab_btn.setChecked(True)   # reveal the editor (also fires toggle)
            self._tab_editor.setVisible(True)

    def _on_tab_changed(self):
        self.tab_changed.emit(self._tab_editor.tabs())

    def _center_playhead(self):
        """Scroll so the playhead sits in the middle of the visible window."""
        if self._zoom <= 1.0 or self._duration <= 0:
            return
        progress = self._disp_ms / self._duration
        vis = 1.0 / self._zoom          # visible fraction of total
        ideal = max(0.0, min(1.0, (progress - vis / 2) / max(1e-9, 1.0 - vis)))
        if abs(ideal - self._scroll_frac) < 1e-4:
            return                      # already there — skip a redundant repaint
        self._scroll_frac = ideal
        self._apply_zoom_scroll()

    # ------------------------------------------------------------------
    # Mixer controls
    # ------------------------------------------------------------------

    def _on_mute(self, stem_id: str, muted: bool):
        self._mutes[stem_id] = muted
        self._refresh_audibility()
        if self._player:
            self._player.set_mute(stem_id, muted)

    def _on_solo(self, stem_id: str, soloed: bool):
        self._solos[stem_id] = soloed
        self._refresh_audibility()
        if self._player:
            any_solo = any(self._solos.values())
            for sid in self._lanes:
                audible = (self._solos[sid] if any_solo else not self._mutes[sid])
                self._player.set_mute(sid, not audible)

    def _refresh_audibility(self):
        any_solo = any(self._solos.values())
        for sid, lane in self._lanes.items():
            audible = (self._solos[sid] if any_solo else not self._mutes[sid])
            lane.set_audible(audible)

    def _on_volume(self, stem_id: str, volume: int):
        self._volumes[stem_id] = volume
        if self._player:
            self._player.set_volume(stem_id, volume / 100.0)

    def _on_master_changed(self, v: int):
        if self._player:
            self._player.set_master_volume(v / 100.0)

    def _on_tempo_changed(self, rate: float):
        self._tempo_rate = rate
        if self._player:
            self._player.set_tempo(rate)

    def _on_pitch_changed(self, semitones: int):
        self._pitch_semitones = semitones
        if self._player:
            self._player.set_pitch(semitones)

    # ------------------------------------------------------------------
    # Metadata dialog
    # ------------------------------------------------------------------

    def _on_title_committed(self, value: str):
        self._apply_meta({"title": value, "artist": self._artist_lbl.text(),
                          "stem_labels": dict(self._stem_labels)})

    def _on_artist_committed(self, value: str):
        self._apply_meta({"title": self._title_lbl.text(), "artist": value,
                          "stem_labels": dict(self._stem_labels)})

    def _on_stem_label_committed(self, stem_id: str, value: str):
        self._apply_meta({"title": self._title_lbl.text(), "artist": self._artist_lbl.text(),
                          "stem_labels": {**self._stem_labels, stem_id: value}})

    def _open_meta_dialog(self):
        if not self._song:
            return
        colors = {s: self._theme.stem_color(s) for s in STEM_IDS}
        dlg = MetaDialog(
            {"title": self._title_lbl.text(), "artist": self._artist_lbl.text()},
            self._stem_labels, colors, self._theme, self
        )
        dlg.saved.connect(self._apply_meta)
        dlg.exec()

    def _apply_meta(self, data: dict):
        self._title_lbl.setText(data["title"])
        self._artist_lbl.setText(data["artist"])
        for sid, lbl in data.get("stem_labels", {}).items():
            self._stem_labels[sid] = lbl
            if sid in self._lanes:
                self._lanes[sid].update_name(lbl)
        self.save_metadata.emit(data)

    def audio_player(self):
        """The live StemPlayer for the current song (or None)."""
        return self._player

    def _show_export_menu(self):
        if not self._song:
            return
        from PySide6.QtWidgets import QMenu
        from PySide6.QtGui import QAction
        from PySide6.QtCore import QPoint
        t = self._theme
        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{ background: {t.surface}; border: 1px solid {t.border};
                     border-radius: 4px; padding: 4px; }}
            QMenu::item {{ padding: 8px 18px 8px 12px; font-size: 13px;
                           color: {t.ink}; border-radius: 4px; }}
            QMenu::item:selected {{ background: {t.surface2}; }}
        """)
        for label, mode in [("All (.stems package)", "all"),
                            ("Current mix…", "current"),
                            ("Original audio…", "original"),
                            ("As template (.rrs)…", "template"),
                            ("Tabs to text/PDF…", "tabs")]:
            act = QAction(label, self)
            act.triggered.connect(lambda _=False, m=mode: self.export_clicked.emit(self._song, m))
            menu.addAction(act)
        pos = self._export_btn.mapToGlobal(self._export_btn.rect().bottomRight())
        menu.exec(QPoint(pos.x() - menu.sizeHint().width(), pos.y() + 4))

    # ------------------------------------------------------------------
    # Keyboard
    # ------------------------------------------------------------------

    def handle_footswitch(self, char: str) -> bool:
        """Handle a Vidami footswitch character. Returns True if consumed."""
        if char == 'K':
            self._toggle_play()
        elif char == '{':
            self._seek(max(0.0, (self._time_ms - 2000) / self._duration))
        elif char == '}':
            self._seek(min(1.0, (self._time_ms + 2000) / self._duration))
        elif char == '`':
            self._vidami_speed_down()
        elif char == ';':
            self._on_loop_button()
        else:
            return False
        return True

    def _vidami_speed_down(self):
        """Reduce speed by 10 %. At 50 % (minimum), next press resets to 100 %."""
        current = round(self._tempo_rate, 2)
        if current <= 0.50:
            new_rate = 1.0          # reset
        else:
            new_rate = max(0.50, current - 0.10)
        # Update via slider so the display stays in sync
        self._transport.set_speed(new_rate)
        self._on_tempo_changed(new_rate)

    def keyPressEvent(self, e):
        text = e.text()
        if text and self.handle_footswitch(text):
            return
        if e.key() == Qt.Key.Key_Space:
            self._toggle_play()
        elif e.key() == Qt.Key.Key_L:
            self._on_loop_button()
        elif e.key() == Qt.Key.Key_Left:
            self._seek(max(0.0, (self._time_ms - 5000) / self._duration))
        elif e.key() == Qt.Key.Key_Right:
            self._seek(min(1.0, (self._time_ms + 5000) / self._duration))
        else:
            super().keyPressEvent(e)

    def apply_theme(self, theme: Theme):
        self._theme = theme
