"""Candlestick patterns on 1-minute bars — the first trigger of a move — and how they have worked on each asset.

Patterns (direction +1 bullish / −1 bearish): engulfing, pin bar (hammer / shooting star, after a move against
it), morning / evening star, three soldiers / three crows. For every asset the last days of 1-minute bars (Yahoo
keeps a few days) are scanned and each pattern's hit rate is measured: did price move the pattern's way within
HORIZON minutes? So "bullish engulfing" carries this asset's own track record, not a textbook claim. Where the free
1-minute feed has no real candles (Yahoo forex: one price per minute), that is reported instead.
"""

from __future__ import annotations

import numpy as np

from . import market_data
from .instruments import Instrument

HORIZON = 15  # minutes after the pattern bar
RECENT = 5  # a pattern within the last 5 bars counts as a current trigger
NAMES = {
    "engulfing": ("bullish engulfing", "bearish engulfing"),
    "pin": ("hammer (pin bar bullish)", "shooting star (pin bar bearish)"),
    "star": ("morning star", "evening star"),
    "three": ("three white soldiers", "three black crows"),
}


def detect(o: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray) -> list[tuple[int, str, int]]:
    """(bar index, pattern key, direction) for every pattern completed on a bar."""
    o, h, l, c = (np.asarray(a, dtype=float) for a in (o, h, l, c))
    n = len(c)
    body = np.abs(c - o)
    rng = np.maximum(h - l, 1e-12)
    upper = h - np.maximum(o, c)
    lower = np.minimum(o, c) - l
    avg = np.convolve(body, np.ones(20) / 20, mode="full")[:n]  # mean body of the 20 bars up to i (causal)
    out: list[tuple[int, str, int]] = []
    for i in range(3, n):
        a = max(avg[i - 1], 1e-12)
        up, down = c[i] > o[i], c[i] < o[i]
        if up and c[i - 1] < o[i - 1] and o[i] <= c[i - 1] and c[i] >= o[i - 1] and body[i] > body[i - 1] and body[i] > 0.5 * a:
            out.append((i, "engulfing", 1))
        elif down and c[i - 1] > o[i - 1] and o[i] >= c[i - 1] and c[i] <= o[i - 1] and body[i] > body[i - 1] and body[i] > 0.5 * a:
            out.append((i, "engulfing", -1))
        if lower[i] >= 2 * body[i] and upper[i] <= 0.25 * rng[i] and rng[i] > a and c[i - 3] > c[i - 1]:
            out.append((i, "pin", 1))
        elif upper[i] >= 2 * body[i] and lower[i] <= 0.25 * rng[i] and rng[i] > a and c[i - 3] < c[i - 1]:
            out.append((i, "pin", -1))
        if body[i - 2] > a and body[i - 1] < 0.4 * a and body[i] > 0.6 * a:
            mid2 = (o[i - 2] + c[i - 2]) / 2
            if c[i - 2] < o[i - 2] and up and c[i] > mid2:
                out.append((i, "star", 1))
            elif c[i - 2] > o[i - 2] and down and c[i] < mid2:
                out.append((i, "star", -1))
        three = range(i - 2, i + 1)
        if all(c[k] > o[k] and body[k] > 0.5 * a for k in three) and c[i] > c[i - 1] > c[i - 2]:
            out.append((i, "three", 1))
        elif all(c[k] < o[k] and body[k] > 0.5 * a for k in three) and c[i] < c[i - 1] < c[i - 2]:
            out.append((i, "three", -1))
    return out


def stats(c: np.ndarray, found: list[tuple[int, str, int]], horizon: int = HORIZON) -> dict[tuple[str, int], dict]:
    """Hit rate per (pattern, direction): price moved the pattern's way `horizon` bars later."""
    c = np.asarray(c, dtype=float)
    agg: dict[tuple[str, int], list[int]] = {}
    for i, key, d in found:
        if i + horizon < len(c):
            agg.setdefault((key, d), []).append(1 if (c[i + horizon] - c[i]) * d > 0 else 0)
    return {k: {"n": len(v), "hits": sum(v), "hitRate": round(sum(v) / len(v), 3)} for k, v in agg.items()}


def trigger(inst: Instrument) -> dict:
    """The latest 1-minute pattern (within RECENT bars) with this asset's own track record for it."""
    got = market_data.history_many([inst], "1m").get(inst.symbol)  # the ~7 days Yahoo keeps, for a larger record
    bars = got if isinstance(got, market_data.Bars) else market_data.load(inst, "1m")
    flat = float(np.mean(bars.high - bars.low <= 0)) if len(bars) else 1.0
    if flat >= 0.8:  # the free 1-minute feed has a single price per minute for this asset (forex): no candles to read
        return {"pattern": None, "scanned": len(bars),
                "unavailable": "data 1 menit dari sumber gratis untuk aset ini hanya satu harga per menit (tanpa OHLC), "
                               "jadi pola candlestick tidak bisa dibaca"}
    found = detect(bars.open, bars.high, bars.low, bars.close)
    if not found:
        return {"pattern": None, "scanned": len(bars)}
    record = stats(bars.close, found)
    i, key, d = found[-1]
    if len(bars) - 1 - i > RECENT:
        return {"pattern": None, "scanned": len(bars)}
    rec = record.get((key, d), {"n": 0, "hits": 0, "hitRate": None})
    return {"pattern": NAMES[key][0 if d > 0 else 1], "key": key, "direction": "bullish" if d > 0 else "bearish",
            "barsAgo": len(bars) - 1 - i, "t": int(bars.time[i]), "horizonMin": HORIZON, **rec, "scanned": len(bars)}
