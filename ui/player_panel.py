"""Player/mixer panel — DAW-style stacked waveform lanes + transport bar.

Faithfully implements the design prototype (player.jsx) and adds:
- Playback speed slider (50–200 %, pitch-preserving) not in the original design.
"""

from __future__ import annotations
from typing import Optional

from PySide6.QtCore import Qt, Signal, QTimer, QRectF
from PySide6.QtGui import QColor, QPainter, QPainterPath, QLinearGradient
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QScrollArea, QSizePolicy, QDialog, QLineEdit, QSlider
)

from ui.theme import Theme, STEM_IDS, STEM_LABELS
from ui.widgets import WaveformWidget, ArtThumbnail, gen_waveform


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


# ---------------------------------------------------------------------------
# Ruler (time markers above the lanes)
# ---------------------------------------------------------------------------

class Ruler(QWidget):
    seek_requested = Signal(float)  # 0-1

    GUTTER_W = 244

    def __init__(self, duration_ms: int, theme: Theme, parent=None):
        super().__init__(parent)
        self._dur = max(1, duration_ms)
        self._theme = theme
        self.setFixedHeight(34)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_duration(self, ms: int):
        self._dur = max(1, ms)
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        w, h = self.width(), self.height()
        gw = self.GUTTER_W

        # gutter bg
        p.fillRect(0, 0, gw, h, QColor(self._theme.surface))
        p.fillRect(gw, 0, w - gw, h, QColor(self._theme.surface))

        # gutter label
        p.setPen(QColor(self._theme.ink3))
        f = p.font(); f.setPointSize(8); f.setBold(True); p.setFont(f)
        p.drawText(16, 0, gw - 16, h, Qt.AlignmentFlag.AlignVCenter, "STEMS")

        # gutter right border
        p.setPen(QColor(self._theme.border))
        p.drawLine(gw, 0, gw, h)

        # bottom border
        p.drawLine(0, h - 1, w, h - 1)

        # time ticks every 30 s
        track_w = w - gw
        interval = 30_000
        t = interval
        while t <= self._dur:
            x = gw + int(t / self._dur * track_w)
            p.setPen(QColor(self._theme.border))
            p.drawLine(x, 0, x, h)
            p.setPen(QColor(self._theme.ink3))
            f2 = p.font(); f2.setPointSize(8); f2.setFamily("Consolas"); p.setFont(f2)
            p.drawText(x + 5, 8, 60, 18, 0, _fmt_clock(t))
            t += interval
        p.end()

    def _to_progress(self, x: int) -> float:
        return max(0.0, min(1.0, (x - self.GUTTER_W) / max(1, self.width() - self.GUTTER_W)))

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
        if self.isChecked():
            if self._letter == "M":
                self.setStyleSheet(
                    "background: #FFE9C7; color: #B26B00; border: 1px solid #F2C887; "
                    "border-radius: 7px; font-size: 11px; font-weight: 700;"
                )
            else:
                self.setStyleSheet(
                    "background: rgba(46,107,255,0.12); color: #2E6BFF; "
                    "border: 1px solid rgba(46,107,255,0.4); border-radius: 7px; "
                    "font-size: 11px; font-weight: 700;"
                )
        else:
            self.setStyleSheet(
                "background: #F4F4F0; color: #93939C; border: 1px solid transparent; "
                "border-radius: 7px; font-size: 11px; font-weight: 700;"
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
        lay.addWidget(self._slider, 1)
        lay.addWidget(self._val_lbl)

    def _on_change(self, v: int):
        self._val_lbl.setText(f"{v}%")
        self.value_changed.emit(v)

    def value(self) -> int:
        return self._slider.value()


class StemLane(QFrame):
    mute_toggled   = Signal(str, bool)
    solo_toggled   = Signal(str, bool)
    volume_changed = Signal(str, int)
    seek_requested = Signal(float)
    loop_set       = Signal(float, float)   # start_frac, end_frac
    handle_moved   = Signal(str, float)     # "start"|"end", frac

    LANE_HEIGHT = 116

    def __init__(self, stem_id: str, label: str, color: str, wavedata: list,
                 theme: Theme, parent=None):
        super().__init__(parent)
        self._id = stem_id
        self._color = color
        self._theme = theme
        self.setFixedHeight(self.LANE_HEIGHT)
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
        head_lay.setContentsMargins(14, 11, 14, 11)
        head_lay.setSpacing(8)

        # top row: color bar + name + M/S buttons
        top_row = QHBoxLayout()
        top_row.setSpacing(8)
        bar = QFrame()
        bar.setFixedWidth(4)
        bar.setStyleSheet(f"background: {color}; border-radius: 2px;")
        name_col = QVBoxLayout()
        name_col.setSpacing(2)
        self._name_lbl = QLabel(label)
        self._name_lbl.setStyleSheet("font-size: 14px; font-weight: 600;")
        file_lbl = QLabel(f"{self._id}.flac")
        file_lbl.setStyleSheet(f"font-family: 'Consolas', monospace; font-size: 10px; color: {self._theme.ink3};")
        name_col.addWidget(self._name_lbl)
        name_col.addWidget(file_lbl)
        name_w = QWidget()
        name_w.setLayout(name_col)

        self._mute_btn = MSSButton("M")
        self._solo_btn = MSSButton("S")
        self._mute_btn.clicked.connect(lambda: self.mute_toggled.emit(self._id, self._mute_btn.isChecked()))
        self._solo_btn.clicked.connect(lambda: self.solo_toggled.emit(self._id, self._solo_btn.isChecked()))

        top_row.addWidget(bar)
        top_row.addWidget(name_w, 1)
        top_row.addWidget(self._mute_btn)
        top_row.addWidget(self._solo_btn)
        head_lay.addLayout(top_row)

        # fader row
        self._fader = FaderSlider(color)
        self._fader.value_changed.connect(lambda v: self.volume_changed.emit(self._id, v))
        head_lay.addWidget(self._fader)

        lay.addWidget(head)

        # --- waveform area ---
        self._wave = WaveformWidget()
        self._wave.set_data(wavedata, color)
        self._wave.seeked.connect(self.seek_requested)
        self._wave.loop_set.connect(self.loop_set)
        self._wave.handle_moved.connect(self.handle_moved)
        lay.addWidget(self._wave, 1)

    def set_progress(self, p: float):
        self._wave.set_progress(p)

    def set_loop_region(self, start: float, end: float):
        self._wave.set_loop_region(start, end)

    def set_audible(self, audible: bool):
        self._wave.set_muted(not audible)
        self._name_lbl.setStyleSheet(
            f"font-size: 14px; font-weight: 600; color: {'#93939C' if not audible else self._theme.ink};"
        )

    def update_name(self, name: str):
        self._name_lbl.setText(name)


# ---------------------------------------------------------------------------
# Transport bar
# ---------------------------------------------------------------------------

class TransportBar(QFrame):
    play_pause = Signal()
    stop = Signal()
    restart = Signal()
    loop_clicked = Signal()   # cycles through 3 states; PlayerPanel owns the state
    master_changed = Signal(int)
    tempo_changed = Signal(float)

    def __init__(self, duration_ms: int, theme: Theme, parent=None):
        super().__init__(parent)
        self._theme = theme
        self._duration = duration_ms
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
            f"QPushButton:hover {{ filter: brightness(1.14); }}"
        )
        self._play_btn.clicked.connect(self.play_pause)
        ctrl.addWidget(self._play_btn)

        stop_btn = self._tbtn("⏹", "Stop")
        stop_btn.clicked.connect(self.stop)
        ctrl.addWidget(stop_btn)

        self._loop_btn = self._tbtn("⊙", "Click to set loop start (L)")
        self._loop_btn.clicked.connect(self.loop_clicked)
        ctrl.addWidget(self._loop_btn)

        lay.addLayout(ctrl)

        # --- Speed slider (not in original design — added per plan) ---
        speed_group = QVBoxLayout()
        speed_group.setSpacing(3)
        speed_lbl = QLabel("SPEED")
        speed_lbl.setStyleSheet(
            f"font-size: 10px; font-weight: 600; letter-spacing: 0.1em; color: {self._theme.ink3};"
        )
        speed_row = QHBoxLayout()
        speed_row.setSpacing(6)
        self._speed_slider = QSlider(Qt.Orientation.Horizontal)
        self._speed_slider.setRange(50, 200)
        self._speed_slider.setValue(100)
        self._speed_slider.setFixedWidth(120)
        self._speed_slider.setStyleSheet(f"""
            QSlider::groove:horizontal {{
                height: 5px; background: {self._theme.surface3}; border-radius: 3px;
            }}
            QSlider::handle:horizontal {{
                width: 15px; height: 15px; background: {self._theme.surface};
                border: 2px solid {self._theme.border_strong}; border-radius: 8px; margin: -5px 0;
            }}
            QSlider::sub-page:horizontal {{
                background: {self._theme.accent}; border-radius: 3px;
            }}
        """)
        self._speed_val_lbl = QLabel("1.0×")
        self._speed_val_lbl.setStyleSheet(
            "font-family: 'Consolas', monospace; font-size: 12px;"
        )
        self._speed_val_lbl.setFixedWidth(34)
        self._speed_slider.valueChanged.connect(self._on_speed)
        self._speed_slider.mouseDoubleClickEvent = lambda e: self._reset_speed()
        self._speed_busy = QLabel("⟳")
        self._speed_busy.setStyleSheet(
            f"font-size: 13px; color: {self._theme.accent};"
        )
        self._speed_busy.setFixedWidth(16)
        self._speed_busy.hide()
        speed_row.addWidget(self._speed_slider)
        speed_row.addWidget(self._speed_val_lbl)
        speed_row.addWidget(self._speed_busy)
        speed_group.addWidget(speed_lbl)
        speed_group.addLayout(speed_row)
        lay.addLayout(speed_group)

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

    def _on_speed(self, v: int):
        rate = v / 100.0
        self._speed_val_lbl.setText(f"{rate:.1f}×")
        self.tempo_changed.emit(rate)

    def _reset_speed(self):
        self._speed_slider.setValue(100)

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

    def set_playing(self, playing: bool):
        self._play_btn.setText("⏸" if playing else "▶")

    def set_time(self, ms: int):
        self._time_lbl.setText(_fmt_ms(ms))

    def set_duration(self, ms: int):
        self._duration = ms
        self._total_lbl.setText(f" / {_fmt_ms(ms)}")


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
        self.setStyleSheet(f"QDialog {{ background: {theme.surface}; border-radius: 22px; }}")

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
    back_clicked = Signal()
    export_clicked = Signal(dict)    # song dict
    save_metadata = Signal(dict)     # updated metadata

    def __init__(self, theme: Theme, parent=None):
        super().__init__(parent)
        self._theme = theme
        self._song: Optional[dict] = None
        self._duration = 1
        self._playing = False
        self._time_ms = 0.0
        # Loop state: 0=none 1=start-set 2=active
        self._loop_state: int = 0
        self._loop_start_ms: float = -1.0
        self._loop_end_ms:   float = -1.0
        self._mutes: dict[str, bool] = {}
        self._solos: dict[str, bool] = {}
        self._volumes: dict[str, int] = {s: 100 for s in STEM_IDS}
        self._stem_labels: dict[str, str] = {}
        self._lanes: dict[str, StemLane] = {}
        self._player = None  # set externally after construction

        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(40)
        self._tick_timer.timeout.connect(self._on_tick)

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
        self._title_lbl = QLabel("—")
        self._title_lbl.setStyleSheet("font-size: 16px; font-weight: 600;")
        self._artist_lbl = QLabel("")
        self._artist_lbl.setStyleSheet(f"font-size: 12px; color: {self._theme.ink3};")
        meta_col.addWidget(self._title_lbl)
        meta_col.addWidget(self._artist_lbl)
        top_lay.addLayout(meta_col)
        top_lay.addStretch()

        # readout chips
        chips_row = QHBoxLayout()
        chips_row.setSpacing(16)
        for key, label_text in [("stems", "Stems"), ("model", "Model")]:
            col = QVBoxLayout()
            col.setSpacing(1)
            k_lbl = QLabel(label_text.upper())
            k_lbl.setStyleSheet(f"font-size: 10px; font-weight: 600; letter-spacing: 0.1em; color: {self._theme.ink3};")
            v_lbl = QLabel("4" if key == "stems" else "htdemucs")
            v_lbl.setStyleSheet("font-family: 'Consolas', monospace; font-size: 13px;")
            col.addWidget(k_lbl)
            col.addWidget(v_lbl)
            setattr(self, f"_{key}_val_lbl", v_lbl)
            w = QWidget()
            w.setLayout(col)
            chips_row.addWidget(w)
        top_lay.addLayout(chips_row)

        self._edit_btn = QPushButton("Edit")
        self._edit_btn.setProperty("role", "outline")
        self._edit_btn.setFixedHeight(34)
        self._edit_btn.clicked.connect(self._open_meta_dialog)
        top_lay.addWidget(self._edit_btn)

        self._export_btn = QPushButton("Export")
        self._export_btn.setProperty("role", "ghost")
        self._export_btn.setFixedHeight(34)
        self._export_btn.clicked.connect(self._on_export)
        top_lay.addWidget(self._export_btn)

        root.addWidget(self._topbar)

        # timeline (ruler + lanes)
        self._timeline = QWidget()
        timeline_lay = QVBoxLayout(self._timeline)
        timeline_lay.setContentsMargins(0, 0, 0, 0)
        timeline_lay.setSpacing(0)

        self._ruler = Ruler(1, self._theme)
        self._ruler.seek_requested.connect(self._seek)
        timeline_lay.addWidget(self._ruler)

        # lanes scroll area
        self._lanes_scroll = QScrollArea()
        self._lanes_scroll.setWidgetResizable(True)
        self._lanes_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._lanes_container = QWidget()
        self._lanes_lay = QVBoxLayout(self._lanes_container)
        self._lanes_lay.setContentsMargins(0, 0, 0, 0)
        self._lanes_lay.setSpacing(0)
        self._lanes_lay.addStretch()
        self._lanes_scroll.setWidget(self._lanes_container)
        timeline_lay.addWidget(self._lanes_scroll, 1)

        root.addWidget(self._timeline, 1)

        # transport
        self._transport = TransportBar(1, self._theme)
        self._transport.play_pause.connect(self._toggle_play)
        self._transport.stop.connect(self._stop)
        self._transport.restart.connect(lambda: self._seek(0.0))
        self._transport.loop_clicked.connect(self._on_loop_button)
        self._transport.master_changed.connect(self._on_master_changed)
        self._transport.tempo_changed.connect(self._on_tempo_changed)
        root.addWidget(self._transport)

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
                self._transport._speed_busy, "show", Qt.ConnectionType.QueuedConnection)
            player.on_stretch_done = lambda: QMetaObject.invokeMethod(
                self._transport._speed_busy, "hide", Qt.ConnectionType.QueuedConnection)
        self._duration = song.get("durationMs", 180_000)
        self._time_ms = 0.0
        self._playing = False
        self._tick_timer.stop()
        self._mutes = {s: False for s in STEM_IDS}
        self._solos = {s: False for s in STEM_IDS}
        self._volumes = {s: 100 for s in STEM_IDS}
        self._stem_labels = {s: STEM_LABELS[i] for i, s in enumerate(STEM_IDS)}

        self._title_lbl.setText(song.get("title", ""))
        self._artist_lbl.setText(song.get("artist", ""))
        self._art.update_song(song["grad"][0], song["grad"][1], song.get("seed", 1))

        self._ruler.set_duration(self._duration)
        self._transport.set_duration(self._duration)
        self._transport.set_playing(False)
        self._transport.set_time(0)

        # Use real waveform data from player if available, else procedural fallback
        waveforms = {}
        if player is not None:
            for sid in STEM_IDS:
                waveforms[sid] = player.waveform_data(sid, 320)

        self._rebuild_lanes(song, waveforms)

    def _rebuild_lanes(self, song: dict, waveforms: dict | None = None):
        # remove old lanes
        for lane in self._lanes.values():
            self._lanes_lay.removeWidget(lane)
            lane.deleteLater()
        self._lanes.clear()

        seed = song.get("seed", 1)
        colors = [self._theme.stem_color(s) for s in STEM_IDS]

        for sid, slbl, col in zip(STEM_IDS, STEM_LABELS, colors):
            # Real waveform data if available, else procedural fallback
            wdata = (waveforms or {}).get(sid) or gen_waveform(seed, sid, 320)
            lane = StemLane(sid, slbl, col, wdata, self._theme)
            lane.mute_toggled.connect(self._on_mute)
            lane.solo_toggled.connect(self._on_solo)
            lane.volume_changed.connect(self._on_volume)
            lane.seek_requested.connect(self._seek)
            lane.loop_set.connect(self._set_loop_from_fracs)
            lane.handle_moved.connect(self._on_handle_moved)
            self._lanes_lay.insertWidget(self._lanes_lay.count() - 1, lane)
            self._lanes[sid] = lane
            self._stem_labels[sid] = slbl

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
            self._tick_timer.start()
        else:
            self._tick_timer.stop()

    def _stop(self):
        if self._player:
            self._player.stop()
        self._playing = False
        self._time_ms = 0.0
        self._transport.set_playing(False)
        self._transport.set_time(0)
        self._tick_timer.stop()
        self._update_progress(0.0)

    def _seek(self, progress: float):
        self._time_ms = progress * self._duration
        if self._player:
            self._player.seek(self._time_ms)
        self._transport.set_time(int(self._time_ms))
        self._update_progress(progress)

    def _on_tick(self):
        if self._player:
            self._time_ms = self._player.position_ms()
        else:
            self._time_ms += 40

        # Loop-back when active
        if self._loop_state == 2 and self._loop_end_ms > 0:
            if self._time_ms >= self._loop_end_ms:
                self._seek(self._loop_start_ms / self._duration)
                return

        if self._time_ms >= self._duration:
            self._time_ms = self._duration
            self._playing = False
            self._transport.set_playing(False)
            self._tick_timer.stop()

        self._transport.set_time(int(self._time_ms))
        self._update_progress(self._time_ms / self._duration)

    def _update_progress(self, p: float):
        for lane in self._lanes.values():
            lane.set_progress(p)

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
            end_frac = self._loop_end_ms / self._duration
        else:
            end_frac = -1.0
        for lane in self._lanes.values():
            lane.set_loop_region(start_frac, end_frac)

        # If the loop is fully defined and the playhead is outside it, jump to start
        if self._loop_state == 2:
            if self._time_ms < self._loop_start_ms or self._time_ms >= self._loop_end_ms:
                self._seek(self._loop_start_ms / self._duration)

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
            for sid in STEM_IDS:
                any_solo = any(self._solos.values())
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
        if self._player:
            self._player.set_tempo(rate)

    # ------------------------------------------------------------------
    # Metadata dialog
    # ------------------------------------------------------------------

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

    def _on_export(self):
        if self._song:
            self.export_clicked.emit(self._song)

    # ------------------------------------------------------------------
    # Keyboard
    # ------------------------------------------------------------------

    def keyPressEvent(self, e):
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
