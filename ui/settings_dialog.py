"""Settings dialog — library path and other preferences."""

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QFileDialog, QFrame, QCheckBox
)

from core import settings as S
from ui.theme import Theme


class SettingsDialog(QDialog):
    library_changed = Signal(str)  # new library path

    def __init__(self, theme: Theme, parent=None):
        super().__init__(parent)
        self._theme = theme
        self.setWindowTitle("Settings")
        self.setFixedWidth(500)
        self.setModal(True)
        self._setup_ui()
        self.setStyleSheet(f"QDialog {{ background: {theme.surface}; border-radius: 4px; }}")

    def _setup_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 24)
        lay.setSpacing(16)

        # header
        head_row = QHBoxLayout()
        title = QLabel("Settings")
        title.setStyleSheet("font-size: 19px; font-weight: 600;")
        close_btn = QPushButton("✕")
        close_btn.setProperty("role", "icon")
        close_btn.setFixedSize(32, 32)
        close_btn.clicked.connect(self.reject)
        head_row.addWidget(title, 1)
        head_row.addWidget(close_btn, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(head_row)

        # divider
        div = QFrame()
        div.setFrameShape(QFrame.Shape.HLine)
        div.setStyleSheet(f"color: {self._theme.border};")
        lay.addWidget(div)

        # library path
        lib_lbl = QLabel("LIBRARY FOLDER")
        lib_lbl.setStyleSheet(
            f"font-size: 11px; font-weight: 600; letter-spacing: 0.07em; color: {self._theme.ink3};"
        )
        lay.addWidget(lib_lbl)

        path_row = QHBoxLayout()
        path_row.setSpacing(8)
        self._path_input = QLineEdit(str(S.library_path()))
        self._path_input.setPlaceholderText("e.g. C:/Users/you/Music/RehearsalRoom")
        browse_btn = QPushButton("Browse…")
        browse_btn.setProperty("role", "ghost")
        browse_btn.setFixedHeight(36)
        browse_btn.clicked.connect(self._browse)
        path_row.addWidget(self._path_input, 1)
        path_row.addWidget(browse_btn)
        lay.addLayout(path_row)

        hint = QLabel(
            "All processed songs are saved here automatically. "
            "The folder is scanned each time the app starts."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"font-size: 12px; color: {self._theme.ink3}; line-height: 1.5;")
        lay.addWidget(hint)

        # divider
        div2 = QFrame()
        div2.setFrameShape(QFrame.Shape.HLine)
        div2.setStyleSheet(f"color: {self._theme.border};")
        lay.addWidget(div2)

        # AcoustID API key
        acoustid_lbl = QLabel("ACOUSTID API KEY")
        acoustid_lbl.setStyleSheet(
            f"font-size: 11px; font-weight: 600; letter-spacing: 0.07em; color: {self._theme.ink3};"
        )
        lay.addWidget(acoustid_lbl)

        self._acoustid_input = QLineEdit(S.get("acoustid_api_key") or "")
        self._acoustid_input.setPlaceholderText("Paste your AcoustID API key here")
        self._acoustid_input.setEchoMode(QLineEdit.EchoMode.Password)
        lay.addWidget(self._acoustid_input)

        acoustid_hint = QLabel(
            'Used to identify songs by audio fingerprint when no tags are available. '
            'Get a free key at <a href="https://acoustid.org/login">acoustid.org</a>.'
        )
        acoustid_hint.setWordWrap(True)
        acoustid_hint.setOpenExternalLinks(True)
        acoustid_hint.setStyleSheet(f"font-size: 12px; color: {self._theme.ink3};")
        lay.addWidget(acoustid_hint)

        # divider
        div3 = QFrame()
        div3.setFrameShape(QFrame.Shape.HLine)
        div3.setStyleSheet(f"color: {self._theme.border};")
        lay.addWidget(div3)

        # Vidami footswitch
        vidami_lbl = QLabel("HARDWARE")
        vidami_lbl.setStyleSheet(
            f"font-size: 11px; font-weight: 600; letter-spacing: 0.07em; color: {self._theme.ink3};"
        )
        lay.addWidget(vidami_lbl)

        self._vidami_check = QCheckBox("Enable Vidami footswitch support")
        self._vidami_check.setChecked(bool(S.get("vidami_enabled")))
        self._vidami_check.setStyleSheet(f"font-size: 13px; color: {self._theme.ink};")
        lay.addWidget(self._vidami_check)

        vidami_hint = QLabel(
            "Intercepts footswitch key commands ({  K  }  `  ;) app-wide. "
            "Disable if these keys conflict with other software."
        )
        vidami_hint.setWordWrap(True)
        vidami_hint.setStyleSheet(f"font-size: 12px; color: {self._theme.ink3};")
        lay.addWidget(vidami_hint)

        lay.addStretch()

        # footer
        foot = QHBoxLayout()
        foot.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setProperty("role", "ghost")
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("Save")
        save_btn.setProperty("role", "primary")
        save_btn.clicked.connect(self._save)
        foot.addWidget(cancel_btn)
        foot.addWidget(save_btn)
        lay.addLayout(foot)

    def _browse(self):
        current = self._path_input.text() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "Choose library folder", current)
        if chosen:
            self._path_input.setText(chosen)

    def _save(self):
        path = self._path_input.text().strip()
        if not path:
            return
        Path(path).mkdir(parents=True, exist_ok=True)
        data = S.load()
        data["library_path"] = path
        data["acoustid_api_key"] = self._acoustid_input.text().strip()
        data["vidami_enabled"] = self._vidami_check.isChecked()
        S.save(data)
        self.library_changed.emit(path)
        self.accept()
