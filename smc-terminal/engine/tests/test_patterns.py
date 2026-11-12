"""Chart patterns: detection on clean synthetic shapes, measure-rule targets, no look-ahead, pattern-mode trades."""

import numpy as np
import pytest

from app import indicators, patterns, training
from app.market_data import Bars
from test_engine import zigzag

SHAPES = [  # kind, breakout direction, zigzag points, bars per leg
    ("double_top", -1, [100, 110, 104, 110.2, 103, 96], 6),
    ("double_bottom", 1, [110, 100, 106, 99.8, 107, 114], 6),
    ("head_shoulders", -1, [96, 106, 100, 112, 100.4, 106.2, 99, 92], 6),
    ("inverse_head_shoulders", 1, [114, 104, 110, 98, 109.6, 103.8, 111, 118], 6),
    ("ascending_triangle", 1, [100, 110, 102, 110.1, 105, 113], 6),
    ("descending_triangle", -1, [110, 100, 108, 99.9, 105, 96], 6),
    ("symmetrical_triangle", 1, [96, 114, 100, 111, 103, 120], 10),
    ("symmetrical_triangle", -1, [114, 96, 110, 99, 107, 90], 10),
    ("rectangle", 1, [104, 110, 104.2, 110.1, 104.3, 113], 6),
    ("falling_wedge", 1, [112, 100, 107, 97, 102, 95.5, 104], 6),
    ("rising_wedge", -1, [100, 104, 101, 109, 104, 111, 100], 6),
    ("bull_flag", 1, [106, 100, 116, 111, 114, 111.5, 120], 6),
    ("bear_flag", -1, [94, 100, 84, 89, 86, 88.5, 80], 6),
]


def found(points, steps=6):
    o, h, l, c = zigzag(points, steps)
    return patterns.detect(h, l, c, indicators.atr(h, l, c, 14), 2.0), (o, h, l, c)


def apex_share(pat):
    """How far along the way from the pattern's first point to the apex of its two lines the breakout came."""
    (u0, uv0), (u1, uv1) = pat.lines[0]
    (w0, wv0), (w1, wv1) = pat.lines[1]
    su, sl = (uv1 - uv0) / (u1 - u0), (wv1 - wv0) / (w1 - w0)
    start = pat.points[0][0]
    width = (uv0 + su * (start - u0)) - (wv0 + sl * (start - w0))
    return (pat.breakout - start) / (width / (sl - su))


@pytest.mark.parametrize("kind,direction,points,steps", SHAPES,
                         ids=[f"{s[0]}-{'up' if s[1] > 0 else 'down'}" for s in SHAPES])
def test_each_pattern_breaks_out_with_its_measure_rule_targets(kind, direction, points, steps):
    pats, (o, h, l, c) = found(points, steps)
    pat = pats[0]
    assert (pat.kind, pat.direction) == (kind, direction)
    t, d = pat.breakout, pat.direction
    atr = indicators.atr(h, l, c, 14)
    assert (c[t] - pat.level) * d >= patterns.BREAK_ATR * atr[t]  # a close beyond the line by the margin
    assert pat.confirmed == t + 1 and (c[t + 1] - (pat.level + pat.slope)) * d > 0  # the next close stayed beyond
    assert pat.tp2 == pytest.approx(pat.level + d * pat.height)  # TP2: the full height from the breakout
    base_key, pct = patterns.MEASURE[kind][d]
    assert pat.pct == pct
    assert (pat.tp1 - pat.level) * d > 0 and abs(pat.tp1 - pat.level) <= abs(pat.tp2 - pat.level) + 1e-9
    assert (pat.stop - pat.level) * d < 0  # the stop is on the other side of the pattern


def test_double_top_target_is_43_percent_of_the_height_below_the_valley():
    [pat], _ = found([100, 110, 104, 110.2, 103, 96])
    valley, peak = 104 - 0.05, 110.2 + 0.05  # zigzag candles: ±0.05 wicks
    assert pat.height == pytest.approx(peak - valley)
    assert pat.tp1 == pytest.approx(valley - 0.43 * pat.height) and pat.stop == pytest.approx(peak)


def test_flag_targets_use_the_pole():
    pats, _ = found([106, 100, 116, 111, 114, 111.5, 120])
    flag = pats[0]
    assert flag.kind == "bull_flag" and flag.height == pytest.approx(116.05 - 99.95)  # the pole
    assert flag.tp1 == pytest.approx(110.95 + 0.46 * flag.height)  # Bulkowski: 46 % of the pole above the flag low
    assert flag.stop == pytest.approx(110.95)


@pytest.mark.parametrize("kind", ["ascending_triangle", "descending_triangle", "symmetrical_triangle",
                                  "rising_wedge", "falling_wedge"])
def test_triangles_and_wedges_break_out_before_three_quarters_of_the_way_to_the_apex(kind):
    shape = next(x for x in SHAPES if x[0] == kind)
    [pat], _ = found(shape[2], shape[3])
    assert apex_share(pat) <= patterns.APEX_SHARE


def test_a_close_beyond_the_line_near_the_apex_is_not_a_breakout():
    # lower highs 112 → 109, higher lows 102 → 105: the lines meet at bar 29, three quarters of the way is bar 23, and
    # the first close above the upper line comes on bar 26 — in the last quarter, where a triangle has lost its power
    # (Edwards & Magee, Murphy): no pattern, no trade
    o, h, l, c = zigzag([100, 112, 102, 109, 105, 115])
    upper = lambda k: 112.05 - 0.25 * (k - 6)  # noqa: E731 — through the highs at bars 6 and 18
    assert c[25] < upper(25) and c[26] - upper(26) > 1.0  # it does close well above the upper line
    pats, _ = found([100, 112, 102, 109, 105, 115])
    assert pats == []
    assert training.signals(_bars([100, 112, 102, 109, 105, 115]), training.pattern_params("1h")) == []


def _upto(arrays, k):
    o, h, l, c = (a[:k] for a in arrays)
    return patterns.detect(h, l, c, indicators.atr(h, l, c, 14), 2.0)


def test_a_breakout_is_known_only_when_the_next_close_confirms_it():
    pats, arrays = found([100, 110, 104, 110.2, 103, 96])
    t = pats[0].breakout
    assert _upto(arrays, t + 1) == []  # the breakout close alone is not a pattern yet
    assert [(x.kind, x.breakout, x.confirmed) for x in _upto(arrays, t + 2)] == [("double_top", t, t + 1)]


def test_a_false_breakout_is_not_traded():
    o, h, l, c = zigzag([100, 110, 104, 110.2, 103, 96])
    pats = patterns.detect(h, l, c, indicators.atr(h, l, c, 14), 2.0)
    t = pats[0].breakout
    c2, h2, l2 = c.copy(), h.copy(), l.copy()
    c2[t + 1] = h2[t + 1] = 104.5  # the next candle closes back above the neckline
    l2[t + 1] = min(l2[t + 1], 104.4)
    assert [x for x in patterns.detect(h2, l2, c2, indicators.atr(h2, l2, c2, 14), 2.0) if x.breakout == t] == []


def test_the_zigzag_knows_a_swing_once_price_turns_by_the_atr_multiple():
    o, h, l, c = zigzag([100, 110, 104, 110.2, 103, 96])
    atr = indicators.atr(h, l, c, 14)
    swings = patterns.zigzag(h, l, atr, 2.0)
    assert [p.kind for p in swings] == ["high", "low", "high"] and [p.index for p in swings] == [6, 12, 18]
    for p in swings:  # confirmed on the first bar 2 × ATR away from the swing, never before
        k = p.confirmed
        away = (h[p.index] - l[k]) if p.kind == "high" else (h[k] - l[p.index])
        assert away >= 2.0 * atr[k] and k > p.index


def _bars(points, steps=6):
    o, h, l, c = zigzag(points, steps)
    return Bars(np.arange(len(c), dtype=np.int64) * 3600, o, h, l, c, np.zeros(len(c)), 0.0)


def test_pattern_mode_waits_for_the_retest_and_stops_on_the_breakout_candles_atr_band():
    b = _bars([100, 110, 104, 110.2, 103, 104.5, 96, 90])  # breaks the neckline, comes back to it, then falls
    p = training.pattern_params("1h")
    assert (p.method, p.entry, p.hold, p.sl_atr, p.take, p.zz) == ("pattern", "retest", 0, 2.0, "both", 2.0)
    s = next(x for x in training.signals(b, p) if x.kind == "DOUBLE TOP")  # the bounce forms later patterns too
    atr = indicators.atr(b.high, b.low, b.close, 14)
    pat = patterns.detect(b.high, b.low, b.close, atr, 2.0)[0]
    t = pat.breakout
    assert (s.kind, s.scope, s.direction, s.created) == ("DOUBLE TOP", "pattern", -1, pat.confirmed)
    assert s.entry == pytest.approx(pat.level) and s.opened > pat.confirmed  # a limit on the neckline: the retest
    assert s.stop == pytest.approx(b.high[t] + 2.0 * atr[t])  # the ATR band above the breakout candle
    assert (s.tp1, s.target) == (pytest.approx(pat.tp1), pytest.approx(pat.tp2))
    assert s.status == "win" and s.reason.endswith("TP2")
    rows = training.pattern_metrics([s])  # every pattern and direction is listed, with 0 trades if absent
    assert {(r["kind"], r["direction"]) for r in rows} == {(k, d) for k, sides in patterns.MEASURE.items() for d in sides}
    assert [(r["name"], r["direction"], r["trades"]) for r in rows if r["trades"]] == [("DOUBLE TOP", -1, 1)]


def test_no_retest_no_trade():
    b = _bars([100, 110, 104, 110.2, 103, 96, 90, 85, 80, 75])  # straight down after the breakout
    [s] = training.signals(b, training.replace(training.pattern_params("1h"), expiry=6))
    assert s.status == "cancelled" and s.opened is None


@pytest.mark.parametrize("take", ["both", "tp1", "tp2"])
def test_take_profit_choice(take):
    b = _bars([100, 110, 104, 110.2, 103, 104.5, 96, 90])
    pat = patterns.detect(b.high, b.low, b.close, indicators.atr(b.high, b.low, b.close, 14), 2.0)[0]
    s = next(x for x in training.signals(b, training.replace(training.pattern_params("1h"), take=take))
             if x.kind == "DOUBLE TOP")
    risk = abs(s.entry - s.stop)
    if take == "both":  # half at TP1, the rest at TP2
        assert s.tp1 == pytest.approx(pat.tp1) and s.target == pytest.approx(pat.tp2)
        assert s.r == pytest.approx(round(0.5 * abs(pat.tp1 - s.entry) / risk + 0.5 * abs(pat.tp2 - s.entry) / risk, 3))
    else:  # everything at the one target chosen; the chart shows only that one
        goal = pat.tp1 if take == "tp1" else pat.tp2
        assert s.tp1 is None and s.target == pytest.approx(goal) and s.exit == pytest.approx(goal)
        assert s.r == pytest.approx(round(abs(goal - s.entry) / risk, 3)) and s.reason == take.upper()


def test_pattern_search_grid_keeps_the_rule():
    grid = list(training._grid("1h", 0, "pattern"))
    assert len(grid) == 9 and all(p.method == "pattern" and p.entry == "retest" and p.target == "tp12" and p.hold == 0
                                  for p in grid)
    assert {p.sl_atr for p in grid} == {1.5, 2.0, 2.5} and {p.zz for p in grid} == {1.5, 2.0, 3.0}


def test_entry_choice_breakout_close():
    b = _bars([100, 110, 104, 110.2, 103, 96, 90])
    s = next(x for x in training.signals(b, training.replace(training.pattern_params("1h"), entry="close")))
    assert s.opened == s.created and s.entry == pytest.approx(b.close[s.created])  # the confirming close, no retest


def test_retest_of_a_sloped_line_meets_it_where_it_is():
    b = _bars([96, 114, 100, 111, 103, 116, 106, 120, 126], steps=10)  # symmetrical triangle, breakout, pullback
    p = training.pattern_params("1h")
    s = next(x for x in training.signals(b, p) if x.kind == "SYMMETRICAL TRIANGLE")
    assert s.entry_slope < 0  # the upper line falls: the limit follows it
    assert s.opened is not None and s.opened > s.created
    assert s.entry == pytest.approx(s.entry_line0 + s.entry_slope * (s.opened - s.created))
    assert s.status == "win"
