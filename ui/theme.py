"""Theme dataclass — light/dark colours and generated QSS for the whole app."""

from dataclasses import dataclass, field
from PySide6.QtGui import QColor


STEM_COLORS_DEFAULT = ("#FF5A5F", "#F2A23A", "#7C5CFF", "#15B6A4")
STEM_IDS = ("vocals", "drums", "bass", "other")
STEM_LABELS = ("Vocals", "Drums", "Bass", "Other")


@dataclass
class Theme:
    dark: bool = False
    accent: str = "#2E6BFF"
    stem_colors: list = field(default_factory=lambda: list(STEM_COLORS_DEFAULT))

    # ---- colour tokens ----------------------------------------------------
    @property
    def bg(self):         return "#0D0D10" if self.dark else "#FBFBF8"
    @property
    def surface(self):    return "#161619" if self.dark else "#FFFFFF"
    @property
    def surface2(self):   return "#1E1E23" if self.dark else "#F4F4F0"
    @property
    def surface3(self):   return "#26262C" if self.dark else "#ECECE6"
    @property
    def border(self):     return "#2A2A30" if self.dark else "#E2E2DC"
    @property
    def border_strong(self): return "#3A3A42" if self.dark else "#CFCFC6"
    @property
    def ink(self):        return "#F3F3F6" if self.dark else "#17171B"
    @property
    def ink2(self):       return "#A2A2AC" if self.dark else "#56565F"
    @property
    def ink3(self):       return "#66666F" if self.dark else "#93939C"
    @property
    def ink_inv(self):    return "#0D0D10" if self.dark else "#FBFBF8"

    def accent_soft(self) -> str:
        c = QColor(self.accent)
        alpha = int(255 * (0.18 if self.dark else 0.10))
        return f"rgba({c.red()},{c.green()},{c.blue()},{alpha})"

    def stem_color(self, stem_id: str) -> str:
        mapping = dict(zip(STEM_IDS, self.stem_colors))
        return mapping.get(stem_id, "#888888")

    # ---- stylesheet -------------------------------------------------------
    def qss(self) -> str:
        acc = self.accent
        acc_soft = self.accent_soft()
        return f"""
/* === base === */
QWidget {{
    background-color: {self.bg};
    color: {self.ink};
    font-family: "Segoe UI", "Space Grotesk", system-ui, sans-serif;
    font-size: 14px;
    outline: none;
    border: none;
}}
QFrame {{ background: transparent; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}

/* scrollbar */
QScrollBar:vertical {{
    background: transparent; width: 11px; margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {self.border_strong}; border-radius: 5px;
    min-height: 28px; margin: 2px 2px 2px 4px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}

/* === buttons === */
QPushButton {{
    background: {self.surface2};
    color: {self.ink};
    border: none;
    border-radius: 10px;
    padding: 9px 16px;
    font-size: 13px;
    font-weight: 600;
}}
QPushButton:hover {{ background: {self.surface3}; }}
QPushButton:pressed {{ background: {self.border_strong}; }}

QPushButton[role="primary"] {{
    background: {acc};
    color: #ffffff;
    border-radius: 10px;
}}
QPushButton[role="primary"]:hover {{ background: {self._lighten(acc)}; }}

QPushButton[role="ghost"] {{
    background: {self.surface2};
    color: {self.ink};
}}
QPushButton[role="ghost"]:hover {{ background: {self.surface3}; }}

QPushButton[role="outline"] {{
    background: transparent;
    color: {self.ink};
    border: 1px solid {self.border_strong};
}}
QPushButton[role="outline"]:hover {{ background: {self.surface2}; }}

QPushButton[role="icon"] {{
    background: transparent;
    border-radius: 8px;
    padding: 6px;
    color: {self.ink2};
}}
QPushButton[role="icon"]:hover {{ background: {self.surface2}; color: {self.ink}; }}

/* === inputs === */
QLineEdit {{
    background: {self.surface2};
    color: {self.ink};
    border: 1px solid {self.border_strong};
    border-radius: 10px;
    padding: 10px 13px;
    font-size: 14px;
    selection-background-color: {acc_soft};
}}
QLineEdit:focus {{
    border-color: {acc};
    background: {self.surface};
}}

/* === labels === */
QLabel {{ background: transparent; }}

/* === slider === */
QSlider::groove:horizontal {{
    height: 5px;
    background: {self.surface3};
    border-radius: 3px;
}}
QSlider::handle:horizontal {{
    width: 15px; height: 15px;
    background: {self.surface};
    border: 2px solid {self.border_strong};
    border-radius: 8px;
    margin: -5px 0;
}}
QSlider::sub-page:horizontal {{
    background: {acc};
    border-radius: 3px;
}}

/* === tooltip === */
QToolTip {{
    background: {self.ink};
    color: {self.ink_inv};
    border: none;
    border-radius: 8px;
    padding: 6px 10px;
    font-size: 12px;
}}
"""

    @staticmethod
    def _lighten(hex_col: str, amount: float = 0.1) -> str:
        c = QColor(hex_col)
        h, s, v, a = c.getHsvF()
        v = min(1.0, v + amount)
        c.setHsvF(h, s, v, a)
        return c.name()
