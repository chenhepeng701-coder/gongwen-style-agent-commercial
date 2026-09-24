"""NEXT-PERF-02: exact model/effort contract, synthetic transport, no network."""
import json

import httpx
import pytest

from app.config import ProviderConfig
from app.services.llm.openai_compatible_provider import OpenAICompatibleProvider
from test_writer_observability import install_stream, offline, read_trace, sse
from test_writing_reference_pack_02b1 import configured, synthetic_packet, api


MAX_MODELS = ("qwen3.8-max", "qwen3.8-max-0902", "qwen3.8-max-2026-09-02")
ARTICLE = "# 合成测试工作总结\n\n本段仅为离线测试正文，不代表真实工作成果。\n"


def provider(model, *, stream=True):
    return OpenAICompatibleProvider(ProviderConfig(
        model=model, base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        endpoint_path="/chat/completions", api_key="synthetic-only",
        stream=stream, timeout=120))


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("model,effort", [
    ("qwen3.8-flash", "low"),
    *((model, effort) for model in MAX_MODELS for effort in ("low", "medium", "xhigh")),
    ("qwen3.8-max-0902", "none"),
])
def test_verified_model_effort_payload(monkeypatch, model, effort, stream):
    active = provider(model, stream=stream)
    before = active.config.model_dump()
    calls = install_stream(monkeypatch, [sse({"content": ARTICLE}), b"data: [DONE]\n\n"])

    def post(**kwargs):
        calls.append(("POST", kwargs))
        return httpx.Response(200, json={"choices": [{"message": {"content": ARTICLE}}]},
                              request=httpx.Request("POST", kwargs["url"]))

    monkeypatch.setattr(httpx, "post", post)
    attempts = []
    assert active.generate_text("synthetic prompt", system_prompt="synthetic system",
        temperature=0.45, reasoning_effort=effort, retry_limit=0,
        preserve_whitespace=True, on_http_request_attempt=attempts.append) == ARTICLE
    assert attempts == [1] and len(calls) == 1
    method, sent = calls[0]
    assert method == "POST" and sent["timeout"] == 120
    assert sent["follow_redirects"] is False
    assert sent["json"] == {"model": model, "messages": [
        {"role": "system", "content": "synthetic system"},
        {"role": "user", "content": "synthetic prompt"}],
        "temperature": 0.45, "reasoning_effort": effort, **({"stream": True} if stream else {})}
    assert "thinking_budget" not in sent["json"]
    assert active.config.model_dump() == before


@pytest.mark.parametrize("model,effort", [
    *(("qwen3.8-flash", effort) for effort in ("none", "medium", "xhigh", "high", "LOW", "")),
    ("qwen3.8-max", "none"), ("qwen3.8-max-2026-09-02", "none"),
    *((model, "high") for model in MAX_MODELS),
    ("qwen3.8-flash-0902", "low"), ("QWEN3.8-FLASH", "low"), ("qwen-synthetic", "low"),
])
def test_unverified_model_effort_fails_before_http(monkeypatch, model, effort):
    calls = install_stream(monkeypatch, [])
    attempts = []
    with pytest.raises(ValueError, match="reasoning_effort"):
        provider(model).generate_text("synthetic", reasoning_effort=effort, retry_limit=0,
                                      on_http_request_attempt=attempts.append)
    assert calls == attempts == []


@pytest.mark.parametrize("model", ["qwen3.8-flash", *MAX_MODELS])
@pytest.mark.parametrize("base_url,endpoint_path", [
    ("https://offline.invalid", "/chat/completions"),
    ("https://dashscope.aliyuncs.com/compatible-mode/v1", "/unverified"),
])
def test_unverified_endpoint_fails_before_http(monkeypatch, model, base_url, endpoint_path):
    active = provider(model)
    active.config.base_url, active.config.endpoint_path = base_url, endpoint_path
    calls = install_stream(monkeypatch, [])
    with pytest.raises(ValueError, match="not verified for this endpoint/model"):
        active.generate_text("synthetic", reasoning_effort="low", retry_limit=0)
    assert calls == []


@pytest.mark.parametrize("model", ["qwen3.8-flash", *MAX_MODELS, "qwen-synthetic"])
def test_provider_without_effort_keeps_payload_unchanged(monkeypatch, model):
    calls = install_stream(monkeypatch, [sse({"content": ARTICLE}), b"data: [DONE]\n\n"])
    assert provider(model).generate_text("synthetic", retry_limit=0, preserve_whitespace=True) == ARTICLE
    assert len(calls) == 1
    assert "reasoning_effort" not in calls[0][1]["json"]
    assert "thinking_budget" not in calls[0][1]["json"]


@pytest.mark.parametrize("model", ["qwen3.8-flash", *MAX_MODELS, "qwen-synthetic", "qwen3.8-flash-0902"])
def test_full_draft_only_exact_flash_uses_low(configured, synthetic_packet, api,
                                            monkeypatch, tmp_path, model):
    client, route = api
    active = provider(model)
    monkeypatch.setattr(route, "get_llm_provider", lambda: active)
    monkeypatch.setattr(route, "prepare_full_draft_material", lambda request: synthetic_packet)
    calls = install_stream(monkeypatch, [sse({"content": ARTICLE}), b"data: [DONE]\n\n"])
    original = active.generate_text
    writer_calls = []

    def writer(prompt, **kwargs):
        writer_calls.append(kwargs)
        return original(prompt, **kwargs)

    monkeypatch.setattr(active, "generate_text", writer)
    response = client.post("/api/generation/generate", json={"title": "合成测试",
        "document_type": "工作总结", "generation_route": "full_article_drafting", "temperature": 0.45})
    assert response.status_code == 200, response.text
    assert len(writer_calls) == len(calls) == 1
    kwargs, sent = writer_calls[0], calls[0][1]
    assert kwargs["retry_limit"] == 0 and kwargs["preserve_whitespace"] is True
    assert kwargs["temperature"] == sent["json"]["temperature"] == 0.45
    assert sent["timeout"] == 120 and sent["json"]["stream"] is True
    if model == "qwen3.8-flash":
        assert kwargs["reasoning_effort"] == sent["json"]["reasoning_effort"] == "low"
    else:
        assert "reasoning_effort" not in kwargs and "reasoning_effort" not in sent["json"]
    assert "thinking_budget" not in kwargs and "thinking_budget" not in sent["json"]
    root, = (tmp_path / "full-draft").iterdir()
    accounting = json.loads((root / "call_accounting.json").read_bytes())
    assert accounting["retry_limit"] == 0 and accounting["fallback_used"] is False
    assert accounting["writer_calls"] == accounting["writer_http_attempts"] == accounting["http_attempts"] == 1
    rows = read_trace(root)
    assert rows[0]["event"] == "writer_running" and rows[-1]["outcome"] == "success"
    assert rows[-1]["run_id"] == accounting["run_id"] == root.name
    assert response.json()["articles"][0]["article_markdown"] == ARTICLE
    assert (root / "RAW_WRITER_ARTICLE.md").read_bytes() == (root / "FINAL_DELIVERED_ARTICLE.md").read_bytes()
