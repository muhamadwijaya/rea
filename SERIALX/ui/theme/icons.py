"""Pabrik ikon SVG internal — QIcon dari string, tanpa file aset eksternal."""
from __future__ import annotations

import re

from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtSvg import QSvgRenderer  # type: ignore[import]
from PySide6.QtCore import QByteArray
from PySide6.QtGui import QImage, QPainter

# path SVG 24x24 (stroke-based, gaya industrial)
_PATHS: dict[str, str] = {
    "play": '<path d="M7 4l13 8-13 8z" fill="{c}"/>',
    "pause": '<rect x="6" y="4" width="4" height="16" rx="1" fill="{c}"/><rect x="14" y="4" width="4" height="16" rx="1" fill="{c}"/>',
    "stop": '<rect x="5" y="5" width="14" height="14" rx="2" fill="{c}"/>',
    "print": '<path d="M7 8V3h10v5" fill="none" stroke="{c}" stroke-width="2"/><rect x="4" y="8" width="16" height="8" rx="2" fill="none" stroke="{c}" stroke-width="2"/><rect x="7" y="14" width="10" height="7" fill="none" stroke="{c}" stroke-width="2"/>',
    "scan": '<path d="M4 8V4h4M20 8V4h-4M4 16v4h4M20 16v4h-4" fill="none" stroke="{c}" stroke-width="2"/><path d="M3 12h18" stroke="{c}" stroke-width="2"/>',
    "camera": '<rect x="3" y="7" width="18" height="13" rx="2" fill="none" stroke="{c}" stroke-width="2"/><circle cx="12" cy="13" r="4" fill="none" stroke="{c}" stroke-width="2"/><path d="M8 7l2-3h4l2 3" fill="none" stroke="{c}" stroke-width="2"/>',
    "database": '<ellipse cx="12" cy="6" rx="8" ry="3" fill="none" stroke="{c}" stroke-width="2"/><path d="M4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6" fill="none" stroke="{c}" stroke-width="2"/><path d="M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3" fill="none" stroke="{c}" stroke-width="2"/>',
    "settings": '<circle cx="12" cy="12" r="3" fill="none" stroke="{c}" stroke-width="2"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M19.1 4.9L17 7M7 17l-2.1 2.1" stroke="{c}" stroke-width="2"/>',
    "user": '<circle cx="12" cy="8" r="4" fill="none" stroke="{c}" stroke-width="2"/><path d="M4 21c0-4 3.6-6 8-6s8 2 8 6" fill="none" stroke="{c}" stroke-width="2"/>',
    "lock": '<rect x="5" y="10" width="14" height="11" rx="2" fill="none" stroke="{c}" stroke-width="2"/><path d="M8 10V7a4 4 0 018 0v3" fill="none" stroke="{c}" stroke-width="2"/>',
    "unlock": '<rect x="5" y="10" width="14" height="11" rx="2" fill="none" stroke="{c}" stroke-width="2"/><path d="M8 10V7a4 4 0 017.5-2" fill="none" stroke="{c}" stroke-width="2"/>',
    "export": '<path d="M12 3v12M7 8l5-5 5 5" fill="none" stroke="{c}" stroke-width="2"/><path d="M4 15v4a2 2 0 002 2h12a2 2 0 002-2v-4" fill="none" stroke="{c}" stroke-width="2"/>',
    "import": '<path d="M12 15V3M7 10l5 5 5-5" fill="none" stroke="{c}" stroke-width="2"/><path d="M4 15v4a2 2 0 002 2h12a2 2 0 002-2v-4" fill="none" stroke="{c}" stroke-width="2"/>',
    "refresh": '<path d="M20 12a8 8 0 11-2.3-5.7M20 3v5h-5" fill="none" stroke="{c}" stroke-width="2"/>',
    "warning": '<path d="M12 3L2 21h20z" fill="none" stroke="{c}" stroke-width="2"/><path d="M12 9v6M12 18v.5" stroke="{c}" stroke-width="2"/>',
    "check": '<path d="M4 12l5 5L20 6" fill="none" stroke="{c}" stroke-width="2.5"/>',
    "close": '<path d="M6 6l12 12M18 6L6 18" stroke="{c}" stroke-width="2.5"/>',
    "search": '<circle cx="10" cy="10" r="6" fill="none" stroke="{c}" stroke-width="2"/><path d="M15 15l6 6" stroke="{c}" stroke-width="2"/>',
    "plus": '<path d="M12 5v14M5 12h14" stroke="{c}" stroke-width="2.5"/>',
    "minus": '<path d="M5 12h14" stroke="{c}" stroke-width="2.5"/>',
    "trash": '<path d="M4 7h16M9 7V4h6v3M6 7l1 14h10l1-14" fill="none" stroke="{c}" stroke-width="2"/>',
    "edit": '<path d="M4 20l4-1L20 7l-3-3L5 16z" fill="none" stroke="{c}" stroke-width="2"/><path d="M14 6l3 3" stroke="{c}" stroke-width="2"/>',
    "chart": '<path d="M4 20V10M10 20V4M16 20v-8M22 20H2" fill="none" stroke="{c}" stroke-width="2"/>',
    "history": '<path d="M12 8v4l3 3" fill="none" stroke="{c}" stroke-width="2"/><path d="M3.5 12A8.5 8.5 0 1012 3.5C7.9 3.5 4.6 6 3.5 9.5M3 4v5h5" fill="none" stroke="{c}" stroke-width="2"/>',
    "network": '<circle cx="12" cy="12" r="9" fill="none" stroke="{c}" stroke-width="2"/><path d="M3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18" fill="none" stroke="{c}" stroke-width="1.5"/>',
    "printer": '<rect x="3" y="9" width="18" height="8" rx="2" fill="none" stroke="{c}" stroke-width="2"/><path d="M7 9V4h10v5M7 17v3h10v-3" fill="none" stroke="{c}" stroke-width="2"/><circle cx="18" cy="13" r="1" fill="{c}"/>',
    "qr": '<rect x="3" y="3" width="7" height="7" fill="none" stroke="{c}" stroke-width="2"/><rect x="14" y="3" width="7" height="7" fill="none" stroke="{c}" stroke-width="2"/><rect x="3" y="14" width="7" height="7" fill="none" stroke="{c}" stroke-width="2"/><path d="M14 14h3v3h-3zM19 19h2v2h-2z" fill="{c}"/>',
    "datamatrix": '<rect x="4" y="4" width="16" height="16" fill="none" stroke="{c}" stroke-width="2"/><path d="M8 8h3v3H8zM13 13h3v3h-3zM13 8h3v2h-3zM8 14h2v2H8z" fill="{c}"/>',
    "code128": '<path d="M3 5v14M6 5v14M8 5v10M11 5v14M14 5v10M16 5v14M19 5v14M21 5v10" stroke="{c}" stroke-width="1.6"/>',
    "dashboard": '<rect x="3" y="3" width="8" height="8" rx="1.5" fill="none" stroke="{c}" stroke-width="2"/><rect x="13" y="3" width="8" height="5" rx="1.5" fill="none" stroke="{c}" stroke-width="2"/><rect x="13" y="10" width="8" height="11" rx="1.5" fill="none" stroke="{c}" stroke-width="2"/><rect x="3" y="13" width="8" height="8" rx="1.5" fill="none" stroke="{c}" stroke-width="2"/>',
    "box": '<path d="M12 3l8 4v10l-8 4-8-4V7z" fill="none" stroke="{c}" stroke-width="2"/><path d="M4 7l8 4 8-4M12 11v10" fill="none" stroke="{c}" stroke-width="2"/>',
    "tag": '<path d="M3 12l9-9h8v8l-9 9z" fill="none" stroke="{c}" stroke-width="2"/><circle cx="16" cy="8" r="1.6" fill="{c}"/>',
    "file": '<path d="M6 2h8l5 5v15H6z" fill="none" stroke="{c}" stroke-width="2"/><path d="M14 2v5h5" fill="none" stroke="{c}" stroke-width="2"/>',
}


def icon(name: str, color: str = "#DCE8F5", size: int = 24) -> QIcon:
    """Bangun QIcon dari SVG string internal."""
    body = _PATHS.get(name, _PATHS["tag"]).replace("{c}", color)
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
           f'width="{size}" height="{size}">{body}</svg>')
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(0)
    painter = QPainter(img)
    renderer.render(painter)
    painter.end()
    return QIcon(QPixmap.fromImage(img))


def recolor_paths(color: str) -> dict[str, str]:
    """Semua ikon dengan warna tertentu (untuk tema accent)."""
    out = {}
    for k in _PATHS:
        try:
            out[k] = icon(k, color)
        except Exception:  # pragma: no cover - Svg module optional di CI headless
            out[k] = QIcon()
    return out
