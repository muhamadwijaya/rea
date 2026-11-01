"""Renderer label SERIALX: template JSON → QImage (Code128 & QR digambar native QPainter).

Tidak butuh dependensi eksternal: Code128-B encoder + QR model-2 encoder
disimpan di ui/qr.py. Untuk produksi, ganti dengan ZPL dari backend C#.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ui.qr import draw_code128, draw_qr
from ui.theme.tokens import P


def bind_fields(text: str, ctx: dict[str, str]) -> str:
    """Ganti {{serial}} {{sku}} {{lot}} {{exp}} {{date}} {{weight}} {{origin}}."""
    def sub(m: re.Match) -> str:
        return str(ctx.get(m.group(1), m.group(0)))
    return re.sub(r"\{\{(\w+)\}\}", sub, text)


def render_label(template: dict, ctx: dict[str, str], scale: float = 3.0) -> QImage:
    """Render template ke QImage. scale = pixel per mm (3 ≈ preview, 11.8 ≈ 300dpi)."""
    w_mm, h_mm = template.get("size_mm", [100, 150])
    img = QImage(int(w_mm * scale), int(h_mm * scale), QImage.Format.Format_ARGB32)
    img.fill(QColor("#FFFFFF"))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    mm = lambda v: v * scale  # noqa: E731

    for el in template.get("elements", []):
        t = el.get("type")
        x, y = mm(el.get("x", 0)), mm(el.get("y", 0))
        if t == "text":
            content = bind_fields(el.get("content", ""), ctx)
            pt = el.get("font_pt", 10)
            f = QFont("Courier" if el.get("mono") else "DejaVu Sans", int(pt * scale / 4.0))
            f.setBold(bool(el.get("bold")))
            p.setFont(f)
            col = el.get("color", "#0B1B34")
            p.setPen(QColor(col))
            p.drawText(QPointF(x, y + mm(4)), content)
        elif t == "code128":
            content = bind_fields(el.get("content", ""), ctx)
            hpx = max(10, mm(el.get("h_mm", 12)))
            draw_code128(p, content, int(x), int(y), int(min(img.width() - x - mm(2), mm(62))), int(hpx),
                         human=bool(el.get("human", True)))
        elif t == "qr":
            content = bind_fields(el.get("content", ""), ctx)
            size = mm(el.get("size_mm", 24))
            draw_qr(p, content, int(x), int(y), int(size))
        elif t == "rect":
            pen = QPen(QColor(el.get("pen", P.neon)), max(1, scale * 0.4))
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(int(x), int(y), int(mm(el.get("w", 10))), int(mm(el.get("h", 10))))
        elif t == "line":
            p.setPen(QPen(QColor(el.get("pen", "#0B1B34")), max(1, scale * 0.3)))
            p.drawLine(int(x), int(y), int(x + mm(el.get("w", 10))), int(y + mm(el.get("h", 0))))
    p.end()
    return img


def demo_ctx(job_row, serial: str, sig: str = "") -> dict[str, str]:
    """Bangun konteks binding dari row job + satu serial."""
    exp = (datetime.now() + timedelta(days=240)).strftime("%d-%m-%Y")
    return {
        "serial": serial, "sku": getattr(job_row, "keys", lambda: []) and "",
        "lot": "", "exp": exp, "date": datetime.now().strftime("%d-%m-%Y"),
        "weight": "330 ml", "origin": "ID", "sig": sig,
    }
