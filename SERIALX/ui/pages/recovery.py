"""Halaman Reprint & Recovery (§3.11) + Blacklist/Hold-list."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
                               QLabel, QLineEdit, QMessageBox, QPushButton, QTextEdit,
                               QVBoxLayout, QWidget)

from ui import db
from ui.theme.effects import apply_glow
from ui.theme.icons import icon
from ui.theme.tokens import P, SZ


class ReasonDialog(QDialog):
    def __init__(self, parent, title="Alasan"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(340)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Alasan wajib diisi:"))
        self.ed = QTextEdit(); self.ed.setMaximumHeight(80)
        lay.addWidget(self.ed)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def value(self) -> str:
        return self.ed.toPlainText().strip()


class RecoveryPage(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.con = ctx.con
        lay = QVBoxLayout(self)
        lay.setContentsMargins(SZ.pad_l, SZ.pad_l, SZ.pad_l, SZ.pad_l)
        t = QLabel("REPRINT & RECOVERY")
        t.setObjectName("SectionTitle")
        lay.addWidget(t)

        # ---- reprint ---------------------------------------------------------
        card = QWidget(); card.setObjectName("Card"); cl = QVBoxLayout(card)
        cl.addWidget(QLabel("Cari serial (scan/manual) → reprint dengan watermark opsional"))
        row = QHBoxLayout()
        self.ed_find = QLineEdit(); self.ed_find.setPlaceholderText("Serial…")
        apply_glow(self.ed_find, P.neon_dim, blur=10)
        b_reprint = QPushButton("Reprint")
        b_reprint.setIcon(icon("print", P.text, 16))
        b_reprint.clicked.connect(self._reprint)
        self.chk_wm = QPushButton("Watermark REPRINT: ON"); self.chk_wm.setCheckable(True)
        self.chk_wm.setChecked(True); self.chk_wm.setObjectName("Flat")
        row.addWidget(self.ed_find, 1); row.addWidget(self.chk_wm); row.addWidget(b_reprint)
        cl.addLayout(row)
        self.lbl_found = QLabel("—"); self.lbl_found.setWordWrap(True)
        self.lbl_found.setObjectName("MonoText")
        cl.addWidget(self.lbl_found)
        lay.addWidget(card)

        # ---- recovery job ----------------------------------------------------
        card2 = QWidget(); card2.setObjectName("Card"); c2 = QVBoxLayout(card2)
        c2.addWidget(QLabel("Pulihkan job terputus — resume counter dari last_issued+1, deteksi gap"))
        self.lbl_gap = QLabel("Gap: —"); self.lbl_gap.setObjectName("MonoText")
        rrow = QHBoxLayout()
        b_resume = QPushButton("Deteksi gap & resume")
        b_resume.setIcon(icon("refresh", P.text, 16))
        b_resume.clicked.connect(self._detect_gap)
        b_fill = QPushButton("Fill-gap mode")
        b_fill.setIcon(icon("plus", P.text, 16))
        b_fill.clicked.connect(self._fill_gap)
        rrow.addWidget(b_resume); rrow.addWidget(b_fill); rrow.addStretch(1)
        c2.addLayout(rrow)
        c2.addWidget(self.lbl_gap)
        lay.addWidget(card2)

        # ---- blacklist --------------------------------------------------------
        card3 = QWidget(); card3.setObjectName("Card"); c3 = QVBoxLayout(card3)
        c3.addWidget(QLabel("Blacklist / hold-list (nomor cacat cetak agar tak dipakai ulang)"))
        brow = QHBoxLayout()
        self.ed_bl = QLineEdit(); self.ed_bl.setPlaceholderText("Serial…")
        b_add = QPushButton("Tambah"); b_add.setIcon(icon("lock", P.err, 16))
        b_add.clicked.connect(self._add_blacklist)
        brow.addWidget(self.ed_bl, 1); brow.addWidget(b_add)
        c3.addLayout(brow)
        self.bl_list = QLabel("—"); self.bl_list.setObjectName("Subtle")
        self.bl_list.setWordWrap(True)
        c3.addWidget(self.bl_list)
        lay.addWidget(card3, 1)
        self._refresh_bl()

    # ------------------------------------------------------------------
    def _reprint(self) -> None:
        serial = self.ed_find.text().strip().upper()
        if not serial:
            return
        row = self.con.execute(
            "SELECT s.*, j.name jn, j.product_id, p.sku FROM serial_records s "
            "JOIN jobs j ON j.id=s.job_id JOIN products p ON p.id=j.product_id WHERE s.serial=?",
            (serial,)).fetchone()
        if not row:
            self.lbl_found.setText(f"✖ Serial {serial} TIDAK ditemukan di DB.")
            self.ctx.toast.push("Serial unknown.", "err")
            return
        dlg = ReasonDialog(self, "Alasan reprint")
        if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.value():
            self.ctx.toast.push("Reprint dibatalkan (alasan wajib).", "warn")
            return
        wm = "REPRINT-" if self.chk_wm.isChecked() else ""
        zpl = self.ctx.backend.call("zpl_label", {"serial": wm + serial, "sku": row["sku"],
                                                  "lot": row["lot"] or "", "exp": ""})["zpl"]
        with db._LOCK:
            self.con.execute("INSERT INTO print_queue(job_id,serial,zpl,status,station) "
                             "VALUES(?,?,?,'QUEUED',?)", (row["job_id"], serial, zpl, self.ctx.station))
            self.con.execute("UPDATE serial_records SET reprint_count=reprint_count+1, note=? "
                             "WHERE serial=?", (f"reprint oleh {self.ctx.user}: {dlg.value()[:120]}",
                                                serial))
            db.audit(self.con, self.ctx.user, "REPRINT", "serial_records", "", serial, dlg.value())
            self.con.commit()
        self.lbl_found.setText(f"✔ Reprint '{serial}' masuk antrian ({'watermark ON' if wm else 'tanpa watermark'}).")
        self.ctx.toast.push(f"Reprint queued: {serial}", "warn")
        self.ed_find.clear()

    def _detect_gap(self) -> None:
        job = self.con.execute("SELECT * FROM jobs WHERE status IN ('RUNNING','PAUSED') "
                               "ORDER BY id DESC LIMIT 1").fetchone()
        if not job:
            self.lbl_gap.setText("Tidak ada job aktif.")
            return
        mx = self.con.execute("SELECT MAX(s.last_seq) m FROM (SELECT CAST(substr(serial,-6) AS INT) "
                              "last_seq FROM serial_records WHERE job_id=? ) s",
                              (job["id"],)).fetchone()["m"] or 0
        cnt = self.con.execute("SELECT COUNT(*) c FROM serial_records WHERE job_id=?",
                               (job["id"],)).fetchone()["c"]
        gap = int(mx) - cnt
        self.lbl_gap.setText(f"Job {job['name']}: max seq={mx}, record={cnt}, gap≈{gap}. "
                             f"Counter resume dari {max(job['last_seq'], int(mx)) + 1}.")
        with db._LOCK:
            self.con.execute("UPDATE counters SET last_seq=MAX(last_seq,?), updated_at=? WHERE job_id=?",
                             (int(mx), db.now_iso(), job["id"]))
            self.con.execute("UPDATE jobs SET last_seq=MAX(last_seq,?), status='RUNNING', updated_at=? "
                             "WHERE id=?", (int(mx), db.now_iso(), job["id"]))
            db.audit(self.con, self.ctx.user, "JOB_RESUME", "jobs", "", job["name"], f"gap {gap}")
            self.con.commit()
        self.ctx.toast.push(f"Job '{job['name']}' di-resume.", "ok")

    def _fill_gap(self) -> None:
        """Terbitkan nomor yang hilang (gap) pada job RUNNING."""
        job = self.con.execute("SELECT * FROM jobs WHERE status='RUNNING' LIMIT 1").fetchone()
        if not job:
            self.ctx.toast.push("Tidak ada job RUNNING.", "warn")
            return
        have = {r["seq"] for r in self.con.execute(
            "SELECT CAST(substr(serial,-6) AS INT) seq FROM serial_records WHERE job_id=?",
            (job["id"],)).fetchall() if isinstance(r["seq"], int)}
        mx = max(have) if have else 0
        missing = [s for s in range(1, mx + 1) if s not in have][:50]
        secret = db.kv_get(self.con, "hmac_secret", "")
        filled = 0
        for s in missing:
            res = self.ctx.backend.call("render_serial", {
                "pattern": job["pattern"], "seq": s, "lot": job["lot"] or "",
                "sku": "FILL", "product": "P", "line": job["line"]})
            serial = res["serial"]
            try:
                sig = self.ctx.backend.call("sign_serial",
                                            {"secret": secret, "serial": serial,
                                             "lot": job["lot"] or ""})["sig"]
                with db._LOCK:
                    self.con.execute(
                        "INSERT INTO serial_records(serial,job_id,product_id,lot,level,sig,"
                        "status,station,note) VALUES(?,?,?,?,?,?,'ISSUED',?,'fill-gap')",
                        (serial, job["id"], job["product_id"], job["lot"], 1, sig, self.ctx.station))
                    self.con.commit()
                filled += 1
            except Exception:
                pass
        db.audit(self.con, self.ctx.user, "FILL_GAP", "jobs", "", job["name"], f"{filled} nomor")
        self.ctx.toast.push(f"Fill-gap: {filled} nomor diterbitkan.", "info" if filled else "warn")

    def _add_blacklist(self) -> None:
        s = self.ed_bl.text().strip().upper()
        if not s:
            return
        dlg = ReasonDialog(self, "Alasan hold")
        if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.value():
            return
        with db._LOCK:
            self.con.execute("INSERT OR REPLACE INTO blacklist(serial,reason,created_at) "
                             "VALUES(?,?,?)", (s, dlg.value(), db.now_iso()))
            db.audit(self.con, self.ctx.user, "BLACKLIST_ADD", "blacklist", "", s, dlg.value())
            self.con.commit()
        self.ctx.toast.push(f"{s} masuk hold-list.", "err")
        self.ed_bl.clear()
        self._refresh_bl()

    def _refresh_bl(self) -> None:
        rows = self.con.execute("SELECT serial,reason FROM blacklist ORDER BY created_at DESC "
                                "LIMIT 12").fetchall()
        self.bl_list.setText("\n".join(f"⛔ {r['serial']} — {r['reason']}" for r in rows) or "(kosong)")
