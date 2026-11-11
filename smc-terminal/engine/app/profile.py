"""Real reports and performance of one asset, as facts for the AI analysis.

- Performance (every asset): returns over 1W/1M/3M/6M/YTD/1Y from daily bars, distance from the 52-week
  high/low, 20-day volatility and the 3-month return relative to the market's benchmark.
- Reports (stocks and crypto): the compact AI view of `company.data` — valuation, growth, analysts,
  quarterly income, earnings surprises, next report and consensus, shareholders.

A failing source is reported in `notes` instead of failing the analysis.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone

import numpy as np

from . import company, instruments, market_data
from .instruments import Instrument

log = logging.getLogger("smc.profile")

BENCHMARK = {"us": "SPX", "idx": "IHSG", "crypto": "BTCUSD"}


def _ret(close: np.ndarray, bars: int) -> float | None:
    if len(close) <= bars or close[-1 - bars] <= 0:
        return None
    return round((close[-1] / close[-1 - bars] - 1) * 100, 2)


def performance(inst: Instrument) -> dict:
    bars = market_data.load(inst, "1d")
    close, high, low = bars.close, bars.high, bars.low
    year = datetime.fromtimestamp(int(bars.time[-1]), timezone.utc).year
    start_year = next((i for i, t in enumerate(bars.time) if datetime.fromtimestamp(int(t), timezone.utc).year == year), None)
    window = min(len(close), 252)
    hi52, lo52 = float(np.max(high[-window:])), float(np.min(low[-window:]))
    daily = np.diff(np.log(close[-21:])) if len(close) > 21 else np.array([])
    out = {
        "last": round(float(close[-1]), 6),
        "asOf": datetime.fromtimestamp(int(bars.time[-1]), timezone.utc).date().isoformat(),
        "returnsPct": {"1W": _ret(close, 5), "1M": _ret(close, 21), "3M": _ret(close, 63), "6M": _ret(close, 126),
                       "YTD": round((close[-1] / close[start_year] - 1) * 100, 2) if start_year is not None and start_year < len(close) - 1 else None,
                       "1Y": _ret(close, 252)},
        "high52w": round(hi52, 6), "low52w": round(lo52, 6),
        "fromHigh52wPct": round((close[-1] / hi52 - 1) * 100, 2), "fromLow52wPct": round((close[-1] / lo52 - 1) * 100, 2),
        "volatility20dAnnualPct": round(float(np.std(daily) * math.sqrt(252) * 100), 1) if len(daily) > 5 else None,
    }
    bench_symbol = BENCHMARK.get(inst.asset_class)
    if bench_symbol and bench_symbol != inst.symbol:
        bench = instruments.find(bench_symbol)
        try:
            b3 = _ret(market_data.load(bench, "1d").close, 63)
            if b3 is not None and out["returnsPct"]["3M"] is not None:
                out["vsBenchmark3M"] = {"benchmark": bench_symbol, "benchmarkPct": b3,
                                        "relativePct": round(out["returnsPct"]["3M"] - b3, 2)}
        except Exception as exc:  # benchmark is context only
            log.info("benchmark %s unavailable: %s", bench_symbol, exc)
    return out


def profile(inst: Instrument) -> dict:
    out: dict = {"notes": []}
    try:
        out["performance"] = performance(inst)
    except Exception as exc:
        out["notes"].append(f"kinerja harga tidak tersedia: {getattr(exc, 'message', exc)}")
    try:
        rep = company.data(inst)
        if rep is not None:
            out["notes"] += rep.get("notes", [])
            out["reports"] = company.ai_summary(rep)
    except Exception as exc:
        out["notes"].append(f"laporan tidak tersedia: {type(exc).__name__}")
    return out
