"""ThemeManager — satu sumber kebenaran tema, signal themeChanged, re-polish global."""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

from ui.theme.qss import build_qss
from ui.theme.tokens import ACCENT_PRESETS, P, Palette, SZ, Sizes, apply_accent


class ThemeManager(QObject):
    """Kelola palette & QSS global; emit themeChanged agar widget custom repaint."""

    themeChanged = Signal(object)  # object = Palette baru

    def __init__(self) -> None:
        super().__init__()
        self.palette: Palette = P
        self.sizes: Sizes = SZ
        self.accent_name: str = "Neon Green"

    # ------------------------------------------------------------------
    def choose_font(self) -> QFont:
        fams = {f.lower() for f in QFontDatabase.families()}
        for cand in ("Segoe UI", "Inter", "Ubuntu", "DejaVu Sans"):
            if cand.lower() in fams:
                f = QFont(cand, self.sizes.fs_body)
                return f
        return QFont("sans-serif", self.sizes.fs_body)

    def mono_font(self, px: int | None = None) -> QFont:
        fams = {f.lower() for f in QFontDatabase.families()}
        for cand in ("JetBrains Mono", "Cascadia Mono", "Consolas", "DejaVu Sans Mono"):
            if cand.lower() in fams:
                return QFont(cand, px or self.sizes.fs_body)
        f = QFont("monospace", px or self.sizes.fs_body)
        f.setStyleHint(QFont.StyleHint.Monospace)
        return f

    # ------------------------------------------------------------------
    def set_accent(self, name: str) -> bool:
        """Ganti accent hue; kembalikan True bila berubah (tanpa restart)."""
        if name not in ACCENT_PRESETS or name == self.accent_name:
            return False
        self.accent_name = name
        self.palette = apply_accent(name)
        self.apply_global()
        self.themeChanged.emit(self.palette)
        return True

    def apply_global(self) -> None:
        app = QApplication.instance()
        if isinstance(app, QApplication):
            app.setStyleSheet(build_qss(self.palette, self.sizes))
            app.setFont(self.choose_font())

    def qss_for(self, extra: str = "") -> str:
        return build_qss(self.palette, self.sizes) + extra

    # helper warna untuk painting manual
    def col(self, attr: str) -> QColor:
        return QColor(getattr(self.palette, attr))
