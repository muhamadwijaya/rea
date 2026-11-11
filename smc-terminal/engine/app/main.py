"""FastAPI entry point of the Python analysis engine.

Run:  python -m uvicorn app.main:app --host 127.0.0.1 --port 8765
The C# gateway is the only intended client; the engine binds to localhost.
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import ai, analysis, background, direction, fundamental, instruments, market_data, outlook, screener, sentiment
from .errors import EngineError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

@asynccontextmanager
async def lifespan(_: FastAPI):
    background.start()
    yield


app = FastAPI(title="SMC Terminal Engine", version="2.2.0", lifespan=lifespan)
STARTED = time.time()


@app.exception_handler(EngineError)
async def _engine_error(_: Request, exc: EngineError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content=exc.to_json())


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "uptime": int(time.time() - STARTED),
        "instruments": len(instruments.watchlist()),
        "universe": len(instruments.universe()),
        "timeframes": list(market_data.TIMEFRAMES),
    }


@app.get("/instruments")
def list_instruments(market: str = "all", scope: str = "core") -> dict:
    return {"market": market, "scope": scope, "instruments": [i.to_json() for i in instruments.by_market(market, scope)]}


@app.get("/quotes")
def quotes(market: str = "all") -> dict:
    if market != "all":
        instruments.by_market(market)  # validates the market name
    return background.quotes(market)


@app.get("/resolve")
def resolve(q: str) -> dict:
    return instruments.resolve(q).to_json()


@app.get("/analyze")
def analyze(
    symbol: str,
    tf: str = "1h",
    bars: int = Query(500, ge=100, le=market_data.MAX_BARS),
    mtf: bool = True,
) -> dict:
    inst = instruments.resolve(symbol)
    result = analysis.to_json(analysis.run(inst, tf), bars_out=bars)
    if mtf:
        result["mtf"] = analysis.mtf(inst)
    return result


@app.get("/mtf")
def multi_timeframe(symbol: str) -> dict:
    inst = instruments.resolve(symbol)
    return {"instrument": inst.to_json(), **analysis.mtf(inst)}


@app.get("/screener")
def screen(market: str = "all", tf: str = "4h", scope: str = "core") -> dict:
    if scope == "all":
        # Full universe: served from the background 1D scan (never blocks on hundreds of downloads).
        instruments.by_market(market, "all")
        return background.universe_screen(market, tf)
    return screener.screen(market, tf)


@app.get("/direction")
def market_direction() -> dict:
    return direction.market_direction()


class NewsItem(BaseModel):
    id: str
    title: str = ""
    summary: str = ""
    lang: str = "en"
    category: str = ""


class SentimentRequest(BaseModel):
    items: list[NewsItem] = Field(default_factory=list, max_length=1000)


@app.post("/sentiment")
def score_sentiment(body: SentimentRequest) -> dict:
    return {"items": sentiment.score_items([i.model_dump() for i in body.items])}


class FundamentalRequest(BaseModel):
    events: list[dict] = Field(default_factory=list, max_length=2000)
    news: list[dict] = Field(default_factory=list, max_length=5000)


@app.post("/fundamental")
def analyze_fundamentals(body: FundamentalRequest) -> dict:
    result = fundamental.analyze(body.events, body.news)
    outlook.set_fundamentals(result)
    return result


@app.get("/fundamental")
def latest_fundamentals() -> dict:
    return outlook.fundamentals() or {"events": [], "accuracy": None, "currencies": {}, "status": "warming"}


class OutlookRequest(BaseModel):
    symbols: list[str] = Field(default_factory=list, max_length=200)
    news: dict[str, list[dict]] = Field(default_factory=dict)


@app.post("/outlook")
def asset_outlook(body: OutlookRequest) -> dict:
    return {"generatedAt": int(time.time()), "rows": outlook.batch(body.symbols, body.news)}


class AiRequest(BaseModel):
    symbol: str
    news: list[dict] = Field(default_factory=list, max_length=200)
    provider: str = "openrouter"
    model: str | None = Field(default=None, max_length=200)
    baseUrl: str | None = Field(default=None, max_length=500)


@app.post("/ai/analyze")
def ai_analyze(body: AiRequest, x_ai_key: str | None = Header(default=None)) -> dict:
    """Key arrives in the X-AI-Key header (never in the body, never logged)."""
    return ai.analyze(body.symbol, body.news, body.provider, x_ai_key, body.model, body.baseUrl)


@app.get("/ai/models")
def ai_models(provider: str = "openrouter") -> dict:
    return ai.models(provider)
