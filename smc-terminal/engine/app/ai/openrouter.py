"""OpenRouter provider: free models only, ranked, with automatic rotation.

The free-model list comes from OpenRouter's public model catalog (no key needed) and is cached. Each
analysis tries the best available free model; when a model is rate limited, unavailable, times out or
answers with something unusable, it is put on a cooldown and the next free model is tried, within a
time and attempt budget. Account-wide failures (key rejected, daily free quota used up) stop at once,
because another model cannot fix them. The last model that worked is tried first next time.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field

import httpx

from .common import SCHEMA, AiFailed, AiNotConfigured, Answer, InvalidAnswer, parse_answer
from .openai_compat import ChatResult, chat

log = logging.getLogger("smc.ai.openrouter")

NAME = "OpenRouter (model gratis)"
BASE_URL = "https://openrouter.ai/api/v1"
ROUTER = "openrouter/free"  # OpenRouter's own free-model router: the last resort
CATALOG_TTL = 30 * 60
MIN_CONTEXT = 32_000
MIN_PARAMS_B = 7.0  # tiny models produce poor multi-factor analysis
MAX_OUTPUT = 12_000
MAX_ATTEMPTS = 6
BUDGET_S = 200.0  # stays below the gateway's 240 s request timeout
ATTEMPT_TIMEOUT_S = 100.0
HEADERS = {"X-Title": "SMC Terminal"}


@dataclass(frozen=True)
class FreeModel:
    id: str
    name: str
    context: int
    max_output: int | None
    params: frozenset = field(default_factory=frozenset)
    created: int = 0

    @property
    def structured(self) -> bool:
        return "structured_outputs" in self.params

    @property
    def json_mode(self) -> bool:
        return "response_format" in self.params


def _is_zero(v) -> bool:
    try:
        return float(v) == 0.0
    except (TypeError, ValueError):
        return False


_SIZE = re.compile(r"(?<![a-z])(\d+(?:\.\d+)?)b(?![a-z])", re.I)


def _size_b(model_id: str) -> float | None:
    sizes = [float(x) for x in _SIZE.findall(model_id.split("/")[-1])]
    return max(sizes) if sizes else None


def parse_catalog(rows: list[dict]) -> list[FreeModel]:
    """Free, text-in/text-out chat models large enough for the task, best first; the router last."""
    out: list[FreeModel] = []
    for m in rows:
        if not isinstance(m, dict) or not isinstance(m.get("id"), str):
            continue
        pricing = m.get("pricing") or {}
        if not (_is_zero(pricing.get("prompt")) and _is_zero(pricing.get("completion"))):
            continue
        if any(k in pricing and not _is_zero(pricing[k]) for k in ("request", "internal_reasoning")):
            continue
        arch = m.get("architecture") or {}
        if set(arch.get("output_modalities") or ["text"]) != {"text"} or "text" not in (arch.get("input_modalities") or ["text"]):
            continue
        endpoints = m.get("endpoints")
        if isinstance(endpoints, list) and endpoints and not any(str(e).endswith("/chat/completions") for e in endpoints):
            continue
        context = int(m.get("context_length") or 0)
        size = _size_b(m["id"])
        if context < MIN_CONTEXT or (size is not None and size < MIN_PARAMS_B):
            continue
        top = m.get("top_provider") or {}
        out.append(FreeModel(m["id"], str(m.get("name") or m["id"]), context, top.get("max_completion_tokens"),
                             frozenset(m.get("supported_parameters") or ()), int(m.get("created") or 0)))
    return sorted(out, key=_rank)


def _rank(m: FreeModel) -> tuple:
    if m.id == ROUTER:
        return (1, 0, 0)
    score = 4 * m.structured + 2 * m.json_mode - 3 * ("code" in m.id.lower())
    return (0, -score, -m.created)


# ------------------------------------------------------------------ shared state (per engine process)
_lock = threading.Lock()
_catalog: tuple[float, list[FreeModel]] | None = None
_cooldown: dict[str, tuple[float, str]] = {}
_last_good: str | None = None


def catalog() -> list[FreeModel]:
    global _catalog
    with _lock:
        if _catalog and _catalog[0] > time.time():
            return _catalog[1]
        stale = _catalog[1] if _catalog else None
    try:
        response = httpx.get(f"{BASE_URL}/models", timeout=20.0)
        response.raise_for_status()
        models = parse_catalog(response.json().get("data") or [])
        if not models:
            raise ValueError("no free models")
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("OpenRouter model catalog unavailable: %s", type(exc).__name__)
        models = stale or [FreeModel(ROUTER, "OpenRouter Free Router", 200_000, None,
                                     frozenset({"response_format", "structured_outputs", "max_tokens"}))]
        with _lock:
            _catalog = (time.time() + 120, models)  # retry the catalog soon
        return models
    with _lock:
        _catalog = (time.time() + CATALOG_TTL, models)
    return models


def order(models: list[FreeModel], preferred: str | None = None, now: float | None = None) -> list[FreeModel]:
    """Preferred (or last good) model first, models on cooldown last (soonest available first)."""
    now = time.time() if now is None else now
    with _lock:
        cooling = {k: v[0] for k, v in _cooldown.items() if v[0] > now}
        first = preferred if preferred and preferred != "auto" else _last_good
    ready = [m for m in models if m.id not in cooling]
    ready.sort(key=lambda m: m.id != first)  # stable: keeps the ranking otherwise
    waiting = sorted((m for m in models if m.id in cooling), key=lambda m: cooling[m.id])
    return ready + waiting


def status() -> dict:
    models = catalog()
    now = time.time()
    with _lock:
        cooldown = {k: v for k, v in _cooldown.items() if v[0] > now}
        last = _last_good
    return {
        "provider": "openrouter",
        "lastGood": last,
        "models": [{"id": m.id, "name": m.name, "context": m.context, "structured": m.structured, "jsonMode": m.json_mode,
                    "cooldownUntil": int(cooldown[m.id][0]) if m.id in cooldown else None,
                    "cooldownReason": cooldown[m.id][1] if m.id in cooldown else None} for m in models],
    }


def _cool(model_id: str, seconds: float, reason: str) -> None:
    with _lock:
        _cooldown[model_id] = (time.time() + seconds, reason)


def payload(m: FreeModel, system: str, user: str) -> dict:
    body: dict = {"model": m.id, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    if "max_tokens" in m.params or not m.params:
        body["max_tokens"] = min(m.max_output or MAX_OUTPUT, MAX_OUTPUT)
    if "temperature" in m.params:
        body["temperature"] = 0.2
    if "reasoning" in m.params:
        body["reasoning"] = {"effort": "low", "exclude": True}
    if m.structured:
        body["response_format"] = {"type": "json_schema", "json_schema": {"name": "analisa_aset", "strict": True, "schema": SCHEMA}}
    elif m.json_mode:
        body["response_format"] = {"type": "json_object"}
    if "response_format" in body:
        body["provider"] = {"require_parameters": True}  # only route to endpoints that honor the format
    return body


def _reset_hint(result: ChatResult) -> str:
    headers = (result.meta or {}).get("headers") or {}
    reset = headers.get("X-RateLimit-Reset") or headers.get("x-ratelimit-reset")
    try:
        when = time.strftime("%H:%M", time.localtime(int(reset) / 1000))
    except (TypeError, ValueError):
        return ""
    return f" Kuota pulih sekitar pukul {when}."


def classify(result: ChatResult) -> tuple[str, float, str]:
    """('stop', 0, message) for account-wide failures, ('next', cooldown_s, reason) otherwise."""
    message = (result.error or "").lower()
    if result.status == 401:
        return "auth", 0, "API key OpenRouter ditolak (tidak valid atau dicabut)."
    if result.status == 402:
        return "stop", 0, "OpenRouter menolak permintaan (402): saldo kredit akun negatif. Periksa akun OpenRouter Anda."
    if result.status == 429 and ("per-day" in message or "per day" in message):
        return "stop", 0, "Kuota harian model gratis OpenRouter untuk akun ini sudah habis." + _reset_hint(result)
    if result.status == 429 and ("per-min" in message or "per min" in message):
        return "stop", 0, "Batas permintaan per menit model gratis OpenRouter tercapai; coba lagi 1 menit lagi."
    if result.status == 0 and result.error_code == "network":
        return "stop", 0, "Tidak dapat terhubung ke OpenRouter (periksa internet)."
    if result.status == 0:
        return "next", 5 * 60, "waktu habis"
    if result.status == 429:
        return "next", 3 * 60, "dibatasi sementara (429)"
    if result.status == 404:
        return "next", 60 * 60, "model tidak tersedia"
    if result.status in (400, 403, 413, 422):
        return "next", 30 * 60, f"ditolak ({result.status})"
    return "next", 3 * 60, f"error {result.status}"


def run(system: str, user: str, api_key: str | None, preferred: str | None = None) -> Answer:
    global _last_good
    if not api_key:
        raise AiNotConfigured("Analisa AI belum aktif: masukkan OpenRouter API key di pengaturan AI (gratis di openrouter.ai/keys).")
    deadline = time.monotonic() + BUDGET_S
    attempts: list[dict] = []
    for m in order(catalog(), preferred):
        remaining = deadline - time.monotonic()
        if len(attempts) >= MAX_ATTEMPTS or remaining < 20:
            break
        result = chat(BASE_URL, api_key, payload(m, system, user), min(ATTEMPT_TIMEOUT_S, remaining), HEADERS)
        attempt = {"model": m.id, "ms": result.elapsed_ms}
        if result.ok:
            try:
                if result.finish == "length":
                    raise InvalidAnswer("jawaban terpotong")
                analysis = parse_answer(result.text)
            except InvalidAnswer as exc:
                _cool(m.id, 10 * 60, str(exc))
                attempts.append({**attempt, "ok": False, "reason": str(exc)})
                continue
            with _lock:
                _last_good = m.id
                _cooldown.pop(m.id, None)
            attempts.append({**attempt, "ok": True, "served": result.model})
            return Answer(analysis, result.model or m.id, result.usage or {"input": 0, "output": 0}, attempts)
        action, seconds, reason = classify(result)
        if action == "auth":
            raise AiNotConfigured(reason)
        if action == "stop":
            raise AiFailed(reason)
        _cool(m.id, seconds, reason)
        attempts.append({**attempt, "ok": False, "reason": reason, "status": result.status})
        log.info("OpenRouter %s failed (%s), trying the next free model", m.id, reason)
    tried = ", ".join(f"{a['model']} ({a['reason']})" for a in attempts) or "tidak ada model yang bisa dicoba"
    raise AiFailed(f"Semua model gratis OpenRouter yang dicoba gagal: {tried}. Coba lagi beberapa menit lagi.")
