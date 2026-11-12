"""Live training session: one asset & timeframe trained *forward* through its last N candles.

The AI starts knowing nothing (the timeframe's default SMC setting). The candles are split into ~SEGMENTS stretches;
before each stretch it re-learns from **only the candles before it** (the same tuner as everywhere: grid, IS/OOS
check, adoption rules) and then trades that stretch with what it has learned. Every trade is therefore taken with a
setting chosen without seeing it — an anchored walk-forward — and the running win rate is out of sample throughout.

Stretches are published as they finish, so the chart can play the candles forward (×1–×7) while the next stretch is
still being learned. When the run ends, the asset is tuned on all N candles as usual and its trades go to the trade
dictionary, so what was learned is kept. One session at a time; it does not run beside the training lab.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import asdict

from . import instruments, smc, training
from .errors import EngineError
from .market_data import Bars

log = logging.getLogger("smc.session")

SEGMENTS = 10
MIN_BARS = 300
MIN_WARMUP = 200


class SessionBusy(EngineError):
    status = 409
    code = "session_busy"


_lock = threading.Lock()
_s: dict = {"state": "idle"}
_bars: Bars | None = None
_stop = threading.Event()


def _first(b: Bars, n: int) -> Bars:
    return Bars(b.time[:n], b.open[:n], b.high[:n], b.low[:n], b.close[:n], b.volume[:n], b.fetched_at)


def bounds(n: int, segments: int = SEGMENTS) -> list[tuple[int, int]]:
    """Stretches [a, z) of n candles: a warm-up stretch traded with the default setting, then equal stretches."""
    warm = max(MIN_WARMUP, n // segments)
    step = max(100, (n - warm) // max(1, segments - 1))
    edges = [0, warm] + list(range(warm + step, n, step))
    if edges[-1] != n:
        edges.append(n)
    if len(edges) > 2 and edges[-1] - edges[-2] < step // 3:  # fold a tiny last stretch into the previous one
        edges.pop(-2)
    return list(zip(edges[:-1], edges[1:]))


def _trade(b: Bars, s: training.Signal, version: int, segment: int, tf_sec: int, p: training.Params) -> dict:
    t = lambda i: int(b.time[i]) if i is not None and i < len(b) else None  # noqa: E731
    return {"created_t": t(s.created), "opened_t": t(s.opened), "closed_t": t(s.closed),
            "expiry_t": int(b.time[s.created]) + tf_sec * p.expiry, "direction": s.direction, "kind": s.kind,
            "scope": s.scope, "entry": s.entry, "stop": s.stop, "target": s.target, "status": s.status, "r": s.r,
            "manage": s.manage, "align": s.align, "version": version, "segment": segment, "live": False,
            "sample": "oos"}


def _stats(trades: list[dict]) -> dict:
    closed = [t for t in trades if t["status"] in ("win", "loss", "timeout")]
    wins = sum(1 for t in closed if (t["r"] or 0) > 0)
    total = sum(t["r"] or 0 for t in closed)
    return {"trades": len(closed), "wins": wins, "winRate": round(wins / len(closed), 4) if closed else None,
            "totalR": round(total, 2), "expectancyR": round(total / len(closed), 3) if closed else None}


def status() -> dict:
    with _lock:
        out = {**_s, "trades": list(_s.get("trades", [])), "segments": list(_s.get("segments", []))}
    out["maxBars"] = training.MAX_TRAIN_BARS
    return out


def candles() -> dict:
    """The session's candles and the structure of the default setting, for the chart (sent once per session)."""
    with _lock:
        b, info = _bars, {k: _s.get(k) for k in ("symbol", "tf", "instrument")}
    if b is None:
        return {**info, "candles": [], "smc": None}
    d = training.default_params(info["tf"])
    r = smc.analyze(b.open, b.high, b.low, b.close, d.swing, d.internal)
    word = {1: "bullish", -1: "bearish"}
    return {**info, "candles": [{"t": int(b.time[i]), "o": float(b.open[i]), "h": float(b.high[i]), "l": float(b.low[i]),
                                 "c": float(b.close[i])} for i in range(len(b))],
            "smc": {"breaks": [{"kind": x.kind, "direction": word[x.direction], "level": x.level, "scope": x.scope,
                                "fromT": int(b.time[x.pivot_index]), "toT": int(b.time[x.index])}
                               for st in (r.swing, r.internal) for x in st.breaks],
                    "pivots": [], "orderBlocks": [], "fvgs": [], "equalLevels": [], "liquidity": {"above": [], "below": []}},
            "ema": {"ema50": [], "ema200": []}, "plan": None}


def start(symbol: str, tf: str, bars: int) -> dict:
    if tf not in training.CORE_TFS:
        raise training.LabInvalid(f"Timeframe {tf} tidak dilatih (pilih {', '.join(training.CORE_TFS)}).")
    try:
        n = int(bars)
    except (TypeError, ValueError):
        raise training.LabInvalid("Jumlah candle harus angka.") from None
    if not MIN_BARS <= n <= training.MAX_TRAIN_BARS:
        raise training.LabInvalid(f"Jumlah candle {MIN_BARS} – {training.MAX_TRAIN_BARS}.")
    inst = instruments.resolve(symbol)
    global _bars
    with _lock:
        if _s.get("state") in ("loading", "running"):
            raise SessionBusy("Training live masih berjalan; hentikan dulu.")
        if training.lab_status().get("state") == "running":
            raise SessionBusy("Training lab (batch) masih berjalan; hentikan dulu.")
        _s.clear()
        _s.update({"state": "loading", "symbol": inst.symbol, "tf": tf, "bars": n, "instrument": inst.to_json(),
                   "startedAt": int(time.time()), "finishedAt": None, "error": None, "segments": [], "trades": [],
                   "computedTo": 0, "total": n, "stats": _stats([]), "saved": None})
        _bars = None
        _stop.clear()
    threading.Thread(target=_run, args=(inst, tf, n), daemon=True, name="training-session").start()
    return status()


def stop() -> dict:
    _stop.set()
    return status()


def _set(**kw) -> None:
    with _lock:
        _s.update(kw)


def _run(inst, tf: str, n: int) -> None:
    global _bars
    state = "done"
    try:
        b = training.history(inst, tf, n)
        if b is None or len(b) < MIN_BARS:
            raise training.LabInvalid(f"Riwayat {inst.symbol} {tf} terlalu pendek ({0 if b is None else len(b)} candle).")
        align = training.htf_alignment(inst, tf, b, download=True)
        with _lock:
            _bars = b
            _s.update(state="running", total=len(b))
        tf_sec = training.TF_SECONDS.get(tf, 3600)
        p, version = training.default_params(tf), 1
        trades: list[dict] = []
        for k, (a, z) in enumerate(bounds(len(b))):
            if _stop.is_set():
                state = "stopped"
                break
            event = None
            if a > 0:  # learn from the candles before this stretch only
                _set(stage=f"belajar dari {a} candle sebelumnya")
                best, report, current = training.tune(_first(b, a), tf, p, align[:a] if align is not None else None)
                event = {"bar": a, "t": int(b.time[a]), "changed": best != p, "before": current["outOfSample"],
                         "after": report["outOfSample"], "params": asdict(best)}
                if best != p:
                    p, version = best, version + 1
            _set(stage=f"trading candle {a}–{z}")
            seg = [_trade(b, s, version, k, tf_sec, p) for s in training.signals(b, p, None, align) if a <= s.created < z]
            trades += seg
            with _lock:
                _s["trades"] = trades[:]
                _s["segments"] = _s["segments"] + [{"index": k, "from": a, "to": z, "fromT": int(b.time[a]),
                                                    "toT": int(b.time[z - 1]), "version": version, "learn": event,
                                                    "stats": _stats(seg)}]
                _s["computedTo"] = z
                _s["stats"] = _stats(trades)
        if state == "done":  # keep what was learned: the usual tuning on all candles + the trade dictionary
            _set(stage="menyimpan hasil ke AI")
            upgrade = training.tune_pair(inst, tf, b, align)
            _set(saved={"upgrade": bool(upgrade), "version": training.active(inst.symbol, tf)[1]})
    except Exception as exc:  # reported in the status
        log.exception("training session failed")
        state = "error"
        _set(error=str(getattr(exc, "message", exc))[:200])
    finally:
        _set(state=state, stage=None, finishedAt=int(time.time()))


def running() -> bool:
    return _s.get("state") in ("loading", "running")
