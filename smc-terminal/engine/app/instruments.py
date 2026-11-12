"""Instrument catalogue and symbol resolution.

This module is the single owner of the mapping between what a user types
("eur/usd", "IDX:BBCA", "aapl") and the identifiers each data source needs
(Yahoo Finance ticker for analysis, TradingView symbol for the live chart).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, replace
from functools import lru_cache
from pathlib import Path

from .errors import InvalidSymbol

ASSET_CLASSES = ("forex", "commodity", "index", "crypto", "us", "idx")

# Market pages of the UI. Every instrument belongs to exactly one market.
MARKETS = ("forex", "saham-as", "saham-idx", "kripto")
DEFAULT_MARKET = {
    "forex": "forex",
    "commodity": "forex",
    "us": "saham-as",
    "index": "saham-as",
    "idx": "saham-idx",
    "crypto": "kripto",
}

CURRENCIES = {
    "USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF", "IDR", "SGD", "HKD",
    "CNH", "CNY", "SEK", "NOK", "DKK", "MXN", "ZAR", "TRY", "PLN", "INR", "KRW",
    "THB", "MYR", "PHP", "XAU", "XAG",
}

CRYPTO_COINS = {"BTC", "ETH", "SOL", "XRP", "BNB", "DOGE", "ADA", "AVAX", "LINK", "LTC", "DOT", "TON"}

_SYMBOL_RE = re.compile(r"^[A-Z0-9.^=:/_\-!&]{1,24}$")


@dataclass(frozen=True)
class Instrument:
    symbol: str
    name: str
    yahoo: str
    tv: str
    asset_class: str
    decimals: int | None = None
    market_override: str | None = None
    core: bool = False  # part of the curated watchlist (multi-timeframe screening)
    sector: str = ""
    group: str = ""

    @property
    def market(self) -> str:
        return self.market_override or DEFAULT_MARKET[self.asset_class]

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
        data.pop("market_override")
        if not self.sector:
            data.pop("sector")
        if not self.group:
            data.pop("group")
        data["market"] = self.market
        data["decimals"] = self.price_decimals()
        return data


def _config_path(env: str, name: str) -> Path:
    override = os.environ.get(env)
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / "config" / name


def _load(path: Path, core: bool) -> list[Instrument]:
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    items: list[Instrument] = []
    for entry in raw.get("instruments", []):
        asset_class = entry.get("class", "us")
        if asset_class not in ASSET_CLASSES:
            raise ValueError(f"{path.name}: kelas aset tidak dikenal {asset_class!r} untuk {entry}")
        items.append(
            Instrument(
                symbol=entry["symbol"].upper(),
                name=entry.get("name", entry["symbol"]),
                yahoo=entry["yahoo"],
                tv=entry.get("tv", entry["symbol"]),
                asset_class=asset_class,
                decimals=entry.get("decimals"),
                market_override=_market(entry.get("market"), entry),
                core=core,
                sector=_label(entry.get("sector")),
                group=_label(entry.get("group")),
            )
        )
    return items


def _label(value) -> str:
    """Sector/group labels from generated data; values without any letter (e.g. a stray "`") are unknown."""
    text = str(value or "").strip()
    return text if any(c.isalpha() for c in text) else ""


@lru_cache(maxsize=1)
def watchlist() -> tuple[Instrument, ...]:
    """Core instruments: screened on every timeframe and used by the market-direction model."""
    return tuple(_load(_config_path("SMC_WATCHLIST", "watchlist.json"), core=True))


@lru_cache(maxsize=1)
def universe() -> tuple[Instrument, ...]:
    """Every known instrument: the core watchlist first, then config/universe.json (deduplicated)."""
    extra = _load(_config_path("SMC_UNIVERSE", "universe.json"), core=False)
    by_key = {k: i for i in extra for k in (i.symbol, i.yahoo)}
    items = []
    for core in watchlist():
        # the curated entry wins, but keeps the sector/group the generated universe knows for it
        twin = by_key.get(core.symbol) or by_key.get(core.yahoo)
        if twin and (not core.sector or not core.group):
            core = replace(core, sector=core.sector or twin.sector, group=core.group or twin.group)
        items.append(core)
    seen = {i.symbol for i in items} | {i.yahoo for i in items}
    for inst in extra:
        if inst.symbol in seen or inst.yahoo in seen:
            continue
        seen.update((inst.symbol, inst.yahoo))
        items.append(inst)
    return tuple(items)


@lru_cache(maxsize=1)
def _index() -> dict[str, Instrument]:
    lookup: dict[str, Instrument] = {}
    for inst in reversed(universe()):  # core entries win on conflicts
        for key in (inst.symbol, inst.yahoo.upper(), inst.tv.upper()):
            lookup[key] = inst
    return lookup


def _market(value: str | None, entry: dict) -> str | None:
    if value is not None and value not in MARKETS:
        raise ValueError(f"watchlist: market tidak dikenal {value!r} untuk {entry}")
    return value


def by_market(market: str, scope: str = "core") -> list[Instrument]:
    """Instruments of one market page (or all). scope: core = watchlist, all = full universe."""
    pool = universe() if scope == "all" else watchlist()
    if market == "all":
        return list(pool)
    if market not in MARKETS:
        raise InvalidSymbol(f"Market tidak dikenal: {market!r}. Pilihan: all, {', '.join(MARKETS)}")
    return [i for i in pool if i.market == market]


def find(symbol: str) -> Instrument | None:
    return _index().get(symbol.upper())


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


# ------------------------------------------------------------------ contract per lot (virtual account)

# Units per lot of the usual CFD / exchange conventions; prices of grains, coffee and sugar futures are in US cents.
LOT_UNITS = {"XAUUSD": (100, "oz"), "XAGUSD": (5000, "oz"), "XPTUSD": (100, "oz"), "XPDUSD": (100, "oz"),
             "USOIL": (1000, "barel"), "UKOIL": (1000, "barel"), "NATGAS": (10000, "MMBtu"), "COPPER": (25000, "lb"),
             "CORN": (5000, "bushel"), "WHEAT": (5000, "bushel"), "SOYBEAN": (5000, "bushel"), "COFFEE": (37500, "lb"),
             "SUGAR": (112000, "lb"), "COCOA": (10, "ton")}
PRICED_IN_CENTS = {"CORN", "WHEAT", "SOYBEAN", "COFFEE", "SUGAR"}


def contract_spec(inst: Instrument) -> dict:
    """What one lot is and how its profit reaches the account currency: `size` units of `unit`, prices in `quote`,
    account in USD (IDR for Indonesian stocks). `convert`: "same" (quote is the account currency), "cents" (× 0.01),
    "per_price" (a USD-based pair: divide by the price), "cross" (needs a rate quote → account)."""
    symbol, cls = inst.symbol.upper(), inst.asset_class
    base = None
    if cls == "forex" and len(symbol) == 6:
        base, quote, size, unit = symbol[:3], symbol[3:], 100_000, symbol[:3]
    elif symbol in LOT_UNITS:
        (size, unit), quote = LOT_UNITS[symbol], ("USX" if symbol in PRICED_IN_CENTS else "USD")
    elif cls == "crypto":
        size, unit, quote = 1, symbol[:-3] if symbol.endswith("USD") else symbol, "USD"
    elif cls == "idx":
        size, unit, quote = 100, "lembar", "IDR"  # 1 lot BEI = 100 lembar
    elif cls == "us":
        size, unit, quote = 100, "lembar", "USD"
    else:  # indices: one currency unit per point per lot
        size, unit, quote = 1, "poin", "IDR" if inst.market == "saham-idx" else "USD"
    account = "IDR" if inst.market == "saham-idx" else "USD"
    if quote == account:
        convert = "same"
    elif quote == "USX" and account == "USD":
        convert = "cents"
    elif base == account:
        convert = "per_price"
    else:
        convert = "cross"
    return {"size": size, "unit": unit, "quote": quote, "account": account, "convert": convert}
