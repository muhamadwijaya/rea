"""Optional narrative analysis by Claude, grounded in the engine's facts.

The engine gathers the facts (price, SMC multi-timeframe bias, fundamental releases with actual vs
forecast and the measured market reaction, tagged headlines with sentiment, the rule model's measured
accuracy) and asks Claude for a structured, Indonesian-language assessment that may only use those
facts. The API key is supplied per request by the gateway (or ANTHROPIC_API_KEY); it is never stored
or logged here.
"""

from __future__ import annotations

import json
import time

import anthropic

from . import analysis, instruments, outlook
from .errors import EngineError

MODEL = "claude-opus-5-5"

SYSTEM = """Kamu adalah analis pasar keuangan. Tugasmu menilai kemungkinan arah harga satu aset
berdasarkan HANYA fakta JSON yang diberikan pengguna: harga, bias Smart Money Concept multi-timeframe,
rilis data ekonomi (actual vs forecast dan reaksi pasar nyata), headline berita beserta sentimennya,
serta akurasi terukur model aturan.

Aturan:
- Jangan mengarang angka, peristiwa, atau berita yang tidak ada di fakta. Jika data kurang, katakan.
- Pasar tidak pasti. Nilai keyakinan (0-100) harus mencerminkan kekuatan dan keselarasan bukti,
  bukan janji. Bukti saling bertentangan atau sedikit = keyakinan rendah (di bawah 55).
- Jelaskan pendorong utama dan risiko yang bisa membatalkan skenario, rujuk fakta spesifik
  (misal "CPI AS 0.4% vs forecast 0.3%", "CHoCH bearish 4H di 1.0850").
- Tulis dalam Bahasa Indonesia yang ringkas dan profesional. Ini bukan saran investasi."""

SCHEMA = {
    "type": "object",
    "properties": {
        "direction": {"type": "string", "enum": ["naik", "turun", "netral"]},
        "confidence": {"type": "integer", "description": "0-100, kekuatan bukti, bukan kepastian"},
        "horizon": {"type": "string", "enum": ["intraday", "1-3 hari", "1-2 minggu"]},
        "summary": {"type": "string"},
        "drivers": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "keyLevels": {"type": "array", "items": {"type": "string"}},
        "invalidation": {"type": "string"},
        "dataQuality": {"type": "string", "description": "kecukupan dan kesegaran data yang tersedia"},
    },
    "required": ["direction", "confidence", "horizon", "summary", "drivers", "risks", "keyLevels", "invalidation", "dataQuality"],
    "additionalProperties": False,
}


class AiNotConfigured(EngineError):
    status = 400
    code = "ai_not_configured"


class AiFailed(EngineError):
    status = 502
    code = "ai_failed"


def _facts(inst: instruments.Instrument, news: list[dict]) -> dict:
    view = outlook.outlook(inst, news)
    detail = analysis.to_json(analysis.run(inst, "4h"), include_series=False)
    funds = outlook.fundamentals() or {}
    currencies = {inst.symbol[:3], inst.symbol[3:]} if inst.asset_class == "forex" else {"USD"}
    if inst.market == "saham-idx":
        currencies |= {"IDR"}
    releases = [
        {k: e.get(k) for k in ("title", "currency", "date", "impact", "forecast", "previous", "actual", "surprise",
                               "prediction", "reaction", "verdict", "status")}
        for e in funds.get("events", []) if e.get("currency") in currencies and e.get("impact") in ("High", "Medium")
    ][-20:]
    headlines = [
        {"time": n.get("published"), "source": n.get("source"), "title": n.get("title"),
         "sentiment": n.get("sentimentLabel"), "score": n.get("sentiment"),
         "sign": next((t.get("sign") for t in n.get("symbols", []) if t.get("symbol") == inst.symbol), 1)}
        for n in news[:25]
    ]
    return {
        "asset": {"symbol": inst.symbol, "name": inst.name, "market": inst.market, "price": detail["price"],
                  "asOf": detail["asOf"]},
        "smc4h": {"bias": detail["bias"], "zones": detail["smc"].get("zones"), "plan": detail["plan"],
                  "narrative": detail["narrative"], "liquidity": detail["smc"]["liquidity"]},
        "multiTimeframe": view["mtf"],
        "ruleBasedOutlook": {k: view[k] for k in ("score", "label", "confidence", "components", "notes")},
        "economicReleases": releases,
        "currencyFundamentalScores": funds.get("currencies"),
        "ruleModelMeasuredAccuracy": funds.get("accuracy"),
        "headlines": headlines,
        "now": int(time.time()),
    }


def analyze(symbol: str, news: list[dict], api_key: str | None) -> dict:
    inst = instruments.resolve(symbol)
    facts = _facts(inst, news)
    try:
        client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
    except anthropic.AnthropicError as exc:
        raise AiNotConfigured("Analisa AI belum aktif: masukkan Anthropic API key di pengaturan AI.") from exc
    started = time.perf_counter()
    try:
        response = client.with_options(timeout=180.0).beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
            system=SYSTEM,
            messages=[{"role": "user", "content": "Fakta (JSON):\n" + json.dumps(facts, ensure_ascii=False, default=str)}],
        )
    except anthropic.AuthenticationError as exc:
        raise AiNotConfigured("API key Anthropic ditolak (tidak valid atau dicabut).") from exc
    except anthropic.PermissionDeniedError as exc:
        raise AiFailed("API key tidak punya akses ke model ini.") from exc
    except anthropic.RateLimitError as exc:
        raise AiFailed("Batas pemakaian API tercapai, coba lagi sebentar lagi.") from exc
    except anthropic.APIStatusError as exc:
        raise AiFailed(f"Claude API error {exc.status_code}: {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise AiFailed("Tidak dapat terhubung ke Claude API (periksa internet).") from exc
    except TypeError as exc:
        # The SDK raises TypeError when no API key / token / profile can be resolved.
        if "authentication" in str(exc).lower():
            raise AiNotConfigured("Analisa AI belum aktif: masukkan Anthropic API key di pengaturan AI.") from exc
        raise

    if response.stop_reason == "refusal":
        raise AiFailed("Permintaan analisa ditolak oleh model.")
    if response.stop_reason == "max_tokens":
        raise AiFailed("Jawaban AI terpotong; coba lagi.")
    text = next((b.text for b in response.content if b.type == "text"), None)
    if text is None:
        raise AiFailed("Jawaban AI kosong.")
    result = json.loads(text)
    result["confidence"] = max(0, min(100, int(result.get("confidence", 0))))
    return {
        "symbol": inst.symbol,
        "analysis": result,
        "facts": {"ruleBasedOutlook": facts["ruleBasedOutlook"], "releases": len(facts["economicReleases"]),
                  "headlines": len(facts["headlines"]), "accuracy": facts["ruleModelMeasuredAccuracy"]},
        "model": response.model,
        "usage": {"input": response.usage.input_tokens, "output": response.usage.output_tokens},
        "elapsedMs": int((time.perf_counter() - started) * 1000),
        "generatedAt": int(time.time()),
    }
