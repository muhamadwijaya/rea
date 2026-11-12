"""Live training session: one asset & timeframe trained *forward* through its last N candles.

The AI starts knowing nothing (the timeframe's default SMC setting). The candles are split into ~SEGMENTS stretches;
before each stretch it re-learns from **only the candles before it** (the same tuner as everywhere: grid, IS/OOS
check, adoption rules) and then trades that stretch with what it has learned. Every trade is therefore taken with a
setting chosen without seeing it — an anchored walk-forward — and the running win rate is out of sample throughout.

Stretches are published as they finish, so the chart can play the candles forward (×1–×7) while the next stretch is
still being learned.

"Terus perbaiki" (improve): the last EXAM_SHARE of the candles are locked away. On the rest, the forward training is
repeated with an ever wider search (training.MAX_LEVEL) until the goal is reached or a round brings no improvement;
the best round is kept. Only then is its setting run once over the locked candles — the final exam, the honest
number, since nothing was chosen on them. When the run ends, the asset is tuned on all N candles as usual and its trades go to the trade
dictionary, so what was learned is kept. One session at a time; it does not run beside the training lab.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import asdict, replace

from . import instruments, market_data, smc, training
from .errors import EngineError
from .market_data import Bars

log = logging.getLogger("smc.session")

SEGMENTS = 10
MIN_BARS = 300
MIN_WARMUP = 200
EXAM_SHARE = 0.2
MIN_EXAM = 150
GOAL_TRADES = 30  # a round counts as reaching the goal only with at least this many trades


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


def _trade(b: Bars, s: training.Signal, version: int, segment: int, tf_sec: int, p: training.Params, exam: bool = False) -> dict:
    t = lambda i: int(b.time[i]) if i is not None and i < len(b) else None  # noqa: E731
    return {"created_t": t(s.created), "opened_t": t(s.opened), "closed_t": t(s.closed), "tp1_t": t(s.tp1_hit),
            "expiry_t": int(b.time[s.created]) + tf_sec * p.expiry, "direction": s.direction, "kind": s.kind,
            "scope": s.scope, "entry": s.entry, "stop": s.stop, "target": s.target, "tp1": s.tp1, "status": s.status,
            "r": s.r, "exit": s.exit, "reason": s.reason, "fromT": t(s.pivot), "level": s.level, "manage": s.manage,
            "align": s.align, "zone_top": s.zone_top, "zone_bottom": s.zone_bottom, "ict": s.ict, "version": version,
            "segment": segment, "live": False, "sample": "oos", "exam": exam}


def _trades(b: Bars, raw: list, tf_sec: int) -> list[dict]:
    """Trades of all stretches so far, one position at a time across them (a stretch's setting may change, the rule
    does not): `raw` holds (signal simulated on its own, version, stretch, params)."""
    sigs = training.one_at_a_time([replace(s) for s, *_ in raw])
    return [_trade(b, s, version, k, tf_sec, p) for s, (_, version, k, p) in zip(sigs, raw)]


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
    out["maxRounds"] = training.MAX_LEVEL + 1
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


def start(symbol: str, tf: str, bars: int, improve: bool = False) -> dict:
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
                   "computedTo": 0, "total": n, "stats": _stats([]), "saved": None, "improve": bool(improve),
                   "rounds": [], "exam": None, "goal": training.GOAL_WIN_RATE, "reached": False, "hint": None})
        _bars = None
        _stop.clear()
    threading.Thread(target=_run, args=(inst, tf, n, bool(improve)), daemon=True, name="training-session").start()
    return status()


def stop() -> dict:
    _stop.set()
    return status()


def _set(**kw) -> None:
    with _lock:
        _s.update(kw)


def _forward(b: Bars, tf: str, align, level: int, publish) -> tuple[list[dict], list[dict], training.Params, int] | None:
    """Anchored walk-forward over `b`; `publish(trades, segments, upto)` after each stretch (None: stopped)."""
    tf_sec = training.TF_SECONDS.get(tf, 3600)
    p, version = training.default_params(tf), 1
    raw: list = []
    trades: list[dict] = []
    segments: list[dict] = []
    for k, (a, z) in enumerate(bounds(len(b))):
        if _stop.is_set():
            return None
        event = None
        if a > 0:  # learn from the candles before this stretch only
            best, report, current = training.tune(_first(b, a), tf, p, align[:a] if align is not None else None, level)
            event = {"bar": a, "t": int(b.time[a]), "changed": best != p, "before": current["outOfSample"],
                     "after": report["outOfSample"], "params": asdict(best)}
            if best != p:
                p, version = best, version + 1
        raw += [(s, version, k, p) for s in training.signals(b, p, None, align, one_position=False) if a <= s.created < z]
        trades = _trades(b, raw, tf_sec)
        segments.append({"index": k, "from": a, "to": z, "fromT": int(b.time[a]), "toT": int(b.time[z - 1]),
                         "version": version, "learn": event})
        for sg in segments:  # a new stretch's trade can keep an earlier stretch's late fill from opening
            sg["stats"] = _stats([t for t in trades if t["segment"] == sg["index"]])
        if publish:
            publish(trades, segments, z)
    return trades, segments, p, version


def _publish(trades, segments, upto) -> None:
    with _lock:
        _s.update(trades=trades[:], segments=segments[:], computedTo=upto, stats=_stats(trades),
                  grades=training.grade_metrics(trades))


def _run(inst, tf: str, n: int, improve: bool) -> None:
    global _bars
    state = "done"
    try:
        b = training.history(inst, tf, n)
        if b is None:  # the long history could not be fetched (e.g. Yahoo rate limit): the chart's bars, or why not
            try:
                b = market_data.load(inst, tf)
            except Exception as exc:
                raise training.LabInvalid(f"Data harga {inst.symbol} {tf} tidak bisa diambil dari Yahoo saat ini "
                                          f"({getattr(exc, 'message', exc)}). Coba lagi sebentar lagi.") from exc
        if len(b) < MIN_BARS:
            raise training.LabInvalid(f"Riwayat {inst.symbol} {tf} terlalu pendek ({0 if b is None else len(b)} candle).")
        align = training.htf_alignment(inst, tf, b, download=True)
        spec = training.contract(inst)  # what one lot is, for the balance / lot inputs
        with _lock:
            _bars = b
            _s.update(state="running", total=len(b), contract=spec)
        level = 0
        if not improve:
            _set(stage="training maju dari candle terlama")
            if _forward(b, tf, align, 0, _publish) is None:
                state = "stopped"
        else:
            level = _improve(b, tf, align)
            if level is None:
                state = "stopped"
        if state == "done":  # keep what was learned: the usual tuning on all candles + the trade dictionary
            _set(stage="menyimpan hasil ke AI")
            upgrade = training.tune_pair(inst, tf, b, align, level)
            _set(saved={"upgrade": bool(upgrade), "version": training.active(inst.symbol, tf)[1], "level": level})
    except Exception as exc:  # reported in the status
        log.exception("training session failed")
        state = "error"
        _set(error=str(getattr(exc, "message", exc))[:200])
    finally:
        _set(state=state, stage=None, finishedAt=int(time.time()))


def _improve(b: Bars, tf: str, align) -> int | None:
    """Improvement rounds on the candles before the exam, each with a wider search; then the final exam. Returns the
    search level of the best round (None: stopped)."""
    cut = max(MIN_BARS, len(b) - max(MIN_EXAM, int(len(b) * EXAM_SHARE)))
    work = _first(b, cut)
    work_align = align[:cut] if align is not None else None
    best = None
    rounds: list[dict] = []
    for level in range(training.MAX_LEVEL + 1):
        combos = training.grid_size(tf, level)
        _set(stage=f"putaran {level + 1}: belajar maju dengan {combos} kombinasi SMC")
        first = best is None
        out = _forward(work, tf, work_align, level, _publish if first else None)
        if out is None:
            return None
        trades, segments, p, _ = out
        st = _stats(trades)
        low = training.wilson_low(st["wins"], st["trades"]) if st["trades"] else 0.0
        better = bool(first or ((st["expectancyR"] or 0) > 0 and low > best["low"] + 0.01))
        rounds.append({"round": level + 1, "level": level, "combos": combos, **st, "winRateLow": round(low, 4), "best": better})
        if better:
            best = {"level": level, "low": low, "p": p, "trades": trades, "segments": segments}
            for r in rounds[:-1]:
                r["best"] = False
            _publish(trades, segments, cut)
        reached = bool(st["trades"] >= GOAL_TRADES and (st["winRate"] or 0) >= training.GOAL_WIN_RATE)
        _set(rounds=[dict(r) for r in rounds], reached=reached)
        if reached or (not better and level > 0):
            break  # goal reached, or a wider search no longer helps
    learned_from = _stats(best["trades"])["trades"]
    if learned_from < GOAL_TRADES:
        _set(hint=f"Bagian belajar hanya menghasilkan {learned_from} trade — terlalu sedikit untuk membuktikan setting yang lebih baik. "
                  "Tambah jumlah candle atau pilih timeframe lebih kecil.")
    # final exam: the best round's setting, once, on candles never used while improving
    _set(stage="ujian akhir pada candle yang tidak pernah dipakai belajar")
    tf_sec = training.TF_SECONDS.get(tf, 3600)
    exam = [_trade(b, s, 0, -1, tf_sec, best["p"], exam=True)
            for s in training.signals(b, best["p"], None, align) if s.created >= cut]
    with _lock:
        _s["exam"] = {"from": cut, "fromT": int(b.time[cut]), "candles": len(b) - cut, "stats": _stats(exam),
                      "params": asdict(best["p"]), "level": best["level"]}
    _publish(best["trades"] + exam, best["segments"], len(b))
    return best["level"]


def running() -> bool:
    return _s.get("state") in ("loading", "running")
