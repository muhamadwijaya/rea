"""Regenerate config/universe.json — the full asset universe beyond the core watchlist.

Sources (public pages / APIs), every ticker validated against Yahoo Finance before it is kept:
  * US stocks: S&P 500 constituents (Wikipedia) + a curated list of popular non-S&P stocks and ETFs
  * Indonesia: all companies listed on IDX (Indonesian Wikipedia), Yahoo ticker CODE.JK
  * Crypto: top coins by market cap (CoinGecko); a Yahoo ticker is only kept when its price matches
    CoinGecko's within 15 %, so a ticker that Yahoo maps to a different coin is dropped
  * Forex: generated majors, crosses and USD exotics; commodities: main futures contracts

Usage:  python tools/build_universe.py            (from the engine folder, needs tools/requirements.txt)
        python tools/build_universe.py idx crypto (refresh only some sections)
"""

from __future__ import annotations

import io
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

logging.getLogger("yfinance").setLevel(logging.CRITICAL)
ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "config" / "universe.json"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}

EXTRA_US = {
    # popular US-listed names outside the S&P 500 (validated; unknown/delisted ones are dropped)
    "ARM": "Arm Holdings", "ASML": "ASML Holding", "AZN": "AstraZeneca", "PDD": "PDD Holdings", "SHOP": "Shopify",
    "MELI": "MercadoLibre", "MRVL": "Marvell Technology", "MDB": "MongoDB", "TEAM": "Atlassian", "ZS": "Zscaler",
    "GFS": "GlobalFoundries", "MSTR": "MicroStrategy", "RIVN": "Rivian Automotive", "LCID": "Lucid Group",
    "SOFI": "SoFi Technologies", "SNOW": "Snowflake", "NET": "Cloudflare", "RBLX": "Roblox", "AFRM": "Affirm",
    "UPST": "Upstart", "BABA": "Alibaba Group", "NIO": "NIO Inc", "TSM": "Taiwan Semiconductor", "SE": "Sea Limited",
    "SPOT": "Spotify", "DKNG": "DraftKings", "ROKU": "Roku", "NU": "Nu Holdings", "GME": "GameStop", "IONQ": "IonQ",
    "RKLB": "Rocket Lab", "SOUN": "SoundHound AI", "ASTS": "AST SpaceMobile", "CVNA": "Carvana", "SMR": "NuScale Power",
    "OKLO": "Oklo", "HIMS": "Hims & Hers Health", "CELH": "Celsius Holdings", "RDDT": "Reddit", "CRWV": "CoreWeave",
    "CRCL": "Circle Internet", "NVO": "Novo Nordisk", "SAP": "SAP SE", "TM": "Toyota Motor", "SONY": "Sony Group",
    "BIDU": "Baidu", "JD": "JD.com", "LI": "Li Auto", "XPEV": "XPeng", "BILI": "Bilibili", "GRAB": "Grab Holdings",
    "PATH": "UiPath", "U": "Unity Software", "DOCU": "DocuSign", "TWLO": "Twilio", "OKTA": "Okta", "PINS": "Pinterest",
    "SNAP": "Snap", "LYFT": "Lyft", "CHWY": "Chewy", "ETSY": "Etsy", "W": "Wayfair", "MARA": "MARA Holdings",
    "RIOT": "Riot Platforms", "CLSK": "CleanSpark", "BITF": "Bitfarms", "HUT": "Hut 8",
}
ETFS = {
    "SPY": "SPDR S&P 500 ETF", "QQQ": "Invesco QQQ (Nasdaq 100)", "IWM": "iShares Russell 2000", "DIA": "SPDR Dow Jones ETF",
    "VOO": "Vanguard S&P 500", "VTI": "Vanguard Total Market", "GLD": "SPDR Gold Shares", "SLV": "iShares Silver",
    "TLT": "iShares 20+ Year Treasury", "HYG": "iShares High Yield Bond", "XLE": "Energy Select SPDR",
    "XLF": "Financial Select SPDR", "XLK": "Technology Select SPDR", "XLV": "Health Care Select SPDR",
    "XLY": "Consumer Discretionary SPDR", "XLP": "Consumer Staples SPDR", "XLI": "Industrial Select SPDR",
    "XLU": "Utilities Select SPDR", "XLB": "Materials Select SPDR", "XLRE": "Real Estate Select SPDR",
    "SMH": "VanEck Semiconductor", "SOXX": "iShares Semiconductor", "ARKK": "ARK Innovation", "EEM": "iShares Emerging Markets",
    "EFA": "iShares EAFE", "FXI": "iShares China Large-Cap", "EWZ": "iShares Brazil", "KWEB": "KraneShares China Internet",
    "USO": "United States Oil Fund", "UNG": "United States Natural Gas", "IBIT": "iShares Bitcoin Trust",
    "ETHA": "iShares Ethereum Trust", "TQQQ": "ProShares UltraPro QQQ", "SQQQ": "ProShares UltraPro Short QQQ",
    "EIDO": "iShares MSCI Indonesia",
}
US_INDICES = {"^RUT": ("RUT", "Russell 2000", "TVC:RUT")}
FX = ["USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF"]
EXOTIC_USD = ["IDR", "SGD", "HKD", "CNH", "MXN", "ZAR", "TRY", "INR", "THB", "KRW", "PHP", "MYR", "SEK", "NOK", "PLN", "DKK", "BRL"]
PRIORITY = ["EUR", "GBP", "AUD", "NZD", "USD", "CAD", "CHF", "JPY"]  # market convention for base currency
COMMODITIES = [
    ("XPTUSD", "Platinum (futures proxy)", "PL=F", "TVC:PLATINUM", 2), ("XPDUSD", "Palladium (futures proxy)", "PA=F", "TVC:PALLADIUM", 2),
    ("COPPER", "Copper futures", "HG=F", "COMEX:HG1!", 4), ("UKOIL", "Brent Crude Oil", "BZ=F", "TVC:UKOIL", 2),
    ("NATGAS", "Natural Gas futures", "NG=F", "NYMEX:NG1!", 3), ("CORN", "Corn futures", "ZC=F", "CBOT:ZC1!", 2),
    ("WHEAT", "Wheat futures", "ZW=F", "CBOT:ZW1!", 2), ("SOYBEAN", "Soybean futures", "ZS=F", "CBOT:ZS1!", 2),
    ("COFFEE", "Coffee futures", "KC=F", "ICEUS:KC1!", 2), ("SUGAR", "Sugar futures", "SB=F", "ICEUS:SB1!", 2),
    ("COCOA", "Cocoa futures", "CC=F", "ICEUS:CC1!", 0),
]
STABLE_OR_WRAPPED = {"USDT", "USDC", "DAI", "FDUSD", "USDE", "PYUSD", "TUSD", "USDS", "USD1", "BUSD", "USDD", "FRAX", "RLUSD",
                     "USDTB", "USDX", "USDF", "GHO", "USDO", "USDG", "USDAI", "BUIDL", "USYC", "SUSDE", "SUSDS", "EURC",
                     "WBTC", "WETH", "STETH", "WSTETH", "WEETH", "WBETH", "RETH", "CBBTC", "CBETH", "METH", "LBTC", "SOLVBTC",
                     "JITOSOL", "BNSOL", "MSOL", "BSC-USD", "XAUT", "PAXG", "BFUSD", "SYRUPUSDC", "STKAAVE", "OSETH", "EZETH",
                     "RSETH", "LSETH", "CLBTC", "TBTC", "FBTC", "UBTC", "BTCB", "WHYPE", "KHYPE", "JLP", "SAVAX"}


def html_tables(url: str) -> list[pd.DataFrame]:
    return pd.read_html(io.StringIO(requests.get(url, headers=UA, timeout=40).text))


def last_closes(tickers: list[str], chunk: int = 150) -> dict[str, float]:
    """Latest close per Yahoo ticker (tickers without data are omitted)."""
    out: dict[str, float] = {}
    for i in range(0, len(tickers), chunk):
        part = tickers[i:i + chunk]
        data = yf.download(part, period="10d", interval="1d", group_by="ticker", threads=True, progress=False, auto_adjust=False)
        for t in part:
            try:
                frame = data[t] if isinstance(data.columns, pd.MultiIndex) else data
                closes = frame["Close"].dropna()
            except KeyError:
                continue
            if len(closes):
                out[t] = float(closes.iloc[-1])
        print(f"  validated {min(i + chunk, len(tickers))}/{len(tickers)}", file=sys.stderr)
        time.sleep(1.0)
    return out


def us_stocks() -> list[dict]:
    sp = html_tables("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")[0]
    rows = {str(r["Symbol"]).strip(): (str(r["Security"]).strip(), str(r["GICS Sector"]).strip()) for _, r in sp.iterrows()}
    items = [{"symbol": s, "name": n, "sector": sec, "group": "S&P 500"} for s, (n, sec) in rows.items()]
    items += [{"symbol": s, "name": n, "sector": "", "group": "Populer"} for s, n in EXTRA_US.items() if s not in rows]
    items += [{"symbol": s, "name": n, "sector": "ETF", "group": "ETF"} for s, n in ETFS.items()]
    yahoo = {i["symbol"]: i["symbol"].replace(".", "-") for i in items}
    ok = last_closes(sorted(set(yahoo.values())))
    out = []
    for i in items:
        if yahoo[i["symbol"]] in ok:
            out.append({"symbol": i["symbol"].replace(".", "-"), "name": i["name"], "yahoo": yahoo[i["symbol"]],
                        "tv": i["symbol"], "class": "us", "sector": i["sector"], "group": i["group"]})
    for y, (sym, name, tv) in US_INDICES.items():
        out.append({"symbol": sym, "name": name, "yahoo": y, "tv": tv, "class": "index", "group": "Indeks"})
    return out


def idx_stocks() -> list[dict]:
    table = html_tables("https://id.wikipedia.org/wiki/Daftar_perusahaan_yang_tercatat_di_Bursa_Efek_Indonesia")[0]
    items = []
    for _, r in table.iterrows():
        code = str(r["Kode"]).split(":")[-1].strip().upper()  # cells look like "BEI: AALI"
        if not code.isalpha() or not 3 <= len(code) <= 5:
            continue
        name = str(r["Nama perusahaan"]).strip()
        sector = str(r.get("Sektor", "") or "").strip()
        items.append({"symbol": code, "name": name, "sector": "" if sector == "nan" else sector})
    ok = last_closes([f"{i['symbol']}.JK" for i in items])
    return [{"symbol": i["symbol"], "name": i["name"], "yahoo": f"{i['symbol']}.JK", "tv": f"IDX:{i['symbol']}",
             "class": "idx", "sector": i["sector"], "group": "BEI"} for i in items if f"{i['symbol']}.JK" in ok]


def crypto(limit: int = 150) -> list[dict]:
    coins = requests.get(
        "https://api.coingecko.com/api/v3/coins/markets",
        params={"vs_currency": "usd", "order": "market_cap_desc", "per_page": limit, "page": 1},
        headers=UA, timeout=40,
    ).json()
    picked = [c for c in coins if c["symbol"].upper() not in STABLE_OR_WRAPPED and c.get("current_price")]
    ok = last_closes([f"{c['symbol'].upper()}-USD" for c in picked])
    out = []
    for c in picked:
        sym = c["symbol"].upper()
        price = ok.get(f"{sym}-USD")
        if price is None or not 0.85 <= price / c["current_price"] <= 1.15:
            continue  # Yahoo ticker missing or points at a different coin
        decimals = 2 if price >= 10 else 4 if price >= 0.1 else 6
        out.append({"symbol": f"{sym}USD", "name": f"{c['name']} / US Dollar", "yahoo": f"{sym}-USD",
                    "tv": f"CRYPTO:{sym}USD", "class": "crypto", "decimals": decimals, "group": f"Top {limit}",
                    "rank": c.get("market_cap_rank")})
    return out


def forex() -> list[dict]:
    pairs = []
    for i, a in enumerate(PRIORITY):
        for b in PRIORITY[i + 1:]:
            pairs.append(a + b)
    pairs += [f"USD{c}" for c in EXOTIC_USD]
    ok = last_closes([f"{p}=X" for p in pairs])
    out = []
    for p in pairs:
        if f"{p}=X" not in ok:
            continue
        price = ok[f"{p}=X"]
        decimals = 3 if p.endswith("JPY") else 0 if price > 1000 else 2 if price > 100 else 4 if price > 10 else 5
        tv = f"FX:{p}" if p[:3] in FX and p[3:] in FX else f"FX_IDC:{p}"
        out.append({"symbol": p, "name": f"{p[:3]} / {p[3:]}", "yahoo": f"{p}=X", "tv": tv, "class": "forex",
                    "decimals": decimals, "group": "Mayor & cross" if p[3:] in FX else "Eksotis"})
    return out


def commodities() -> list[dict]:
    ok = last_closes([c[2] for c in COMMODITIES])
    return [{"symbol": s, "name": n, "yahoo": y, "tv": tv, "class": "commodity", "decimals": d, "group": "Komoditas"}
            for s, n, y, tv, d in COMMODITIES if y in ok]


SECTION_CLASSES = {"forex": {"forex"}, "commodities": {"commodity"}, "crypto": {"crypto"}, "us": {"us", "index"}, "idx": {"idx"}}


def main() -> None:
    sections = {"forex": forex, "commodities": commodities, "crypto": crypto, "us": us_stocks, "idx": idx_stocks}
    only = [a for a in sys.argv[1:] if a in sections]
    instruments: list[dict] = []
    if only and OUT.exists():
        # Refresh some sections only: keep the other classes from the existing file.
        drop = set().union(*(SECTION_CLASSES[a] for a in only))
        instruments = [i for i in json.loads(OUT.read_text(encoding="utf-8"))["instruments"] if i["class"] not in drop]
        sections = {k: v for k, v in sections.items() if k in only}
    for name, fn in sections.items():
        print(f"[{name}]", file=sys.stderr)
        part = fn()
        print(f"  kept {len(part)}", file=sys.stderr)
        instruments += part
    payload = {
        "comment": "Generated by engine/tools/build_universe.py — do not edit by hand; core instruments live in watchlist.json.",
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sources": ["Wikipedia: List of S&P 500 companies", "Wikipedia (id): Daftar perusahaan yang tercatat di BEI",
                    "CoinGecko markets API", "Yahoo Finance (validation)"],
        "instruments": instruments,
    }
    OUT.write_text("{\n" + ",\n".join(
        f'  "{k}": ' + (json.dumps(v, ensure_ascii=False) if k != "instruments" else
                        "[\n" + ",\n".join("    " + json.dumps(i, ensure_ascii=False) for i in v) + "\n  ]")
        for k, v in payload.items()) + "\n}\n", encoding="utf-8")
    print(f"wrote {OUT} with {len(instruments)} instruments", file=sys.stderr)


if __name__ == "__main__":
    main()
