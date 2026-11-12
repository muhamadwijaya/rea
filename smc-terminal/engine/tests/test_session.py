"""Live training session: the AI learns forward, from earlier candles only, and trades the next stretch."""

import time
from types import SimpleNamespace

import numpy as np
import pytest

from app import session, training
from app.market_data import Bars


def test_stretches_cover_all_candles_after_a_warm_up():
    parts = session.bounds(5000)
    assert parts[0] == (0, 500) and parts[-1][1] == 5000
    assert all(a < z for a, z in parts) and all(parts[i][1] == parts[i + 1][0] for i in range(len(parts) - 1))
    assert len(parts) <= session.SEGMENTS + 1
    assert session.bounds(300)[0] == (0, 200)


@pytest.fixture
def fake_env(monkeypatch):
    n = 1000
    t = np.arange(n, dtype=np.int64) * 300
    c = np.ones(n)
    b = Bars(t, c, c + 0.1, c - 0.1, c, np.zeros(n), 0.0)
    learned, saved = [], []
    inst = SimpleNamespace(symbol="EURUSD", market="forex", asset_class="forex", to_json=lambda: {"symbol": "EURUSD", "decimals": 5})
    monkeypatch.setattr(session.instruments, "resolve", lambda s: inst)
    monkeypatch.setattr(training, "history", lambda i, tf, bars=None: b)
    monkeypatch.setattr(training, "htf_alignment", lambda *a, **k: None)

    def fake_tune(prefix, tf, current, align=None, level=0):
        learned.append(len(prefix))
        better = training.replace(current, rr=2.0) if len(learned) == 2 else current  # learns something once
        rep = {"outOfSample": {"trades": 5, "wins": 3}}
        return better, rep, rep

    def fake_signals(bars, p, r=None, align=None, one_position=True):  # one trade every 50 candles; wins when the rr-2 setting is active
        return [training.Signal(1, "CHoCH", "internal", i, 1, 0.9, 1.2, status="win" if p.rr == 2.0 else "loss",
                                r=2.0 if p.rr == 2.0 else -1.0, opened=i + 1, closed=i + 5) for i in range(10, len(bars), 50)]

    monkeypatch.setattr(training, "tune", fake_tune)
    monkeypatch.setattr(training, "signals", fake_signals)
    monkeypatch.setattr(training, "tune_pair", lambda *a, **k: saved.append(a[1]) or None)
    monkeypatch.setattr(training, "grid_size", lambda tf, level=0: 100 * (level + 1))
    monkeypatch.setattr(training, "active", lambda s, tf: ("asset", 3, training.default_params(tf)))
    monkeypatch.setattr(training, "lab_status", lambda: {"state": "idle"})
    yield SimpleNamespace(bars=b, learned=learned, saved=saved)
    session.stop()


def wait_done():
    for _ in range(200):
        if session.status()["state"] not in ("loading", "running"):
            return session.status()
        time.sleep(0.02)
    raise AssertionError("session did not finish")


def test_each_stretch_is_traded_with_a_setting_learned_from_earlier_candles_only(fake_env):
    session.start("EURUSD", "5m", 1000)
    st = wait_done()
    starts = [a for a, _ in session.bounds(1000)][1:]
    assert fake_env.learned == starts  # before each stretch: exactly the candles before it, nothing after
    assert st["state"] == "done" and st["computedTo"] == 1000 and fake_env.saved == ["5m"]
    by_seg = {s["index"]: s for s in st["segments"]}
    assert by_seg[0]["learn"] is None and by_seg[0]["version"] == 1
    assert by_seg[2]["learn"]["changed"] and by_seg[2]["version"] == 2
    for tr in st["trades"]:
        seg = by_seg[tr["segment"]]
        assert seg["fromT"] <= tr["created_t"] <= seg["toT"]  # every trade belongs to the stretch it was traded in
    early = [tr for tr in st["trades"] if tr["version"] == 1]
    assert early and all(tr["status"] == "loss" for tr in early)  # before learning: the default setting's results
    assert st["stats"]["trades"] == len(st["trades"])
    assert len(session.candles()["candles"]) == 1000


def test_one_session_at_a_time_and_input_is_validated(fake_env):
    with pytest.raises(training.LabInvalid):
        session.start("EURUSD", "10m", 1000)
    with pytest.raises(training.LabInvalid):
        session.start("EURUSD", "5m", 50)


def test_search_widens_while_a_core_pair_stays_below_goal(tmp_path, monkeypatch):
    monkeypatch.setenv("SHARX_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(training, "_market_of", lambda s: "forex")
    for state in (training._params, training._groups, training._evals):
        state.clear()
    training._conn = None
    seen = []

    def fake_tune(bars, tf, current, align=None, level=0):
        seen.append(level)
        rep = {"outOfSample": {"trades": 20, "wins": 11, "expectancyR": 0.1}, "inSample": {}, "all": {}}
        return current, rep, rep  # nothing better found

    monkeypatch.setattr(training, "tune", fake_tune)
    monkeypatch.setattr(training, "signals", lambda *a, **k: [])
    b = Bars(np.arange(300, dtype=np.int64) * 60, *(np.ones(300) for _ in range(4)), np.zeros(300), 0.0)
    try:
        core = SimpleNamespace(symbol="EURUSD", market="forex", core=True)
        other = SimpleNamespace(symbol="ZZZ", market="forex", core=False)
        for inst in (core, other):
            training._since(inst.symbol, "1h", 0)
        for _ in range(4):
            training.tune_pair(core, "1h", b)
            training.tune_pair(other, "1h", b)
        assert seen[0::2] == [0, 1, 2, 2]  # core: wider every round without success, capped at MAX_LEVEL
        assert seen[1::2] == [0, 0, 0, 0]  # universe assets keep the base search (CPU)
        assert training.grid_size("1h", 0) == 54 < training.grid_size("1h", 1) < training.grid_size("1h", 2)
    finally:
        training._conn.close()
        training._conn = None


def test_improve_rounds_widen_the_search_and_end_with_an_exam_on_locked_candles(fake_env, monkeypatch):
    levels = []

    def fake_tune(prefix, tf, current, align=None, level=0):
        levels.append((level, len(prefix)))
        better = training.replace(current, rr=2.0) if level >= 1 else current  # the wider search finds a winner
        rep = {"outOfSample": {"trades": 5, "wins": 3}}
        return better, rep, rep

    monkeypatch.setattr(training, "tune", fake_tune)
    session.start("EURUSD", "5m", 1000, improve=True)
    st = wait_done()
    cut = 1000 - max(session.MIN_EXAM, int(1000 * session.EXAM_SHARE))
    assert st["state"] == "done" and st["exam"]["from"] == cut
    assert max(n for _, n in levels) < cut  # improving never saw the exam candles
    assert [r["level"] for r in st["rounds"]] == [0, 1, 2]  # round 3 brought nothing over round 2 → stop
    assert [r["best"] for r in st["rounds"]] == [False, True, False]
    exam = [t for t in st["trades"] if t["exam"]]
    assert exam and all(t["created_t"] >= fake_env.bars.time[cut] for t in exam)
    assert st["exam"]["stats"]["winRate"] == 1.0 and st["exam"]["level"] == 1  # the best round's setting, run once
    assert st["saved"]["level"] == 1


def test_results_are_plain_json_values_even_for_trades_closed_by_the_time_limit():
    import json

    c = np.array([1.04, 1.01, 1.02, 1.03, 1.03])  # entry 1.0 touched on bar 1, neither stop nor target until the limit
    b = Bars(np.arange(5, dtype=np.int64) * 60, c, c + 0.005, np.array([1.03, 0.99, 1.0, 1.01, 1.01]), c, np.zeros(5), 0.0)
    s = training.simulate(b, training.Signal(1, "CHoCH", "internal", 0, 1.0, 0.9, 1.5), set(), expiry=3, hold=2)
    assert s.status == "timeout" and type(s.r) is float and type(s.exit) is float
    m = training.metrics([s])
    json.dumps(m)  # no numpy scalars
    json.dumps(session.status())
