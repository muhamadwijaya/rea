"""Jembatan Python ↔ backend C# (SerialX.Server).

Protokol: JSON-RPC 2.0 satu baris per pesan via stdin/stdout proses child.
Jika .NET tidak tersedia, otomatis memakai FALLBACK murni Python yang
implementasinya identik byte-per-byte dengan SerialX.Core.cs (diverifikasi
oleh tests/test_parity.py), sehingga aplikasi tetap berjalan.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent          # .../SERIALX
SERVER_DIR = ROOT / "backend" / "SerialX.Server"


class BackendError(RuntimeError):
    """Error dari backend (C# maupun fallback)."""


# --------------------------------------------------------------------------
# Fallback Python — cerminan persis SerialX.Core
# --------------------------------------------------------------------------
BASE32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
_AMBIG = str.maketrans({"I": "J", "O": "K", "0": "9", "1": "8"})
_KNOWN_TOKENS = {
    "YYYY", "YY", "MM", "DD", "HH", "MIN", "SEQ", "LOT", "SKU", "PROD",
    "LINE", "STATION", "RAND", "BASE32", "DATE11", "GS1AI", "LUHNCHECK",
    "MOD10", "CRC1", "CRC2", "CHECKSUM",
}


def _base32_of(value: int, length: int) -> str:
    buf = ["A"] * length
    for i in range(length - 1, -1, -1):
        buf[i] = BASE32[value % 32]
        value //= 32
    return "".join(buf)


def luhn_digit(text: str) -> int:
    digits = [int(c) for c in text if c.isdigit()]
    total, dbl = 0, True
    for d in reversed(digits):
        if dbl:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        dbl = not dbl
    return (10 - (total % 10)) % 10


def crc8(data: bytes) -> int:
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def crc1_char(text: str) -> str:
    return BASE32[crc8(text.encode()) % 32]


def crc2_str(text: str) -> str:
    c = crc8(text.encode())
    return BASE32[(c >> 5) & 0x1F] + BASE32[c & 0x1F]


def mod10(text: str) -> int:
    return sum(text.encode()) % 10


def date11(dt) -> str:
    iso = dt.isocalendar()
    return f"{dt.year % 100:02d}{iso[1]:02d}{iso[2]}"


def scrub(s: str) -> str:
    return s.translate(_AMBIG)


class _PyEngine:
    """Renderer pola serial — mirror SerialEngine.cs."""

    def __init__(self, avoid_ambiguous: bool = False) -> None:
        self.avoid_ambiguous = avoid_ambiguous

    def render_full(self, pattern: str, seq: int, ctx: dict[str, Any]) -> str:
        import datetime as _dt
        import random as _random

        now = _dt.datetime.now()
        markers = {"\u0001": "luhn", "\u0002": "crc1", "\u0003": "crc2", "\u0004": "mod10"}
        p = pattern
        for k, v in (("luhncheck", "\u0001"), ("checksum", "\u0001"), ("crc1", "\u0002"),
                     ("crc2", "\u0003"), ("mod10", "\u0004")):
            p = p.replace("{" + k.upper() + "}", v).replace("{" + k.capitalize() + "}", v)
        rng = _random.Random()
        out: list[str] = []
        i = 0
        while i < len(p):
            c = p[i]
            if c != "{":
                out.append(c)
                i += 1
                continue
            end = p.find("}", i)
            if end < 0:
                out.append(c)
                i += 1
                continue
            tok = p[i + 1:end]
            name, _, arg = tok.partition(":")
            name = name.strip().upper()
            step = max(1, int(ctx.get("step", 1) or 1))
            if name == "YYYY":
                val = f"{now.year:04d}"
            elif name == "YY":
                val = f"{now.year % 100:02d}"
            elif name == "MM":
                val = f"{now.month:02d}"
            elif name == "DD":
                val = f"{now.day:02d}"
            elif name == "HH":
                val = f"{now.hour:02d}"
            elif name == "MIN":
                val = f"{now.minute:02d}"
            elif name == "SEQ":
                width = max(1, int(arg or 6))
                v = seq * step
                if ctx.get("descending"):
                    v = max(0, int(ctx.get("max_value", 999_999_999)) - v)
                val = f"{v:0{width}d}"
            elif name in ("LOT", "SKU", "PROD", "LINE", "STATION"):
                key = {"PROD": "product", "LINE": "line", "STATION": "station"}.get(name, name.lower())
                val = str(ctx.get(key, ""))
            elif name == "RAND":
                val = _base32_of(rng.randrange(1 << 62), min(16, max(1, int(arg or 4))))
            elif name == "BASE32":
                val = _base32_of(seq, min(12, max(1, int(arg or 6))))
            elif name == "DATE11":
                val = date11(now)
            elif name == "GS1AI":
                val = "(" + (arg.strip() or "01") + ")"
            elif name in markers:
                val = markers[name]  # marker, diselesaikan di bawah
            else:
                val = "{" + tok + "}"
            out.append(val)
            i = end + 1
        partial = "".join(out)
        res: list[str] = []
        for c in partial:
            if c == "\u0001":
                res.append(str(luhn_digit("".join(res))))
            elif c == "\u0002":
                res.append(crc1_char("".join(res)))
            elif c == "\u0003":
                res.append(crc2_str("".join(res)))
            elif c == "\u0004":
                res.append(str(mod10("".join(res))))
            else:
                res.append(c)
        result = "".join(res).upper()
        return scrub(result) if self.avoid_ambiguous else result


def py_validate_pattern(pattern: str) -> tuple[bool, str]:
    if not pattern.strip():
        return False, "Pola kosong."
    depth = 0
    for i, c in enumerate(pattern):
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth < 0:
                return False, "Tanda '}' tanpa pasangan '{'."
            start = pattern.rfind("{", 0, i)
            nm = pattern[start + 1:i].split(":")[0].strip().upper()
            if nm not in _KNOWN_TOKENS:
                return False, f"Token tidak dikenal: {{{nm}}}"
    if depth != 0:
        return False, "Tanda '{' belum ditutup."
    if "{SEQ" not in pattern.upper():
        return False, "Pola wajib memuat {SEQ:n} agar unik."
    return True, "OK"


def py_sign_serial(secret: str, serial: str, lot: str) -> str:
    mac = hmac.new(secret.encode(), f"{serial}|{lot}".encode(), hashlib.sha256).digest()
    bits = value = 0
    out: list[str] = []
    for b in mac:
        value = (value << 8) | b
        bits += 8
        while bits >= 5:
            out.append(BASE32[(value >> (bits - 5)) & 31])
            bits -= 5
    if bits > 0:
        out.append(BASE32[(value << (5 - bits)) & 31])
    return "".join(out)[:8]


def py_hash_password(password: str, salt: str | None = None, iterations: int = 120_000) -> str:
    salt = salt or os.urandom(16).hex().upper()
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), iterations, 32)
    return f"{salt}${dk.hex().upper()}${iterations}"


def py_verify_password(password: str, stored: str) -> bool:
    parts = stored.split("$")
    if len(parts) != 3:
        return False
    check = py_hash_password(password, parts[0], int(parts[2]))
    return hmac.compare_digest(check.split("$")[1], parts[1])


class _AggTree:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}
        self.children: dict[str, set[str]] = {}
        self.nodes: set[str] = set()

    def link(self, child: str, parent: str) -> str:
        if not child.strip() or not parent.strip():
            return "NotFound"
        self.nodes.update((child, parent))
        cur = self.parent.get(child)
        if cur is not None and cur != parent:
            return "Conflict"
        x, seen = parent, set()
        while x in self.parent:
            if x in seen:
                break
            seen.add(x)
            if self.parent[x] == child:
                return "Cycle"
            x = self.parent[x]
        self.parent[child] = parent
        self.children.setdefault(parent, set()).add(child)
        return "Ok"

    def descendants(self, root: str) -> list[str]:
        res, queue, visited = [], [root], {root}
        while queue:
            for c in self.children.get(queue.pop(0), ()):
                if c not in visited:
                    visited.add(c)
                    res.append(c)
                    queue.append(c)
        return res

    def ancestry(self, node: str) -> list[str]:
        chain, cur, guard = [], node, set()
        while cur in self.parent and cur not in guard:
            guard.add(cur)
            cur = self.parent[cur]
            chain.append(cur)
        return chain

    def export(self, gtin: str = "0890000000000") -> str:
        roots = sorted(n for n in self.nodes if n not in self.parent)
        events = [
            {"type": "ObjectEvent", "epc": r, "parentChain": self.ancestry(r),
             "childCount": len(self.descendants(r)), "gtin": gtin}
            for r in roots
        ]
        return json.dumps({"context": "https://ref.gs1.org/standards/epcis/",
                           "events": events}, indent=2)


def _py_dispatch(method: str, prm: dict[str, Any]) -> Any:
    eng = _PyEngine(bool(prm.get("avoid_ambiguous")))
    if method == "ping":
        return "pong"
    if method == "version":
        return {"name": "SerialX.Fallback", "ver": "1.0.0-py", "runtime": "python"}
    if method == "validate_pattern":
        ok, msg = py_validate_pattern(prm.get("pattern", ""))
        return {"ok": ok, "message": msg}
    if method == "render_serial":
        return {"serial": eng.render_full(prm.get("pattern", ""), int(prm.get("seq", 0)), prm)}
    if method == "render_batch":
        n = min(max(int(prm.get("count", 1)), 1), 100_000)
        start = int(prm.get("seq_start", 1))
        return {"serials": [eng.render_full(prm.get("pattern", ""), start + k, prm) for k in range(n)],
                "count": n}
    if method == "sign_serial":
        return {"sig": py_sign_serial(prm.get("secret", ""), prm.get("serial", ""), prm.get("lot", ""))}
    if method == "verify_serial":
        want = py_sign_serial(prm.get("secret", ""), prm.get("serial", ""), prm.get("lot", ""))
        return {"valid": want == (prm.get("sig", "").strip().upper())}
    if method == "hash_password":
        return {"stored": py_hash_password(prm.get("password", ""))}
    if method == "verify_password":
        return {"valid": py_verify_password(prm.get("password", ""), prm.get("stored", ""))}
    if method == "zpl_label":
        return {"zpl": ZPL.build(prm.get("serial", ""), prm.get("sku", ""),
                                 prm.get("lot", ""), prm.get("exp", ""))}
    if method == "tspl_label":
        return {"tspl": TSPL.build(prm.get("serial", ""), prm.get("sku", ""))}
    raise BackendError(f"Method butuh state/tidak ada di fallback: {method}")


# --------------------------------------------------------------------------
# ZPL/TSPL python mirror (untuk preview simulator; output nyata dari C#)
# --------------------------------------------------------------------------
class ZPL:
    @staticmethod
    def build(serial: str, sku: str, lot: str, exp: str, dpi: int = 203) -> str:
        px = lambda mm: round(mm * dpi / 25.4)  # noqa: E731
        esc = lambda v: str(v).replace("^", " ").replace("~", " ")  # noqa: E731
        return (
            "^XA\n"
            f"^PW{px(100)}\n^LL{px(150)}\n^CI28\n"
            f"^FO{px(6)},{px(8)}^AAN,{round(24 * dpi / 203)},0^FD{esc(serial)}^FS\n"
            f"^FO{px(6)},{px(20)}^AAN,{round(14 * dpi / 203)},0^FDArtikel: {esc(sku)}  LOT: {esc(lot)}  EXP: {esc(exp)}^FS\n"
            f"^FO{px(6)},{px(30)}^BY{round(2 * dpi / 203)}^BCN,{round(60 * dpi / 203)},Y,N,A^FD>;{esc(serial)}^FS\n"
            f"^FO{px(70)},{px(95)}^BQN,2,{max(2, round(5 * dpi / 203))}^FDLA,{esc(serial)}^FS\n"
            f"^FO{px(6)},{px(120)}^AAN,{round(12 * dpi / 203)},0^FDSERIALX :: TERVERIFIKASI^FS\n"
            "^PQ1\n^XZ\n"
        )


class TSPL:
    @staticmethod
    def build(serial: str, sku: str) -> str:
        return "\n".join([
            "SIZE 100 mm,150 mm", "GAP 2 mm,2 mm", "CLS",
            f'TEXT 20,20,"TTF-EN",1,1,0,"{serial}"',
            f'TEXT 20,60,"TTF-EN",1,1,0,"SKU {sku}"',
            f'BARCODE 20,100,"128",100,1,0,2.0, "{serial}"',
            f'BARCODE 400,100,"QRCODE",6,A,0,"{serial}"',
            "PRINT 1,1", "",
        ])


# --------------------------------------------------------------------------
# Client utama
# --------------------------------------------------------------------------
class BackendClient:
    """Klien JSON-RPC ke serialx-server (C#). Auto-fallback ke Python."""

    def __init__(self, server_path: str | None = None) -> None:
        self._lock = threading.Lock()
        self._id = 0
        self._proc: subprocess.Popen[str] | None = None
        self.mode = "fallback-python"
        exe = server_path or self._find_server_binary()
        if exe:
            try:
                self._proc = subprocess.Popen(
                    [exe], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, text=True, encoding="utf-8", bufsize=1,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                r = self._rpc("ping", {})
                if r == "pong":
                    self.mode = "csharp-native"
                    ver = self._rpc("version", {})
                    self.core_version = f"{ver.get('name')} {ver.get('ver')} (.NET {ver.get('runtime')})"
                else:
                    self._kill()
            except (OSError, BackendError):
                self._kill()
        if self.mode.startswith("fallback"):
            self.core_version = "SerialX.Fallback 1.0.0-py (Python parity engine)"
            self._agg = _AggTree()

    def _kill(self) -> None:
        if self._proc:
            try:
                self._proc.kill()
            except OSError:
                pass
            self._proc = None

    @staticmethod
    def _find_server_binary() -> str | None:
        env = os.environ.get("SERIALX_SERVER")
        if env and Path(env).is_file():
            return env
        cand_dirs = [
            SERVER_DIR / "bin" / "Release" / "net8.0",
            SERVER_DIR / "bin" / "Debug" / "net8.0",
            SERVER_DIR / "bin" / "publish",
            ROOT / "bin",
        ]
        names = ["serialx-server.exe", "serialx-server"]
        for d in cand_dirs:
            for n in names:
                f = d / n
                if f.is_file():
                    return str(f)
        dotnet = shutil.which("dotnet")
        if dotnet and (SERVER_DIR / "SerialX.Server.csproj").is_file():
            return None  # dicoba build-on-demand oleh scripts/build_backend.ps1
        return None

    # ---------------- RPC --------------------------------------------------
    def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        params = params or {}
        if self._proc and self.mode == "csharp-native":
            return self._rpc(method, params)
        if method == "agg_link":
            return {"result": self._agg.link(params.get("child", ""), params.get("parent", "")),
                    "nodes": len(self._agg.nodes)}
        if method == "agg_descendants":
            return {"list": self._agg.descendants(params.get("root", ""))}
        if method == "agg_export":
            return {"json": self._agg.export(params.get("gtin", "0890000000000"))}
        if method == "bench":
            import time
            t0 = time.perf_counter()
            res = _py_dispatch("render_batch", {**params, "count": min(int(params.get("count", 50000)), 50000)})
            el = time.perf_counter() - t0
            return {"count": res["count"], "elapsed_ms": int(el * 1000),
                    "per_second": int(res["count"] / max(0.001, el)), "sample_len": 0}
        return _py_dispatch(method, params)

    def _rpc(self, method: str, params: dict[str, Any]) -> Any:
        assert self._proc and self._proc.stdin and self._proc.stdout
        with self._lock:
            self._id += 1
            rid = str(self._id)
            req = json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
            try:
                self._proc.stdin.write(req + "\n")
                self._proc.stdin.flush()
                line = self._proc.stdout.readline()
            except (BrokenPipeError, ValueError) as exc:
                raise BackendError("Backend C# berhenti; jalankan ulang aplikasi.") from exc
            if not line:
                raise BackendError("Tidak ada respons dari backend C#.")
            resp = json.loads(line)
            if "error" in resp:
                raise BackendError(resp["error"].get("message", "unknown"))
            return resp.get("result")

    def shutdown(self) -> None:
        if self._proc:
            try:
                if self._proc.stdin:
                    self._proc.stdin.close()
                self._proc.wait(timeout=3)
            except Exception:
                self._kill()
