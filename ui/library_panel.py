"""Library panel — song list with artwork, sortable columns."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QThread
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QFrame, QSizePolicy, QMenu
)
from PySide6.QtGui import QAction, QPixmap

import time as _time
from pathlib import Path

from core.library_stats import fmt_size as _fmt_size
from core.project import read_cover
from core import artwork
from ui.theme import Theme
from ui.widgets import ArtThumbnail, Spinner


# Cover-art pixmap cache, keyed by .stems path. Value None = "no art, resolved".
_PIX_CACHE: dict[str, "QPixmap | None"] = {}
# Tracks we've already queued for an online backfill this session.
_ATTEMPTED: set[str] = set()


def _load_cover_pixmap(song: dict) -> "QPixmap | None":
    """Resolve a cover QPixmap for a song: embedded in .stems → disk cache.

    Cached in-process so repeated row rebuilds don't re-read the zip.
    """
    sp = song.get("stems_path")
    if not sp:
        return None
    if sp in _PIX_CACHE:
        return _PIX_CACHE[sp]
    data = None
    try:
        data = read_cover(Path(sp))
    except Exception:
        data = None
    if not data:
        data = artwork.cached_cover(song.get("artist", ""), song.get("title", ""))
    pm = None
    if data:
        cand = QPixmap()
        if cand.loadFromData(data) and not cand.isNull():
            pm = cand
    _PIX_CACHE[sp] = pm
    return pm


class _CoverBackfillWorker(QThread):
    """Looks up missing covers online (iTunes) one at a time, politely."""
    fetched = Signal(str, bytes)   # stems_path, image bytes

    def __init__(self, jobs: list[tuple[str, str, str]], parent=None):
        super().__init__(parent)
        self._jobs = jobs          # (stems_path, artist, title)

    def run(self):
        for sp, artist, title in self._jobs:
            if self.isInterruptionRequested():
                return
            data = artwork.itunes_cover(artist, title)
            if data:
                artwork.store_cached(artist, title, data)
                self.fetched.emit(sp, data)
            self.msleep(300)   # rate-limit the API


def _fmt_viewed(ts: float | None) -> str:
    """Format a unix timestamp as a human-readable 'last played' string."""
    if not ts:
        return "Never"
    age = _time.time() - ts
    if age < 60:
        return "Just now"
    if age < 3600:
        mins = int(age / 60)
        return f"{mins} min{'s' if mins != 1 else ''} ago"
    if age < 86400:
        hrs = int(age / 3600)
        return f"{hrs} hr{'s' if hrs != 1 else ''} ago"
    days = int(age / 86400)
    if days == 1:
        return "Yesterday"
    if days < 7:
        return f"{days} days ago"
    if days < 14:
        return "Last week"
    if days < 30:
        return f"{int(days / 7)} weeks ago"
    return f"{int(days / 30)} months ago"


# ── column definitions ────────────────────────────────────────────────────────
# (key, header label, fixed_width or 0 for stretch, sort key fn)
_COLS = [
    ("title",       "Track",       0,    lambda s: (s.get("title",  "") or "").lower()),
    ("artist",      "Artist",      0,    lambda s: (s.get("artist", "") or "").lower()),
    ("duration",    "Length",      64,   lambda s: s.get("durationMs", 0)),
    ("size",        "Size",        72,   lambda s: s.get("file_size", 0)),
    ("last_viewed", "Last played", 104,  lambda s: -(s.get("last_viewed") or 0)),
    ("added",       "Added",       96,   lambda s: -s.get("_mtime", 0)),
]
_DEFAULT_SORT = "title"
_DEFAULT_ASC  = True


class _HeaderLabel(QLabel):
    """A column-header label that emits `clicked` when pressed."""
    clicked = Signal(str)   # column key

    def __init__(self, key: str, text: str, theme: Theme, parent=None):
        super().__init__(text, parent)
        self._key = key
        self._theme = theme
        self._sort_dir: str | None = None   # None / "asc" / "desc"
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._refresh()

    def set_sort(self, direction: str | None):
        self._sort_dir = direction
        self._refresh()

    def _refresh(self):
        arrow = ""
        if self._sort_dir == "asc":
            arrow = "  ▲"
        elif self._sort_dir == "desc":
            arrow = "  ▼"
        self.setText(self.text().split("  ")[0] + arrow)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._key)
        super().mousePressEvent(e)


class SongRow(QFrame):
    clicked           = Signal(dict)
    favourite_toggled = Signal(str, bool)   # (song_id, is_now_favourite)
    delete_requested  = Signal(dict)        # song dict
    archive_requested = Signal(dict)        # song dict
    restore_requested = Signal(dict)        # archived song dict

    def __init__(self, song: dict, theme: Theme, is_fav: bool = False,
                 archived: bool = False, parent=None):
        super().__init__(parent)
        self._song   = song
        self._theme  = theme
        self._is_fav = is_fav
        self._archived = archived
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._setup_ui()
        self.apply_theme(theme)

    def _setup_ui(self):
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(10)
        s = self._song

        # star (a plain 24px placeholder in archived mode — favourites N/A)
        if self._archived:
            self._star_btn = QWidget()
            self._star_btn.setFixedSize(24, 24)
        else:
            self._star_btn = QPushButton()
            self._star_btn.setFixedSize(24, 24)
            self._star_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._star_btn.setFlat(True)
            self._star_btn.clicked.connect(self._on_star_clicked)
            self._update_star()
        lay.addWidget(self._star_btn)

        # artwork
        self._art = ArtThumbnail(s["grad"][0], s["grad"][1], s.get("seed", 1), 44)
        lay.addWidget(self._art)

        # title
        self._title_lbl = QLabel(s["title"])
        self._title_lbl.setStyleSheet("font-size: 14px; font-weight: 600;")
        self._title_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        lay.addWidget(self._title_lbl, 1)

        # artist
        self._artist_lbl = QLabel(s.get("artist", ""))
        self._artist_lbl.setStyleSheet(f"font-size: 13px; color: {self._theme.ink3};")
        self._artist_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        lay.addWidget(self._artist_lbl, 1)

        # duration
        ms   = s.get("durationMs", 0)
        secs = ms // 1000
        dur_lbl = QLabel(f"{secs // 60}:{secs % 60:02d}")
        dur_lbl.setStyleSheet("font-family: 'Consolas', monospace; font-size: 13px;")
        dur_lbl.setFixedWidth(64)
        dur_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(dur_lbl)

        # file size
        size_lbl = QLabel(_fmt_size(s.get("file_size", 0)))
        size_lbl.setStyleSheet(f"font-size: 12px; color: {self._theme.ink3};")
        size_lbl.setFixedWidth(72)
        size_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(size_lbl)

        # last played
        viewed_lbl = QLabel(_fmt_viewed(s.get("last_viewed")))
        viewed_lbl.setStyleSheet(f"font-size: 12px; color: {self._theme.ink3};")
        viewed_lbl.setFixedWidth(104)
        viewed_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(viewed_lbl)

        # added
        added_lbl = QLabel(s.get("addedLabel", ""))
        added_lbl.setStyleSheet("font-size: 12px;")
        added_lbl.setFixedWidth(96)
        added_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(added_lbl)

        # three-dot context menu button
        self._more_btn = QPushButton("⋮")
        self._more_btn.setFixedSize(28, 28)
        self._more_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._more_btn.setToolTip("More options")
        self._more_btn.clicked.connect(self._show_row_menu)
        self._more_btn.setStyleSheet(
            "QPushButton { border: none; background: transparent; "
            "font-size: 18px; font-weight: 700; color: transparent; padding: 0; border-radius: 4px; }"
            "QPushButton:hover { background: rgba(0,0,0,0.08); color: #666; }"
        )
        lay.addWidget(self._more_btn)

    # ── row context menu ─────────────────────────────────────────────────────

    def _show_row_menu(self):
        t = self._theme
        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background: {t.surface};
                border: 1px solid {t.border};
                border-radius: 4px;
                padding: 4px;
            }}
            QMenu::item {{
                padding: 8px 18px 8px 12px;
                font-size: 13px;
                color: {t.ink};
                border-radius: 4px;
            }}
            QMenu::item:selected {{ background: {t.surface2}; }}
        """)

        if self._archived:
            restore_action = QAction("Restore", self)
            restore_action.triggered.connect(lambda: self.restore_requested.emit(self._song))
            menu.addAction(restore_action)
            delete_action = QAction("Delete permanently", self)
            delete_action.triggered.connect(lambda: self.delete_requested.emit(self._song))
            menu.addAction(delete_action)
        else:
            archive_action = QAction("Archive", self)
            archive_action.triggered.connect(lambda: self.archive_requested.emit(self._song))
            menu.addAction(archive_action)
            delete_action = QAction("Delete track", self)
            delete_action.triggered.connect(lambda: self.delete_requested.emit(self._song))
            menu.addAction(delete_action)

        from PySide6.QtCore import QPoint
        btn_rect = self._more_btn.rect()
        pos = self._more_btn.mapToGlobal(btn_rect.bottomRight())
        menu.exec(QPoint(pos.x() - menu.sizeHint().width(), pos.y() + 4))

    # ── star ─────────────────────────────────────────────────────────────────

    def _update_star(self):
        if self._is_fav:
            self._star_btn.setText("★")
            self._star_btn.setStyleSheet(
                "QPushButton { border: none; background: transparent; "
                "font-size: 16px; color: #F2A23A; padding: 0; }"
            )
        else:
            self._star_btn.setText("☆")
            self._star_btn.setStyleSheet(
                "QPushButton { border: none; background: transparent; "
                "font-size: 16px; color: #AAAAAA; padding: 0; }"
            )

    def _on_star_clicked(self):
        self._is_fav = not self._is_fav
        self._update_star()
        self.favourite_toggled.emit(self._song["id"], self._is_fav)

    # ── theme / events ───────────────────────────────────────────────────────

    def apply_theme(self, theme: Theme):
        self._theme = theme
        self._artist_lbl.setStyleSheet(f"font-size: 13px; color: {theme.ink3};")
        self.setStyleSheet(
            f"SongRow {{ background: transparent; border-radius: 4px; }}"
            f"SongRow:hover {{ background: {theme.surface2}; }}"
        )

    # ── loading / dimmed feedback while a track opens ─────────────────────────

    def song_id(self) -> str:
        return self._song.get("id", "")

    def set_loading(self, loading: bool):
        """Swap the ⋮ button for a spinner while this track is opening."""
        if loading:
            if getattr(self, "_spinner", None) is None:
                self._spinner = Spinner(self._theme.accent, 18)
                self.layout().addWidget(self._spinner)
            self._more_btn.hide()
            self._spinner.show()
            self._spinner.start()
        else:
            sp = getattr(self, "_spinner", None)
            if sp is not None:
                sp.stop()
                sp.hide()
            self._more_btn.show()

    def set_dimmed(self, dimmed: bool):
        """Fade this row while another track is loading."""
        if dimmed:
            from PySide6.QtWidgets import QGraphicsOpacityEffect
            eff = QGraphicsOpacityEffect(self)
            eff.setOpacity(0.4)
            self.setGraphicsEffect(eff)
        else:
            self.setGraphicsEffect(None)

    def mousePressEvent(self, e):
        if self._archived:
            return   # archived rows aren't playable — use the ⋮ menu to restore
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._song)

    def enterEvent(self, e):
        self.setStyleSheet(f"background: {self._theme.surface2}; border-radius: 4px;")
        self._more_btn.setStyleSheet(
            "QPushButton { border: none; background: transparent; "
            "font-size: 18px; font-weight: 700; color: #93939C; padding: 0; border-radius: 4px; }"
            f"QPushButton:hover {{ background: {self._theme.surface3}; color: {self._theme.ink}; }}"
        )

    def leaveEvent(self, e):
        self.setStyleSheet("background: transparent; border-radius: 4px;")
        self._more_btn.setStyleSheet(
            "QPushButton { border: none; background: transparent; "
            "font-size: 18px; font-weight: 700; color: transparent; padding: 0; border-radius: 4px; }"
            "QPushButton:hover { background: rgba(0,0,0,0.08); color: #666; }"
        )


class ArtistGroupHeader(QWidget):
    """Divider shown above each artist group in the 'By artist' view."""

    def __init__(self, artist: str, count: int, theme: Theme, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 16, 12, 6)
        lay.setSpacing(8)

        name_lbl = QLabel(artist or "Unknown artist")
        name_lbl.setStyleSheet(
            f"font-size: 13px; font-weight: 700; color: {theme.ink};"
        )
        count_lbl = QLabel(f"{count} {'track' if count == 1 else 'tracks'}")
        count_lbl.setStyleSheet(
            f"font-family: 'Consolas', monospace; font-size: 11px; color: {theme.ink3};"
        )
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {theme.border};")

        lay.addWidget(name_lbl)
        lay.addWidget(count_lbl)
        lay.addWidget(sep, 1)


class SongRowNoArtist(SongRow):
    """SongRow with the artist label omitted (used in the artist-grouped view)."""

    def _setup_ui(self):
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(10)
        s = self._song

        # star
        self._star_btn = QPushButton()
        self._star_btn.setFixedSize(24, 24)
        self._star_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._star_btn.setFlat(True)
        self._star_btn.clicked.connect(self._on_star_clicked)
        self._update_star()
        lay.addWidget(self._star_btn)

        # artwork
        self._art = ArtThumbnail(s["grad"][0], s["grad"][1], s.get("seed", 1), 44)
        lay.addWidget(self._art)

        # title (takes all the space the artist column used to share)
        self._title_lbl = QLabel(s["title"])
        self._title_lbl.setStyleSheet("font-size: 14px; font-weight: 600;")
        self._title_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        lay.addWidget(self._title_lbl, 2)

        # keep a hidden stub so parent helper methods that reference _artist_lbl don't crash
        self._artist_lbl = QLabel("")
        self._artist_lbl.hide()

        # duration
        ms   = s.get("durationMs", 0)
        secs = ms // 1000
        dur_lbl = QLabel(f"{secs // 60}:{secs % 60:02d}")
        dur_lbl.setStyleSheet("font-family: 'Consolas', monospace; font-size: 13px;")
        dur_lbl.setFixedWidth(64)
        dur_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(dur_lbl)

        # file size
        size_lbl = QLabel(_fmt_size(s.get("file_size", 0)))
        size_lbl.setStyleSheet(f"font-size: 12px; color: {self._theme.ink3};")
        size_lbl.setFixedWidth(72)
        size_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(size_lbl)

        # last played
        viewed_lbl = QLabel(_fmt_viewed(s.get("last_viewed")))
        viewed_lbl.setStyleSheet(f"font-size: 12px; color: {self._theme.ink3};")
        viewed_lbl.setFixedWidth(104)
        viewed_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(viewed_lbl)

        # added
        added_lbl = QLabel(s.get("addedLabel", ""))
        added_lbl.setStyleSheet("font-size: 12px;")
        added_lbl.setFixedWidth(96)
        added_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(added_lbl)

        # three-dot context menu button
        self._more_btn = QPushButton("⋮")
        self._more_btn.setFixedSize(28, 28)
        self._more_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._more_btn.setToolTip("More options")
        self._more_btn.clicked.connect(self._show_row_menu)
        self._more_btn.setStyleSheet(
            "QPushButton { border: none; background: transparent; "
            "font-size: 18px; font-weight: 700; color: transparent; padding: 0; border-radius: 4px; }"
            "QPushButton:hover { background: rgba(0,0,0,0.08); color: #666; }"
        )
        lay.addWidget(self._more_btn)


class LibraryPanel(QWidget):
    song_opened       = Signal(dict)
    import_requested  = Signal()
    favourite_toggled = Signal(str, bool)
    delete_requested  = Signal(dict)   # song dict
    archive_requested = Signal(dict)   # song dict
    restore_requested = Signal(dict)   # archived song dict

    def __init__(self, theme: Theme, parent=None):
        super().__init__(parent)
        self._theme         = theme
        self._songs:    list[dict] = []
        self._archived: list[dict] = []
        self._rows:     list[SongRow] = []
        self._favourites:   set[str] = set()
        self._last_viewed:  dict[str, float] = {}
        self._nav_filter:   str = "all"
        self._search_query: str = ""
        self._sort_key: str = _DEFAULT_SORT
        self._sort_asc: bool = _DEFAULT_ASC
        self._header_labels: dict[str, _HeaderLabel] = {}
        # Cover-art backfill: maps .stems path → its ArtThumbnail for the
        # current rows, plus the running lookup worker.
        self._art_by_path: dict[str, ArtThumbnail] = {}
        self._art_loader: _CoverBackfillWorker | None = None
        self._setup_ui()

    # ── public API ────────────────────────────────────────────────────────────

    def set_songs(self, songs: list[dict]):
        self._songs = songs
        self._rebuild_rows()

    def set_archived(self, archived: list[dict]):
        self._archived = archived
        if self._nav_filter == "archived":
            self._rebuild_rows()

    def set_favourites(self, favs: set[str]):
        self._favourites = favs
        self._rebuild_rows()

    def set_last_viewed(self, last_viewed: dict[str, float]):
        self._last_viewed = last_viewed
        self._rebuild_rows()

    def set_nav_filter(self, nav_filter: str):
        self._nav_filter = nav_filter
        titles = {
            "fav":    "Favourites",
            "recent": "Recently played",
            "all":    "All tracks",
            "artist": "By artist",
            "archived": "Archived",
        }
        self._head_lbl.setText(titles.get(nav_filter, "All tracks"))
        # Hide sort headers in artist view (grouped by artist, no sort)
        self._col_head_w.setVisible(nav_filter not in ("artist", "archived"))
        self._rebuild_rows()

    def filter(self, query: str):
        self._search_query = query
        self._rebuild_rows()

    # ── UI construction ───────────────────────────────────────────────────────

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)

        self._content = QWidget()
        cl = QVBoxLayout(self._content)
        cl.setContentsMargins(28, 26, 28, 60)
        cl.setSpacing(0)

        # heading row
        self._head_lbl = QLabel("All tracks")
        self._head_lbl.setStyleSheet("font-size: 15px; font-weight: 600;")
        self._meta_lbl = QLabel("")
        self._meta_lbl.setStyleSheet("font-family: 'Consolas', monospace; font-size: 13px;")
        head_row = QHBoxLayout()
        head_row.setContentsMargins(0, 0, 0, 14)
        head_row.addWidget(self._head_lbl)
        head_row.addWidget(self._meta_lbl)
        head_row.addStretch()
        cl.addLayout(head_row)

        # column header bar
        col_head = QHBoxLayout()
        col_head.setContentsMargins(12, 0, 12, 0)
        col_head.setSpacing(10)

        # spacers matching star + artwork leading widgets
        col_head.addSpacing(24 + 10 + 44 + 10)   # star + gap + art + gap

        base_style = (
            "font-size: 11px; font-weight: 600; letter-spacing: 0.08em; "
            "text-transform: uppercase;"
        )

        for key, label, fixed, _ in _COLS:
            lbl = _HeaderLabel(key, label, self._theme)
            lbl.setStyleSheet(base_style)
            if fixed:
                lbl.setFixedWidth(fixed)
                lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            lbl.clicked.connect(self._on_header_clicked)
            self._header_labels[key] = lbl
            if fixed:
                col_head.addWidget(lbl)
            else:
                col_head.addWidget(lbl, 1)

        # spacer matching the three-dot button width in each row
        col_head.addSpacing(28 + 10)   # button width + layout spacing

        self._col_head_w = QWidget()
        self._col_head_w.setLayout(col_head)
        self._col_head_w.setFixedHeight(36)
        cl.addWidget(self._col_head_w)
        self._update_header_arrows()

        # separator
        self._sep = QFrame()
        self._sep.setFrameShape(QFrame.Shape.HLine)
        cl.addWidget(self._sep)

        # rows
        self._rows_w = QWidget()
        self._rows_lay = QVBoxLayout(self._rows_w)
        self._rows_lay.setContentsMargins(0, 4, 0, 0)
        self._rows_lay.setSpacing(0)
        cl.addWidget(self._rows_w)
        cl.addStretch()

        # empty: no library
        self._empty = QWidget()
        el = QVBoxLayout(self._empty)
        el.setAlignment(Qt.AlignmentFlag.AlignCenter)
        e1 = QLabel("Your library is empty")
        e1.setStyleSheet("font-size: 17px; font-weight: 600;")
        e2 = QLabel("Import a song from a file or a YouTube link to split it into stems.")
        e2.setStyleSheet("font-size: 13px;")
        e2.setWordWrap(True)
        e2.setAlignment(Qt.AlignmentFlag.AlignCenter)
        eb = QPushButton("+ Import a track")
        eb.setProperty("role", "primary")
        eb.setFixedWidth(160)
        eb.clicked.connect(self.import_requested)
        el.addWidget(e1, 0, Qt.AlignmentFlag.AlignHCenter)
        el.addWidget(e2, 0, Qt.AlignmentFlag.AlignHCenter)
        el.addWidget(eb, 0, Qt.AlignmentFlag.AlignHCenter)
        self._empty.hide()
        cl.addWidget(self._empty, 0, Qt.AlignmentFlag.AlignCenter)

        # empty: no recent plays
        self._empty_recent = QWidget()
        rl = QVBoxLayout(self._empty_recent)
        rl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        r1 = QLabel("No recently played tracks")
        r1.setStyleSheet("font-size: 17px; font-weight: 600;")
        r2 = QLabel("Tracks you open will appear here.")
        r2.setStyleSheet("font-size: 13px;")
        r2.setWordWrap(True)
        r2.setAlignment(Qt.AlignmentFlag.AlignCenter)
        rl.addWidget(r1, 0, Qt.AlignmentFlag.AlignHCenter)
        rl.addWidget(r2, 0, Qt.AlignmentFlag.AlignHCenter)
        self._empty_recent.hide()
        cl.addWidget(self._empty_recent, 0, Qt.AlignmentFlag.AlignCenter)

        # empty: no matches in artist view
        self._empty_artist = QWidget()
        al = QVBoxLayout(self._empty_artist)
        al.setAlignment(Qt.AlignmentFlag.AlignCenter)
        a1 = QLabel("No tracks found")
        a1.setStyleSheet("font-size: 17px; font-weight: 600;")
        al.addWidget(a1, 0, Qt.AlignmentFlag.AlignHCenter)
        self._empty_artist.hide()
        cl.addWidget(self._empty_artist, 0, Qt.AlignmentFlag.AlignCenter)

        # empty: no favourites
        self._empty_fav = QWidget()
        fl = QVBoxLayout(self._empty_fav)
        fl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        f1 = QLabel("No favourites yet")
        f1.setStyleSheet("font-size: 17px; font-weight: 600;")
        f2 = QLabel("Click the ☆ star next to any track to add it to your favourites.")
        f2.setStyleSheet("font-size: 13px;")
        f2.setWordWrap(True)
        f2.setAlignment(Qt.AlignmentFlag.AlignCenter)
        fl.addWidget(f1, 0, Qt.AlignmentFlag.AlignHCenter)
        fl.addWidget(f2, 0, Qt.AlignmentFlag.AlignHCenter)
        self._empty_fav.hide()
        cl.addWidget(self._empty_fav, 0, Qt.AlignmentFlag.AlignCenter)

        # empty: no archived tracks
        self._empty_archived = QWidget()
        arl = QVBoxLayout(self._empty_archived)
        arl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ar1 = QLabel("No archived tracks")
        ar1.setStyleSheet("font-size: 17px; font-weight: 600;")
        ar2 = QLabel("Archive a track from its ⋮ menu to free up space; "
                     "it can be restored here later.")
        ar2.setStyleSheet("font-size: 13px;")
        ar2.setWordWrap(True)
        ar2.setAlignment(Qt.AlignmentFlag.AlignCenter)
        arl.addWidget(ar1, 0, Qt.AlignmentFlag.AlignHCenter)
        arl.addWidget(ar2, 0, Qt.AlignmentFlag.AlignHCenter)
        self._empty_archived.hide()
        cl.addWidget(self._empty_archived, 0, Qt.AlignmentFlag.AlignCenter)

        self._scroll.setWidget(self._content)
        root.addWidget(self._scroll)

    # ── sorting ───────────────────────────────────────────────────────────────

    def _on_header_clicked(self, key: str):
        if self._sort_key == key:
            self._sort_asc = not self._sort_asc
        else:
            self._sort_key = key
            # "added" naturally means newest-first → default desc
            self._sort_asc = key != "added"
        self._update_header_arrows()
        self._rebuild_rows()

    def _update_header_arrows(self):
        for key, lbl in self._header_labels.items():
            if key == self._sort_key:
                lbl.set_sort("asc" if self._sort_asc else "desc")
            else:
                lbl.set_sort(None)

    def _sorted(self, songs: list[dict]) -> list[dict]:
        col = next((c for c in _COLS if c[0] == self._sort_key), _COLS[0])
        key_fn = col[3]
        return sorted(songs, key=key_fn, reverse=not self._sort_asc)

    # ── row building ──────────────────────────────────────────────────────────

    def _visible_songs(self) -> list[dict]:
        songs = self._songs
        if self._nav_filter == "archived":
            songs = self._archived
            if self._search_query:
                q = self._search_query.lower()
                songs = [s for s in songs if
                         q in (s.get("title") or "").lower() or
                         q in (s.get("artist") or "").lower()]
            return songs
        if self._nav_filter == "artist":
            if self._search_query:
                q = self._search_query.lower()
                songs = [s for s in songs if
                         q in (s.get("title") or "").lower() or
                         q in (s.get("artist") or "").lower()]
            return songs   # grouping/sorting handled in _rebuild_rows_by_artist
        elif self._nav_filter == "fav":
            songs = [s for s in songs if s["id"] in self._favourites]
        elif self._nav_filter == "recent":
            viewed = self._last_viewed
            songs = [s for s in songs if s["id"] in viewed]
            songs = sorted(songs, key=lambda s: viewed[s["id"]], reverse=True)[:10]
            # return early — recent has its own fixed order, skip generic sort
            if self._search_query:
                q = self._search_query.lower()
                songs = [s for s in songs if
                         q in (s.get("title") or "").lower() or
                         q in (s.get("artist") or "").lower()]
            return songs
        if self._search_query:
            q = self._search_query.lower()
            songs = [s for s in songs if
                     q in (s.get("title") or "").lower() or
                     q in (s.get("artist") or "").lower()]
        return self._sorted(songs)

    def _rebuild_rows(self):
        # Remove all existing row widgets (and any artist group headers)
        while self._rows_lay.count():
            item = self._rows_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._rows.clear()

        songs = self._visible_songs()
        self._meta_lbl.setText(f"{len(songs)} {'track' if len(songs) == 1 else 'tracks'}")

        self._empty.hide()
        self._empty_recent.hide()
        self._empty_fav.hide()
        self._empty_artist.hide()
        self._empty_archived.hide()

        if not songs:
            self._sep.hide()
            if self._nav_filter == "artist":
                self._empty_artist.show()
            elif self._nav_filter == "archived":
                self._empty_archived.show()
            elif self._nav_filter == "fav":
                self._empty_fav.show()
            elif self._nav_filter == "recent":
                self._empty_recent.show()
            else:
                self._col_head_w.hide()
                self._sep.hide()
                self._empty.show()
            return

        self._sep.show()

        if self._nav_filter == "archived":
            self._art_by_path = {}
            self._col_head_w.hide()
            for song in songs:
                row = SongRow(song, self._theme, archived=True)
                row.restore_requested.connect(self.restore_requested)
                row.delete_requested.connect(self.delete_requested)
                self._rows_lay.addWidget(row)
                self._rows.append(row)
            return

        # Reset the per-rebuild art registry; collect tracks needing a lookup.
        self._art_by_path = {}
        pending: list[tuple[str, str, str]] = []

        if self._nav_filter == "artist":
            self._rebuild_rows_by_artist(songs, pending)
        else:
            self._col_head_w.show()
            for song in songs:
                song = {**song, "last_viewed": self._last_viewed.get(song["id"])}
                row = SongRow(song, self._theme, is_fav=song["id"] in self._favourites)
                row.clicked.connect(self.song_opened)
                row.favourite_toggled.connect(self.favourite_toggled)
                row.delete_requested.connect(self.delete_requested)
                row.archive_requested.connect(self.archive_requested)
                self._rows_lay.addWidget(row)
                self._rows.append(row)
                self._attach_cover(row._art, song, pending)

        self._start_backfill(pending)

    def _rebuild_rows_by_artist(self, songs: list[dict], pending: list):
        """Build the artist-grouped layout: artist header then track rows (no artist column)."""
        # Group by artist, case-insensitively; first-seen capitalisation wins.
        groups: dict[str, tuple[str, list[dict]]] = {}   # lower -> (canonical, songs)
        for song in songs:
            key = (song.get("artist") or "Unknown artist").strip() or "Unknown artist"
            norm = key.lower()
            if norm not in groups:
                groups[norm] = (key, [])
            groups[norm][1].append(song)

        for norm in sorted(groups):
            canon, group_songs = groups[norm]
            header = ArtistGroupHeader(canon, len(group_songs), self._theme)
            self._rows_lay.addWidget(header)
            for song in sorted(group_songs, key=lambda s: (s.get("title") or "").lower()):
                song = {**song, "last_viewed": self._last_viewed.get(song["id"])}
                row = SongRowNoArtist(song, self._theme, is_fav=song["id"] in self._favourites)
                row.clicked.connect(self.song_opened)
                row.favourite_toggled.connect(self.favourite_toggled)
                row.delete_requested.connect(self.delete_requested)
                row.archive_requested.connect(self.archive_requested)
                self._rows_lay.addWidget(row)
                self._rows.append(row)
                self._attach_cover(row._art, song, pending)

    # ── cover art ───────────────────────────────────────────────────────────

    def _attach_cover(self, art: ArtThumbnail, song: dict, pending: list):
        """Show a cached cover if available; otherwise queue an online lookup."""
        sp = song.get("stems_path")
        if not sp:
            return
        self._art_by_path[sp] = art
        pm = _load_cover_pixmap(song)
        if pm is not None:
            art.set_cover(pm)
            return
        # No art yet — queue a one-time online lookup (needs at least a title).
        title = song.get("title") or ""
        if title and sp not in _ATTEMPTED:
            _ATTEMPTED.add(sp)
            pending.append((sp, song.get("artist", ""), title))

    def _start_backfill(self, pending: list):
        if not pending:
            return
        if self._art_loader and self._art_loader.isRunning():
            # Let the in-flight loader finish; its items are already _ATTEMPTED.
            return
        self._art_loader = _CoverBackfillWorker(pending, self)
        self._art_loader.fetched.connect(self._on_cover_fetched)
        self._art_loader.start()

    def _on_cover_fetched(self, stems_path: str, data: bytes):
        pm = QPixmap()
        if not (pm.loadFromData(data) and not pm.isNull()):
            return
        _PIX_CACHE[stems_path] = pm
        art = self._art_by_path.get(stems_path)
        if art is not None:
            art.set_cover(pm)

    def stop_background(self):
        """Stop the cover-lookup worker (called on app close)."""
        if self._art_loader and self._art_loader.isRunning():
            self._art_loader.requestInterruption()
            self._art_loader.wait(2000)

    # ── track-open loading feedback ───────────────────────────────────────────

    def show_loading(self, song_id: str):
        """Dim every row except the one opening, which shows a spinner."""
        for row in self._rows:
            if row.song_id() == song_id:
                row.set_loading(True)
                row.set_dimmed(False)
            else:
                row.set_dimmed(True)

    def clear_loading(self):
        for row in self._rows:
            row.set_loading(False)
            row.set_dimmed(False)

    # ── theme ─────────────────────────────────────────────────────────────────

    def apply_theme(self, theme: Theme):
        self._theme = theme
        self._meta_lbl.setStyleSheet(
            f"font-family: 'Consolas', monospace; font-size: 13px; color: {theme.ink3};")
        self._sep.setStyleSheet(f"color: {theme.border};")
        base = (
            f"font-size: 11px; font-weight: 600; letter-spacing: 0.08em; color: {theme.ink3};"
        )
        for lbl in self._header_labels.values():
            lbl.setStyleSheet(base)
        for row in self._rows:
            row.apply_theme(theme)
