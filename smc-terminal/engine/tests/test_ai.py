"""AI provider plumbing: free-model selection, answer parsing and OpenRouter rotation (no network)."""

import json

import pytest

from app.ai import common, openrouter
from app.ai.openai_compat import ChatResult


def _model(mid, prompt="0", completion="0", out=("text",), ctx=131072, params=("max_tokens",), created=1):
    return {"id": mid, "name": mid, "context_length": ctx, "created": created,
            "pricing": {"prompt": prompt, "completion": completion},
            "architecture": {"input_modalities": ["text"], "output_modalities": list(out)},
            "top_provider": {"max_completion_tokens": 8192}, "supported_parameters": list(params)}


def test_catalog_keeps_usable_free_models_best_first():
    rows = [
        _model("openrouter/free", params=("structured_outputs", "response_format")),
        _model("paid/model", prompt="0.000001"),
        _model("google/lyria-3-pro-preview", out=("text", "audio")),
        _model("liquid/lfm-3-1.7b:free", params=("structured_outputs",)),
        _model("short/ctx-70b:free", ctx=8192),
        _model("plain/big-70b:free", created=5),
        _model("json/mode-31b:free", params=("response_format",)),
        _model("strict/schema-26b-a4b:free", params=("structured_outputs", "response_format")),
        _model("acme/coder-code:free", params=("response_format",)),
    ]
    ids = [m.id for m in openrouter.parse_catalog(rows)]
    assert ids == ["strict/schema-26b-a4b:free", "json/mode-31b:free", "plain/big-70b:free", "acme/coder-code:free", "openrouter/free"]


def test_payload_requests_strict_schema_only_when_supported():
    strict, plain = openrouter.parse_catalog([
        _model("a/strict-70b:free", params=("structured_outputs", "response_format", "max_tokens", "temperature")),
        _model("b/plain-70b:free", params=("max_tokens",)),
    ])
    body = openrouter.payload(strict, "s", "u")
    assert body["response_format"]["type"] == "json_schema" and body["provider"] == {"require_parameters": True}
    assert body["temperature"] == 0.2
    body = openrouter.payload(plain, "s", "u")
    assert "response_format" not in body and "temperature" not in body and "provider" not in body


def test_answer_parsing_is_lenient_but_validated():
    text = "<think>hmm</think>\nBerikut analisanya:\n```json\n" + json.dumps({
        "direction": "Bullish", "confidence": 140.4, "horizon": "besok", "summary": " Naik ",
        "drivers": "CPI lebih rendah", "risks": [], "keyLevels": [1.08, None]}) + "\n```"
    a = common.parse_answer(text)
    assert a["direction"] == "naik" and a["confidence"] == 100 and a["horizon"] == "1-3 hari"
    assert a["summary"] == "Naik" and a["drivers"] == ["CPI lebih rendah"] and a["keyLevels"] == ["1.08"]
    for bad in ("", "tidak tahu", '{"direction": "keatas", "summary": "x"}', '{"direction": "naik"}', "[1]"):
        with pytest.raises(common.InvalidAnswer):
            common.parse_answer(bad)


GOOD = json.dumps({"direction": "turun", "confidence": 61, "horizon": "intraday", "summary": "Tekanan jual.",
                   "drivers": ["CHoCH bearish 4H"], "risks": ["NFP"], "keyLevels": ["1.0850"], "invalidation": "di atas 1.09",
                   "dataQuality": "cukup"})


@pytest.fixture
def rotation(monkeypatch):
    models = openrouter.parse_catalog([
        _model("a/first-70b:free", created=3), _model("b/second-70b:free", created=2), _model("c/third-70b:free", created=1),
        _model("openrouter/free"),
    ])
    calls: list[str] = []
    replies: dict[str, ChatResult] = {}

    def fake_chat(base_url, key, body, timeout, headers=None):
        calls.append(body["model"])
        return replies.get(body["model"], ChatResult(True, 200, text=GOOD, model=body["model"], finish="stop",
                                                     usage={"input": 10, "output": 5}))

    monkeypatch.setattr(openrouter, "catalog", lambda: models)
    monkeypatch.setattr(openrouter, "chat", fake_chat)
    monkeypatch.setattr(openrouter, "_cooldown", {})
    monkeypatch.setattr(openrouter, "_last_good", None)
    return calls, replies


def test_rotates_to_next_free_model_and_remembers_the_one_that_worked(rotation):
    calls, replies = rotation
    replies["a/first-70b:free"] = ChatResult(False, 429, error="a/first-70b:free is temporarily rate-limited upstream")
    replies["b/second-70b:free"] = ChatResult(True, 200, text="maaf, saya tidak bisa", finish="stop")
    answer = openrouter.run("s", "u", "sk-or-test")
    assert calls == ["a/first-70b:free", "b/second-70b:free", "c/third-70b:free"]
    assert answer.model == "c/third-70b:free" and answer.analysis["direction"] == "turun"
    assert [a["ok"] for a in answer.attempts] == [False, False, True]
    order = [m.id for m in openrouter.order(openrouter.catalog())]
    assert order[0] == "c/third-70b:free" and order[-2:] == ["a/first-70b:free", "b/second-70b:free"]
    calls.clear()
    openrouter.run("s", "u", "sk-or-test")
    assert calls == ["c/third-70b:free"]


def test_account_wide_limits_stop_rotation(rotation):
    calls, replies = rotation
    replies["a/first-70b:free"] = ChatResult(False, 429, error="Rate limit exceeded: free-models-per-day. Add credits",
                                             meta={"headers": {"X-RateLimit-Reset": "1794268800000"}})
    with pytest.raises(common.AiFailed, match="Kuota harian"):
        openrouter.run("s", "u", "sk-or-test")
    assert calls == ["a/first-70b:free"]
    calls.clear()
    replies["a/first-70b:free"] = ChatResult(False, 401, error="User not found.")
    with pytest.raises(common.AiNotConfigured):
        openrouter.run("s", "u", "sk-or-test")
    assert calls == ["a/first-70b:free"]
    with pytest.raises(common.AiNotConfigured):
        openrouter.run("s", "u", None)


def test_all_models_failing_reports_each_attempt(rotation):
    calls, replies = rotation
    for mid in ("a/first-70b:free", "b/second-70b:free", "c/third-70b:free", "openrouter/free"):
        replies[mid] = ChatResult(False, 503, error="provider down")
    with pytest.raises(common.AiFailed, match="a/first-70b:free \\(error 503\\)"):
        openrouter.run("s", "u", "sk-or-test")
    assert len(calls) == 4


def _answer(direction, confidence):
    return common.normalize({"direction": direction, "confidence": confidence, "summary": "x"})


@pytest.mark.parametrize("direction,confidence,smc_score,status,final,signal", [
    ("naik", 60, 60, "match", 77, "BELI"),          # agreement raises confidence (5 + 60*0.2 = 17)
    ("turun", 85, -100, "match", 95, "JUAL"),       # capped: agreement never becomes certainty
    ("naik", 60, -60, "conflict", 43, "NETRAL"),    # disagreement lowers it below the signal threshold
    ("naik", 62, 8, "neutral", 62, "BELI"),         # SMC without a clear direction changes nothing
    ("netral", 70, 80, "neutral", 70, "NETRAL"),
    ("turun", 50, -20, "match", 59, "JUAL"),
])
def test_confluence_with_smc(direction, confidence, smc_score, status, final, signal):
    c = common.confluence(_answer(direction, confidence), {"score": smc_score, "label": "x", "aligned": True})
    assert (c["status"], c["final"], c["signal"]) == (status, final, signal)
    assert c["base"] == confidence
    assert c["final"] == min(common.CONFIDENCE_CAP, max(5, confidence + c["adjustment"]))


def test_new_answer_fields_default_when_missing():
    a = common.parse_answer('{"direction": "naik", "confidence": 55, "summary": "ok"}')
    assert a["catalysts"] == [] and a["fundamentalView"] == ""
