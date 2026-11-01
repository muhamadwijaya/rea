"""AppContext — pusat state aplikasi SERIALX (login, role, job runner, lisensi).

Halaman-halaman UI hanya berbicara lewat ctx; tidak ada global mutable.
"""
from __future__ import annotations

import hashlib
import platform
import uuid
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QVBoxLayout

from ui import db
from ui.backend_bridge import BackendClient
from ui.demo_runner import DemoRunner
from ui.dialogs.label_designer import LabelDesigner
from ui.theme.effects import ToastHost
from ui.theme.manager import ThemeManager

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"

# Public key RSA embed (untuk verifikasi token lisensi offline §3.13).
LICENSE_PUBKEY_PEM = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA2cZtn1BV+UvNqQ0y8tLx
rW7EeA0kF3zR4pM1sYb3T9dKqVXhHkF6PvJmS5oCgwN2bXcR7iZdLpE0uT1aQfB4
vK9sMmR2nXpPqLwYcE7hT0jGd6bFfS8aAq3rNnZmV0kYpPbWcDxJ2eFfR7sTtUuV
wXxYyZz0aAbBcCdDeEfFgGhHiIjJkKlLmMnNoOpPqQrRsStTuUvVwWxXyYzZ0aa2
QQIDAQAB
-----END PUBLIC KEY-----"""


def _machine_id() -> str:
    """ID stabil: motherboard UUID bila terbaca, fallback hostname+MAC (§3.13)."""
    candidates: list[str] = []
    try:
        for p in ("/sys/class/dmi/id/product_uuid",
                  "/sys/class/dmi/id/board_serial"):
            f = Path(p)
            if f.is_file():
                candidates.append(f.read_text(errors="ignore").strip())
    except OSError:
        pass
    if not candidates:  # windows/linux umum
        candidates.append(uuid.getnode().__str__())
        candidates.append(platform.node())
    blob = "|".join(c for c in candidates if c)
    return "SRX-" + hashlib.sha256(blob.encode()).hexdigest()[:16].upper()


class LoginDialog(QDialog):
    """Dialog login navy-neon; dipakai first-run & saat unlock manual."""

    def __init__(self, ctx: "AppContext", title: str = "Masuk ke SERIALX"):
        super().__init__()
        self.ctx = ctx
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(360)
        lay = QVBoxLayout(self)
        head = QLabel("⚡ SERIALX")
        head.setObjectName("Display")
        sub = QLabel("Industrial Serialization & Marking Suite")
        sub.setObjectName("Subtle")
        form = QFormLayout()
        self.ed_user = QLineEdit(); self.ed_user.setPlaceholderText("username")
        self.ed_pw = QLineEdit(); self.ed_pw.setEchoMode(QLineEdit.EchoMode.Password)
        self.ed_pw.setPlaceholderText("password")
        form.addRow("User", self.ed_user)
        form.addRow("Password", self.ed_pw)
        lay.addWidget(head); lay.addWidget(sub); lay.addLayout(form)
        self.lbl_err = QLabel("")
        self.lbl_err.setObjectName("ErrText")
        lay.addWidget(self.lbl_err)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._try)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.ed_pw.returnPressed.connect(self._try)
        self.ed_user.setText("admin")

    def _try(self) -> None:
        u, pw = self.ed_user.text().strip(), self.ed_pw.text()
        if self.ctx.login(u, pw):
            self.accept()
        else:
            self.lbl_err.setText("✖ Kredensial salah.")
            QApplication_beep()


def QApplication_beep() -> None:
    from PySide6.QtWidgets import QApplication
    QApplication.beep()


class AppContext(QObject):
    """Satu-satunya objek yang di-pass ke semua Page/Dialog."""

    jobStateChanged = Signal(str)          # "RUNNING"|"PAUSED"|"STOPPED"
    serialIssued = Signal(dict)

    def __init__(self, demo: bool = False) -> None:
        super().__init__()
        self.demo_mode = demo
        self.backend = BackendClient()
        self.con = db.connect(DATA_DIR / "serialx.db")
        db.migrate(self.con)
        db.seed_demo(self.con, self.backend)
        self.theme = ThemeManager()
        self.toast: ToastHost | Any = None      # diisi setelah MainWindow jadi
        self.user: str = ""
        self.role: str = "viewer"
        self.station: str = "ST-DEMO"
        self.current_lot: str = ""
        self.machine_id: str = _machine_id()
        self.license_pubkey_pem: str = LICENSE_PUBKEY_PEM
        self.lic_checked: bool = False
        self.runner: DemoRunner | None = None
        self.active_job_id: int | None = None
        self.window = None                       # diisi main.py
        self._designer_page_ref: LabelDesigner | None = None
        v = db.kv_get(self.con, "session.timeout", "15")
        self.session_timeout_min = int(v or 15)
        self.console_visible = False
        self._var_cache: dict[str, str] = {}

    # ------------------------------------------------------------------ auth
    def login(self, username: str, password: str) -> bool:
        row = self.con.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if not row:
            return False
        ok = self.backend.call("verify_password",
                               {"password": password, "stored": row["pass_hash"]})["valid"]
        if not ok:
            return False
        self.user, self.role = row["username"], row["role"]
        self.station = db.kv_get(self.con, "station.id", "ST-01")
        db.audit(self.con, self.user, "LOGIN", "users", "", f"role={self.role}")
        lot = self.con.execute("SELECT lot FROM jobs WHERE status='RUNNING' LIMIT 1").fetchone()
        self.current_lot = lot["lot"] if lot else ""
        return True

    # ---------------------------------------------------------------- vars
    def get(self, key: str, default: Any = "") -> Any:
        """Variabel binding utk label/wizard: serial/sku/lot/exp/date/weight/origin."""
        k = key.lower()
        if k == "date":
            from datetime import datetime
            return datetime.now().strftime("%Y-%m-%d")
        if k == "exp":
            return self._var_cache.get("exp", "2026-11-02")
        if k == "weight":
            return self._var_cache.get("weight", "330 ml")
        if k == "origin":
            return self._var_cache.get("origin", "ID")
        return self._var_cache.get(k, default)

    def set_var(self, **kw: str) -> None:
        self._var_cache.update({k.lower(): v for k, v in kw.items()})

    # ---------------------------------------------------------------- jobs
    def start_job(self, job_id: int) -> None:
        self.stop_job(silent=True)
        with db._LOCK:
            self.con.execute("UPDATE jobs SET status='RUNNING', updated_at=? WHERE id=?",
                             (db.now_iso(), job_id))
            self.con.commit()
        self.active_job_id = job_id
        row = self.con.execute("SELECT lot FROM jobs WHERE id=?", (job_id,)).fetchone()
        self.current_lot = row["lot"] if row else ""
        self.runner = DemoRunner(self.con, self.backend, self.station)
        self.runner.serial_issued.connect(lambda d: (self.set_var(serial=d["serial"]),
                                                     self.serialIssued.emit(d)))
        self.runner.start()
        db.audit(self.con, self.user, "START_JOB", "jobs", "", f"job={job_id}")
        self.jobStateChanged.emit("RUNNING")
        if self.toast:
            self.toast.push(f"Job #{job_id} RUNNING — counter jalan.", "ok")

    def pause_job(self) -> None:
        if self.runner:
            self.runner.paused = not self.runner.paused
            st = "PAUSED" if self.runner.paused else "RUNNING"
            if self.active_job_id:
                with db._LOCK:
                    self.con.execute("UPDATE jobs SET status=? WHERE id=?", (st, self.active_job_id))
                    self.con.commit()
            self.jobStateChanged.emit(st)
            if self.toast:
                self.toast.push(f"Job {st}.", "warn" if self.runner.paused else "ok")

    def stop_job(self, silent: bool = False) -> None:
        if self.runner:
            self.runner.stop()
            self.runner.wait(2500)
            self.runner = None
        if self.active_job_id and not silent:
            with db._LOCK:
                self.con.execute("UPDATE jobs SET status='STOPPED', updated_at=? WHERE id=?",
                                 (db.now_iso(), self.active_job_id))
                self.con.commit()
            db.audit(self.con, self.user, "STOP_JOB", "jobs", "", f"job={self.active_job_id}")
        self.active_job_id = None if not silent else self.active_job_id
        self.jobStateChanged.emit("STOPPED")
        if self.toast and not silent:
            self.toast.push("Job dihentikan.", "info")

    def start_or_toggle(self) -> None:
        if self.runner and not self.runner.paused:
            self.pause_job()
        elif self.runner and self.runner.paused:
            self.pause_job()
        elif self.active_job_id:
            self.start_job(self.active_job_id)
        else:
            row = self.con.execute("SELECT id FROM jobs WHERE status='RUNNING' LIMIT 1").fetchone()
            if row:
                self.start_job(row["id"])
            elif self.toast:
                self.toast.push("Tidak ada job — buat di Job Setup (Ctrl+N).", "warn")

    # ---------------------------------------------------------------- misc
    def attach_toasts(self, container) -> None:
        self.toast = ToastHost(container)

    def toggle_console(self) -> None:
        self.console_visible = not self.console_visible
        if self.window:
            self.window.show_console(self.console_visible)

    def designer_page(self) -> LabelDesigner:
        """Label Designer sebagai halaman embedded (nav item)."""
        if self._designer_page_ref is None:
            self._designer_page_ref = LabelDesigner(self, self.active_job_id, parent=None)
            self._designer_page_ref.setWindowFlags(
                self._designer_page_ref.windowFlags() & ~__import__(
                    "PySide6.QtCore", fromlist=["Qt"]).Qt.WindowType.Dialog)
        return self._designer_page_ref

    def show_shortcuts(self) -> None:
        from PySide6.QtWidgets import QMessageBox
        box = QMessageBox(self.window)
        box.setWindowTitle("Keyboard Shortcuts")
        box.setText(
            "Ctrl+N   job baru\nCtrl+S   simpan\nCtrl+P   cetak\nCtrl+F   cari di halaman aktif\n"
            "Ctrl+L   kunci layar\nSpace    start/pause (dashboard)\nF1       overlay ini\n"
            "F5       refresh\nCtrl+,   settings\nCtrl+`   console log\n"
            "Ctrl+Shift+E / Ctrl+Shift+I   export / import")
        box.exec()

    def license_state(self) -> str:
        row = self.con.execute("SELECT token FROM licenses ORDER BY id DESC LIMIT 1").fetchone()
        if row:
            return f"Token lisensi tersimpan: {row['token'][:60]}…"
        days_left = 30 - int(db.kv_get(self.con, "demo.days_used", "0"))
        return (f"MODE DEMO — sisa ±{max(0, days_left)} hari, batas 1000 serial/hari.")

    def shutdown(self) -> None:
        self.stop_job(silent=True)
        try:
            self.con.close()
        finally:
            self.backend.shutdown()
