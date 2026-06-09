"""First-run dialog — shown once when Demucs model weights are not cached."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QProgressBar, QFrame
)

from core.model_cache import ModelDownloadWorker
from ui.theme import Theme


class FirstRunDialog(QDialog):
    """Blocks until model weights have been downloaded (or the user quits)."""

    def __init__(self, theme: Theme, parent=None):
        super().__init__(parent)
        self._theme = theme
        self._worker: ModelDownloadWorker | None = None
        self._success = False

        self.setWindowTitle("Rehearsal Room — First Run Setup")
        self.setMinimumWidth(480)
        self.setModal(True)
        self._setup_ui()

    def _setup_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(32, 28, 32, 24)
        lay.setSpacing(16)

        # heading
        title = QLabel("Welcome to Rehearsal Room")
        title.setStyleSheet("font-size: 18px; font-weight: 700;")
        lay.addWidget(title)

        # explanation
        body = QLabel(
            "Before you can import and separate tracks, Rehearsal Room needs to "
            "download the Demucs AI model (~80 MB). This only happens once — the "
            "model is saved to your local cache for all future sessions."
        )
        body.setWordWrap(True)
        body.setStyleSheet(f"font-size: 13px; color: {self._theme.ink3}; line-height: 1.5;")
        lay.addWidget(body)

        # divider
        div = QFrame()
        div.setFrameShape(QFrame.Shape.HLine)
        div.setStyleSheet(f"color: {self._theme.border};")
        lay.addWidget(div)

        # progress bar
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setFixedHeight(8)
        self._progress_bar.setStyleSheet(f"""
            QProgressBar {{
                background: {self._theme.surface2};
                border-radius: 4px;
                border: none;
            }}
            QProgressBar::chunk {{
                background: {self._theme.accent};
                border-radius: 4px;
            }}
        """)
        lay.addWidget(self._progress_bar)

        # status label
        self._status_lbl = QLabel("Ready to download.")
        self._status_lbl.setStyleSheet(f"font-size: 12px; color: {self._theme.ink3};")
        lay.addWidget(self._status_lbl)

        # buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        self._quit_btn = QPushButton("Quit")
        self._quit_btn.setProperty("role", "ghost")
        self._quit_btn.setFixedHeight(36)
        self._quit_btn.clicked.connect(self._on_quit)
        btn_row.addWidget(self._quit_btn)

        self._download_btn = QPushButton("Download Model")
        self._download_btn.setProperty("role", "primary")
        self._download_btn.setFixedHeight(36)
        self._download_btn.clicked.connect(self._start_download)
        btn_row.addWidget(self._download_btn)

        lay.addLayout(btn_row)

    def _start_download(self):
        self._download_btn.setEnabled(False)
        self._download_btn.setText("Downloading…")
        self._quit_btn.setEnabled(False)

        self._worker = ModelDownloadWorker()
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_progress(self, pct: int, msg: str):
        self._progress_bar.setValue(pct)
        self._status_lbl.setText(msg)

    def _on_finished(self):
        self._progress_bar.setValue(100)
        self._status_lbl.setText("Model downloaded successfully.")
        self._success = True
        self.accept()

    def _on_error(self, msg: str):
        self._status_lbl.setText("Download failed — check your internet connection.")
        self._download_btn.setEnabled(True)
        self._download_btn.setText("Retry")
        self._quit_btn.setEnabled(True)
        # Show error detail in a sub-label
        err = QLabel(msg[:300])
        err.setWordWrap(True)
        err.setStyleSheet("font-size: 11px; color: #E74C3C; font-family: Consolas;")
        self.layout().addWidget(err)

    def _on_quit(self):
        if self._worker and self._worker.isRunning():
            self._worker.terminate()
        self.reject()

    def succeeded(self) -> bool:
        return self._success
