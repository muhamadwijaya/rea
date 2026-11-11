"""Virtual-trading lifecycle and the honesty guards of the SMC tuner (synthetic bars, no network)."""

import numpy as np
import pytest

from app import training
from app.market_data import Bars


def bars(rows):
    o, h, l, c = (np.array([r[i] for r in rows], dtype=float) for i in range(4))
    t = np.arange(len(rows), dtype=np.int64) * 3600
    return Bars(t, o, h, l, c, np.zeros(len(rows)), 0.0)


def sig(**kw):
    base = dict(direction=1, kind="CHoCH", scope="internal", created=0, entry=100.0, stop=95.0, target=110.0)
    return training.Signal(**{**base, **kw})


# bar = (open, high, low, close); signal created on bar 0, long entry 100, stop 95, target 110
CASES = [
    ("win", [(104, 105, 103, 104), (103, 104, 99.5, 101), (102, 111, 101, 110)], "win", 1.0, 2),
    ("loss", [(104, 105, 103, 104), (103, 104, 99.5, 101), (100, 101, 94, 95)], "loss", -1.0, 2),
    ("both in one bar counts as loss", [(104, 105, 103, 104), (103, 104, 99.5, 101), (100, 112, 94, 105)], "loss", -1.0, 2),
    ("target before entry", [(104, 105, 103, 104), (106, 111, 105, 110)], "cancelled", None, 1),
    ("expired", [(104, 105, 103, 104)] + [(104, 105, 103, 104)] * 4, "cancelled", None, 3),
]


@pytest.mark.parametrize("name,rows,status,r,closed", CASES, ids=[c[0] for c in CASES])
def test_signal_lifecycle(name, rows, status, r, closed):
    s = training.simulate(bars(rows), sig(), set(), expiry=3, hold=10)
    assert s.status == status and s.closed == closed
    if r is not None:
        assert s.r == pytest.approx(2.0 if r > 0 else -1.0)


def test_structure_flip_cancels_before_entry_and_pending_stays_open_ended():
    rows = [(104, 105, 103, 104), (104, 105, 103, 104), (104, 105, 103, 104)]
    s = training.simulate(bars(rows), sig(), {2}, expiry=10, hold=10)
    assert (s.status, s.closed) == ("cancelled", 2) and "struktur" in s.reason
    s = training.simulate(bars(rows[:2]), sig(), set(), expiry=10, hold=10)
    assert s.status == "pending"  # not enough bars yet: still live


def test_tuner_never_trades_below_one_to_one_and_needs_samples():
    assert training.MIN_RR >= 1.0
    assert all(p.rr >= 1.0 for p in training._grid("1h"))
    weak = {"trades": 3, "wins": 3, "expectancyR": 1.0}
    assert training._score(weak, training.MIN_OOS_TRADES) < 0  # 3/3 is not enough evidence
    losing = {"trades": 30, "wins": 25, "expectancyR": -0.1}
    assert training._score(losing, training.MIN_OOS_TRADES) < 0  # a high win rate that loses money is rejected
    assert training.wilson_low(10, 10, training.ADOPT_Z) < 0.9  # 10/10 is not "99 %"


# ------------------------------------------------------------------ group learning and qualification


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A fresh training store in a temporary data folder; every asset belongs to the forex market."""
    monkeypatch.setenv("SHARX_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(training, "_market_of", lambda symbol: "forex")
    for state in (training._params, training._groups, training._evals):
        state.clear()
    training._conn = None
    yield tmp_path
    if training._conn is not None:
        training._conn.close()
    training._conn = None
    for state in (training._params, training._groups, training._evals):
        state.clear()


def custom(tf="1h", **kw):
    return training.Params(**{**training.asdict(training.default_params(tf)), "target": "fixed", "rr": 1.0, **kw})


def test_group_split_is_by_time_not_by_count():
    b = bars([(1, 1, 1, 1)] * 100)
    sigs = [sig(created=i) for i in (0, 59, 60, 79, 80, 99)]
    train, valid, test = training._parts(b, sigs)
    assert [s.created for s in train] == [0, 59]
    assert [s.created for s in valid] == [60, 79]
    assert [s.created for s in test] == [80, 99]


def test_inheritance_and_qualification_need_out_of_sample_evidence(store):
    sym, tf = "EURUSD=X", "1h"
    assert training.active(sym, tf)[0] == "default"
    assert training.evidence(sym, tf)["qualified"] is False  # nothing proven yet
    assert training.pivot_lengths(sym, tf) is None

    group = custom(swing=30, internal=4)
    training._groups[("forex", tf)] = (1, group, {"test": {"trades": 12, "wins": 11, "expectancyR": 0.9}})
    assert training.active(sym, tf) == ("group", 1, group)
    assert training.pivot_lengths(sym, tf) == (30, 4)
    assert training.evidence(sym, tf)["qualified"] is False  # 11/12 on a pooled test set is too few trades

    training._groups[("forex", tf)] = (1, group, {"test": {"trades": 80, "wins": 52, "expectancyR": 0.3}})
    assert training.evidence(sym, tf)["qualified"] is True
    training._evals[(sym, tf)] = {"outOfSample": {"trades": 10, "wins": 3, "expectancyR": -0.4}}
    assert training.evidence(sym, tf)["qualified"] is False  # the group setting fails on this asset

    own = custom(swing=20, internal=3)
    training._params[(sym, tf)] = (2, own, {"outOfSample": {"trades": 12, "wins": 10, "expectancyR": 0.5}})
    ev = training.evidence(sym, tf)
    assert (ev["source"], ev["trades"], ev["qualified"]) == ("asset", 12, True)
    assert training.pivot_lengths(sym, tf) == (20, 3)

    training._params[(sym, tf)] = (2, own, {"outOfSample": {"trades": 20, "wins": 13, "expectancyR": -0.05}})
    assert training.evidence(sym, tf)["qualified"] is False  # a high win rate that loses money never qualifies


def test_journal_written_by_earlier_versions_is_migrated(store):
    import json
    import sqlite3

    old = sqlite3.connect(store / "training.db")
    old.executescript("""
        CREATE TABLE signals (id TEXT PRIMARY KEY, symbol TEXT, market TEXT, tf TEXT, version INTEGER, direction INTEGER,
            kind TEXT, scope TEXT, created_t INTEGER, first_seen INTEGER, entry REAL, stop REAL, target REAL, expiry_t INTEGER,
            status TEXT, opened_t INTEGER, closed_t INTEGER, exit REAL, r REAL, reason TEXT, updated INTEGER);
        CREATE TABLE params (symbol TEXT, tf TEXT, version INTEGER, params TEXT, report TEXT, created INTEGER,
            PRIMARY KEY (symbol, tf, version));
        CREATE TABLE upgrades (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, tf TEXT, from_version INTEGER,
            to_version INTEGER, before TEXT, after TEXT, params TEXT, created INTEGER);
        CREATE TABLE tracking (symbol TEXT, tf TEXT, since INTEGER, last_tuned INTEGER, PRIMARY KEY (symbol, tf));
    """)
    dump = lambda p: json.dumps(training.asdict(p))  # noqa: E731
    rows = [
        ("PLACEHOLDER", dump(training.default_params("1h")), 1),  # earlier versions stored un-upgraded defaults
        ("UPGRADED", dump(custom(swing=20)), 1),
        ("BACK", dump(custom(swing=20)), 1),
        ("BACK", json.dumps({"inherit": True}), 2),  # went back to the group's parameters
        ("@forex", dump(custom(swing=30)), 1),
    ]
    old.executemany("INSERT INTO params VALUES (?, '1h', ?, ?, '{}', 0)", [(s, v, p) for s, p, v in rows])
    old.execute("INSERT INTO upgrades(symbol, tf, from_version, to_version, before, after, params, created) "
                "VALUES ('UPGRADED', '1h', 0, 1, '{}', '{}', '{}', 0)")
    old.execute("INSERT INTO signals(id, symbol, tf, status, r) VALUES ('s1', 'UPGRADED', '1h', 'win', 2.0)")
    old.commit()
    old.close()

    training._db()
    assert set(training._params) == {("UPGRADED", "1h")}
    assert training._groups[("forex", "1h")][1].swing == 30
    assert training.active("PLACEHOLDER", "1h")[0] == "group"
    assert training.active("BACK", "1h")[0] == "group"
    row = training._live_rows()[0]
    assert row["id"] == "s1" and row["qualified"] is None  # kept, but not counted as a qualified AI trade


def test_summary_reports_qualified_and_all_trades_separately(store):
    training._db()
    training._params[("A", "1h")] = (1, custom(), {"outOfSample": {"trades": 20, "wins": 15, "expectancyR": 0.5}})
    training._evals[("A", "1h")] = {"outOfSample": {"trades": 20, "wins": 15, "expectancyR": 0.5, "totalR": 10.0}}
    training._evals[("B", "1h")] = {"outOfSample": {"trades": 40, "wins": 10, "expectancyR": -0.2, "totalR": -8.0}}
    db = training._db()
    for i, (symbol, status, r, q) in enumerate([("A", "win", 1.0, 1), ("A", "loss", -1.0, 1), ("B", "loss", -1.0, 0)]):
        db.execute("INSERT INTO signals(id, symbol, market, tf, status, r, closed_t, qualified) VALUES (?,?,?,?,?,?,?,?)",
                   (f"s{i}", symbol, "forex", "1h", status, r, i, q))
    db.commit()
    d = training.summary()
    assert (d["live"]["trades"], d["liveAll"]["trades"]) == (2, 3)
    assert (d["backtest"]["trades"], d["backtest"]["winRate"]) == (20, 0.75)
    assert (d["backtestAll"]["trades"], d["backtestAll"]["wins"]) == (60, 25)
    assert d["sources"]["asset"]["trades"] == 20 and d["sources"]["default"]["trades"] == 40
    assert (d["qualifiedPairs"], d["evaluatedPairs"]) == (1, 2)
