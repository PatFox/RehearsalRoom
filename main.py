"""Rehearsal Room — entry point."""

import sys
import os

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(__file__))

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtCore import Qt

from ui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Rehearsal Room")
    app.setApplicationVersion("0.1.0")

    # High-DPI support
    app.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    # Load bundled fonts if present
    font_dir = os.path.join(os.path.dirname(__file__), "assets", "fonts")
    if os.path.isdir(font_dir):
        for fname in os.listdir(font_dir):
            if fname.endswith((".ttf", ".otf")):
                QFontDatabase.addApplicationFont(os.path.join(font_dir, fname))

    # Default font
    default_font = QFont("Segoe UI", 10)
    app.setFont(default_font)

    win = MainWindow()
    win.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
