"""Halaman Settings (§3.12) — tab Umum/Mesin/Serial/Keamanan/Lisensi/Backup/About."""
from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout,
                               QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
                               QSpinBox, QTabWidget, QVBoxLayout, QWidget)

from ui import db
from ui.theme.tokens import ACCENT_PRESETS, P, SZ
from ui.widgets.common import PrimaryButton


class SettingsPage(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.con = ctx.con
        lay = QVBoxLayout(self)
        lay.setContentsMargins(SZ.pad_l, SZ.pad_l, SZ.pad_l, SZ.pad_l)
        t = QLabel("SETTINGS")
        t.setObjectName("SectionTitle")
        lay.addWidget(t)
        tabs = QTabWidget()
        lay.addWidget(tabs)

        # ---- Umum -----------------------------------------------------------
        w = QWidget(); f = QFormLayout(w)
        self.cb_lang = QComboBox(); self.cb_lang.addItems(["id", "en"])
        self.cb_lang.setCurrentText(db.kv_get(self.con, "lang", "id"))
        self.cb_accent = QComboBox(); self.cb_accent.addItems(list(ACCENT_PRESETS))
        self.cb_accent.setCurrentText(ctx.theme.accent_name)
        self.cb_accent.currentTextChanged.connect(self._accent_changed)
        self.cb_notify = QCheckBox(); self.cb_notify.setChecked(True)
        f.addRow("Bahasa", self.cb_lang)
        f.addRow("Accent hue (langsung ter-polish)", self.cb_accent)
        f.addRow("Notifikasi toast", self.cb_notify)
        tabs.addTab(w, "Umum")

        # ---- Mesin ------------------------------------------------------------
        w = QWidget(); f = QFormLayout(w)
        self.ed_port = QLineEdit(db.kv_get(self.con, "printer.port", "COM3"))
        self.ed_baud = QComboBox(); self.ed_baud.addItems(["9600", "19200", "38400", "57600", "115200"])
        self.ed_baud.setCurrentText(db.kv_get(self.con, "printer.baud", "9600"))
        self.ed_ip = QLineEdit(db.kv_get(self.con, "printer.ip", "192.168.1.50"))
        self.ed_proto = QComboBox(); self.ed_proto.addItems(["ZPL-TCP9100", "ZPL-SERIAL", "TSPL-TCP", "CUPS/lpr", "Dummy/File"])
        self.sp_retry = QSpinBox(); self.sp_retry.setRange(0, 9); self.sp_retry.setValue(3)
        for k, wdg in (("printer.port", self.ed_port), ("printer.baud", self.ed_baud),
                       ("printer.ip", self.ed_ip)):
            wdg.editingFinished if hasattr(wdg, "editingFinished") else None
        f.addRow("COM/USB port", self.ed_port)
        f.addRow("Baud", self.ed_baud)
        f.addRow("IP printer", self.ed_ip)
        f.addRow("Protocol", self.ed_proto)
        f.addRow("Retry max", self.sp_retry)
        b_save_m = QPushButton("Simpan mesin")
        b_save_m.clicked.connect(self._save_machine)
        f.addRow(b_save_m)
        tabs.addTab(w, "Mesin")

        # ---- Serial ----------------------------------------------------------
        w = QWidget(); f = QFormLayout(w)
        self.ed_secret = QLineEdit(db.kv_get(self.con, "hmac_secret", ""))
        self.ed_secret.setEchoMode(QLineEdit.EchoMode.Password)
        b_rot = QPushButton("Rotasi secret HMAC")
        b_rot.setIcon(__import__("ui.theme.icons", fromlist=["icon"]).icon("refresh", P.text, 16))
        b_rot.clicked.connect(self._rotate_secret)
        f.addRow("Secret HMAC (sig serial)", self.ed_secret)
        f.addRow(b_rot)
        tabs.addTab(w, "Serial")

        # ---- Keamanan -----------------------------------------------------------
        w = QWidget(); f = QFormLayout(w)
        self.sp_timeout = QSpinBox(); self.sp_timeout.setRange(1, 240)
        self.sp_timeout.setValue(int(db.kv_get(self.con, "session.timeout", "15")))
        self.sp_timeout.setSuffix(" menit")
        self.sp_retention = QSpinBox(); self.sp_retention.setRange(30, 3650)
        self.sp_retention.setValue(int(db.kv_get(self.con, "audit.retention", "365")))
        self.sp_retention.setSuffix(" hari")
        f.addRow("Session timeout (auto-lock)", self.sp_timeout)
        f.addRow("Audit retention", self.sp_retention)
        b_s = QPushButton("Simpan keamanan"); b_s.clicked.connect(self._save_sec)
        f.addRow(b_s)
        tabs.addTab(w, "Keamanan")

        # ---- Lisensi -------------------------------------------------------------
        w = QWidget(); f = QFormLayout(w)
        self.lbl_mid = QLabel(ctx.machine_id)
        self.lbl_mid.setObjectName("MonoText")
        self.ed_license = QLineEdit(); self.ed_license.setPlaceholderText("tempel token lisensi…")
        b_act = QPushButton("Aktivasi offline")
        b_act.setIcon(__import__("ui.theme.icons", fromlist=["icon"]).icon("unlock", P.text, 16))
        b_act.clicked.connect(self._activate_license)
        self.lbl_lic = QLabel(ctx.license_state())
        self.lbl_lic.setWordWrap(True)
        f.addRow("Machine ID", self.lbl_mid)
        f.addRow("Token lisensi", self.ed_license)
        f.addRow(b_act)
        f.addRow(self.lbl_lic)
        tabs.addTab(w, "Lisensi")

        # ---- Backup ---------------------------------------------------------------
        w = QWidget(); f = QFormLayout(w)
        b_bak = QPushButton("Backup DB sekarang")
        b_bak.setIcon(__import__("ui.theme.icons", fromlist=["icon"]).icon("database", P.text, 16))
        b_bak.clicked.connect(self._backup_now)
        b_res = QPushButton("Restore dari file…")
        b_res.setIcon(__import__("ui.theme.icons", fromlist=["icon"]).icon("import", P.text, 16))
        b_res.clicked.connect(self._restore)
        b_purge = QPushButton("Purge audit > N hari")
        b_purge.setIcon(__import__("ui.theme.icons", fromlist=["icon"]).icon("trash", P.err, 16))
        b_purge.clicked.connect(self._purge)
        b_vac = QPushButton("VACUUM DB")
        b_vac.clicked.connect(self._vacuum)
        self.btn_demo = PrimaryButton("Reset Demo Data", "refresh")
        self.btn_demo.clicked.connect(self._reset_demo)
        for b in (b_bak, b_res, b_purge, b_vac):
            b.setToolTip(b.text())
        f.addRow(b_bak); f.addRow(b_res); f.addRow(b_purge); f.addRow(b_vac)
        f.addRow(self.btn_demo)
        tabs.addTab(w, "Backup")

        # ---- About -----------------------------------------------------------------
        w = QWidget(); f = QFormLayout(w)
        ver = f"1.0.0 · build {datetime.now():%Y%m%d}"
        info = (f"<b>SERIALX</b> Industrial Serialization & Marking Suite<br>"
                f"UI: PySide6 (Qt {__import__('PySide6', fromlist=['__version__']).__version__}) · "
                f"Python {sys.version.split()[0]} · {platform.system()} {platform.release()}<br>"
                f"Backend: <span style='color:{P.neon}'>{ctx.backend.core_version}</span><br>"
                f"Tema: ABYSS NEON (Navy + Monster-neon #00FF5F)<br>Versi: {ver}")
        lbl = QLabel(info)
        lbl.setTextFormat(Qt.TextFormat.RichText)
        lbl.setWordWrap(True)
        f.addRow(lbl)
        tabs.addTab(w, "About")

    # ------------------------------------------------------------------
    def _accent_changed(self, name: str) -> None:
        if self.ctx.theme.set_accent(name):
            db.kv_set(self.con, "theme.accent", name)
            self.ctx.toast.push(f"Accent → {name} (re-polish tanpa restart).", "ok")

    def _save_machine(self) -> None:
        db.kv_set(self.con, "printer.port", self.ed_port.text())
        db.kv_set(self.con, "printer.baud", self.ed_baud.currentText())
        db.kv_set(self.con, "printer.ip", self.ed_ip.text())
        db.audit(self.con, self.ctx.user, "SETTINGS_MACHINE", "settings_kv", "", "")
        self.ctx.toast.push("Konfigurasi mesin tersimpan.", "ok")

    def _rotate_secret(self) -> None:
        import secrets
        s = secrets.token_hex(16)
        db.kv_set(self.con, "hmac_secret", s)
        self.ed_secret.setText(s)
        db.audit(self.con, self.ctx.user, "SECRET_ROTATE", "settings_kv", "", "")
        self.ctx.toast.push("Secret HMAC dirotasi — sig lama tak valid.", "warn")

    def _save_sec(self) -> None:
        db.kv_set(self.con, "session.timeout", str(self.sp_timeout.value()))
        db.kv_set(self.con, "audit.retention", str(self.sp_retention.value()))
        self.ctx.session_timeout_min = self.sp_timeout.value()
        self.ctx.toast.push("Pengaturan keamanan disimpan.", "ok")

    def _activate_license(self) -> None:
        tok = self.ed_license.text().strip()
        if not tok:
            self.ctx.toast.push("Tempel token lisensi dulu.", "warn")
            return
        res = self.ctx.backend.call("license_verify",
                                    {"public_key_pem": self.ctx.license_pubkey_pem, "token": tok})
        if res.get("valid"):
            with db._LOCK:
                self.con.execute("INSERT INTO licenses(machine_id,token,payload) VALUES(?,?,?)",
                                 (self.ctx.machine_id, tok, res["payload"]))
                db.audit(self.con, self.ctx.user, "LICENSE_ACTIVATE", "licenses", "", tok[:24])
                self.con.commit()
            self.ctx.lic_checked = True
            self.lbl_lic.setText("✔ Lisensi valid: " + res["payload"][:180])
            self.ctx.toast.push("Lisensi aktif!", "ok")
        else:
            self.lbl_lic.setText("✖ Tanda tangan lisensi TIDAK valid — tetap mode DEMO.")
            self.ctx.toast.push("Lisensi ditolak.", "err")

    def _backup_now(self) -> None:
        dst = Path("data/backups"); dst.mkdir(parents=True, exist_ok=True)
        name = dst / f"serialx_{datetime.now():%Y%m%d_%H%M%S}.db"
        with db._LOCK:
            self.con.execute("VACUUM INTO ?", (str(name),))
        db.audit(self.con, self.ctx.user, "BACKUP", "db", "", str(name))
        self.ctx.toast.push(f"Backup: {name}", "ok")

    def _restore(self) -> None:
        src, _ = QFileDialog.getOpenFileName(self, "Restore DB", "data", "SQLite (*.db)")
        if not src:
            return
        if QMessageBox.question(self, "Restore", "Timpa database aktif?") != QMessageBox.StandardButton.Yes:
            return
        self.con.close()
        shutil.copy(src, db.DB_PATH)
        self.ctx.toast.push("DB dipulihkan — restart aplikasi.", "warn")

    def _purge(self) -> None:
        days = self.sp_retention.value()
        with db._LOCK:
            n = self.con.execute("SELECT COUNT(*) c FROM audit_log WHERE ts < datetime('now', ?)",
                                 (f"-{days} days",)).fetchone()["c"]
            self.con.execute("DELETE FROM audit_log WHERE ts < datetime('now', ?)", (f"-{days} days",))
            db.audit(self.con, self.ctx.user, "PURGE_AUDIT", "audit_log", "", str(n))
            self.con.commit()
        self.ctx.toast.push(f"{n} baris audit dibersihkan.", "info")

    def _vacuum(self) -> None:
        with db._LOCK:
            self.con.execute("VACUUM")
        self.ctx.toast.push("VACUUM selesai.", "ok")

    def _reset_demo(self) -> None:
        if QMessageBox.question(self, "Reset demo",
                                "Hapus SEMUA data lalu seed ulang demo?") != QMessageBox.StandardButton.Yes:
            return
        with db._LOCK:
            for tbl in ("serial_records", "print_queue", "aggregation_map", "counters",
                        "daily_stats", "jobs", "products", "users", "audit_log", "blacklist"):
                self.con.execute(f"DELETE FROM {tbl}")
            self.con.commit()
        db.seed_demo(self.con, self.ctx.backend)
        self.ctx.toast.push("Demo data di-seed ulang.", "ok")
