"""FastAPI entry point of the Python analysis engine.

Run:  python -m uvicorn app.main:app --host 127.0.0.1 --port 8765
The C# gateway is the only intended client; the engine binds to localhost.
"""

from __future__ import annotations

import logging
import time

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import analysis, direction, instruments, market_data, screener, sentiment
from .errors import EngineError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="SMC Terminal Engine", version="1.0.0")
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
        "timeframes": list(market_data.TIMEFRAMES),
    }


@app.get("/instruments")
def list_instruments(market: str = "all") -> dict:
    return {"market": market, "instruments": [i.to_json() for i in instruments.by_market(market)]}


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
def screen(market: str = "all", tf: str = "4h") -> dict:
    return screener.screen(market, tf)


@app.get("/direction")
def market_direction() -> dict:
    return direction.market_direction()


class NewsItem(BaseModel):
    id: str
    title: str = ""
    summary: str = ""


class SentimentRequest(BaseModel):
    items: list[NewsItem] = Field(default_factory=list, max_length=1000)


@app.post("/sentiment")
def score_sentiment(body: SentimentRequest) -> dict:
    return {"items": sentiment.score_items([i.model_dump() for i in body.items])}
