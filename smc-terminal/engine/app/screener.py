"""Watchlist screener: one SMC summary row per instrument."""

from __future__ import annotations

import time

from . import analysis, instruments, market_data
from .market_data import Bars


def _change(bars: Bars | Exception | None, lookback: int = 1) -> float | None:
    if not isinstance(bars, Bars) or len(bars) <= lookback:
        return None
    prev = bars.close[-1 - lookback]
    return round((bars.close[-1] / prev - 1.0) * 100.0, 3) if prev else None


def screen(group: str = "all", tf_key: str = "4h") -> dict:
    started = time.perf_counter()
    tf = market_data.timeframe(tf_key)
    insts = instruments.by_group(group)
    bars_map = market_data.load_many(insts, tf.key)
    daily_map = bars_map if tf.key == "1d" else market_data.load_many(insts, "1d")

    rows: list[dict] = []
    failures = 0
    for inst in insts:
        bars = bars_map.get(inst.symbol)
        base = {"instrument": inst.to_json()}
        if not isinstance(bars, Bars):
            failures += 1
            rows.append({**base, "error": getattr(bars, "message", str(bars))})
            continue
        a = analysis.run_on_bars(inst, tf, bars)
        daily = daily_map.get(inst.symbol)
        closes = bars.close[-40:]
        rows.append({
            **base,
            **analysis.summary(a),
            "price": float(bars.close[-1]),
            "changePct": _change(daily),
            "change5dPct": _change(daily, 5),
            "spark": [round(float(x), 6) for x in closes],
        })
    return {
        "group": group,
        "tf": tf.key,
        "generatedAt": int(time.time()),
        "elapsedMs": int((time.perf_counter() - started) * 1000),
        "count": len(rows),
        "failures": failures,
        "rows": rows,
    }
