"""Provider-independent parts of the AI analysis: prompt, output contract, facts and result parsing.

Every provider receives the same system prompt and the same facts JSON and must return the same
structured assessment (SCHEMA). Providers without strict structured output get the contract in the
prompt; their text is parsed leniently and validated here, so the UI only ever sees normalized results.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

from .. import analysis, instruments, outlook
from ..errors import EngineError

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
- Tulis dalam Bahasa Indonesia yang ringkas dan profesional. Ini bukan saran investasi.

Format jawaban: HANYA satu objek JSON (tanpa markdown, tanpa teks lain) dengan kunci:
direction ("naik" | "turun" | "netral"), confidence (bilangan bulat 0-100), horizon ("intraday" |
"1-3 hari" | "1-2 minggu"), summary (string), drivers (array string), risks (array string),
keyLevels (array string), invalidation (string), dataQuality (string: kecukupan & kesegaran data)."""

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


@dataclass
class Answer:
    """A validated assessment plus where it came from."""

    analysis: dict
    model: str
    usage: dict = field(default_factory=lambda: {"input": 0, "output": 0})
    attempts: list[dict] = field(default_factory=list)


class InvalidAnswer(ValueError):
    """The model answered, but not with a usable assessment (reason is safe to show)."""


def facts(inst: instruments.Instrument, news: list[dict]) -> dict:
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


def user_message(fact_json: dict) -> str:
    return "Fakta (JSON):\n" + json.dumps(fact_json, ensure_ascii=False, default=str)


_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I)
_DIRECTIONS = {
    "naik": "naik", "bullish": "naik", "up": "naik", "long": "naik", "buy": "naik",
    "turun": "turun", "bearish": "turun", "down": "turun", "short": "turun", "sell": "turun",
    "netral": "netral", "neutral": "netral", "sideways": "netral", "flat": "netral",
}
_HORIZONS = ("intraday", "1-3 hari", "1-2 minggu")


def parse_answer(text: str | None) -> dict:
    """Parse and validate a model answer; tolerant of fences, reasoning tags and prose around the JSON."""
    if not text or not text.strip():
        raise InvalidAnswer("jawaban kosong")
    body = _FENCE.sub("", _THINK.sub("", text).strip()).strip()
    try:
        raw = json.loads(body)
    except json.JSONDecodeError:
        start, end = body.find("{"), body.rfind("}")
        if start < 0 or end <= start:
            raise InvalidAnswer("jawaban bukan JSON") from None
        try:
            raw = json.loads(body[start:end + 1])
        except json.JSONDecodeError:
            raise InvalidAnswer("JSON jawaban rusak") from None
    if not isinstance(raw, dict):
        raise InvalidAnswer("JSON jawaban bukan objek")
    return normalize(raw)


def _text(v) -> str:
    return v.strip() if isinstance(v, str) else ("" if v is None else str(v))


def _texts(v) -> list[str]:
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, list):
        return []
    return [t for t in (_text(x) for x in v) if t][:12]


def normalize(raw: dict) -> dict:
    direction = _DIRECTIONS.get(_text(raw.get("direction")).lower())
    if direction is None:
        raise InvalidAnswer(f"arah tidak valid: {_text(raw.get('direction'))[:20]!r}")
    summary = _text(raw.get("summary"))
    if not summary:
        raise InvalidAnswer("ringkasan kosong")
    try:
        confidence = int(round(float(raw.get("confidence", 0))))
    except (TypeError, ValueError):
        raise InvalidAnswer("keyakinan bukan angka") from None
    horizon = _text(raw.get("horizon"))
    return {
        "direction": direction,
        "confidence": max(0, min(100, confidence)),
        "horizon": horizon if horizon in _HORIZONS else "1-3 hari",
        "summary": summary,
        "drivers": _texts(raw.get("drivers")),
        "risks": _texts(raw.get("risks")),
        "keyLevels": _texts(raw.get("keyLevels")),
        "invalidation": _text(raw.get("invalidation")) or "-",
        "dataQuality": _text(raw.get("dataQuality")) or "-",
    }
