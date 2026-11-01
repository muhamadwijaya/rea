"""Halaman Reports & Analytics (§3.10) — chart QPainter kustom + ekspor CSV/XLSX/PDF."""
from __future__ import annotations

import csv
from datetime import datetime, timedelta
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtPrintSupport import QPrinter
from PySide6.QtWidgets import (QComboBox, QFileDialog, QHBoxLayout, QLabel,
                               QPushButton, QVBoxLayout, QWidget)

from ui import db
from ui.theme.icons import icon
from ui.theme.tokens import P, SZ


class BarChart(QWidget):
    """Bar chart custom QPainter: produksi per hari N terakhir."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.data: list[tuple[str, int]] = []
        self.setMinimumHeight(200)

    def set_data(self, data: list[tuple[str, int]]) -> None:
        self.data = data
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(40, 16, -12, -28)
        p.fillRect(self.rect(), QColor(P.navy_900))
        if not self.data:
            p.setPen(QColor(P.text_mute)); p.drawText(rect, Qt.AlignmentFlag.AlignCenter, "no data")
            return
        mx = max(v for _, v in self.data) or 1
        bw = rect.width() / len(self.data)
        p.setPen(QPen(QColor(P.line), 1))
        for i in range(5):
            y = rect.top() + rect.height() * i / 4
            p.drawLine(int(rect.left()), int(y), int(rect.right()), int(y))
        for i, (lbl, v) in enumerate(self.data):
            h = rect.height() * (v / mx)
            x = rect.left() + i * bw + bw * 0.15
            g = QColor(P.neon)
            grad_top = g.lighter(120)
            p.setPen(Qt.PenStyle.NoPen)
            from PySide6.QtGui import QLinearGradient
            lg = QLinearGradient(0, rect.bottom() - h, 0, rect.bottom())
            lg.setColorAt(0, grad_top)
            b = QColor(P.neon_dim); lg.setColorAt(1, b)
            p.setBrush(lg)
            p.drawRoundedRect(int(x), int(rect.bottom() - h), int(bw * 0.7), int(h), 3, 3)
            p.setPen(QColor(P.text_dim))
            p.setFont(QFont("Consolas", 8))
            p.drawText(int(x - bw * 0.15), int(rect.bottom() + 14), int(bw), 14,
                       Qt.AlignmentFlag.AlignCenter, lbl[-5:])
            p.setPen(QColor(P.neon_bright))
            p.drawText(int(x), int(rect.bottom() - h - 14), int(bw * 0.7), 12,
                       Qt.AlignmentFlag.AlignCenter, str(v))
        p.setPen(QColor(P.text_mute))
        p.drawText(6, 14, f"max {mx}")
        p.end()


class DonutChart(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.slices: list[tuple[str, int, str]] = []
        self.setMinimumHeight(200)

    def set_data(self, slices):
        self.slices = slices
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(P.navy_900))
        total = sum(v for _, v, _ in self.slices) or 1
        from PySide6.QtCore import QRectF
        r = QRectF(10, 10, min(self.width() // 2 - 20, self.height() - 30),
                   self.height() - 30)
        start = 90 * 16
        for name, v, col in self.slices:
            span = -int(360 * 16 * v / total)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(col))
            p.drawPie(r, start, span)
            start += span
        p.setBrush(QColor(P.navy_900))
        hole = r.adjusted(r.width() * 0.28, r.height() * 0.28, -r.width() * 0.28, -r.height() * 0.28)
        p.drawEllipse(hole)
        y = 24
        p.setFont(QFont("Segoe UI", 9))
        for name, v, col in self.slices:
            p.setPen(QColor(col))
            p.drawText(int(r.right() + 24), y, f"● {name}: {v} ({v * 100 // total}%)")
            y += 18
        p.end()


class ReportsPage(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.con = ctx.con
        lay = QVBoxLayout(self)
        lay.setContentsMargins(SZ.pad_l, SZ.pad_l, SZ.pad_l, SZ.pad_l)
        head = QHBoxLayout()
        t = QLabel("REPORTS & ANALYTICS")
        t.setObjectName("SectionTitle")
        self.cb_preset = QComboBox()
        self.cb_preset.addItems(["Produksi Harian", "Shift Summary", "Reject/Anomaly",
                                 "Serial Usage", "Print Performance"])
        self.cb_days = QComboBox(); self.cb_days.addItems(["7", "14", "30"])
        self.cb_preset.currentIndexChanged.connect(self.refresh)
        self.cb_days.currentIndexChanged.connect(self.refresh)
        head.addWidget(t); head.addStretch(1)
        head.addWidget(QLabel("Preset:")); head.addWidget(self.cb_preset)
        head.addWidget(QLabel("Hari:")); head.addWidget(self.cb_days)
        lay.addLayout(head)

        row = QHBoxLayout()
        self.bar = BarChart()
        bc = QWidget(); bc.setObjectName("Card"); bl = QVBoxLayout(bc)
        bl.addWidget(QLabel("OUTPUT HARIAN")); bl.addWidget(self.bar)
        self.donut = DonutChart()
        dc = QWidget(); dc.setObjectName("Card"); dl = QVBoxLayout(dc)
        dl.addWidget(QLabel("DISTRIBUSI STATUS SERIAL")); dl.addWidget(self.donut)
        row.addWidget(bc, 3); row.addWidget(dc, 2)
        lay.addLayout(row, 1)

        self.tbl = QLabel("")
        self.tbl.setTextFormat(Qt.TextFormat.RichText)
        self.tbl.setStyleSheet(f"background:{P.navy_800}; border:1px solid {P.line};"
                               f"border-radius:{SZ.radius_card}px; padding:10px;"
                               f"font-family:Consolas,monospace; color:{P.text};")
        self.tbl.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(self.tbl, 1)

        btns = QHBoxLayout()
        for label, fn, ic in (("Ekspor CSV", self._export_csv, "export"),
                              ("Ekspor XLSX", self._export_xlsx, "export"),
                              ("Ekspor PDF", self._export_pdf, "print")):
            b = QPushButton(label)
            b.setIcon(icon(ic, P.text, 16))
            b.clicked.connect(fn)
            b.setToolTip(label)
            btns.addWidget(b)
        btns.addStretch(1)
        lay.addLayout(btns)
        self.last_rows: list[tuple] = []
        self.refresh()

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        days = int(self.cb_days.currentText())
        rows = self.con.execute(
            "SELECT day, produced, printed, rejected FROM daily_stats ORDER BY day DESC LIMIT ?",
            (days,)).fetchall()[::-1]
        self.last_rows = [tuple(r) for r in rows]
        self.bar.set_data([(r["day"], r["produced"]) for r in rows])
        st = self.con.execute(
            "SELECT status, COUNT(*) n FROM serial_records GROUP BY status").fetchall()
        colors = {"ISSUED": P.info, "PRINTED": P.neon, "VERIFIED": P.neon_bright,
                  "VOID": P.err}
        self.donut.set_data([(r["status"], r["n"], colors.get(r["status"], P.text_dim)) for r in st])
        html = ("<table width='100%'><tr><td align='left' style='color:%s'>DAY</td>"
                "<td>PRODUCED</td><td>PRINTED</td><td>REJECTED</td></tr>" % P.neon_bright)
        for r in rows[-10:]:
            html += (f"<tr><td>{r['day']}</td><td align='center'>{r['produced']}</td>"
                     f"<td align='center'>{r['printed']}</td>"
                     f"<td align='center' style='color:{P.err}'>{r['rejected']}</td></tr>")
        html += "</table>"
        self.tbl.setText(html)

    def _export_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Ekspor CSV", "report.csv", "CSV (*.csv)")
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["day", "produced", "printed", "rejected"])
            w.writerows(self.last_rows)
        self.ctx.toast.push(f"CSV tersimpan: {path}", "ok")

    def _export_xlsx(self) -> None:
        try:
            from openpyxl import Workbook
        except ImportError:
            self.ctx.toast.push("openpyxl belum di-install (pip install openpyxl).", "warn")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Ekspor XLSX", "report.xlsx", "Excel (*.xlsx)")
        if not path:
            return
        wb = Workbook()
        ws = wb.active
        ws.title = "Report"
        ws.append(["day", "produced", "printed", "rejected"])
        for r in self.last_rows:
            ws.append(list(r))
        wb.save(path)
        self.ctx.toast.push(f"XLSX tersimpan: {path}", "ok")

    def _export_pdf(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Ekspor PDF", "report.pdf", "PDF (*.pdf)")
        if not path:
            return
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setOutputFileName(str(Path(path)))
        painter = QPainter(printer)
        painter.drawText(40, 40, f"SERIALX Report — {datetime.now():%Y-%m-%d %H:%M}")
        self.bar.render(painter) if hasattr(self.bar, "render") else None
        y = 120
        painter.setFont(QFont("Consolas", 11))
        for r in self.last_rows:
            painter.drawText(40, y, f"{r[0]}  produced={r[1]}  printed={r[2]}  rejected={r[3]}")
            y += 20
        painter.end()
        self.ctx.toast.push(f"PDF tersimpan: {path}", "ok")
