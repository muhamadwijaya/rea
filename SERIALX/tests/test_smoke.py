"""Smoke test SERIALX — jalankan: pytest tests -q (dari folder SERIALX).

API nyata backend bridge: ping, version, validate_pattern, render_serial,
render_batch(seq_start/count), sign_serial/verify_serial, hash_password/
verify_password, zpl_label/tspl_label, agg_link/agg_descendants/agg_export,
license_verify(token), bench.
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _engine():
    from ui.backend_bridge import BackendClient
    return BackendClient()


def _batch(b, pattern, count, start=1, **kw):
    prm = {"pattern": pattern, "lot": "L1", "sku": "SKU1", "prod": "P",
           "line": "LN1", "seq_start": start, "count": count}
    prm.update(kw)
    return b.call("render_batch", prm)["serials"]


# ---------------------------------------------------------------- 1
def test_ping_and_mode():
    b = _engine()
    assert b.call("ping", {}) == "pong"
    assert b.mode in ("csharp-native", "fallback-python")
    assert "SerialX" in b.core_version


# ---------------------------------------------------------------- 2
def test_pattern_basic_tokens():
    b = _engine()
    got = _batch(b, "SRX{YY}{MM}-{SEQ:4}", 3)
    assert got == ["SRX2611-0001", "SRX2611-0002", "SRX2611-0003"]


# ---------------------------------------------------------------- 3
def test_checksum_tokens():
    b = _engine()
    for s in _batch(b, "AB{SEQ:3}{CRC2}", 5):
        assert len(s) == 7 and s.startswith("AB")
    s = _batch(b, "{SEQ:4}{LuhnCheck}", 1, start=9)[0]
    assert s.startswith("0009") and len(s) == 5


# ---------------------------------------------------------------- 4
def test_validate_pattern_rejects_garbage():
    b = _engine()
    assert b.call("validate_pattern", {"pattern": "OK{SEQ:4}"})["ok"] is True
    assert b.call("validate_pattern", {"pattern": "BAD{SEQX}"})["ok"] is False


# ---------------------------------------------------------------- 5
def test_sign_and_verify():
    b = _engine()
    sig = b.call("sign_serial", {"serial": "SRX0001", "lot": "L1", "secret": "k"})["sig"]
    ok = b.call("verify_serial", {"serial": "SRX0001", "lot": "L1", "secret": "k", "sig": sig})
    bad = b.call("verify_serial", {"serial": "TAMPER", "lot": "L1", "secret": "k", "sig": sig})
    assert ok["valid"] is True and bad["valid"] is False


# ---------------------------------------------------------------- 6
def test_password_pbkdf2_roundtrip():
    b = _engine()
    stored = b.call("hash_password", {"password": "rahasia123"})["stored"]
    assert "pbkdf2" in stored.lower() or "$" in stored
    assert b.call("verify_password", {"password": "rahasia123", "stored": stored})["valid"]
    assert not b.call("verify_password", {"password": "salah", "stored": stored})["valid"]


# ---------------------------------------------------------------- 7
def test_db_repo(tmp_path):
    from ui import db
    con = db.connect(tmp_path / "t.db")
    db.migrate(con)
    db.seed_demo(con, _engine())
    n = con.execute("SELECT COUNT(*) FROM serial_records").fetchone()[0]
    assert n >= 500
    u = con.execute("SELECT pass_hash FROM users WHERE username='admin'").fetchone()
    assert u and len(u["pass_hash"]) > 20


# ---------------------------------------------------------------- 8
def test_zpl_tspl_generators():
    b = _engine()
    z = b.call("zpl_label", {"serial": "S1", "sku": "K1", "lot": "L1", "exp": "2027"})["zpl"]
    assert z.startswith("^XA") and "^XZ" in z and "S1" in z
    t = b.call("tspl_label", {"serial": "S1", "sku": "K1"})["tspl"]
    assert "SIZE" in t and "PRINT" in t


# ---------------------------------------------------------------- 9
def test_aggregation_conflict():
    b = _engine()
    b.call("agg_link", {"child": "U1", "parent": "C1"})
    r = b.call("agg_link", {"child": "U1", "parent": "C2"})
    assert "CONFLICT" in str(r).upper() or r.get("result") != "OK"
    d = b.call("agg_descendants", {"root": "C1"})["list"]
    assert "U1" in d


# ---------------------------------------------------------------- 10
def test_license_verify_rejects_garbage():
    from ui.app import LICENSE_PUBKEY_PEM
    b = _engine()
    r = b.call("license_verify", {"token": "sam.aaa", "public_key_pem": LICENSE_PUBKEY_PEM})
    assert r["valid"] is False


# ---------------------------------------------------------------- 11
def test_no_hex_hardcoded_outside_tokens():
    bad = []
    for p in ROOT.rglob("*.py"):
        s = str(p)
        sp = s.replace("\\", "/")
        if "tokens.py" in sp or "__pycache__" in sp or "/tests/" in sp:
            continue
        src = p.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(r"(?<![\w'#])#[0-9A-Fa-f]{6}\b", src):
            bad.append(f"{p.relative_to(ROOT)}: {m.group()}")
    assert not bad, f"hex hardcoded di luar tokens.py: {bad[:6]}"


# ---------------------------------------------------------------- 12
def test_label_render_image():
    from PySide6.QtGui import QImage
    from ui.label_render import render_label
    tpl = {"size_mm": [100, 150], "elements": [
        {"type": "text", "x": 5, "y": 5, "content": "{{serial}}", "size_pt": 12},
        {"type": "qr", "x": 70, "y": 5, "w": 20, "h": 20, "content": "{{serial}}"}]}
    img = render_label(tpl, {"serial": "SRX2611-0001"}, scale=6.0)
    assert isinstance(img, QImage) and img.width() == 600


# ---------------------------------------------------------------- 13
def test_throughput_target_500_per_sec():
    b = _engine()
    t0 = time.perf_counter()
    ser = _batch(b, "B{SEQ:6}{CRC2}", 5000)
    dt = time.perf_counter() - t0
    assert len(ser) == 5000 and len(set(ser)) == 5000
    assert len(ser) / max(dt, 1e-6) > 500, f"hanya {len(ser)/dt:.0f}/dtk"
