"""SMC virtual trading ("AI training"): signal journal, paper trades, measured accuracy and self-tuning.

Strategy (causal — every decision uses only bars up to that moment):
  a structure break (CHoCH, optionally BOS) of the chosen scope creates a signal from the order block that
  produced it: limit entry at the block (edge or middle), stop beyond the block ± k·ATR, target = entry +
  RR × risk. Optional filters: entry in discount/premium of the dealing range known at that bar, an
  internal break only when it agrees with the swing trend, and SMC confluence — a liquidity sweep (the leg's
  extreme wicked through a known, untouched pivot and closed back) and/or a fair value gap in the leg that
  broke structure. Optional management: half the position is closed at +1R and the stop moves to entry.

Lifecycle of every signal (bar by bar, with timestamps):
  PENDING → OPEN when price trades into the entry, or CANCELLED when, before entry: the structure breaks the
  other way, price reaches the target first, price closes beyond the stop, or the signal expires.
  OPEN → WIN (target) / LOSS (stop; if a bar touches both, it is counted as a loss) / TIMEOUT (max holding,
  closed at the bar's close). With partial management, a trade that reached +1R and then returned to entry is
  a WIN of +0.5R; the moved stop applies from the bar after +1R. Result in R (risk units).

Live journal: signals created after an asset/timeframe starts being tracked are stored (SQLite, in the app
data folder) with the moment the system first saw them, and advanced as new bars arrive; closed rows are
frozen.

Training data: long history (market_data.HISTORY — 10 years of daily bars, 730 days of hourly / 4-hour bars,
60 days of 15-minute bars, at most 6000) with the fresher analysis bars as its tail, so every asset has several
times more out-of-sample trades than the chart's own window. Evaluations made on other data are redone first.

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

Precision modes (normal / ketat / sangat ketat) raise that bar: only settings whose proven lower bound reaches
the mode's threshold count. The lower bound is stamped on every live signal when it appears, so live results
per mode are never chosen after the outcome is known.

Qualification: a signal counts as an AI trade only when the setting behind it has evidence — enough
out-of-sample trades, a Wilson lower bound of the win rate ≥ QUALIFY_LOW and a positive expectancy. Other
signals are still traded virtually as shadow trades (to keep learning) but are reported separately, so
adding many unproven assets cannot silently dilute — or inflate — the headline numbers. The goal shown is
99 %, but only measured results are reported.
"""

from __future__ import annotations

import bisect
import hashlib
import itertools
import json
import logging
import math
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np

from . import candles, fundamental, instruments, market_data, newsctx, paths, smc
from .errors import EngineError
from .market_data import Bars

log = logging.getLogger("smc.training")

TARGET_WIN_RATE = 0.70  # first milestone shown in the UI
GOAL_WIN_RATE = 0.99
SPLIT = 0.70
MIN_IS_TRADES = 8
MIN_OOS_TRADES = 5
ADOPT_Z = 1.64  # adoption compares one-sided 90 % lower bounds of the win rate
MIN_IMPROVEMENT = 0.02
MIN_RR = 1.0  # settings with a reward filter (Params.min_rr) skip setups paying less than they risk
CORE_TFS = ("5m", "15m", "1h", "4h", "1d", "1w")  # trained per asset, each with its own setting and evidence
TF_ORDER = ("1m", "5m", "15m", "1h", "4h", "1d", "1w")
# Bars the background tuner learns from per timeframe; the training lab can ask for up to MAX_TRAIN_BARS.
DEFAULT_TRAIN_BARS = {"5m": 6000, "15m": 5000, "1h": 6000, "4h": 3000, "1d": 2600, "1w": 1000}
MAX_TRAIN_BARS = market_data.HISTORY_MAX_BARS
MTF_ALIGNED = 0.5  # higher-timeframe alignment (−1…+1, relative to the trade) from which a trade counts as aligned
UNIVERSE_TFS = ("1d",)
TUNE_EVERY = 6 * 3600
HISTORY_BASIS = 4  # evaluations made before the long history (1) or the BOS/CHoCH area rule (2, 3) are re-done first
PREFETCH = 40  # long histories downloaded together for the next pairs due
RETRY_AFTER = 30 * 60  # a group or pair whose tuning failed waits this long before the next attempt
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
    confluence: str = "none"  # extra SMC confirmation: "none" | "sweep" | "fvg" | "both"
    manage: str = "none"  # "none" | "partial" = close half at +1R and move the stop to entry
    mtf: str = "none"  # "none" | "align" = only trades the two higher timeframes' swing trends agree with
    # Field defaults keep the meaning of settings saved by earlier versions (order-block entry, one target);
    # default_params() starts new learning from the FVG rule below.
    zone: str = "ob"  # entry zone: "ob" = order block of the leg, "fvg" = the fair value gap inside the leg that broke
    ext: float = 0.618  # target "tp12": TP2 = TP1 + ext × the leg that broke structure
    min_rr: float = MIN_RR  # reward filter: setups paying less (TP1/TP2 blended) are not traded; 0 = every break

    def key(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @property
    def follows_rule(self) -> bool:
        """The SMC rule: every BOS / CHoCH of both scales traded from its FVG area with TP1 at the break's extreme and
        TP2 beyond. Settings of earlier versions skipped breaks (filters, one scale, minimum reward) or used other
        targets; they are no longer used."""
        return (self.scope == "both" and self.kinds == "all" and self.zone == "fvg" and self.target == "tp12"
                and self.min_rr <= 0 and not self.pd_filter and not self.trend_filter and self.confluence == "none"
                and self.mtf == "none")


def default_params(tf_key: str) -> Params:
    """Where learning starts — the SMC rule: every BOS / CHoCH (swing and internal) is a buy (bullish break) or sell
    (bearish) setup, none skipped: entry at the FVG inside the leg that broke structure (its order block when the leg
    has no open FVG), TP1 at that leg's extreme (the BOS / CHoCH high or low), TP2 beyond it; half closes at TP1 and the
    stop moves to entry. The tuner only tunes how (pivot lengths, entry in the area, stop, TP2, expiry)."""
    tf = market_data.timeframe(tf_key)
    return Params(swing=tf.swing, internal=tf.internal, scope="both", zone="fvg", target="tp12", trend_filter=False,
                  min_rr=0.0)



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
    manage: str = "none"
    opened: int | None = None
    closed: int | None = None
    exit: float | None = None
    r: float | None = None
    reason: str = ""
    align: float | None = None  # higher-timeframe alignment with this trade at its signal bar (−1…+1)
    tp1: float | None = None  # first target (target "tp12"): half closes here, then the stop moves to entry
    level: float | None = None  # the broken structure level and the pivot it came from (for the chart)
    pivot: int | None = None
    origin: float | None = None  # target "tp12": start of the leg; while pending, TP1 follows the leg's new extreme
    ext: float | None = None  # and TP2 stays `ext` × the leg beyond TP1
    zone_top: float | None = None  # the buy / sell area (FVG or order block) the entry sits in
    zone_bottom: float | None = None
    ict: dict = field(default_factory=dict)  # ICT checklist of the setup at its break (see ict_checks)


# ------------------------------------------------------------------ bars


def _with_tail(long: Bars, short: Bars | None, cap: int = MAX_TRAIN_BARS) -> Bars:
    """Long history with the fresher bars of the analysis cache in place of its end (same timeframe grid)."""
    if short is None or not len(short) or not len(long) or short.time[-1] <= long.time[-1]:
        return long
    cut = int(np.searchsorted(long.time, short.time[0]))
    start = max(0, cut + len(short) - cap)
    join = lambda a, b: np.concatenate([a[:cut], b])[start:]  # noqa: E731
    return Bars(join(long.time, short.time), join(long.open, short.open), join(long.high, short.high),
                join(long.low, short.low), join(long.close, short.close), join(long.volume, short.volume), short.fetched_at)


def _last(b: Bars | None, n: int) -> Bars | None:
    if b is None or len(b) <= n:
        return b
    return Bars(b.time[-n:], b.open[-n:], b.high[-n:], b.low[-n:], b.close[-n:], b.volume[-n:], b.fetched_at)


def history(inst: instruments.Instrument, tf: str, bars: int | None = None) -> Bars | None:
    """Bars the tuner learns from: the last `bars` (default DEFAULT_TRAIN_BARS) of the long history with a fresh tail,
    else the analysis bars."""
    got = market_data.history_many([inst], tf).get(inst.symbol)
    short = market_data.cached(inst, tf)
    full = _with_tail(got, short) if isinstance(got, Bars) else short
    return _last(full, bars or DEFAULT_TRAIN_BARS.get(tf, 6000))


def higher_tfs(tf: str) -> list[str]:
    """The two trained timeframes above `tf` (5m → 15m, 1h; 4h → 1d, 1w; 1w → none)."""
    if tf not in TF_ORDER:
        return []
    i = TF_ORDER.index(tf)
    return [t for t in TF_ORDER[i + 1:] if t in CORE_TFS][:2]


def htf_alignment(inst: instruments.Instrument, tf: str, bars: Bars, download: bool = False) -> np.ndarray | None:
    """Per bar of `bars`: the swing trend of the two higher timeframes as known when that bar closed (only higher
    bars already closed by then — no hindsight), weighted 0.6 / 0.4, from −1 (both bearish) to +1 (both bullish).
    None when no higher-timeframe bars are at hand (without `download`, only bars already held are used)."""
    close_t = bars.time + TF_SECONDS.get(tf, 3600)
    parts, weights = [], []
    for weight, htf in zip((0.6, 0.4), higher_tfs(tf)):
        hb = None
        if download or market_data.history_cached(inst, htf):
            got = market_data.history_many([inst], htf).get(inst.symbol)
            hb = got if isinstance(got, Bars) else None
        if hb is None:
            hb = market_data.cached(inst, htf)
        if hb is None or len(hb) < 60:
            continue
        d = default_params(htf)
        trend = smc.analyze(hb.open, hb.high, hb.low, hb.close, d.swing, d.internal).swing.trend_history
        j = np.searchsorted(hb.time + TF_SECONDS.get(htf, 86400), close_t, side="right") - 1
        parts.append(weight * np.where(j >= 0, trend[np.clip(j, 0, len(hb) - 1)], 0).astype(float))
        weights.append(weight)
    if not parts:
        return None
    return np.sum(parts, axis=0) / sum(weights)


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
        if s.ext is not None:  # the BOS / CHoCH leg is still running: its new high (low) becomes TP1
            peak = h[k] if d > 0 else l[k]
            if (peak - s.tp1) * d > 0:
                s.tp1 = float(peak)
                s.target = float(peak + d * s.ext * abs(peak - s.origin))
        elif (h[k] >= s.target) if d > 0 else (l[k] <= s.target):
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
    reward = abs(s.target - s.entry) / risk
    # half closes at TP1 (target "tp12") or at +1R ("partial"), then the stop moves to entry
    level = s.tp1 if s.tp1 is not None else s.entry + d * risk
    partial = s.tp1 is not None or (s.manage == "partial" and reward > 1.0 + 1e-9)
    half = abs(level - s.entry) / risk
    tp_name = "TP1" if s.tp1 is not None else "TP1 +1R"
    taken = False
    while k < end_hold:
        stop = s.entry if taken else s.stop
        hit_stop = l[k] <= stop if d > 0 else h[k] >= stop
        hit_target = (h[k] >= s.target if d > 0 else l[k] <= s.target) and k > s.opened
        if hit_stop:  # also when the target is touched in the same bar: conservative
            if taken:
                s.status, s.closed, s.exit, s.r, s.reason = "win", k, s.entry, round(0.5 * half, 3), f"{tp_name} (50%), sisa ditutup di entry"
            else:
                s.status, s.closed, s.exit, s.r, s.reason = "loss", k, s.stop, -1.0, "stop loss"
            return s
        if hit_target:
            r = 0.5 * half + 0.5 * reward if partial else reward
            s.status, s.closed, s.exit, s.r = "win", k, s.target, round(float(r), 3)
            s.reason = (f"{tp_name} (50%) lalu TP2" if s.tp1 is not None else f"{tp_name} (50%) lalu target") if partial else "target"
            return s
        if partial and not taken and k > s.opened and (h[k] >= level if d > 0 else l[k] <= level):
            taken = True  # the stop at entry applies from the next bar (order inside this bar is unknown)
        k += 1
    if n > s.opened + hold:
        k = s.opened + hold
        rest = float((c[k] - s.entry) * d / risk)  # plain floats: results are serialised as JSON
        s.status, s.closed, s.exit = "timeout", k, float(c[k])
        s.r, s.reason = round(0.5 * half + 0.5 * rest if taken else rest, 3), "batas waktu, ditutup di harga pasar"
    return s


def _swept(d: int, ob, t: int, l, h, c, pivots_low, pivots_high) -> bool:
    """Liquidity sweep: the extreme of the leg that broke structure wicked through a known, untouched pivot of
    the other side and closed back inside on that bar (sell-side grab before a bullish break, and vice versa)."""
    lo = max(0, ob.index - 3)
    if d > 0:
        j = lo + int(np.argmin(l[lo:t + 1]))
        for p in pivots_low:
            if p.confirmed > j or p.index >= j or j - p.index > 120:
                continue
            if l[j] < p.price < c[j] and (j == p.index + 1 or l[p.index + 1:j].min() >= p.price):
                return True
    else:
        j = lo + int(np.argmax(h[lo:t + 1]))
        for p in pivots_high:
            if p.confirmed > j or p.index >= j or j - p.index > 120:
                continue
            if h[j] > p.price > c[j] and (j == p.index + 1 or h[p.index + 1:j].max() <= p.price):
                return True
    return False


# ------------------------------------------------------------------ ICT checklist

DISPLACEMENT_ATR = 1.0  # a displacement candle: body in the break's direction of at least 1 × ATR
NEW_YORK = ZoneInfo("America/New_York")
KILLZONES = ((2 * 60, 5 * 60), (7 * 60, 10 * 60), (13 * 60 + 30, 16 * 60))  # London, NY AM, NY PM (New York minutes)
ICT_CORE = ("sweep", "displacement", "fvg", "zone")  # without these an ICT trader sees no setup


def _displaced(d: int, start: int, t: int, o, c, atr) -> bool:
    """Displacement: the leg that broke structure holds a full-bodied candle in its direction (body ≥ 1 × ATR)."""
    body = (c[start:t + 1] - o[start:t + 1]) * d
    return bool(np.any(body >= DISPLACEMENT_ATR * np.nan_to_num(atr[start:t + 1], nan=np.inf)))


def _in_killzone(t_open: int, step: int) -> bool | None:
    """ICT killzones (New York time, daylight saving included) for intraday bars; None above 1 hour."""
    if step > 3600:
        return None
    now = datetime.fromtimestamp(int(t_open) + step, NEW_YORK)  # the break is known when its bar closes
    minute = now.hour * 60 + now.minute
    return any(a < minute <= b for a, b in KILLZONES)


def ict_grade(checks: dict) -> str:
    """A+ = every ICT condition (core + higher-timeframe bias + killzone where they apply); A = the core model
    (sweep → displacement → MSS with FVG, entry in discount / premium); B = 3 of the 4 core; C = the rest."""
    core = sum(1 for k in ICT_CORE if checks.get(k))
    if core == len(ICT_CORE):
        return "A+" if checks.get("htf") is not False and checks.get("killzone") is not False else "A"
    return "B" if core == len(ICT_CORE) - 1 else "C"


def one_at_a_time(sigs: list[Signal], held: list[tuple[float, float]] = ()) -> list[Signal]:
    """One position at a time: a setup whose entry fills while another trade is still running (entered, not closed)
    is not opened — cancelled with "posisi lain masih berjalan"; its area stays on the chart. A fill on the bar where
    the running trade closes is blocked too (the order inside one bar is unknown). `held` = (opened, closed) bars of
    positions already taken that cannot change (journal rows). Modifies and returns `sigs`."""
    busy = -1.0  # the bar the running trade closes on (inf while it is still open)
    for s in sorted((x for x in sigs if x.opened is not None), key=lambda x: (x.opened, x.created)):
        if s.opened <= busy or any(a <= s.opened <= z for a, z in held):
            s.status, s.closed, s.reason = "cancelled", s.opened, "posisi lain masih berjalan"
            s.opened = s.exit = s.r = None
            continue
        busy = s.closed if s.closed is not None else math.inf
    return sigs


def signals(bars: Bars, p: Params, r: smc.SmcResult | None = None, align: np.ndarray | None = None,
            one_position: bool = True) -> list[Signal]:
    """Every BOS / CHoCH of the setting's structure becomes a buy (bullish break) or sell (bearish) setup with its
    area, stop and targets — with the rule (Params.follows_rule) none is skipped. The filters of earlier versions
    (one scale, CHoCH only, trend, premium/discount, minimum reward, confluence, higher timeframes) remain for
    research only. `align` (htf_alignment) is recorded on each signal. With `one_position` (default) a setup never
    opens while another trade is running (see one_at_a_time)."""
    o, h, l, c = bars.open, bars.high, bars.low, bars.close
    if r is None:
        r = smc.analyze(o, h, l, c, p.swing, p.internal)
    used = {"internal": ("internal",), "swing": ("swing",), "both": ("swing", "internal")}.get(p.scope, ("internal",))
    structures = [st for st in (r.swing, r.internal) if st.scope in used]
    blocks = {(ob.created, ob.direction, st.scope): ob for st in structures for ob in st.order_blocks}
    # cancel rule: a pending setup is dropped when its own structure (swing or internal) breaks the other way
    against = {(st.scope, d): set() for st in structures for d in (1, -1)}
    seen: set[tuple[int, int]] = set()
    breaks = []
    for st in structures:  # swing first: a bar broken on both scales is one setup, the swing break's
        for brk in st.breaks:
            against[(st.scope, -brk.direction)].add(brk.index)
            if (brk.index, brk.direction) not in seen:
                seen.add((brk.index, brk.direction))
                breaks.append(brk)
    breaks.sort(key=lambda x: x.index)
    hi, lo = _swing_range_at(r) if p.pd_filter else (None, None)
    pivots = r.internal.pivots + r.swing.pivots  # liquidity a sweep can take
    pivots_low = [q for q in pivots if q.kind == "low"]
    pivots_high = [q for q in pivots if q.kind == "high"]
    step = int(np.median(np.diff(bars.time[-60:]))) if len(bars) > 1 else 3600
    if p.confluence in ("fvg", "both"):
        gap_index = {1: sorted((g.index, g.created) for g in r.gaps if g.direction == 1),
                     -1: sorted((g.index, g.created) for g in r.gaps if g.direction == -1)}
    if p.zone == "fvg":
        gaps_by = {d: sorted((g for g in r.gaps if g.direction == d), key=lambda g: g.index) for d in (1, -1)}

    out: list[Signal] = []
    for b in breaks:
        t, d = b.index, b.direction
        ob = blocks.get((t, d, b.scope))
        if ob is None:  # degenerate origin candle: the leg's extreme candle is the area
            start = min(b.pivot_index, t)
            k = start + int(np.argmin(l[start:t + 1]) if d > 0 else np.argmax(h[start:t + 1]))
            ob = smc.OrderBlock(d, float(h[k]), float(l[k]), k, t, b.scope)
        leg_hi, leg_lo = float(h[ob.index:t + 1].max()), float(l[ob.index:t + 1].min())
        extreme, origin = (leg_hi, leg_lo) if d > 0 else (leg_lo, leg_hi)  # the BOS / CHoCH high (low) and its start
        atr_t = float(r.atr[t])
        if not math.isfinite(atr_t) or atr_t <= 0:
            atr_t = max(leg_hi - leg_lo, abs(float(c[t])) * 1e-4) * 0.1
        top, bottom = ob.top, ob.bottom
        if p.zone == "fvg":  # the latest fair value gap of the leg that broke, not yet filled when the break happened
            gap = next((g for g in reversed(gaps_by[d]) if ob.index <= g.index <= t and g.created <= t
                        and (g.filled is None or g.filled > t)), None)
            if gap is not None:
                top, bottom = gap.top, gap.bottom
            stop = origin - p.sl_atr * atr_t if d > 0 else origin + p.sl_atr * atr_t
        else:
            gap = None
            stop = bottom - p.sl_atr * atr_t if d > 0 else top + p.sl_atr * atr_t
        entry = (top + bottom) / 2 if p.entry == "mid" else (top if d > 0 else bottom)
        if (entry - stop) * d <= 0:
            stop = entry - d * p.sl_atr * atr_t
        risk = abs(entry - stop)
        tp1 = None
        if p.target == "liq":  # back to the extreme of the move that broke structure
            target = extreme
        elif p.target == "tp12":  # TP1 at the BOS / CHoCH extreme, TP2 beyond it
            tp1 = extreme if (extreme - entry) * d > 0 else entry + d * risk
            target = tp1 + d * p.ext * (leg_hi - leg_lo)
        else:
            target = entry + d * p.rr * risk
        first = tp1 if tp1 is not None else target
        reward = (0.5 * (tp1 - entry) * d + 0.5 * (target - entry) * d) / risk if tp1 is not None else (target - entry) * d / risk
        aligned = float(align[t]) * d if align is not None else None
        swept = _swept(d, ob, t, l, h, c, pivots_low, pivots_high)
        retrace = (extreme - entry) * d / (leg_hi - leg_lo) if leg_hi > leg_lo else 0.0  # 0 = at TP1, 1 = leg start
        ict = {"sweep": swept, "displacement": _displaced(d, ob.index, t, o, c, r.atr), "fvg": gap is not None,
               "zone": retrace >= 0.5, "ote": 0.62 <= retrace <= 0.79, "retrace": round(float(retrace), 3),
               "htf": None if aligned is None else aligned >= MTF_ALIGNED, "killzone": _in_killzone(bars.time[t], step)}
        ict["grade"] = ict_grade(ict)
        skipped = (  # filters of earlier versions; the rule has none
            (p.kinds == "CHoCH" and b.kind != "CHoCH")
            or (p.trend_filter and b.scope == "internal" and r.swing.trend_history[t] != d)
            or (p.pd_filter and _outside_zone(hi[t], lo[t], entry, d))
            or reward < p.min_rr
            or (tp1 is None and (first - c[t]) * d <= 0)  # one fixed target already behind price
            or (p.confluence in ("sweep", "both") and not swept)
            or (p.confluence in ("fvg", "both") and not _gap_in_leg(gap_index[d], ob.index, t))
            or (p.mtf == "align" and (aligned is None or aligned < MTF_ALIGNED)))
        if skipped:
            continue
        s = Signal(d, b.kind, b.scope, t, float(entry), float(stop), float(target), manage="tp12" if tp1 is not None else p.manage,
                   align=aligned, level=float(b.level), pivot=b.pivot_index, zone_top=float(top), zone_bottom=float(bottom),
                   ict=ict)
        if tp1 is not None:
            s.tp1, s.origin, s.ext = float(tp1), float(origin), float(p.ext)
        out.append(simulate(bars, s, against[(b.scope, d)], p.expiry, p.hold))
    return one_at_a_time(out) if one_position else out


def _outside_zone(top: float, bottom: float, entry: float, d: int) -> bool:
    """Premium/discount filter: a buy must sit in discount, a sell in premium (no range yet: outside)."""
    if math.isnan(top) or math.isnan(bottom) or top <= bottom:
        return True
    mid = (top + bottom) / 2
    return entry > mid if d > 0 else entry < mid


def _gap_in_leg(gaps: list, start: int, t: int) -> bool:
    k = bisect.bisect_left(gaps, (start, -1))  # first gap of this direction from the leg's start on
    return k < len(gaps) and gaps[k][1] <= t


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
        "profitFactor": round(float(gross_win / gross_loss), 2) if gross_loss else (None if not gross_win else 99.0),
        "maxDrawdownR": round(drawdown, 2),
    }


GRADES = ("A+", "A", "B", "C")


def grade_metrics(items: list) -> dict:
    """Results per ICT grade (signals or journal / chart rows), plus "ICT" = the full model (A+ and A)."""
    def grade(x):
        return (x.ict if isinstance(x, Signal) else x.get("ict") or {}).get("grade")

    def closed(xs):
        rs = [(x.r if isinstance(x, Signal) else x["r"]) or 0.0 for x in xs
              if (x.status if isinstance(x, Signal) else x["status"]) in ("win", "loss", "timeout")]
        wins = sum(1 for v in rs if v > 0)
        return {"trades": len(rs), "wins": wins, "winRate": round(wins / len(rs), 4) if rs else None,
                "expectancyR": round(float(np.mean(rs)), 3) if rs else None}

    out = {g: closed([x for x in items if grade(x) == g]) for g in GRADES}
    out["ICT"] = closed([x for x in items if grade(x) in ("A+", "A")])
    return out


def wilson_low(wins: int, n: int, z: float = 1.0) -> float:
    """Lower bound of the win rate (Wilson interval): small samples cannot look better than they are."""
    if n == 0:
        return 0.0
    p = wins / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (centre - margin) / denom


def walk_forward(bars: Bars, p: Params, r: smc.SmcResult | None = None, align: np.ndarray | None = None) -> dict:
    sigs = signals(bars, p, r, align)
    split_t = bars.time[0] + (bars.time[-1] - bars.time[0]) * SPLIT
    ins = [s for s in sigs if bars.time[s.created] < split_t]
    oos = [s for s in sigs if bars.time[s.created] >= split_t]
    return {"inSample": metrics(ins), "outOfSample": metrics(oos), "all": metrics(sigs)}


_breaths = itertools.count()


def _breathe() -> None:
    """Long searches run in a background thread; a short pause every 25 evaluations lets the engine's web server
    (same process, same interpreter lock) keep answering the app's requests."""
    if next(_breaths) % 25 == 0:
        time.sleep(0.002)


MAX_LEVEL = 2  # search levels: 0 = base grid; while a pair stays below its goal its next search widens


def _grid(tf_key: str, level: int = 0):
    """Settings the tuner tries — all follow the rule (every BOS / CHoCH traded from its FVG area, TP1 at the break's
    extreme, TP2 beyond); only how is tuned: pivot lengths, entry in the area, stop distance, TP2 distance, expiry.
    Level 1 adds more stops and TP2 distances and the area's edge; level 2 other pivot lengths and a shorter expiry
    (54 → 360 → 900 settings)."""
    base = default_params(tf_key)
    lengths = {(base.swing, base.internal), (max(4, base.swing - 4), base.internal), (base.swing + 4, base.internal + 1)}
    stops, exts, entries, expiries = (0.25, 0.5, 1.0), (0.382, 0.618, 1.0), ("mid",), (base.expiry, base.expiry * 2)
    if level >= 1:
        stops, exts, entries = (0.1, 0.25, 0.5, 0.75, 1.0, 1.5), (0.272, 0.382, 0.618, 1.0, 1.618), ("mid", "edge")
    if level >= 2:
        lengths |= {(max(3, base.swing - 7), max(2, base.internal - 1)), (base.swing + 8, base.internal + 2)}
        expiries += (max(5, base.expiry // 2),)
    for (swing, internal), entry, sl_atr, ext, expiry in itertools.product(sorted(lengths), entries, stops, exts, expiries):
        yield replace(base, swing=swing, internal=internal, entry=entry, sl_atr=sl_atr, ext=ext, expiry=expiry)


def grid_size(tf_key: str, level: int = 0) -> int:
    return sum(1 for _ in _grid(tf_key, level))


def _score(m: dict, min_trades: int) -> float:
    if m["trades"] < min_trades or (m["expectancyR"] or 0) <= 0:
        return -1.0
    return wilson_low(m["wins"], m["trades"], ADOPT_Z)


def tune(bars: Bars, tf_key: str, current: Params, align: np.ndarray | None = None, level: int = 0) -> tuple[Params, dict, dict]:
    """(best params, its walk-forward report, the current params' report); `level` widens the search (see _grid)."""
    level = max(0, min(MAX_LEVEL, level))
    analyses: dict[tuple[int, int], smc.SmcResult] = {}

    def analysis_for(p: Params) -> smc.SmcResult:
        key = (p.swing, p.internal)
        if key not in analyses:
            analyses[key] = smc.analyze(bars.open, bars.high, bars.low, bars.close, p.swing, p.internal)
        return analyses[key]

    current_report = walk_forward(bars, current, analysis_for(current), align)

    def evaluate(p: Params):
        _breathe()
        report = walk_forward(bars, p, analysis_for(p), align)
        return _score(report["inSample"], MIN_IS_TRADES), p, report

    ranked = sorted((evaluate(p) for p in _grid(tf_key, level)), key=lambda x: x[0], reverse=True)
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
# Precision modes: a qualified setting must also prove at least this lower bound (z = 1) of its win rate.
PRECISION_MODES = (("normal", "NORMAL", QUALIFY_LOW), ("ketat", "KETAT", 0.65), ("sangat", "SANGAT KETAT", 0.80))


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
        _breathe()
        train, valid, test = [], [], []
        for sym, bars in pairs:
            key = (sym, p.swing, p.internal)
            if key not in analyses:
                analyses[key] = smc.analyze(bars.open, bars.high, bars.low, bars.close, p.swing, p.internal)
            a, b, c = _parts(bars, signals(bars, p, analyses[key]))
            train += a
            valid += b
            test += c
        return {"train": metrics(train), "valid": metrics(valid), "test": metrics(test), "pairs": len(pairs),
                "basis": HISTORY_BASIS}

    def evaluate(p: Params):
        report = pooled(p)
        return _score(report["train"], GROUP_MIN_TRAIN), p, report

    current_report = pooled(current)
    current_report["basis"] = HISTORY_BASIS
    ranked = sorted((evaluate(p) for p in _grid(tf_key)), key=lambda x: x[0], reverse=True)
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
            -- the trade dictionary: every closed trade of each pair's active setting on the history it was trained on,
            -- with its context; rows outside the latest history window are kept, so it only grows
            CREATE TABLE IF NOT EXISTS patterns (
                symbol TEXT, market TEXT, tf TEXT, created_t INTEGER, direction INTEGER, kind TEXT, scope TEXT,
                status TEXT, r REAL, sample TEXT, align REAL, version INTEGER, recorded INTEGER,
                PRIMARY KEY (symbol, tf, created_t, direction));
            CREATE INDEX IF NOT EXISTS patterns_market ON patterns(market, tf, sample);
        """)
        # additive migrations of journals written by earlier versions
        if "qualified" not in _columns(_conn, "signals"):
            _conn.execute("ALTER TABLE signals ADD COLUMN qualified INTEGER")
        for column, kind in (("quality", "REAL"), ("manage", "TEXT"), ("align", "REAL"), ("news", "REAL"), ("tp1", "REAL"),
                             ("origin", "REAL"), ("ext", "REAL"), ("zone_top", "REAL"), ("zone_bottom", "REAL"),
                             ("ict", "TEXT")):
            if column not in _columns(_conn, "signals"):
                _conn.execute(f"ALTER TABLE signals ADD COLUMN {column} {kind}")
        if "level" not in _columns(_conn, "tracking"):
            _conn.execute("ALTER TABLE tracking ADD COLUMN level INTEGER DEFAULT 0")
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
            if not entry[1].follows_rule:
                continue  # an earlier version's setting (skipped breaks / other targets): the rule replaces it
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
                  "stop", "target", "expiry_t", "status", "opened_t", "closed_t", "exit", "r", "reason", "updated", "qualified",
                  "quality", "manage", "align", "news", "tp1", "origin", "ext", "zone_top", "zone_bottom", "ict"]


def _sid(symbol: str, tf: str, s: Signal, t: int) -> str:
    return f"{symbol}|{tf}|{t}|{s.direction}|{s.scope}"


def track(inst: instruments.Instrument, tf: str, bars: Bars) -> int:
    """Record / advance live signals of one asset & timeframe. Returns the number of rows written.

    A signal is "qualified" when, at the moment it appears, the active parameters of this pair have proven an
    edge out of sample (see `evidence`); other signals are still recorded as shadow trades for learning.
    """
    now = int(time.time())
    _, version, p = active(inst.symbol, tf)
    ev = evidence(inst.symbol, tf)
    qualified_now = 1 if ev["qualified"] else 0
    quality_now = ev["winRateLow"] if ev["qualified"] else 0.0  # precision modes compare this, stamped before the outcome
    sigs = signals(bars, p, None, htf_alignment(inst, tf, bars), one_position=False)
    news = newsctx.score(inst.symbol)
    tf_sec = TF_SECONDS.get(tf, 3600)
    index_of = {int(t): i for i, t in enumerate(bars.time)}
    written = 0
    insert = (f"INSERT OR REPLACE INTO signals ({', '.join(SIGNAL_COLUMNS)}) VALUES "
              f"({', '.join(':' + c for c in SIGNAL_COLUMNS)})")
    with _db_lock:
        db = _db()
        since = _since(inst.symbol, tf, now)
        existing = {row[0]: row for row in db.execute(
            "SELECT id, status, first_seen, version, direction, entry, stop, target, created_t, qualified, quality, manage, align, news, "
            "tp1, origin, ext, opened_t, closed_t "
            "FROM signals "
            "WHERE symbol=? AND tf=? AND created_t >= ?", (inst.symbol, tf, since - tf_sec * (p.expiry + p.hold)))}
        # signals of an older parameter version keep their stored levels and are advanced on their own; with the
        # positions they already took (closed rows are facts) they share the one-position rule with the new setting
        olds, held = [], []
        bar = lambda t: int(np.searchsorted(bars.time, t))  # noqa: E731
        for (sid, status, first_seen, ver, direction, entry, stop, target, created_t, _q, _quality, manage, _a, _n,
             tp1, origin, ext, opened_t, closed_t) in existing.values():
            if ver == version:
                continue
            if status in ("pending", "open") and created_t in index_of:
                olds.append((sid, status, first_seen, ver, created_t, simulate(bars, Signal(
                    direction, "", "", index_of[created_t], entry, stop, target, manage=manage or "none", tp1=tp1,
                    origin=origin if tp1 is not None else None, ext=ext if tp1 is not None and origin is not None else None),
                    set(), p.expiry, p.hold)))
            elif opened_t is not None and status in ("win", "loss", "timeout"):
                held.append((bar(opened_t), bar(closed_t) if closed_t is not None else math.inf))
        one_at_a_time(sigs + [o[-1] for o in olds], held)
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
            row["quality"] = old[10] if old and old[10] is not None else quality_now
            if old and old[11]:
                row["manage"] = old[11]
            # context stamped once, when the signal appears: higher-timeframe alignment and the news agreement
            row["align"] = old[12] if old and old[12] is not None else s.align
            row["news"] = old[13] if old and old[13] is not None else (round(news["score"] * s.direction, 3) if news else None)
            db.execute(insert, {**row, "id": sid, "ict": json.dumps(row["ict"])})
            written += 1
        for sid, status, first_seen, ver, created_t, s in olds:
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
            "updated": int(time.time()), "manage": s.manage, "align": s.align, "tp1": s.tp1, "origin": s.origin, "ext": s.ext,
            "zone_top": s.zone_top, "zone_bottom": s.zone_bottom, "ict": s.ict, "fromT": t(s.pivot), "level": s.level}


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


def _record_patterns(inst: instruments.Instrument, tf: str, bars: Bars, p: Params, version: int,
                     align: np.ndarray | None) -> int:
    """Store the closed trades of setting `p` on `bars` in the trade dictionary (replacing that window only)."""
    sigs = signals(bars, p, None, align)
    split_t = bars.time[0] + (bars.time[-1] - bars.time[0]) * SPLIT
    now = int(time.time())
    rows = [(inst.symbol, inst.market, tf, int(bars.time[s.created]), s.direction, s.kind, s.scope, s.status, s.r,
             "oos" if bars.time[s.created] >= split_t else "is", s.align, version, now)
            for s in sigs if s.status in ("win", "loss", "timeout")]
    with _db_lock:
        db = _db()
        db.execute("DELETE FROM patterns WHERE symbol=? AND tf=? AND created_t>=?", (inst.symbol, tf, int(bars.time[0])))
        db.executemany("INSERT OR REPLACE INTO patterns VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        db.commit()
    return len(rows)


def search_level(symbol: str, tf: str) -> int:
    with _db_lock:
        row = _db().execute("SELECT level FROM tracking WHERE symbol=? AND tf=?", (symbol, tf)).fetchone()
    return int(row[0] or 0) if row else 0


def at_goal(ev: dict) -> bool:
    return bool(ev.get("qualified")) and (ev.get("winRate") or 0) >= GOAL_WIN_RATE


def tune_pair(inst: instruments.Instrument, tf: str, bars: Bars, align: np.ndarray | None = None,
              level: int | None = None) -> dict | None:
    """Give an asset its own parameters only when they beat its inherited ones (group / default) out of sample;
    drop an own setting that the inherited one now beats. Every evaluation is kept for the accuracy board, and the
    active setting's trades go to the trade dictionary.

    The SMC setting is never final: while a core pair stays below the goal and this round found nothing better, its
    next search is wider (`search_level`, up to MAX_LEVEL); a better setting still has to pass the same out-of-sample
    test, so a wider search cannot buy a win rate by fitting noise."""
    level = search_level(inst.symbol, tf) if level is None else max(0, min(MAX_LEVEL, level))
    source, version, current = active(inst.symbol, tf)
    best, best_report, current_report = tune(bars, tf, current, align, level)
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
                inherited = walk_forward(bars, group[1], None, align)
                if _score(inherited["outOfSample"], MIN_OOS_TRADES) > _score(current_report["outOfSample"], MIN_OOS_TRADES) + MIN_IMPROVEMENT:
                    _save_params(inst.symbol, tf, {"inherit": True}, inherited, now)
                    _params.pop((inst.symbol, tf), None)
                    report = inherited
        report = {**report, "basis": HISTORY_BASIS, "bars": len(bars)}
        _evals[(inst.symbol, tf)] = report
        db.execute("UPDATE tracking SET last_tuned=?, report=? WHERE symbol=? AND tf=?",
                   (now, json.dumps(report), inst.symbol, tf))
        db.commit()
    _, now_version, now_params = active(inst.symbol, tf)
    _record_patterns(inst, tf, bars, now_params, now_version, align)
    if getattr(inst, "core", False) and not upgrade and not at_goal(evidence(inst.symbol, tf)) and level < MAX_LEVEL:
        with _db_lock:  # still short of the goal and nothing better at this width: search wider next time
            _db().execute("UPDATE tracking SET level=? WHERE symbol=? AND tf=?", (level + 1, inst.symbol, tf))
            _db().commit()
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
    out = [dict(zip(SIGNAL_COLUMNS, r)) for r in rows]
    for row in out:
        row["ict"] = json.loads(row["ict"]) if row["ict"] else {}
    return out


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


# ------------------------------------------------------------------ multi-timeframe evidence (trade dictionary)

TF_WEIGHT = {"5m": 1.0, "15m": 1.0, "1h": 1.5, "4h": 2.0, "1d": 2.5, "1w": 3.0}
EXPERT = {"key": "expert", "label": "EXPERT", "mtf": True}


def _rows_metrics(rows: list[tuple]) -> dict:
    """rows: (r, ...) of closed trades."""
    rs = [float(r[0] or 0) for r in rows]
    wins = sum(1 for x in rs if x > 0)
    n = len(rs)
    return {"trades": n, "wins": wins, "winRate": round(wins / n, 4) if n else None,
            "winRateLow": round(wilson_low(wins, n), 4) if n else None,
            "expectancyR": round(sum(rs) / n, 3) if n else None, "totalR": round(sum(rs), 2)}


def _bucket(align) -> str:
    if align is None:
        return "unknown"
    return "aligned" if align >= MTF_ALIGNED else "against" if align <= -MTF_ALIGNED else "mixed"


def mtf_stats(symbol: str | None = None) -> dict:
    """Out-of-sample trades of the dictionary by higher-timeframe alignment: how much more often trades win when the
    two higher timeframes agree (the multi-timeframe percentage), overall and per timeframe."""
    where, args = "WHERE sample='oos'", ()
    if symbol:
        where, args = where + " AND symbol=?", (symbol,)
    with _db_lock:
        rows = _db().execute(f"SELECT r, align, tf FROM patterns {where}", args).fetchall()
    buckets: dict[str, list] = {}
    per_tf: dict[str, dict[str, list]] = {}
    for r, align, tf in rows:
        b = _bucket(align)
        buckets.setdefault(b, []).append((r,))
        per_tf.setdefault(tf, {}).setdefault(b, []).append((r,))
    return {"all": _rows_metrics([(r,) for r, _, _ in rows]),
            "buckets": {b: _rows_metrics(v) for b, v in buckets.items()},
            "tfs": {tf: {b: _rows_metrics(v) for b, v in bs.items()} for tf, bs in sorted(per_tf.items(), key=lambda kv: TF_ORDER.index(kv[0]) if kv[0] in TF_ORDER else 99)}}


def _expert_backtest(qualified: set[tuple[str, str]]) -> dict:
    with _db_lock:
        rows = _db().execute("SELECT r, symbol, tf FROM patterns WHERE sample='oos' AND align>=?", (MTF_ALIGNED,)).fetchall()
    keep = [row for row in rows if (row[1], row[2]) in qualified]
    return {**_rows_metrics(keep), "pairs": len({(row[1], row[2]) for row in keep})}


def consensus(inst: instruments.Instrument) -> dict:
    """Direction across the trained timeframes right now: each timeframe's swing trend (its active pivot lengths),
    weighted by timeframe (weekly 3 … 5-minute 1). Signals stay per timeframe; this shows which side dominates."""
    tfs, up, down = [], 0.0, 0.0
    for tf in CORE_TFS:
        try:
            bars = market_data.cached(inst, tf) or market_data.load(inst, tf)
        except Exception:  # a timeframe without data must not hide the others
            continue
        if bars is None or len(bars) < 60:
            continue
        _, _, p = active(inst.symbol, tf)
        r = smc.analyze(bars.open, bars.high, bars.low, bars.close, p.swing, p.internal)
        trend, w = int(r.swing.trend), TF_WEIGHT.get(tf, 1.0)
        up += w if trend > 0 else 0.0
        down += w if trend < 0 else 0.0
        tfs.append({"tf": tf, "trend": trend, "internal": int(r.internal.trend), "weight": w})
    total = up + down
    share = up / total if total else 0.5
    return {"up": round(100 * share) if total else None, "down": round(100 * (1 - share)) if total else None,
            "dominant": "naik" if total and share >= 0.6 else "turun" if total and share <= 0.4 else "netral", "tfs": tfs}


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
        levels = {(s, tf): lv or 0 for s, tf, lv in _db().execute("SELECT symbol, tf, level FROM tracking")}
    closed = sorted((r for r in rows if r["status"] in ("win", "loss", "timeout") and r["qualified"]), key=lambda r: r["closed_t"] or 0)
    equity, total = [], 0.0
    for r in closed:
        total += r["r"] or 0
        equity.append([r["closed_t"], round(total, 2)])
    board, by_source, by_market, qualified_oos = [], {}, {}, []
    by_mode: dict[str, list] = {key: [] for key, _, _ in PRECISION_MODES}
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
            for key, _, low in PRECISION_MODES:
                if (ev["winRateLow"] or 0) >= low:
                    by_mode[key].append(oos)
        source, version, _ = active(symbol, tf)
        board.append({"symbol": symbol, "tf": tf, "market": market, "version": version, "source": source, "level": levels.get((symbol, tf), 0),
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
        "modes": [{"key": key, "label": label, "low": low, "backtest": _sum(by_mode[key]),
                   "live": _live_metrics([r for r in rows if r["qualified"] and (r["quality"] is None and low <= QUALIFY_LOW
                                                                                 or (r["quality"] or 0) >= low)])}
                  for key, label, low in PRECISION_MODES]
                 + [{**EXPERT, "low": QUALIFY_LOW,
                     "backtest": _expert_backtest({(b["symbol"], b["tf"]) for b in board if b["qualified"]}),
                     "live": _live_metrics([r for r in rows if r["qualified"] and (r["align"] or -1) >= MTF_ALIGNED])}],
        "mtf": mtf_stats(),
        "lab": lab_status(),
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
        "activity": _activity([r for r in rows if r["status"] != "cancelled"]),
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
        for s in signals(bars, p, None, htf_alignment(inst, tf, bars))[-5:][::-1]:
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
            "target": TARGET_WIN_RATE, "goal": GOAL_WIN_RATE,
            "modes": [{"key": key, "label": label, "low": low} for key, label, low in PRECISION_MODES] + [{**EXPERT, "low": QUALIFY_LOW}],
            "consensus": consensus(inst), "mtf": mtf_stats(inst.symbol), "news": newsctx.score(inst.symbol),
            "trigger1m": _safe(lambda: candles.trigger(inst)), "calendar": _safe(lambda: fundamental.upcoming_for(inst, _fundamentals()))}


def _fundamentals() -> dict | None:
    from . import outlook  # imported here: outlook → analysis → training would otherwise be an import cycle

    return outlook.fundamentals()


def _safe(fn):
    """Optional context (1-minute trigger, calendar): missing data must not fail the PLAN card."""
    try:
        return fn()
    except Exception as exc:
        log.debug("optional context unavailable: %s", exc)
        return None


def chart(symbol: str, tf: str, bars_wanted: int | None = None) -> dict:
    """Live training chart of one pair: the bars the tuner learns from (long history with a fresh tail), the
    structure the active setting sees, and its virtual trades — the journal's live rows replacing their
    re-simulated twins. Times are bar opens (UTC seconds)."""
    inst = instruments.resolve(symbol)
    source, version, p = active(inst.symbol, tf)
    bars = history(inst, tf, max(200, min(MAX_TRAIN_BARS, bars_wanted)) if bars_wanted else None)
    if bars is None or len(bars) < 50:
        bars = market_data.load(inst, tf)
    tf_sec = TF_SECONDS.get(tf, 3600)
    t = bars.time
    t0, t1 = int(t[0]), int(t[-1])
    split_t = int(t0 + (t1 - t0) * SPLIT)
    r = smc.analyze(bars.open, bars.high, bars.low, bars.close, p.swing, p.internal)
    live = _live_rows("WHERE symbol=? AND tf=? AND created_t >= ?", (inst.symbol, tf, t0))
    replaced = {(row["created_t"], row["direction"]) for row in live}
    sigs = signals(bars, p, r, htf_alignment(inst, tf, bars, download=True))
    trades = []
    for s in sigs:
        created_t = int(t[s.created])
        if (created_t, s.direction) not in replaced:
            trades.append({**_row(inst, tf, version, s, bars, created_t, None, p, tf_sec), "live": False,
                           "sample": "oos" if created_t >= split_t else "is"})
    trades += [{**row, "live": True, "sample": "live"} for row in live]
    trades.sort(key=lambda row: row["created_t"])
    word = {1: "bullish", -1: "bearish"}
    return {
        "instrument": inst.to_json(), "tf": tf, "symbol": inst.symbol, "version": version, "source": source,
        "params": asdict(p), "evidence": evidence(inst.symbol, tf),
        "modes": [{"key": key, "label": label, "low": low} for key, label, low in PRECISION_MODES],
        "split": split_t, "outOfSample": metrics([s for s in sigs if t[s.created] >= split_t]),
        "live": _live_metrics(live), "trades": trades, "grades": grade_metrics(sigs),
        "candles": [{"t": int(t[i]), "o": float(bars.open[i]), "h": float(bars.high[i]), "l": float(bars.low[i]),
                     "c": float(bars.close[i])} for i in range(len(bars))],
        "smc": {"breaks": [{"kind": b.kind, "direction": word[b.direction], "level": b.level, "scope": b.scope,
                            "fromT": int(t[b.pivot_index]), "toT": int(t[b.index])}
                           for st in (r.swing, r.internal) for b in st.breaks],
                "pivots": [], "orderBlocks": [], "fvgs": [], "equalLevels": [], "liquidity": {"above": [], "below": []}},
        "ema": {"ema50": [], "ema200": []}, "plan": None,
    }


def _activity(rows: list[dict], limit: int = 20) -> list[dict]:
    """Latest virtual-trading events (signal seen, entry, exit) of the live journal, newest first."""
    events = []
    for r in rows:
        for t, what in ((r["closed_t"] if r["status"] in ("win", "loss", "timeout") else None, r["status"]),
                        (r["opened_t"], "open"), (r["first_seen"], "signal")):
            if t:
                events.append({"t": t, "event": what, "symbol": r["symbol"], "tf": r["tf"], "market": r["market"]})
                break  # the latest stage of each signal
    events.sort(key=lambda e: e["t"], reverse=True)
    return events[:limit]


def precision_level(ev: dict) -> str | None:
    """Highest precision mode a pair's evidence reaches (None = shadow / not proven)."""
    if not ev.get("qualified"):
        return None
    return next((label for _, label, low in reversed(PRECISION_MODES) if (ev.get("winRateLow") or 0) >= low), None)


def current_setup(inst: instruments.Instrument, tf: str, direction: int) -> dict | None:
    """The pending or open virtual-trading setup of a pair in `direction`: the live journal's newest, else the newest
    signal of the active setting on the analysis bars; with the evidence and precision level behind it."""
    if tf not in TF_SECONDS or not direction:
        return None
    source, version, p = active(inst.symbol, tf)
    rows = _live_rows("WHERE symbol=? AND tf=? AND direction=? AND status IN ('pending', 'open') ORDER BY created_t DESC LIMIT 1",
                      (inst.symbol, tf, direction))
    if rows:
        r = rows[0]
        setup = {"entry": r["entry"], "stop": r["stop"], "target": r["target"], "tp1": r["tp1"], "status": r["status"],
                 "createdT": r["created_t"], "manage": r["manage"] or "none", "live": True}
    else:
        bars = market_data.cached(inst, tf)
        if bars is None or len(bars) < 50:
            return None
        sig = next((s for s in reversed(signals(bars, p, None, htf_alignment(inst, tf, bars)))
                    if s.direction == direction and s.status in ("pending", "open")), None)
        if sig is None:
            return None
        setup = {"entry": sig.entry, "stop": sig.stop, "target": sig.target, "tp1": sig.tp1, "status": sig.status,
                 "createdT": int(bars.time[sig.created]), "manage": sig.manage, "live": False}
    risk = abs(setup["entry"] - setup["stop"])
    ev = evidence(inst.symbol, tf)
    return {**setup, "rr": round(abs(setup["target"] - setup["entry"]) / risk, 2) if risk else None,
            "source": f"virtual trading v{version}", "paramSource": source, "evidence": ev, "level": precision_level(ev)}


def cancel_rules(p: Params, tf: str) -> list[str]:
    span = TF_SECONDS.get(tf, 3600) * p.expiry
    hours = span / 3600
    return [
        "struktur berbalik (CHoCH/BOS berlawanan di skala yang sama) sebelum harga menyentuh entry",
        "TP1 ikut puncak baru leg BOS/CHoCH selama belum entry (setup tidak dibatalkan)" if p.target == "tp12" else
        "harga mencapai target sebelum entry (peluang terlewat)",
        "harga close menembus stop loss sebelum entry (zona tidak valid)",
        "harga menyentuh area saat posisi lain di aset & timeframe ini masih berjalan (satu posisi sekaligus)",
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

_progress = {"tracked": 0, "lastPass": None, "tuning": None, "tunedThisRun": 0, "lastTuned": None}
_seen_bars: dict[tuple[str, str], int] = {}
_failed: dict[tuple[str, str], float] = {}  # (symbol or @market, tf) -> time of the last failed tuning


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


def _group_candidates(market: str, tf: str, pairs) -> list[tuple[instruments.Instrument, Bars]]:
    """Assets of one market & timeframe with enough analysis bars, a stable spread of at most GROUP_MAX_PAIRS."""
    chosen = []
    for inst, t in pairs:
        if t == tf and inst.market == market:
            bars = market_data.cached(inst, tf)
            if bars is not None and len(bars) >= 200:
                chosen.append((inst, bars))
    chosen.sort(key=lambda x: hashlib.sha1(x[0].symbol.encode()).hexdigest())
    return chosen[:GROUP_MAX_PAIRS]


def _group_pairs(chosen: list[tuple[instruments.Instrument, Bars]], tf: str) -> list[tuple[str, Bars]]:
    """Long history of the chosen assets (downloaded in batches), each with the fresh tail of its analysis bars."""
    got = market_data.history_many([inst for inst, _ in chosen], tf)
    n = DEFAULT_TRAIN_BARS.get(tf, 6000)
    return [(inst.symbol, _last(_with_tail(got[inst.symbol], short) if isinstance(got.get(inst.symbol), Bars) else short, n))
            for inst, short in chosen]


def _due(key: tuple[str, str], last: dict, report: dict | None, now: float) -> bool:
    if now - _failed.get(key, 0) < RETRY_AFTER:
        return False
    return (report or {}).get("basis") != HISTORY_BASIS or now - last.get(key, 0) >= TUNE_EVERY


def _tune_next() -> None:
    if _lab.get("state") == "running":
        return  # the training lab has the CPU
    from . import session  # imported here: session imports this module

    if session.running():
        return  # so has a live training session
    with _db_lock:
        last = dict(((s, tf), lt) for s, tf, lt in _db().execute("SELECT symbol, tf, last_tuned FROM tracking"))
    now = time.time()
    pairs = _pairs()
    for market, tf in sorted({(i.market, t) for i, t in pairs}):  # group learning first: most assets inherit it
        group = _groups.get((market, tf))
        if not _due((_group_key(market), tf), last, group[2] if group else None, now):
            continue
        chosen = _group_candidates(market, tf, pairs)
        if len(chosen) < GROUP_MIN_PAIRS:
            continue  # not enough assets with data yet: never blocks the per-asset tuning below
        _progress["tuning"] = f"grup {market} {tf} ({len(chosen)} aset)"
        try:
            train_group(market, tf, _group_pairs(chosen, tf))
        except Exception:
            _failed[(_group_key(market), tf)] = time.time()
            log.exception("training group %s %s failed", market, tf)
        finally:
            _progress["tuning"] = None
        return
    # core first, then the universe; pairs evaluated on older data (or never) are due at once
    due = [(i, t) for i, t in pairs if (i.symbol, t) in last and _due((i.symbol, t), last, _evals.get((i.symbol, t)), now)]
    for k, (inst, tf) in enumerate(due):
        short = market_data.cached(inst, tf)
        if short is None or len(short) < 200:
            continue
        if not market_data.history_cached(inst, tf):
            ahead = [i for i, t in due[k:k + 4 * PREFETCH] if t == tf][:PREFETCH]
            _progress["tuning"] = f"unduh riwayat panjang {tf} ({len(ahead)} aset)"
            market_data.history_many(ahead, tf)
        bars = history(inst, tf)
        if bars is None or len(bars) < 200:
            continue
        _progress["tuning"] = f"{inst.symbol} {tf}"
        try:
            tune_pair(inst, tf, bars, htf_alignment(inst, tf, bars))
            _progress["tunedThisRun"] += 1
            _progress["lastTuned"] = {"symbol": inst.symbol, "tf": tf, "market": inst.market, "at": int(time.time())}
        except Exception:
            _failed[(inst.symbol, tf)] = time.time()
            log.exception("training tune %s %s failed", inst.symbol, tf)
        finally:
            _progress["tuning"] = None
        return


# ------------------------------------------------------------------ training lab


MARKETS = ("forex", "saham-as", "saham-idx", "kripto")
LAB_MAX_ASSETS = 150  # assets per market & timeframe (core watchlist first, then a stable spread of the universe)


class LabBusy(EngineError):
    status = 409
    code = "lab_busy"


class LabInvalid(EngineError):
    status = 400
    code = "invalid_argument"


_lab_lock = threading.Lock()
_lab: dict = {"state": "idle"}
_lab_stop = threading.Event()


def lab_plan(markets: list[str], tfs: list[str]) -> list[tuple[str, str, list[instruments.Instrument]]]:
    core = list(instruments.watchlist())
    names = {i.symbol for i in core}
    universe = sorted((i for i in instruments.universe() if i.symbol not in names),
                      key=lambda i: hashlib.sha1(i.symbol.encode()).hexdigest())
    plan = []
    for market in markets:
        for tf in tfs:
            insts = [i for i in core if i.market == market]
            if tf in UNIVERSE_TFS + ("1w",):
                insts += [i for i in universe if i.market == market][:max(0, LAB_MAX_ASSETS - len(insts))]
            if insts:
                plan.append((market, tf, insts[:LAB_MAX_ASSETS]))
    return plan


def lab_status() -> dict:
    with _lab_lock:
        out = json.loads(json.dumps(_lab, default=str))
    total = out.get("total") or 0
    out["progressPct"] = round(100 * (out.get("done") or 0) / total, 1) if total else None
    out["markets_all"], out["tfs_all"], out["maxBars"] = list(MARKETS), list(CORE_TFS), MAX_TRAIN_BARS
    out["defaultBars"] = DEFAULT_TRAIN_BARS
    return out


def _lab_set(**kw) -> None:
    with _lab_lock:
        _lab.update(kw)


def lab_start(markets: list[str], tfs: list[str], bars: int) -> dict:
    """Train the chosen markets × timeframes on the last `bars` candles, at full CPU speed, in the background."""
    markets = [m for m in MARKETS if m in set(markets or [])]
    tfs = [t for t in CORE_TFS if t in set(tfs or [])]
    if not markets or not tfs:
        raise LabInvalid("Pilih minimal satu pasar dan satu timeframe.")
    try:
        bars = int(bars)
    except (TypeError, ValueError):
        raise LabInvalid("Jumlah candle harus angka.") from None
    if not 200 <= bars <= MAX_TRAIN_BARS:
        raise LabInvalid(f"Jumlah candle 200 – {MAX_TRAIN_BARS}.")
    with _lab_lock:
        if _lab.get("state") == "running":
            raise LabBusy("Training lab masih berjalan; hentikan dulu.")
        from . import session

        if session.running():
            raise LabBusy("Training live masih berjalan; hentikan dulu.")
        plan = lab_plan(markets, tfs)
        _lab.clear()
        _lab.update({"state": "running", "markets": markets, "tfs": tfs, "bars": bars, "total": sum(len(p[2]) for p in plan),
                     "done": 0, "skipped": 0, "current": None, "trades": 0, "wins": 0, "totalR": 0.0, "upgrades": 0,
                     "groups": [], "startedAt": int(time.time()), "finishedAt": None, "error": None})
        _lab_stop.clear()
    threading.Thread(target=_lab_run, args=(plan, bars), daemon=True, name="training-lab").start()
    return lab_status()


def lab_stop() -> dict:
    _lab_stop.set()
    return lab_status()


def _lab_run(plan, bars: int) -> None:
    state = "done"
    try:
        for market, tf, insts in plan:
            if _lab_stop.is_set():
                break
            _lab_set(current={"market": market, "tf": tf, "symbol": None, "stage": f"unduh riwayat {len(insts)} aset"})
            got = market_data.history_many(insts, tf)
            pairs = []
            for inst in insts:
                long, short = got.get(inst.symbol), market_data.cached(inst, tf)
                full = _last(_with_tail(long, short) if isinstance(long, Bars) else short, bars)
                if full is not None and len(full) >= 200:
                    pairs.append((inst, full))
            with _lab_lock:
                _lab["skipped"] += len(insts) - len(pairs)
                _lab["done"] += len(insts) - len(pairs)
                row = {"market": market, "tf": tf, "assets": len(pairs), "bars": int(np.median([len(b) for _, b in pairs])) if pairs else 0,
                       "trades": 0, "wins": 0, "totalR": 0.0, "upgrades": 0, "aligned": None}
                _lab["groups"].append(row)
            if len(pairs) >= GROUP_MIN_PAIRS and not _lab_stop.is_set():
                _lab_set(current={"market": market, "tf": tf, "symbol": None, "stage": f"grup {len(pairs)} aset"})
                train_group(market, tf, [(inst.symbol, b) for inst, b in pairs])
            for inst, b in pairs:
                if _lab_stop.is_set():
                    break
                _lab_set(current={"market": market, "tf": tf, "symbol": inst.symbol, "stage": "tuning", "bars": len(b)})
                upgrade = tune_pair(inst, tf, b, htf_alignment(inst, tf, b, download=True))
                oos = (_evals.get((inst.symbol, tf)) or {}).get("outOfSample") or {}
                with _lab_lock:
                    row["trades"] += oos.get("trades", 0) or 0
                    row["wins"] += oos.get("wins", 0) or 0
                    row["totalR"] = round(row["totalR"] + (oos.get("totalR") or 0), 2)
                    row["upgrades"] += 1 if upgrade else 0
                    _lab["trades"] += oos.get("trades", 0) or 0
                    _lab["wins"] += oos.get("wins", 0) or 0
                    _lab["totalR"] = round(_lab["totalR"] + (oos.get("totalR") or 0), 2)
                    _lab["upgrades"] += 1 if upgrade else 0
                    _lab["done"] += 1
            with _lab_lock:
                row["winRate"] = round(row["wins"] / row["trades"], 4) if row["trades"] else None
        if _lab_stop.is_set():
            state = "stopped"
    except Exception as exc:  # reported in the status; the engine keeps running
        log.exception("training lab failed")
        state = "error"
        _lab_set(error=str(getattr(exc, "message", exc))[:200])
    finally:
        _lab_set(state=state, current=None, finishedAt=int(time.time()))


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
