"""Upcoming dividends and other holder benefits for every market, with a "hype" read of each event.

Sources (no event is invented; every row says where it came from):
- Stocks / ETFs: dividend history from the daily price downloads (market_data), the announced ex-dividend
  and payment dates from Yahoo's company calendar (fetched slowly in the background, cached on disk), and a
  projection from the payment rhythm when nothing is announced yet (marked "perkiraan").
- Forex: central-bank rate decisions of both currencies from the economic calendar.
- Crypto: halving countdowns from the chains' block height (BTC, LTC, BCH).
- Every asset: benefit / corporate-action headlines (dividend, buyback, split, rights, RUPS/AGM, airdrop,
  staking, token unlock, burn, listing, upgrade, rate decision) recorded as they are scored.

Hype = how much attention the event gets now: volume of the last 5 days vs 20 days, related headlines in 7
days and the 5-day price move.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone

import numpy as np

from . import instruments, market_data, outlook, paths

log = logging.getLogger("smc.benefits")

DAY = 86400
NEWS_DAYS = 14
CALENDAR_REFRESH = DAY
CALENDAR_PER_PASS = 400
HALVING_REFRESH = 1800

# (label, pattern) — first match wins; patterns are case-insensitive and cover EN + ID wording.
BENEFIT_KINDS: list[tuple[str, re.Pattern]] = [(label, re.compile(p, re.I)) for label, p in [
    ("DIVIDEN", r"\bdividen|\bdividend|\bcum date|\bex[- ]date|\bex[- ]dividend|\bpayout\b"),
    ("BUYBACK", r"\bbuy ?back|\bshare repurchase|\bpembelian kembali saham"),
    ("STOCK SPLIT", r"\bstock split|\bpemecahan saham|\breverse split"),
    ("RIGHTS / BONUS", r"\brights issue|\bhmetd\b|\bbonus share|\bsaham bonus|\bprivate placement|\bwaran\b"),
    ("RUPS / AGM", r"\brups(lb)?\b|\bannual general meeting|\bagm\b|\bshareholder meeting"),
    ("AIRDROP", r"\bairdrop"),
    ("STAKING", r"\bstaking\b|\bstake rewards?|\byield farming"),
    ("TOKEN UNLOCK", r"\btoken unlock|\bunlocks?\b.*\btokens?\b|\bvesting"),
    ("HALVING", r"\bhalving"),
    ("BURN", r"\btoken burn|\bburns?\b.*\btokens?\b|\bburn mechanism"),
    ("LISTING", r"\blisting\b|\blisted on\b|\bto list\b|\bspot etf approv"),
    ("UPGRADE", r"\bmainnet\b|\bhard fork\b|\bnetwork upgrade\b"),
    ("SUKU BUNGA", r"\brate decision|\brate (cut|hike)s?\b|\bsuku bunga|\bbi[- ]rate\b|\bfomc\b|\binterest rates?\b"),
]]
# Which benefit kinds mean something for holders of each market.
KINDS_BY_MARKET = {
    "saham-as": {"DIVIDEN", "BUYBACK", "STOCK SPLIT", "RIGHTS / BONUS", "RUPS / AGM"},
    "saham-idx": {"DIVIDEN", "BUYBACK", "STOCK SPLIT", "RIGHTS / BONUS", "RUPS / AGM"},
    "kripto": {"AIRDROP", "STAKING", "TOKEN UNLOCK", "HALVING", "BURN", "LISTING", "UPGRADE"},
    "forex": {"SUKU BUNGA"},
}
RATE_EVENT = re.compile(r"(interest rate|policy rate|cash rate|funds rate|refinancing rate|bank rate|overnight rate|"
                        r"rate statement|monetary policy statement|deposit rate)", re.I)
HALVINGS = {  # symbol: (block-height API, blocks between halvings, minutes per block)
    "BTCUSD": ("https://mempool.space/api/blocks/tip/height", 210_000, 10.0),
    "LTCUSD": ("https://litecoinspace.org/api/blocks/tip/height", 840_000, 2.5),
    "BCHUSD": ("https://api.blockchair.com/bitcoin-cash/stats", 210_000, 10.0),
}


@dataclass
class _State:
    news: dict[str, list[dict]]
    calendar: dict[str, dict]  # yahoo -> {"exDate", "payDate", "fetchedAt"}
    halvings: dict[str, dict]
    calendar_pass: dict


_state = _State(news={}, calendar={}, halvings={}, calendar_pass={"done": 0, "total": 0, "finished": None})
_lock = threading.Lock()


def _store_path():
    return paths.data_dir() / "benefits.json"


def _load_disk() -> None:
    try:
        data = json.loads(_store_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    with _lock:
        _state.calendar = data.get("calendar", {})
        _state.news = {k: v for k, v in data.get("news", {}).items() if isinstance(v, list)}


def _save_disk() -> None:
    with _lock:
        payload = {"calendar": _state.calendar, "news": _state.news, "savedAt": int(time.time())}
    try:
        tmp = _store_path().with_suffix(".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(_store_path())
    except OSError as exc:
        log.warning("benefits cache not saved: %s", exc)


# ------------------------------------------------------------------ news-derived benefit events

def classify(text: str, allowed: set[str] | None = None) -> str | None:
    for label, pattern in BENEFIT_KINDS:
        if (allowed is None or label in allowed) and pattern.search(text or ""):
            return label
    return None


def record_news(items: list[dict]) -> None:
    """Called with scored headlines (title, symbols, published, link, source): keeps benefit headlines per asset."""
    now = time.time()
    changed = False
    with _lock:
        for item in items:
            text = f"{item.get('title', '')} {item.get('summary', '')}"
            ts = _ts(item.get("published")) or now
            for tag in item.get("symbols") or []:
                kind = classify(text, KINDS_BY_MARKET.get(tag.get("market"), set()))
                if not kind:
                    continue
                rows = _state.news.setdefault(tag["symbol"], [])
                if any(r["id"] == item.get("id") or r["title"] == item.get("title") for r in rows):
                    continue
                rows.append({"id": item.get("id"), "kind": kind, "title": item.get("title"), "t": int(ts),
                             "link": item.get("link"), "source": item.get("source")})
                changed = True
        cutoff = now - NEWS_DAYS * DAY
        for sym in list(_state.news):
            _state.news[sym] = [r for r in _state.news[sym] if r["t"] >= cutoff][-30:]
            if not _state.news[sym]:
                del _state.news[sym]
    if changed:
        _save_disk()


def _ts(value) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


# ------------------------------------------------------------------ dividends

def dividend_view(inst: instruments.Instrument, now: float) -> dict | None:
    """Last/next dividend of a stock or ETF from history + announced calendar; None if it pays none."""
    history = market_data.dividends(inst.yahoo)
    announced = _state.calendar.get(inst.yahoo, {})
    if not history and not announced.get("exDate"):
        return None
    history = history or []
    last_t, last_amount = history[-1] if history else (None, None)
    year_sum = sum(a for t, a in history if t >= now - 365 * DAY)
    gaps = np.diff([t for t, _ in history]) if len(history) >= 2 else np.array([])
    rhythm = float(np.median(gaps[-4:])) if len(gaps) else None
    bars = market_data.cached(inst, "1d")
    price = float(bars.close[-1]) if bars is not None and len(bars) else None
    out = {"lastExDate": _iso(last_t), "lastAmount": last_amount, "trailing12m": round(year_sum, 6) or None,
           "yieldPct": round(year_sum / price * 100, 2) if price and year_sum else None,
           "perYear": round(365 * DAY / rhythm, 1) if rhythm else None}
    ex = announced.get("exDate")
    if ex and ex >= date.fromtimestamp(now).isoformat():
        out.update({"nextExDate": ex, "payDate": announced.get("payDate"), "status": "dijadwalkan",
                    "source": "kalender emiten (Yahoo Finance)", "amountEstimate": last_amount})
    elif last_t and rhythm and 20 * DAY <= rhythm <= 400 * DAY:
        nxt = last_t + rhythm
        while nxt < now - 3 * DAY:
            nxt += rhythm
        if nxt - now <= 400 * DAY:
            out.update({"nextExDate": _iso(nxt), "status": "perkiraan", "amountEstimate": last_amount,
                        "source": f"perkiraan dari ritme {len(history)} dividen terakhir"})
    return out


def _iso(t) -> str | None:
    return datetime.fromtimestamp(t, timezone.utc).date().isoformat() if t else None


def _refresh_calendars() -> None:
    """Announced ex-dividend dates of dividend payers, oldest first, a few hundred per pass."""
    import yfinance as yf

    payers = [i for i in instruments.universe() if i.asset_class in ("us", "idx") and market_data.dividends(i.yahoo)]
    now = time.time()
    stale = sorted((i for i in payers if now - _state.calendar.get(i.yahoo, {}).get("fetchedAt", 0) > CALENDAR_REFRESH),
                   key=lambda i: _state.calendar.get(i.yahoo, {}).get("fetchedAt", 0))[:CALENDAR_PER_PASS]
    _state.calendar_pass = {"done": 0, "total": len(stale), "payers": len(payers), "finished": None}
    for n, inst in enumerate(stale, 1):
        entry = {"fetchedAt": int(time.time())}
        try:
            cal = yf.Ticker(inst.yahoo).calendar or {}
            for key, dest in (("Ex-Dividend Date", "exDate"), ("Dividend Date", "payDate")):
                if cal.get(key):
                    entry[dest] = cal[key].isoformat() if hasattr(cal[key], "isoformat") else str(cal[key])
        except Exception as exc:  # one ticker failing must not stop the pass
            entry["error"] = type(exc).__name__
        with _lock:
            _state.calendar[inst.yahoo] = entry
        _state.calendar_pass["done"] = n
        if n % 50 == 0:
            _save_disk()
        time.sleep(1.0)  # gentle on Yahoo
    _state.calendar_pass["finished"] = int(time.time())
    _save_disk()


# ------------------------------------------------------------------ halvings

def _refresh_halvings() -> None:
    import httpx

    for symbol, (url, interval, minutes) in HALVINGS.items():
        try:
            r = httpx.get(url, timeout=15.0)
            r.raise_for_status()
            height = int(r.json()["data"]["blocks"]) if "blockchair" in url else int(r.text.strip())
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            log.info("halving height for %s unavailable: %s", symbol, type(exc).__name__)
            continue
        target = (height // interval + 1) * interval
        eta = time.time() + (target - height) * minutes * 60
        with _lock:
            _state.halvings[symbol] = {"height": height, "target": target, "blocksLeft": target - height,
                                       "date": _iso(eta), "fetchedAt": int(time.time())}


# ------------------------------------------------------------------ hype

def hype(inst: instruments.Instrument, news_count: int) -> dict:
    bars = market_data.cached(inst, "1d")
    vol_ratio = ret5 = None
    if bars is not None and len(bars) > 25:
        v = bars.volume
        if v[-25:].sum() > 0 and v[-20:].mean() > 0:
            vol_ratio = round(float(v[-5:].mean() / v[-20:].mean()), 2)
        ret5 = round(float(bars.close[-1] / bars.close[-6] - 1) * 100, 2)
    score = 0.0
    if vol_ratio:
        score += 40 * min(1.0, max(0.0, (vol_ratio - 1) / 1.5))
    score += 40 * min(1.0, news_count / 5)
    if ret5:
        score += 20 * min(1.0, max(0.0, ret5) / 10)
    label = "TINGGI" if score >= 60 else "SEDANG" if score >= 30 else "RENDAH"
    return {"score": round(score), "label": label, "volumeRatio": vol_ratio, "return5dPct": ret5, "news7d": news_count}


# ------------------------------------------------------------------ view

def view(market: str) -> dict:
    now = time.time()
    today = date.fromtimestamp(now).isoformat()
    rows: list[dict] = []
    with _lock:
        news = {k: list(v) for k, v in _state.news.items()}
        halvings = dict(_state.halvings)
    funds = outlook.fundamentals() or {}
    rate_events = [e for e in funds.get("events", []) if RATE_EVENT.search(e.get("title", "")) and e.get("status") != "released"]
    for inst in instruments.universe():
        if market != "all" and inst.market != market:
            continue
        recent = [n for n in news.get(inst.symbol, []) if n["t"] >= now - 7 * DAY]
        base = {"symbol": inst.symbol, "name": inst.name, "market": inst.market, "assetClass": inst.asset_class}
        events: list[dict] = []
        if inst.asset_class in ("us", "idx"):
            d = dividend_view(inst, now)
            if d and d.get("nextExDate"):
                events.append({"kind": "DIVIDEN", "date": d["nextExDate"], "status": d["status"], "source": d["source"],
                               "detail": d})
        if inst.symbol in halvings:
            h = halvings[inst.symbol]
            events.append({"kind": "HALVING", "date": h["date"], "status": "perkiraan",
                           "source": f"tinggi blok {h['height']:,} → {h['target']:,} ({h['blocksLeft']:,} blok lagi)", "detail": h})
        if inst.asset_class == "forex":
            ccys = {inst.symbol[:3], inst.symbol[3:]}
            for e in rate_events:
                if e.get("currency") in ccys:
                    events.append({"kind": "SUKU BUNGA", "date": (e.get("date") or "")[:10], "status": "dijadwalkan",
                                   "source": "kalender ekonomi", "detail": {"title": e.get("title"), "currency": e.get("currency"),
                                                                          "forecast": e.get("forecast"), "previous": e.get("previous"),
                                                                          "time": e.get("date")}})
        for n in recent:
            events.append({"kind": n["kind"], "date": _iso(n["t"]), "status": "berita", "source": n.get("source") or "berita",
                           "detail": {"title": n["title"], "link": n.get("link")}})
        if not events:
            continue
        h = hype(inst, len(recent))
        for ev in events:
            days = None
            if ev["date"]:
                try:
                    days = (date.fromisoformat(ev["date"]) - date.fromisoformat(today)).days
                except ValueError:
                    days = None
            rows.append({**base, **ev, "daysTo": days, "hype": h})
    # upcoming first (soonest), then recent headlines (newest)
    rows.sort(key=lambda r: (0, r["daysTo"]) if r["daysTo"] is not None and r["daysTo"] >= 0 else (1, -(r["daysTo"] or 0)))
    return {"market": market, "generatedAt": int(now), "count": len(rows), "rows": rows[:600],
            "calendarScan": dict(_state.calendar_pass), "halvings": halvings}


def _loop() -> None:
    _load_disk()
    time.sleep(240)  # after the first daily universe scan has captured dividend history
    while True:
        started = time.time()
        for job in (_refresh_halvings, _refresh_calendars):
            try:
                job()
            except Exception:
                log.exception("benefits job %s failed", job.__name__)
        time.sleep(max(60.0, HALVING_REFRESH - (time.time() - started)))


_started = False


def start() -> None:
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_loop, daemon=True, name="benefits").start()
