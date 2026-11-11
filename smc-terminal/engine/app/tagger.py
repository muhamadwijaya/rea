"""Symbol-level tagging of headlines.

Finds which instruments of the universe a headline is about, with a sign telling whether good news
for the mentioned thing is good (+1) or bad (-1) for the instrument's price, e.g. "yen strengthens"
is bad for USDJPY. Matching is deliberately conservative:

* tickers only count as tickers when written in upper case and not a common word/acronym
  ("$NVDA", "(TSLA)", "BBCA" …); Indonesian headlines prefer IDX codes, others US tickers;
* company / coin names match as capitalised phrases with the corporate suffixes removed;
* currencies, metals, energy and indices go through a curated alias table.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache

from . import instruments

MAX_TAGS = 6

# Upper-case words that are tickers somewhere but almost always mean something else in a headline.
TICKER_STOP = {
    "A", "AI", "ALL", "AN", "ANY", "ARE", "AT", "BE", "BIG", "BUY", "CAN", "CEO", "CFO", "CPI", "PPI", "PMI", "GDP", "IPO",
    "ETF", "ETFS", "USA", "US", "UK", "EU", "FED", "ECB", "BOJ", "BOE", "RBA", "SEC", "IMF", "OPEC", "NOW", "KEY", "LOW",
    "WELL", "COST", "HAS", "ONE", "NEW", "IT", "ON", "OR", "SO", "GO", "FOR", "OUT", "HOT", "NICE", "TECH", "POOL", "PEAK",
    "CASH", "GOOD", "BEST", "FAST", "LAND", "LIFE", "HOME", "OPEN", "TRUE", "TOP", "WIN", "YES", "SIX", "TEN", "ICE", "GAS",
    "SUN", "AIR", "CAR", "CARS", "PLAY", "ROCK", "STEP", "MOVE", "WAVE", "LOVE", "ELSE", "CORE", "FUND", "SAFE", "RARE",
    "Q1", "Q2", "Q3", "Q4", "H1", "H2", "YOY", "MOM", "QOQ", "EPS", "AND", "THE", "NOT", "BUT", "WAS", "HAD", "WHO",
    "OJK", "BEI", "BI", "IDX", "RI", "PT", "TBK", "DPR", "APBN", "BUMN", "PHK", "IHSG", "USD", "IDR", "EUR", "JPY", "GBP",
    "CNY", "WTI", "LNG", "EV", "EVS", "AR", "VR", "TV", "PC", "API", "CHIP", "DATA", "BANK", "MAN", "MEN", "WAR", "LAW",
    "MSCI", "FTSE", "STOXX",  # index providers: headlines are about index reviews, not the listed company
}

# A first word shared by this many instrument names (e.g. "Teknologi", "Nusantara", "Mitra") is a common word,
# not a distinctive company name.
GENERIC_NAME_WORD = 3

# Generic words that are not distinctive enough as a one-word company name.
NAME_STOP = {
    "american", "united", "general", "first", "southern", "progressive", "public", "global", "energy", "international",
    "digital", "realty", "mobile", "national", "capital", "financial", "target", "ball", "visa", "oracle", "best",
    "fox", "news", "match", "block", "booking", "corning", "dollar", "gap", "insulet", "lumen", "masco", "tapestry",
    "united", "western", "eastern", "central", "pacific", "atlantic", "republic", "citizens", "regions", "ventas",
    "bank", "indonesia", "mandiri", "jaya", "sejahtera", "utama", "abadi", "makmur", "sentosa", "persada", "prima",
    "group", "holdings", "resources", "industries", "solutions", "systems", "technologies", "services", "partners",
    "trust", "gold", "oil", "coin", "token", "network", "protocol", "finance", "chain", "world", "pepe", "sui", "near",
}

SUFFIXES = re.compile(
    r"\b(inc|incorporated|corp|corporation|co|company|ltd|limited|plc|holdings?|group|n\.?v|s\.?a|se|ag|tbk|pt|persero|"
    r"class [abc]|the|\(the\)|& co|co\.)\b\.?",
    re.IGNORECASE,
)

# alias -> (symbol, sign). Longest aliases are matched first, so "Australian dollar" wins over "dollar".
CURATED: dict[str, tuple[str, int]] = {
    "U.S. dollar": ("DXY", 1), "US dollar": ("DXY", 1), "dollar": ("DXY", 1), "greenback": ("DXY", 1), "dolar AS": ("DXY", 1),
    "dollar index": ("DXY", 1), "DXY": ("DXY", 1),
    "euro": ("EURUSD", 1), "pound": ("GBPUSD", 1), "sterling": ("GBPUSD", 1), "yen": ("USDJPY", -1),
    "Australian dollar": ("AUDUSD", 1), "Aussie": ("AUDUSD", 1), "New Zealand dollar": ("NZDUSD", 1), "kiwi": ("NZDUSD", 1),
    "Canadian dollar": ("USDCAD", -1), "loonie": ("USDCAD", -1), "Swiss franc": ("USDCHF", -1), "franc": ("USDCHF", -1),
    "rupiah": ("USDIDR", -1), "yuan": ("USDCNH", -1), "renminbi": ("USDCNH", -1),
    "gold": ("XAUUSD", 1), "emas": ("XAUUSD", 1), "bullion": ("XAUUSD", 1),
    "silver": ("XAGUSD", 1), "perak": ("XAGUSD", 1), "platinum": ("XPTUSD", 1), "palladium": ("XPDUSD", 1),
    "copper": ("COPPER", 1), "oil": ("USOIL", 1), "crude": ("USOIL", 1), "WTI": ("USOIL", 1), "minyak": ("USOIL", 1),
    "Brent": ("UKOIL", 1), "natural gas": ("NATGAS", 1), "coffee": ("COFFEE", 1), "cocoa": ("COCOA", 1), "wheat": ("WHEAT", 1),
    "S&P 500": ("SPX", 1), "S&P": ("SPX", 1), "Wall Street": ("SPX", 1), "Nasdaq": ("NDX", 1), "Dow Jones": ("DJI", 1),
    "the Dow": ("DJI", 1), "Russell 2000": ("RUT", 1), "VIX": ("VIX", 1), "Treasury yields": ("US10Y", 1),
    "10-year yield": ("US10Y", 1), "IHSG": ("IHSG", 1), "JCI": ("IHSG", 1), "Jakarta Composite": ("IHSG", 1),
    "Bitcoin": ("BTCUSD", 1), "Ether": ("ETHUSD", 1), "Ethereum": ("ETHUSD", 1),
}

_PAIR = re.compile(r"\b([A-Z]{3})\s?/\s?([A-Z]{3})\b|\b([A-Z]{6})\b")
_TOKEN = re.compile(r"\$?[A-Z][A-Z0-9]{1,5}\b")
_WORDS = re.compile(r"[A-Za-z0-9&'.\-]+")


@dataclass(frozen=True)
class Tag:
    symbol: str
    market: str
    sign: int

    def to_json(self) -> dict:
        return {"symbol": self.symbol, "market": self.market, "sign": self.sign}


def _clean_name(name: str) -> str:
    name = name.split(" / ")[0]
    name = re.sub(r"\(.*?\)", " ", name)
    name = SUFFIXES.sub(" ", name)
    name = re.sub(r"[^\w&'\- ]", " ", name)
    return re.sub(r"\s+", " ", name).strip()


@lru_cache(maxsize=1)
def _indexes() -> tuple[dict[str, list], dict[str, tuple[str, int, bool]], int]:
    """(ticker -> instruments, lower-cased phrase -> (symbol, sign, needs capital), longest phrase in words)."""
    tickers: dict[str, list] = {}
    phrases: dict[str, tuple[str, int, bool]] = {}
    named = [i for i in instruments.universe() if i.asset_class in ("us", "idx", "crypto")]
    word_use = Counter(w for i in named for w in {x.lower() for x in _clean_name(i.name).split()})
    for inst in instruments.universe():
        code = inst.symbol
        if inst.asset_class == "crypto":
            code = code.removesuffix("USD")
        if code not in TICKER_STOP and len(code) >= 2:
            tickers.setdefault(code, []).append(inst)
        if inst.asset_class in ("us", "idx", "crypto"):
            clean = _clean_name(inst.name)
            words = clean.split()
            if not words:
                continue
            # Company / coin names are proper nouns: matched case-insensitively but only when capitalised.
            if len(words) >= 2 and len(clean) >= 8:
                phrases.setdefault(clean.lower(), (inst.symbol, 1, True))
            first = words[0]
            if (len(first) >= 5 and first.lower() not in NAME_STOP and first[0].isupper()
                    and word_use[first.lower()] < GENERIC_NAME_WORD):
                phrases.setdefault(first.lower(), (inst.symbol, 1, True))
    for alias, (symbol, sign) in CURATED.items():
        phrases[alias.lower()] = (symbol, sign, False)
    longest = max(len(p.split()) for p in phrases)
    return tickers, phrases, longest


def _pick(candidates: list, prefer_idx: bool):
    if len(candidates) == 1:
        return candidates[0]
    wanted = "idx" if prefer_idx else "us"
    for c in candidates:
        if c.asset_class == wanted:
            return c
    return candidates[0]


def tag(text: str, lang: str = "en", category: str = "") -> list[Tag]:
    tickers, phrases, longest = _indexes()
    prefer_idx = lang == "id" or category == "idx"
    found: dict[str, Tag] = {}

    alias_fx: set[str] = set()  # currencies named by a curated alias ("euro"), not by an explicit pair

    def add(symbol: str, sign: int) -> None:
        inst = instruments.find(symbol)
        if inst and symbol not in found and len(found) < MAX_TAGS:
            found[symbol] = Tag(inst.symbol, inst.market, sign)

    # Explicit FX pairs: "EUR/USD", "USDJPY".
    for m in _PAIR.finditer(text):
        pair = (m.group(1) + m.group(2)) if m.group(1) else m.group(3)
        if pair and instruments.find(pair) and instruments.find(pair).asset_class == "forex":
            add(pair, 1)

    # Phrases (names, curated aliases), longest first, without overlapping spans.
    words = _WORDS.findall(text)
    used = [False] * len(words)
    for size in range(min(longest, 5), 0, -1):
        for i in range(0, len(words) - size + 1):
            if any(used[i:i + size]):
                continue
            phrase = " ".join(w.rstrip(".,'") for w in words[i:i + size])
            hit = phrases.get(phrase.lower())
            if hit and (not hit[2] or phrase[:1].isupper()):
                if not hit[2] and hit[0] not in found:
                    alias_fx.add(hit[0])
                add(hit[0], hit[1])
                for j in range(i, i + size):
                    used[j] = True

    # Upper-case tickers ($TSLA, (NVDA), BBCA).
    for m in _TOKEN.finditer(text):
        token = m.group(0).lstrip("$")
        candidates = tickers.get(token)
        if candidates:
            add(_pick(candidates, prefer_idx).symbol, 1)
    # "Bitcoin price in euro": the currency is only the quote unit of a crypto headline.
    if any(t.market == "kripto" for t in found.values()):
        for symbol in alias_fx:
            inst = instruments.find(symbol)
            if symbol in found and inst and (inst.asset_class == "forex" or symbol == "DXY"):
                del found[symbol]
    return list(found.values())
