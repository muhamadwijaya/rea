"""Jendela utama SERIALX — frameless, custom title bar, nav, splash, lock overlay."""
from __future__ import annotations

import hashlib
import platform
import uuid

from PySide6.QtCore import QPropertyAnimation, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QFrame, QGraphicsBlurEffect, QHBoxLayout, QLabel,
                               QLineEdit, QMainWindow, QPushButton, QStackedWidget,
                               QVBoxLayout, QWidget)

import logging
from pathlib import Path

from ui import db
from ui.theme.effects import LedIndicator, ToastHost, apply_glow
from ui.theme.icons import icon
from ui.theme.tokens import P, SZ
from ui.widgets.common import ClawLogo, PrimaryButton


class TitleBar(QFrame):
    """Custom title bar: logo claw, nama app, chip koneksi mesin, min/max/close."""

    def __init__(self, win: "MainWindow"):
        super().__init__(win)
        self.win = win
        self.setObjectName("TitleBar")
        self.setFixedHeight(42)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(SZ.pad_m, 0, SZ.pad_s, 0)
        lay.setSpacing(SZ.sp * 2)
        lay.addWidget(ClawLogo(24))
        name = QLabel("SERIALX")
        name.setObjectName("AppTitle")
        sub = QLabel("Industrial Serialization & Marking Suite")
        sub.setObjectName("Subtle")
        sub.setStyleSheet(f"color:{P.text_mute}; background:transparent; border:none; font-size:10px;")
        lay.addWidget(name)
        lay.addWidget(sub)
        lay.addStretch(1)
        self.chip = QLabel("● mesin: tersambung")
        self.chip.setObjectName("MachineChip")
        lay.addWidget(self.chip)
        for txt, obj, slot in (("—", "WinBtn", win.showMinimized),
                               ("▢", "WinBtn", win.toggle_max),
                               ("✕", "WinClose", win.close)):
            b = QPushButton(txt)
            b.setObjectName(obj)
            b.setFixedSize(38, 30)
            b.clicked.connect(slot)
            b.setToolTip({"—": "Minimize", "▢": "Maximize", "✕": "Close"}[txt])
            lay.addWidget(b)

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.win.frameGeometry().topLeft()
            e.accept()

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        if getattr(self, "_drag", None) and e.buttons() & Qt.MouseButton.LeftButton:
            if not self.win.isMaximized():
                self.win.move(e.globalPosition().toPoint() - self._drag)
            e.accept()

    def mouseDoubleClickEvent(self, e) -> None:  # noqa: N802
        self.win.toggle_max()


class LockOverlay(QWidget):
    """Overlay auto-lock: blur + form login ulang."""

    def __init__(self, win: "MainWindow"):
        super().__init__(win)
        self.win = win
        self.hide()
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        t = QLabel("SESSION TERKUNCI")
        t.setStyleSheet(f"color:{P.neon}; font-size:{SZ.fs_title}px; font-weight:800;"
                        f" background:transparent; border:none;")
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(t)
        self.ed_user = QLineEdit(); self.ed_user.setPlaceholderText("username")
        self.ed_pw = QLineEdit(); self.ed_pw.setPlaceholderText("password")
        self.ed_pw.setEchoMode(QLineEdit.EchoMode.Password)
        for w in (self.ed_user, self.ed_pw):
            w.setMaximumWidth(280)
            lay.addWidget(w, alignment=Qt.AlignmentFlag.AlignCenter)
        b = PrimaryButton("UNLOCK", "unlock")
        b.clicked.connect(self._try)
        self.ed_pw.returnPressed.connect(self._try)
        lay.addWidget(b, alignment=Qt.AlignmentFlag.AlignCenter)

    def _try(self) -> None:
        u, pw = self.ed_user.text().strip(), self.ed_pw.text()
        ok = self.win.ctx.login(u, pw)
        if ok:
            self.win.blur.setBlurRadius(0)
            self.hide()
            self.win.ctx.toast.push("Session dipulihkan.", "ok")
        else:
            self.win.ctx.toast.push("Kredensial salah.", "err")


class MainWindow(QMainWindow):
    """Shell aplikasi: frameless + stacked pages + idle-timeout lock + console log."""

    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.setWindowTitle("SERIALX")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        self.resize(1440, 860)
        self.setMinimumSize(1280, 720)

        shell = QFrame(); shell.setObjectName("RootShell")
        self.setCentralWidget(shell)
        root = QVBoxLayout(shell)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.title_bar = TitleBar(self)
        root.addWidget(self.title_bar)

        body = QHBoxLayout()
        body.setSpacing(0)
        nav = QFrame(); nav.setObjectName("NavPanel"); nav.setFixedWidth(210)
        nl = QVBoxLayout(nav); nl.setContentsMargins(0, SZ.pad_m, 0, SZ.pad_m)
        nl.setSpacing(2)
        self.stack = QStackedWidget()
        self.nav_btns: dict[str, QPushButton] = {}
        pages = [
            ("dashboard", "Dashboard", "dashboard"),
            ("jobs", "Job Setup", "plus"),
            ("queue", "Print Queue", "printer"),
            ("verify", "Verification", "scan"),
            ("db", "Database", "database"),
            ("agg", "Aggregation", "box"),
            ("reports", "Reports", "chart"),
            ("recovery", "Reprint/Recovery", "history"),
            ("designer", "Label Designer", "edit"),
            ("settings", "Settings", "settings"),
        ]
        from ui.pages.dashboard import DashboardPage
        from ui.pages.job_wizard_page import JobWizardLauncherPage
        from ui.pages.print_queue import PrintQueuePage
        from ui.pages.verify import VerifyPage
        from ui.pages.db_browser import DbBrowserPage
        from ui.pages.aggregate import AggregatePage
        from ui.pages.reports import ReportsPage
        from ui.pages.recovery import RecoveryPage
        from ui.pages.settings import SettingsPage
        makers = {
            "dashboard": lambda: DashboardPage(ctx),
            "jobs": lambda: JobWizardLauncherPage(ctx),
            "queue": lambda: PrintQueuePage(ctx),
            "verify": lambda: VerifyPage(ctx),
            "db": lambda: DbBrowserPage(ctx),
            "agg": lambda: AggregatePage(ctx),
            "reports": lambda: ReportsPage(ctx),
            "recovery": lambda: RecoveryPage(ctx),
            "designer": lambda: ctx.designer_page(),
            "settings": lambda: SettingsPage(ctx),
        }
        for key, label, ic in pages:
            btn = QPushButton(f"  {label}")
            btn.setObjectName("NavButton")
            btn.setCheckable(True)
            btn.setIcon(icon(ic, P.text_dim, 18))
            btn.setToolTip(label)
            btn.setStatusTip(f"Buka halaman {label}")
            page = makers[key]()
            idx = self.stack.addWidget(page)
            btn.clicked.connect(lambda _=False, i=idx, k=key: self._go(i, k))
            nl.addWidget(btn)
            self.nav_btns[key] = btn
            ROLE_MAP = {"viewer": ("dashboard", "db", "reports"),
                        "operator": ("dashboard", "jobs", "queue", "verify", "db",
                                     "agg", "reports", "recovery", "designer"),
                        "qa": ("dashboard", "verify", "db", "agg", "reports", "recovery")}
            allowed = ["dashboard"] if ctx.role == "viewer" else \
                (list(ROLE_MAP["operator"]) if ctx.role == "admin" else list(ROLE_MAP.get(ctx.role, [])))
            btn.setEnabled(key in allowed or ctx.role == "admin")
        nl.addStretch(1)
        ver = QLabel(f"backend: {ctx.backend.mode}")
        ver.setObjectName("Subtle")
        ver.setStyleSheet(f"color:{P.text_mute}; background:transparent; border:none;"
                          f" padding:8px; font-size:10px;")
        nl.addWidget(ver)
        body.addWidget(nav)

        right = QWidget()
        rl = QVBoxLayout(right); rl.setContentsMargins(0, 0, 0, 0); rl.setSpacing(0)
        rl.addWidget(self.stack, 1)
        self.statusbar = QFrame()
        self.statusbar.setStyleSheet(f"background:{P.navy_900}; border-top:1px solid {P.line};")
        sl = QHBoxLayout(self.statusbar); sl.setContentsMargins(SZ.pad_m, 4, SZ.pad_m, 4)
        self.led_run = LedIndicator("IDLE")
        self.lbl_status = QLabel("Siap.")
        self.lbl_status.setStyleSheet("background:transparent;border:none;color:#8FA6C2;font-size:11px;")
        b_console = QPushButton("Console ▴")
        b_console.setObjectName("Flat")
        b_console.clicked.connect(ctx.toggle_console)
        sl.addWidget(self.led_run); sl.addWidget(self.lbl_status); sl.addStretch(1)
        sl.addWidget(b_console)
        rl.addWidget(self.statusbar)
        body.addWidget(right, 1)
        root.addLayout(body, 1)

        self.toast_host_widget = QWidget(shell)
        self.toast_host_widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.toast_host_widget.setGeometry(0, 0, shell.width(), shell.height())
        ctx.attach_toasts(self.toast_host_widget)

        self.blur = QGraphicsBlurEffect()
        self.stack.setGraphicsEffect(self.blur)
        self.blur.setBlurRadius(0)
        self.lock = LockOverlay(self)
        self.lock.setGeometry(self.rect())
        self.lock.raise_()

        # ---- idle timeout ----------------------------------------------------
        self._idle_timer = QTimer(self)
        self._idle_timer.timeout.connect(self._maybe_lock)
        self._idle_timer.start(30_000)
        self.last_activity = QTimer().msecsSinceReference() if False else None
        self._act = QTimer(); self._act.start(1000)
        self._act.timeout.connect(self._beat)
        self._idle_secs = 0

        self.nav_btns["dashboard"].click()

    # ------------------------------------------------------------------
    def _beat(self) -> None:
        self._idle_secs += 1
        if self._idle_secs > 5 and self.lock.isVisible():
            self._idle_secs = 0

    def eventFilter(self, obj, ev) -> bool:  # noqa: D102
        return super().eventFilter(obj, ev)

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        self._idle_secs = 0

    def keyPressEvent(self, e) -> None:  # noqa: N802
        self._idle_secs = 0
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier and e.key() == Qt.Key.Key_L:
            self.do_lock()
        elif e.key() == Qt.Key.Key_Space and self.stack.currentIndex() == 0:
            self.ctx.start_or_toggle()
        elif e.key() == Qt.Key.Key_F1:
            self.ctx.show_shortcuts()
        else:
            super().keyPressEvent(e)

    def _maybe_lock(self) -> None:
        if self._idle_secs >= self.ctx.session_timeout_min * 60:
            self.do_lock()

    def do_lock(self) -> None:
        self.lock.setGeometry(self.centralWidget().rect())
        self.blur.setBlurRadius(14)
        self.lock.show(); self.lock.raise_()
        self.lock.ed_user.setFocus()
        db.audit(self.con_lock, self.ctx.user, "LOCK", "", "", "")

    @property
    def con_lock(self):
        return self.ctx.con

    def toggle_max(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def _go(self, idx: int, key: str) -> None:
        self.stack.setCurrentIndex(idx)
        for k, b in self.nav_btns.items():
            b.setChecked(k == key)
        self.lbl_status.setText(f"Halaman: {key}")

    def set_running_led(self, on: bool) -> None:
        self.led_run.update_state("RUNNING" if on else "IDLE")

    # ---------------------------------------------------------------- console
    def show_console(self, visible: bool) -> None:
        """Pane log di bawah statusbar (Ctrl+`) — tail serialx.log."""
        from PySide6.QtWidgets import QPlainTextEdit
        if not hasattr(self, "_console"):
            ed = QPlainTextEdit()
            ed.setObjectName("ConsolePane")
            ed.setReadOnly(True)
            ed.setMaximumHeight(160)
            ed.setStyleSheet("background:#04070E;color:#8FA6C2;border-top:1px solid #16305A;"
                             "font-family:Consolas,monospace;font-size:11px;")
            central = self.centralWidget().layout()
            central.addWidget(ed)
            self._console = ed
        self._console.setVisible(visible)
        if visible:
            try:
                logf = Path(__file__).resolve().parent.parent / "data" / "logs" / "serialx.log"
                txt = logf.read_text(errors="ignore")[-4000:] if logf.is_file() else "(log kosong)"
                self._console.setPlainText(txt)
                self._console.moveCursor(__import__("PySide6.QtGui", fromlist=["QTextCursor"]).QTextCursor.MoveOperation.End)
            except OSError as exc:
                self._console.setPlainText(f"Gagal baca log: {exc}")

    def refresh_current(self) -> None:
        page = self.stack.currentWidget()
        for name in ("refresh", "_auto_refresh", "_refresh_bl"):
            fn = getattr(page, name, None)
            if callable(fn):
                fn()
        d = getattr(page, "refresh", None)
        if callable(d):
            d()

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        if hasattr(self, "toast_host_widget"):
            self.toast_host_widget.setGeometry(self.centralWidget().rect())
            self.lock.setGeometry(self.centralWidget().rect())
