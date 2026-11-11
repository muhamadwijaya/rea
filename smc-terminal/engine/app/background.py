"""Background jobs owned by the engine: quotes for the whole universe and a full-universe 1D SMC scan.

Both run in daemon threads, download in small batches (gentle on Yahoo) and publish immutable
snapshots, so HTTP requests never wait for a sweep: they read the latest snapshot and its progress.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field

import pandas as pd

from . import instruments, screener

log = logging.getLogger("smc.background")

QUOTE_CHUNK = 150
QUOTE_INTERVAL = 120  # seconds between the starts of two full quote sweeps
SCAN_INTERVAL = 20 * 60  # seconds between full-universe 1D scans


@dataclass
class Progress:
    done: int = 0
    total: int = 0
    started: float = 0.0
    finished: float | None = None
    error: str | None = None

    def to_json(self) -> dict:
        return {"done": self.done, "total": self.total, "started": int(self.started) or None,
                "finished": int(self.finished) if self.finished else None, "error": self.error}


@dataclass
class QuoteBook:
    rows: dict[str, dict] = field(default_factory=dict)
    progress: Progress = field(default_factory=Progress)
    sweeps: int = 0


_quotes = QuoteBook()
_quotes_lock = threading.Lock()
_scan: dict[str, dict] = {}
_scan_progress = Progress()
_scan_lock = threading.Lock()


def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def parse_quote(frame: pd.DataFrame) -> dict | None:
    """Session quote from daily bars: open & last of the latest session, change vs previous close."""
    frame = frame.dropna(subset=["Open", "Close"])
    if frame.empty:
        return None
    last_row = frame.iloc[-1]
    prev_close = _num(frame["Close"].iloc[-2]) if len(frame) > 1 else None
    open_, last = _num(last_row["Open"]), _num(last_row["Close"])
    if not open_ or not last:
        return None
    ts = frame.index[-1]
    return {
        "open": open_,
        "last": last,
        "high": _num(last_row.get("High")),
        "low": _num(last_row.get("Low")),
        "prevClose": prev_close,
        "changePct": round((last / prev_close - 1) * 100, 3) if prev_close else None,
        "openChangePct": round((last / open_ - 1) * 100, 3),
        "session": ts.strftime("%Y-%m-%d") if hasattr(ts, "strftime") else str(ts),
    }


def _sweep_quotes() -> None:
    import yfinance as yf

    universe = instruments.universe()
    progress = Progress(total=len(universe), started=time.time())
    with _quotes_lock:
        _quotes.progress = progress
    for start in range(0, len(universe), QUOTE_CHUNK):
        part = universe[start:start + QUOTE_CHUNK]
        tickers = sorted({i.yahoo for i in part})
        try:
            data = yf.download(tickers, period="5d", interval="1d", group_by="ticker", auto_adjust=False,
                               actions=False, threads=True, progress=False)
        except Exception as exc:  # network / Yahoo errors: keep previous quotes, report, continue
            progress.error = str(exc)[:200]
            data = None
        fresh: dict[str, dict] = {}
        if data is not None and not data.empty:
            for inst in part:
                try:
                    frame = data[inst.yahoo] if isinstance(data.columns, pd.MultiIndex) else data
                except KeyError:
                    continue
                quote = parse_quote(frame)
                if quote:
                    fresh[inst.symbol] = {
                        "symbol": inst.symbol, "name": inst.name, "market": inst.market, "assetClass": inst.asset_class,
                        "core": inst.core, "decimals": inst.price_decimals(quote["last"]), "tv": inst.tv, **quote,
                        "updated": int(time.time()),
                    }
        with _quotes_lock:
            _quotes.rows.update(fresh)
            progress.done = min(start + QUOTE_CHUNK, len(universe))
        time.sleep(0.5)
    progress.finished = time.time()
    with _quotes_lock:
        _quotes.sweeps += 1
    log.info("quote sweep: %d/%d quotes in %.0fs", len(_quotes.rows), len(universe), progress.finished - progress.started)


def quotes(market: str = "all") -> dict:
    with _quotes_lock:
        rows = [r for r in _quotes.rows.values() if market == "all" or r["market"] == market]
        progress = _quotes.progress.to_json()
        sweeps = _quotes.sweeps
    return {"market": market, "generatedAt": int(time.time()), "sweeps": sweeps, "progress": progress, "count": len(rows), "rows": rows}


def _scan_universe() -> None:
    markets = instruments.MARKETS
    progress = Progress(total=len(markets), started=time.time())
    with _scan_lock:
        global _scan_progress
        _scan_progress = progress
    for market in markets:
        try:
            snap = screener.screen(market, "1d", scope="all")
            with _scan_lock:
                _scan[market] = snap
        except Exception as exc:  # one failing market must not stop the others
            progress.error = f"{market}: {exc}"[:200]
            log.exception("universe scan %s failed", market)
        progress.done += 1
    progress.finished = time.time()
    log.info("universe 1D scan finished in %.0fs", progress.finished - progress.started)


def universe_screen(market: str) -> dict:
    with _scan_lock:
        progress = _scan_progress.to_json()
        if market == "all":
            parts = [_scan[m] for m in instruments.MARKETS if m in _scan]
            if not parts:
                return {"market": market, "tf": "1d", "scope": "all", "status": "warming", "progress": progress, "rows": []}
            rows = [r for p in parts for r in p["rows"]]
            return {"market": market, "tf": "1d", "scope": "all", "status": "ready", "progress": progress,
                    "generatedAt": min(p["generatedAt"] for p in parts), "count": len(rows), "rows": rows,
                    "failures": sum(p["failures"] for p in parts), "elapsedMs": sum(p["elapsedMs"] for p in parts)}
        snap = _scan.get(market)
    if snap is None:
        return {"market": market, "tf": "1d", "scope": "all", "status": "warming", "progress": progress, "rows": []}
    return {**snap, "status": "ready", "progress": progress}


def _loop(name: str, fn, interval: int, initial_delay: float) -> None:
    time.sleep(initial_delay)
    while True:
        started = time.time()
        try:
            fn()
        except Exception:
            log.exception("%s failed", name)
        time.sleep(max(10.0, interval - (time.time() - started)))


_started = False


def start() -> None:
    """Start the background jobs once (disabled with SMC_BACKGROUND=0, e.g. in tests)."""
    global _started
    if _started or os.environ.get("SMC_BACKGROUND", "1") == "0":
        return
    _started = True
    threading.Thread(target=_loop, args=("quotes", _sweep_quotes, QUOTE_INTERVAL, 2), daemon=True, name="quotes").start()
    # The core 4H screener runs first (requested by the gateway at boot); the universe scan follows.
    threading.Thread(target=_loop, args=("universe-scan", _scan_universe, SCAN_INTERVAL, 45), daemon=True, name="scan").start()
