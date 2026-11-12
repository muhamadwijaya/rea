"""Training lab, higher-timeframe alignment without hindsight, the trade dictionary, EXPERT mode, the 1-minute
candlestick trigger, calendar events per asset and the news context (synthetic data, no network)."""

import time
from types import SimpleNamespace

import numpy as np
import pytest

from app import candles, fundamental, newsctx, training
from app.market_data import Bars


def make_bars(close, step=3600, start=0):
    c = np.asarray(close, dtype=float)
    o = np.concatenate([[c[0]], c[:-1]])
    t = start + np.arange(len(c), dtype=np.int64) * step
    return Bars(t, o, np.maximum(o, c) + 0.2, np.minimum(o, c) - 0.2, c, np.zeros(len(c)), 0.0)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("SHARX_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(training, "_market_of", lambda s: "forex")
    for state in (training._params, training._groups, training._evals):
        state.clear()
    training._conn = None
    yield
    if training._conn is not None:
        training._conn.close()
    training._conn = None
    for state in (training._params, training._groups, training._evals):
        state.clear()


def test_higher_timeframes_are_read_only_after_their_bars_closed(monkeypatch):
    inst = SimpleNamespace(symbol="X", yahoo="X")
    x = make_bars(np.ones(12), step=3600)             # 1h bars 00:00 … 11:00
    h4 = make_bars(np.ones(70), step=14400)           # 4h bars 00:00, 04:00, 08:00, …
    trends = {"4h": np.resize([1, -1], 70), "1d": None}
    monkeypatch.setattr(training.market_data, "history_cached", lambda i, tf: False)
    monkeypatch.setattr(training.market_data, "cached", lambda i, tf: h4 if tf == "4h" else None)
    monkeypatch.setattr(training.smc, "analyze", lambda *a: SimpleNamespace(swing=SimpleNamespace(trend_history=trends["4h"])))
    align = training.htf_alignment(inst, "1h", x)
    # the 1h bar closing at 04:00 sees the 00:00–04:00 4h bar (+1); the 4h bar of 04:00 counts only from 08:00 on
    assert list(align[:3]) == [0, 0, 0] and align[3] == 1 and align[7] == -1 and align[11] == 1


def test_alignment_filter_keeps_only_trades_the_higher_timeframes_agree_with():
    rng = np.random.default_rng(3)
    b = make_bars(100 + np.cumsum(rng.normal(0, 1, 800)))
    p = training.Params(swing=10, internal=3, trend_filter=False, target="rr", rr=1.5)
    base = training.signals(b, p)
    assert {s.direction for s in base} == {1, -1}
    bullish = np.ones(len(b))
    kept = training.signals(b, training.replace(p, mtf="align"), None, bullish)
    assert kept and all(s.direction == 1 and s.align == 1.0 for s in kept)
    assert training.signals(b, training.replace(p, mtf="align")) == []  # without higher-timeframe data: no trade
    assert all(s.align == s.direction * 1.0 for s in training.signals(b, p, None, bullish))


def test_dictionary_keeps_trades_outside_the_new_window_and_measures_alignment(store, monkeypatch):
    inst = SimpleNamespace(symbol="EURUSD", market="forex")
    b = make_bars(np.ones(100))

    def sig(created, d, r, align):
        return training.Signal(d, "CHoCH", "internal", created, 1, 0.9, 1.2, status="win" if r > 0 else "loss", r=r, align=align)

    trades = [sig(10, 1, 1.5, 1.0), sig(80, 1, 1.5, 1.0), sig(85, -1, -1.0, -1.0), sig(90, 1, -1.0, 0.0)]
    monkeypatch.setattr(training, "signals", lambda *a, **k: trades)
    assert training._record_patterns(inst, "1h", b, training.default_params("1h"), 2, None) == 4
    later = make_bars(np.ones(50), start=int(b.time[50]))  # a newer window: rows before it stay in the dictionary
    monkeypatch.setattr(training, "signals", lambda *a, **k: [sig(40, 1, 1.5, 1.0)])
    training._record_patterns(inst, "1h", later, training.default_params("1h"), 3, None)
    stats = training.mtf_stats("EURUSD")
    assert stats["all"]["trades"] == 1  # out of sample: the old window's 3 later rows were replaced by the new one
    rows = training._db().execute("SELECT created_t, version FROM patterns ORDER BY created_t").fetchall()
    assert rows[0] == (int(b.time[10]), 2) and len(rows) == 2
    assert stats["buckets"]["aligned"]["trades"] == 1


def test_lab_validates_and_trains_the_chosen_markets_and_timeframes(store, monkeypatch):
    with pytest.raises(training.LabInvalid):
        training.lab_start([], ["1h"], 5000)
    with pytest.raises(training.LabInvalid):
        training.lab_start(["forex"], ["1h"], 50)
    inst = SimpleNamespace(symbol="EURUSD", market="forex")
    b = make_bars(np.ones(300))
    tuned = []
    monkeypatch.setattr(training, "lab_plan", lambda m, t: [("forex", "5m", [inst])])
    monkeypatch.setattr(training.market_data, "history_many", lambda insts, tf: {"EURUSD": b})
    monkeypatch.setattr(training.market_data, "cached", lambda i, tf: None)
    monkeypatch.setattr(training, "htf_alignment", lambda *a, **k: None)

    def fake_tune(i, tf, bars, align=None):
        tuned.append((i.symbol, tf, len(bars)))
        training._evals[(i.symbol, tf)] = {"outOfSample": {"trades": 10, "wins": 7, "totalR": 4.0}}
        return {"to": 2}

    monkeypatch.setattr(training, "tune_pair", fake_tune)
    training.lab_start(["forex"], ["5m"], 250)
    for _ in range(100):
        if training.lab_status()["state"] != "running":
            break
        time.sleep(0.02)
    st = training.lab_status()
    assert st["state"] == "done" and tuned == [("EURUSD", "5m", 250)]  # trained on the last 250 candles asked for
    assert (st["trades"], st["wins"], st["upgrades"], st["progressPct"]) == (10, 7, 1, 100.0)
    assert st["groups"][0]["winRate"] == 0.7


def test_bullish_engulfing_is_found_and_its_record_measured():
    o = np.array([10, 10, 10, 10.0, 9.9, 9.6, 9.45, 10.3])
    c = np.array([10, 10, 10, 9.9, 9.6, 9.5, 10.4, 10.5])
    h, l = np.maximum(o, c) + 0.05, np.minimum(o, c) - 0.05
    found = candles.detect(o, h, l, c)
    assert (6, "engulfing", 1) in found
    record = candles.stats(c, [(6, "engulfing", 1)], horizon=1)
    assert record[("engulfing", 1)] == {"n": 1, "hits": 1, "hitRate": 1.0}


def test_calendar_release_maps_to_the_asset_direction(monkeypatch):
    now = time.time()
    events = [{"title": "CPI m/m", "currency": "USD", "ts": now + 3600, "status": "upcoming", "impact": "High",
               "kind": "inflation", "forecast": "0.3%", "previous": "0.4%"},
              {"title": "CPI m/m", "currency": "USD", "ts": now - 3600, "status": "released", "impact": "High", "kind": "inflation"}]
    eurusd = SimpleNamespace(symbol="EURUSD", asset_class="forex", market="forex")
    gold = SimpleNamespace(symbol="XAUUSD", asset_class="commodity", market="forex")
    [e] = fundamental.upcoming_for(eurusd, {"events": events})
    sign = fundamental.classify("CPI m/m").sign
    assert (e["ifAbove"], e["ifBelow"], e["consensusChange"]) == (-sign, sign, "turun")
    assert fundamental.upcoming_for(gold, {"events": events})[0]["ifAbove"] == -sign
    assert fundamental.upcoming_for(SimpleNamespace(symbol="BBCA", asset_class="stock", market="saham-idx"),
                                    {"events": [{**events[0], "currency": "JPY"}]}) == []


def test_news_context_is_signed_per_asset_and_windowed():
    now = 1_000_000.0
    newsctx._items.clear()
    raw = [{"published": now - 600}, {"published": now - 3 * 86400}]
    scored = [{"score": 0.6, "symbols": [{"symbol": "USDJPY", "sign": -1}]}, {"score": 0.9, "symbols": [{"symbol": "USDJPY", "sign": 1}]}]
    newsctx.record(raw, scored, now)
    assert newsctx.score("USDJPY", now) == {"score": -0.6, "n": 1}  # the old headline is outside the window
    assert newsctx.score("EURUSD", now) is None


def test_flat_one_minute_feeds_are_reported_not_read_as_patterns(monkeypatch):
    flat = make_bars(np.full(100, 1.1), step=60)
    flat = training.Bars(flat.time, flat.close, flat.close, flat.close, flat.close, flat.volume, 0.0)  # O=H=L=C
    monkeypatch.setattr(candles.market_data, "history_many", lambda insts, tf: {"EURUSD": flat})
    out = candles.trigger(SimpleNamespace(symbol="EURUSD"))
    assert out["pattern"] is None and "OHLC" in out["unavailable"]
