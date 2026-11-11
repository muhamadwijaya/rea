"""Anthropic Claude provider (strict JSON-schema output, server-side model fallback)."""

from __future__ import annotations

import anthropic

from .common import SCHEMA, AiFailed, AiNotConfigured, Answer, InvalidAnswer, parse_answer

MODEL = "claude-opus-5-5"
NAME = "Anthropic Claude"


def run(system: str, user: str, api_key: str | None) -> Answer:
    try:
        client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
    except anthropic.AnthropicError as exc:
        raise AiNotConfigured("Analisa AI belum aktif: masukkan Anthropic API key di pengaturan AI.") from exc
    try:
        response = client.with_options(timeout=180.0).beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
            system=system,
            messages=[{"role": "user", "content": user}],
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
    try:
        result = parse_answer(text)
    except InvalidAnswer as exc:
        raise AiFailed(f"Jawaban Claude tidak bisa dipakai: {exc}") from exc
    return Answer(result, response.model, {"input": response.usage.input_tokens, "output": response.usage.output_tokens},
                  [{"model": response.model, "ok": True}])
