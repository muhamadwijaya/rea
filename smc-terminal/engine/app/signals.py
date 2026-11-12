"""Trading signals per timeframe for a fundamental direction (e.g. the AI's reading of one news article).

A timeframe gets a signal only when it has momentum the same way: its SMC bias points in the direction
(|score| ≥ MOMENTUM_SCORE) and its latest structure break (internal or swing) is in that direction and recent
(≤ MOMENTUM_BARS bars ago). The setup is the pair's pending/open virtual-trading setup (tuned strategy, with the
evidence and precision level behind it) or, without one, the SMC trade plan of that timeframe. Timeframes without
momentum, or with momentum the other way, are reported as such — never forced into a signal.
"""

from __future__ import annotations

from . import analysis, training
from .instruments import Instrument

SIGNAL_TFS = ("5m", "15m", "1h", "4h", "1d")
MOMENTUM_SCORE = 15
MOMENTUM_BARS = 20
WORD = {1: "bullish", -1: "bearish"}


def _plan_setup(a: analysis.Analysis, want: int) -> dict | None:
    plan = analysis.trade_plan(a)
    if not plan or plan.get("direction") != WORD[want]:
        return None
    if plan.get("entry") is None:
        return {"status": "wait", "source": "plan SMC", "note": plan.get("note")}
    target = (plan.get("targets") or [None])[0]
    return {"status": plan["status"], "entry": plan["entry"], "stop": plan["stop"], "target": target,
            "rr": (plan.get("rr") or [None])[0], "source": f"plan SMC ({plan.get('source', 'zona')})", "level": None}


def timeframe_signal(a: analysis.Analysis, want: int, setup=None) -> dict:
    """Momentum and signal of one analysed timeframe for direction `want` (1 / -1 / 0)."""
    n = len(a.bars)
    score = float(a.score)
    smc_dir = 0 if abs(score) < MOMENTUM_SCORE else (1 if score > 0 else -1)
    breaks = [b for st in (a.result.internal, a.result.swing) for b in st.breaks]
    last = max(breaks, key=lambda b: (b.index, b.scope == "swing"), default=None)
    ago = n - 1 - last.index if last else None
    fresh = last is not None and ago <= MOMENTUM_BARS
    row = {"tf": a.tf.key, "smcScore": round(score, 1), "smcLabel": analysis.direction_of(score)[1],
           "lastBreak": {"kind": last.kind, "scope": last.scope, "direction": WORD[last.direction], "barsAgo": ago} if last else None}
    if not want:
        return {**row, "status": "neutral", "note": "arah berita netral: tidak ada sinyal"}
    if smc_dir == -want or (fresh and last.direction == -want):
        return {**row, "status": "against", "note": "SMC timeframe ini bergerak berlawanan"}
    if smc_dir != want or not fresh or last.direction != want:
        return {**row, "status": "no_momentum", "note": "belum ada momentum searah"
                + ("" if fresh else f" (break terakhir {ago} bar lalu)" if ago is not None else "")}
    found = setup() if setup else None
    return {**row, "status": "signal", "direction": WORD[want], "setup": found or _plan_setup(a, want)}


def for_direction(inst: Instrument, direction: str) -> list[dict]:
    """Signals of every SIGNAL_TFS timeframe for a direction ("naik" / "turun" / "netral")."""
    want = {"naik": 1, "turun": -1}.get(direction, 0)
    out = []
    for tf in SIGNAL_TFS:
        try:
            a = analysis.run(inst, tf)
        except Exception as exc:  # one timeframe without data must not hide the others
            out.append({"tf": tf, "status": "error", "note": str(getattr(exc, "message", exc))[:160]})
            continue
        out.append(timeframe_signal(a, want, lambda tf=tf: training.current_setup(inst, tf, want)))
    return out
