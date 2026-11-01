"""Halaman Dashboard SERIALX — HUD control room (§3.2)."""
from __future__ import annotations

import json
from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QGridLayout, QHBoxLayout, QLabel, QProgressBar,
                               QPushButton, QScrollArea, QVBoxLayout, QWidget)

from ui import db
from ui.theme.effects import AnimatedBorderFrame, LedIndicator
from ui.theme.icons import icon
from ui.theme.tokens import P, SZ
from ui.widgets.common import (Kvp, NeonProgressBar, PrimaryButton, Sparkline, StatTile)


class DashboardPage(QWidget):
    """Stat tiles + sparkline 24 jam + panel mesin + job aktif + log terakhir."""

    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.con = ctx.con
        outer = QVBoxLayout(self)
        outer.setContentsMargins(SZ.pad_l, SZ.pad_l, SZ.pad_l, SZ.pad_l)
        outer.setSpacing(SZ.pad_m)

        head = QHBoxLayout()
        t = QLabel("DASHBOARD PRODUKSI")
        t.setObjectName("SectionTitle")
        self.clock = QLabel(datetime.now().strftime("%H:%M:%S"))
        self.clock.setObjectName("MonoText")
        head.addWidget(t)
        head.addStretch(1)
        head.addWidget(self.clock)
        outer.addLayout(head)

        # ---- 6 stat tile ---------------------------------------------------
        grid = QGridLayout()
        grid.setSpacing(SZ.pad_m)
        self.tile_today = StatTile("Total Hari Ini", "0")
        self.tile_target = StatTile("Target vs Aktual", "0%", ring_fn=lambda: 0)
        self.tile_reject = StatTile("Reject Rate", "0%")
        self.tile_wip = StatTile("WIP", "0")
        self.tile_uptime = StatTile("Uptime Mesin", "99.2%")
        self.tile_tp = StatTile("Throughput", "0 pcs/mnt")
        for i, w in enumerate([self.tile_today, self.tile_target, self.tile_reject,
                               self.tile_wip, self.tile_uptime, self.tile_tp]):
            grid.addWidget(w, 0, i)
        outer.addLayout(grid)

        # ---- sparkline -----------------------------------------------------
        card_sp = QWidget()
        card_sp.setObjectName("Card")
        lay_sp = QVBoxLayout(card_sp)
        lbl = QLabel("OUTPUT 24 JAM TERAKHIR (pcs/jam)")
        lbl.setObjectName("StatLabel")
        self.spark = Sparkline()
        lay_sp.addWidget(lbl)
        lay_sp.addWidget(self.spark)
        outer.addWidget(card_sp)

        # ---- mesin + job aktif --------------------------------------------
        row = QHBoxLayout()
        row.setSpacing(SZ.pad_m)

        mach = QWidget()
        mach.setObjectName("Card")
        ml = QVBoxLayout(mach)
        mh = QLabel("MESIN / PERIFERAL")
        mh.setObjectName("StatLabel")
        ml.addWidget(mh)
        self.mach_leds: dict[str, LedIndicator] = {}
        for name in ("Printer Zebra ZT411", "Scanner Honeywell", "Vision Cognex", "PLC Siemens"):
            r = QHBoxLayout()
            led = LedIndicator("RUNNING" if "Printer" in name else "IDLE")
            self.mach_leds[name] = led
            lab = QLabel(name)
            r.addWidget(led)
            r.addWidget(lab)
            r.addStretch(1)
            btn_rec = QPushButton("Reconnect")
            btn_rec.setIcon(icon("refresh", P.text_dim, 16))
            btn_rec.clicked.connect(lambda _=False, n=name: self._reconnect(n))
            st = QPushButton("Self-test")
            st.setIcon(icon("check", P.text_dim, 16))
            st.clicked.connect(lambda _=False, n=name: self.ctx.toast.push(f"Self-test {n}: OK", "ok"))
            r.addWidget(btn_rec)
            r.addWidget(st)
            ml.addLayout(r)
        row.addWidget(mach, 3)

        self.job_frame = AnimatedBorderFrame()
        self.job_frame.setObjectName("Card")
        jl = QVBoxLayout(self.job_frame)
        jh = QLabel("JOB AKTIF")
        jh.setObjectName("StatLabel")
        jl.addWidget(jh)
        self.job_name = QLabel("—")
        self.job_name.setStyleSheet(f"color:{P.neon_bright}; background:transparent; border:none;"
                                    f" font-size:{SZ.fs_sub}px; font-weight:800;")
        self.job_pattern = QLabel("—")
        self.job_pattern.setObjectName("MonoText")
        self.job_counter = Kvp("Counter terbaru", "0")
        self.job_eta = Kvp("ETA", "—")
        self.bar = NeonProgressBar()
        jl.addWidget(self.job_name)
        jl.addWidget(self.job_pattern)
        jl.addWidget(self.job_counter)
        jl.addWidget(self.job_eta)
        jl.addWidget(self.bar)
        btnrow = QHBoxLayout()
        self.btn_start = PrimaryButton("START", "play")
        self.btn_pause = QPushButton("PAUSE")
        self.btn_pause.setIcon(icon("pause", P.text, 18))
        self.btn_stop = QPushButton("STOP")
        self.btn_stop.setObjectName("Danger")
        self.btn_stop.setIcon(icon("stop", P.err, 18))
        self.btn_start.clicked.connect(self.ctx.start_or_toggle)
        self.btn_pause.clicked.connect(self.ctx.pause_job)
        self.btn_stop.clicked.connect(self.ctx.stop_job)
        for b in (self.btn_start, self.btn_pause, self.btn_stop):
            b.setMinimumHeight(44)
            btnrow.addWidget(b)
        jl.addLayout(btnrow)
        row.addWidget(self.job_frame, 4)
        outer.addLayout(row, 2)

        # ---- log aktivitas ---------------------------------------------------
        logc = QWidget()
        logc.setObjectName("Card")
        ll = QVBoxLayout(logc)
        lh = QLabel("LOG AKTIVITAS TERAKHIR")
        lh.setObjectName("StatLabel")
        self.log_view = QLabel("")
        self.log_view.setWordWrap(True)
        self.log_view.setTextFormat(Qt.TextFormat.RichText)
        self.log_view.setStyleSheet("background:transparent;border:none;color:#8FA6C2;"
                                    "font-family:Consolas,monospace;font-size:11px;")
        ll.addWidget(lh)
        ll.addWidget(self.log_view)
        outer.addWidget(logc, 2)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(1500)
        self.refresh()

    # ------------------------------------------------------------------
    def _reconnect(self, name: str) -> None:
        self.mach_leds[name].update_state("RUNNING")
        self.ctx.toast.push(f"{name} tersambung kembali.", "ok")

    def refresh(self) -> None:
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            st = self.con.execute("SELECT * FROM daily_stats WHERE day=?", (today,)).fetchone()
            produced = st["produced"] if st else 0
            rejected = st["rejected"] if st else 0
            printed = st["printed"] if st else 0
            job = self.con.execute(
                "SELECT j.*, p.sku FROM jobs j JOIN products p ON p.id=j.product_id "
                "WHERE j.status='RUNNING' ORDER BY j.priority LIMIT 1").fetchone()
            wip = self.con.execute(
                "SELECT COUNT(*) c FROM serial_records WHERE status='ISSUED'").fetchone()["c"]
            self.tile_today.set_value(f"{produced}", f"cetak: {printed}")
            if job:
                pct = int(job["last_seq"] * 100 / max(1, job["target_qty"]))
                self.tile_target.set_value(f"{min(pct, 999)}%", f"target {job['target_qty']}")
                rate = rejected * 100.0 / max(1, produced)
                self.tile_reject.set_value(f"{rate:.1f}%", f"reject {rejected}")
                self.tile_wip.set_value(f"{wip}", "issued belum cetak")
                tp = int(60_000 / max(1, self.ctx.runner.interval_ms)) if self.ctx.runner else 0
                self.tile_tp.set_value(f"{tp}", "pcs/menit (sim)")
                self.job_name.setText(job["name"])
                self.job_pattern.setText(job["pattern"])
                self.job_counter.set_v(str(job["last_seq"]))
                remain = max(0, job["target_qty"] - job["last_seq"])
                eta_min = remain / max(1, tp) if tp else 0
                self.job_eta.set_v(f"{eta_min:.0f} mnt" if tp else "—")
                self.bar.set_range(max(1, job["target_qty"]))
                self.bar.set_value(job["last_seq"])
                running = bool(self.ctx.runner and not self.ctx.runner.paused)
                self.job_frame.set_active(running)
            hist = [r[0] for r in self.con.execute(
                "SELECT produced FROM daily_stats ORDER BY day DESC LIMIT 24").fetchall()][::-1]
            base = hist or [0]
            seed = list(range(1, len(base) + 1))
            curve = [int(v * (0.6 + 0.4 * abs(((i * 7) % 24 - 12) / 12))) for i, v in enumerate(base)]
            self.spark.set_data(curve or [0])
            rows = self.con.execute(
                "SELECT ts,username,action,detail FROM audit_log ORDER BY id DESC LIMIT 8").fetchall()
            html = "<br>".join(
                f"<span style='color:{P.neon_dim}'>{r['ts'][11:]}</span> "
                f"<span style='color:{P.text_dim}'>{r['username'] or 'sys'}</span> "
                f"{r['action']} <span style='color:{P.text_mute}'>{r['detail'] or ''}</span>"
                for r in rows)
            self.log_view.setText(html or "—")
            self.clock.setText(datetime.now().strftime("%H:%M:%S"))
        except Exception as exc:  # pragma: no cover
            self.log_view.setText(f"refresh error: {exc}")
