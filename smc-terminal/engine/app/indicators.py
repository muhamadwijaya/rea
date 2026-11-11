"""Plain numpy indicator helpers (no look-ahead: value at i uses bars <= i)."""

from __future__ import annotations

import numpy as np


def ema(values: np.ndarray, length: int) -> np.ndarray:
    out = np.full(len(values), np.nan)
    if len(values) < length:
        return out
    alpha = 2.0 / (length + 1.0)
    out[length - 1] = values[:length].mean()
    for i in range(length, len(values)):
        out[i] = alpha * values[i] + (1 - alpha) * out[i - 1]
    return out


def _wilder(values: np.ndarray, length: int) -> np.ndarray:
    out = np.full(len(values), np.nan)
    if len(values) < length:
        return out
    out[length - 1] = values[:length].mean()
    for i in range(length, len(values)):
        out[i] = (out[i - 1] * (length - 1) + values[i]) / length
    return out


def true_range(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    prev_close = np.concatenate(([close[0]], close[:-1]))
    return np.maximum.reduce([high - low, np.abs(high - prev_close), np.abs(low - prev_close)])


def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, length: int = 14) -> np.ndarray:
    values = _wilder(true_range(high, low, close), length)
    # Back-fill the warm-up period with the first defined value so that
    # threshold comparisons on early bars stay meaningful.
    first = np.argmax(~np.isnan(values)) if np.any(~np.isnan(values)) else None
    if first is None:
        return np.full(len(high), float(np.mean(high - low)) if len(high) else 0.0)
    values[:first] = values[first]
    return values


def rsi(close: np.ndarray, length: int = 14) -> np.ndarray:
    out = np.full(len(close), np.nan)
    if len(close) <= length:
        return out
    delta = np.diff(close)
    gains = np.where(delta > 0, delta, 0.0)
    losses = np.where(delta < 0, -delta, 0.0)
    avg_gain = gains[:length].mean()
    avg_loss = losses[:length].mean()
    for i in range(length, len(close)):
        if i > length:
            avg_gain = (avg_gain * (length - 1) + gains[i - 1]) / length
            avg_loss = (avg_loss * (length - 1) + losses[i - 1]) / length
        out[i] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    return out
