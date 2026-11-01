"""Widget reusable SERIALX: StatTile, Sparkline, NeonProgressBar, ClawLogo, TitleBar."""
from __future__ import annotations

import math
from typing import Callable

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (QColor, QFont, QLinearGradient, QPainter, QPainterPath,
                           QPen, QRadialGradient)
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
                               QVBoxLayout, QWidget)

from ui.theme.effects import apply_glow, draw_claw
from ui.theme.tokens import P, SZ


class ClawLogo(QWidget):
    """Logo claw-slash neon (QPainter), dipakai di title bar & splash."""

    def __init__(self, size: int = 28, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._size = size

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        draw_claw(p, QRectF(0, 0, self._size, self._size), P.neon, self._size / 28.0)
        p.end()


class StatTile(QFrame):
    """Kartu HUD: label kecil, nilai monospace neon besar, subteks, optional ring progress."""

    def __init__(self, label: str, value: str = "0", sub: str = "",
                 ring_fn: Callable[[], float] | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.ring_fn = ring_fn
        lay = QVBoxLayout(self)
        lay.setContentsMargins(SZ.pad_m, SZ.pad_s + 2, SZ.pad_m, SZ.pad_s + 2)
        self._lbl = QLabel(label.upper())
        self._lbl.setObjectName("StatLabel")
        self._val = QLabel(value)
        self._val.setObjectName("StatValue")
        self._sub = QLabel(sub)
        self._sub.setObjectName("Subtle")
        self._sub.setStyleSheet("color:#5C7494; background:transparent; border:none; font-size:11px;")
        lay.addWidget(self._lbl)
        lay.addWidget(self._val)
        lay.addWidget(self._sub)
        if ring_fn:
            self._ring = _ProgressRing(46)
            lay.insertWidget(1, self._ring, alignment=Qt.AlignmentFlag.AlignRight)

    def set_value(self, v: str, sub: str = "") -> None:
        self._val.setText(v)
        if sub:
            self._sub.setText(sub)


class _ProgressRing(QWidget):
    def __init__(self, d: int, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedSize(d, d)
        self._frac = 0.0

    def set_fraction(self, f: float) -> None:
        self._frac = max(0.0, min(1.0, f))
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect().adjusted(3, 3, -3, -3).getRect().__class__(*([0] * 0)) if False else QRectF(self.rect()).adjusted(3, 3, -3, -3)
        p.setPen(QPen(QColor(P.navy_600), 5))
        p.drawArc(r, 0, 360 * 16)
        p.setPen(QPen(QColor(P.neon), 5, cap=Qt.PenCapStyle.RoundCap))
        p.drawArc(r, 90 * 16, int(-self._frac * 360 * 16))
        p.setPen(QColor(P.text))
        p.setFont(QFont("Consolas", 9))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, f"{int(self._frac * 100)}%")
        p.end()


class Sparkline(QWidget):
    """Grafik garis 24 jam custom QPainter — area gradient neon, tanpa matplotlib."""

    def __init__(self, parent: QWidget | None = None, height: int = 90):
        super().__init__(parent)
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._data: list[float] = []

    def set_data(self, data: list[float]) -> None:
        self._data = list(data)
        self.update()

    def append(self, v: float, keep: int = 96) -> None:
        self._data.append(v)
        if len(self._data) > keep:
            self._data = self._data[-keep:]
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor(P.navy_900))
        data = self._data or [0.0]
        mx = max(max(data), 1.0)
        step = w / max(1, len(data) - 1)
        pts = [QPointF(i * step, h - 6 - (v / mx) * (h - 14)) for i, v in enumerate(data)]
        path = QPainterPath()
        if pts:
            path.moveTo(pts[0].x(), h)
            for pt in pts:
                path.lineTo(pt)
            path.lineTo(pts[-1].x(), h)
            path.closeSubpath()
            grad = QLinearGradient(0, 0, 0, h)
            c1 = QColor(P.neon)
            c1.setAlphaF(0.35)
            c2 = QColor(P.neon)
            c2.setAlphaF(0.02)
            grad.setColorAt(0, c1)
            grad.setColorAt(1, c2)
            p.fillPath(path, grad)
            line = QPen(QColor(P.neon), 2)
            p.setPen(line)
            pl = QPainterPath()
            pl.moveTo(pts[0])
            for pt in pts[1:]:
                pl.lineTo(pt)
            p.drawPath(pl)
            last = pts[-1]
            glowc = QColor(P.neon)
            glowc.setAlphaF(0.5)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(glowc)
            p.drawEllipse(last, 6, 6)
            p.setBrush(QColor(P.neon_bright))
            p.drawEllipse(last, 2.5, 2.5)
        p.setPen(QColor(P.text_mute))
        p.drawText(6, 12, f"peak {int(mx)}")
        p.end()


class NeonProgressBar(QFrame):
    """Progress custom: track navy gelap, chunk neon bergaris diagonal + % monospace."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedHeight(22)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._value = 0
        self._max = 100

    def set_range(self, maximum: int) -> None:
        self._max = max(1, maximum)
        self.update()

    def set_value(self, v: int) -> None:
        self._value = max(0, min(self._max, v))
        self.update()

    @property
    def fraction(self) -> float:
        return self._value / self._max

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(QColor(P.line_strong), 1))
        p.setBrush(QColor(P.navy_900))
        p.drawRoundedRect(r, 6, 6)
        frac = self.fraction
        if frac > 0:
            cw = (r.width() - 4) * frac
            chunk = QRectF(r.left() + 2, r.top() + 2, cw, r.height() - 4)
            g = QLinearGradient(chunk.topLeft(), chunk.bottomRight())
            g.setColorAt(0, QColor(P.neon))
            g.setColorAt(1, QColor(P.neon_dim))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(g)
            p.drawRoundedRect(chunk, 4, 4)
            # garis diagonal khas industri
            p.save()
            p.setClipRect(chunk)
            pen = QPen(QColor(4, 7, 14, 90), 3)
            p.setPen(pen)
            x = chunk.left() - chunk.height()
            while x < chunk.right():
                p.drawLine(QPoint(int(x), int(chunk.bottom())),
                           QPoint(int(x + chunk.height()), int(chunk.top())))
                x += 10
            p.restore()
        p.setPen(QColor(P.neon_bright))
        f = QFont("Consolas", SZ.fs_caption)
        p.setFont(f)
        txt = f"{int(frac * 100):3d}% · {self._value}/{self._max}"
        p.drawText(r.adjusted(0, 0, -6, 0), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, txt)
        p.end()


class PrimaryButton(QPushButton):
    """Tombol primer neon dengan glow-on-hover animatif."""

    def __init__(self, text: str, icon_name: str = "play", parent: QWidget | None = None):
        super().__init__(text, parent)
        self.setObjectName("Primary")
        from ui.theme.icons import icon
        self.setIcon(icon(icon_name, P.on_neon))
        apply_glow(self, P.neon, blur=12)
        from ui.theme.effects import animate_glow_on_hover
        animate_glow_on_hover(self, base=12, peak=26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)


class SectionHeader(QHBoxLayout):
    def __init__(self, title: str, right_widget: QWidget | None = None):
        super().__init__()
        lbl = QLabel(title)
        lbl.setObjectName("SectionTitle")
        self.addWidget(lbl)
        self.addStretch(1)
        if right_widget:
            self.addWidget(right_widget)


class Kvp(QLabel):
    """Label key:value monospace untuk panel mesin/job."""

    def __init__(self, k: str, v: str = "—"):
        super().__init__(f"{k}: {v}")
        self.setStyleSheet("background:transparent; border:none; color:#8FA6C2;")

    def set_v(self, v: str) -> None:
        k = self.text().split(":")[0]
        self.setText(f"{k}: {v}")
