"""Lightweight headline sentiment and asset tagging (English + Indonesian).

A transparent lexicon model: each matched term carries a weight, a negation
word within the previous three tokens flips it, and the sum is squashed into
[-1, 1]. It is deliberately simple and explainable; the matched terms are
returned so the UI can show *why* a headline was scored.
"""

from __future__ import annotations

import math
import re

POSITIVE = {
    # English
    "surge": 2, "surges": 2, "soar": 2, "soars": 2, "rally": 2, "rallies": 2, "jump": 1.5, "jumps": 1.5,
    "gain": 1, "gains": 1, "rise": 1, "rises": 1, "rising": 1, "climb": 1, "climbs": 1, "higher": 1,
    "record high": 2, "all-time high": 2, "beat": 1.5, "beats": 1.5, "upgrade": 1.5, "upgraded": 1.5,
    "bullish": 2, "rebound": 1.5, "rebounds": 1.5, "recover": 1, "recovers": 1, "strong": 1, "stronger": 1,
    "growth": 1, "expands": 1, "optimism": 1.5, "boost": 1.5, "boosts": 1.5, "outperform": 1.5,
    "upbeat": 1.5, "eases": 0.5, "cools": 0.5, "rate cut": 1, "stimulus": 1, "deal": 0.5, "profit": 1,
    "buyback": 1, "dividend": 0.5, "breakout": 1.5, "accelerates": 1, "tops": 1, "edges higher": 1,
    "winning streak": 1.5, "tailwind": 1, "tailwinds": 1,
    # Indonesian
    "naik": 1, "menguat": 1.5, "melonjak": 2, "melesat": 2, "meroket": 2, "terbang": 1.5, "rekor": 1.5,
    "positif": 1, "laba": 1, "untung": 1, "tumbuh": 1, "pertumbuhan": 1, "optimis": 1.5, "rebound": 1.5,
    "pulih": 1, "akumulasi": 1, "borong": 1.5, "net buy": 1.5, "hijau": 1, "cuan": 1.5, "bullish kuat": 2,
    "dividen": 0.5, "ekspansi": 1, "surplus": 1, "kenaikan": 1,
}

NEGATIVE = {
    # English
    "plunge": 2, "plunges": 2, "plummet": 2, "plummets": 2, "crash": 2.5, "crashes": 2.5, "tumble": 2,
    "tumbles": 2, "slump": 2, "slumps": 2, "drop": 1.5, "drops": 1.5, "fall": 1, "falls": 1, "falling": 1,
    "decline": 1, "declines": 1, "lower": 1, "slide": 1.5, "slides": 1.5, "sink": 1.5, "sinks": 1.5,
    "miss": 1.5, "misses": 1.5, "downgrade": 1.5, "downgraded": 1.5, "bearish": 2, "weak": 1, "weaker": 1,
    "recession": 2, "slowdown": 1.5, "contraction": 1.5, "fear": 1.5, "fears": 1.5, "worries": 1.5,
    "concern": 1, "concerns": 1, "risk-off": 1.5, "selloff": 2, "sell-off": 2, "loss": 1.5, "losses": 1.5,
    "warns": 1.5, "warning": 1.5, "default": 2, "bankruptcy": 2.5, "layoffs": 1.5, "tariff": 1,
    "tariffs": 1, "inflation": 0.5, "hawkish": 1, "rate hike": 1, "war": 1.5, "sanctions": 1, "probe": 1,
    "lawsuit": 1, "cut guidance": 2, "volatility": 0.5, "turmoil": 2, "crisis": 2,
    "at risk": 2, "pressure": 1, "pressured": 1, "weighs": 1, "weigh on": 1.5, "headwinds": 1.5, "slips": 1,
    "dips": 1, "retreat": 1, "retreats": 1, "pares": 0.5, "edges lower": 1, "losing streak": 1.5, "shutdown": 1,
    # Indonesian
    "turun": 1, "melemah": 1.5, "anjlok": 2, "ambruk": 2.5, "longsor": 2, "merosot": 2, "terjun": 2,
    "negatif": 1, "rugi": 1.5, "kerugian": 1.5, "resesi": 2, "krisis": 2, "khawatir": 1.5, "kekhawatiran": 1.5,
    "tekanan": 1, "tertekan": 1.5, "net sell": 1.5, "jual": 0.5, "merah": 1, "gagal bayar": 2.5,
    "pailit": 2.5, "phk": 1.5, "defisit": 1, "inflasi": 0.5, "penurunan": 1, "koreksi": 1, "boncos": 1.5,
}

NEGATIONS = {"not", "no", "never", "without", "fails", "failed", "tidak", "bukan", "belum", "tanpa", "gagal"}

ASSETS: dict[str, tuple[str, ...]] = {
    "USD": ("dollar", "usd", "greenback", "fed", "fomc", "powell", "treasury", "dolar", "the fed", "nonfarm", "payrolls", "us cpi"),
    "EUR": ("euro", "eur", "ecb", "lagarde", "eurozone", "zona euro"),
    "GBP": ("pound", "sterling", "gbp", "boe", "bank of england", "cable"),
    "JPY": ("yen", "jpy", "boj", "bank of japan", "ueda"),
    "AUD": ("aussie", "aud", "rba", "australia"),
    "CAD": ("loonie", "cad", "bank of canada", "boc"),
    "CHF": ("franc", "chf", "snb"),
    "NZD": ("kiwi", "nzd", "rbnz"),
    "IDR": ("rupiah", "idr", "bank indonesia", "bi rate", "bi-rate"),
    "GOLD": ("gold", "emas", "xau", "bullion"),
    "OIL": ("oil", "crude", "opec", "minyak", "brent", "wti"),
    "US_EQ": ("s&p", "nasdaq", "dow", "wall street", "stocks", "equities", "nvidia", "apple", "tesla", "microsoft", "earnings"),
    "IDX": ("ihsg", "bei", "bursa efek", "idx", "saham", "emiten", "bbca", "bbri", "bmri", "tlkm", "goto"),
    "CRYPTO": ("bitcoin", "btc", "crypto", "kripto", "ethereum", "ether"),
}

_TOKEN = re.compile(r"[a-z0-9&'\-]+")


def _phrases(tokens: list[str], max_len: int = 3):
    for i in range(len(tokens)):
        for size in range(max_len, 0, -1):
            if i + size <= len(tokens):
                yield i, size, " ".join(tokens[i:i + size])


def score_text(text: str) -> dict:
    tokens = _TOKEN.findall((text or "").lower())
    total = 0.0
    hits: list[str] = []
    consumed: set[int] = set()
    for i, size, phrase in _phrases(tokens):
        if any(j in consumed for j in range(i, i + size)):
            continue
        weight = POSITIVE.get(phrase, 0.0) - NEGATIVE.get(phrase, 0.0)
        if not weight:
            continue
        window = tokens[max(0, i - 3):i]
        negated = any(w in NEGATIONS for w in window)
        if negated:
            weight = -weight
        total += weight
        hits.append(("+" if weight > 0 else "-") + ("tidak " if negated else "") + phrase)
        consumed.update(range(i, i + size))
    score = math.tanh(total / 3.0)
    label = "bullish" if score > 0.15 else "bearish" if score < -0.15 else "neutral"
    joined = " " + " ".join(tokens) + " "
    assets = [asset for asset, words in ASSETS.items() if any(f" {w} " in joined for w in words)]
    return {"score": round(score, 3), "label": label, "terms": hits[:8], "assets": assets}


def score_items(items: list[dict]) -> list[dict]:
    from .tagger import tag

    out = []
    for item in items:
        title = item.get("title", "")
        text = f"{title}. {item.get('summary', '')}"
        scored = score_text(text)
        # Symbols come from the headline only: summaries mention many side assets.
        tags = tag(title, item.get("lang", "en"), item.get("category", ""))
        out.append({"id": item.get("id"), **scored, "symbols": [t.to_json() for t in tags]})
    return out
