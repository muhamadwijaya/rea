"""Optional AI narrative analysis, grounded in the engine's facts, through a selectable provider.

Providers: OpenRouter free models with automatic rotation (default), Anthropic Claude, or any
OpenAI-compatible endpoint. API keys arrive per request from the gateway (or the provider's
environment variable); they are never stored or logged by the engine.
"""

from __future__ import annotations

import os
import time

from .. import instruments
from . import claude, custom, openrouter
from .common import SYSTEM, AiFailed, AiNotConfigured, facts, user_message

PROVIDERS = {"openrouter": openrouter.NAME, "anthropic": claude.NAME, "custom": custom.NAME}
ENV_KEYS = {"openrouter": "OPENROUTER_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "custom": "AI_CUSTOM_API_KEY"}

__all__ = ["PROVIDERS", "AiFailed", "AiNotConfigured", "analyze", "models"]


def analyze(symbol: str, news: list[dict], provider: str | None = None, api_key: str | None = None,
            model: str | None = None, base_url: str | None = None) -> dict:
    provider = (provider or "openrouter").strip().lower()
    if provider not in PROVIDERS:
        raise AiNotConfigured(f"Provider AI tidak dikenal: {provider!r}. Pilihan: {', '.join(PROVIDERS)}")
    key = api_key or os.environ.get(ENV_KEYS[provider]) or None
    if provider == "openrouter" and not key:
        raise AiNotConfigured("Analisa AI belum aktif: masukkan OpenRouter API key di pengaturan AI (gratis di openrouter.ai/keys).")
    started = time.perf_counter()
    inst = instruments.resolve(symbol)
    fact = facts(inst, news)
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
        "facts": {"ruleBasedOutlook": fact["ruleBasedOutlook"], "releases": len(fact["economicReleases"]),
                  "headlines": len(fact["headlines"]), "accuracy": fact["ruleModelMeasuredAccuracy"]},
        "provider": provider,
        "providerName": PROVIDERS[provider],
        "model": answer.model,
        "attempts": answer.attempts,
        "usage": answer.usage,
        "elapsedMs": int((time.perf_counter() - started) * 1000),
        "generatedAt": int(time.time()),
    }


def models(provider: str = "openrouter") -> dict:
    if provider != "openrouter":
        return {"provider": provider, "models": []}
    return openrouter.status()
