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
