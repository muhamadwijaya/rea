"""Real reports and performance of one asset, as facts for the AI analysis.

- Performance (every asset): returns over 1W/1M/3M/6M/YTD/1Y from daily bars, distance from the 52-week
  high/low, 20-day volatility and the 3-month return relative to the market's benchmark.
- Reports (US & IDX stocks): valuation, profitability and growth, analyst ratings and price targets,
  the last reported quarters (revenue, net income), earnings surprises and the next earnings date with
  its consensus; for crypto, market cap / supply / volume. Source: Yahoo Finance via yfinance.

Results are cached per ticker; a failing source is reported in `notes` instead of failing the analysis.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from datetime import date, datetime, timezone

import numpy as np

from . import instruments, market_data
from .instruments import Instrument

log = logging.getLogger("smc.profile")

REPORT_TTL = 6 * 3600
BENCHMARK = {"us": "SPX", "idx": "IHSG", "crypto": "BTCUSD"}
INFO_FIELDS = {
    "valuation": ("marketCap", "trailingPE", "forwardPE", "priceToBook", "dividendYield"),
    "profitabilityGrowth": ("profitMargins", "revenueGrowth", "earningsGrowth", "earningsQuarterlyGrowth", "returnOnEquity",
                            "debtToEquity", "totalRevenue", "freeCashflow"),
    "analysts": ("recommendationKey", "recommendationMean", "numberOfAnalystOpinions", "targetMeanPrice", "targetHighPrice",
                 "targetLowPrice", "trailingEps", "forwardEps"),
    "crypto": ("marketCap", "circulatingSupply", "volume24Hr"),
}

_cache: dict[str, tuple[float, dict]] = {}
_locks: dict[str, threading.Lock] = {}
_guard = threading.Lock()


def _clean(v):
    if v is None:
        return None
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (float, np.floating)):
        f = float(v)
        return None if math.isnan(f) or math.isinf(f) else round(f, 4)
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return v


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


def _reports(inst: Instrument) -> dict:
    import yfinance as yf  # lazy: tests do not need network libraries

    ticker = yf.Ticker(inst.yahoo)
    out: dict = {}
    notes: list[str] = []
    try:
        info = ticker.info or {}
    except Exception as exc:  # yfinance raises many unrelated types
        info = {}
        notes.append(f"profil/valuasi tidak tersedia ({type(exc).__name__})")
    groups = ("crypto",) if inst.asset_class == "crypto" else ("valuation", "profitabilityGrowth", "analysts")
    for group in groups:
        values = {k: _clean(info.get(k)) for k in INFO_FIELDS[group] if info.get(k) is not None}
        if values:
            out[group] = values
    if info.get("sector"):
        out["sector"], out["industry"] = info.get("sector"), info.get("industry")
    price = info.get("currentPrice") or info.get("regularMarketPrice")
    target = info.get("targetMeanPrice")
    if price and target:
        out.setdefault("analysts", {})["upsideToMeanTargetPct"] = round((target / price - 1) * 100, 2)
    if inst.asset_class == "crypto":
        return {**out, "notes": notes}
    try:
        ed = ticker.get_earnings_dates(limit=8)
        if ed is not None and not ed.empty:
            rows = [{"date": idx.date().isoformat(), "epsEstimate": _clean(r.get("EPS Estimate")),
                     "reportedEps": _clean(r.get("Reported EPS")), "surprisePct": _clean(r.get("Surprise(%)"))}
                    for idx, r in ed.iterrows()]
            out["earningsReported"] = [r for r in rows if r["reportedEps"] is not None][:4]
            upcoming = [r for r in rows if r["reportedEps"] is None and r["date"] >= date.today().isoformat()]
            if upcoming:
                out["nextEarnings"] = upcoming[-1]
    except Exception as exc:
        notes.append(f"riwayat laba tidak tersedia ({type(exc).__name__})")
    try:
        cal = ticker.calendar or {}
        if cal.get("Earnings Average") is not None or cal.get("Revenue Average") is not None:
            out.setdefault("nextEarnings", {}).update({
                "date": _clean((cal.get("Earnings Date") or [None])[0]) or out.get("nextEarnings", {}).get("date"),
                "epsConsensus": _clean(cal.get("Earnings Average")), "revenueConsensus": _clean(cal.get("Revenue Average"))})
        if cal.get("Ex-Dividend Date"):
            out["exDividendDate"] = _clean(cal.get("Ex-Dividend Date"))
    except Exception as exc:
        notes.append(f"kalender emiten tidak tersedia ({type(exc).__name__})")
    try:
        q = ticker.quarterly_income_stmt
        if q is not None and not q.empty:
            quarters = []
            cols = list(q.columns)[:5]
            for i, col in enumerate(cols[:4]):
                rev = _clean(q.at["Total Revenue", col]) if "Total Revenue" in q.index else None
                net = _clean(q.at["Net Income", col]) if "Net Income" in q.index else None
                prev = cols[i + 1] if i + 1 < len(cols) else None
                prev_rev = _clean(q.at["Total Revenue", prev]) if prev is not None and "Total Revenue" in q.index else None
                quarters.append({"quarter": col.date().isoformat(), "revenue": rev, "netIncome": net,
                                 "revenueQoQPct": round((rev / prev_rev - 1) * 100, 2) if rev and prev_rev else None})
            out["quarterlyResults"] = quarters
    except Exception as exc:
        notes.append(f"laporan kuartalan tidak tersedia ({type(exc).__name__})")
    return {**out, "notes": notes}


def reports(inst: Instrument) -> dict | None:
    if inst.asset_class not in ("us", "idx", "crypto"):
        return None
    key = inst.yahoo
    hit = _cache.get(key)
    if hit and hit[0] > time.time():
        return hit[1]
    with _guard:
        lock = _locks.setdefault(key, threading.Lock())
    with lock:
        hit = _cache.get(key)
        if hit and hit[0] > time.time():
            return hit[1]
        data = _reports(inst)
        data["fetchedAt"] = int(time.time())
        # a fully failed fetch is retried sooner than a good one
        _cache[key] = (time.time() + (REPORT_TTL if len(data) > 2 else 600), data)
        return data


def profile(inst: Instrument) -> dict:
    out: dict = {"notes": []}
    try:
        out["performance"] = performance(inst)
    except Exception as exc:
        out["notes"].append(f"kinerja harga tidak tersedia: {getattr(exc, 'message', exc)}")
    try:
        rep = reports(inst)
        if rep is not None:
            out["notes"] += rep.get("notes", [])
            out["reports"] = {k: v for k, v in rep.items() if k != "notes"}
    except Exception as exc:
        out["notes"].append(f"laporan tidak tersedia: {type(exc).__name__}")
    return out
