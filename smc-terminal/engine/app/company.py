"""Company data of one asset from Yahoo Finance (yfinance): profile, valuation, growth, analysts, financial
statements (annual + quarterly income, balance sheet, cash flow), earnings history and next report, and
shareholders (ownership breakdown, top institutions and funds with shares, insiders).

This module owns the parsing of Yahoo company data; `profile` derives the compact AI facts from it and
the gateway shows it in the LAPORAN tab. Results are cached per ticker; any part that fails is listed in
`notes` instead of failing the whole report.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from datetime import date, datetime

import numpy as np

from .instruments import Instrument

log = logging.getLogger("smc.company")

TTL = 6 * 3600
RETRY_TTL = 10 * 60

INFO_FIELDS = {
    "valuation": ("marketCap", "enterpriseValue", "trailingPE", "forwardPE", "priceToBook", "priceToSalesTrailing12Months",
                  "enterpriseToEbitda", "dividendYield", "payoutRatio", "beta"),
    "profitabilityGrowth": ("profitMargins", "grossMargins", "operatingMargins", "returnOnEquity", "returnOnAssets",
                            "revenueGrowth", "earningsGrowth", "earningsQuarterlyGrowth", "debtToEquity", "currentRatio",
                            "totalRevenue", "ebitda", "freeCashflow", "totalCash", "totalDebt"),
    "analysts": ("recommendationKey", "recommendationMean", "numberOfAnalystOpinions", "targetMeanPrice", "targetHighPrice",
                 "targetLowPrice", "trailingEps", "forwardEps"),
    "shares": ("sharesOutstanding", "floatShares", "heldPercentInsiders", "heldPercentInstitutions"),
    "crypto": ("marketCap", "circulatingSupply", "volume24Hr"),
}

STATEMENT_ROWS = {
    "income": ("Total Revenue", "Gross Profit", "Operating Income", "EBITDA", "Net Income", "Diluted EPS"),
    "balance": ("Total Assets", "Total Liabilities Net Minority Interest", "Stockholders Equity", "Total Debt",
                "Cash And Cash Equivalents", "Ordinary Shares Number"),
    "cashflow": ("Operating Cash Flow", "Capital Expenditure", "Free Cash Flow", "Cash Dividends Paid",
                 "Repurchase Of Capital Stock"),
}

_cache: dict[str, tuple[float, dict]] = {}
_locks: dict[str, threading.Lock] = {}
_guard = threading.Lock()


def clean(v):
    if v is None:
        return None
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, (float, np.floating)):
        f = float(v)
        return None if math.isnan(f) or math.isinf(f) else round(f, 4)
    if isinstance(v, (datetime, date)):
        return v.date().isoformat() if isinstance(v, datetime) else v.isoformat()
    if hasattr(v, "isoformat"):  # pandas Timestamp
        return v.isoformat()[:10]
    return v


def _statement(df, rows: tuple[str, ...], periods: int) -> dict | None:
    if df is None or df.empty:
        return None
    cols = list(df.columns)[:periods]
    out = {"periods": [clean(c) for c in cols], "rows": {}}
    for row in rows:
        if row in df.index:
            values = [clean(df.at[row, c]) for c in cols]
            if any(v is not None for v in values):
                out["rows"][row] = values
    return out if out["rows"] else None


def _table(df, columns: dict[str, str], limit: int = 10) -> list[dict]:
    if df is None or df.empty:
        return []
    rows = []
    for _, r in df.head(limit).iterrows():
        rows.append({out: clean(r.get(src)) for out, src in columns.items() if src in r.index})
    return rows


def _fetch(inst: Instrument) -> dict:
    import yfinance as yf  # lazy: tests do not need network libraries

    t = yf.Ticker(inst.yahoo)
    out: dict = {"symbol": inst.symbol, "name": inst.name, "assetClass": inst.asset_class}
    notes: list[str] = []

    def part(label: str, fn):
        try:
            return fn()
        except Exception as exc:  # yfinance raises many unrelated types
            notes.append(f"{label} tidak tersedia ({type(exc).__name__})")
            return None

    info = part("profil", lambda: t.info) or {}
    groups = ("crypto",) if inst.asset_class == "crypto" else ("valuation", "profitabilityGrowth", "analysts", "shares")
    for group in groups:
        values = {k: clean(info.get(k)) for k in INFO_FIELDS[group] if info.get(k) is not None}
        if values:
            out[group] = values
    for k, dest in (("sector", "sector"), ("industry", "industry"), ("website", "website"),
                    ("fullTimeEmployees", "employees"), ("financialCurrency", "currency"), ("currency", "quoteCurrency")):
        if info.get(k) is not None:
            out[dest] = clean(info.get(k))
    if info.get("longBusinessSummary"):
        out["summary"] = str(info["longBusinessSummary"])[:700]
    price = info.get("currentPrice") or info.get("regularMarketPrice")
    if price and info.get("targetMeanPrice"):
        out.setdefault("analysts", {})["upsideToMeanTargetPct"] = round((info["targetMeanPrice"] / price - 1) * 100, 2)
    if inst.asset_class == "crypto":
        out["notes"] = notes
        return out

    ed = part("riwayat laba", lambda: t.get_earnings_dates(limit=8))
    if ed is not None and not ed.empty:
        rows = [{"date": clean(idx), "epsEstimate": clean(r.get("EPS Estimate")), "reportedEps": clean(r.get("Reported EPS")),
                 "surprisePct": clean(r.get("Surprise(%)"))} for idx, r in ed.iterrows()]
        out["earningsReported"] = [r for r in rows if r["reportedEps"] is not None][:4]
        upcoming = [r for r in rows if r["reportedEps"] is None and (r["date"] or "") >= date.today().isoformat()]
        if upcoming:
            out["nextEarnings"] = upcoming[-1]
    cal = part("kalender emiten", lambda: t.calendar) or {}
    if cal.get("Earnings Average") is not None or cal.get("Revenue Average") is not None:
        nxt = out.setdefault("nextEarnings", {})
        nxt.update({"date": clean((cal.get("Earnings Date") or [None])[0]) or nxt.get("date"),
                    "epsConsensus": clean(cal.get("Earnings Average")), "revenueConsensus": clean(cal.get("Revenue Average"))})
    for k in ("Ex-Dividend Date", "Dividend Date"):
        if cal.get(k):
            out.setdefault("dividendCalendar", {})["exDate" if k.startswith("Ex") else "payDate"] = clean(cal[k])

    statements = {}
    for name, attr_annual, attr_quarter in (("income", "income_stmt", "quarterly_income_stmt"),
                                            ("balance", "balance_sheet", "quarterly_balance_sheet"),
                                            ("cashflow", "cashflow", "quarterly_cashflow")):
        annual = part(f"laporan {name} tahunan", lambda a=attr_annual: _statement(getattr(t, a), STATEMENT_ROWS[name], 4))
        quarterly = part(f"laporan {name} kuartalan", lambda a=attr_quarter: _statement(getattr(t, a), STATEMENT_ROWS[name], 5))
        if annual or quarterly:
            statements[name] = {"annual": annual, "quarterly": quarterly}
    if statements:
        out["statements"] = statements

    holders: dict = {}
    major = part("komposisi pemegang saham", lambda: t.major_holders)
    if major is not None and not major.empty:
        col = major.columns[0]
        holders["breakdown"] = {str(k): clean(major.at[k, col]) for k in major.index}
    cols = {"holder": "Holder", "shares": "Shares", "pct": "pctHeld", "value": "Value", "date": "Date Reported", "pctChange": "pctChange"}
    holders["institutions"] = _table(part("pemegang institusi", lambda: t.institutional_holders), cols)
    holders["funds"] = _table(part("pemegang reksa dana", lambda: t.mutualfund_holders), cols)
    holders["insiders"] = _table(part("orang dalam", lambda: t.insider_roster_holders),
                                 {"name": "Name", "position": "Position", "shares": "Shares Owned Directly",
                                  "transaction": "Most Recent Transaction", "date": "Latest Transaction Date"})
    holders = {k: v for k, v in holders.items() if v}
    if holders:
        out["holders"] = holders
    if inst.asset_class == "idx":
        notes.append("pemegang saham pengendali ≥5% (KSEI/IDX) tidak tersedia dari Yahoo; lihat porsi orang dalam")
    out["notes"] = notes
    return out


def data(inst: Instrument) -> dict | None:
    """Cached company data (stocks, ETFs and crypto); None for forex, commodities and indices."""
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
        started = time.perf_counter()
        result = _fetch(inst)
        result["fetchedAt"] = int(time.time())
        complete = len(result) > 6
        _cache[key] = (time.time() + (TTL if complete else RETRY_TTL), result)
        log.info("company %s: %d sections in %.1fs", inst.yahoo, len(result), time.perf_counter() - started)
        return result


def ai_summary(d: dict) -> dict:
    """The compact part of company data given to the AI (no long tables)."""
    keep = {k: d[k] for k in ("sector", "industry", "currency", "valuation", "profitabilityGrowth", "analysts", "shares",
                              "crypto", "earningsReported", "nextEarnings", "dividendCalendar") if k in d}
    inc = (d.get("statements", {}).get("income") or {}).get("quarterly")
    if inc:
        keep["quarterlyIncome"] = inc
    h = d.get("holders") or {}
    if h:
        keep["shareholders"] = {
            "breakdown": h.get("breakdown"),
            "topInstitutions": [{k: r.get(k) for k in ("holder", "shares", "pct", "pctChange")} for r in h.get("institutions", [])[:5]],
            "topFunds": [{k: r.get(k) for k in ("holder", "shares", "pct", "pctChange")} for r in h.get("funds", [])[:5]],
            "insiders": [{k: r.get(k) for k in ("name", "position", "shares", "transaction", "date")} for r in h.get("insiders", [])[:5]],
        }
    return keep
