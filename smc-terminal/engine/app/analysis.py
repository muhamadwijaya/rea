"""Bias scoring, narrative, trade scenario and JSON projection of an SMC run."""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass

import numpy as np

from . import market_data, smc
from .indicators import ema, rsi
from .instruments import Instrument
from .market_data import Bars, Timeframe

DIRECTIONS = (
    (50, "strong_bullish", "NAIK KUAT"),
    (20, "bullish", "NAIK"),
    (-20, "neutral", "SIDEWAYS"),
    (-50, "bearish", "TURUN"),
    (-101, "strong_bearish", "TURUN KUAT"),
)

TREND_WORD = {1: "BULLISH", -1: "BEARISH", 0: "BELUM TERBENTUK"}
DIR_WORD = {1: "bullish", -1: "bearish"}


def direction_of(score: float) -> tuple[str, str]:
    for floor, key, label in DIRECTIONS:
        if score >= floor:
            return key, label
    return DIRECTIONS[-1][1], DIRECTIONS[-1][2]


def _num(value) -> float | None:
    if value is None:
        return None
    value = float(value)
    return None if math.isnan(value) or math.isinf(value) else value


@dataclass
class Component:
    key: str
    name: str
    value: float
    max: float
    note: str

    def to_json(self) -> dict:
        return {"key": self.key, "name": self.name, "value": round(self.value, 1), "max": self.max, "note": self.note}


@dataclass
class Analysis:
    instrument: Instrument
    tf: Timeframe
    bars: Bars
    result: smc.SmcResult
    ema50: np.ndarray
    ema200: np.ndarray
    rsi14: np.ndarray
    score: float
    components: list[Component]

    @property
    def price(self) -> float:
        return float(self.bars.close[-1])

    @property
    def last_index(self) -> int:
        return len(self.bars) - 1

    def fmt(self, value: float | None) -> str:
        if value is None:
            return "-"
        decimals = self.instrument.price_decimals(value)
        return f"{value:,.{decimals}f}"


# --------------------------------------------------------------------------- scoring


def _score(bars: Bars, r: smc.SmcResult, e50, e200, rsi14) -> tuple[float, list[Component]]:
    c = bars.close
    n = len(c)
    price = float(c[-1])
    comps: list[Component] = []

    comps.append(Component("swing", "Struktur swing", 30.0 * r.swing.trend, 30,
                           f"Tren swing {TREND_WORD[r.swing.trend]}"))
    comps.append(Component("internal", "Struktur internal", 15.0 * r.internal.trend, 15,
                           f"Tren internal {TREND_WORD[r.internal.trend]}"))

    recent = 0.0
    note = "Tidak ada CHoCH baru"
    for b in reversed(r.internal.breaks + r.swing.breaks):
        if b.kind == "CHoCH" and n - 1 - b.index <= 12:
            weight = 15.0 if b.scope == "swing" else 10.0
            recent = weight * b.direction
            note = f"CHoCH {DIR_WORD[b.direction]} ({b.scope}) {n - 1 - b.index} bar lalu"
            break
    comps.append(Component("choch", "Perubahan karakter", recent, 15, note))

    ema_score, ema_note = 0.0, "EMA belum cukup data"
    if not math.isnan(e50[-1]):
        if math.isnan(e200[-1]):
            ema_score = 8.0 if price > e50[-1] else -8.0
            ema_note = "Harga di atas EMA50" if price > e50[-1] else "Harga di bawah EMA50"
        elif price > e50[-1] > e200[-1]:
            ema_score, ema_note = 15.0, "Harga > EMA50 > EMA200 (tren besar naik)"
        elif price < e50[-1] < e200[-1]:
            ema_score, ema_note = -15.0, "Harga < EMA50 < EMA200 (tren besar turun)"
        else:
            ema_score = 6.0 if price > e50[-1] else -6.0
            ema_note = "EMA bercampur, harga " + ("di atas" if price > e50[-1] else "di bawah") + " EMA50"
    comps.append(Component("ema", "EMA 50/200", ema_score, 15, ema_note))

    zone_score, zone_note = 0.0, "Range belum terbentuk"
    if r.zones:
        # Discount favours longs, premium favours shorts.
        zone_score = float(np.clip((0.5 - r.zones.position) * 20.0, -10, 10))
        zone_note = f"Harga di {r.zones.area.upper()} ({r.zones.position:.0%} dari range)"
    comps.append(Component("zone", "Premium / discount", zone_score, 10, zone_note))

    rsi_now = rsi14[-1]
    mom = 0.0 if math.isnan(rsi_now) else float(np.clip((rsi_now - 50.0) / 50.0 * 10.0, -10, 10))
    comps.append(Component("momentum", "Momentum RSI", mom, 10,
                           "RSI belum cukup data" if math.isnan(rsi_now) else f"RSI(14) {rsi_now:.1f}"))

    ob_score, ob_note = 0.0, "Harga tidak di dalam order block"
    for ob in reversed(r.swing.order_blocks + r.internal.order_blocks):
        if ob.active and ob.bottom <= price <= ob.top:
            ob_score = 5.0 * ob.direction
            ob_note = f"Harga di dalam OB {DIR_WORD[ob.direction]} ({ob.scope})"
            break
    comps.append(Component("ob", "Reaksi order block", ob_score, 5, ob_note))

    total = float(np.clip(sum(x.value for x in comps), -100, 100))
    return total, comps


def run(inst: Instrument, tf_key: str) -> Analysis:
    tf = market_data.timeframe(tf_key)
    bars = market_data.load(inst, tf.key)
    return run_on_bars(inst, tf, bars)


# Analyses are pure functions of (instrument, timeframe, downloaded bars), so a
# result is reused for as long as the same Bars object is served by the cache.
_memo: dict[tuple[str, str], Analysis] = {}
_memo_lock = threading.Lock()


def run_on_bars(inst: Instrument, tf: Timeframe, bars: Bars) -> Analysis:
    key = (inst.yahoo, tf.key)
    with _memo_lock:
        hit = _memo.get(key)
    if hit is not None and hit.bars is bars and hit.instrument == inst:
        return hit
    r = smc.analyze(bars.open, bars.high, bars.low, bars.close, tf.swing, tf.internal)
    e50, e200, rsi14 = ema(bars.close, 50), ema(bars.close, 200), rsi(bars.close, 14)
    score, comps = _score(bars, r, e50, e200, rsi14)
    result = Analysis(inst, tf, bars, r, e50, e200, rsi14, score, comps)
    with _memo_lock:
        _memo[key] = result
    return result


# --------------------------------------------------------------------------- scenario


def _active_blocks(a: Analysis, direction: int) -> list[smc.OrderBlock]:
    blocks = [ob for ob in a.result.swing.order_blocks + a.result.internal.order_blocks
              if ob.active and ob.direction == direction]
    seen: set[tuple[float, float]] = set()
    unique = []
    for ob in blocks:
        key = (round(ob.top, 8), round(ob.bottom, 8))
        if key not in seen:
            seen.add(key)
            unique.append(ob)
    return unique


def _open_gaps(a: Analysis, direction: int) -> list[smc.Gap]:
    return [g for g in a.result.gaps if g.filled is None and g.direction == direction]


def trade_plan(a: Analysis) -> dict | None:
    key, _ = direction_of(a.score)
    if key == "neutral":
        return None
    d = smc.BULL if a.score > 0 else smc.BEAR
    price = a.price
    atr_now = float(a.result.atr[-1])

    zones: list[tuple[float, float, str]] = []
    for ob in _active_blocks(a, d):
        if (d == smc.BULL and ob.bottom <= price) or (d == smc.BEAR and ob.top >= price):
            zones.append((ob.top, ob.bottom, f"Order block {ob.scope}"))
    for g in _open_gaps(a, d):
        if (d == smc.BULL and g.bottom <= price) or (d == smc.BEAR and g.top >= price):
            zones.append((g.top, g.bottom, "Fair value gap"))
    if not zones:
        return {
            "direction": DIR_WORD[d],
            "status": "wait",
            "note": "Bias " + DIR_WORD[d] + " tetapi belum ada zona OB/FVG yang valid di arah pullback. "
                    "Tunggu harga kembali ke " + ("discount" if d == smc.BULL else "premium") +
                    " atau terbentuk order block baru.",
        }

    # Nearest zone to price in the pullback direction.
    zones.sort(key=lambda z: (price - z[0]) if d == smc.BULL else (z[1] - price))
    top, bottom, source = zones[0]
    inside = bottom <= price <= top
    if d == smc.BULL:
        entry = min(price, (top + bottom) / 2.0)
        stop = bottom - 0.3 * atr_now
        targets = [lvl for lvl, _, _ in _dedupe_levels(a.result.liquidity_above) if lvl > entry]
        if a.result.zones and a.result.zones.top > entry and not targets:
            targets.append(a.result.zones.top)
    else:
        entry = max(price, (top + bottom) / 2.0)
        stop = top + 0.3 * atr_now
        targets = [lvl for lvl, _, _ in _dedupe_levels(a.result.liquidity_below) if lvl < entry]
        if a.result.zones and a.result.zones.bottom < entry and not targets:
            targets.append(a.result.zones.bottom)

    risk = abs(entry - stop)
    tps = targets[:2]
    rr = [round(abs(tp - entry) / risk, 2) for tp in tps] if risk > 0 else []
    return {
        "direction": DIR_WORD[d],
        "status": "active" if inside else "pending",
        "source": source,
        "zone": {"top": top, "bottom": bottom},
        "entry": entry,
        "stop": stop,
        "targets": tps,
        "rr": rr,
        "note": ("Harga sedang di dalam zona; cari konfirmasi CHoCH internal di timeframe lebih kecil."
                 if inside else "Pasang skenario limit di zona; batal bila struktur berbalik sebelum tersentuh."),
    }


def _dedupe_levels(levels: list[tuple[float, int, str]]) -> list[tuple[float, int, str]]:
    out: list[tuple[float, int, str]] = []
    for lvl in levels:
        if not out or abs(out[-1][0] - lvl[0]) > 1e-9:
            out.append(lvl)
    return out


# --------------------------------------------------------------------------- narrative


def narrative(a: Analysis, plan: dict | None) -> list[str]:
    r = a.result
    n = len(a.bars)
    tf = a.tf.label
    lines: list[str] = []

    last = r.swing.last_break()
    if last:
        lines.append(
            f"Struktur swing {tf} {TREND_WORD[r.swing.trend]}: {last.kind} {DIR_WORD[last.direction]} "
            f"menembus {a.fmt(last.level)} {n - 1 - last.index} bar lalu."
        )
    else:
        lines.append(f"Struktur swing {tf} belum memiliki break yang jelas.")

    ilast = r.internal.last_break()
    if ilast:
        agree = "searah" if r.internal.trend == r.swing.trend else "berlawanan (pullback/koreksi)"
        lines.append(
            f"Struktur internal {TREND_WORD[r.internal.trend]} ({agree} dengan swing); terakhir "
            f"{ilast.kind} {DIR_WORD[ilast.direction]} di {a.fmt(ilast.level)}."
        )

    if r.zones:
        z = r.zones
        lines.append(
            f"Harga {a.fmt(a.price)} berada di zona {z.area.upper()} ({z.position:.0%}) dari dealing range "
            f"{a.fmt(z.bottom)} – {a.fmt(z.top)}; equilibrium {a.fmt(z.equilibrium)}."
        )
        weak = "high" if z.strong == "low" else "low"
        lines.append(
            f"Strong {z.strong} di {a.fmt(z.bottom if z.strong == 'low' else z.top)} (dijaga), "
            f"weak {weak} di {a.fmt(z.top if weak == 'high' else z.bottom)} (target likuiditas)."
        )

    for d in (smc.BULL, smc.BEAR):
        blocks = _active_blocks(a, d)
        if blocks:
            ob = min(blocks, key=lambda b: abs((b.top + b.bottom) / 2 - a.price))
            dist = ((ob.top + ob.bottom) / 2 - a.price) / a.price * 100
            lines.append(
                f"Order block {DIR_WORD[d]} aktif terdekat {a.fmt(ob.bottom)} – {a.fmt(ob.top)} "
                f"({dist:+.2f}% dari harga){', sudah disentuh' if ob.tapped is not None else ''}."
            )

    bull_gaps, bear_gaps = _open_gaps(a, smc.BULL), _open_gaps(a, smc.BEAR)
    if bull_gaps or bear_gaps:
        lines.append(f"FVG terbuka: {len(bull_gaps)} bullish, {len(bear_gaps)} bearish (imbalance yang cenderung diisi).")

    above = _dedupe_levels(r.liquidity_above)[:2]
    below = _dedupe_levels(r.liquidity_below)[:2]
    if above or below:
        parts = []
        if above:
            parts.append("buy-side di " + ", ".join(a.fmt(x[0]) for x in above))
        if below:
            parts.append("sell-side di " + ", ".join(a.fmt(x[0]) for x in below))
        lines.append("Likuiditas: " + "; ".join(parts) + ".")

    key, label = direction_of(a.score)
    conclusion = f"Kesimpulan {tf}: bias {label} (skor {a.score:+.0f}/100)."
    if plan and plan.get("status") in ("active", "pending"):
        conclusion += (
            f" Skenario {plan['direction']}: zona {a.fmt(plan['zone']['bottom'])} – {a.fmt(plan['zone']['top'])}, "
            f"invalidasi {a.fmt(plan['stop'])}."
        )
    elif key == "neutral":
        conclusion += " Tunggu CHoCH/BOS yang jelas sebelum mengambil posisi."
    lines.append(conclusion)
    return lines


# --------------------------------------------------------------------------- projection


def _t(a: Analysis, index: int, first: int) -> int:
    return int(a.bars.time[max(index, first)])


def to_json(a: Analysis, bars_out: int = 500, include_series: bool = True) -> dict:
    r = a.result
    n = len(a.bars)
    first = max(0, n - bars_out)
    t = a.bars.time

    def visible(index: int | None) -> bool:
        return index is not None and index >= first

    out: dict = {
        "instrument": a.instrument.to_json(),
        "tf": a.tf.key,
        "tfLabel": a.tf.label,
        "asOf": int(t[-1]),
        "fetchedAt": int(a.bars.fetched_at),
        "generatedAt": int(time.time()),
        "price": a.price,
        "source": "Yahoo Finance (yfinance) — bisa tertunda tergantung bursa",
    }
    key, label = direction_of(a.score)
    out["bias"] = {
        "score": round(a.score, 1),
        "direction": key,
        "label": label,
        "components": [c.to_json() for c in a.components],
    }
    out["indicators"] = {
        "ema50": _num(a.ema50[-1]),
        "ema200": _num(a.ema200[-1]),
        "rsi": _num(a.rsi14[-1]),
        "atr": _num(r.atr[-1]),
    }

    smc_json: dict = {
        "swingTrend": DIR_WORD.get(r.swing.trend, "neutral"),
        "internalTrend": DIR_WORD.get(r.internal.trend, "neutral"),
        "pivots": [
            {"t": int(t[p.index]), "price": p.price, "kind": p.kind, "label": p.label, "scope": s.scope}
            for s in (r.swing, r.internal) for p in s.pivots
            if visible(p.index) and p.confirmed < n and (s.scope == "swing" or p.label)
        ],
        "breaks": [
            {"kind": b.kind, "direction": DIR_WORD[b.direction], "level": b.level, "scope": b.scope,
             "fromT": _t(a, b.pivot_index, first), "toT": int(t[b.index]), "barsAgo": n - 1 - b.index}
            for s in (r.swing, r.internal) for b in s.breaks if visible(b.index)
        ],
        "orderBlocks": [
            {"direction": DIR_WORD[ob.direction], "top": ob.top, "bottom": ob.bottom, "scope": ob.scope,
             "t": _t(a, ob.index, first), "createdT": int(t[ob.created]),
             "tappedT": int(t[ob.tapped]) if ob.tapped is not None else None,
             "brokenT": int(t[ob.broken]) if ob.broken is not None else None,
             "active": ob.active}
            for ob in _visible_blocks(r, first, a.price)
        ],
        "fvgs": [
            {"direction": DIR_WORD[g.direction], "top": g.top, "bottom": g.bottom, "t": _t(a, g.index, first),
             "filledT": int(t[g.filled]) if g.filled is not None else None, "fill": round(g.fill, 3)}
            for g in _visible_gaps(r, first)
        ],
        "equalLevels": [
            {"kind": e.kind, "price": e.price, "fromT": _t(a, e.first, first), "toT": int(t[e.second]),
             "sweptT": int(t[e.swept]) if e.swept is not None else None}
            for e in r.equal_levels if visible(e.second)
        ][-8:],
        "liquidity": {
            "above": [{"price": p, "t": _t(a, i, first), "kind": k} for p, i, k in _dedupe_levels(r.liquidity_above)[:4]],
            "below": [{"price": p, "t": _t(a, i, first), "kind": k} for p, i, k in _dedupe_levels(r.liquidity_below)[:4]],
        },
    }
    if r.zones:
        z = r.zones
        smc_json["zones"] = {
            "top": z.top, "bottom": z.bottom, "equilibrium": z.equilibrium, "position": round(z.position, 4),
            "area": z.area, "strong": z.strong, "topT": _t(a, z.top_index, first), "bottomT": _t(a, z.bottom_index, first),
        }
    out["smc"] = smc_json

    plan = trade_plan(a)
    out["plan"] = plan
    out["narrative"] = narrative(a, plan)

    if include_series:
        b = a.bars
        out["candles"] = [
            {"t": int(b.time[i]), "o": float(b.open[i]), "h": float(b.high[i]), "l": float(b.low[i]),
             "c": float(b.close[i]), "v": float(b.volume[i])}
            for i in range(first, n)
        ]
        out["ema"] = {
            "ema50": [{"t": int(t[i]), "v": float(a.ema50[i])} for i in range(first, n) if not math.isnan(a.ema50[i])],
            "ema200": [{"t": int(t[i]), "v": float(a.ema200[i])} for i in range(first, n) if not math.isnan(a.ema200[i])],
        }
    return out


def _visible_blocks(r: smc.SmcResult, first: int, price: float) -> list[smc.OrderBlock]:
    """Active blocks nearest to price (2 per scope and direction) plus the latest invalidated ones."""
    blocks = r.swing.order_blocks + r.internal.order_blocks
    broken = [ob for ob in blocks if not ob.active and ob.created >= first][-4:]
    keep: list[smc.OrderBlock] = []
    for scope in ("swing", "internal"):
        for d in (smc.BULL, smc.BEAR):
            active = [ob for ob in blocks if ob.active and ob.scope == scope and ob.direction == d]
            active.sort(key=lambda ob: abs((ob.top + ob.bottom) / 2 - price))
            keep += active[:2]
    return keep + broken


def _visible_gaps(r: smc.SmcResult, first: int) -> list[smc.Gap]:
    open_gaps = [g for g in r.gaps if g.filled is None and g.created >= first]
    recent_filled = [g for g in r.gaps if g.filled is not None and g.created >= first][-4:]
    keep: list[smc.Gap] = []
    for d in (smc.BULL, smc.BEAR):
        keep += [g for g in open_gaps if g.direction == d][-5:]
    return keep + recent_filled


def summary(a: Analysis) -> dict:
    """Compact per-timeframe view used by MTF tables and the screener."""
    r = a.result
    n = len(a.bars)
    key, label = direction_of(a.score)
    last = None
    for b in sorted(r.swing.breaks + r.internal.breaks, key=lambda x: x.index)[-1:]:
        last = {"kind": b.kind, "direction": DIR_WORD[b.direction], "scope": b.scope, "level": b.level,
                "barsAgo": n - 1 - b.index, "t": int(a.bars.time[b.index])}
    price = a.price
    atr_now = float(r.atr[-1])
    nearest_ob = None
    blocks = _active_blocks(a, smc.BULL) + _active_blocks(a, smc.BEAR)
    if blocks:
        ob = min(blocks, key=lambda x: abs((x.top + x.bottom) / 2 - price))
        nearest_ob = {"direction": DIR_WORD[ob.direction], "top": ob.top, "bottom": ob.bottom,
                      "distancePct": round(((ob.top + ob.bottom) / 2 - price) / price * 100, 3),
                      "inside": ob.bottom <= price <= ob.top}
    return {
        "tf": a.tf.key,
        "score": round(a.score, 1),
        "direction": key,
        "label": label,
        "swingTrend": DIR_WORD.get(r.swing.trend, "neutral"),
        "internalTrend": DIR_WORD.get(r.internal.trend, "neutral"),
        "lastEvent": last,
        "zone": {"area": r.zones.area, "position": round(r.zones.position, 4)} if r.zones else None,
        "nearestOb": nearest_ob,
        "openFvg": {"bullish": len(_open_gaps(a, smc.BULL)), "bearish": len(_open_gaps(a, smc.BEAR))},
        "activeOb": {"bullish": len(_active_blocks(a, smc.BULL)), "bearish": len(_active_blocks(a, smc.BEAR))},
        # Zones within 3 ATR of price are the ones that can matter for the next moves.
        "nearby": {
            "ob": sum(1 for d in (smc.BULL, smc.BEAR) for ob in _active_blocks(a, d) if _near(ob.top, ob.bottom, price, atr_now)),
            "fvg": sum(1 for d in (smc.BULL, smc.BEAR) for g in _open_gaps(a, d) if _near(g.top, g.bottom, price, atr_now)),
        },
        "rsi": _num(a.rsi14[-1]),
        "asOf": int(a.bars.time[-1]),
    }


def _near(top: float, bottom: float, price: float, atr_now: float, k: float = 3.0) -> bool:
    return bottom - k * atr_now <= price <= top + k * atr_now


MTF_WEIGHTS = {"1d": 0.45, "4h": 0.35, "1h": 0.20}


def mtf(inst: Instrument, tfs: tuple[str, ...] = ("1d", "4h", "1h")) -> dict:
    rows = []
    total = weight = 0.0
    for key in tfs:
        try:
            s = summary(run(inst, key))
        except Exception as exc:  # one failing timeframe must not hide the others
            rows.append({"tf": key, "error": str(getattr(exc, "message", exc))})
            continue
        rows.append(s)
        w = MTF_WEIGHTS.get(key, 0.2)
        total += s["score"] * w
        weight += w
    combined = total / weight if weight else 0.0
    signs = {np.sign(r["score"]) for r in rows if "score" in r and abs(r["score"]) >= 20}
    aligned = len(signs) == 1 and sum(1 for r in rows if "score" in r and abs(r["score"]) >= 20) == len(
        [r for r in rows if "score" in r])
    key, label = direction_of(combined)
    return {
        "timeframes": rows,
        "score": round(combined, 1),
        "direction": key,
        "label": label,
        "aligned": bool(aligned),
        "note": "Semua timeframe searah — konfluensi kuat." if aligned else
                "Timeframe tidak sepenuhnya searah — kurangi ukuran posisi / tunggu konfirmasi.",
    }
