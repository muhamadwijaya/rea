"""OHLCV loading from Yahoo Finance (yfinance) with a TTL cache.

The cache key is (yahoo ticker, timeframe) and the timeframe fully determines
interval, look-back period and resampling, so every input that changes the
bars is part of the key. Concurrent requests for the same key share a single
download (per-key lock).
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .errors import InvalidTimeframe, NoData, UpstreamError
from .instruments import Instrument

log = logging.getLogger("smc.data")

MAX_BARS = 900


@dataclass(frozen=True)
class Timeframe:
    key: str
    label: str
    interval: str
    period: str
    resample: str | None
    ttl: int  # seconds a cached download stays fresh
    swing: int  # pivot length for swing structure
    internal: int  # pivot length for internal structure
    max_bars: int = MAX_BARS  # most recent bars kept from a download


TIMEFRAMES: dict[str, Timeframe] = {
    "1m": Timeframe("1m", "1M", "1m", "5d", None, 20, 10, 3),
    "5m": Timeframe("5m", "5M", "5m", "30d", None, 30, 12, 4),
    "10m": Timeframe("10m", "10M", "5m", "30d", "10min", 45, 12, 4),
    "15m": Timeframe("15m", "15M", "15m", "30d", None, 60, 15, 4),
    "1h": Timeframe("1h", "1H", "60m", "120d", None, 120, 12, 4),
    "4h": Timeframe("4h", "4H", "60m", "300d", "4h", 300, 10, 3),
    "1d": Timeframe("1d", "1D", "1d", "2y", None, 900, 10, 3),
    # Lighter 4H for the full-universe scan (90 days of hourly bars per asset instead of 300).
    "4hu": Timeframe("4hu", "4H", "60m", "90d", "4h", 1800, 10, 3),
    "1w": Timeframe("1w", "1W", "1wk", "15y", None, 3600, 8, 3),
}


# Long history for the virtual-trading tuner (more out-of-sample trades per asset): Yahoo's longest intraday
# periods (60 days of 15-minute, 730 days of hourly bars) and 10 years of daily bars, up to 6000 bars. Pivot
# lengths match the analysis timeframe of the same name. Kept in a bounded cache of their own (`history_many`).
HISTORY: dict[str, Timeframe] = {
    "15m": Timeframe("15m@hist", "15M", "15m", "60d", None, 1800, 15, 4, 6000),
    "1h": Timeframe("1h@hist", "1H", "60m", "730d", None, 3600, 12, 4, 6000),
    "4h": Timeframe("4h@hist", "4H", "60m", "730d", "4h", 3600, 10, 3, 6000),
    "1d": Timeframe("1d@hist", "1D", "1d", "10y", None, 6 * 3600, 10, 3, 6000),
}
HISTORY_CACHE = 240  # assets × timeframes of long history held at once (least recently used dropped first)


# Realtime feed for the open chart: today's 1-minute bars, refreshed every few seconds and merged by the
# browser into the last candle of whatever timeframe is shown. Not an analysis timeframe.
LIVE = Timeframe("live", "LIVE", "1m", "1d", None, 3, 10, 3)
LIVE_BARS = 240


def timeframe(key: str) -> Timeframe:
    tf = TIMEFRAMES.get((key or "").lower())
    if tf is None:
        raise InvalidTimeframe(f"Timeframe tidak dikenal: {key!r}. Pilihan: {', '.join(TIMEFRAMES)}")
    return tf


@dataclass(frozen=True)
class Bars:
    time: np.ndarray  # int64 unix seconds (bar open, UTC)
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    fetched_at: float

    def __len__(self) -> int:
        return len(self.close)


_cache: dict[tuple[str, str], tuple[float, Bars]] = {}
_history: OrderedDict[tuple[str, str], tuple[float, Bars]] = OrderedDict()
_history_lock = threading.Lock()
# Cash dividends per Yahoo ticker, captured from the daily downloads (no extra requests): [(unix time, amount)].
_dividends: dict[str, list[tuple[int, float]]] = {}
_locks: dict[tuple[str, str], threading.Lock] = {}
_guard = threading.Lock()


def _lock_for(key: tuple[str, str]) -> threading.Lock:
    with _guard:
        return _locks.setdefault(key, threading.Lock())


def _cached(key: tuple[str, str]) -> Bars | None:
    hit = _cache.get(key)
    if hit and hit[0] > time.time():
        return hit[1]
    return None


def _store(key: tuple[str, str], bars: Bars, tf: Timeframe) -> None:
    _cache[key] = (time.time() + tf.ttl, bars)


def frame_to_bars(df: pd.DataFrame, tf: Timeframe, min_bars: int = 30) -> Bars:
    cols = ["Open", "High", "Low", "Close"]
    if df is None or df.empty or any(c not in df.columns for c in cols):
        raise NoData("Tidak ada data harga dari sumber.")
    df = df.dropna(subset=cols)
    df = df[(df["High"] >= df["Low"]) & (df["Close"] > 0)]
    if "Volume" not in df.columns:
        df = df.assign(Volume=0.0)
    idx = df.index
    if not isinstance(idx, pd.DatetimeIndex):
        raise NoData("Format waktu data tidak dikenali.")
    df = df.set_axis(idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC"))
    if tf.resample:
        df = (
            df.resample(tf.resample, label="left", closed="left")
            .agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"})
            .dropna(subset=cols)
        )
    df = df.tail(tf.max_bars)
    if len(df) < min_bars:
        raise NoData(f"Data terlalu sedikit ({len(df)} bar) untuk dianalisis.")
    epoch = pd.Timestamp("1970-01-01", tz="UTC")
    times = ((df.index - epoch) // pd.Timedelta(seconds=1)).to_numpy(dtype=np.int64)
    return Bars(
        time=times,
        open=df["Open"].to_numpy(dtype=float),
        high=df["High"].to_numpy(dtype=float),
        low=df["Low"].to_numpy(dtype=float),
        close=df["Close"].to_numpy(dtype=float),
        volume=df["Volume"].fillna(0).to_numpy(dtype=float),
        fetched_at=time.time(),
    )


def _download(tickers: list[str], tf: Timeframe) -> pd.DataFrame:
    import yfinance as yf  # imported lazily so unit tests do not need network libs

    try:
        return yf.download(
            tickers=tickers,
            period=tf.period,
            interval=tf.interval,
            group_by="ticker",
            auto_adjust=False,
            actions=tf.key == "1d",  # daily downloads also carry dividends (used by the benefit tracker)
            threads=True,
            progress=False,
        )
    except Exception as exc:  # yfinance raises many unrelated types
        raise UpstreamError(f"Gagal mengambil data Yahoo Finance: {exc}") from exc


def _slice(data: pd.DataFrame, ticker: str) -> pd.DataFrame | None:
    if isinstance(data.columns, pd.MultiIndex):
        level0 = data.columns.get_level_values(0)
        if ticker in level0:
            return data[ticker]
        level1 = data.columns.get_level_values(1)
        if ticker in level1:
            return data.xs(ticker, axis=1, level=1)
        return None
    return data


def _capture_dividends(yahoo: str, frame: pd.DataFrame) -> None:
    if "Dividends" not in frame.columns:
        return
    paid = frame["Dividends"].dropna()
    paid = paid[paid > 0]
    epoch = pd.Timestamp("1970-01-01", tz="UTC")
    idx = paid.index.tz_localize("UTC") if paid.index.tz is None else paid.index.tz_convert("UTC")
    _dividends[yahoo] = [(int((t - epoch) // pd.Timedelta(seconds=1)), float(v)) for t, v in zip(idx, paid.to_numpy())]


def dividends(yahoo: str) -> list[tuple[int, float]] | None:
    """Dividends seen in the last daily download of this ticker (None = not downloaded yet)."""
    return _dividends.get(yahoo)


def cached(inst: Instrument, tf_key: str) -> Bars | None:
    """Bars already in the cache (fresh or not), without downloading."""
    hit = _cache.get((inst.yahoo, timeframe(tf_key).key))
    return hit[1] if hit else None


def load(inst: Instrument, tf_key: str) -> Bars:
    tf = timeframe(tf_key)
    key = (inst.yahoo, tf.key)
    bars = _cached(key)
    if bars is not None:
        return bars
    with _lock_for(key):
        bars = _cached(key)
        if bars is not None:
            return bars
        started = time.perf_counter()
        frame = _slice(_download([inst.yahoo], tf), inst.yahoo)
        if frame is None:
            raise NoData(f"Yahoo Finance tidak mengembalikan data untuk {inst.yahoo}.")
        _capture_dividends(inst.yahoo, frame)
        try:
            bars = frame_to_bars(frame, tf)
        except NoData as exc:
            raise NoData(f"{inst.symbol} ({inst.yahoo}) {tf.label}: {exc.message}") from exc
        _store(key, bars, tf)
        log.info("loaded %s %s: %d bars in %.2fs", inst.yahoo, tf.key, len(bars), time.perf_counter() - started)
        return bars


def load_many(instruments: list[Instrument], tf_key: str, chunk: int = 150) -> dict[str, Bars | Exception]:
    """Load many instruments; cache misses are downloaded in batches of `chunk` tickers."""
    tf = timeframe(tf_key)
    out: dict[str, Bars | Exception] = {}
    missing: list[Instrument] = []
    for inst in instruments:
        bars = _cached((inst.yahoo, tf.key))
        if bars is not None:
            out[inst.symbol] = bars
        else:
            missing.append(inst)
    for start in range(0, len(missing), chunk):
        part = missing[start:start + chunk]
        _download_part(part, tf, out)
        if start + chunk < len(missing):
            time.sleep(0.5)  # be gentle with Yahoo between batches
    return out


def _download_part(part: list[Instrument], tf: Timeframe, out: dict[str, Bars | Exception], store=None) -> None:
    tickers = sorted({i.yahoo for i in part})
    started = time.perf_counter()
    try:
        data = _download(tickers, tf)
    except UpstreamError as exc:
        for inst in part:
            out[inst.symbol] = exc
        return
    for inst in part:
        frame = _slice(data, inst.yahoo)
        try:
            if frame is None:
                raise NoData(f"Tidak ada data untuk {inst.yahoo}.")
            _capture_dividends(inst.yahoo, frame)
            bars = frame_to_bars(frame, tf)
            (store or _store)((inst.yahoo, tf.key), bars, tf)
            out[inst.symbol] = bars
        except NoData as exc:
            out[inst.symbol] = exc
    log.info("batch %s: %d tickers in %.2fs", tf.key, len(tickers), time.perf_counter() - started)


def _store_history(key: tuple[str, str], bars: Bars, tf: Timeframe) -> None:
    with _history_lock:
        _history[key] = (time.time() + tf.ttl, bars)
        _history.move_to_end(key)
        while len(_history) > HISTORY_CACHE:
            _history.popitem(last=False)


def history_cached(inst: Instrument, tf_key: str) -> bool:
    tf = HISTORY.get(tf_key)
    if tf is None:
        return _cached((inst.yahoo, timeframe(tf_key).key)) is not None
    with _history_lock:
        hit = _history.get((inst.yahoo, tf.key))
    return bool(hit and hit[0] > time.time())


def history_many(instruments: list[Instrument], tf_key: str, chunk: int = 60) -> dict[str, Bars | Exception]:
    """Long history (see HISTORY) of many instruments; misses are downloaded in batches. Other timeframes fall back
    to the regular bars."""
    tf = HISTORY.get(tf_key)
    if tf is None:
        return load_many(instruments, tf_key)
    out: dict[str, Bars | Exception] = {}
    missing: list[Instrument] = []
    now = time.time()
    with _history_lock:
        for inst in instruments:
            key = (inst.yahoo, tf.key)
            hit = _history.get(key)
            if hit and hit[0] > now:
                _history.move_to_end(key)
                out[inst.symbol] = hit[1]
            else:
                missing.append(inst)
    for start in range(0, len(missing), chunk):
        _download_part(missing[start:start + chunk], tf, out, store=_store_history)
        if start + chunk < len(missing):
            time.sleep(0.5)  # be gentle with Yahoo between batches
    return out


def live(inst: Instrument) -> dict:
    """Latest 1-minute bars of today's session (cached for a few seconds, shared by all viewers)."""
    key = (inst.yahoo, LIVE.key)
    bars = _cached(key)
    if bars is None:
        with _lock_for(key):
            bars = _cached(key)
            if bars is None:
                frame = _slice(_download([inst.yahoo], LIVE), inst.yahoo)
                if frame is None:
                    raise NoData(f"Yahoo Finance tidak mengembalikan data 1 menit untuk {inst.yahoo}.")
                bars = frame_to_bars(frame, LIVE, min_bars=1)
                _store(key, bars, LIVE)
    n = min(len(bars), LIVE_BARS)
    rows = [{"t": int(bars.time[i]), "o": float(bars.open[i]), "h": float(bars.high[i]), "l": float(bars.low[i]),
             "c": float(bars.close[i])} for i in range(len(bars) - n, len(bars))]
    return {"symbol": inst.symbol, "bars": rows, "price": rows[-1]["c"], "asOf": rows[-1]["t"],
            "fetchedAt": int(bars.fetched_at), "serverTime": int(time.time())}
