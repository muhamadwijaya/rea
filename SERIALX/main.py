"""SERIALX — Industrial Serialization & Marking Suite.

Entry point. Jalankan:  python main.py [--demo] [--nospash]
UI   : PySide6 (tema ABYSS NEON — navy + hijau neon menyala)
Core : C# .NET 8 (SerialX.Server, JSON-RPC stdin/stdout) dengan fallback Python parity.
"""
from __future__ import annotations

import argparse
import logging
import logging.handlers
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def setup_logging() -> None:
    logdir = ROOT / "data" / "logs"
    logdir.mkdir(parents=True, exist_ok=True)
    h = logging.handlers.RotatingFileHandler(logdir / "serialx.log", maxBytes=10_485_760, backupCount=5, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(h)
    sh = logging.StreamHandler()
    sh.setFormatter(logging.Formatter("%(levelname)-7s %(name)s: %(message)s"))
    root.addHandler(sh)


def make_splash():
    """Splash 'power-on' §1.4 — bg void, claw logo flicker lalu stabil."""
    from PySide6.QtCore import Qt, QRectF
    from PySide6.QtGui import QColor, QPainter
    from PySide6.QtWidgets import QSplashScreen

    from ui.theme.effects import draw_claw
    from ui.theme.tokens import P

    class Splash(QSplashScreen):
        def drawContents(self, painter: QPainter) -> None:  # noqa: N802
            painter.fillRect(self.rect(), QColor(P.void))
            r = QRectF(self.rect()).adjusted(0, 40, 0, -60)
            draw_claw(painter, r, P.neon, scale=0.9)
            painter.setPen(QColor(P.neon_bright))
            f = painter.font(); f.setPointSize(22); f.setBold(True)
            painter.setFont(f)
            painter.drawText(r, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter, "SERIALX")
            painter.setPen(QColor(P.text_dim))
            f2 = painter.font(); f2.setPointSize(10); f2.setBold(False)
            painter.setFont(f2)
            painter.drawText(r, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter,
                             "Industrial Serialization & Marking Suite\nABYSS NEON theme · backend C#/.NET")

    sp = Splash()
    sp.showMessage("Memuat tema…", Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter, QColor(P.neon))
    return sp


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="SERIALX")
    ap.add_argument("--demo", action="store_true", help="jalankan simulasi produksi otomatis")
    ap.add_argument("--nospash", action="store_true", help="tanpa splash screen")
    args, rest = ap.parse_known_args(argv)

    setup_logging()
    log = logging.getLogger("serialx.main")

    from PySide6.QtCore import QTimer, Qt
    from PySide6.QtGui import QShortcut, QKeySequence
    from PySide6.QtWidgets import QApplication

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication
    try:
        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    except Exception:
        pass
    app = QApplication(sys.argv[:1] + rest)
    app.setApplicationName("SERIALX")
    app.setOrganizationName("SerialX Industries")

    from ui.app import AppContext, LoginDialog
    from ui.theme.tokens import P

    ctx = AppContext(demo=args.demo)
    ctx.theme.apply_global()
    log.info("Backend mode: %s (%s)", ctx.backend.mode, ctx.backend.core_version)

    splash = None if args.nospash else make_splash()
    if splash:
        splash.show()
        app.processEvents()

    from ui.main_window import MainWindow

    win = MainWindow(ctx)
    ctx.window = win

    def finish_boot() -> None:
        if splash:
            splash.finish(win)
        # login pertama
        if not ctx.user:
            dlg = LoginDialog(ctx)
            if dlg.exec() != dlg.DialogCode.Accepted:
                app.quit()
                return
        win.show()
        # resume prompt bila ada job RUNNING saat restart (§7.5)
        running = ctx.con.execute(
            "SELECT id,name FROM jobs WHERE status='RUNNING' LIMIT 1").fetchone()
        if running:
            from PySide6.QtWidgets import QMessageBox
            box = QMessageBox(win)
            box.setWindowTitle("Resume job?")
            box.setText(f"Job '{running['name']}' berstatus RUNNING saat shutdown.\nLanjutkan dari counter terakhir?")
            box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if box.exec() == QMessageBox.StandardButton.Yes:
                ctx.start_job(running["id"])
        elif ctx.demo_mode:
            row = ctx.con.execute("SELECT id FROM jobs LIMIT 1").fetchone()
            if row:
                ctx.start_job(row["id"])
        # shortcut global §5
        sc = {
            "Ctrl+N": lambda: _nav(win, "jobs"),
            "Ctrl+F": lambda: _focus_search(win),
            "Ctrl+P": lambda: _nav(win, "queue"),
            "Ctrl+L": win.do_lock,
            "F5": lambda: win.refresh_current(),
            "Ctrl+,": lambda: _nav(win, "settings"),
            "Ctrl+`": ctx.toggle_console,
            "Ctrl+Shift+E": lambda: ctx.toast and ctx.toast.push("Export cepat: buka halaman Reports.", "info"),
            "Ctrl+Shift+I": lambda: ctx.toast and ctx.toast.push("Import .slx: buka Label Designer → Import.", "info"),
        }
        for seq, fn in sc.items():
            s = QShortcut(QKeySequence(seq), win)
            try:  # Qt >= 6.8 memindahkan enum ini ke QtCore.Qt.ShortcutContext
                s.setContext(Qt.ContextWidgetContext.WidgetWithChildrenShortcut)
            except AttributeError:
                s.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            s.activated.connect(fn)
        if splash is None and not ctx.user:
            pass

    QTimer.singleShot(2000 if not args.nospash else 100, finish_boot)

    about = ctx.shutdown
    app.aboutToQuit.connect(about)
    return app.exec()


def _nav(win, key: str) -> None:
    btn = win.nav_btns.get(key)
    if btn and btn.isEnabled():
        btn.click()


def _focus_search(win) -> None:
    from PySide6.QtWidgets import QLineEdit
    page = win.stack.currentWidget()
    le = page.findChild(QLineEdit)
    if le:
        le.setFocus()
        le.selectAll()


if __name__ == "__main__":
    raise SystemExit(main())
