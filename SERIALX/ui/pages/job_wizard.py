"""Job Setup Wizard 5 langkah bergaya neon stepper (§3.3)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QComboBox, QDateEdit, QDialog, QDialogButtonBox,
                               QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QMessageBox, QPushButton, QSpinBox, QVBoxLayout, QWidget)

from ui import db
from ui.label_render import render_label
from ui.theme.icons import icon
from ui.theme.tokens import P, SZ
from ui.widgets.common import PrimaryButton


class StepperBar(QWidget):
    """Indikator langkah segmented: 1 Produk → 2 Serial → 3 Label → 4 Cetak → 5 Verifikasi."""

    LABELS = ["1 · PRODUK", "2 · ATURAN SERIAL", "3 · LAYOUT LABEL", "4 · PERANGKAT CETAK", "5 · VERIFIKASI"]

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._cur = 0
        self._lbls: list[QLabel] = []
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(SZ.sp)
        for i, t in enumerate(self.LABELS):
            b = QLabel(t)
            b.setAlignment(Qt.AlignmentFlag.AlignCenter)
            b.setStyleSheet(self._style(i == 0))
            self._lbls.append(b)
            lay.addWidget(b, 1)

    def _style(self, on: bool, done: bool = False) -> str:
        if on:
            return (f"background:{P.neon}; color:{P.on_neon}; border-radius:{SZ.radius_pill}px;"
                    f" padding:7px; font-weight:800;")
        if done:
            return (f"background:{P.navy_700}; color:{P.neon_bright};"
                    f" border:1px solid {P.neon_dim}; border-radius:{SZ.radius_pill}px; padding:7px;")
        return (f"background:{P.navy_900}; color:{P.text_mute}; border:1px solid {P.line};"
                f" border-radius:{SZ.radius_pill}px; padding:7px;")

    def set_step(self, idx: int) -> None:
        self._cur = idx
        for i, b in enumerate(self._lbls):
            b.setStyleSheet(self._style(i == idx, done=i < idx))


class JobWizard(QDialog):
    """Wizard pembuatan job dengan preview live di kanan + validasi keras."""

    def __init__(self, ctx, parent=None, clone_from: int | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self.con = ctx.con
        self.setWindowTitle("Job Setup Wizard")
        self.setMinimumSize(920, 620)
        root = QVBoxLayout(self)
        self.stepper = StepperBar()
        root.addWidget(self.stepper)

        body = QHBoxLayout()
        self.pages_host = QWidget()
        self.stack_layout = QVBoxLayout(self.pages_host)
        body.addWidget(self.pages_host, 3)

        prev_card = QWidget()
        prev_card.setObjectName("Card")
        pv = QVBoxLayout(prev_card)
        pv.addWidget(QLabel("PREVIEW LIVE"))
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(260, 330)
        pv.addWidget(self.preview, 1)
        self.preview_serial = QLabel("—")
        self.preview_serial.setObjectName("MonoText")
        self.preview_serial.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pv.addWidget(self.preview_serial)
        body.addWidget(prev_card, 2)
        root.addLayout(body, 1)

        # ---- page 1 produk ---------------------------------------------------
        p1 = QWidget()
        f1 = QFormLayout(p1)
        self.cb_product = QComboBox()
        for r in self.con.execute("SELECT id,sku,name FROM products ORDER BY sku"):
            self.cb_product.addItem(f"{r['sku']} — {r['name']}", r["id"])
        self.ed_name = QLineEdit(f"JOB-{datetime.now().strftime('%y%m%d')}/01")
        self.sp_target = QSpinBox(); self.sp_target.setRange(1, 10_000_000); self.sp_target.setValue(1000)
        self.cb_shift = QComboBox(); self.cb_shift.addItems(["A", "B", "C"])
        self.ed_line = QLineEdit("L1")
        self.ed_lot = QLineEdit(f"LOT{datetime.now().strftime('%y%m')}")
        self.dt_exp = QDateEdit(); self.dt_exp.setDate(datetime.now() + timedelta(days=240)); self.dt_exp.setCalendarPopup(True)
        self.sp_prio = QSpinBox(); self.sp_prio.setRange(1, 9); self.sp_prio.setValue(5)
        f1.addRow("Nama job *", self.ed_name)
        f1.addRow("Produk *", self.cb_product)
        f1.addRow("Target qty", self.sp_target)
        f1.addRow("Shift", self.cb_shift)
        f1.addRow("Line", self.ed_line)
        f1.addRow("Batch/Lot", self.ed_lot)
        f1.addRow("Tgl kedaluwarsa", self.dt_exp)
        f1.addRow("Prioritas", self.sp_prio)

        # ---- page 2 serial rules ----------------------------------------------
        p2 = QWidget()
        v2 = QVBoxLayout(p2)
        form2 = QFormLayout()
        self.ed_pattern = QLineEdit("SRX{YY}{MM}{DD}-{LINE}{SEQ:6}-{CRC2}")
        self.ed_pattern.setFont(ctx.theme.mono_font())
        self.sp_step = QSpinBox(); self.sp_step.setRange(1, 1000); self.sp_step.setValue(1)
        self.cb_order = QComboBox(); self.cb_order.addItems(["ascending", "descending"])
        self.chk_ambig = QPushButton("Hindari karakter ambigu (I O 0 1): OFF")
        self.chk_ambig.setObjectName("Flat")
        self.chk_ambig.setCheckable(True)
        self.lbl_valid = QLabel("")
        self.lbl_valid.setWordWrap(True)
        form2.addRow("Pola serial *", self.ed_pattern)
        form2.addRow("Step", self.sp_step)
        form2.addRow("Urutan", self.cb_order)
        v2.addLayout(form2)
        v2.addWidget(self.chk_ambig)
        tok = QLabel(("Token: {YYYY} {YY} {MM} {DD} {HH} {MIN} {SEQ:n} {LOT} {SKU} {PROD} "
                      "{LINE} {RAND:n} {BASE32:n} {LuhnCheck} {CRC2} {CRC1} {MOD10} "
                      "{GS1AI:01} {DATE11} {CHECKSUM}"))
        tok.setObjectName("Subtle"); tok.setWordWrap(True)
        v2.addWidget(tok)
        v2.addWidget(self.lbl_valid)
        v2.addStretch(1)
        self.ed_pattern.textChanged.connect(self._validate_pattern)

        # ---- page 3 label -------------------------------------------------------
        p3 = QWidget()
        f3 = QFormLayout(p3)
        self.cb_template = QComboBox()
        self.cb_template.addItem("Label Standar 100x150 (bawaan)", json.dumps(_STD_TEMPLATE))
        self.cb_dpi = QComboBox(); self.cb_dpi.addItems(["300", "600"]); self.cb_dpi.setCurrentIndex(0)
        self.cb_backend = QComboBox()
        self.cb_backend.addItems(["Simulator (render ke layar)", "ZPL USB/COM", "ZPL TCP 9100",
                                  "TSPL TCP", "PDF single-page", "Dummy/File spool"])
        f3.addRow("Template label", self.cb_template)
        f3.addRow("Resolusi output", self.cb_dpi)
        f3.addRow("Perangkat cetak", self.cb_backend)
        edit_btn = QPushButton("Buka Label Designer…")
        edit_btn.setIcon(icon("edit", P.text, 16))
        f3.addRow(edit_btn)

        # ---- page 4 verifikasi -----------------------------------------------------
        p4 = QWidget()
        v4 = QVBoxLayout(p4)
        self.summ = QLabel()
        self.summ.setTextFormat(Qt.TextFormat.RichText)
        self.summ.setWordWrap(True)
        v4.addWidget(self.summ)
        self.chk_sim = QPushButton("Mode simulasi (boleh tanpa printer fisik): ON")
        self.chk_sim.setCheckable(True); self.chk_sim.setChecked(True)
        self.chk_sim.setObjectName("Flat")
        v4.addWidget(self.chk_sim)
        v4.addStretch(1)

        for w in (p1, p2, p3, p4):
            w.hide()
            self.stack_layout.addWidget(w)
        self._pages = [p1, p2, p3, p4]
        self._idx = -1
        self._show(0)

        btns = QHBoxLayout()
        self.btn_back = QPushButton("← Kembali")
        self.btn_next = PrimaryButton("Lanjut →", "play")
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        bb.rejected.connect(self.reject)
        self.btn_back.clicked.connect(lambda: self._show(self._idx - 1))
        self.btn_next.clicked.connect(self._next)
        btns.addWidget(bb)
        btns.addStretch(1)
        btns.addWidget(self.btn_back)
        btns.addWidget(self.btn_next)
        root.addLayout(btns)

        if clone_from:
            row = self.con.execute("SELECT * FROM jobs WHERE id=?", (clone_from,)).fetchone()
            if row:
                self.ed_name.setText(row["name"] + " (copy)")
                self.ed_pattern.setText(row["pattern"])
                self.ed_lot.setText(row["lot"] or "")
                self.sp_target.setValue(row["target_qty"] or 1000)
        self._update_preview()

    # ------------------------------------------------------------------
    def _show(self, idx: int) -> None:
        idx = max(0, min(len(self._pages) - 1, idx))
        self._idx = idx
        for i, w in enumerate(self._pages):
            w.setVisible(i == idx)
        self.stepper.set_step(idx if idx < 3 else 4)
        self.btn_back.setEnabled(idx > 0)
        self.btn_next.setText("SIMPAN & SELESAIKAN" if idx == len(self._pages) - 1 else "Lanjut →")
        if idx == 3:
            self.summ.setText(self._summary_html())

    def _validate_pattern(self, text: str) -> None:
        res = self.ctx.backend.call("validate_pattern", {"pattern": text})
        ok = res.get("ok")
        self.lbl_valid.setText(("✔ " if ok else "✖ ") + res.get("message", ""))
        self.lbl_valid.setStyleSheet(
            f"background:transparent;border:none;color:{P.ok if ok else P.err};")
        self._update_preview()

    def _next(self) -> None:
        if self._idx == 1:
            res = self.ctx.backend.call("validate_pattern", {"pattern": self.ed_pattern.text()})
            if not res.get("ok"):
                QMessageBox.warning(self, "Pola invalid", res.get("message", ""))
                return
        if self._idx == len(self._pages) - 1:
            self._save()
        else:
            self._show(self._idx + 1)

    def _summary_html(self) -> str:
        c = P.neon_bright
        d = P.text_dim
        return (f"<b style='color:{c}'>Ringkasan Job</b><br>"
                f"Nama: <span style='color:{d}'>{self.ed_name.text()}</span><br>"
                f"Pola: <span style='font-family:Consolas;color:{c}'>{self.ed_pattern.text()}</span><br>"
                f"Lot: <span style='color:{d}'>{self.ed_lot.text()}</span> · "
                f"Target: <span style='color:{d}'>{self.sp_target.value()}</span> pcs<br>"
                f"Cetak via: <span style='color:{d}'>{self.cb_backend.currentText()}</span>")

    def _update_preview(self) -> None:
        try:
            tmpl = json.loads(self.cb_template.currentData() or "{}")
            res = self.ctx.backend.call("render_serial", {
                "pattern": self.ed_pattern.text() or "X{SEQ:4}", "seq": 42,
                "lot": self.ed_lot.text(), "sku": "SKU-DEMO", "product": "PRD",
                "line": self.ed_line.text() or "L1"})
            serial = res["serial"]
            ctx = {"serial": serial, "sku": "SKU-DEMO", "lot": self.ed_lot.text(),
                   "exp": self.dt_exp.date().toString("dd-MM-yyyy"),
                   "date": datetime.now().strftime("%d-%m-%Y"), "weight": "330 ml",
                   "origin": "ID"}
            img = render_label(tmpl, ctx, scale=2.6)
            self.preview.setPixmap(QPixmap.fromImage(img))
            self.preview_serial.setText(serial)
        except Exception as exc:
            self.preview_serial.setText(f"preview error: {exc}")

    def _save(self) -> None:
        name = self.ed_name.text().strip()
        if not name:
            QMessageBox.warning(self, "Validasi", "Nama job wajib diisi.")
            return
        pat = self.ed_pattern.text()
        res = self.ctx.backend.call("validate_pattern", {"pattern": pat})
        if not res.get("ok"):
            QMessageBox.warning(self, "Pola invalid", res["message"])
            return
        sim_on = self.chk_sim.isChecked()
        if not sim_on and "Simulator" not in self.cb_backend.currentText():
            QMessageBox.warning(self, "Printer offline",
                                "Perangkat belum tersambung — aktifkan mode simulasi "
                                "atau sambungkan printer (halaman Settings › Mesin).")
            return
        secret = db.kv_get(self.con, "hmac_secret", "demo-secret")
        with db._LOCK:
            self.con.execute(
                "INSERT INTO jobs(name,product_id,pattern,lot,line,shift,target_qty,start_seq,"
                "last_seq,status,priority,hmac_secret,avoid_ambig,step,descending,template_json,"
                "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,'DRAFT',?,?,?,?,?,?,?,?)",
                (name, self.cb_product.currentData(), pat, self.ed_lot.text(),
                 self.ed_line.text(), self.cb_shift.currentText(), self.sp_target.value(),
                 1, 0, self.sp_prio.value(), secret, int(self.chk_ambig.isChecked()),
                 self.sp_step.value(), int(self.cb_order.currentIndex() == 1),
                 self.cb_template.currentData(), db.now_iso(), db.now_iso()))
            jid = self.con.execute("SELECT last_insert_rowid() i").fetchone()["i"]
            self.con.execute("INSERT OR REPLACE INTO counters(job_id,last_seq,reserved_until,"
                             "updated_at) VALUES(?,?,0,?)", (jid, 0, db.now_iso()))
            db.audit(self.con, self.ctx.user, "JOB_CREATE", "jobs", "", name,
                     f"wizard · pola {pat}")
            self.con.commit()
        self.new_job_id = jid
        self.ctx.toast.push(f"Job '{name}' tersimpan (ID {jid}).", "ok")
        self.accept()


_STD_TEMPLATE = {
    "ver": 1, "name": "Label Standar 100x150", "size_mm": [100, 150], "dpi": 300,
    "elements": [
        {"type": "text", "x": 6, "y": 8, "font_pt": 14, "bold": True, "content": "{{serial}}"},
        {"type": "text", "x": 6, "y": 20, "font_pt": 8,
         "content": "SKU {{sku}} · LOT {{lot}} · EXP {{exp}}"},
        {"type": "code128", "x": 6, "y": 30, "h_mm": 14, "content": "{{serial}}", "human": True},
        {"type": "qr", "x": 70, "y": 95, "size_mm": 24, "ec": "M",
         "content": "https://verify.serialx.id/?s={{serial}}"},
        {"type": "rect", "x": 4, "y": 4, "w": 92, "h": 142, "pen": "#00FF5F"},
        {"type": "text", "x": 6, "y": 132, "font_pt": 6, "content": "SERIALX :: INDUSTRIAL SERIALIZATION"},
    ],
}
