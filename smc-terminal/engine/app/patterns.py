"""Classic chart patterns with measured-move targets, detected bar by bar without look-ahead.

Patterns are read from a zigzag of confirmed pivots (smc.find_pivots: a pivot exists once ``length`` bars to its right
have closed): double top / bottom, head & shoulders (and inverse), ascending / descending / symmetrical triangles,
rectangles, rising / falling wedges, bull / bear flags. A pattern is traded on the first close beyond its line
(neckline or boundary) after its last pivot is confirmed; it is dropped when price invalidates it first or when it
grows stale.

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

from .smc import Pivot, find_pivots

MIN_HEIGHT_ATR = 2.0  # a pattern smaller than 2 × ATR is noise
POLE_ATR = 3.5  # a flagpole moves at least 3.5 × ATR

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
    breakout: int  # bar of the breakout close
    level: float  # the pattern line at that bar (breakout price B)
    height: float  # height used by the measure rule (flags: the pole)
    tp1: float  # Bulkowski's share of the height from his base
    tp2: float  # the full measured move from the breakout
    pct: float
    stop: float  # the pattern's invalidation point (no buffer)
    start: int
    points: list[tuple[int, float]]
    lines: list[tuple[tuple[int, float], tuple[int, float]]] = field(default_factory=list)

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


def detect(h: np.ndarray, l: np.ndarray, c: np.ndarray, atr: np.ndarray, length: int) -> list[Pattern]:
    """Every pattern that broke out, in bar order (at most one per bar and direction, the most specific)."""
    n = len(c)
    by_confirm: dict[int, list[Pivot]] = {}
    for p in find_pivots(h, l, length):
        if p.confirmed < n:
            by_confirm.setdefault(p.confirmed, []).append(p)
    zz: list[Pivot] = []
    active: list[_Candidate] = []
    found: dict[tuple[int, int], Pattern] = {}
    for t in range(n):
        for p in sorted(by_confirm.get(t, ()), key=lambda q: q.index):
            if zz and zz[-1].kind == p.kind:
                better = p.price > zz[-1].price if p.kind == "high" else p.price < zz[-1].price
                if not better:
                    continue
                gone = zz.pop().index  # the zigzag point moved: patterns built on it are void
                active = [x for x in active if all(q.index != gone for q in x.points)]
            elif zz and p.index <= zz[-1].index:
                continue
            zz.append(p)
            active += _templates(zz, atr, length, t)
        still: list[_Candidate] = []
        for cand in active:
            if t <= cand.made:
                still.append(cand)
                continue
            if t > cand.expire or (cand.invalid_above is not None and c[t] > cand.invalid_above) or (
                    cand.invalid_below is not None and c[t] < cand.invalid_below):
                continue
            hit = None
            if cand.up is not None and 1 in MEASURE[cand.kind] and c[t] > cand.up.at(t):
                hit = (1, cand.up.at(t))
            elif cand.down is not None and -1 in MEASURE[cand.kind] and c[t] < cand.down.at(t):
                hit = (-1, cand.down.at(t))
            elif (cand.up is not None and c[t] > cand.up.at(t)) or (cand.down is not None and c[t] < cand.down.at(t)):
                continue  # broke out the side the pattern is not traded on: done
            if hit is None:
                still.append(cand)
                continue
            d, level = hit
            base_key, pct = MEASURE[cand.kind][d]
            base = level if base_key == "B" else cand.bases[base_key]
            tp1, tp2 = base + d * pct * cand.height, level + d * cand.height
            if (tp1 - level) * d <= 0.05 * cand.height:  # Bulkowski's base lies behind the breakout (flags)
                tp1 = level + d * pct * cand.height
            pat = Pattern(cand.kind, d, t, float(level), float(cand.height), float(tp1), float(tp2), pct,
                          float(cand.stops[d]), cand.points[0].index, [(q.index, float(q.price)) for q in cand.points],
                          [((ln.i0 if ln.i0 >= cand.points[0].index else cand.points[0].index,
                             ln.at(max(ln.i0, cand.points[0].index))), (t, ln.at(t))) for ln in cand.draw])
            key = (t, d)
            if key not in found or PRIORITY.index(pat.kind) < PRIORITY.index(found[key].kind):
                found[key] = pat
        active = still
    return sorted(found.values(), key=lambda x: x.breakout)
