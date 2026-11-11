"""Any OpenAI-compatible chat-completions endpoint chosen by the user (base URL + model)."""

from __future__ import annotations

from urllib.parse import urlparse

from .common import AiFailed, AiNotConfigured, Answer, InvalidAnswer, parse_answer
from .openai_compat import chat, validate_base_url

NAME = "API lain (OpenAI-compatible)"


def run(system: str, user: str, api_key: str | None, base_url: str | None, model: str | None) -> Answer:
    url = validate_base_url(base_url)
    if not (model or "").strip():
        raise AiNotConfigured("Isi nama model untuk API ini di pengaturan AI.")
    host = urlparse(url).netloc
    body = {"model": model.strip(), "temperature": 0.2, "max_tokens": 8000,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    result = chat(url, api_key, body, timeout=180.0)
    if not result.ok:
        if result.status in (401, 403):
            raise AiNotConfigured(f"API key ditolak oleh {host} ({result.status}).")
        if result.status == 0:
            raise AiFailed(f"Tidak dapat terhubung ke {host}: {result.error}.")
        raise AiFailed(f"{host} error {result.status}: {result.error}")
    try:
        if result.finish == "length":
            raise InvalidAnswer("jawaban terpotong")
        analysis = parse_answer(result.text)
    except InvalidAnswer as exc:
        raise AiFailed(f"Jawaban model tidak bisa dipakai: {exc}.") from exc
    return Answer(analysis, result.model or model.strip(), result.usage or {"input": 0, "output": 0},
                  [{"model": model.strip(), "ok": True, "ms": result.elapsed_ms}])
