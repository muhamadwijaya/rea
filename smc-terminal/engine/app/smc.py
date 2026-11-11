"""Smart Money Concepts engine.

An original, rule-based implementation of the usual SMC building blocks:

* swing and internal market structure (pivots, HH/HL/LH/LL),
* break of structure (BOS) and change of character (CHoCH),
* order blocks created by each structure break, with tap/invalidation,
* fair value gaps (three-candle imbalances) with fill tracking,
* equal highs / equal lows (resting liquidity) with sweep tracking,
* premium / discount zones of the current dealing range.

Everything is evaluated bar by bar without look-ahead: a pivot only exists
once ``length`` bars to its right have closed, and a break is only recorded on
the close that crosses the level.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from .indicators import atr as atr_series

BULL = 1
BEAR = -1


@dataclass(frozen=True)
class Pivot:
    index: int
    price: float
    kind: str  # "high" | "low"
    confirmed: int  # first bar on which the pivot is known
    label: str  # HH / LH / HL / LL ("" for the first pivot of its kind)


@dataclass(frozen=True)
class Break:
    kind: str  # "BOS" | "CHoCH"
    direction: int
    level: float
    pivot_index: int
    index: int
    scope: str  # "swing" | "internal"


@dataclass
class OrderBlock:
    direction: int
    top: float
    bottom: float
    index: int  # origin candle
    created: int  # bar of the structure break that produced it
    scope: str
    tapped: int | None = None  # first bar trading back into the block
    broken: int | None = None  # close beyond the far edge: invalidated

    @property
    def active(self) -> bool:
        return self.broken is None


@dataclass
class Gap:
    direction: int
    top: float
    bottom: float
    index: int  # middle candle of the three-candle pattern
    created: int
    filled: int | None = None
    fill: float = 0.0  # fraction of the gap already traded through


@dataclass
class EqualLevel:
    kind: str  # "EQH" | "EQL"
    price: float
    first: int
    second: int
    confirmed: int
    swept: int | None = None


@dataclass
class Structure:
    scope: str
    pivots: list[Pivot]
    breaks: list[Break]
    order_blocks: list[OrderBlock]
    trend: int
    trend_history: np.ndarray

    def last_break(self) -> Break | None:
        return self.breaks[-1] if self.breaks else None


@dataclass(frozen=True)
class Zones:
    top: float
    bottom: float
    top_index: int
    bottom_index: int
    equilibrium: float
    position: float  # 0 = range low, 1 = range high (of the last close)
    strong: str  # "low" when the range low is protected (bullish), "high" otherwise

    @property
    def area(self) -> str:
        if self.position >= 0.55:
            return "premium"
        if self.position <= 0.45:
            return "discount"
        return "equilibrium"


@dataclass
class SmcResult:
    swing: Structure
    internal: Structure
    gaps: list[Gap]
    equal_levels: list[EqualLevel]
    zones: Zones | None
    atr: np.ndarray
    liquidity_above: list[tuple[float, int, str]] = field(default_factory=list)
    liquidity_below: list[tuple[float, int, str]] = field(default_factory=list)


def find_pivots(high: np.ndarray, low: np.ndarray, length: int) -> list[Pivot]:
    """Fractal pivots: extreme of the 2*length+1 window centred on the bar.

    Strict on the left and inclusive on the right so a flat top yields one
    pivot (the first bar of the plateau).
    """
    n = len(high)
    if n < 2 * length + 1:
        return []
    window = 2 * length + 1
    hi_max = sliding_window_view(high, window).max(axis=1)
    lo_min = sliding_window_view(low, window).min(axis=1)
    left_hi = sliding_window_view(high[:-1], length).max(axis=1)  # left_hi[j] = max(high[j:j+length])
    left_lo = sliding_window_view(low[:-1], length).min(axis=1)

    raw: list[tuple[int, str, float]] = []
    for j in range(len(hi_max)):
        i = j + length
        if high[i] >= hi_max[j] and high[i] > left_hi[j]:
            raw.append((i, "high", float(high[i])))
        if low[i] <= lo_min[j] and low[i] < left_lo[j]:
            raw.append((i, "low", float(low[i])))

    pivots: list[Pivot] = []
    last: dict[str, float] = {}
    for i, kind, price in raw:
        prev = last.get(kind)
        if prev is None:
            label = ""
        elif kind == "high":
            label = "HH" if price > prev else "LH"
        else:
            label = "HL" if price > prev else "LL"
        last[kind] = price
        pivots.append(Pivot(i, price, kind, i + length, label))
    return pivots


def _order_block(direction: int, start: int, end: int, o, h, l, c, atr, scope: str, created: int) -> OrderBlock | None:
    """Origin candle of the leg that broke structure.

    For a bullish break, the candle with the lowest low between the broken
    swing high and the breaking bar (the last sell-side push before the
    displacement); mirrored for bearish breaks. Very wide candles are trimmed
    to their body so one spike does not create an unrealistically tall block.
    """
    if end <= start:
        return None
    segment = slice(start, end)
    k = start + int(np.argmin(l[segment]) if direction == BULL else np.argmax(h[segment]))
    top, bottom = float(h[k]), float(l[k])
    if top - bottom > 2.0 * atr[k]:
        if direction == BULL:
            top = float(max(o[k], c[k]))
        else:
            bottom = float(min(o[k], c[k]))
    if top <= bottom:
        return None
    return OrderBlock(direction, top, bottom, k, created, scope)


def detect_structure(o, h, l, c, atr, pivots: list[Pivot], scope: str) -> Structure:
    n = len(c)
    by_confirm: dict[int, list[Pivot]] = defaultdict(list)
    for p in pivots:
        if p.confirmed < n:
            by_confirm[p.confirmed].append(p)

    top: Pivot | None = None
    bottom: Pivot | None = None
    top_live = bottom_live = False
    trend = 0
    breaks: list[Break] = []
    blocks: list[OrderBlock] = []
    live_blocks: list[OrderBlock] = []
    history = np.zeros(n, dtype=np.int8)

    for t in range(n):
        for p in by_confirm.get(t, ()):
            if p.kind == "high":
                top, top_live = p, True
            else:
                bottom, bottom_live = p, True

        # Update existing blocks with this bar before adding new ones.
        if live_blocks:
            still: list[OrderBlock] = []
            for ob in live_blocks:
                if ob.direction == BULL:
                    if ob.tapped is None and l[t] <= ob.top:
                        ob.tapped = t
                    if c[t] < ob.bottom:
                        ob.broken = t
                else:
                    if ob.tapped is None and h[t] >= ob.bottom:
                        ob.tapped = t
                    if c[t] > ob.top:
                        ob.broken = t
                if ob.broken is None:
                    still.append(ob)
            live_blocks = still

        if top is not None and top_live and c[t] > top.price:
            breaks.append(Break("CHoCH" if trend == BEAR else "BOS", BULL, top.price, top.index, t, scope))
            top_live = False
            trend = BULL
            ob = _order_block(BULL, top.index, t, o, h, l, c, atr, scope, t)
            if ob:
                blocks.append(ob)
                live_blocks.append(ob)

        if bottom is not None and bottom_live and c[t] < bottom.price:
            breaks.append(Break("CHoCH" if trend == BULL else "BOS", BEAR, bottom.price, bottom.index, t, scope))
            bottom_live = False
            trend = BEAR
            ob = _order_block(BEAR, bottom.index, t, o, h, l, c, atr, scope, t)
            if ob:
                blocks.append(ob)
                live_blocks.append(ob)

        history[t] = trend

    return Structure(scope, pivots, breaks, blocks, trend, history)


def detect_gaps(o, h, l, c, atr, min_atr: float = 0.15) -> list[Gap]:
    n = len(c)
    gaps: list[Gap] = []
    for i in range(2, n):
        threshold = min_atr * atr[i]
        if l[i] > h[i - 2] and c[i - 1] > h[i - 2] and l[i] - h[i - 2] >= threshold:
            gaps.append(Gap(BULL, float(l[i]), float(h[i - 2]), i - 1, i))
        elif h[i] < l[i - 2] and c[i - 1] < l[i - 2] and l[i - 2] - h[i] >= threshold:
            gaps.append(Gap(BEAR, float(l[i - 2]), float(h[i]), i - 1, i))

    for g in gaps:
        start = g.created + 1
        if start >= n:
            continue
        size = g.top - g.bottom
        if g.direction == BULL:
            lows = l[start:]
            g.fill = float(np.clip((g.top - lows.min()) / size, 0.0, 1.0))
            hit = np.nonzero(lows <= g.bottom)[0]
        else:
            highs = h[start:]
            g.fill = float(np.clip((highs.max() - g.bottom) / size, 0.0, 1.0))
            hit = np.nonzero(highs >= g.top)[0]
        if hit.size:
            g.filled = start + int(hit[0])
            g.fill = 1.0
    return gaps


def detect_equal_levels(h, l, atr, length: int = 3, tolerance: float = 0.1) -> list[EqualLevel]:
    n = len(h)
    levels: list[EqualLevel] = []
    previous: dict[str, Pivot] = {}
    for p in find_pivots(h, l, length):
        prev = previous.get(p.kind)
        previous[p.kind] = p
        if prev is None or abs(p.price - prev.price) > tolerance * atr[p.index]:
            continue
        if p.kind == "high":
            level = EqualLevel("EQH", max(p.price, prev.price), prev.index, p.index, p.confirmed)
            after = h[p.confirmed + 1:] if p.confirmed + 1 < n else np.array([])
            hit = np.nonzero(after > level.price)[0]
        else:
            level = EqualLevel("EQL", min(p.price, prev.price), prev.index, p.index, p.confirmed)
            after = l[p.confirmed + 1:] if p.confirmed + 1 < n else np.array([])
            hit = np.nonzero(after < level.price)[0]
        if hit.size:
            level.swept = p.confirmed + 1 + int(hit[0])
        levels.append(level)
    return levels


def dealing_range(h, l, c, swing: Structure) -> Zones | None:
    highs = [p for p in swing.pivots if p.kind == "high" and p.confirmed < len(c)]
    lows = [p for p in swing.pivots if p.kind == "low" and p.confirmed < len(c)]
    if not highs or not lows:
        return None
    hi_pivot, lo_pivot = highs[-1], lows[-1]
    top_index = hi_pivot.index + int(np.argmax(h[hi_pivot.index:]))
    bottom_index = lo_pivot.index + int(np.argmin(l[lo_pivot.index:]))
    top, bottom = float(h[top_index]), float(l[bottom_index])
    if top <= bottom:
        return None
    position = float(np.clip((c[-1] - bottom) / (top - bottom), 0.0, 1.0))
    strong = "low" if swing.trend == BULL else "high"
    return Zones(top, bottom, top_index, bottom_index, (top + bottom) / 2.0, position, strong)


def _resting_liquidity(h, l, c, pivots: list[Pivot], equal: list[EqualLevel]):
    """Untaken pivot highs above / lows below the last close, nearest first."""
    n = len(c)
    price = c[-1]
    above: list[tuple[float, int, str]] = []
    below: list[tuple[float, int, str]] = []
    for p in pivots:
        if p.confirmed >= n:
            continue
        after = slice(p.index + 1, n)
        if p.kind == "high" and p.price > price and (p.index + 1 >= n or h[after].max() <= p.price):
            above.append((p.price, p.index, "swing high"))
        if p.kind == "low" and p.price < price and (p.index + 1 >= n or l[after].min() >= p.price):
            below.append((p.price, p.index, "swing low"))
    for e in equal:
        if e.swept is None:
            if e.kind == "EQH" and e.price > price:
                above.append((e.price, e.second, "equal highs"))
            elif e.kind == "EQL" and e.price < price:
                below.append((e.price, e.second, "equal lows"))
    above.sort(key=lambda x: x[0])
    below.sort(key=lambda x: -x[0])
    return above, below


def analyze(o, h, l, c, swing_length: int = 10, internal_length: int = 3) -> SmcResult:
    o, h, l, c = (np.asarray(a, dtype=float) for a in (o, h, l, c))
    atr = atr_series(h, l, c, 14)
    swing_pivots = find_pivots(h, l, swing_length)
    internal_pivots = find_pivots(h, l, internal_length)
    swing = detect_structure(o, h, l, c, atr, swing_pivots, "swing")
    internal = detect_structure(o, h, l, c, atr, internal_pivots, "internal")
    gaps = detect_gaps(o, h, l, c, atr)
    equal = detect_equal_levels(h, l, atr)
    zones = dealing_range(h, l, c, swing)
    above, below = _resting_liquidity(h, l, c, internal_pivots + swing_pivots, equal)
    return SmcResult(swing, internal, gaps, equal, zones, atr, above, below)
