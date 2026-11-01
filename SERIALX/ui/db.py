"""Database layer SERIALX — SQLite WAL, skema §4 prompt, migrasi user_version."""
from __future__ import annotations

import json
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = 1
DB_PATH = Path(__file__).resolve().parent.parent / "data" / "serialx.db"

_LOCK = threading.Lock()


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    p = Path(path or DB_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p), check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def migrate(con: sqlite3.Connection) -> None:
    ver = con.execute("PRAGMA user_version").fetchone()[0]
    if ver < 1:
        con.executescript(
            """
            CREATE TABLE users(
              id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL,
              pass_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'operator',
              must_change INTEGER NOT NULL DEFAULT 0, created_at TEXT);
            CREATE TABLE products(
              id INTEGER PRIMARY KEY, sku TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
              gtin TEXT DEFAULT '', origin TEXT DEFAULT '', exp_days INTEGER DEFAULT 365);
            CREATE TABLE jobs(
              id INTEGER PRIMARY KEY, name TEXT NOT NULL, product_id INTEGER REFERENCES products(id),
              pattern TEXT NOT NULL, lot TEXT, line TEXT DEFAULT 'L1', shift TEXT DEFAULT 'A',
              target_qty INTEGER DEFAULT 0, start_seq INTEGER DEFAULT 1, last_seq INTEGER DEFAULT 0,
              status TEXT DEFAULT 'DRAFT', priority INTEGER DEFAULT 5, hmac_secret TEXT,
              avoid_ambig INTEGER DEFAULT 0, step INTEGER DEFAULT 1, descending INTEGER DEFAULT 0,
              template_json TEXT, created_at TEXT, updated_at TEXT);
            CREATE TABLE serial_records(
              id INTEGER PRIMARY KEY, serial TEXT NOT NULL UNIQUE, job_id INTEGER REFERENCES jobs(id),
              product_id INTEGER, lot TEXT, level INTEGER DEFAULT 1, parent_id INTEGER,
              sig TEXT, status TEXT DEFAULT 'ISSUED', station TEXT, printed_at TEXT,
              verified_at TEXT, reprint_count INTEGER DEFAULT 0, note TEXT,
              issued_at TEXT DEFAULT (datetime('now')));
            CREATE INDEX idx_sr_job_status ON serial_records(job_id,status);
            CREATE INDEX idx_sr_parent ON serial_records(parent_id);
            CREATE INDEX idx_sr_printed ON serial_records(printed_at);
            CREATE TABLE aggregation_map(
              id INTEGER PRIMARY KEY, unit TEXT NOT NULL, pack TEXT, "case" TEXT, pallet TEXT,
              job_id INTEGER, UNIQUE(unit));
            CREATE TABLE print_queue(
              id INTEGER PRIMARY KEY, job_id INTEGER, serial TEXT, zpl TEXT,
              status TEXT DEFAULT 'QUEUED', station TEXT, retry INTEGER DEFAULT 0,
              created_at TEXT DEFAULT (datetime('now')), sent_at TEXT);
            CREATE TABLE audit_log(
              id INTEGER PRIMARY KEY, ts TEXT DEFAULT (datetime('now')),
              username TEXT, action TEXT, entity TEXT, before TEXT, after TEXT, detail TEXT);
            CREATE TABLE licenses(
              id INTEGER PRIMARY KEY, machine_id TEXT, token TEXT, payload TEXT,
              expires TEXT, created_at TEXT DEFAULT (datetime('now')));
            CREATE TABLE counters(
              job_id INTEGER PRIMARY KEY REFERENCES jobs(id), last_seq INTEGER DEFAULT 0,
              reserved_until INTEGER DEFAULT 0, updated_at TEXT);
            CREATE TABLE settings_kv(key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE daily_stats(day TEXT PRIMARY KEY, produced INTEGER DEFAULT 0,
              rejected INTEGER DEFAULT 0, printed INTEGER DEFAULT 0, verified INTEGER DEFAULT 0);
            CREATE TABLE blacklist(serial TEXT PRIMARY KEY, reason TEXT, created_at TEXT);
            """
        )
        con.execute("PRAGMA user_version=1")
    con.commit()


def kv_get(con: sqlite3.Connection, key: str, default: str = "") -> str:
    row = con.execute("SELECT value FROM settings_kv WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def kv_set(con: sqlite3.Connection, key: str, value: str) -> None:
    with _LOCK:
        con.execute("INSERT INTO settings_kv(key,value) VALUES(?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        con.commit()


def audit(con: sqlite3.Connection, username: str, action: str, entity: str = "",
          before: str = "", after: str = "", detail: str = "") -> None:
    with _LOCK:
        con.execute("INSERT INTO audit_log(username,action,entity,before,after,detail) "
                    "VALUES(?,?,?,?,?,?)", (username, action, entity, before, after, detail))
        con.commit()


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# --------------------------------------------------------------------------
# Seed demo data (§6 prompt)
# --------------------------------------------------------------------------
def seed_demo(con: sqlite3.Connection, backend: Any) -> None:
    """3 produk, 2 job, ±500 serial, 1 template, admin/qa/operator."""
    from ui.backend_bridge import py_hash_password  # fallback dipakai juga utk seed (cepat: iter rendah)

    def h(pw: str) -> str:
        return py_hash_password(pw, iterations=120_000)

    if con.execute("SELECT COUNT(*) c FROM users").fetchone()["c"] == 0:
        con.executemany(
            "INSERT INTO users(username,pass_hash,role,must_change,created_at) VALUES(?,?,?,?,?)",
            [("admin", h("admin123"), "admin", 0, now_iso()),
             ("operator", h("operator123"), "operator", 0, now_iso()),
             ("qa", h("qa123"), "qa", 0, now_iso())],
        )
    secret = secrets.token_hex(16)
    kv_set(con, "hmac_secret", secret)

    if con.execute("SELECT COUNT(*) c FROM products").fetchone()["c"] > 0:
        con.commit()
        return

    prods = [
        ("SKU-Energy-330", "Monster Burst Energy 330ml", "8993123450017", "ID", 270),
        ("SKU-Energy-500", "Monster Burst Energy 500ml", "8993123450024", "ID", 240),
        ("SKU-Hydro-600", "Neon Hydro Electrolyte 600ml", "8993123450031", "MY", 180),
    ]
    con.executemany("INSERT INTO products(sku,name,gtin,origin,exp_days) VALUES(?,?,?,?,?)", prods)

    template = {
        "ver": 1, "name": "Label Standar 100x150", "size_mm": [100, 150], "dpi": 300,
        "elements": [
            {"type": "text", "x": 6, "y": 8, "font_pt": 14, "bold": True,
             "content": "{{serial}}", "color": "#0B1B34"},
            {"type": "text", "x": 6, "y": 20, "font_pt": 8,
             "content": "SKU {{sku}} · LOT {{lot}} · EXP {{exp}}", "color": "#0B1B34"},
            {"type": "code128", "x": 6, "y": 30, "h_mm": 14, "content": "{{serial}}",
             "human": True},
            {"type": "qr", "x": 70, "y": 95, "size_mm": 24, "ec": "M",
             "content": "https://verify.serialx.id/?s={{serial}}"},
            {"type": "rect", "x": 4, "y": 4, "w": 92, "h": 142, "pen": "#00FF5F"},
            {"type": "text", "x": 6, "y": 132, "font_pt": 6,
             "content": "SERIALX :: INDUSTRIAL SERIALIZATION", "color": "#16305A"},
        ],
    }
    jobs = [
        ("JOB-A24/01 Burst 330", 1, "SRX{YY}{MM}{DD}-{LINE}{SEQ:6}-{CRC2}", "LOT2401",
         json.dumps(template)),
        ("JOB-B07 Hydro 600", 3, "NX-{SKU}-{SEQ:7}{LuhnCheck}", "LOT2407", json.dumps(template)),
    ]
    for name, pid, pat, lot, tpl in jobs:
        con.execute(
            "INSERT INTO jobs(name,product_id,pattern,lot,line,shift,target_qty,start_seq,"
            "last_seq,status,hmac_secret,template_json,created_at,updated_at) "
            "VALUES(?,?,?,?,'L1','A',?,0,0,'RUNNING',?,?,?,?)",
            (name, pid, pat, lot, 10000, secret, tpl, now_iso(), now_iso()))
        jid = con.execute("SELECT id FROM jobs WHERE name=?", (name,)).fetchone()["id"]
        con.execute("INSERT INTO counters(job_id,last_seq,reserved_until,updated_at) VALUES(?,?,0,?)",
                    (jid, 0, now_iso()))

    rows: list[tuple] = []
    cur = con.cursor()
    for jid, pat, lot, sku, pid in [
        (1, "SRX{YY}{MM}{DD}-{LINE}{SEQ:6}-{CRC2}", "LOT2401", "SKU-Energy-330", 1),
        (2, "NX-{SKU}-{SEQ:7}{LuhnCheck}", "LOT2407", "SKU-Hydro-600", 3),
    ]:
        n = 300 if jid == 1 else 200
        res = backend.call("render_batch", {"pattern": pat, "seq_start": 1, "count": n,
                                            "lot": lot, "sku": sku, "product": "P", "line": "L1"})
        serials: list[str] = res["serials"]
        for k, s in enumerate(serials):
            sig = backend.call("sign_serial", {"secret": secret, "serial": s, "lot": lot})["sig"]
            status = "PRINTED" if k % 9 else "ISSUED"
            tstamp = (datetime.now() - timedelta(minutes=k)).strftime("%Y-%m-%d %H:%M:%S")
            rows.append((s, jid, pid, lot, 1, sig, status, "ST01",
                         tstamp if status == "PRINTED" else None,
                         tstamp if status == "PRINTED" and k % 3 == 0 else None,
                         tstamp))
        cur.execute("UPDATE counters SET last_seq=? WHERE job_id=?", (n, jid))
        cur.execute("UPDATE jobs SET last_seq=? WHERE id=?", (n, jid))
    con.executemany(
        "INSERT OR IGNORE INTO serial_records(serial,job_id,product_id,lot,level,sig,status,"
        "station,printed_at,verified_at,issued_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", rows)

    today = datetime.now().strftime("%Y-%m-%d")
    con.execute("INSERT OR REPLACE INTO daily_stats(day,produced,rejected,printed,verified) "
                "VALUES(?,?,?,?,?)", (today, len(rows), 7,
                                      sum(1 for r in rows if r[6] == "PRINTED"),
                                      sum(1 for r in rows if r[8])))
    con.commit()


def reset_admin_password(con: sqlite3.Connection, new_pw: str) -> None:
    from ui.backend_bridge import py_hash_password
    with _LOCK:
        con.execute("UPDATE users SET pass_hash=?, must_change=0 WHERE username='admin'",
                    (py_hash_password(new_pw),))
        con.commit()
