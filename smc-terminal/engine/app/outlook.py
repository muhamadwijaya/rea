"""Combined outlook per asset: SMC multi-timeframe bias + fundamental releases + tagged news sentiment.

The fundamentals snapshot is pushed by the gateway (which owns the calendar and the news store) and kept
here as the latest value; tagged news for each symbol arrives with the request.
"""

from __future__ import annotations

import threading

import numpy as np

from . import analysis, fundamental, instruments, market_data

WEIGHTS = {"smc": 0.4, "fundamental": 0.35, "news": 0.25}

_lock = threading.Lock()
_fundamentals: dict | None = None


def set_fundamentals(value: dict) -> None:
    global _fundamentals
    with _lock:
        _fundamentals = value


def fundamentals() -> dict | None:
    with _lock:
        return _fundamentals


def news_score(items: list[dict], symbol: str) -> tuple[float | None, int]:
    """Average sentiment towards `symbol` (headline sentiment × tag sign), scaled to −100…100."""
    values = []
    for item in items:
        sign = next((t.get("sign", 1) for t in item.get("symbols", []) if t.get("symbol") == symbol), None)
        if sign is None or item.get("sentiment") is None:
            continue
        values.append(item["sentiment"] * sign)
    if not values:
        return None, 0
    # Few headlines move the score less: confidence grows with the number of articles.
    weight = min(1.0, len(values) / 5)
    return round(float(np.mean(values)) * 100 * weight, 1), len(values)


def _confidence(parts: dict[str, float], combined: float) -> str:
    strong = [v for v in parts.values() if abs(v) >= 15]
    agree = len(strong) >= 2 and len({np.sign(v) for v in strong}) == 1
    if agree and len(strong) == len(parts) and abs(combined) >= 35:
        return "tinggi"
    if agree or abs(combined) >= 30:
        return "sedang"
    return "rendah"


def outlook(inst: instruments.Instrument, news: list[dict]) -> dict:
    mtf = analysis.mtf(inst)
    parts: dict[str, float] = {"smc": mtf["score"]}
    notes = [f"SMC multi-timeframe {mtf['label']} ({mtf['score']:+.0f})"]
    fscore, fnotes = fundamental.exposure(inst, fundamentals())
    if fscore is not None:
        parts["fundamental"] = fscore
        notes += fnotes
    nscore, ncount = news_score(news, inst.symbol)
    if nscore is not None:
        parts["news"] = nscore
        notes.append(f"Sentimen {ncount} berita terkait {nscore:+.0f}")
    total_w = sum(WEIGHTS[k] for k in parts)
    combined = sum(WEIGHTS[k] * v for k, v in parts.items()) / total_w
    key, label = analysis.direction_of(combined)
    return {
        "instrument": inst.to_json(),
        "score": round(combined, 1),
        "direction": key,
        "label": label,
        "confidence": _confidence(parts, combined),
        "components": {k: round(v, 1) for k, v in parts.items()},
        "newsCount": ncount,
        "mtf": {"score": mtf["score"], "label": mtf["label"], "aligned": mtf["aligned"],
                "timeframes": [{k: t.get(k) for k in ("tf", "score", "label", "swingTrend", "internalTrend", "lastEvent", "zone")}
                               for t in mtf["timeframes"]]},
        "notes": notes,
        "weights": WEIGHTS,
    }


def batch(symbols: list[str], news_by_symbol: dict[str, list[dict]]) -> list[dict]:
    insts = [i for i in (instruments.find(s) for s in symbols) if i is not None]
    for tf in ("1d", "4h", "1h"):
        market_data.load_many(insts, tf)  # warm the caches with three batched downloads
    out = []
    for inst in insts:
        try:
            out.append(outlook(inst, news_by_symbol.get(inst.symbol, [])))
        except Exception as exc:  # one failing symbol must not hide the others
            out.append({"instrument": inst.to_json(), "error": str(getattr(exc, "message", exc))})
    return out
