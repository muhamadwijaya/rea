"""Fundamental analysis of economic releases (CPI, PMI, NFP, GDP, rate decisions, …).

For each calendar event the engine:

1. classifies the indicator and whether a higher reading is good (+1) or bad (-1) for the currency;
2. after the release, looks for headlines reporting the number and extracts the *actual* value
   (and the consensus when the headline states it); the calendar's forecast is the default consensus;
3. turns actual vs forecast into an expected currency direction and expected moves for related
   assets (pairs, gold, US indices, crypto, IHSG);
4. measures the *real* market reaction 60 minutes after the release on a liquid proxy pair and marks
   the call as confirmed / opposite / flat. The share of confirmed calls is reported as the model's
   measured accuracy — no accuracy is assumed or promised.

Everything here is rule based and explainable; the optional Claude analysis (ai.py) builds on it.
"""

from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass
from datetime import datetime

import numpy as np

from . import instruments, market_data
from .market_data import Bars


@dataclass(frozen=True)
class Indicator:
    kind: str  # inflation | growth | labor | rates | sentiment | trade | housing | speech
    sign: int  # +1: higher than expected is good for the currency
    title: re.Pattern
    words: tuple[str, ...]  # words that must appear in a matching headline (any of)


INDICATORS = [
    Indicator("speech", 0, re.compile(r"speaks|testifies|press conference|minutes|statement|hearing", re.I), ()),
    Indicator("labor", -1, re.compile(r"unemployment claims|jobless claims|claimant count", re.I), ("jobless", "claims", "claimant")),
    Indicator("labor", -1, re.compile(r"unemployment rate", re.I), ("unemployment", "jobless rate", "pengangguran")),
    Indicator("labor", 1, re.compile(r"non-farm|nonfarm|employment change|payrolls|adp", re.I), ("payroll", "jobs", "employment", "hiring")),
    Indicator("labor", 1, re.compile(r"average (hourly )?earnings|wage|labor cost", re.I), ("earnings", "wage")),
    Indicator("inflation", 1, re.compile(r"\bcpi\b|consumer price|inflation", re.I), ("cpi", "inflation", "consumer price", "inflasi")),
    Indicator("inflation", 1, re.compile(r"\bppi\b|producer price", re.I), ("ppi", "producer price")),
    Indicator("inflation", 1, re.compile(r"\bpce\b", re.I), ("pce",)),
    Indicator("rates", 1, re.compile(r"rate decision|cash rate|bank rate|refinancing rate|interest rate|policy rate|funds rate|bi rate|rate statement", re.I),
              ("rate", "suku bunga", "hike", "cut", "holds")),
    Indicator("growth", 1, re.compile(r"\bpmi\b|\bism\b", re.I), ("pmi", "ism", "purchasing")),
    Indicator("growth", 1, re.compile(r"\bgdp\b", re.I), ("gdp", "gross domestic", "economy grew", "pdb")),
    Indicator("growth", 1, re.compile(r"retail sales|core retail", re.I), ("retail sales", "penjualan ritel")),
    Indicator("growth", 1, re.compile(r"industrial production|manufacturing production|factory orders|durable goods", re.I),
              ("industrial", "manufacturing", "factory", "durable")),
    Indicator("sentiment", 1, re.compile(r"confidence|sentiment|\bzew\b|\bifo\b|economic optimism", re.I), ("confidence", "sentiment", "zew", "ifo")),
    Indicator("trade", 1, re.compile(r"trade balance|current account", re.I), ("trade", "surplus", "deficit")),
    Indicator("housing", 1, re.compile(r"housing|home sales|building permits|housing starts|building approvals", re.I),
              ("home", "housing", "permits", "starts")),
]

COUNTRY_WORDS = {
    "USD": ("u.s.", "us ", "united states", "american", "fed", "america", "wall street", "treasury", "cpi as", "inflasi as",
            "data as", "amerika", "dolar"),
    "EUR": ("euro zone", "eurozone", "euro area", "german", "germany", "france", "french", "ecb", "italy", "spain"),
    "GBP": ("uk ", "u.k.", "britain", "british", "boe", "bank of england", "england"),
    "JPY": ("japan", "japanese", "boj", "tokyo"),
    "AUD": ("australia", "australian", "rba"),
    "NZD": ("new zealand", "rbnz", "kiwi"),
    "CAD": ("canada", "canadian", "boc"),
    "CHF": ("swiss", "switzerland", "snb"),
    "CNY": ("china", "chinese", "beijing"),
    "IDR": ("indonesia", "bank indonesia", " ri ", "bps"),
}

# Liquid pair whose move stands in for the currency (sign: + when the pair rises with the currency).
PROXY = {"USD": ("EURUSD", -1), "EUR": ("EURUSD", 1), "GBP": ("GBPUSD", 1), "JPY": ("USDJPY", -1), "AUD": ("AUDUSD", 1),
         "NZD": ("NZDUSD", 1), "CAD": ("USDCAD", -1), "CHF": ("USDCHF", -1), "IDR": ("USDIDR", -1)}

IMPACT_WEIGHT = {"High": 1.0, "Medium": 0.5, "Low": 0.2}
REACTION_MINUTES = 60
FLAT_PCT = 0.03

_NUM = r"(-?\d+(?:[.,]\d+)?)\s*(%|k|m|b|bn)?"
_VS = re.compile(_NUM + r"\s*(?:vs\.?|versus|against|v\.?)\s*(?:an?\s+)?(?:the\s+)?(?:expected|expectations?\s+of|exp\.?|forecast|f'cast|est\.?|estimates?|consensus|poll|prior)?\s*(?:of\s+)?" + _NUM, re.I)
_EXPECTED = re.compile(r"(?:expected|forecast|estimates?|consensus|poll|est\.?)\s*(?:of|was|at|for)?\s*" + _NUM, re.I)
_VERBS = (r"(?:rose|rises|rise|fell|falls|fall|climbed|climbs|increased|decreased|declined|dropped|jumped|slowed|accelerated|"
          r"eased|came in at|printed|at|to|was|is|naik|turun|sebesar|tumbuh)")


def _actual_pattern(words: tuple[str, ...]) -> re.Pattern:
    """Number right after the indicator's own name ("CPI rose 0.4%", "claims fall to 205K"), so numbers
    about other assets in the same headline ("gold climbs 0.7% after CPI") are never taken."""
    names = "|".join(re.escape(w) for w in words) or r"(?!x)x"
    return re.compile(rf"(?:{names})[^.,;:!?]{{0,25}}?\b{_VERBS}\s+(?:by\s+)?" + _NUM, re.I)
_BETTER = re.compile(r"\b(beats?|tops|stronger[- ]than[- ]expected|better[- ]than[- ]expected|surprises? to the upside|robust|hawkish hike)\b", re.I)
_WORSE = re.compile(r"\b(miss(?:es|ed)?|weaker[- ]than[- ]expected|worse[- ]than[- ]expected|disappoints?|surprises? to the downside)\b", re.I)
_HIGHER = re.compile(
    r"\b(hotter|higher|above|more than|stronger)\b[^.]{0,25}\b(expected|forecast|estimates?|expectations|consensus)"
    r"|\b(exceeds?|exceeded|tops|topped|beats?)\s+(the\s+)?(estimates?|expectations|forecasts?|consensus)"
    r"|\bdi atas (ekspektasi|perkiraan|konsensus|proyeksi)", re.I)
_LOWER = re.compile(
    r"\b(cooler|softer|lower|below|less than|weaker)\b[^.]{0,25}\b(expected|forecast|estimates?|expectations|consensus)"
    r"|\b(misse?s?|missed|undershoots?)\s+(the\s+)?(estimates?|expectations|forecasts?|consensus)"
    r"|\bdi bawah (ekspektasi|perkiraan|konsensus|proyeksi)", re.I)
# Inflation-specific shorthand used by headline writers ("hot CPI print", "cooler PPI").
_HOT = re.compile(r"\b(hot|hotter|sticky|accelerat\w*|panas)\b", re.I)
_COOL = re.compile(r"\b(cool|cooler|cooling|soft|softer|tame|eases?|easing|melandai)\b", re.I)


def classify(title: str) -> Indicator | None:
    for ind in INDICATORS:
        if ind.title.search(title):
            return ind
    return None


def parse_value(text: str | None) -> float | None:
    """'0.3%' -> 0.3, '195K' -> 195000, '-1.2B' -> -1.2e9, '' -> None."""
    if not text:
        return None
    m = re.search(_NUM, text.replace(",", "."), re.I)
    if not m:
        return None
    value = float(m.group(1).replace(",", "."))
    unit = (m.group(2) or "").lower()
    return value * {"k": 1e3, "m": 1e6, "b": 1e9, "bn": 1e9}.get(unit, 1)


def _num(match_num: str, unit: str | None) -> float:
    value = float(match_num.replace(",", "."))
    return value * {"k": 1e3, "m": 1e6, "b": 1e9, "bn": 1e9}.get((unit or "").lower(), 1)


def _plausible(actual: float, reference: float | None) -> bool:
    if reference is None or reference == 0:
        return True
    return abs(actual - reference) <= max(abs(reference) * 2, 0.5)


def extract(headline: str, forecast: float | None, kind: str = "", words: tuple[str, ...] = ()) -> dict:
    """Actual value / consensus / qualitative surprise found in one headline."""
    out: dict = {}
    m = _VS.search(headline)
    if m:
        actual, consensus = _num(m.group(1), m.group(2)), _num(m.group(3), m.group(4))
        if _plausible(actual, forecast if forecast is not None else consensus):
            out["actual"], out["consensus"] = actual, consensus
    if "actual" not in out:
        exp = _EXPECTED.search(headline)
        act = _actual_pattern(words).search(headline) if words else None
        if act:
            actual = _num(act.group(1), act.group(2))
            reference = forecast if forecast is not None else (_num(exp.group(1), exp.group(2)) if exp else None)
            # Loose verbs ("at", "to") need a reference value to rule out years, prices and other numbers.
            if reference is not None and _plausible(actual, reference):
                out["actual"] = actual
                if exp:
                    out["consensus"] = _num(exp.group(1), exp.group(2))
    if _BETTER.search(headline):
        out["quality"] = 1  # better for the currency
    elif _WORSE.search(headline):
        out["quality"] = -1
    if _HIGHER.search(headline):
        out["numeric"] = 1  # number above expectations
    elif _LOWER.search(headline):
        out["numeric"] = -1
    elif kind == "inflation" and _HOT.search(headline):
        out["numeric"] = 1
    elif kind == "inflation" and _COOL.search(headline):
        out["numeric"] = -1
    return out


def _headlines_for(event: dict, indicator: Indicator, news: list[dict], unique: bool) -> list[dict]:
    """Headlines reporting this release: right indicator words, right country (unless no other country
    released the same indicator around that time), published within 24 hours after the release."""
    t0 = event["ts"]
    country = COUNTRY_WORDS.get(event["currency"], ())
    matched = []
    for item in news:
        ts = item.get("ts")
        if ts is None or not (t0 - 1800 <= ts <= t0 + 24 * 3600):
            continue
        text = f" {item.get('title', '')} ".lower()
        if not any(w in text for w in indicator.words):
            continue
        if country and not unique and not any(w in text for w in country):
            continue
        matched.append(item)
    matched.sort(key=lambda i: i["ts"])
    return matched


def _reaction(currency: str, t0: int) -> dict | None:
    proxy = PROXY.get(currency)
    if not proxy or t0 + REACTION_MINUTES * 60 > time.time() or time.time() - t0 > 25 * 86400:
        return None
    symbol, sign = proxy
    inst = instruments.find(symbol)
    if inst is None:
        return None
    try:
        bars: Bars = market_data.load(inst, "15m")
    except Exception:
        return None
    before = np.nonzero(bars.time < t0)[0]
    after = np.nonzero(bars.time <= t0 + (REACTION_MINUTES - 15) * 60)[0]
    if not before.size or not after.size or after[-1] <= before[-1]:
        return None
    p0, p1 = float(bars.close[before[-1]]), float(bars.close[after[-1]])
    move = (p1 / p0 - 1) * 100
    return {"symbol": symbol, "movePct": round(move, 3), "currencyMovePct": round(move * sign, 3),
            "from": p0, "to": p1, "minutes": REACTION_MINUTES}


def _related(currency: str, direction: int, kind: str) -> list[dict]:
    """Expected direction of related assets when `currency` moves `direction` after a `kind` release."""
    if direction == 0:
        return []
    out: list[dict] = []
    for inst in instruments.watchlist():
        if inst.asset_class != "forex" or len(inst.symbol) != 6:
            continue
        if inst.symbol[:3] == currency:
            out.append({"symbol": inst.symbol, "direction": direction})
        elif inst.symbol[3:] == currency:
            out.append({"symbol": inst.symbol, "direction": -direction})
    if currency == "USD":
        out += [{"symbol": "DXY", "direction": direction}, {"symbol": "XAUUSD", "direction": -direction}]
        if kind in ("inflation", "rates"):
            out += [{"symbol": "SPX", "direction": -direction}, {"symbol": "NDX", "direction": -direction},
                    {"symbol": "US10Y", "direction": direction}, {"symbol": "BTCUSD", "direction": -direction}]
        elif kind in ("growth", "labor"):
            out += [{"symbol": "SPX", "direction": direction}, {"symbol": "US10Y", "direction": direction}]
    if currency == "IDR":
        out.append({"symbol": "IHSG", "direction": direction})
    return out[:10]


def _event_ts(date: str) -> int | None:
    try:
        return int(datetime.fromisoformat(date.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def analyze(events: list[dict], news: list[dict]) -> dict:
    now = time.time()
    for item in news:
        item["ts"] = _event_ts(item.get("published", "")) if "ts" not in item else item["ts"]
    out_events: list[dict] = []
    for e in events:
        ts = _event_ts(e.get("date", ""))
        impact = e.get("impact", "")
        if ts is None or impact not in IMPACT_WEIGHT:
            continue
        ind = classify(e.get("title", ""))
        forecast = parse_value(e.get("forecast"))
        previous = parse_value(e.get("previous"))
        row = {
            "id": e.get("id"), "title": e.get("title"), "currency": e.get("currency"), "date": e.get("date"), "ts": ts,
            "impact": impact, "kind": ind.kind if ind else "other", "forecast": e.get("forecast") or None,
            "previous": e.get("previous") or None, "status": "released" if ts <= now else "upcoming",
        }
        if ind and ind.kind != "speech" and ind.sign:
            if ts <= now:
                unique = not any(
                    o is not e and o.get("currency") != e.get("currency") and ind.title.search(o.get("title", ""))
                    and abs((_event_ts(o.get("date", "")) or 0) - ts) < 12 * 3600 for o in events)
                row.update(_released(row, ind, forecast, previous, news, unique))
            else:
                row["scenario"] = _scenario(row, ind)
        if ts <= now:
            row["reaction"] = _reaction(row["currency"], ts)
            pred = (row.get("prediction") or {}).get("direction", 0)
            reaction = row["reaction"]
            if pred and reaction:
                move = reaction["currencyMovePct"]
                row["verdict"] = "flat" if abs(move) < FLAT_PCT else ("confirmed" if math.copysign(1, move) == pred else "opposite")
        out_events.append(row)
    out_events.sort(key=lambda r: r["ts"])

    # One verdict per release (currency + time): CPI and core CPI at 13:30 share one market reaction.
    judged, seen = [], set()
    for r in out_events:
        if r.get("verdict") and (r["currency"], r["ts"]) not in seen:
            seen.add((r["currency"], r["ts"]))
            judged.append(r)
    hits = sum(1 for r in judged if r["verdict"] == "confirmed")
    misses = sum(1 for r in judged if r["verdict"] == "opposite")
    flats = sum(1 for r in judged if r["verdict"] == "flat")
    decided = hits + misses
    return {
        "generatedAt": int(now),
        "events": out_events,
        "accuracy": {"confirmed": hits, "opposite": misses, "flat": flats, "judged": len(judged),
                     "pct": round(hits / decided * 100, 1) if decided else None},
        "currencies": currency_scores(out_events, now),
        "usEquity": us_equity_score(out_events, now),
        "method": ("Actual diambil dari headline berita setelah rilis; konsensus = forecast kalender. Arah mata uang = "
                   "arah kejutan × sifat indikator (mis. pengangguran lebih tinggi = negatif). Diverifikasi dengan "
                   f"pergerakan pair proksi {REACTION_MINUTES} menit setelah rilis; akurasi = konfirmasi / (konfirmasi + berlawanan)."),
    }


def _released(row: dict, ind: Indicator, forecast: float | None, previous: float | None, news: list[dict],
              unique: bool) -> dict:
    out: dict = {}
    votes: list[int] = []
    for item in _headlines_for(row, ind, news, unique):
        found = extract(item.get("title", ""), forecast, ind.kind, ind.words)
        if not found:
            continue
        out.setdefault("evidence", []).append({"title": item.get("title"), "source": item.get("source"), "link": item.get("link")})
        if "actual" in found and "actual" not in out:
            out["actual"] = found["actual"]
            out["consensusFromNews"] = found.get("consensus")
        if "quality" in found:
            votes.append(found["quality"])
        elif "numeric" in found:
            votes.append(found["numeric"] * ind.sign)
        if len(out.get("evidence", [])) >= 8:
            break
    consensus = forecast if forecast is not None else out.get("consensusFromNews")
    direction, surprise_pct, basis = 0, None, None
    if out.get("actual") is not None and consensus is not None:
        diff = out["actual"] - consensus
        scale = max(abs(consensus - previous) if previous is not None else 0, abs(consensus) * 0.05, 1e-9)
        surprise_pct = round(diff / scale, 2)
        if abs(diff) > 1e-12:
            direction = int(math.copysign(1, diff)) * ind.sign
            basis = "angka actual vs forecast"
    elif votes and abs(sum(votes)) > len(votes) / 3:
        # Majority of the headlines' qualitative wording (above/below expectations, hot/cool …).
        direction = int(math.copysign(1, sum(votes)))
        basis = f"{len(votes)} headline: {sum(1 for v in votes if v == direction)} searah"
    if direction:
        strength = min(1.0, abs(surprise_pct)) if surprise_pct is not None else 0.5
        word = "MENGUAT" if direction > 0 else "MELEMAH"
        out["prediction"] = {
            "currency": row["currency"], "direction": direction, "strength": round(strength, 2), "basis": basis,
            "assets": _related(row["currency"], direction, ind.kind),
            "text": f"{row['currency']} diperkirakan {word} ({basis}).",
        }
    out["surprise"] = surprise_pct
    out["evidence"] = out.get("evidence", [])[:4]
    return out


def _scenario(row: dict, ind: Indicator) -> dict:
    cur = row["currency"]
    up = "MENGUAT" if ind.sign > 0 else "MELEMAH"
    down = "MELEMAH" if ind.sign > 0 else "MENGUAT"
    return {
        "above": f"Actual > forecast {row.get('forecast') or ''}: {cur} cenderung {up}.",
        "below": f"Actual < forecast {row.get('forecast') or ''}: {cur} cenderung {down}.",
        "assetsIfAbove": _related(cur, ind.sign, ind.kind),
    }


def _decay(ts: int, now: float) -> float:
    return math.exp(-max(0.0, now - ts) / (48 * 3600))


def currency_scores(events: list[dict], now: float) -> dict:
    scores: dict[str, dict] = {}
    for e in events:
        pred = e.get("prediction")
        if not pred or now - e["ts"] > 7 * 86400:
            continue
        cur = e["currency"]
        bucket = scores.setdefault(cur, {"score": 0.0, "events": 0})
        bucket["score"] += pred["direction"] * IMPACT_WEIGHT[e["impact"]] * (0.5 + pred["strength"]) * _decay(e["ts"], now) * 40
        bucket["events"] += 1
    for v in scores.values():
        v["score"] = round(max(-100.0, min(100.0, v["score"])), 1)
    return scores


def us_equity_score(events: list[dict], now: float) -> float:
    total = 0.0
    for e in events:
        pred = e.get("prediction")
        if not pred or e["currency"] != "USD":
            continue
        sign = -1 if e["kind"] in ("inflation", "rates") else 1 if e["kind"] in ("growth", "labor") else 0
        total += sign * pred["direction"] * IMPACT_WEIGHT[e["impact"]] * _decay(e["ts"], now) * 30
    return round(max(-100.0, min(100.0, total)), 1)


def _asset_sign(inst: instruments.Instrument, currency: str, kind: str) -> int:
    """How the asset moves when `currency` strengthens on a `kind` release (+1 with it, −1 against it, 0 not at all)."""
    if inst.asset_class == "forex" and len(inst.symbol) == 6:
        return 1 if inst.symbol[:3] == currency else -1 if inst.symbol[3:] == currency else 0
    if currency != "USD":
        return 1 if inst.market == "saham-idx" and currency == "IDR" else 0
    if inst.symbol == "DXY":
        return 1
    if inst.symbol in ("XAUUSD", "XAGUSD", "XPTUSD") or inst.market == "kripto":
        return -1
    if inst.market in ("saham-as", "saham-idx"):  # a stronger dollar on hot inflation / rates weighs on stocks
        return -1 if kind in ("inflation", "rates") else 1 if kind in ("growth", "labor") and inst.market == "saham-as" else 0
    return 0


def upcoming_for(inst: instruments.Instrument, fundamentals: dict | None, days: int = 7) -> list[dict]:
    """Upcoming calendar releases that move this asset, with the asset's expected direction if the actual comes in
    above / below the forecast. The forecast is the market consensus — the closest real estimate of the number before
    it is released; which side it lands on is not predicted here (the AI weighs it with the news)."""
    now = time.time()
    out = []
    for e in (fundamentals or {}).get("events", []):
        if e.get("status") == "released" or not e.get("ts") or e["ts"] > now + days * 86400:
            continue
        ind = classify(e.get("title", ""))
        sign = _asset_sign(inst, e.get("currency", ""), e.get("kind", "")) if ind and ind.sign else 0
        if not sign:
            continue
        forecast, previous = parse_value(e.get("forecast")), parse_value(e.get("previous"))
        expected = None if forecast is None or previous is None else "naik" if forecast > previous else "turun" if forecast < previous else "tetap"
        out.append({"title": e.get("title"), "currency": e.get("currency"), "ts": e["ts"], "impact": e.get("impact"),
                    "kind": e.get("kind"), "forecast": e.get("forecast"), "previous": e.get("previous"),
                    "consensusChange": expected, "ifAbove": ind.sign * sign, "ifBelow": -ind.sign * sign})
    return sorted(out, key=lambda r: r["ts"])[:6]


def exposure(inst: instruments.Instrument, fundamentals: dict | None) -> tuple[float | None, list[str]]:
    """Fundamental score of one instrument from the latest currency scores, with explanations."""
    if not fundamentals:
        return None, []
    cur = fundamentals.get("currencies", {})
    eq = fundamentals.get("usEquity", 0.0)
    s = lambda c: cur.get(c, {}).get("score", 0.0)  # noqa: E731
    notes: list[str] = []
    if inst.asset_class == "forex" and len(inst.symbol) == 6:
        base, quote = inst.symbol[:3], inst.symbol[3:]
        value = s(base) - s(quote)
        notes.append(f"Fundamental {base} {s(base):+.0f} vs {quote} {s(quote):+.0f}")
    elif inst.symbol in ("XAUUSD", "XAGUSD", "XPTUSD"):
        value = -s("USD")
        notes.append(f"Logam mulia berlawanan dengan USD ({s('USD'):+.0f})")
    elif inst.symbol == "DXY":
        value = s("USD")
        notes.append(f"Skor fundamental USD {s('USD'):+.0f}")
    elif inst.market == "saham-as":
        value = eq
        notes.append(f"Rilis data AS untuk saham: {eq:+.0f} (inflasi panas = negatif, pertumbuhan kuat = positif)")
    elif inst.market == "kripto":
        value = 0.5 * eq - 0.5 * s("USD")
        notes.append(f"Kripto: sentimen risiko AS {eq:+.0f}, USD {s('USD'):+.0f}")
    elif inst.market == "saham-idx":
        value = s("IDR") + 0.3 * eq
        notes.append(f"IDR {s('IDR'):+.0f}, sentimen risiko AS {eq:+.0f}")
    else:
        return None, []
    return round(max(-100.0, min(100.0, value)), 1), notes
