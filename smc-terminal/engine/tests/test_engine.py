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


@pytest.mark.parametrize(
    "text,lang,expected",
    [
        ("Nvidia shares jump after earnings beat", "en", {("NVDA", 1)}),
        ("Yen strengthens as BOJ signals rate hike", "en", {("USDJPY", -1)}),
        ("Saham BBCA dan BBRI menguat", "id", {("BBCA", 1), ("BBRI", 1)}),
        ("Rupiah melemah ke Rp 18.150", "id", {("USDIDR", -1)}),
        ("Bitcoin falls below $74,000", "en", {("BTCUSD", 1)}),
        ("CEO says AI spending is up", "en", set()),
        # generic Indonesian words and index providers are not companies
        ("Pemanfaatan Teknologi AI Ubah Cara Investor Bertransaksi Saham", "id", set()),
        ("Gelagat Investor Asing di Saham BRPT & BRMS Jelang Review MSCI", "id", {("BRPT", 1), ("BRMS", 1)}),
        # a currency named only as the quote unit of a crypto headline is not tagged; explicit pairs are
        ("Bitcoin price in euro hits a record", "en", {("BTCUSD", 1)}),
        ("EUR/USD and Bitcoin both rally", "en", {("EURUSD", 1), ("BTCUSD", 1)}),
        ("Bitcoin and gold rally together", "en", {("BTCUSD", 1), ("XAUUSD", 1)}),
    ],
)
def test_tagger_maps_headlines_to_instruments(text, lang, expected):
    from app.tagger import tag

    assert {(t.symbol, t.sign) for t in tag(text, lang)} == expected


def test_universe_extends_core_watchlist_without_duplicates():
    uni = instruments.universe()
    assert len(uni) > len(instruments.watchlist())
    symbols = [i.symbol for i in uni]
    assert len(symbols) == len(set(symbols))
    assert all(i.core for i in uni[: len(instruments.watchlist())])


def test_fundamental_extracts_actual_and_direction():
    from app import fundamental

    ind = fundamental.classify("CPI m/m")
    assert ind.kind == "inflation" and ind.sign == 1
    assert fundamental.classify("Unemployment Rate").sign == -1
    assert fundamental.classify("FOMC Member Waller Speaks").kind == "speech"
    found = fundamental.extract("US CPI rises 0.4% vs 0.3% expected", 0.3)
    assert found["actual"] == pytest.approx(0.4) and found["consensus"] == pytest.approx(0.3)
    claims = fundamental.classify("Unemployment Claims")
    assert fundamental.extract("Jobless claims fall to 205K, below expectations", 220e3, claims.kind, claims.words)["actual"] == pytest.approx(205e3)
    # A number about another asset in a CPI headline is not the CPI reading.
    found = fundamental.extract("Gold climbs 0.74% after hot CPI print", 0.3, ind.kind, ind.words)
    assert "actual" not in found and found["numeric"] == 1
    # Years and unrelated numbers are not taken as the actual value.
    assert "actual" not in fundamental.extract("Fed outlook to 2027 unclear", 0.3)
    assert fundamental.parse_value("195K") == pytest.approx(195e3)


def test_fundamental_release_prediction_without_network(monkeypatch):
    from app import fundamental

    monkeypatch.setattr(fundamental, "_reaction", lambda currency, t0: {"symbol": "EURUSD", "movePct": -0.2, "currencyMovePct": 0.2})
    events = [{"id": "1", "title": "CPI m/m", "currency": "USD", "date": "2026-11-10T13:30:00+00:00", "impact": "High",
               "forecast": "0.3%", "previous": "0.2%"}]
    news = [{"title": "U.S. CPI rises 0.5% vs 0.3% expected", "published": "2026-11-10T13:31:00+00:00", "source": "x"}]
    result = fundamental.analyze(events, news)
    event = result["events"][0]
    assert event["actual"] == pytest.approx(0.5)
    assert event["prediction"]["direction"] == 1
    assert event["verdict"] == "confirmed"
    assert result["accuracy"]["confirmed"] == 1 and result["accuracy"]["pct"] == 100.0
    assert result["currencies"]["USD"]["score"] > 0
    # A second indicator released at the same time shares the reaction and is not counted twice.
    events.append({**events[0], "id": "2", "title": "Core CPI m/m", "forecast": "0.2%"})
    assert fundamental.analyze(events, news)["accuracy"]["judged"] == 1


def test_every_instrument_has_a_map_block():
    from app import blocks

    uni = instruments.universe()
    assert all(blocks.block_of(i) != "Lainnya" for i in uni if i.asset_class in ("us", "forex", "commodity", "index"))
    crypto = [i for i in uni if i.asset_class == "crypto"]
    assert sum(blocks.block_of(i) != "Lainnya" for i in crypto) >= len(crypto) - 5
    assert blocks.block_of(instruments.find("AAPL")) == "Information Technology"  # core entry keeps the universe sector
    assert blocks.block_of(instruments.find("EURUSD")) == "Mayor" and blocks.block_of(instruments.find("XAUUSD")) == "Logam"
