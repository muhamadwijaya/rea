"""DemoJobRunner — QThread yang mensimulasikan jalur produksi (§6 mode --demo).

Setiap 300ms: issue 1 serial lewat backend (C#/fallback), insert DB, enqueue print,
update daily_stats. Sinyal keluar untuk di-consume Dashboard.
"""
from __future__ import annotations

import json
import random
from datetime import datetime

from PySide6.QtCore import QThread, Signal

from ui import db


class DemoRunner(QThread):
    serial_issued = Signal(dict)      # {serial, sig, job_id}
    printed = Signal(str)             # serial
    ticked = Signal(dict)             # stat ringkas utk HUD

    def __init__(self, con, backend, station: str = "ST-DEMO", parent=None):
        super().__init__(parent)
        self.con = con
        self.backend = backend
        self.station = station
        self._stop = False
        self.paused = False
        self.interval_ms = 300

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:  # noqa: D102
        jobs = [dict(r) for r in self.con.execute(
            "SELECT j.*, p.sku FROM jobs j JOIN products p ON p.id=j.product_id "
            "WHERE j.status='RUNNING'").fetchall()]
        if not jobs:
            return
        secret = db.kv_get(self.con, "hmac_secret", "demo-secret")
        n = 0
        while not self._stop:
            if self.paused:
                self.msleep(120)
                continue
            job = random.choice(jobs)
            with db._LOCK:
                cur_seq = self.con.execute(
                    "UPDATE counters SET last_seq=last_seq+? WHERE job_id=? RETURNING last_seq",
                    (job.get("step", 1) or 1, job["id"])).fetchone()
                seq = cur_seq[0] if cur_seq else n + 1
                self.con.commit()
            res = self.backend.call("render_serial", {
                "pattern": job["pattern"], "seq": seq, "lot": job["lot"] or "",
                "sku": job["sku"], "product": job["name"][:8], "line": job["line"],
                "station": self.station, "step": 1,
                "avoid_ambiguous": bool(job.get("avoid_ambig"))})
            serial = res["serial"]
            sig = self.backend.call("sign_serial",
                                    {"secret": secret, "serial": serial, "lot": job["lot"] or ""})["sig"]
            try:
                with db._LOCK:
                    self.con.execute(
                        "INSERT OR IGNORE INTO serial_records(serial,job_id,product_id,lot,level,"
                        "sig,status,station,issued_at) VALUES(?,?,?,?,?,?,'ISSUED',?,?)",
                        (serial, job["id"], job["product_id"], job["lot"], 1, sig,
                         self.station, db.now_iso()))
                    zpl = self.backend.call("zpl_label", {
                        "serial": serial, "sku": job["sku"], "lot": job["lot"] or "",
                        "exp": ""})["zpl"]
                    self.con.execute(
                        "INSERT INTO print_queue(job_id,serial,zpl,status,station) "
                        "VALUES(?,?,?,'QUEUED',?)", (job["id"], serial, zpl, self.station))
                    today = datetime.now().strftime("%Y-%m-%d")
                    self.con.execute(
                        "INSERT INTO daily_stats(day,produced) VALUES(?,1) "
                        "ON CONFLICT(day) DO UPDATE SET produced=produced+1", (today,))
                    self.con.commit()
            except Exception:
                pass
            n += 1
            self.serial_issued.emit({"serial": serial, "sig": sig, "job_id": job["id"]})
            # simulator cetak: beberapa detik kemudian status jadi PRINTED
            qid = self.con.execute("SELECT MAX(id) m FROM print_queue").fetchone()["m"]
            self._mark_later(qid, serial)
            self.ticked.emit({"seq": seq, "n": n, "job": job["name"]})
            self.msleep(self.interval_ms)

    def _mark_later(self, qid: int | None, serial: str) -> None:
        if qid is None:
            return
        try:
            with db._LOCK:
                self.con.execute("UPDATE print_queue SET status='PRINTED', sent_at=? WHERE id=?",
                                 (db.now_iso(), qid))
                self.con.execute("UPDATE serial_records SET status='PRINTED', printed_at=? "
                                 "WHERE serial=?", (db.now_iso(), serial))
                self.con.execute("UPDATE daily_stats SET printed=printed+1 WHERE day=?",
                                 (datetime.now().strftime("%Y-%m-%d"),))
                self.con.commit()
            self.printed.emit(serial)
        except Exception:
            pass
