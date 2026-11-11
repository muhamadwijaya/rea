import numpy as np
import pytest

from app import instruments, sentiment, smc
from app.errors import InvalidSymbol


def zigzag(points, steps=6):
    """Piecewise-linear close path through `points`, with small candle bodies."""
    closes = []
    for a, b in zip(points, points[1:]):
        closes.extend(np.linspace(a, b, steps, endpoint=False))
    closes.append(points[-1])
    c = np.array(closes, dtype=float)
    o = np.concatenate(([c[0]], c[:-1]))
    h = np.maximum(o, c) + 0.05
    l = np.minimum(o, c) - 0.05
    return o, h, l, c


def test_pivots_are_confirmed_after_length_bars():
    o, h, l, c = zigzag([10, 12, 11, 13, 12, 14])
    pivots = smc.find_pivots(h, l, 3)
    assert pivots, "expected pivots on a zigzag"
    for p in pivots:
        assert p.confirmed == p.index + 3
    highs = [p for p in pivots if p.kind == "high"]
    assert [p.label for p in highs][1:] == ["HH"] * (len(highs) - 1)


def test_uptrend_then_breakdown_is_bos_then_choch():
    o, h, l, c = zigzag([10, 12, 11, 13, 12, 14, 13, 15, 13.5, 10, 9])
    r = smc.analyze(o, h, l, c, swing_length=3, internal_length=2)
    kinds = [(b.kind, b.direction) for b in r.swing.breaks]
    assert ("BOS", smc.BULL) in kinds
    first_bear = next(b for b in r.swing.breaks if b.direction == smc.BEAR)
    assert first_bear.kind == "CHoCH"
    assert r.swing.trend == smc.BEAR
    # every structure break creates an order block on the opposite side of the move
    assert any(ob.direction == smc.BULL for ob in r.swing.order_blocks)


def test_break_never_uses_future_bars():
    o, h, l, c = zigzag([10, 12, 11, 13, 12, 14, 13, 15])
    full = smc.analyze(o, h, l, c, 3, 2)
    for b in full.swing.breaks:
        part = smc.analyze(o[: b.index + 1], h[: b.index + 1], l[: b.index + 1], c[: b.index + 1], 3, 2)
        assert any(x.index == b.index and x.kind == b.kind for x in part.swing.breaks)


def test_fair_value_gap_detected_and_filled():
    o = np.array([10.0, 10.1, 11.0, 11.3, 11.4])
    c = np.array([10.1, 11.0, 11.3, 11.4, 10.0])
    h = np.maximum(o, c) + 0.05
    l = np.minimum(o, c) - 0.05
    gaps = smc.detect_gaps(o, h, l, c, np.full(len(c), 0.5))
    first = gaps[0]
    assert first.direction == smc.BULL and first.index == 1
    assert (first.bottom, first.top) == pytest.approx((h[0], l[2]))
    assert first.filled == 4 and first.fill == 1.0


@pytest.mark.parametrize(
    "query,symbol,yahoo,cls",
    [
        ("eur/usd", "EURUSD", "EURUSD=X", "forex"),
        ("IDX:BBCA", "BBCA", "BBCA.JK", "idx"),
        ("tlkm.jk", "TLKM", "TLKM.JK", "idx"),
        ("NYSE:KO", "KO", "KO", "us"),
        ("cadjpy", "CADJPY", "CADJPY=X", "forex"),
        ("btcusdt", "BTCUSD", "BTC-USD", "crypto"),
    ],
)
def test_resolve(query, symbol, yahoo, cls):
    inst = instruments.resolve(query)
    assert (inst.symbol, inst.yahoo, inst.asset_class) == (symbol, yahoo, cls)


def test_every_watchlist_instrument_has_a_market():
    for inst in instruments.watchlist():
        assert inst.market in instruments.MARKETS
    assert instruments.find("IHSG").market == "saham-idx"
    assert instruments.find("DXY").market == "forex"
    assert instruments.find("SOLUSD").market == "kripto"
    assert {i.symbol for i in instruments.by_market("kripto")} >= {"BTCUSD", "ETHUSD"}


def test_resolve_rejects_garbage():
    with pytest.raises(InvalidSymbol):
        instruments.resolve("<script>")


def test_sentiment_negation_and_assets():
    pos = sentiment.score_text("IHSG menguat, saham bank melonjak")
    neg = sentiment.score_text("Gold plunges as dollar surges after hawkish Fed")
    flipped = sentiment.score_text("Stocks did not fall")
    assert pos["label"] == "bullish" and "IDX" in pos["assets"]
    assert "GOLD" in neg["assets"] and "USD" in neg["assets"]
    assert flipped["score"] > 0
