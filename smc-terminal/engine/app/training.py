"""SMC virtual trading ("AI training"): signal journal, paper trades, measured accuracy and self-tuning.

Strategy (causal — every decision uses only bars up to that moment):
  a structure break (CHoCH, optionally BOS) of the chosen scope creates a signal from the order block that
  produced it: limit entry at the block (edge or middle), stop beyond the block ± k·ATR, target = entry +
  RR × risk. Optional filters: entry in discount/premium of the dealing range known at that bar, and an
  internal break only when it agrees with the swing trend.

Lifecycle of every signal (bar by bar, with timestamps):
  PENDING → OPEN when price trades into the entry, or CANCELLED when, before entry: the structure breaks the
  other way, price reaches the target first, price closes beyond the stop, or the signal expires.
  OPEN → WIN (target) / LOSS (stop; if a bar touches both, it is counted as a loss) / TIMEOUT (max holding,
  closed at the bar's close). Result in R (risk units).

Live journal: signals created after an asset/timeframe starts being tracked are stored (SQLite, in the app
data folder) with the moment the system first saw them, and advanced as new bars arrive; closed rows are
frozen.

Learning happens at two levels, and each asset uses the most specific setting that has been proven:
  * Group (market × timeframe): the histories of up to GROUP_MAX_PAIRS assets of one market are pooled and
    split by time into 60 % train / 20 % validation / 20 % test. The grid is ranked on train, the winner is
    chosen on validation, and the test part (which never took part in the choice) is what gets reported.
  * Asset: a parameter grid is backtested on the asset's own history; the best set on the first 70 % is
    checked on the last 30 % (out of sample). It replaces the active set only when its out-of-sample win
    rate (Wilson lower bound) is higher and its expectancy stays positive, with a minimum number of trades —
    so the win rate cannot be bought with tiny targets (RR ≥ 1) or by fitting noise. An asset override is
    dropped again when the group setting does better on that asset.
  Inheritance: asset override → group setting → default. Every upgrade is versioned and logged.

Qualification: a signal counts as an AI trade only when the setting behind it has evidence — enough
out-of-sample trades, a Wilson lower bound of the win rate ≥ QUALIFY_LOW and a positive expectancy. Other
signals are still traded virtually as shadow trades (to keep learning) but are reported separately, so
adding many unproven assets cannot silently dilute — or inflate — the headline numbers. The goal shown is
99 %, but only measured results are reported.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import logging
import math
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass, replace

import numpy as np

from . import instruments, market_data, paths, smc
from .market_data import Bars

log = logging.getLogger("smc.training")

TARGET_WIN_RATE = 0.70  # first milestone shown in the UI
GOAL_WIN_RATE = 0.99
SPLIT = 0.70
MIN_IS_TRADES = 8
MIN_OOS_TRADES = 5
ADOPT_Z = 1.64  # adoption compares one-sided 90 % lower bounds of the win rate
MIN_IMPROVEMENT = 0.02
MIN_RR = 1.0  # setups paying less than they risk are never taken (keeps the win rate honest)
CORE_TFS = ("15m", "1h", "4h", "1d")
UNIVERSE_TFS = ("1d",)
TUNE_EVERY = 6 * 3600
TF_SECONDS = {"1m": 60, "5m": 300, "10m": 600, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400, "1w": 604800}


@dataclass(frozen=True)
class Params:
    swing: int
    internal: int
    scope: str = "internal"  # which breaks create signals
    kinds: str = "all"  # "all" = BOS + CHoCH, "CHoCH" = only changes of character
    entry: str = "mid"  # "mid" | "edge" of the order block
    sl_atr: float = 0.3
    target: str = "liq"  # "liq" = the impulse extreme the break created (resting liquidity), "rr" = fixed RR
    rr: float = 1.5  # fixed reward:risk when target == "rr"
    pd_filter: bool = False
    trend_filter: bool = True
    expiry: int = 30  # bars a pending signal stays valid
    hold: int = 60  # bars an open trade may run before it is closed at market

    def key(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)


def default_params(tf_key: str) -> Params:
    tf = market_data.timeframe(tf_key)
    return Params(swing=tf.swing, internal=tf.internal)


@dataclass
class Signal:
    direction: int
    kind: str
    scope: str
    created: int  # bar index of the break
    entry: float
    stop: float
    target: float
    status: str = "pending"  # pending | open | win | loss | timeout | cancelled
    opened: int | None = None
    closed: int | None = None
    exit: float | None = None
    r: float | None = None
    reason: str = ""


# ------------------------------------------------------------------ strategy + simulation


def _swing_range_at(r: smc.SmcResult):
    """Causal dealing range: last confirmed swing high / low at every bar."""
    n = len(r.atr)
    hi = np.full(n, np.nan)
    lo = np.full(n, np.nan)
    for p in sorted(r.swing.pivots, key=lambda p: p.confirmed):
        if p.confirmed >= n:
            continue
        (hi if p.kind == "high" else lo)[p.confirmed:] = p.price
    return hi, lo


def simulate(bars: Bars, s: Signal, against: set[int], expiry: int, hold: int) -> Signal:
    """Advance one signal through the bars after its creation (see module docstring)."""
    h, l, c = bars.high, bars.low, bars.close
    n = len(c)
    d = s.direction
    risk = abs(s.entry - s.stop)
    k = s.created + 1
    end_pending = min(n, s.created + 1 + expiry)
    while s.status == "pending" and k < end_pending:
        if k in against:
            s.status, s.closed, s.reason = "cancelled", k, "struktur berbalik sebelum entry"
            break
        touched = l[k] <= s.entry if d > 0 else h[k] >= s.entry
        if touched:
            s.status, s.opened = "open", k
            break
        if (h[k] >= s.target) if d > 0 else (l[k] <= s.target):
            s.status, s.closed, s.reason = "cancelled", k, "target tercapai sebelum entry"
            break
        if (c[k] < s.stop) if d > 0 else (c[k] > s.stop):
            s.status, s.closed, s.reason = "cancelled", k, "zona ditembus sebelum entry"
            break
        k += 1
    if s.status == "pending" and k >= s.created + 1 + expiry and n > s.created + expiry:
        s.status, s.closed, s.reason = "cancelled", s.created + expiry, "kedaluwarsa tanpa entry"
    if s.status != "open":
        return s
    k = s.opened
    end_hold = min(n, s.opened + 1 + hold)
    while k < end_hold:
        hit_stop = l[k] <= s.stop if d > 0 else h[k] >= s.stop
        hit_target = (h[k] >= s.target if d > 0 else l[k] <= s.target) and k > s.opened
        if hit_stop:  # also when the target is touched in the same bar: conservative
            s.status, s.closed, s.exit, s.r, s.reason = "loss", k, s.stop, -1.0, "stop loss"
            return s
        if hit_target:
            s.status, s.closed, s.exit, s.r, s.reason = "win", k, s.target, round(abs(s.target - s.entry) / risk, 3), "target"
            return s
        k += 1
    if n > s.opened + hold:
        k = s.opened + hold
        s.status, s.closed, s.exit = "timeout", k, float(c[k])
        s.r, s.reason = round((c[k] - s.entry) * d / risk, 3), "batas waktu, ditutup di harga pasar"
    return s


def signals(bars: Bars, p: Params, r: smc.SmcResult | None = None) -> list[Signal]:
    o, h, l, c = bars.open, bars.high, bars.low, bars.close
    if r is None:
        r = smc.analyze(o, h, l, c, p.swing, p.internal)
    structure = r.internal if p.scope == "internal" else r.swing
    blocks = {(ob.created, ob.direction): ob for ob in structure.order_blocks}
    against = {1: set(), -1: set()}  # bar indices of breaks in each direction (cancel rule)
    for b in structure.breaks:
        against[-b.direction].add(b.index)
    hi, lo = _swing_range_at(r) if p.pd_filter else (None, None)
    out: list[Signal] = []
    for b in structure.breaks:
        if p.kinds == "CHoCH" and b.kind != "CHoCH":
            continue
        ob = blocks.get((b.index, b.direction))
        if ob is None:
            continue
        t, d = b.index, b.direction
        if p.trend_filter and p.scope == "internal" and r.swing.trend_history[t] != d:
            continue
        entry = (ob.top + ob.bottom) / 2 if p.entry == "mid" else (ob.top if d > 0 else ob.bottom)
        if p.pd_filter:
            top, bottom = hi[t], lo[t]
            if math.isnan(top) or math.isnan(bottom) or top <= bottom:
                continue
            mid = (top + bottom) / 2
            if (d > 0 and entry > mid) or (d < 0 and entry < mid):
                continue
        atr_t = float(r.atr[t])
        stop = ob.bottom - p.sl_atr * atr_t if d > 0 else ob.top + p.sl_atr * atr_t
        risk = abs(entry - stop)
        if risk <= 0 or not math.isfinite(risk):
            continue
        if p.target == "liq":  # back to the extreme of the move that broke structure
            target = float(h[ob.index:t + 1].max()) if d > 0 else float(l[ob.index:t + 1].min())
        else:
            target = entry + d * p.rr * risk
        if (target - entry) * d < MIN_RR * risk or (target - c[t]) * d <= 0:
            continue  # less than 1:1, or the target is already behind price: no trade
        s = Signal(d, b.kind, b.scope, t, float(entry), float(stop), float(target))
        out.append(simulate(bars, s, against[d], p.expiry, p.hold))
    return out


def metrics(sigs: list[Signal]) -> dict:
    closed = [s for s in sigs if s.status in ("win", "loss", "timeout")]
    wins = sum(1 for s in closed if (s.r or 0) > 0)
    rs = [s.r or 0.0 for s in closed]
    gross_win = sum(x for x in rs if x > 0)
    gross_loss = -sum(x for x in rs if x < 0)
    equity = np.cumsum(rs) if rs else np.array([0.0])
    drawdown = float(np.max(np.maximum.accumulate(equity) - equity)) if rs else 0.0
    n = len(closed)
    return {
        "signals": len(sigs), "trades": n, "wins": wins, "losses": n - wins,
        "cancelled": sum(1 for s in sigs if s.status == "cancelled"),
        "winRate": round(wins / n, 4) if n else None, "winRateLow": round(wilson_low(wins, n), 4) if n else None,
        "expectancyR": round(float(np.mean(rs)), 3) if rs else None, "totalR": round(float(sum(rs)), 2),
        "profitFactor": round(gross_win / gross_loss, 2) if gross_loss else (None if not gross_win else 99.0),
        "maxDrawdownR": round(drawdown, 2),
    }


def wilson_low(wins: int, n: int, z: float = 1.0) -> float:
    """Lower bound of the win rate (Wilson interval): small samples cannot look better than they are."""
    if n == 0:
        return 0.0
    p = wins / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (centre - margin) / denom


def walk_forward(bars: Bars, p: Params, r: smc.SmcResult | None = None) -> dict:
    sigs = signals(bars, p, r)
    split_t = bars.time[0] + (bars.time[-1] - bars.time[0]) * SPLIT
    ins = [s for s in sigs if bars.time[s.created] < split_t]
    oos = [s for s in sigs if bars.time[s.created] >= split_t]
    return {"inSample": metrics(ins), "outOfSample": metrics(oos), "all": metrics(sigs)}


def _grid(tf_key: str):
    base = default_params(tf_key)
    lengths = sorted({(base.swing, base.internal), (max(4, base.swing - 4), base.internal), (base.swing + 4, base.internal + 1)})
    targets = (("liq", base.rr), ("rr", 1.0), ("rr", 1.5), ("rr", 2.0))
    for (swing, internal), scope, kinds, entry, sl_atr, (target, rr), pd, trend, expiry in itertools.product(
            lengths, ("internal", "swing"), ("all", "CHoCH"), ("mid", "edge"), (0.25, 0.5), targets, (False, True), (True, False),
            (base.expiry, base.expiry * 2)):
        if scope == "swing" and not trend:
            continue  # the trend filter only applies to internal breaks
        yield replace(base, swing=swing, internal=internal, scope=scope, kinds=kinds, entry=entry, sl_atr=sl_atr,
                      target=target, rr=rr, pd_filter=pd, trend_filter=trend, expiry=expiry)


def _score(m: dict, min_trades: int) -> float:
    if m["trades"] < min_trades or (m["expectancyR"] or 0) <= 0:
        return -1.0
    return wilson_low(m["wins"], m["trades"], ADOPT_Z)


def tune(bars: Bars, tf_key: str, current: Params) -> tuple[Params, dict, dict]:
    """(best params, its walk-forward report, the current params' report)."""
    analyses: dict[tuple[int, int], smc.SmcResult] = {}

    def analysis_for(p: Params) -> smc.SmcResult:
        key = (p.swing, p.internal)
        if key not in analyses:
            analyses[key] = smc.analyze(bars.open, bars.high, bars.low, bars.close, p.swing, p.internal)
        return analyses[key]

    current_report = walk_forward(bars, current, analysis_for(current))
    ranked = []
    for p in _grid(tf_key):
        report = walk_forward(bars, p, analysis_for(p))
        ranked.append((_score(report["inSample"], MIN_IS_TRADES), p, report))
    ranked.sort(key=lambda x: x[0], reverse=True)
    best, best_report, best_oos = current, current_report, _score(current_report["outOfSample"], MIN_OOS_TRADES)
    for is_score, p, report in ranked[:8]:
        if is_score < 0:
            break
        oos_score = _score(report["outOfSample"], MIN_OOS_TRADES)
        if oos_score > best_oos + MIN_IMPROVEMENT:
            best, best_report, best_oos = p, report, oos_score
    return best, best_report, current_report


# ------------------------------------------------------------------ group learning (market × timeframe)

GROUP_SPLIT = (0.60, 0.80)  # per asset timeline: train < 60 % ≤ validation < 80 % ≤ test
GROUP_MAX_PAIRS = 150
GROUP_MIN_PAIRS = 5
GROUP_MIN_TRAIN = 40
GROUP_MIN_VALID = 15
QUALIFY_LOW = 0.50  # qualified = lower bound (z = 1) of the win rate ≥ 50 % with positive expectancy
QUALIFY_MIN_TRADES = {"asset": 8, "group": 30, "default": 8}


def _parts(bars: Bars, sigs: list[Signal]) -> tuple[list, list, list]:
    t0, t1 = float(bars.time[0]), float(bars.time[-1])
    a, b = t0 + (t1 - t0) * GROUP_SPLIT[0], t0 + (t1 - t0) * GROUP_SPLIT[1]
    train = [s for s in sigs if bars.time[s.created] < a]
    valid = [s for s in sigs if a <= bars.time[s.created] < b]
    test = [s for s in sigs if bars.time[s.created] >= b]
    return train, valid, test


def tune_group(pairs: list[tuple[str, Bars]], tf_key: str, current: Params) -> tuple[Params, dict, dict]:
    """Pool every asset of a market & timeframe: rank parameter sets on the train part, choose on the validation
    part, report on the untouched test part (so the reported win rate is not inflated by the selection)."""
    analyses: dict[tuple[str, int, int], smc.SmcResult] = {}

    def pooled(p: Params) -> dict:
        train, valid, test = [], [], []
        for sym, bars in pairs:
            key = (sym, p.swing, p.internal)
            if key not in analyses:
                analyses[key] = smc.analyze(bars.open, bars.high, bars.low, bars.close, p.swing, p.internal)
            a, b, c = _parts(bars, signals(bars, p, analyses[key]))
            train += a
            valid += b
            test += c
        return {"train": metrics(train), "valid": metrics(valid), "test": metrics(test), "pairs": len(pairs)}

    current_report = pooled(current)
    ranked = sorted(((_score(r["train"], GROUP_MIN_TRAIN), p, r) for p in _grid(tf_key) for r in [pooled(p)]),
                    key=lambda x: x[0], reverse=True)
    best, best_report = current, current_report
    best_valid = _score(current_report["valid"], GROUP_MIN_VALID)
    for train_score, p, report in ranked[:10]:
        if train_score < 0:
            break
        valid_score = _score(report["valid"], GROUP_MIN_VALID)
        if valid_score > best_valid + 0.01:
            best, best_report, best_valid = p, report, valid_score
    return best, best_report, current_report


# ------------------------------------------------------------------ persistence

_db_lock = threading.Lock()
_conn: sqlite3.Connection | None = None
# Parameters are inherited: an asset's own upgrade, else its market & timeframe group, else the default.
_params: dict[tuple[str, str], tuple[int, Params, dict]] = {}  # per-asset overrides: (symbol, tf) -> (version, params, report)
_groups: dict[tuple[str, str], tuple[int, Params, dict]] = {}  # (market, tf) -> (version, params, pooled report)
_evals: dict[tuple[str, str], dict] = {}  # latest walk-forward of each pair's active parameters


def _group_key(market: str) -> str:
    return f"@{market}"


def _columns(db: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in db.execute(f"PRAGMA table_info({table})")}


def _db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(paths.data_dir() / "training.db", check_same_thread=False, timeout=30)
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.executescript("""
            CREATE TABLE IF NOT EXISTS signals (
                id TEXT PRIMARY KEY, symbol TEXT, market TEXT, tf TEXT, version INTEGER, direction INTEGER, kind TEXT,
                scope TEXT, created_t INTEGER, first_seen INTEGER, entry REAL, stop REAL, target REAL, expiry_t INTEGER,
                status TEXT, opened_t INTEGER, closed_t INTEGER, exit REAL, r REAL, reason TEXT, updated INTEGER);
            CREATE INDEX IF NOT EXISTS signals_asset ON signals(symbol, tf, created_t);
            CREATE INDEX IF NOT EXISTS signals_status ON signals(status, closed_t);
            CREATE TABLE IF NOT EXISTS params (
                symbol TEXT, tf TEXT, version INTEGER, params TEXT, report TEXT, created INTEGER,
                PRIMARY KEY (symbol, tf, version));
            CREATE TABLE IF NOT EXISTS upgrades (
                id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, tf TEXT, from_version INTEGER, to_version INTEGER,
                before TEXT, after TEXT, params TEXT, created INTEGER);
            CREATE TABLE IF NOT EXISTS tracking (symbol TEXT, tf TEXT, since INTEGER, last_tuned INTEGER,
                PRIMARY KEY (symbol, tf));
        """)
        # additive migrations of journals written by earlier versions
        if "qualified" not in _columns(_conn, "signals"):
            _conn.execute("ALTER TABLE signals ADD COLUMN qualified INTEGER")
        if "report" not in _columns(_conn, "tracking"):
            _conn.execute("ALTER TABLE tracking ADD COLUMN report TEXT")
        upgraded = {(s, tf) for s, tf in _conn.execute("SELECT DISTINCT symbol, tf FROM upgrades")}
        for symbol, tf, version, params, report in _conn.execute(
                "SELECT symbol, tf, version, params, report FROM params p WHERE version = "
                "(SELECT MAX(version) FROM params q WHERE q.symbol = p.symbol AND q.tf = p.tf)"):
            raw = json.loads(params)
            if raw.get("inherit"):
                continue  # the asset went back to its group's parameters
            entry = (version, Params(**raw), json.loads(report or "{}"))
            if symbol.startswith("@"):
                _groups[(symbol[1:], tf)] = entry
            elif (symbol, tf) in upgraded or entry[1] != default_params(tf):
                _params[(symbol, tf)] = entry  # earlier versions also stored un-upgraded defaults: not overrides
        for symbol, tf, report in _conn.execute("SELECT symbol, tf, report FROM tracking WHERE report IS NOT NULL"):
            _evals[(symbol, tf)] = json.loads(report)
        _conn.commit()
    return _conn


def _market_of(symbol: str) -> str:
    inst = instruments.find(symbol)
    return inst.market if inst else ""


def active(symbol: str, tf: str) -> tuple[str, int, Params]:
    """(source, version, params) — source: "asset" (own upgrade), "group" (market & timeframe) or "default"."""
    with _db_lock:
        _db()
        own = _params.get((symbol, tf))
        group = _groups.get((_market_of(symbol), tf))
    if own:
        return "asset", own[0], own[1]
    if group:
        return "group", group[0], group[1]
    return "default", 0, default_params(tf)


def evidence(symbol: str, tf: str) -> dict:
    """What the active parameters of a pair have proven out of sample, and whether its signals qualify."""
    source, _, _ = active(symbol, tf)
    own_eval = (_evals.get((symbol, tf)) or {}).get("outOfSample") or {}
    if source == "asset":
        m = ((_params.get((symbol, tf)) or (0, None, {}))[2] or {}).get("outOfSample") or own_eval
    elif source == "group":
        m = ((_groups.get((_market_of(symbol), tf)) or (0, None, {}))[2] or {}).get("test") or {}
    else:
        m = own_eval
    trades, wins = m.get("trades", 0) or 0, m.get("wins", 0) or 0
    low = wilson_low(wins, trades) if trades else 0.0
    qualified = trades >= QUALIFY_MIN_TRADES[source] and low >= QUALIFY_LOW and (m.get("expectancyR") or 0) > 0
    # a group setting that clearly fails on this particular asset does not qualify it
    if qualified and source == "group" and own_eval.get("trades", 0) >= 8 and (own_eval.get("expectancyR") or 0) <= 0:
        qualified = False
    return {"source": source, "trades": trades, "wins": wins, "winRate": round(wins / trades, 4) if trades else None,
            "winRateLow": round(low, 4) if trades else None, "expectancyR": m.get("expectancyR"), "qualified": qualified}


def pivot_lengths(symbol: str, tf: str) -> tuple[int, int] | None:
    """Tuned swing/internal pivot lengths for the chart & screener SMC (None = timeframe default)."""
    source, _, p = active(symbol, tf)
    return None if source == "default" else (p.swing, p.internal)


def _since(symbol: str, tf: str, now: int) -> int:
    row = _db().execute("SELECT since FROM tracking WHERE symbol=? AND tf=?", (symbol, tf)).fetchone()
    if row:
        return row[0]
    _db().execute("INSERT INTO tracking(symbol, tf, since, last_tuned) VALUES (?,?,?,0)", (symbol, tf, now))
    return now


# ------------------------------------------------------------------ live journal

SIGNAL_COLUMNS = ["id", "symbol", "market", "tf", "version", "direction", "kind", "scope", "created_t", "first_seen", "entry",
                  "stop", "target", "expiry_t", "status", "opened_t", "closed_t", "exit", "r", "reason", "updated", "qualified"]


def _sid(symbol: str, tf: str, s: Signal, t: int) -> str:
    return f"{symbol}|{tf}|{t}|{s.direction}|{s.scope}"


def track(inst: instruments.Instrument, tf: str, bars: Bars) -> int:
    """Record / advance live signals of one asset & timeframe. Returns the number of rows written.

    A signal is "qualified" when, at the moment it appears, the active parameters of this pair have proven an
    edge out of sample (see `evidence`); other signals are still recorded as shadow trades for learning.
    """
    now = int(time.time())
    _, version, p = active(inst.symbol, tf)
    qualified_now = 1 if evidence(inst.symbol, tf)["qualified"] else 0
    sigs = signals(bars, p)
    tf_sec = TF_SECONDS.get(tf, 3600)
    index_of = {int(t): i for i, t in enumerate(bars.time)}
    written = 0
    insert = (f"INSERT OR REPLACE INTO signals ({', '.join(SIGNAL_COLUMNS)}) VALUES "
              f"({', '.join(':' + c for c in SIGNAL_COLUMNS)})")
    with _db_lock:
        db = _db()
        since = _since(inst.symbol, tf, now)
        existing = {row[0]: row for row in db.execute(
            "SELECT id, status, first_seen, version, direction, entry, stop, target, created_t, qualified FROM signals "
            "WHERE symbol=? AND tf=? AND created_t >= ?", (inst.symbol, tf, since - tf_sec * (p.expiry + p.hold)))}
        for s in sigs:
            created_t = int(bars.time[s.created])
            if created_t + tf_sec < since:
                continue  # before tracking started: history (covered by the backtest), not a live signal
            sid = _sid(inst.symbol, tf, s, created_t)
            old = existing.get(sid)
            if old and old[1] in ("win", "loss", "timeout", "cancelled"):
                continue  # closed rows are frozen
            row = _row(inst, tf, version, s, bars, created_t, old[2] if old else now, p, tf_sec)
            row["qualified"] = old[9] if old and old[9] is not None else qualified_now  # decided once, when it appears
            db.execute(insert, {**row, "id": sid})
            written += 1
        # signals of an older parameter version keep their stored levels and are advanced on their own
        for sid, status, first_seen, ver, direction, entry, stop, target, created_t, _q in existing.values():
            if status in ("pending", "open") and ver != version and created_t in index_of:
                s = simulate(bars, Signal(direction, "", "", index_of[created_t], entry, stop, target), set(), p.expiry, p.hold)
                if s.status != status:
                    row = _row(inst, tf, ver, s, bars, created_t, first_seen, p, tf_sec)
                    db.execute("UPDATE signals SET status=?, opened_t=?, closed_t=?, exit=?, r=?, reason=?, updated=? WHERE id=?",
                               (row["status"], row["opened_t"], row["closed_t"], row["exit"], row["r"], row["reason"], now, sid))
                    written += 1
        db.commit()
    return written


def _row(inst, tf, version, s: Signal, bars: Bars, created_t, first_seen, p: Params, tf_sec) -> dict:
    t = lambda i: int(bars.time[i]) if i is not None and i < len(bars.time) else None  # noqa: E731
    return {"symbol": inst.symbol, "market": inst.market, "tf": tf, "version": version, "direction": s.direction,
            "kind": s.kind, "scope": s.scope, "created_t": created_t, "first_seen": first_seen, "entry": s.entry,
            "stop": s.stop, "target": s.target, "expiry_t": created_t + tf_sec * p.expiry, "status": s.status,
            "opened_t": t(s.opened), "closed_t": t(s.closed), "exit": s.exit, "r": s.r, "reason": s.reason,
            "updated": int(time.time())}


def _save_params(symbol: str, tf: str, params: dict, report: dict, now: int) -> int:
    row = _db().execute("SELECT MAX(version) FROM params WHERE symbol=? AND tf=?", (symbol, tf)).fetchone()
    version = (row[0] or 0) + 1
    _db().execute("INSERT INTO params VALUES (?,?,?,?,?,?)", (symbol, tf, version, json.dumps(params, sort_keys=True),
                                                             json.dumps(report), now))
    return version


def _log_upgrade(symbol, tf, old_version, new_version, before, after, params, now) -> None:
    _db().execute("INSERT INTO upgrades(symbol, tf, from_version, to_version, before, after, params, created) "
                  "VALUES (?,?,?,?,?,?,?,?)", (symbol, tf, old_version, new_version, json.dumps(before), json.dumps(after),
                                               json.dumps(params, sort_keys=True), now))


def tune_pair(inst: instruments.Instrument, tf: str, bars: Bars) -> dict | None:
    """Give an asset its own parameters only when they beat its inherited ones (group / default) out of sample;
    drop an own setting that the inherited one now beats. Every evaluation is kept for the accuracy board."""
    source, version, current = active(inst.symbol, tf)
    best, best_report, current_report = tune(bars, tf, current)
    now = int(time.time())
    upgrade = None
    with _db_lock:
        db = _db()
        if best != current:
            new_version = _save_params(inst.symbol, tf, asdict(best), best_report, now)
            _params[(inst.symbol, tf)] = (new_version, best, best_report)
            upgrade = {"symbol": inst.symbol, "tf": tf, "from": version, "to": new_version, "before": current_report["outOfSample"],
                       "after": best_report["outOfSample"], "params": asdict(best), "inherited": source != "asset"}
            _log_upgrade(inst.symbol, tf, version, new_version, upgrade["before"], upgrade["after"], asdict(best), now)
            report = best_report
        else:
            report = current_report
            group = _groups.get((inst.market, tf))
            if source == "asset" and group:
                inherited = walk_forward(bars, group[1])
                if _score(inherited["outOfSample"], MIN_OOS_TRADES) > _score(current_report["outOfSample"], MIN_OOS_TRADES) + MIN_IMPROVEMENT:
                    _save_params(inst.symbol, tf, {"inherit": True}, inherited, now)
                    _params.pop((inst.symbol, tf), None)
                    report = inherited
        _evals[(inst.symbol, tf)] = report
        db.execute("UPDATE tracking SET last_tuned=?, report=? WHERE symbol=? AND tf=?",
                   (now, json.dumps(report), inst.symbol, tf))
        db.commit()
    if upgrade:
        log.info("SMC upgrade %s %s v%d→v%d: OOS win %.0f%% → %.0f%%", inst.symbol, tf, version, upgrade["to"],
                 100 * (upgrade["before"]["winRate"] or 0), 100 * (upgrade["after"]["winRate"] or 0))
    return upgrade


def train_group(market: str, tf: str, pairs: list[tuple[str, Bars]]) -> dict | None:
    """Learn the shared parameters of one market & timeframe from all its assets (see `tune_group`)."""
    key = _group_key(market)
    current_entry = _groups.get((market, tf))
    current = current_entry[1] if current_entry else default_params(tf)
    best, best_report, current_report = tune_group(pairs, tf, current)
    now = int(time.time())
    with _db_lock:
        db = _db()
        version = current_entry[0] if current_entry else 0
        if best != current or current_entry is None:
            report = best_report if best != current else current_report
            new_version = _save_params(key, tf, asdict(best), report, now)
            _groups[(market, tf)] = (new_version, best, report)
            if best != current:
                _log_upgrade(key, tf, version, new_version, current_report["test"], best_report["test"], asdict(best), now)
        else:
            _groups[(market, tf)] = (version, current, current_report)
            db.execute("UPDATE params SET report=? WHERE symbol=? AND tf=? AND version=?", (json.dumps(current_report), key, tf, version))
        if db.execute("SELECT 1 FROM tracking WHERE symbol=? AND tf=?", (key, tf)).fetchone() is None:
            db.execute("INSERT INTO tracking(symbol, tf, since, last_tuned) VALUES (?,?,?,0)", (key, tf, now))
        db.execute("UPDATE tracking SET last_tuned=? WHERE symbol=? AND tf=?", (now, key, tf))
        db.commit()
    if best != current:
        log.info("SMC group upgrade %s %s: test win %.0f%% → %.0f%% (%d assets)", market, tf,
                 100 * (current_report["test"]["winRate"] or 0), 100 * (best_report["test"]["winRate"] or 0), len(pairs))
    return _groups[(market, tf)][2]


# ------------------------------------------------------------------ queries (API)


def _live_rows(where: str = "", args: tuple = ()) -> list[dict]:
    with _db_lock:
        rows = _db().execute(f"SELECT {', '.join(SIGNAL_COLUMNS)} FROM signals {where}", args).fetchall()
    return [dict(zip(SIGNAL_COLUMNS, r)) for r in rows]


def _live_metrics(rows: list[dict]) -> dict:
    closed = [r for r in rows if r["status"] in ("win", "loss", "timeout")]
    wins = sum(1 for r in closed if (r["r"] or 0) > 0)
    rs = [r["r"] or 0.0 for r in closed]
    gw, gl = sum(x for x in rs if x > 0), -sum(x for x in rs if x < 0)
    return {"signals": len(rows), "trades": len(closed), "wins": wins, "losses": len(closed) - wins,
            "pending": sum(1 for r in rows if r["status"] == "pending"), "open": sum(1 for r in rows if r["status"] == "open"),
            "cancelled": sum(1 for r in rows if r["status"] == "cancelled"),
            "winRate": round(wins / len(closed), 4) if closed else None,
            "winRateLow": round(wilson_low(wins, len(closed)), 4) if closed else None,
            "expectancyR": round(float(np.mean(rs)), 3) if rs else None, "totalR": round(float(sum(rs)), 2),
            "profitFactor": round(gw / gl, 2) if gl else (None if not gw else 99.0)}


def _sum(parts: list[dict]) -> dict:
    trades = sum(m.get("trades", 0) or 0 for m in parts)
    wins = sum(m.get("wins", 0) or 0 for m in parts)
    total_r = sum(m.get("totalR", 0) or 0 for m in parts)
    return {"pairs": len(parts), "trades": trades, "wins": wins, "winRate": round(wins / trades, 4) if trades else None,
            "winRateLow": round(wilson_low(wins, trades), 4) if trades else None,
            "expectancyR": round(total_r / trades, 3) if trades else None}


def summary() -> dict:
    rows = _live_rows()
    with _db_lock:
        _db()
        evals = dict(_evals)
        groups = dict(_groups)
        ups = _db().execute("SELECT symbol, tf, from_version, to_version, before, after, params, created FROM upgrades "
                            "ORDER BY id DESC LIMIT 40").fetchall()
        tuned = _db().execute("SELECT COUNT(*) FROM tracking WHERE last_tuned > 0 AND symbol NOT LIKE '@%'").fetchone()[0]
        tracked = _db().execute("SELECT COUNT(*) FROM tracking WHERE symbol NOT LIKE '@%'").fetchone()[0]
        upgrades_total = _db().execute("SELECT COUNT(*) FROM upgrades").fetchone()[0]
    closed = sorted((r for r in rows if r["status"] in ("win", "loss", "timeout") and r["qualified"]), key=lambda r: r["closed_t"] or 0)
    equity, total = [], 0.0
    for r in closed:
        total += r["r"] or 0
        equity.append([r["closed_t"], round(total, 2)])
    board, by_source, by_market, qualified_oos = [], {}, {}, []
    rows_by_pair: dict[tuple[str, str], list] = {}
    for r in rows:
        rows_by_pair.setdefault((r["symbol"], r["tf"]), []).append(r)
    for (symbol, tf), rep in evals.items():
        ev = evidence(symbol, tf)
        oos = (rep or {}).get("outOfSample") or {}
        market = _market_of(symbol)
        by_source.setdefault(ev["source"], []).append(oos)
        by_market.setdefault(market, {"all": [], "qualified": []})["all"].append(oos)
        if ev["qualified"]:
            qualified_oos.append(oos)
            by_market[market]["qualified"].append(oos)
        source, version, _ = active(symbol, tf)
        board.append({"symbol": symbol, "tf": tf, "market": market, "version": version, "source": source,
                      "qualified": ev["qualified"], "evidence": ev, "backtest": oos,
                      "live": _live_metrics([r for r in rows_by_pair.get((symbol, tf), []) if r["qualified"]])})
    board.sort(key=lambda b: (b["qualified"], b["evidence"].get("winRateLow") or 0, b["backtest"].get("trades") or 0), reverse=True)
    live_q = [r for r in rows if r["qualified"]]
    markets = sorted({r["market"] for r in rows} | set(by_market))
    return {
        "generatedAt": int(time.time()), "target": TARGET_WIN_RATE, "goal": GOAL_WIN_RATE, "qualifyLow": QUALIFY_LOW,
        "live": _live_metrics(live_q), "liveAll": _live_metrics(rows),
        "backtest": _sum(qualified_oos), "backtestAll": _sum([(r or {}).get("outOfSample") or {} for r in evals.values()]),
        "sources": {k: _sum(v) for k, v in by_source.items()},
        "markets": {m: {"live": _live_metrics([r for r in rows if r["market"] == m and r["qualified"]]),
                        "liveAll": _live_metrics([r for r in rows if r["market"] == m]),
                        "backtest": _sum(by_market.get(m, {}).get("qualified", [])),
                        "backtestAll": _sum(by_market.get(m, {}).get("all", []))} for m in markets},
        "groups": [{"market": m, "tf": tf, "version": v, "params": asdict(p), "test": rep.get("test"), "valid": rep.get("valid"),
                    "train": rep.get("train"), "pairs": rep.get("pairs")} for (m, tf), (v, p, rep) in sorted(groups.items())],
        "equity": equity[-500:], "tracked": tracked, "tuned": tuned, "upgradesTotal": upgrades_total,
        "qualifiedPairs": sum(1 for b in board if b["qualified"]), "evaluatedPairs": len(board),
        "upgrades": [{"symbol": s, "tf": tf, "from": a, "to": b, "before": json.loads(be), "after": json.loads(af),
                      "params": json.loads(pa), "created": cr} for s, tf, a, b, be, af, pa, cr in ups],
        "leaderboard": board[:400],
        "recent": sorted(rows, key=lambda r: r["updated"] or 0, reverse=True)[:80],
        "progress": dict(_progress),
    }


def asset(symbol: str, tf: str) -> dict:
    inst = instruments.resolve(symbol)
    source, version, p = active(inst.symbol, tf)
    rows = sorted(_live_rows("WHERE symbol=? AND tf=?", (inst.symbol, tf)), key=lambda r: r["created_t"], reverse=True)
    bars = market_data.cached(inst, tf)
    recent: list[dict] = []
    if bars is not None and len(bars) > 50:
        tf_sec = TF_SECONDS.get(tf, 3600)
        for s in signals(bars, p)[-5:][::-1]:
            created_t = int(bars.time[s.created])
            recent.append(_row(inst, tf, version, s, bars, created_t, None, p, tf_sec))
    with _db_lock:
        ups = _db().execute("SELECT from_version, to_version, before, after, created FROM upgrades WHERE symbol=? AND tf=? "
                            "ORDER BY id DESC LIMIT 10", (inst.symbol, tf)).fetchall()
    ev = evidence(inst.symbol, tf)
    return {"symbol": inst.symbol, "tf": tf, "version": version, "source": source, "params": asdict(p),
            "backtest": _evals.get((inst.symbol, tf)) or {}, "evidence": ev,
            "live": _live_metrics([r for r in rows if r["qualified"]]), "liveAll": _live_metrics(rows),
            "journal": rows[:30], "latest": recent, "cancelRules": cancel_rules(p, tf),
            "upgrades": [{"from": a, "to": b, "before": json.loads(be), "after": json.loads(af), "created": c}
                         for a, b, be, af, c in ups],
            "target": TARGET_WIN_RATE, "goal": GOAL_WIN_RATE}


def cancel_rules(p: Params, tf: str) -> list[str]:
    span = TF_SECONDS.get(tf, 3600) * p.expiry
    hours = span / 3600
    return [
        "struktur berbalik (CHoCH/BOS berlawanan) sebelum harga menyentuh entry",
        "harga mencapai target sebelum entry (peluang terlewat)",
        "harga close menembus stop loss sebelum entry (zona tidak valid)",
        f"kedaluwarsa setelah {p.expiry} bar ({hours:.0f} jam) tanpa entry" if hours < 72 else
        f"kedaluwarsa setelah {p.expiry} bar ({hours / 24:.0f} hari) tanpa entry",
    ]


def reliability(symbol: str, tfs: list[str]) -> dict | None:
    """Measured accuracy of the SMC signals of this asset (proven evidence + qualified live trades) for the AI confluence."""
    trades = wins = 0
    for tf in tfs:
        ev = evidence(symbol, tf)
        trades += ev["trades"]
        wins += ev["wins"]
    live = _live_metrics(_live_rows("WHERE symbol=? AND qualified=1 AND tf IN (%s)" % ",".join("?" * len(tfs)), (symbol, *tfs)))
    trades += live["trades"]
    wins += live["wins"]
    if trades < 5:
        return None
    rate = wins / trades
    return {"trades": trades, "winRate": round(rate, 4), "factor": round(min(1.3, max(0.5, wilson_low(wins, trades) / 0.5)), 2)}


# ------------------------------------------------------------------ background loop

_progress = {"tracked": 0, "lastPass": None, "tuning": None, "tunedThisRun": 0}
_seen_bars: dict[tuple[str, str], int] = {}


def _pairs() -> list[tuple[instruments.Instrument, str]]:
    pairs = [(i, tf) for i in instruments.watchlist() for tf in CORE_TFS]
    core = {i.symbol for i in instruments.watchlist()}
    pairs += [(i, tf) for i in instruments.universe() if i.symbol not in core for tf in UNIVERSE_TFS]
    return pairs


def _pass() -> None:
    n = 0
    for inst, tf in _pairs():
        bars = market_data.cached(inst, tf)
        if bars is None or len(bars) < 120 or _seen_bars.get((inst.symbol, tf)) == id(bars):
            continue
        _seen_bars[(inst.symbol, tf)] = id(bars)
        try:
            track(inst, tf, bars)
            n += 1
        except Exception:
            log.exception("training track %s %s failed", inst.symbol, tf)
    _progress["tracked"] += n
    _progress["lastPass"] = int(time.time())


def _group_pairs(market: str, tf: str) -> list[tuple[str, Bars]]:
    found = []
    for inst, t in _pairs():
        if t != tf or inst.market != market:
            continue
        bars = market_data.cached(inst, tf)
        if bars is not None and len(bars) >= 200:
            found.append((inst.symbol, bars))
    found.sort(key=lambda x: hashlib.sha1(x[0].encode()).hexdigest())  # a stable spread of the market
    return found[:GROUP_MAX_PAIRS]


def _tune_next() -> None:
    with _db_lock:
        last = dict(((s, tf), lt) for s, tf, lt in _db().execute("SELECT symbol, tf, last_tuned FROM tracking"))
    now = time.time()
    for market, tf in sorted({(i.market, t) for i, t in _pairs()}):  # group learning first: most assets inherit it
        if now - last.get((_group_key(market), tf), 0) < TUNE_EVERY:
            continue
        pairs = _group_pairs(market, tf)
        if len(pairs) < GROUP_MIN_PAIRS:
            continue
        _progress["tuning"] = f"grup {market} {tf} ({len(pairs)} aset)"
        try:
            train_group(market, tf, pairs)
        except Exception:
            log.exception("training group %s %s failed", market, tf)
        finally:
            _progress["tuning"] = None
        return
    for inst, tf in _pairs():  # core first, then the universe; pairs without a stored evaluation are due at once
        key = (inst.symbol, tf)
        if key not in last or (key in _evals and now - last[key] < TUNE_EVERY):
            continue
        bars = market_data.cached(inst, tf)
        if bars is None or len(bars) < 200:
            continue
        _progress["tuning"] = f"{inst.symbol} {tf}"
        try:
            tune_pair(inst, tf, bars)
            _progress["tunedThisRun"] += 1
        except Exception:
            log.exception("training tune %s %s failed", inst.symbol, tf)
        finally:
            _progress["tuning"] = None
        return


def _loop() -> None:
    time.sleep(90)
    last_pass = 0.0
    while True:
        if time.time() - last_pass >= 60:
            try:
                _pass()
            except Exception:
                log.exception("training pass failed")
            last_pass = time.time()
        try:
            _tune_next()
        except Exception:
            log.exception("training tuner failed")
        time.sleep(5)


_started = False


def start() -> None:
    global _started
    if _started:
        return
    _started = True
    with _db_lock:
        _db()  # load the upgraded parameters before the first analyses
    threading.Thread(target=_loop, daemon=True, name="training").start()
