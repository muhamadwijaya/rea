"""Minimal OpenAI-compatible chat-completions client, shared by OpenRouter and custom endpoints.

Returns a tagged outcome instead of raising for HTTP-level failures, so callers can decide whether to
rotate to another model, stop, or report. The API key only goes into the Authorization header.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from .common import AiNotConfigured


@dataclass
class ChatResult:
    ok: bool
    status: int  # HTTP status; 0 = network error / timeout
    text: str | None = None
    model: str | None = None
    finish: str | None = None
    usage: dict | None = None
    error: str | None = None  # provider message (no credentials)
    error_code: str | None = None
    meta: dict | None = None
    elapsed_ms: int = 0


def validate_base_url(base_url: str | None) -> str:
    url = (base_url or "").strip().rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise AiNotConfigured("Base URL API tidak valid (contoh: https://api.example.com/v1).")
    return url


def chat(base_url: str, api_key: str | None, payload: dict, timeout: float, headers: dict | None = None) -> ChatResult:
    started = time.perf_counter()
    request_headers = {"Content-Type": "application/json", **(headers or {})}
    if api_key:
        request_headers["Authorization"] = f"Bearer {api_key}"
    try:
        response = httpx.post(f"{base_url}/chat/completions", json=payload, headers=request_headers,
                              timeout=httpx.Timeout(timeout, connect=15.0))
    except httpx.TimeoutException:
        return ChatResult(False, 0, error="waktu habis", error_code="timeout", elapsed_ms=_ms(started))
    except httpx.HTTPError as exc:
        return ChatResult(False, 0, error=f"koneksi gagal ({type(exc).__name__})", error_code="network", elapsed_ms=_ms(started))
    elapsed = _ms(started)
    try:
        data = response.json()
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return ChatResult(False, response.status_code, error=f"jawaban bukan JSON (HTTP {response.status_code})", elapsed_ms=elapsed)
    err = data.get("error")
    if response.status_code >= 400 or err:
        err = err if isinstance(err, dict) else {"message": str(err or "")}
        status = response.status_code if response.status_code >= 400 else _int(err.get("code"), 502)
        return ChatResult(False, status, error=str(err.get("message") or f"HTTP {status}")[:300],
                          error_code=str(err.get("code") or ""), meta=err.get("metadata") if isinstance(err.get("metadata"), dict) else None,
                          elapsed_ms=elapsed)
    choices = data.get("choices") or []
    if not choices:
        return ChatResult(False, response.status_code, error="tanpa pilihan jawaban", elapsed_ms=elapsed)
    choice = choices[0] or {}
    if isinstance(choice.get("error"), dict):  # mid-generation provider error
        return ChatResult(False, _int(choice["error"].get("code"), 502), error=str(choice["error"].get("message"))[:300],
                          model=data.get("model"), elapsed_ms=elapsed)
    message = choice.get("message") or {}
    content = message.get("content")
    if isinstance(content, list):  # content parts
        content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    usage = data.get("usage") or {}
    return ChatResult(True, response.status_code, text=content, model=data.get("model"), finish=choice.get("finish_reason"),
                      usage={"input": _int(usage.get("prompt_tokens"), 0), "output": _int(usage.get("completion_tokens"), 0)},
                      elapsed_ms=elapsed)


def _int(v, default: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
