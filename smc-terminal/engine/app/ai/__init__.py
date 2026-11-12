"""Optional AI narrative analysis, grounded in the engine's facts, through a selectable provider.

Providers: OpenRouter free models with automatic rotation (default), Anthropic Claude, or any
OpenAI-compatible endpoint. API keys arrive per request from the gateway (or the provider's
environment variable); they are never stored or logged by the engine.
"""

from __future__ import annotations

import os
import time

from .. import instruments, signals, voice
from . import claude, custom, openrouter
from .common import SYSTEM, AiFailed, AiNotConfigured, confluence, facts, user_message

PROVIDERS = {"openrouter": openrouter.NAME, "anthropic": claude.NAME, "custom": custom.NAME}
ENV_KEYS = {"openrouter": "OPENROUTER_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "custom": "AI_CUSTOM_API_KEY"}

__all__ = ["PROVIDERS", "AiFailed", "AiNotConfigured", "analyze", "analyze_news", "models"]
FOCUS_CHARS = 6000  # article text given to the model (the reader gets the whole text)
NEWS_CACHE_SECONDS = 1800
_news_cache: dict[tuple, tuple[float, dict]] = {}


def analyze(symbol: str, news: list[dict], provider: str | None = None, api_key: str | None = None,
            model: str | None = None, base_url: str | None = None, focus: dict | None = None) -> dict:
    provider = (provider or "openrouter").strip().lower()
    if provider not in PROVIDERS:
        raise AiNotConfigured(f"Provider AI tidak dikenal: {provider!r}. Pilihan: {', '.join(PROVIDERS)}")
    key = api_key or os.environ.get(ENV_KEYS[provider]) or None
    if provider == "openrouter" and not key:
        raise AiNotConfigured("Analisa AI belum aktif: masukkan OpenRouter API key di pengaturan AI (gratis di openrouter.ai/keys).")
    started = time.perf_counter()
    inst = instruments.resolve(symbol)
    fact, smc = facts(inst, news, focus)
    user = user_message(fact)
    if provider == "openrouter":
        answer = openrouter.run(SYSTEM, user, key, model)
    elif provider == "anthropic":
        answer = claude.run(SYSTEM, user, key)
    else:
        answer = custom.run(SYSTEM, user, key, base_url, model)
    return {
        "symbol": inst.symbol,
        "analysis": answer.analysis,
        "confluence": confluence(answer.analysis, smc),
        "smc": smc,
        "facts": {"releases": len(fact["economicReleases"]), "upcoming": len(fact["upcomingEconomicEvents"]),
                  "headlines": len(fact["headlines"]), "reports": bool(fact["companyReports"]),
                  "performance": fact["performance"], "accuracy": fact["ruleModelMeasuredAccuracy"],
                  "notes": fact["dataNotes"]},
        "provider": provider,
        "providerName": PROVIDERS[provider],
        "model": answer.model,
        "attempts": answer.attempts,
        "usage": answer.usage,
        "elapsedMs": int((time.perf_counter() - started) * 1000),
        "generatedAt": int(time.time()),
    }


def analyze_news(symbol: str, item: dict, news: list[dict], provider: str | None = None, api_key: str | None = None,
                 model: str | None = None, base_url: str | None = None) -> dict:
    """One news article, analysed before it is read aloud: its text (fetched from the link) is the AI's main fact,
    and the AI direction is turned into a signal per timeframe where that timeframe has momentum. Without a
    working AI the direction comes from the article's tagged sentiment, and the answer says so."""
    inst = instruments.resolve(symbol)
    link = str(item.get("link") or "").strip()
    cache_key = (inst.symbol, link or item.get("title"), provider, model)
    hit = _news_cache.get(cache_key)
    if hit and hit[0] > time.time():
        return hit[1]
    art = voice.article(link, str(item.get("title") or ""), str(item.get("summary") or ""))
    focus = {"title": item.get("title"), "source": item.get("source"), "published": item.get("published"), "link": link,
             "text": art["text"][:FOCUS_CHARS], "fullText": art["source"] == "article"}
    try:
        result = analyze(inst.symbol, news, provider, api_key, model, base_url, focus=focus)
        direction, basis, error = result["analysis"]["direction"], "ai", None
    except (AiNotConfigured, AiFailed) as exc:
        sign = next((t.get("sign") or 1 for t in item.get("symbols") or [] if t.get("symbol") == inst.symbol), 1)
        score = float(item.get("sentiment") or 0) * sign
        direction = "naik" if score > 0.15 else "turun" if score < -0.15 else "netral"
        result, basis = {"symbol": inst.symbol}, "sentiment"
        error = {"code": exc.code, "message": getattr(exc, "message", str(exc))}
    out = {**result, "symbol": inst.symbol, "direction": direction, "directionBasis": basis, "aiError": error,
           "article": {"title": item.get("title"), "link": link, "source": item.get("source"), "published": item.get("published"),
                       "text": art["text"], "fullText": focus["fullText"], "reason": art.get("reason"),
                       "truncated": bool(art.get("truncated"))},
           "timeframes": signals.for_direction(inst, direction)}
    if basis == "ai":
        _news_cache[cache_key] = (time.time() + NEWS_CACHE_SECONDS, out)
        for k in [k for k, (until, _) in _news_cache.items() if until < time.time()]:
            _news_cache.pop(k, None)
    return out


def models(provider: str = "openrouter") -> dict:
    if provider != "openrouter":
        return {"provider": provider, "models": []}
    return openrouter.status()
