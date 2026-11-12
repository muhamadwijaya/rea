"""Classic chart patterns with measured-move targets, detected bar by bar without look-ahead.

Patterns are read from an ATR zigzag: a swing high / low is known as soon as price has turned away from it by
``reversal`` × ATR (so the last point of a pattern is known when price turns, not bars later): double top / bottom,
head & shoulders (and inverse), ascending / descending / symmetrical triangles, rectangles, rising / falling wedges,
bull / bear flags.

Breakout (Bulkowski: price piercing the pattern's line; trading convention: on a close, by a margin, not undone by
the next close): the first close beyond the neckline / boundary by at least BREAK_ATR × ATR, confirmed when the next
close is still beyond the line — a breakout undone by the next close is a false breakout and the pattern is dropped.
A pattern whose line was already crossed before the pattern could be known is dropped too (it would be read late).

Targets follow the measure rule as T. Bulkowski documents it (thepatternsite.com/measure.html): the pattern's height
projected from the breakout. TP2 is the full height (the textbook objective); TP1 is the share of the height that he
measured as actually reached for that pattern and breakout direction, projected from the base he measures from (for
example a double top: 43 % of the height below the valley). The stop is the pattern's own invalidation point (above
the right shoulder, beyond the second top, beyond the last swing inside a triangle or flag); the caller adds an ATR
buffer. Heights and tolerances are in ATR units so the rules work on every market and timeframe.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .smc import Pivot

MIN_HEIGHT_ATR = 2.0  # a pattern smaller than 2 × ATR is noise
POLE_ATR = 3.5  # a flagpole moves at least 3.5 × ATR
BREAK_ATR = 0.25  # a breakout close is beyond the line by at least this much ATR
MIN_BARS = 3  # bars between swing points of a pattern (and the scale of the flag / expiry limits)

# Bulkowski's measure-rule shares (bull-market table): (base of the projection, share of the height) per direction.
# base: "B" = breakout price, "H" / "L" = the pattern's flat top / bottom (or the valley / peak between two tops /
# bottoms), "flag" = the flag's far end (flag low for an upward breakout).
MEASURE = {
    "double_top": {-1: ("L", 0.43)},
    "double_bottom": {1: ("H", 0.65)},
    "head_shoulders": {-1: ("B", 0.51)},
    "inverse_head_shoulders": {1: ("B", 0.71)},
    "ascending_triangle": {1: ("H", 0.70), -1: ("B", 0.44)},
    "descending_triangle": {1: ("B", 0.64), -1: ("L", 0.50)},
    "symmetrical_triangle": {1: ("B", 0.58), -1: ("B", 0.36)},
    "rectangle": {1: ("H", 0.78), -1: ("L", 0.54)},
    "rising_wedge": {1: ("B", 0.63), -1: ("B", 0.32)},
    "falling_wedge": {1: ("B", 0.62), -1: ("B", 0.29)},
    "bull_flag": {1: ("flag", 0.46)},
    "bear_flag": {-1: ("flag", 0.46)},
}
NAMES = {
    "double_top": "DOUBLE TOP", "double_bottom": "DOUBLE BOTTOM", "head_shoulders": "HEAD & SHOULDERS",
    "inverse_head_shoulders": "INVERSE H&S", "ascending_triangle": "ASCENDING TRIANGLE",
    "descending_triangle": "DESCENDING TRIANGLE", "symmetrical_triangle": "SYMMETRICAL TRIANGLE",
    "rectangle": "RECTANGLE", "rising_wedge": "RISING WEDGE", "falling_wedge": "FALLING WEDGE",
    "bull_flag": "BULL FLAG", "bear_flag": "BEAR FLAG",
}
# when two patterns break out on the same bar in the same direction, the one with more touches is kept
PRIORITY = ("head_shoulders", "inverse_head_shoulders", "bull_flag", "bear_flag", "rising_wedge", "falling_wedge",
            "ascending_triangle", "descending_triangle", "symmetrical_triangle", "rectangle", "double_top",
            "double_bottom")


@dataclass
class Pattern:
    kind: str
    direction: int  # +1: breakout up (buy), −1: breakout down (sell)
    breakout: int  # bar of the breakout close (the breakout candle)
    level: float  # the pattern line at that bar (breakout price B)
    height: float  # height used by the measure rule (flags: the pole)
    tp1: float  # Bulkowski's share of the height from his base
    tp2: float  # the full measured move from the breakout
    pct: float
    stop: float  # the pattern's invalidation point (no buffer)
    start: int
    points: list[tuple[int, float]]
    lines: list[tuple[tuple[int, float], tuple[int, float]]] = field(default_factory=list)
    slope: float = 0.0  # of the broken line, per bar: a retest meets it where it is then (sloped triangles, wedges)
    confirmed: int = -1  # bar whose close confirmed the breakout (the next one): the earliest a trade is known

    @property
    def name(self) -> str:
        return NAMES[self.kind]


@dataclass
class _Line:
    i0: int
    p0: float
    i1: int
    p1: float

    def at(self, t: float) -> float:
        if self.i1 == self.i0:
            return self.p0
        return self.p0 + (self.p1 - self.p0) * (t - self.i0) / (self.i1 - self.i0)

    @property
    def slope(self) -> float:
        return 0.0 if self.i1 == self.i0 else (self.p1 - self.p0) / (self.i1 - self.i0)


@dataclass
class _Candidate:
    kind: str
    made: int  # bar on which its last pivot was confirmed
    expire: int
    points: list[Pivot]
    height: float
    up: _Line | None = None  # breakout line upwards
    down: _Line | None = None
    bases: dict = field(default_factory=dict)  # "H" / "L" / "flag" prices
    stops: dict = field(default_factory=dict)  # direction → invalidation price
    invalid_above: float | None = None  # a close above / below drops the pattern before its breakout
    invalid_below: float | None = None
    draw: list[_Line] = field(default_factory=list)


def _line(a: Pivot, b: Pivot) -> _Line:
    return _Line(a.index, a.price, b.index, b.price)


def _flat(x: float, y: float, tol: float) -> int:
    """0 when two levels are equal within `tol`, else the sign of the change."""
    return 0 if abs(y - x) <= tol else (1 if y > x else -1)


def _templates(zz: list[Pivot], atr: np.ndarray, length: int, t: int) -> list[_Candidate]:
    """Patterns whose last pivot is the newest zigzag point (confirmed on bar t)."""
    out: list[_Candidate] = []
    last = zz[-1]
    a = float(atr[last.index]) if np.isfinite(atr[last.index]) and atr[last.index] > 0 else None
    if a is None:
        return out
    min_h = MIN_HEIGHT_ATR * a

    # --- double top / bottom: peak, valley, peak (mirrored)
    if len(zz) >= 3:
        p1, mid, p2 = zz[-3:]
        top = p1.kind == "high"
        height = (max(p1.price, p2.price) - mid.price) if top else (mid.price - min(p1.price, p2.price))
        width = p2.index - p1.index
        if height >= min_h and abs(p1.price - p2.price) <= 0.6 * a + 0.05 * height and width >= 2 * length:
            neck = _Line(p1.index, mid.price, p2.index, mid.price)
            if top:
                out.append(_Candidate("double_top", t, p2.index + width + length, [p1, mid, p2], height, down=neck,
                                      bases={"L": mid.price}, stops={-1: p2.price},
                                      invalid_above=max(p1.price, p2.price), draw=[neck]))
            else:
                out.append(_Candidate("double_bottom", t, p2.index + width + length, [p1, mid, p2], height, up=neck,
                                      bases={"H": mid.price}, stops={1: p2.price},
                                      invalid_below=min(p1.price, p2.price), draw=[neck]))

    # --- head & shoulders (top: high, low, high, low, high) and inverse
    if len(zz) >= 5:
        s1, n1, head, n2, s2 = zz[-5:]
        top = s1.kind == "high"
        neck = _line(n1, n2)
        sign = 1 if top else -1
        height = (head.price - neck.at(head.index)) * sign
        shoulders_level = abs(s1.price - s2.price) <= max(1.5 * a, 0.25 * height)
        head_out = (head.price - max(s1.price, s2.price) if top else min(s1.price, s2.price) - head.price) >= 0.5 * a
        above_neck = (min(s1.price, s2.price) > max(n1.price, n2.price)) if top else (
            max(s1.price, s2.price) < min(n1.price, n2.price))
        if height >= min_h and shoulders_level and head_out and above_neck and abs(n2.price - n1.price) <= 0.5 * height:
            span = s2.index - s1.index
            if top:
                out.append(_Candidate("head_shoulders", t, s2.index + span // 2 + length, [s1, n1, head, n2, s2], height,
                                      down=neck, stops={-1: s2.price}, invalid_above=s2.price, draw=[neck]))
            else:
                out.append(_Candidate("inverse_head_shoulders", t, s2.index + span // 2 + length,
                                      [s1, n1, head, n2, s2], height, up=neck, stops={1: s2.price},
                                      invalid_below=s2.price, draw=[neck]))

    if len(zz) >= 4:
        pts = zz[-4:]
        highs = [p for p in pts if p.kind == "high"]
        lows = [p for p in pts if p.kind == "low"]

        # --- flags: pole, then a short counter-trend channel (bull: low, HIGH, low, lower high)
        p0, p1, p2, p3 = pts
        bull = p0.kind == "low"
        sign = 1 if bull else -1
        pole = (p1.price - p0.price) * sign
        retrace = (p1.price - p2.price) * sign
        pole_bars = p1.index - p0.index
        if (pole >= POLE_ATR * a and pole_bars <= 4 * length and 0.1 * pole <= retrace <= 0.5 * pole
                and (p1.price - p3.price) * sign > 0 and (p3.price - p2.price) * sign > 0
                and p3.index - p1.index <= 2 * pole_bars + 2 * length):
            edge = _line(p1, p3)  # the flag's breakout side
            other = _Line(p2.index, p2.price, p2.index + 1, p2.price + edge.slope)  # parallel through the flag's end
            kind = "bull_flag" if bull else "bear_flag"
            cand = _Candidate(kind, t, p3.index + (p3.index - p1.index) + length, [p0, p1, p2, p3], pole,
                              bases={"flag": p2.price}, stops={sign: p2.price}, draw=[edge, other])
            if bull:
                cand.up, cand.invalid_below = edge, p2.price
            else:
                cand.down, cand.invalid_above = edge, p2.price
            out.append(cand)

        # --- triangles, wedges, rectangles: two highs and two lows
        if len(highs) == 2 and len(lows) == 2:
            upper, lower = _line(*highs), _line(*lows)
            start, end = pts[0].index, pts[-1].index
            height = upper.at(start) - lower.at(start)
            if height >= min_h and upper.at(end) > lower.at(end):
                tol = 0.6 * a + 0.1 * height
                hi_dir, lo_dir = _flat(highs[0].price, highs[1].price, tol), _flat(lows[0].price, lows[1].price, tol)
                converging = upper.slope < lower.slope
                kind = {(0, 0): "rectangle", (0, 1): "ascending_triangle", (-1, 0): "descending_triangle",
                        (-1, 1): "symmetrical_triangle"}.get((hi_dir, lo_dir))
                if kind is None and converging and hi_dir == lo_dir == 1:
                    kind = "rising_wedge"
                elif kind is None and converging and hi_dir == lo_dir == -1:
                    kind = "falling_wedge"
                if kind is not None:
                    expire = end + (end - start)
                    if converging and kind != "rectangle":
                        apex = start + height / (lower.slope - upper.slope)
                        expire = int(min(expire, apex))
                    out.append(_Candidate(kind, t, expire + length, pts, height, up=upper, down=lower,
                                          bases={"H": max(h.price for h in highs), "L": min(x.price for x in lows)},
                                          stops={1: lows[-1].price, -1: highs[-1].price}, draw=[upper, lower]))
    return out


def zigzag(h: np.ndarray, l: np.ndarray, atr: np.ndarray, reversal: float) -> list[Pivot]:
    """Swing points known as soon as price turns away from them by `reversal` × ATR (Pivot.confirmed = that bar)."""
    n = len(h)
    out: list[Pivot] = []
    if n == 0:
        return out
    up = None  # direction of the swing in progress (None until the first reversal)
    hi_i, lo_i = 0, 0
    for t in range(1, n):
        a = atr[t] if np.isfinite(atr[t]) and atr[t] > 0 else 0.0
        if h[t] > h[hi_i]:
            hi_i = t
        if l[t] < l[lo_i]:
            lo_i = t
        if up is not False and hi_i < t and h[hi_i] - l[t] >= reversal * a > 0 and (up or hi_i > lo_i):
            out.append(Pivot(hi_i, float(h[hi_i]), "high", t, ""))  # the high is in: price fell reversal × ATR
            up, lo_i = False, hi_i + 1 + int(np.argmin(l[hi_i + 1:t + 1]))  # the down swing so far
        elif up is not True and lo_i < t and h[t] - l[lo_i] >= reversal * a > 0 and (up is False or lo_i > hi_i):
            out.append(Pivot(lo_i, float(l[lo_i]), "low", t, ""))
            up, hi_i = True, lo_i + 1 + int(np.argmax(h[lo_i + 1:t + 1]))
    return out


def detect(h: np.ndarray, l: np.ndarray, c: np.ndarray, atr: np.ndarray, reversal: float = 2.0) -> list[Pattern]:
    """Every pattern whose breakout was confirmed, in bar order (at most one per breakout bar and direction, the most
    specific). `reversal`: the ATR zigzag's turn (pattern size)."""
    n = len(c)
    by_confirm: dict[int, list[Pivot]] = {}
    for p in zigzag(h, l, atr, reversal):
        by_confirm.setdefault(p.confirmed, []).append(p)
    zz: list[Pivot] = []
    active: list[_Candidate] = []
    waiting: list[tuple[_Candidate, int, int, float, float]] = []  # breakouts waiting for the next close
    found: dict[tuple[int, int], Pattern] = {}

    def beyond(line: _Line, k: int, d: int, margin: float) -> bool:
        return (c[k] - line.at(k)) * d > margin

    for t in range(n):
        # a breakout of the previous bar is confirmed by this close staying beyond the line, else it was false
        for cand, tb, d, level, slope in waiting:
            line = cand.up if d > 0 else cand.down
            if not beyond(line, t, d, 0.0):
                continue
            base_key, pct = MEASURE[cand.kind][d]
            base = level if base_key == "B" else cand.bases[base_key]
            tp1, tp2 = base + d * pct * cand.height, level + d * cand.height
            if (tp1 - level) * d <= 0.05 * cand.height:  # Bulkowski's base lies behind the breakout (flags)
                tp1 = level + d * pct * cand.height
            pat = Pattern(cand.kind, d, tb, float(level), float(cand.height), float(tp1), float(tp2), pct,
                          float(cand.stops[d]), cand.points[0].index, [(q.index, float(q.price)) for q in cand.points],
                          [((ln.i0 if ln.i0 >= cand.points[0].index else cand.points[0].index,
                             ln.at(max(ln.i0, cand.points[0].index))), (tb, ln.at(tb))) for ln in cand.draw],
                          float(slope), t)
            key = (tb, d)
            if key not in found or PRIORITY.index(pat.kind) < PRIORITY.index(found[key].kind):
                found[key] = pat
        waiting = []
        for p in by_confirm.get(t, ()):
            if zz and zz[-1].kind == p.kind:  # the ATR zigzag alternates; kept for safety
                continue
            zz.append(p)
            for cand in _templates(zz, atr, MIN_BARS, t):
                last = cand.points[-1].index
                # the pattern is known only now: if its line was crossed before this bar, it would be read late — drop
                # it (a breakout on this very bar is known on the same close, and is checked below)
                if any(line is not None and any(beyond(line, k, d, 0.0) for k in range(last + 1, t))
                       for line, d in ((cand.up, 1), (cand.down, -1))):
                    continue
                active.append(cand)
        still: list[_Candidate] = []
        for cand in active:
            if t < cand.made:
                still.append(cand)
                continue
            if t > cand.expire or (cand.invalid_above is not None and c[t] > cand.invalid_above) or (
                    cand.invalid_below is not None and c[t] < cand.invalid_below):
                continue
            a = atr[t] if np.isfinite(atr[t]) else 0.0
            hit = None
            for line, d in ((cand.up, 1), (cand.down, -1)):
                if line is not None and beyond(line, t, d, BREAK_ATR * a):  # a poke inside the margin is noise
                    hit = (d, line)
                    break
            if hit is None:
                still.append(cand)
                continue
            d, line = hit
            if d in MEASURE[cand.kind]:  # the breakout close: wait for the next close
                waiting.append((cand, t, d, line.at(t), line.slope))
            # either way the pattern is done: it broke out (a side not traded ends it as well)
        active = still
    return sorted(found.values(), key=lambda x: x.breakout)
