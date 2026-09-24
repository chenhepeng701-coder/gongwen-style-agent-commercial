"""NEXT-PERF-01: real SSE parser and formal route, synthetic transport only."""
from contextlib import contextmanager
import copy
import hashlib
import ipaddress
import json
import logging
from pathlib import Path
import socket
import subprocess
from types import ModuleType, SimpleNamespace

import httpx
from httpx import _utils as httpx_utils
import pytest

from app.config import ProviderConfig
from app.services.llm import openai_compatible_provider as provider_owner
from app.services.llm import writer_observability as trace
from app.utils import http_client
from test_writing_reference_pack_02b1 import configured, synthetic_packet, api


BASE_HEAD = "01d1b6a1dbaa406a4b6dc1915631cae93b4ae7e4"
API_KEY = "sk-synthetic-secret-NEXT-PERF"
PROMPT = "PRIVATE_PROMPT_synthetic_only"
REASONING = "PRIVATE_REASONING_synthetic_only"
ARTICLE = "  # 合成正文\n\nPRIVATE_ARTICLE_synthetic_only。\n"


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    def check(host):
        # Windows asyncio uses a loopback socket pair for its event loop.
        if host == "localhost" or ipaddress.ip_address(host).is_loopback:
            return
        raise AssertionError("NEXT-PERF-01: external network access forbidden")
    connect, connect_ex, resolve = socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo
    def guarded_connect(stream, address):
        check(address[0])
        return connect(stream, address)
    def guarded_connect_ex(stream, address):
        check(address[0])
        return connect_ex(stream, address)
    def guarded_resolve(host, *args, **kwargs):
        check(host)
        return resolve(host, *args, **kwargs)
    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", guarded_resolve)
    monkeypatch.setattr(provider_owner, "get_logs_dir", lambda: tmp_path)
    # Stable default; each proxy case explicitly exercises the real resolver.
    monkeypatch.setattr(http_client.urllib.request, "getproxies_environment", lambda: {})
    monkeypatch.setattr(httpx_utils, "getproxies", lambda: {})


def provider():
    return provider_owner.OpenAICompatibleProvider(ProviderConfig(
        model="qwen-synthetic", base_url="https://offline.invalid", api_key=API_KEY,
        timeout=431, stream=True))


def sse(delta=None, finish=None, **extra):
    return ("data: " + json.dumps({"choices": [{"delta": delta or {},
        "finish_reason": finish}], **extra}, ensure_ascii=False) + "\n\n").encode("utf-8")


class Clock:
    value = 1000.0

    def monotonic(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


def install_stream(monkeypatch, chunks, *, headers=None, connect_error=None,
                   clock=None, status=200, on_close=None):
    calls = []

    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            for chunk in chunks:
                if clock:
                    clock.advance(3)
                if isinstance(chunk, Exception):
                    raise chunk
                yield chunk
            if clock:
                clock.advance(5)  # observable EOF tail

        def close(self):
            if on_close:
                on_close()

    @contextmanager
    def stream(method, **kwargs):
        calls.append((method, copy.deepcopy(kwargs)))
        if clock:
            clock.advance(7)
        if connect_error:
            raise connect_error
        response = httpx.Response(status, headers=headers or {}, stream=Stream(),
                                  request=httpx.Request(method, kwargs["url"]))
        try:
            yield response
        finally:
            response.close()
            if clock:
                clock.advance(2)  # HTTP client context teardown after iteration

    monkeypatch.setattr(provider_owner.httpx, "stream", stream)
    return calls


def read_trace(root):
    return [json.loads(line) for line in (root / trace.TRACE_FILENAME).read_text("utf-8").splitlines()]


def invoke(root, active=None):
    active = active or provider()
    with trace.observe_writer_call(root):
        return active.generate_text(PROMPT, system_prompt="PRIVATE_SYSTEM_PROMPT", temperature=0.45,
                                    retry_limit=0, preserve_whitespace=True)


@pytest.mark.parametrize("ending", ["done", "eof", "done_without_newline"])
def test_timeline_counters_usage_and_no_sensitive_text(monkeypatch, tmp_path, caplog, ending):
    clock = Clock()
    monkeypatch.setattr(trace, "time", clock)
    usage = {"prompt_tokens": 13, "completion_tokens": 21, "total_tokens": 34,
             "completion_tokens_details": {"reasoning_tokens": 8, "private": REASONING},
             "private": PROMPT}
    chunks = [sse({"reasoning_content": REASONING}, id="completion-id-1"),
              sse({"content": ARTICLE[:8]}), sse({"content": ARTICLE[8:]}),
              sse(finish="stop"),
              ("data: " + json.dumps({"choices": [], "usage": usage}) + "\n\n").encode()]
    if ending.startswith("done"):
        chunks.append(b"data: [DONE]" + (b"\n\n" if ending == "done" else b""))
    install_stream(monkeypatch, chunks, headers={"x-request-id": "request-id-1"}, clock=clock)
    with caplog.at_level(logging.INFO):
        assert invoke(tmp_path) == ARTICLE
    rows = read_trace(tmp_path)
    result = rows[-1]
    assert result["event"] == "provider_return" and result["outcome"] == "success"
    assert {r["run_id"] for r in rows} == {tmp_path.name}
    assert {r["writer_attempt"] for r in rows} == {1}
    assert {r["http_attempt"] for r in rows} == {1}
    assert result["first_sse_type"] == "reasoning_content"
    assert (result["reasoning_event_count"], result["content_event_count"], result["other_event_count"]) == (1, 2, 2)
    assert result["received_character_count"] == len(ARTICLE)
    assert result["received_bytes"] == sum(map(len, chunks))
    assert result["finish_reason"] == "stop"
    assert result["request_id"] == {"sha256": hashlib.sha256(b"request-id-1").hexdigest()}
    assert result["response_id"] == {"sha256": hashlib.sha256(b"completion-id-1").hexdigest()}
    assert result["usage"] == {"prompt_tokens": 13, "completion_tokens": 21, "total_tokens": 34,
                               "completion_tokens_details": {"reasoning_tokens": 8}}
    offsets = result["offsets"]
    order = [offsets[k] for k in ("writer_running", "request_start", "response_headers",
             "first_sse", "first_content", "last_content", "first_finish_reason", "done_or_eof", "provider_return")]
    assert order == sorted(order)
    assert offsets["response_headers"] - offsets["request_start"] == 7
    assert offsets["provider_return"] - offsets["done_or_eof"] == 2
    assert offsets["done_or_eof"] > offsets["first_finish_reason"]
    assert result["termination"] == ("eof" if ending == "eof" else "done")
    persisted = (tmp_path / trace.TRACE_FILENAME).read_text("utf-8") + caplog.text
    assert all(secret not in persisted for secret in (API_KEY, PROMPT, ARTICLE, REASONING, "PRIVATE_SYSTEM_PROMPT"))
    assert rows[0]["event"] == "writer_running"  # append-only, survives final snapshot


@pytest.mark.parametrize("first", ["content", "other"])
def test_optional_metadata_absent_and_first_event_types(monkeypatch, tmp_path, first):
    chunks = ([sse({"role": "assistant"})] if first == "other" else [])
    chunks += [sse({"content": ARTICLE}), b"data: [DONE]\n\n"]
    install_stream(monkeypatch, chunks)
    assert invoke(tmp_path) == ARTICLE
    result = read_trace(tmp_path)[-1]
    assert result["first_sse_type"] == first
    assert all(result[field] is None for field in ("usage", "request_id", "response_id", "finish_reason"))
    assert result["offsets"]["first_content"] is not None


@pytest.mark.parametrize("failure", [httpx.ConnectTimeout, httpx.ReadTimeout, httpx.RemoteProtocolError])
def test_transport_error_cause_and_partial_observation_survive(monkeypatch, tmp_path, caplog, failure):
    error = failure(API_KEY + PROMPT + ARTICLE + REASONING)
    chunks = [sse({"reasoning_content": REASONING}), sse({"content": ARTICLE}), error]
    calls = install_stream(monkeypatch, chunks, connect_error=error if failure is httpx.ConnectTimeout else None)
    with pytest.raises(provider_owner.LLMConnectionError) as caught:
        invoke(tmp_path)
    assert caught.value.__cause__ is error
    assert len(calls) == 1
    result = read_trace(tmp_path)[-1]
    assert result["error_type"] == "LLMConnectionError"
    assert result["transport_error_type"] == failure.__name__
    assert result["exception_types"][:2] == ["LLMConnectionError", failure.__name__]
    assert result["outcome"] == "error" and result["termination"] is None
    assert result["offsets"]["done_or_eof"] is None
    assert result["offsets"]["provider_return"] is not None
    if failure is httpx.ConnectTimeout:
        assert result["offsets"]["response_headers"] is None
    else:
        assert result["content_event_count"] == result["reasoning_event_count"] == 1
    persisted = (tmp_path / trace.TRACE_FILENAME).read_text("utf-8") + caplog.text
    assert all(secret not in persisted for secret in (API_KEY, PROMPT, ARTICLE, REASONING))


@pytest.mark.parametrize("failure", ["io", "proxy", "clock"])
def test_observation_failure_cannot_change_writer_result(monkeypatch, tmp_path, failure):
    def fail(*args, **kwargs):
        raise OSError("private diagnostic exception")
    if failure == "io":
        original = Path.open
        def open_file(path, *args, **kwargs):
            if path.name == trace.TRACE_FILENAME:
                return fail()
            return original(path, *args, **kwargs)
        monkeypatch.setattr(Path, "open", open_file)
    elif failure == "proxy":
        monkeypatch.setattr(trace, "describe_effective_proxy", fail)
    else:
        monkeypatch.setattr(trace, "time", SimpleNamespace(monotonic=fail))
    install_stream(monkeypatch, [sse({"content": ARTICLE}), b"data: [DONE]\n\n"])
    assert invoke(tmp_path) == ARTICLE
    install_stream(monkeypatch, [], connect_error=httpx.ConnectTimeout("private"))
    with pytest.raises(provider_owner.LLMConnectionError) as caught:
        invoke(tmp_path)
    assert isinstance(caught.value.__cause__, httpx.ConnectTimeout)
    assert trace._active_trace.get() is None


@pytest.mark.parametrize("case,expected", [
    ("windows", "windows_system"), ("environment", "environment"), ("none", "none"),
    ("no_proxy", "none"), ("wildcard", "none"), ("disabled", "none"),
    ("http_only", "none"), ("all_proxy", "environment"), ("unknown", "unknown"),
])
def test_proxy_truth_uses_httpx_rules_and_never_records_addresses(monkeypatch, tmp_path, case, expected):
    monkeypatch.setattr(http_client.sys, "platform", "win32")
    proxy = "http://private-user:private-password@private-proxy:8080"
    env = {} if case in {"windows", "none", "unknown"} else {"https": proxy}
    if case == "no_proxy": env["no"] = "offline.invalid"
    if case == "wildcard": env["no"] = "*"
    if case == "http_only": env = {"http": proxy}
    if case == "all_proxy": env = {"all": proxy}
    monkeypatch.setattr(http_client.urllib.request, "getproxies_environment", lambda: env)
    monkeypatch.setattr(httpx_utils, "getproxies", lambda: env or ({"https": proxy} if case == "windows" else {}))
    if case == "unknown":
        def fail(): raise OSError("private registry error")
        monkeypatch.setattr(httpx_utils, "get_environment_proxies", fail)
    kwargs = {"trust_env": False} if case == "disabled" else {}
    before = copy.deepcopy(kwargs)
    state = http_client.describe_effective_proxy("https://offline.invalid/v1/chat/completions", kwargs)
    assert state["effective_proxy"] == expected
    assert kwargs == before
    assert "private" not in json.dumps(state)
    if case in {"no_proxy", "wildcard"}:
        assert state["bypassed"] is True
    if case == "http_only":
        assert state["bypassed"] is False  # no applicable proxy, not NO_PROXY
    if case == "unknown":
        assert state["diagnostic_error_type"] == "OSError"
    else:
        # Verify against HTTPX's actual transport selection, without sockets.
        with httpx.Client(**kwargs) as client:
            direct = client._transport_for_url(httpx.URL("https://offline.invalid")) is client._transport
            assert direct == (expected == "none")
    monkeypatch.setattr(provider_owner, "get_httpx_request_kwargs", lambda service: kwargs.copy())
    install_stream(monkeypatch, [sse({"content": ARTICLE}), b"data: [DONE]\n\n"])
    assert invoke(tmp_path) == ARTICLE
    assert read_trace(tmp_path)[-1]["proxy"] == state


@pytest.mark.parametrize("body", ["normal", "fragmented", "unexpected_eof", "malformed", "reasoning_only"])
def test_baseline_provider_behavior_and_exact_request_contract(monkeypatch, tmp_path, body):
    root = Path(__file__).resolve().parents[2]
    source = subprocess.check_output(["git", "show", BASE_HEAD + ":backend/app/services/llm/openai_compatible_provider.py"], cwd=root)
    baseline = ModuleType("next_perf_baseline_provider")
    exec(compile(source, "current-repository-baseline-provider", "exec"), baseline.__dict__)
    monkeypatch.setattr(baseline, "get_logs_dir", lambda: tmp_path)
    chunks = [sse({"reasoning_content": REASONING}), sse({"content": ARTICLE}),
              sse(finish="stop"), b"data: [DONE]\n\n"]
    if body == "fragmented":
        wire = b"\xef\xbb\xbf: comment\r\n\r\n" + b"".join(chunks)
        chunks = [wire[i:i+2] for i in range(0, len(wire), 2)]
    if body == "unexpected_eof": chunks = [sse({"content": ARTICLE})]
    if body == "malformed": chunks = [b"data: not-json\n\n"]
    if body == "reasoning_only": chunks = [sse({"reasoning_content": REASONING}), b"data: [DONE]\n\n"]
    outcomes, sent = [], []
    for owner in (baseline, provider_owner):
        calls = install_stream(monkeypatch, chunks)
        active = owner.OpenAICompatibleProvider(provider().config.model_copy(deep=True))
        try:
            answer = invoke(tmp_path, active)
            outcomes.append(("success", answer))
        except Exception as exc:
            outcomes.append((type(exc).__name__, str(exc), type(exc.__cause__).__name__))
        sent.append(calls)
    assert outcomes[0] == outcomes[1]
    assert sent[0] == sent[1] and len(sent[1]) == 1
    method, request = sent[1][0]
    assert method == "POST" and request["timeout"] == 431
    assert request["json"] == {"model": "qwen-synthetic", "messages": [
        {"role": "system", "content": "PRIVATE_SYSTEM_PROMPT"}, {"role": "user", "content": PROMPT}],
        "temperature": 0.45, "stream": True}


def test_untrusted_server_metadata_is_not_persisted(monkeypatch, tmp_path, caplog):
    private = API_KEY + PROMPT + ARTICLE + REASONING
    install_stream(monkeypatch, [sse({"content": ARTICLE}, finish=private, id=private,
        request_id=private, usage={"prompt_tokens": private, private: 123}), b"data: [DONE]\n\n"],
        headers={"x-request-id": "private-header-" + API_KEY})
    assert invoke(tmp_path) == ARTICLE
    row = read_trace(tmp_path)[-1]
    assert row["finish_reason"] == "[REDACTED]" and row["usage"] is None
    persisted = (tmp_path / trace.TRACE_FILENAME).read_text("utf-8") + caplog.text
    assert all(value not in persisted for value in (API_KEY, PROMPT, ARTICLE, REASONING))


@pytest.mark.parametrize("fail", [False, True])
def test_formal_api_binds_run_and_keeps_running_history(configured, synthetic_packet, api,
                                                      monkeypatch, tmp_path, fail):
    client, route = api
    monkeypatch.setattr(route, "prepare_full_draft_material", lambda request: synthetic_packet)
    monkeypatch.setattr(route, "get_llm_provider", provider)
    article_text = ARTICLE.lstrip()  # candidate contract requires a Markdown title at column 0
    calls = install_stream(monkeypatch, [sse({"content": article_text}), b"data: [DONE]\n\n"],
                           connect_error=httpx.ConnectTimeout("synthetic only") if fail else None)
    response = client.post("/api/generation/generate", json={"title": "合成测试", "document_type": "工作总结",
        "generation_route": "full_article_drafting", "temperature": 0.45})
    assert response.status_code == (502 if fail else 200), response.text
    run, = (tmp_path / "full-draft").iterdir()
    rows = read_trace(run)
    accounting = json.loads((run / "call_accounting.json").read_bytes())
    assert accounting["run_id"] == run.name == rows[-1]["run_id"]
    assert accounting["writer_trace"] == trace.TRACE_FILENAME
    assert accounting["retry_limit"] == 0 and accounting["writer_http_attempts"] == 1
    assert len(calls) == 1 and rows[0]["event"] == "writer_running"
    assert rows[0]["outcome"] == "running" and rows[-1]["offsets"]["writer_running"] is not None
    assert rows[-1]["offsets"]["provider_return"] is not None
    if fail:
        assert accounting["status"] == "FAILED"
        assert rows[-1]["transport_error_type"] == "ConnectTimeout"
    else:
        article = response.json()["articles"][0]
        assert article["generation_provenance"]["run_id"] == run.name
        assert article["article_markdown"] == article_text
        assert (run / "RAW_WRITER_ARTICLE.md").read_bytes() == article_text.encode()
        assert (run / "FINAL_DELIVERED_ARTICLE.md").read_bytes() == article_text.encode()
    assert trace._active_trace.get() is None
