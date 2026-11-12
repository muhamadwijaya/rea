"""Chart patterns: detection on clean synthetic shapes, measure-rule targets, no look-ahead, pattern-mode trades."""

import numpy as np
import pytest

from app import indicators, patterns, training
from app.market_data import Bars
from test_engine import zigzag

SHAPES = [
    ("double_top", -1, [100, 110, 104, 110.2, 103, 96]),
    ("double_bottom", 1, [110, 100, 106, 99.8, 107, 114]),
    ("head_shoulders", -1, [96, 106, 100, 112, 100.4, 106.2, 99, 92]),
    ("inverse_head_shoulders", 1, [114, 104, 110, 98, 109.6, 103.8, 111, 118]),
    ("ascending_triangle", 1, [100, 110, 102, 110.1, 105, 113]),
    ("descending_triangle", -1, [110, 100, 108, 99.9, 105, 96]),
    ("symmetrical_triangle", 1, [100, 112, 102, 109, 105, 115]),
    ("rectangle", 1, [104, 110, 104.2, 110.1, 104.3, 113]),
    ("falling_wedge", 1, [112, 100, 107, 97, 102, 95.5, 104]),
    ("rising_wedge", -1, [100, 104, 101, 109, 104, 111, 100]),
    ("bull_flag", 1, [106, 100, 116, 111, 114, 111.5, 120]),
    ("bear_flag", -1, [94, 100, 84, 89, 86, 88.5, 80]),
]


def found(points):
    o, h, l, c = zigzag(points)
    return patterns.detect(h, l, c, indicators.atr(h, l, c, 14), 2), (o, h, l, c)


@pytest.mark.parametrize("kind,direction,points", SHAPES, ids=[s[0] for s in SHAPES])
def test_each_pattern_breaks_out_with_its_measure_rule_targets(kind, direction, points):
    pats, (o, h, l, c) = found(points)
    pat = pats[0]
    assert (pat.kind, pat.direction) == (kind, direction)
    t, d = pat.breakout, pat.direction
    assert (c[t] - pat.level) * d > 0  # traded on a close beyond the line (the first one: see the look-ahead test)
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


def test_no_pattern_before_its_breakout_close():
    pats, (o, h, l, c) = found([100, 110, 104, 110.2, 103, 96])
    t = pats[0].breakout
    early = patterns.detect(h[:t], l[:t], c[:t], indicators.atr(h[:t], l[:t], c[:t], 14), 2)
    assert early == []  # known only on the close that breaks the neckline
    again = patterns.detect(h[:t + 1], l[:t + 1], c[:t + 1], indicators.atr(h[:t + 1], l[:t + 1], c[:t + 1], 14), 2)
    assert [(x.kind, x.breakout) for x in again] == [("double_top", t)]


def _bars(points):
    o, h, l, c = zigzag(points)
    return Bars(np.arange(len(c), dtype=np.int64) * 3600, o, h, l, c, np.zeros(len(c)), 0.0)


def test_pattern_mode_waits_for_the_retest_and_stops_on_the_breakout_candles_atr_band():
    b = _bars([100, 110, 104, 110.2, 103, 104.5, 96, 90])  # breaks the neckline, comes back to it, then falls
    p = training.replace(training.pattern_params("1h"), swing=2)
    assert (p.method, p.entry, p.hold, p.sl_atr, p.take) == ("pattern", "retest", 0, 2.0, "both")
    s = next(x for x in training.signals(b, p) if x.kind == "DOUBLE TOP")  # the bounce forms later patterns too
    atr = indicators.atr(b.high, b.low, b.close, 14)
    pat = patterns.detect(b.high, b.low, b.close, atr, 2)[0]
    t = pat.breakout
    assert (s.kind, s.scope, s.direction, s.created) == ("DOUBLE TOP", "pattern", -1, t)
    assert s.entry == pytest.approx(pat.level) and s.opened > t  # a limit on the neckline, filled on the retest
    assert s.stop == pytest.approx(b.high[t] + 2.0 * atr[t])  # the ATR band above the breakout candle
    assert (s.tp1, s.target) == (pytest.approx(pat.tp1), pytest.approx(pat.tp2))
    assert s.status == "win" and s.reason.endswith("TP2")
    assert training.pattern_metrics([s])["DOUBLE TOP"]["trades"] == 1


def test_no_retest_no_trade():
    b = _bars([100, 110, 104, 110.2, 103, 96, 90, 85, 80, 75])  # straight down after the breakout
    [s] = training.signals(b, training.replace(training.pattern_params("1h"), swing=2, expiry=6))
    assert s.status == "cancelled" and s.opened is None


@pytest.mark.parametrize("take", ["both", "tp1", "tp2"])
def test_take_profit_choice(take):
    b = _bars([100, 110, 104, 110.2, 103, 104.5, 96, 90])
    pat = patterns.detect(b.high, b.low, b.close, indicators.atr(b.high, b.low, b.close, 14), 2)[0]
    s = next(x for x in training.signals(b, training.replace(training.pattern_params("1h"), swing=2, take=take))
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
    assert {p.sl_atr for p in grid} == {1.5, 2.0, 2.5}


def test_entry_choice_breakout_close():
    b = _bars([100, 110, 104, 110.2, 103, 96, 90])
    s = next(x for x in training.signals(b, training.replace(training.pattern_params("1h"), swing=2, entry="close")))
    assert s.opened == s.created and s.entry == pytest.approx(b.close[s.created])  # no waiting for a retest


def test_retest_of_a_sloped_line_meets_it_where_it_is():
    b = _bars([100, 112, 102, 109, 105, 115, 107.5, 113, 118])  # symmetrical triangle, breakout up, pullback
    p = training.replace(training.pattern_params("1h"), swing=2)
    s = next(x for x in training.signals(b, p) if x.kind == "SYMMETRICAL TRIANGLE")
    assert s.entry_slope < 0  # the upper line falls: the limit follows it
    if s.opened is not None:
        assert s.entry == pytest.approx(s.entry_line0 + s.entry_slope * (s.opened - s.created))
