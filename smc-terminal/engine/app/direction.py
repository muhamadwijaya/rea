"""Market direction: where is the market heading?

Combines daily + 4H SMC bias of key macro instruments into
* an outlook per asset class (USD, US equities, IHSG, gold, oil, crypto, yields),
* a currency strength ranking built from every G8 pair in the watchlist,
* a risk-on / risk-off regime.
"""

from __future__ import annotations

import time

import numpy as np

from . import analysis, instruments, market_data
from .market_data import Bars

G8 = ("USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF")
CORE = ("DXY", "EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "USOIL", "SPX", "NDX", "IHSG", "VIX", "US10Y",
        "BTCUSD", "USDIDR")
WEIGHT_D1, WEIGHT_H4 = 0.55, 0.45


def _pct(bars: Bars, lookback: int) -> float | None:
    if len(bars) <= lookback or not bars.close[-1 - lookback]:
        return None
    return round((bars.close[-1] / bars.close[-1 - lookback] - 1) * 100, 3)


def _confidence(score: float, aligned: bool) -> str:
    if aligned and abs(score) >= 40:
        return "tinggi"
    if aligned or abs(score) >= 30:
        return "sedang"
    return "rendah"


def _instrument_view(inst: instruments.Instrument, d1: Bars, h4: Bars | None) -> dict:
    a1 = analysis.run_on_bars(inst, market_data.timeframe("1d"), d1)
    s1 = analysis.summary(a1)
    s4 = analysis.summary(analysis.run_on_bars(inst, market_data.timeframe("4h"), h4)) if h4 is not None else None
    score = s1["score"] if s4 is None else WEIGHT_D1 * s1["score"] + WEIGHT_H4 * s4["score"]
    aligned = s4 is not None and np.sign(s1["score"]) == np.sign(s4["score"]) and min(abs(s1["score"]), abs(s4["score"])) >= 20
    key, label = analysis.direction_of(score)
    return {
        "symbol": inst.symbol,
        "name": inst.name,
        "tv": inst.tv,
        "assetClass": inst.asset_class,
        "decimals": inst.price_decimals(float(d1.close[-1])),
        "price": float(d1.close[-1]),
        "changePct": _pct(d1, 1),
        "change5dPct": _pct(d1, 5),
        "score": round(float(score), 1),
        "direction": key,
        "label": label,
        "aligned": bool(aligned),
        "confidence": _confidence(score, bool(aligned)),
        "d1": {k: s1[k] for k in ("score", "label", "swingTrend", "internalTrend", "lastEvent", "zone")},
        "h4": {k: s4[k] for k in ("score", "label", "swingTrend", "internalTrend", "lastEvent", "zone")} if s4 else None,
    }


def currency_strength(views: dict[str, dict]) -> list[dict]:
    totals = {c: [] for c in G8}
    changes = {c: [] for c in G8}
    pairs_used: dict[str, list[str]] = {c: [] for c in G8}
    for sym, v in views.items():
        if v["assetClass"] != "forex" or len(sym) != 6:
            continue
        base, quote = sym[:3], sym[3:]
        if base not in totals or quote not in totals:
            continue
        totals[base].append(v["score"])
        totals[quote].append(-v["score"])
        if v["changePct"] is not None:
            changes[base].append(v["changePct"])
            changes[quote].append(-v["changePct"])
        pairs_used[base].append(sym)
        pairs_used[quote].append(sym)
    out = []
    for c in G8:
        if not totals[c]:
            continue
        out.append({
            "currency": c,
            "score": round(float(np.mean(totals[c])), 1),
            "changePct": round(float(np.mean(changes[c])), 3) if changes[c] else None,
            "pairs": pairs_used[c],
        })
    out.sort(key=lambda x: -x["score"])
    return out


def _strength_of(strength: list[dict], currency: str) -> float:
    for row in strength:
        if row["currency"] == currency:
            return row["score"]
    return 0.0


def _outlook(key: str, title: str, parts: list[tuple[str, float, float]], views: dict[str, dict],
             extra: list[str] | None = None) -> dict | None:
    """parts: (symbol, weight, sign). A negative sign means the instrument moves against the asset."""
    used = [(views[s], w, sign) for s, w, sign in parts if s in views]
    if not used:
        return None
    total_w = sum(w for _, w, _ in used)
    score = sum(v["score"] * w * sign for v, w, sign in used) / total_w
    aligned = all(v["aligned"] for v, _, _ in used)
    direction, label = analysis.direction_of(score)
    drivers = []
    for v, _, sign in used:
        word = v["label"] if sign > 0 else f"{v['label']} (berkebalikan)"
        chg = f", {v['changePct']:+.2f}% hari ini" if v["changePct"] is not None else ""
        drivers.append(f"{v['symbol']}: {word}{chg}")
    return {
        "key": key,
        "title": title,
        "score": round(float(score), 1),
        "direction": direction,
        "label": label,
        "confidence": _confidence(score, aligned),
        "drivers": drivers + (extra or []),
        "symbols": [v["symbol"] for v, _, _ in used],
        "tv": used[0][0]["tv"],
    }


def _risk_regime(views: dict[str, dict], strength: list[dict]) -> dict:
    parts: list[tuple[str, float, float]] = []
    for sym, weight in (("SPX", 1.0), ("NDX", 1.0), ("BTCUSD", 0.5), ("IHSG", 0.5), ("VIX", -1.0), ("XAUUSD", -0.25)):
        if sym in views:
            parts.append((sym, weight, views[sym]["score"]))
    jpy, chf = _strength_of(strength, "JPY"), _strength_of(strength, "CHF")
    parts += [("JPY", -0.75, jpy), ("CHF", -0.5, chf)]
    total_w = sum(abs(w) for _, w, _ in parts)
    score = sum(w * s for _, w, s in parts) / total_w if total_w else 0.0
    if score > 15:
        regime, text = "RISK-ON", "Investor berani mengambil risiko: aset berisiko (saham, kripto, AUD/NZD) cenderung diuntungkan."
    elif score < -15:
        regime, text = "RISK-OFF", "Investor mencari aman: safe haven (USD, JPY, CHF, emas) cenderung diuntungkan."
    else:
        regime, text = "MIXED", "Sentimen campuran: tidak ada dominasi risk-on maupun risk-off yang jelas."
    contributions = sorted(
        ({"name": name, "weight": w, "score": round(s, 1), "contribution": round(w * s / total_w, 1)} for name, w, s in parts),
        key=lambda x: -abs(x["contribution"]),
    )
    return {"regime": regime, "score": round(float(score), 1), "text": text, "contributions": contributions}


def market_direction() -> dict:
    started = time.perf_counter()
    universe = [i for i in instruments.watchlist() if i.symbol in CORE or i.asset_class == "forex"]
    d1_map = market_data.load_many(universe, "1d")
    h4_map = market_data.load_many(universe, "4h")
    views: dict[str, dict] = {}
    errors: dict[str, str] = {}
    for inst in universe:
        d1 = d1_map.get(inst.symbol)
        if not isinstance(d1, Bars):
            errors[inst.symbol] = getattr(d1, "message", str(d1))
            continue
        h4 = h4_map.get(inst.symbol)
        views[inst.symbol] = _instrument_view(inst, d1, h4 if isinstance(h4, Bars) else None)

    strength = currency_strength(views)
    usd_strength = _strength_of(strength, "USD")
    outlooks = [
        _outlook("usd", "Dolar AS", [("DXY", 1.0, 1)], views,
                 [f"Kekuatan USD vs G8: {usd_strength:+.0f}"] if strength else None),
        _outlook("us_equities", "Saham AS", [("SPX", 1.0, 1), ("NDX", 1.0, 1)], views),
        _outlook("idx", "IHSG", [("IHSG", 0.7, 1), ("USDIDR", 0.3, -1)], views),
        _outlook("gold", "Emas", [("XAUUSD", 1.0, 1)], views),
        _outlook("oil", "Minyak WTI", [("USOIL", 1.0, 1)], views),
        _outlook("crypto", "Kripto", [("BTCUSD", 1.0, 1)], views),
        _outlook("yields", "Yield US10Y", [("US10Y", 1.0, 1)], views),
    ]
    outlooks = [o for o in outlooks if o]

    best = None
    if len(strength) >= 2:
        strong, weak = strength[0], strength[-1]
        pair = f"{strong['currency']}{weak['currency']}"
        inverse = f"{weak['currency']}{strong['currency']}"
        if instruments.find(inverse) and not instruments.find(pair):
            best = {"pair": inverse, "direction": "bearish", "spread": round(strong["score"] - weak["score"], 1)}
        else:
            best = {"pair": pair, "direction": "bullish", "spread": round(strong["score"] - weak["score"], 1)}

    regime = _risk_regime(views, strength)
    headline = _headline(regime, outlooks, strength)
    return {
        "generatedAt": int(time.time()),
        "elapsedMs": int((time.perf_counter() - started) * 1000),
        "headline": headline,
        "regime": regime,
        "outlooks": outlooks,
        "currencyStrength": strength,
        "bestPair": best,
        "instruments": [views[s] for s in CORE if s in views],
        "errors": errors,
        "method": "Skor = 55% bias SMC harian + 45% bias SMC 4H. Kekuatan mata uang = rata-rata skor semua pair G8 di watchlist.",
    }


def _headline(regime: dict, outlooks: list[dict], strength: list[dict]) -> str:
    parts = [f"{regime['regime']}"]
    ranked = sorted(outlooks, key=lambda o: -abs(o["score"]))[:3]
    parts += [f"{o['title']} {o['label'].lower()}" for o in ranked if o["direction"] != "neutral"]
    if len(strength) >= 2:
        parts.append(f"terkuat {strength[0]['currency']}, terlemah {strength[-1]['currency']}")
    return " · ".join(parts)
