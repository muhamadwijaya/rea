"""Instrument catalogue and symbol resolution.

This module is the single owner of the mapping between what a user types
("eur/usd", "IDX:BBCA", "aapl") and the identifiers each data source needs
(Yahoo Finance ticker for analysis, TradingView symbol for the live chart).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path

from .errors import InvalidSymbol

ASSET_CLASSES = ("forex", "commodity", "index", "crypto", "us", "idx")

# Screener groups exposed to the UI -> asset classes they contain.
GROUPS: dict[str, tuple[str, ...]] = {
    "all": ASSET_CLASSES,
    "forex": ("forex",),
    "us": ("us",),
    "idx": ("idx",),
    "macro": ("index", "commodity", "crypto"),
}

CURRENCIES = {
    "USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF", "IDR", "SGD", "HKD",
    "CNH", "CNY", "SEK", "NOK", "DKK", "MXN", "ZAR", "TRY", "PLN", "INR", "KRW",
    "THB", "MYR", "PHP", "XAU", "XAG",
}

CRYPTO_COINS = {"BTC", "ETH", "SOL", "XRP", "BNB", "DOGE", "ADA", "AVAX", "LINK", "TON"}

_SYMBOL_RE = re.compile(r"^[A-Z0-9.^=:/_\-!&]{1,24}$")


@dataclass(frozen=True)
class Instrument:
    symbol: str
    name: str
    yahoo: str
    tv: str
    asset_class: str
    decimals: int | None = None

    def price_decimals(self, price: float | None = None) -> int:
        if self.decimals is not None:
            return self.decimals
        if self.asset_class == "forex":
            return 3 if self.symbol.endswith("JPY") else 5
        if self.asset_class == "idx":
            return 0
        if price is not None and abs(price) < 1:
            return 4
        return 2

    def to_json(self) -> dict:
        data = asdict(self)
        data["assetClass"] = data.pop("asset_class")
        data["decimals"] = self.price_decimals()
        return data


def _watchlist_path() -> Path:
    override = os.environ.get("SMC_WATCHLIST")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / "config" / "watchlist.json"


@lru_cache(maxsize=1)
def watchlist() -> tuple[Instrument, ...]:
    raw = json.loads(_watchlist_path().read_text(encoding="utf-8"))
    items: list[Instrument] = []
    for entry in raw.get("instruments", []):
        asset_class = entry.get("class", "us")
        if asset_class not in ASSET_CLASSES:
            raise ValueError(f"watchlist: kelas aset tidak dikenal {asset_class!r} untuk {entry}")
        items.append(
            Instrument(
                symbol=entry["symbol"].upper(),
                name=entry.get("name", entry["symbol"]),
                yahoo=entry["yahoo"],
                tv=entry.get("tv", entry["symbol"]),
                asset_class=asset_class,
                decimals=entry.get("decimals"),
            )
        )
    return tuple(items)


def by_group(group: str) -> list[Instrument]:
    classes = GROUPS.get(group)
    if classes is None:
        raise InvalidSymbol(f"Grup tidak dikenal: {group!r}. Pilihan: {', '.join(GROUPS)}")
    return [i for i in watchlist() if i.asset_class in classes]


def find(symbol: str) -> Instrument | None:
    key = symbol.upper()
    for inst in watchlist():
        if key in (inst.symbol, inst.yahoo.upper(), inst.tv.upper()):
            return inst
    return None


def resolve(query: str) -> Instrument:
    """Turn free text into an Instrument, preferring watchlist entries."""
    text = (query or "").strip().upper().replace(" ", "")
    if not text or not _SYMBOL_RE.match(text):
        raise InvalidSymbol(f"Simbol tidak valid: {query!r}")

    known = find(text)
    if known:
        return known

    exchange, _, ticker = text.rpartition(":")
    compact = ticker.replace("/", "").replace("-", "").replace("_", "")

    known = find(compact)
    if known:
        return known

    if exchange == "IDX" or ticker.endswith(".JK"):
        code = ticker.removesuffix(".JK")
        return Instrument(code, code, f"{code}.JK", f"IDX:{code}", "idx")

    if len(compact) == 6 and compact[:3] in CURRENCIES and compact[3:] in CURRENCIES:
        base, quote = compact[:3], compact[3:]
        if base in ("XAU", "XAG"):
            yahoo = "GC=F" if base == "XAU" else "SI=F"
            return Instrument(compact, compact, yahoo, f"OANDA:{compact}", "commodity", 2)
        return Instrument(compact, f"{base} / {quote}", f"{compact}=X", f"FX:{compact}", "forex")

    coin = compact.removesuffix("USDT").removesuffix("USD")
    if coin != compact and coin in CRYPTO_COINS:
        return Instrument(f"{coin}USD", f"{coin} / USD", f"{coin}-USD", f"BINANCE:{coin}USDT", "crypto")

    if ticker.startswith("^"):
        return Instrument(ticker, ticker, ticker, ticker.lstrip("^"), "index")

    tv = f"{exchange}:{ticker}" if exchange else ticker
    return Instrument(ticker, ticker, ticker.replace(".", "-"), tv, "us")
