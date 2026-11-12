"""FastAPI entry point of the Python analysis engine.

Run:  python -m uvicorn app.main:app --host 127.0.0.1 --port 8765
The C# gateway is the only intended client; the engine binds to localhost.
"""

from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, Query, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from . import ai, analysis, background, benefits, company, direction, fundamental, instruments, market_data, outlook, screener, sentiment, training, voice
from .errors import EngineError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

@asynccontextmanager
async def lifespan(_: FastAPI):
    background.start()
    if os.environ.get("SMC_BACKGROUND", "1") != "0":
        benefits.start()
        training.start()
    yield


app = FastAPI(title="SHARX INVEST Engine", version="3.0.0", lifespan=lifespan)
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
    published: str | None = None
    link: str | None = None
    source: str | None = None


class SentimentRequest(BaseModel):
    items: list[NewsItem] = Field(default_factory=list, max_length=1000)


@app.post("/sentiment")
def score_sentiment(body: SentimentRequest) -> dict:
    raw = [i.model_dump() for i in body.items]
    scored = sentiment.score_items(raw)
    # benefit / corporate-action headlines (dividend, buyback, airdrop, ...) are kept per tagged asset
    benefits.record_news([{**r, "symbols": s.get("symbols", [])} for r, s in zip(raw, scored)])
    return {"items": scored}


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


class NewsAiRequest(BaseModel):
    symbol: str
    item: dict
    news: list[dict] = Field(default_factory=list, max_length=200)
    provider: str = "openrouter"
    model: str | None = Field(default=None, max_length=200)
    baseUrl: str | None = Field(default=None, max_length=500)


@app.post("/ai/news")
def ai_news(body: NewsAiRequest, x_ai_key: str | None = Header(default=None)) -> dict:
    """One article analysed before it is read aloud (key in the X-AI-Key header, never logged)."""
    return ai.analyze_news(body.symbol, body.item, body.news, body.provider, x_ai_key, body.model, body.baseUrl)


class TtsRequest(BaseModel):
    text: str = Field(max_length=5000)
    lang: str = "id"  # main language of the text
    rate: float = 1.0
    persona: str = "jarvis"  # "jarvis" | "natural"
    mixed: bool = True  # English words / phrases inside Indonesian text read in English
    engine: str = "piper"  # "supertonic" (natural, Indonesian + English in one voice) | "piper"
    voice: str = Field(default="M1", max_length=8)  # Supertonic voice style


class VoiceSetupRequest(BaseModel):
    items: list[str] = Field(default_factory=lambda: ["id", "engb", "stt"], max_length=5)


class ArticleRequest(BaseModel):
    url: str = Field(max_length=2000)
    title: str = Field(default="", max_length=1000)
    summary: str = Field(default="", max_length=5000)


@app.get("/voice/status")
def voice_status() -> dict:
    return voice.status()


@app.post("/voice/setup")
def voice_setup(body: VoiceSetupRequest) -> dict:
    return voice.setup(body.items)


@app.post("/voice/tts")
def voice_tts(body: TtsRequest) -> Response:
    return Response(content=voice.speak(body.text, body.lang, body.rate, body.persona, body.mixed, body.engine, body.voice),
                    media_type="audio/wav")


@app.post("/voice/stt")
async def voice_stt(request: Request, lang: str = "id") -> dict:
    audio = await request.body()
    if len(audio) > 3_000_000:
        raise voice.VoiceUnavailable("Rekaman terlalu panjang.")
    return await run_in_threadpool(voice.transcribe, audio, lang)


@app.post("/news/article")
def news_article(body: ArticleRequest) -> dict:
    return voice.article(body.url, body.title, body.summary)


@app.get("/training/summary")
def training_summary() -> dict:
    return training.summary()


@app.get("/training/asset")
def training_asset(symbol: str, tf: str = "1h") -> dict:
    market_data.timeframe(tf)  # validates the timeframe
    return training.asset(symbol, tf)


@app.get("/training/chart")
def training_chart(symbol: str, tf: str = "1h") -> dict:
    market_data.timeframe(tf)  # validates the timeframe
    return training.chart(symbol, tf)


@app.get("/benefits")
def benefit_calendar(market: str = "all") -> dict:
    return benefits.view(market)


@app.get("/company")
def company_report(symbol: str) -> dict:
    inst = instruments.resolve(symbol)
    data = company.data(inst)
    if data is None:
        return {"symbol": inst.symbol, "assetClass": inst.asset_class, "unavailable": True,
                "notes": ["Laporan perusahaan hanya untuk saham, ETF dan kripto."]}
    return data


@app.get("/live")
def live(symbol: str) -> dict:
    return market_data.live(instruments.resolve(symbol))


@app.get("/ai/models")
def ai_models(provider: str = "openrouter") -> dict:
    return ai.models(provider)
