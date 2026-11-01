"""Halaman peluncur Job Wizard — daftar job + tombol new/clone/start."""
from __future__ import annotations

import json

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QHBoxLayout, QHeaderView, QLabel, QMessageBox,
                               QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from ui import db
from ui.pages.job_wizard import JobWizard
from ui.theme.effects import LedIndicator
from ui.theme.icons import icon
from ui.theme.tokens import P, SZ


class JobWizardLauncherPage(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.con = ctx.con
        lay = QVBoxLayout(self)
        lay.setContentsMargins(SZ.pad_l, SZ.pad_l, SZ.pad_l, SZ.pad_l)
        head = QHBoxLayout()
        t = QLabel("JOB SETUP")
        t.setObjectName("SectionTitle")
        b_new = QPushButton("  Job Baru (Ctrl+N)")
        b_new.setIcon(icon("plus", P.on_neon, 18))
        b_new.setObjectName("Primary")
        b_new.clicked.connect(lambda: self._wizard(None))
        b_clone = QPushButton("Duplikasi terpilih")
        b_clone.setIcon(icon("file", P.text, 16))
        b_clone.clicked.connect(lambda: self._wizard(self._selected_id()))
        for b in (b_new, b_clone):
            b.setToolTip(b.text().strip())
        head.addWidget(t); head.addStretch(1); head.addWidget(b_new); head.addWidget(b_clone)
        lay.addLayout(head)

        self.tbl = QTableWidget(0, 9)
        self.tbl.setHorizontalHeaderLabels(
            ["ID", "Nama", "Produk", "Pola", "Lot", "Target", "Last", "Status", "Aksi"])
        self.tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.tbl.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.tbl.setAlternatingRowColors(True)
        lay.addWidget(self.tbl, 1)

        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self.refresh)
        self.refresh_timer.start(2500)
        self.refresh()

    def _selected_id(self) -> int | None:
        r = self.tbl.currentRow()
        if r < 0:
            return None
        try:
            return int(self.tbl.item(r, 0).text())
        except Exception:
            return None

    def refresh(self) -> None:
        rows = self.con.execute(
            "SELECT j.id,j.name,p.sku,j.pattern,j.lot,j.target_qty,j.last_seq,j.status "
            "FROM jobs j LEFT JOIN products p ON p.id=j.product_id ORDER BY j.id").fetchall()
        self.tbl.setRowCount(len(rows))
        for i, r in enumerate(rows):
            vals = [r["id"], r["name"], r["sku"] or "", r["pattern"], r["lot"] or "",
                    r["target_qty"], r["last_seq"], r["status"]]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(str(v))
                if c == 7:
                    col = {"RUNNING": P.ok, "DRAFT": P.info, "PAUSED": P.paused,
                           "DONE": P.text_dim}.get(str(v), P.text)
                    it.setForeground(Qt.GlobalColor.white)
                    from PySide6.QtGui import QColor
                    it.setForeground(QColor(col))
                self.tbl.setItem(i, c, it)
            btns = QWidget(); bl = QHBoxLayout(btns); bl.setContentsMargins(2, 2, 2, 2)
            b_start = QPushButton("START"); b_start.setObjectName("Primary")
            b_stop = QPushButton("STOP"); b_stop.setObjectName("Danger")
            b_edit = QPushButton(""); b_edit.setIcon(icon("edit", P.text, 14))
            b_start.clicked.connect(lambda _=False, rid=r["id"]: self.ctx.start_job(rid))
            b_stop.clicked.connect(lambda _=False, rid=r["id"]: self.ctx.stop_job(rid))
            b_edit.clicked.connect(lambda _=False, rid=r["id"]: self._wizard(rid))
            bl.addWidget(b_start); bl.addWidget(b_stop); bl.addWidget(b_edit)
            self.tbl.setCellWidget(i, 8, btns)

    def _wizard(self, clone_or_edit_id: int | None) -> None:
        if self.ctx.role == "viewer":
            QMessageBox.warning(self, "Akses", "Role viewer tidak boleh membuat job.")
            return
        wiz = JobWizard(self.ctx, self, clone_from=clone_or_edit_id)
        if wiz.exec() == QDialog_OK():
            self.refresh()


def QDialog_OK() -> int:
    from PySide6.QtWidgets import QDialog
    return int(QDialog.DialogCode.Accepted)
