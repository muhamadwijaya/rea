"""Halaman Verification / Vision (§3.7) — scan keyboard wedge, verify vs DB, reconcile."""
from __future__ import annotations

import random

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QVBoxLayout, QWidget)

from ui import db
from ui.theme.effects import LedIndicator, apply_glow
from ui.theme.icons import icon
from ui.theme.tokens import P, SZ


class FakeCameraView(QWidget):
    """Simulasi video frame kamera vision dengan overlay bounding box neon."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(320, 220)
        self.has_target = False
        self.confidence = 0.0
        self._noise_seed = 0

    def grab_frame(self, found: bool, conf: float) -> None:
        self.has_target = found
        self.confidence = conf
        self._noise_seed += 1
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        rnd = random.Random(self._noise_seed)
        p.fillRect(self.rect(), QColor(P.navy_900))
        # noise ala sensor
        for _ in range(140):
            x, y = rnd.randrange(self.width()), rnd.randrange(self.height())
            g = rnd.randint(10, 40)
            p.fillRect(x, y, 2, 2, QColor(g, g + 8, g + 20))
        if self.has_target:
            w = int(self.width() * 0.5)
            h = int(w * 0.6)
            x = (self.width() - w) // 2
            y = (self.height() - h) // 2
            p.setPen(QPen(QColor(P.neon), 3))
            p.drawRect(x, y, w, h)
            p.setPen(QColor(P.neon_bright))
            p.drawText(x, y - 8, f"DECODE {int(self.confidence * 100)}%")
            # kotak-kotak DM semu
            cell = max(4, w // 14)
            for i in range(12):
                for j in range(12):
                    if rnd.random() > 0.5:
                        p.fillRect(x + 10 + i * cell, y + 10 + j * cell, cell - 1, cell - 1,
                                   QColor(230, 240, 250))
        p.setPen(QColor(P.text_mute))
        p.drawText(8, self.height() - 8, "CAM-01 · 1280×720 · 30fps (simulasi)")
        p.end()


class VerifyPage(QWidget):
    """Scanner handheld (wedge), Serial Verify OK/DUP/UNKNOWN/WRONG-BATCH, anti-dup global."""

    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.con = ctx.con
        lay = QVBoxLayout(self)
        lay.setContentsMargins(SZ.pad_l, SZ.pad_l, SZ.pad_l, SZ.pad_l)
        t = QLabel("VERIFIKASI & VISION")
        t.setObjectName("SectionTitle")
        lay.addWidget(t)

        row = QHBoxLayout()
        left = QWidget()
        left.setObjectName("Card")
        lv = QVBoxLayout(left)
        lv.addWidget(QLabel("MODE SCANNER HANDHELD (keyboard wedge — scan lalu Enter)"))
        self.ed_scan = QLineEdit()
        self.ed_scan.setPlaceholderText("Arahkan scanner, hasil masuk ke sini…")
        self.ed_scan.returnPressed.connect(self._verify)
        apply_glow(self.ed_scan, P.neon_dim, blur=10)
        lv.addWidget(self.ed_scan)
        btnrow = QHBoxLayout()
        b_v = QPushButton("VERIFY")
        b_v.setIcon(icon("check", P.text, 16))
        b_v.clicked.connect(self._verify)
        b_fake = QPushButton("Simulasi scan serial acak")
        b_fake.setIcon(icon("scan", P.text, 16))
        b_fake.clicked.connect(self._fake_scan)
        b_recon = QPushButton("Reconcile pass karton")
        b_recon.setIcon(icon("box", P.text, 16))
        b_recon.clicked.connect(self._reconcile)
        for b in (b_v, b_fake, b_recon):
            b.setToolTip(b.text())
            btnrow.addWidget(b)
        lv.addLayout(btnrow)
        self.result = QLabel("Siap.")
        self.result.setWordWrap(True)
        self.result.setStyleSheet(f"color:{P.text}; background:transparent; border:none;"
                                  f" font-family:Consolas,monospace; font-size:{SZ.fs_sub}px;")
        self.res_led = LedIndicator("IDLE")
        rr = QHBoxLayout()
        rr.addWidget(self.res_led)
        rr.addWidget(self.result, 1)
        lv.addLayout(rr)
        self.hist = QLabel("")
        self.hist.setTextFormat(Qt.TextFormat.RichText)
        self.hist.setStyleSheet("background:transparent;border:none;color:#8FA6C2;"
                                "font-family:Consolas,monospace;font-size:11px;")
        lv.addWidget(self.hist)
        self._hist: list[str] = []
        row.addWidget(left, 3)

        camwrap = QWidget()
        camwrap.setObjectName("Card")
        cvl = QVBoxLayout(camwrap)
        ch = QLabel("KAMERA VISION (OCR hook — pytesseract bila tersedia, else fake)")
        cvl.addWidget(ch)
        self.cam = FakeCameraView()
        cvl.addWidget(self.cam, 1)
        b_snap = QPushButton("Grab & decode")
        b_snap.setIcon(icon("camera", P.text, 16))
        b_snap.clicked.connect(self._snap)
        cvl.addWidget(b_snap)
        row.addWidget(camwrap, 2)
        lay.addLayout(row, 1)

    # ------------------------------------------------------------------
    def _log(self, msg_html: str) -> None:
        self._hist.insert(0, msg_html)
        self._hist = self._hist[:8]
        self.hist.setText("<br>".join(self._hist))

    def _verify(self) -> None:
        serial = self.ed_scan.text().strip().upper()
        if not serial:
            return
        self.ed_scan.clear()
        row = self.con.execute(
            "SELECT s.*, j.lot AS jlot FROM serial_records s JOIN jobs j ON j.id=s.job_id "
            "WHERE s.serial=?", (serial,)).fetchone()
        secret = db.kv_get(self.con, "hmac_secret", "demo-secret")
        blacklisted = self.con.execute("SELECT 1 FROM blacklist WHERE serial=?", (serial,)).fetchone()
        if row is None:
            verdict, kind, led = "UNKNOWN", "err", "ERROR"
            self.ctx.toast.push(f"Serial UNKNOWN: {serial}", "err")
        elif blacklisted:
            verdict, kind, led = "HOLD-LIST!", "err", "ERROR"
            QApplication_beep()
        elif row["status"] in ("PRINTED", "VERIFIED"):
            verdict, kind, led = "DUPLICATE — sudah pernah dicetak!", "err", "ERROR"
            QApplication_beep()
            db.audit(self.con, self.ctx.user, "DUP_SCAN_ALARM", "serial_records", "", serial,
                     "anti-duplicate global terpicu")
        elif row["jlot"] != (self.ctx.current_lot or row["jlot"]):
            verdict, kind, led = "WRONG-BATCH", "warn", "PAUSED"
        else:
            ok_sig = self.ctx.backend.call("verify_serial",
                                           {"secret": secret, "serial": serial,
                                            "lot": row["lot"] or "", "sig": row["sig"] or ""}).get("valid")
            verdict = "OK" + (" ✔sig" if ok_sig else " ⚠sig-invalid")
            kind, led = "ok", "RUNNING"
            with db._LOCK:
                self.con.execute("UPDATE serial_records SET status='VERIFIED', verified_at=? "
                                 "WHERE serial=?", (db.now_iso(), serial))
                self.con.execute("UPDATE daily_stats SET verified=verified+1 WHERE day=?",
                                 (db.datetime.now().strftime("%Y-%m-%d"),))
                self.con.commit()
        colors = {"ok": P.ok, "err": P.err, "warn": P.warn}
        self.result.setText(f"{verdict}\n{serial}")
        self.result.setStyleSheet(f"color:{colors[kind]}; background:transparent; border:none;"
                                  f" font-family:Consolas,monospace; font-size:{SZ.fs_sub}px;")
        self.res_led.update_state(led)
        self._log(f"<span style='color:{colors[kind]}'>{verdict.split()[0]}</span> "
                  f"<span style='color:{P.text_mute}'>{serial}</span>")

    def _fake_scan(self) -> None:
        rows = self.con.execute(
            "SELECT serial FROM serial_records ORDER BY RANDOM() LIMIT 2").fetchall()
        pick = random.choice(rows)["serial"] if rows and random.random() < 0.75 else \
            "SRX999999-L1000000-ZZ"
        self.ed_scan.setText(pick)
        self._verify()

    def _snap(self) -> None:
        found = random.random() < 0.8
        conf = round(random.uniform(0.82, 0.99), 2) if found else 0.0
        self.cam.grab_frame(found, conf)
        if found:
            self.ctx.toast.push(f"Vision decode OK ({int(conf * 100)}%)", "ok")
        else:
            self.ctx.toast.push("Vision: target tidak ditemukan.", "warn")

    def _reconcile(self) -> None:
        """Scan akhir karton: cocokkan jumlah PRINTED vs target job aktif."""
        job = self.con.execute("SELECT * FROM jobs WHERE status='RUNNING' LIMIT 1").fetchone()
        if not job:
            self.ctx.toast.push("Tidak ada job RUNNING utk direkonsiliasi.", "warn")
            return
        printed = self.con.execute(
            "SELECT COUNT(*) c FROM serial_records WHERE job_id=? AND status IN ('PRINTED','VERIFIED')",
            (job["id"],)).fetchone()["c"]
        issued = self.con.execute(
            "SELECT COUNT(*) c FROM serial_records WHERE job_id=?", (job["id"],)).fetchone()["c"]
        missing = issued - printed
        extra = max(0, printed - job["last_seq"])
        msg = f"Job {job['name']}: printed {printed}/{issued}, missing {missing}, extra {extra}"
        self._log(f"<span style='color:{P.info}'>RECONCILE</span> {msg}")
        self.ctx.toast.push(msg, "ok" if missing == 0 else "warn")
        db.audit(self.con, self.ctx.user, "RECONCILE", "jobs", "", job["name"], msg)


def QApplication_beep() -> None:
    from PySide6.QtWidgets import QApplication
    QApplication.beep()
