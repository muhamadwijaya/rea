"""Recent news sentiment per asset, from the headlines the gateway sends to /sentiment.

Each scored headline tagged to an asset adds (time, score × sign) for that asset — sign −1 when good news for the
subject is bad for the instrument. Virtual-trading signals stamp the last 24 hours' mean when they appear, so their
results can later be compared with and against the news. Kept in memory only (the journal holds the stamps).
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from datetime import datetime

WINDOW = 24 * 3600
KEEP = 48 * 3600
PER_ASSET = 200

_lock = threading.Lock()
_items: dict[str, deque] = defaultdict(lambda: deque(maxlen=PER_ASSET))


def _ts(value) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def record(raw: list[dict], scored: list[dict], now: float | None = None) -> None:
    """`raw` news items (with `published`) and their `scored` results (score, symbols), in the same order."""
    now = now or time.time()
    with _lock:
        for item, s in zip(raw, scored):
            ts = _ts(item.get("published")) or now
            if now - ts > KEEP or not s.get("symbols"):
                continue
            for tag in s["symbols"]:
                _items[tag["symbol"]].append((ts, float(s.get("score") or 0) * (tag.get("sign") or 1)))


def score(symbol: str, now: float | None = None, window: int = WINDOW) -> dict | None:
    """Mean news sentiment of the asset over the last `window` seconds (−1…+1) and how many headlines."""
    now = now or time.time()
    with _lock:
        recent = [v for ts, v in _items.get(symbol, ()) if now - ts <= window]
    if not recent:
        return None
    return {"score": round(sum(recent) / len(recent), 3), "n": len(recent)}
