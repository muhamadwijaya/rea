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

from .. import analysis, instruments, outlook, profile, training
from ..errors import EngineError

SYSTEM = """Kamu adalah analis fundamental pasar keuangan. Tentukan arah harga satu aset (naik / turun / netral)
berdasarkan HANYA fakta JSON yang diberikan pengguna:
- berita terkait aset beserta sentimennya,
- kalender ekonomi: rilis data (actual vs forecast dan reaksi pasar nyata) serta jadwal rilis mendatang,
- laporan nyata emiten: pendapatan & laba kuartalan, kejutan EPS, valuasi, profitabilitas, utang,
- kinerja harga nyata: return 1 minggu s/d 1 tahun, posisi terhadap high/low 52 minggu, relatif ke benchmark,
- rencana & ekspektasi ke depan: jadwal laporan berikutnya dan konsensusnya, target harga & rekomendasi analis,
  serta berita rencana perusahaan / kebijakan.
Jika fakta berisi focusArticle (berita yang sedang dibahas: judul, sumber, waktu, link dan isinya), nilai
terutama dampak berita itu terhadap aset — arah, seberapa kuat dan berapa lama — dengan fakta lain sebagai
konteks, dan jelaskan di summary bagaimana berita itu menggerakkan aset. Bila isi lengkap tidak tersedia
(fullText false), nilai dari judul dan ringkasannya saja dan sebutkan keterbatasan itu di dataQuality.
Jangan memakai analisa teknikal atau Smart Money Concept: engine akan mencocokkan arahmu dengan analisa SMC
secara terpisah, jadi penilaianmu harus independen dari chart.

Aturan:
- Jangan mengarang angka, peristiwa, atau berita yang tidak ada di fakta. Jika data kurang, katakan.
- Pasar tidak pasti. Nilai keyakinan (0-100) harus mencerminkan kekuatan dan keselarasan bukti,
  bukan janji. Bukti saling bertentangan atau sedikit = keyakinan rendah (di bawah 55).
- Jelaskan pendorong utama dan risiko yang bisa membatalkan skenario, rujuk fakta spesifik
  (misal "CPI AS 0.4% vs forecast 0.3%", "laba Q3 naik 9% YoY, EPS di atas konsensus 4%").
- Tulis dalam Bahasa Indonesia yang ringkas dan profesional. Ini bukan saran investasi.

Format jawaban: HANYA satu objek JSON (tanpa markdown, tanpa teks lain) dengan kunci:
direction ("naik" | "turun" | "netral"), confidence (bilangan bulat 0-100), horizon ("intraday" |
"1-3 hari" | "1-2 minggu"), summary (string), fundamentalView (string: ringkasan laporan, kinerja dan
rencana ke depan), drivers (array string), risks (array string), catalysts (array string: peristiwa
mendatang yang bisa menggerakkan harga, sertakan tanggal), keyLevels (array string: level penting dari
fakta, mis. target analis atau high/low 52 minggu), invalidation (string), dataQuality (string:
kecukupan & kesegaran data)."""

SCHEMA = {
    "type": "object",
    "properties": {
        "direction": {"type": "string", "enum": ["naik", "turun", "netral"]},
        "confidence": {"type": "integer", "description": "0-100, kekuatan bukti, bukan kepastian"},
        "horizon": {"type": "string", "enum": ["intraday", "1-3 hari", "1-2 minggu"]},
        "summary": {"type": "string"},
        "fundamentalView": {"type": "string"},
        "drivers": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "catalysts": {"type": "array", "items": {"type": "string"}},
        "keyLevels": {"type": "array", "items": {"type": "string"}},
        "invalidation": {"type": "string"},
        "dataQuality": {"type": "string", "description": "kecukupan dan kesegaran data yang tersedia"},
    },
    "required": ["direction", "confidence", "horizon", "summary", "fundamentalView", "drivers", "risks", "catalysts",
                 "keyLevels", "invalidation", "dataQuality"],
    "additionalProperties": False,
}

# Confluence with SMC: the AI's fundamental call is compared with the SMC multi-timeframe bias.
SMC_MIN_SCORE = 15  # |MTF score| below this is "no clear SMC direction"
MAX_ADJUSTMENT = 20
CONFIDENCE_CAP = 95  # agreement raises confidence, it never turns it into certainty
SIGNAL_MIN_CONFIDENCE = 55


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


def _event(e: dict, upcoming: bool) -> dict:
    keys = ("title", "currency", "date", "impact", "forecast", "previous")
    keys += ("scenario",) if upcoming else ("actual", "surprise", "prediction", "reaction", "verdict")
    return {k: e.get(k) for k in keys if e.get(k) is not None}


def facts(inst: instruments.Instrument, news: list[dict], focus: dict | None = None) -> tuple[dict, dict]:
    """(facts for the AI, SMC view for the confluence step). SMC is deliberately not in the AI facts.
    `focus`: one article the analysis is about (title, source, published, link, text, fullText)."""
    view = outlook.outlook(inst, news)
    detail = analysis.to_json(analysis.run(inst, "4h"), include_series=False)
    funds = outlook.fundamentals() or {}
    currencies = {inst.symbol[:3], inst.symbol[3:]} if inst.asset_class == "forex" else {"USD"}
    if inst.market == "saham-idx":
        currencies |= {"IDR"}
    now = time.time()
    events = [e for e in funds.get("events", []) if e.get("currency") in currencies and e.get("impact") in ("High", "Medium")]
    released = [_event(e, False) for e in events if e.get("status") == "released"][-12:]
    upcoming = [_event(e, True) for e in events if e.get("status") != "released" and (e.get("ts") or 0) <= now + 7 * 86400][:12]
    prof = profile.profile(inst)
    headlines = [
        {"time": n.get("published"), "source": n.get("source"), "title": n.get("title"),
         "sentiment": n.get("sentimentLabel"), "score": n.get("sentiment"),
         "sign": next((t.get("sign") for t in n.get("symbols", []) if t.get("symbol") == inst.symbol), 1)}
        for n in news[:25]
    ]
    fact = {
        "asset": {"symbol": inst.symbol, "name": inst.name, "market": inst.market, "assetClass": inst.asset_class,
                  "price": detail["price"], "asOf": detail["asOf"]},
        "performance": prof.get("performance"),
        "companyReports": prof.get("reports"),
        "economicReleases": released,
        "upcomingEconomicEvents": upcoming,
        "currencyFundamentalScores": funds.get("currencies"),
        "ruleModelMeasuredAccuracy": funds.get("accuracy"),
        "ruleScores": {k: v for k, v in view["components"].items() if k != "smc"},
        "headlines": headlines,
        "dataNotes": prof.get("notes", []),
        "now": int(now),
    }
    if focus:
        fact["focusArticle"] = focus
    horizons = {}
    for horizon, tfs in analysis.HORIZON_TIMEFRAMES.items():
        mtf = view["mtf"] if tfs == ("1d", "4h", "1h") else analysis.mtf(inst, tfs)
        horizons[horizon] = {"score": mtf["score"], "label": mtf["label"], "aligned": mtf["aligned"], "tfs": list(tfs),
                             "timeframes": [{"tf": t.get("tf"), "score": t.get("score"), "label": t.get("label")}
                                            for t in mtf["timeframes"]]}
    smc = {"horizons": horizons, "plan4h": detail.get("plan"),
           "reliability": {h: training.reliability(inst.symbol, list(tfs)) for h, tfs in analysis.HORIZON_TIMEFRAMES.items()}}
    return fact, smc


def confluence(answer: dict, smc: dict) -> dict:
    """Raise the AI's confidence when its fundamental call agrees with the SMC bias, lower it on conflict.

    The SMC view compared is the one for the AI's horizon (intraday: 1H/15M/5M, 1-3 hari: 4H/1H/15M,
    1-2 minggu: 1D/4H/1H). `smc` is either {"horizons": {...}} or a single view.
    """
    horizons = smc.get("horizons") or {}
    view = horizons.get(answer["horizon"]) or horizons.get("1-2 minggu") or smc
    ai_dir = {"naik": 1, "turun": -1}.get(answer["direction"], 0)
    score = float(view.get("score") or 0)
    smc_dir = 0 if abs(score) < SMC_MIN_SCORE else (1 if score > 0 else -1)
    weight = min(MAX_ADJUSTMENT, 5 + abs(score) * 0.2)
    # SMC that has been accurate on this asset (virtual trading) counts more, inaccurate SMC less
    rel = (smc.get("reliability") or {}).get(answer["horizon"])
    weight = round(min(MAX_ADJUSTMENT, weight * (rel["factor"] if rel else 1.0)))
    base = answer["confidence"]
    if ai_dir and smc_dir and ai_dir == smc_dir:
        status, adj = "match", weight
        note = f"Fundamental dan SMC searah: keyakinan naik {weight} poin."
    elif ai_dir and smc_dir:
        status, adj = "conflict", -weight
        note = f"Fundamental dan SMC berlawanan: keyakinan turun {weight} poin; tunggu konfirmasi."
    else:
        status, adj = "neutral", 0
        note = "SMC belum memberi arah jelas." if not smc_dir else "AI menilai netral; SMC tidak mengubah keyakinan."
    if rel:
        note += f" Akurasi SMC aset ini {rel['winRate'] * 100:.0f}% dari {rel['trades']} trade virtual (bobot ×{rel['factor']})."
    final = max(5, min(CONFIDENCE_CAP, base + adj))
    signal = ("BELI" if ai_dir > 0 else "JUAL") if ai_dir and final >= SIGNAL_MIN_CONFIDENCE else "NETRAL"
    return {"status": status, "aiDirection": answer["direction"], "smcScore": round(score, 1), "smcLabel": view.get("label"),
            "smcAligned": bool(view.get("aligned")), "smcTimeframes": [t.upper() for t in view.get("tfs", [])],
            "horizon": answer["horizon"], "base": base, "adjustment": adj, "final": final, "signal": signal, "note": note,
            "smcReliability": rel}


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
        "fundamentalView": _text(raw.get("fundamentalView")),
        "drivers": _texts(raw.get("drivers")),
        "risks": _texts(raw.get("risks")),
        "catalysts": _texts(raw.get("catalysts")),
        "keyLevels": _texts(raw.get("keyLevels")),
        "invalidation": _text(raw.get("invalidation")) or "-",
        "dataQuality": _text(raw.get("dataQuality")) or "-",
    }
