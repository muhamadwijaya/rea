"""Halaman Print Queue & Simulator cetak (§3.6)."""
from __future__ import annotations

import json
from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QAbstractTableModel, QHBoxLayout, QHeaderView, QLabel,
                               QMessageBox, QPushButton, QSpinBox, QTableView,
                               QVBoxLayout, QWidget)

from ui import db
from ui.label_render import render_label
from ui.theme.effects import LedIndicator
from ui.theme.icons import icon
from ui.theme.tokens import P, SZ


class QueueModel(QAbstractTableModel):
    COLS = ["ID", "Serial", "Status", "Station", "Waktu", "Retry"]

    def __init__(self, con):
        super().__init__()
        self.con = con
        self.rows: list = []
        self.reload()

    def reload(self) -> None:
        self.beginResetModel()
        with db._LOCK:
            self.rows = self.con.execute(
                "SELECT id, serial, status, station, COALESCE(sent_at, created_at), retry "
                "FROM print_queue ORDER BY id DESC LIMIT 500").fetchall()
        self.endResetModel()

    def rowCount(self, parent=None) -> int:
        return len(self.rows)

    def columnCount(self, parent=None) -> int:
        return len(self.COLS)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and index.isValid():
            v = str(self.rows[index.row()][index.column()] or "")
            return v[:19]
        if role == Qt.ItemDataRole.ForegroundRole and index.column() == 2:
            s = str(self.rows[index.row()][2])
            col = {"PRINTED": P.ok, "QUEUED": P.info, "SENT": P.paused,
                   "FAILED": P.err}.get(s, P.text_dim)
            from PySide6.QtGui import QColor
            return QColor(col)
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole:
            return self.COLS[section] if orientation == Qt.Orientation.Horizontal else str(section + 1)
        return None


class PrintQueuePage(QWidget):
    """Antrian cetak + simulator render label + reprint/batch/pacing."""

    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.con = ctx.con
        lay = QVBoxLayout(self)
        lay.setContentsMargins(SZ.pad_l, SZ.pad_l, SZ.pad_l, SZ.pad_l)

        head = QHBoxLayout()
        t = QLabel("PRINT QUEUE & SIMULATOR")
        t.setObjectName("SectionTitle")
        self.led = LedIndicator("IDLE")
        self.lbl_backend = QLabel(f"Backend: {ctx.backend.mode}")
        self.lbl_backend.setObjectName("Subtle")
        head.addWidget(t)
        head.addWidget(self.led)
        head.addStretch(1)
        head.addWidget(self.lbl_backend)
        lay.addLayout(head)

        body = QHBoxLayout()
        self.view = QTableView()
        self.model = QueueModel(ctx.con)
        self.view.setModel(self.model)
        self.view.setAlternatingRowColors(True)
        self.view.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.view.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        body.addWidget(self.view, 3)

        sim = QWidget()
        sim.setObjectName("Card")
        sl = QVBoxLayout(sim)
        sl.addWidget(QLabel("SIMULATOR CETAK — label terbaru"))
        self.sim_img = QLabel("(belum ada cetakan)")
        self.sim_img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sim_img.setMinimumSize(300, 340)
        sl.addWidget(self.sim_img, 1)
        self.sim_meta = QLabel("—")
        self.sim_meta.setObjectName("MonoText")
        self.sim_meta.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sl.addWidget(self.sim_meta)
        body.addWidget(sim, 2)
        lay.addLayout(body, 1)

        tools = QHBoxLayout()
        b_refresh = QPushButton("Refresh")
        b_refresh.setIcon(icon("refresh", P.text, 16))
        b_refresh.clicked.connect(self.reload)
        b_reprint = QPushButton("Reprint terpilih")
        b_reprint.setIcon(icon("history", P.text, 16))
        b_reprint.clicked.connect(self._reprint)
        self.sp_batch = QSpinBox(); self.sp_batch.setRange(1, 100); self.sp_batch.setValue(10)
        self.sp_delay = QSpinBox(); self.sp_batch.setSuffix(""); self.sp_delay.setRange(0, 5000)
        self.sp_delay.setSuffix(" ms"); self.sp_delay.setValue(80)
        lbl_b = QLabel("Batch:"); lbl_d = QLabel("Pacing:")
        for w in (lbl_b, self.sp_batch, lbl_d, self.sp_delay):
            w.setStyleSheet("background:transparent;border:none;")
        b_send = QPushButton("Kirim batch (simulasi)")
        b_send.setIcon(icon("print", P.text, 16))
        b_send.clicked.connect(self._send_batch)
        for w in (b_refresh, b_reprint, b_send):
            w.setToolTip(w.text())
        tools.addWidget(b_refresh)
        tools.addWidget(b_reprint)
        tools.addWidget(lbl_b)
        tools.addWidget(self.sp_batch)
        tools.addWidget(lbl_d)
        tools.addWidget(self.sp_delay)
        tools.addStretch(1)
        lay.addLayout(tools)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._auto_refresh)
        self.timer.start(2000)
        self.reload()

    def _auto_refresh(self) -> None:
        self.model.reload()
        top = self.model.rows[0] if self.model.rows else None
        if top:
            self.led.update_state("RUNNING" if top[2] == "QUEUED" else "IDLE")
            if top[2] == "PRINTED":
                self._show_sim(top[1])

    def reload(self) -> None:
        self.model.reload()
        if self.model.rows:
            self._show_sim(self.model.rows[0][1])

    def _show_sim(self, serial: str) -> None:
        try:
            row = self.con.execute(
                "SELECT j.template_json, p.sku, j.lot FROM serial_records s "
                "JOIN jobs j ON j.id=s.job_id JOIN products p ON p.id=j.product_id "
                "WHERE s.serial=?", (serial,)).fetchone()
            if not row:
                return
            tmpl = json.loads(row["template_json"] or "{}")
            ctx = {"serial": serial, "sku": row["sku"], "lot": row["lot"] or "",
                   "exp": (datetime.now()).strftime("%d-%m-%Y"),
                   "date": datetime.now().strftime("%d-%m-%Y"),
                   "weight": "330 ml", "origin": "ID"}
            img = render_label(tmpl, ctx, scale=3.0)
            self.sim_img.setPixmap(QPixmap.fromImage(img))
            self.sim_meta.setText(serial)
        except Exception as exc:
            self.sim_img.setText(f"render error: {exc}")

    def _reprint(self) -> None:
        idx = self.view.currentIndex()
        if not idx.isValid():
            QMessageBox.information(self, "Reprint", "Pilih satu baris antrian.")
            return
        serial = str(self.model.rows[idx.row()][1])
        zpl = self.ctx.backend.call("zpl_label", {"serial": serial, "sku": "REPRINT",
                                                  "lot": "", "exp": ""})["zpl"]
        with db._LOCK:
            self.con.execute(
                "INSERT INTO print_queue(job_id,serial,zpl,status,station,retry) "
                "VALUES((SELECT job_id FROM serial_records WHERE serial=?),?,?, 'QUEUED',?,0)",
                (serial, serial, zpl, self.ctx.station))
            self.con.execute("UPDATE serial_records SET reprint_count=reprint_count+1 WHERE serial=?",
                             (serial,))
            db.audit(self.con, self.ctx.user, "REPRINT", "serial_records", "", serial)
            self.con.commit()
        self.ctx.toast.push(f"Reprint '{serial}' masuk antrian.", "warn")
        self.reload()

    def _send_batch(self) -> None:
        n = self.sp_batch.value()
        delay = self.sp_delay.value()
        rows = self.con.execute(
            "SELECT id,serial FROM print_queue WHERE status='QUEUED' ORDER BY id LIMIT ?",
            (n,)).fetchall()
        if not rows:
            self.ctx.toast.push("Tidak ada item QUEUED utk dikirim.", "info")
            return
        sent = 0
        for r in rows:
            try:
                # simulasi kirim + ack "~TA" dari printer
                with db._LOCK:
                    self.con.execute(
                        "UPDATE print_queue SET status='PRINTED', sent_at=?, retry=retry WHERE id=?",
                        (db.now_iso(), r["id"]))
                    self.con.execute(
                        "UPDATE serial_records SET status='PRINTED', printed_at=? WHERE serial=?",
                        (db.now_iso(), r["serial"]))
                    self.con.commit()
                sent += 1
                QTimer.singleShot(delay * (sent), lambda: None)  # pacing (non-blocking noop)
            except Exception:
                with db._LOCK:
                    self.con.execute("UPDATE print_queue SET status='FAILED', retry=retry+1 WHERE id=?",
                                     (r["id"],))
                    self.con.commit()
        db.audit(self.con, self.ctx.user, "BATCH_SEND", "print_queue", "", f"{sent} label",
                 f"pacing {delay}ms")
        self.ctx.toast.push(f"Terkirim {sent}/{len(rows)} label ke simulator.", "ok")
        self.reload()
