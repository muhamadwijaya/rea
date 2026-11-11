"""Grouping of every instrument into a display block (sector / category) for the asset map.

This module owns the interpretation; consumers (quotes, the map) use `block_of` and `BLOCK_ORDER`
instead of re-deriving groups from names. Stocks use their exchange sector (GICS for the US, IDX-IC for
BEI); popular US stocks outside the S&P 500, crypto coins and forex/commodities use curated groups.
"""

from __future__ import annotations

from .instruments import Instrument

FX_MAJORS = {"EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "NZDUSD", "USDCAD"}
METALS = {"XAUUSD", "XAGUSD", "XPTUSD", "XPDUSD", "COPPER"}
ENERGY = {"USOIL", "UKOIL", "NATGAS"}
AGRI = {"CORN", "WHEAT", "SOYBEAN", "COFFEE", "SUGAR", "COCOA"}

# Popular US stocks outside the S&P 500 (universe group "Populer"), by GICS sector.
US_SECTORS = {
    "Information Technology": "ARM ASML SHOP MDB TEAM ZS GFS MSTR SNOW NET TSM IONQ SOUN CRWV SAP PATH U DOCU",
    "Consumer Discretionary": "PDD MELI RIVN LCID BABA NIO SE DKNG GME TM SONY JD LI XPEV CHWY ETSY W",
    "Communication Services": "RBLX SPOT ROKU ASTS BIDU BILI PINS SNAP",
    "Financials": "SOFI AFRM UPST NU CRCL MARA RIOT CLSK HUT",
    "Health Care": "AZN HIMS NVO",
    "Industrials": "RKLB SMR GRAB LYFT",
    "Utilities": "OKLO",
    "Consumer Staples": "CELH",
}

CRYPTO_CATEGORIES = {
    "Bitcoin & PoW": "BTC BCH BSV LTC ETC KAS DASH",
    "Layer 1": "ETH SOL ADA AVAX TRX NEAR ICP DOT HBAR ALGO ATOM XTZ SEI TIA VET FLR XDC XPL",
    "Pembayaran": "XRP XLM",
    "Layer 2": "OP STRK",
    "Token Exchange": "BNB LEO CRO OKB HTX BGB GT KCS WBT",
    "DeFi & RWA": "AAVE ENA ONDO JST CAKE RAY CRV PENDLE LDO ETHFI GNO OHM SUN NEXO INJ",
    "Infrastruktur & Oracle": "LINK PYTH FIL QNT BTT",
    "AI": "RENDER FET VIRTUAL WLD KITE",
    "Privasi": "ZEC XMR BDX NIGHT",
    "Meme": "DOGE SHIB BONK",
    "Stablecoin": "USDGO OUSD USD0 A7A5 YLDS",
}

_US = {sym: sector for sector, syms in US_SECTORS.items() for sym in syms.split()}
_CRYPTO = {f"{coin}USD": cat for cat, coins in CRYPTO_CATEGORIES.items() for coin in coins.split()}

# Display order per market; blocks not listed follow, largest first.
BLOCK_ORDER = {
    "forex": ["Mayor", "Cross", "Eksotis", "Logam", "Energi", "Pertanian", "Indeks"],
    "saham-as": ["Indeks & Yield"],
    "saham-idx": ["Indeks"],
    "kripto": list(CRYPTO_CATEGORIES) + ["Lainnya"],
}


def block_of(inst: Instrument) -> str:
    cls, sym = inst.asset_class, inst.symbol
    if cls == "forex":
        if sym in FX_MAJORS:
            return "Mayor"
        return "Eksotis" if inst.group == "Eksotis" else "Cross"
    if cls == "commodity":
        return "Logam" if sym in METALS else "Energi" if sym in ENERGY else "Pertanian" if sym in AGRI else "Komoditas"
    if cls == "index":
        return {"forex": "Indeks", "saham-idx": "Indeks"}.get(inst.market, "Indeks & Yield")
    if cls == "crypto":
        return _CRYPTO.get(sym, "Lainnya")
    if cls == "us":
        if inst.group == "ETF":
            return "ETF"
        return inst.sector if inst.sector and inst.sector not in ("Populer", "ETF") else _US.get(sym, "Lainnya")
    return inst.sector or "Lainnya"
