"""Library panel — song list with artwork, stems chips, duration, date."""

from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QFrame, QLineEdit, QSizePolicy
)

from core.library_stats import fmt_size as _fmt_size
from ui.theme import Theme, STEM_IDS
from ui.widgets import ArtThumbnail


class StemChips(QWidget):
    def __init__(self, stem_colors: list, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(5)
        for c in stem_colors:
            dot = QFrame()
            dot.setFixedSize(9, 9)
            dot.setStyleSheet(f"background:{c}; border-radius:4px;")
            lay.addWidget(dot)
        lbl = QLabel(f"{len(stem_colors)} stems")
        lbl.setStyleSheet("font-family: 'Consolas', monospace; font-size: 12px; color: inherit;")
        lay.addWidget(lbl)
        lay.addStretch()


class SongRow(QFrame):
    clicked = Signal(dict)

    def __init__(self, song: dict, theme: Theme, parent=None):
        super().__init__(parent)
        self._song = song
        self._theme = theme
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._setup_ui()
        self.apply_theme(theme)

    def _setup_ui(self):
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(16)

        s = self._song
        self._art = ArtThumbnail(s["grad"][0], s["grad"][1], s.get("seed", 1), 44)
        lay.addWidget(self._art)

        # title + artist
        info = QVBoxLayout()
        info.setSpacing(2)
        self._title_lbl = QLabel(s["title"])
        self._title_lbl.setStyleSheet("font-size: 14px; font-weight: 600;")
        self._artist_lbl = QLabel(s["artist"])
        self._artist_lbl.setStyleSheet("font-size: 12px;")
        info.addWidget(self._title_lbl)
        info.addWidget(self._artist_lbl)
        info_w = QWidget()
        info_w.setLayout(info)
        info_w.setMinimumWidth(160)
        lay.addWidget(info_w, 1)

        # stems chips
        chip_w = StemChips([
            "#FF5A5F", "#F2A23A", "#7C5CFF", "#15B6A4"
        ])
        chip_w.setFixedWidth(150)
        lay.addWidget(chip_w)

        # duration
        ms = s.get("durationMs", 0)
        secs = ms // 1000
        dur_str = f"{secs // 60}:{secs % 60:02d}"
        dur_lbl = QLabel(dur_str)
        dur_lbl.setStyleSheet("font-family: 'Consolas', monospace; font-size: 13px;")
        dur_lbl.setFixedWidth(48)
        lay.addWidget(dur_lbl)

        # file size
        size_lbl = QLabel(_fmt_size(s.get("file_size", 0)))
        size_lbl.setStyleSheet(f"font-size: 12px; color: {theme.ink3};")
        size_lbl.setFixedWidth(60)
        lay.addWidget(size_lbl)

        # added
        added_lbl = QLabel(s.get("addedLabel", ""))
        added_lbl.setStyleSheet("font-size: 12px;")
        added_lbl.setFixedWidth(88)
        lay.addWidget(added_lbl)

    def apply_theme(self, theme: Theme):
        self._theme = theme
        self._artist_lbl.setStyleSheet(f"font-size: 12px; color: {theme.ink3};")
        self._hover_bg = theme.surface2
        self._normal_bg = "transparent"
        self.setStyleSheet(f"""
            SongRow {{ background: transparent; border-radius: 10px; }}
            SongRow:hover {{ background: {theme.surface2}; }}
        """)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._song)

    def enterEvent(self, e):
        self.setStyleSheet(f"background: {self._theme.surface2}; border-radius: 10px;")

    def leaveEvent(self, e):
        self.setStyleSheet("background: transparent; border-radius: 10px;")


class LibraryPanel(QWidget):
    song_opened = Signal(dict)
    import_requested = Signal()

    def __init__(self, theme: Theme, parent=None):
        super().__init__(parent)
        self._theme = theme
        self._songs: list[dict] = []
        self._rows: list[SongRow] = []
        self._setup_ui()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # scroll area
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)

        self._content = QWidget()
        self._content_lay = QVBoxLayout(self._content)
        self._content_lay.setContentsMargins(28, 26, 28, 60)
        self._content_lay.setSpacing(0)

        # header row
        self._head_lbl = QLabel("All tracks")
        self._head_lbl.setStyleSheet("font-size: 15px; font-weight: 600;")
        self._meta_lbl = QLabel("")
        self._meta_lbl.setStyleSheet("font-family: 'Consolas', monospace; font-size: 13px;")
        head_row = QHBoxLayout()
        head_row.setContentsMargins(0, 0, 0, 14)
        head_row.addWidget(self._head_lbl)
        head_row.addWidget(self._meta_lbl)
        head_row.addStretch()
        self._content_lay.addLayout(head_row)

        # column headers
        col_head = QHBoxLayout()
        col_head.setContentsMargins(12, 0, 12, 0)
        for (text, stretch, fixed) in [
            ("", 0, 44), ("Track", 1, 0), ("Stems", 0, 150),
            ("Length", 0, 60), ("Added", 0, 100),
        ]:
            lbl = QLabel(text)
            lbl.setStyleSheet("font-size: 11px; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase;")
            if fixed:
                lbl.setFixedWidth(fixed)
            if stretch:
                col_head.addWidget(lbl, stretch)
            else:
                col_head.addWidget(lbl)
        col_head.addSpacing(28)  # menu button column

        col_head_w = QWidget()
        col_head_w.setLayout(col_head)
        col_head_w.setFixedHeight(36)
        self._col_head_w = col_head_w
        self._content_lay.addWidget(col_head_w)

        # separator line
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        self._sep = sep
        self._content_lay.addWidget(sep)

        # rows container
        self._rows_w = QWidget()
        self._rows_lay = QVBoxLayout(self._rows_w)
        self._rows_lay.setContentsMargins(0, 4, 0, 0)
        self._rows_lay.setSpacing(0)
        self._content_lay.addWidget(self._rows_w)
        self._content_lay.addStretch()

        # empty state
        self._empty = QWidget()
        emp_lay = QVBoxLayout(self._empty)
        emp_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        emp_lbl = QLabel("Your library is empty")
        emp_lbl.setStyleSheet("font-size: 17px; font-weight: 600;")
        emp_sub = QLabel("Import a song from a file or a YouTube link to split it into stems.")
        emp_sub.setStyleSheet("font-size: 13px;")
        emp_sub.setWordWrap(True)
        emp_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        emp_btn = QPushButton("+ Import a track")
        emp_btn.setProperty("role", "primary")
        emp_btn.setFixedWidth(160)
        emp_btn.clicked.connect(self.import_requested)
        emp_lay.addWidget(emp_lbl, 0, Qt.AlignmentFlag.AlignHCenter)
        emp_lay.addWidget(emp_sub, 0, Qt.AlignmentFlag.AlignHCenter)
        emp_lay.addWidget(emp_btn, 0, Qt.AlignmentFlag.AlignHCenter)
        self._empty.hide()
        self._content_lay.addWidget(self._empty, 0, Qt.AlignmentFlag.AlignCenter)

        self._scroll.setWidget(self._content)
        root.addWidget(self._scroll)

    def set_songs(self, songs: list[dict]):
        self._songs = songs
        self._rebuild_rows(songs)

    def filter(self, query: str):
        q = query.lower()
        filtered = [s for s in self._songs if not q or
                    q in s["title"].lower() or q in s.get("artist", "").lower()]
        self._rebuild_rows(filtered)

    def _rebuild_rows(self, songs: list[dict]):
        # clear existing rows
        for row in self._rows:
            self._rows_lay.removeWidget(row)
            row.deleteLater()
        self._rows.clear()

        self._meta_lbl.setText(f"{len(songs)} {'track' if len(songs) == 1 else 'tracks'}")

        if not songs:
            self._col_head_w.hide()
            self._sep.hide()
            self._empty.show()
            return

        self._col_head_w.show()
        self._sep.show()
        self._empty.hide()

        for song in songs:
            row = SongRow(song, self._theme)
            row.clicked.connect(self.song_opened)
            self._rows_lay.addWidget(row)
            self._rows.append(row)

    def apply_theme(self, theme: Theme):
        self._theme = theme
        self._meta_lbl.setStyleSheet(f"font-family: 'Consolas', monospace; font-size: 13px; color: {theme.ink3};")
        self._sep.setStyleSheet(f"color: {theme.border};")
        for col_lbl in self._col_head_w.findChildren(QLabel):
            col_lbl.setStyleSheet(
                f"font-size: 11px; font-weight: 600; letter-spacing: 0.08em; color: {theme.ink3};")
        for row in self._rows:
            row.apply_theme(theme)
