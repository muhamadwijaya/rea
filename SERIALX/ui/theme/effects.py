"""Efek visual ABYSS NEON: glow, LED, animated border, scanline, toast, splash."""
from __future__ import annotations

import math
import random

from PySide6.QtCore import (QEasingCurve, QObject, QPropertyAnimation, QRectF, Qt,
                            QTimer)
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import (QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel,
                               QVBoxLayout, QWidget)

from ui.theme.tokens import P, Palette


def apply_glow(widget: QWidget, color: str | None = None, blur: int = 18,
               dy: int = 0) -> QGraphicsDropShadowEffect:
    """Pasang efek glow neon pada widget; kembalikan effect agar bisa dianimasikan."""
    pal = color or P.neon
    eff = QGraphicsDropShadowEffect(widget)
    eff.setBlurRadius(blur)
    eff.setColor(QColor(pal))
    eff.setOffset(0, dy)
    widget.setGraphicsEffect(eff)
    return eff


def animate_glow_on_hover(btn: QWidget, base: int = 12, peak: int = 26) -> None:
    """Hover → blur radius glow membesar (QPropertyAnimation)."""
    eff = btn.graphicsEffect()
    if not isinstance(eff, QGraphicsDropShadowEffect):
        eff = apply_glow(btn, blur=base)
    anim_in = QPropertyAnimation(eff, b"blurRadius", btn)
    anim_in.setDuration(140)
    anim_in.setStartValue(base)
    anim_in.setEndValue(peak)
    anim_out = QPropertyAnimation(eff, b"blurRadius", btn)
    anim_out.setDuration(220)
    anim_out.setStartValue(peak)
    anim_out.setEndValue(base)

    def _enter() -> None:
        anim_out.stop()
        anim_in.stop()
        anim_in.start()

    def _leave() -> None:
        anim_in.stop()
        anim_out.stop()
        anim_out.start()

    btn.enterEvent = lambda e: (_enter(), QWidget.enterEvent(btn, e))[1]  # type: ignore[assignment]
    btn.leaveEvent = lambda e: (_leave(), QWidget.leaveEvent(btn, e))[1]  # type: ignore[assignment]


class LedIndicator(QWidget):
    """LED status bulat dengan radial gradient + bloom, bisa berdenyut saat RUNNING."""

    STATES = {"IDLE": P.idle, "RUNNING": P.running, "PAUSED": P.paused, "ERROR": P.err}

    def __init__(self, state: str = "IDLE", diameter: int = 12, parent: QWidget | None = None):
        super().__init__(parent)
        self._state = state.upper()
        self._d = diameter
        self._pulse = 1.0
        self.setFixedSize(diameter + 10, diameter + 10)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._t = 0.0
        self.update_state(self._state)

    def _tick(self) -> None:
        self._t += 0.18
        self._pulse = 0.75 + 0.25 * abs(math.sin(self._t))
        self.update()

    def update_state(self, state: str) -> None:
        state = state.upper()
        if state not in self.STATES:
            state = "IDLE"
        self._state = state
        if state == "RUNNING":
            self._timer.start(90)
        else:
            self._timer.stop()
            self._pulse = 1.0
        self.update()

    @property
    def color(self) -> str:
        return self.STATES[self._state]

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QColor(self.color)
        cx, cy = self.width() / 2, self.height() / 2
        r = self._d / 2
        # bloom luar
        bloom = QRadialGradient(cx, cy, r * 3.2)
        outer = QColor(c)
        outer.setAlphaF(0.35 * self._pulse)
        bloom.setColorAt(0.0, outer)
        mid = QColor(c)
        mid.setAlphaF(0.12 * self._pulse)
        bloom.setColorAt(0.55, mid)
        bloom.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setBrush(bloom)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QRectF(cx - r * 3.2, cy - r * 3.2, r * 6.4, r * 6.4))
        # badan LED
        body = QRadialGradient(cx - r * 0.3, cy - r * 0.3, r * 1.6)
        hi = c.lighter(180)
        body.setColorAt(0.0, hi)
        body.setColorAt(0.5, c)
        body.setColorAt(1.0, c.darker(170))
        p.setBrush(body)
        p.setPen(QPen(c.darker(220), 1))
        p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))
        p.end()


class AnimatedBorderFrame(QFrame):
    """Frame dengan border gradien bergerak navy→neon→navy saat aktif (RUNNING)."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.active = False
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.setInterval(40)

    def set_active(self, on: bool) -> None:
        self.active = on
        if on:
            self._timer.start()
        else:
            self._timer.stop()
        self.update()

    def _tick(self) -> None:
        self._phase = (self._phase + 0.05) % 1.0
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        if not self.active:
            return
        p = QPainter(self)
        r = self.rect().adjusted(1, 1, -1, -1)
        w = self.width()
        for i in range(max(2, w // 6)):
            t = (i / max(2, w // 6) + self._phase) % 1.0
            col = QColor(P.neon)
            col.setAlphaF(0.25 + 0.75 * abs((t * 2) % 2 - 1))
            p.setPen(QPen(col, 2))
            x = int(t * (w + 40)) - 20
            p.drawLine(x, r.top(), x + 10, r.top())
            p.drawLine(w - x - 10, r.bottom(), w - x, r.bottom())
        p.setPen(QPen(QColor(P.neon_dim), 1))
        p.drawRoundedRect(r, 10, 10)
        p.end()


class ScanlineOverlay(QObject):
    """Overlay scanline halus 4% di atas widget preview — kesan mesin industri."""

    def __init__(self, widget: QWidget):
        super().__init__(widget)
        self._w = widget
        widget.installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:  # noqa: ANN001
        if obj is self._w and event.type().name == "Paint":
            # digambar oleh LabelPreview itu sendiri (lihat widgets.py)
            pass
        return False

    @staticmethod
    def paint(painter: QPainter, rect: QRectF) -> None:
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 10))  # ~4% opacity
        y = rect.top()
        while y < rect.bottom():
            painter.drawRect(QRectF(rect.left(), y, rect.width(), 1))
            y += 3
        painter.restore()


class Toast(QWidget):
    """Notifikasi melayang kanan-bawah: border kiri neon 3px, slide+fade, auto 3 dtk."""

    def __init__(self, text: str, kind: str = "info", parent: QWidget | None = None):
        super().__init__(parent)
        colors = {"info": P.info, "ok": P.neon, "warn": P.warn, "err": P.err}
        accent = colors.get(kind, P.neon)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(340)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        card = QFrame()
        card.setObjectName("Card")
        card.setStyleSheet(
            f"#Card {{ background: {P.navy_800}; border: 1px solid {P.line_strong};"
            f" border-left: 3px solid {accent}; border-radius: {P and 10}px; }}")
        cl = QHBoxLayout(card)
        cl.setContentsMargins(12, 10, 12, 10)
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setStyleSheet(f"color:{P.text}; background:transparent; border:none;")
        cl.addWidget(lbl)
        lay.addWidget(card)
        apply_glow(card, accent, blur=16)
        self._anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._anim.setDuration(260)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.start()
        QTimer.singleShot(3000, self._fade_out)

    def _fade_out(self) -> None:
        self._anim.stop()
        self._anim.setDirection(QPropertyAnimation.Direction.Backward)
        self._anim.start()
        QTimer.singleShot(280, self.deleteLater)

    def show_at(self, area_x: int, area_y: int) -> None:
        self.move(area_x - self.width() - 16, area_y - self.height() - 16)
        self.show()
        self.raise_()


class ToastHost:
    """Pengelola tumpukan toast pada sebuah container."""

    def __init__(self, container: QWidget):
        self.container = container
        self._stack: list[Toast] = []

    def push(self, text: str, kind: str = "info") -> None:
        t = Toast(text, kind, self.container)
        self._stack = [x for x in self._stack if x.isVisible()]
        base_y = self.container.height() - 60
        for i, old in enumerate(reversed(self._stack)):
            pass
        offset = len(self._stack) * 64
        t.show_at(self.container.width(), base_y - offset)
        self._stack.append(t)
        QTimer.singleShot(3400, lambda: self._stack.remove(t) if t in self._stack else None)


def draw_claw(painter: QPainter, rect: QRectF, color: str = P.neon, scale: float = 1.0) -> None:
    """Logo 'claw-slash': tiga cakar diagonal neon, digambar via QPainter."""
    p = painter
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    c = QColor(color)
    pen = QPen(c, max(2.0, 3.2 * scale))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    w, h = rect.width(), rect.height()
    for i, (dx, dw) in enumerate([(0.12, 0.16), (0.34, 0.22), (0.58, 0.28)]):
        pen.setWidthF(max(2.0, (2.2 + i * 1.4) * scale))
        p.setPen(pen)
        x0 = rect.left() + w * dx
        p.drawLine(QPoint_(int(x0), int(rect.top() + h * 0.78)),
                   QPoint_(int(x0 + w * dw), int(rect.top() + h * 0.15)))
    p.restore()


# helper kecil agar tidak import QPoint terpisah
from PySide6.QtCore import QPoint as _QP  # noqa: E402


def QPoint_(x: int, y: int):  # noqa: N802
    return _QP(x, y)
