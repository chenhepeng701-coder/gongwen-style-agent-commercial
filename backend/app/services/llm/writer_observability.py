"""Content-free, best-effort Writer timeline in the existing run directory.

Only the formal Writer call opens this context. Provider hooks are no-ops for
other callers. Append milestone snapshots, not token text or one row per token.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import logging
import time

import httpx

from app.utils.http_client import describe_effective_proxy


logger = logging.getLogger(__name__)
TRACE_FILENAME = "writer_timeline.jsonl"
_active_trace = ContextVar("writer_performance_trace", default=None)
_STAGES = ("writer_running", "request_start", "response_headers", "first_sse",
           "first_reasoning", "last_reasoning", "first_content", "last_content",
           "first_finish_reason", "done_or_eof", "provider_return")
_FINISH_REASONS = {"stop", "length", "tool_calls", "content_filter", "function_call"}
_USAGE_FIELDS = {"prompt_tokens", "completion_tokens", "total_tokens", "input_tokens",
                 "output_tokens", "cached_tokens", "cache_creation_input_tokens",
                 "cache_read_input_tokens"}
_USAGE_DETAILS = {
    "prompt_tokens_details": {"cached_tokens", "audio_tokens"},
    "completion_tokens_details": {"reasoning_tokens", "audio_tokens",
                                  "accepted_prediction_tokens", "rejected_prediction_tokens"},
    "input_tokens_details": {"cached_tokens"},
    "output_tokens_details": {"reasoning_tokens"},
}


def _identifier(value):
    # IDs are server-controlled and can echo private text. A stable fingerprint
    # preserves correlation without trusting any raw server string as log-safe.
    if isinstance(value, str) and value:
        return {"sha256": hashlib.sha256(value.encode("utf-8")).hexdigest()}
    return None


def _usage(value):
    if not isinstance(value, dict):
        return None
    safe = {key: value[key] for key in _USAGE_FIELDS
            if type(value.get(key)) is int and value[key] >= 0}
    for key, fields in _USAGE_DETAILS.items():
        details = value.get(key)
        if isinstance(details, dict):
            safe[key] = {name: details[name] for name in fields
                         if type(details.get(name)) is int and details[name] >= 0}
    return safe or None


class _WriterTrace:
    def __init__(self, root, writer_attempt):
        self.path = root / TRACE_FILENAME
        self.started = time.monotonic()
        self.state = {"schema_version": 1, "run_id": root.name,
                      "writer_attempt": writer_attempt, "http_attempt": None,
                      "clock": "monotonic", "offset_unit": "seconds",
                      "offsets": dict.fromkeys(_STAGES), "first_sse_type": None,
                      "reasoning_event_count": 0, "content_event_count": 0,
                      "other_event_count": 0, "received_character_count": 0,
                      "received_bytes": None, "finish_reason": None,
                      "request_id": None, "response_id": None, "usage": None,
                      "proxy": None, "termination": None, "outcome": "running",
                      "error_type": None, "transport_error_type": None}

    def handle(self, event, values):
        now = round(time.monotonic() - self.started, 6)
        state, offsets = self.state, self.state["offsets"]
        persist = True
        if event == "writer_running":
            state["http_attempt"] = values["attempt"]
            offsets["writer_running"] = now
        elif event == "request_start":
            offsets["request_start"] = now
            state["proxy"] = describe_effective_proxy(values["url"], values["request_kwargs"])
        elif event == "response_headers":
            offsets[event] = now
            response = values["response"]
            state["http_status"] = response.status_code
            state["request_id"] = _identifier(response.headers.get("x-request-id")
                or response.headers.get("x-dashscope-request-id"))
        elif event == "sse":
            chunk = values.get("chunk")
            chunk = chunk if isinstance(chunk, dict) else {}
            choices = chunk.get("choices")
            choice = choices[0] if isinstance(choices, list) and choices else {}
            choice = choice if isinstance(choice, dict) else {}
            delta = choice.get("delta")
            delta = delta if isinstance(delta, dict) else {}
            reasoning = isinstance(delta.get("reasoning_content"), str) and bool(delta["reasoning_content"])
            content = isinstance(delta.get("content"), str) and bool(delta["content"])
            persist = offsets["first_sse"] is None
            if persist:
                offsets["first_sse"] = now
                state["first_sse_type"] = "reasoning_content" if reasoning else "content" if content else "other"
            for kind, present in (("reasoning", reasoning), ("content", content)):
                if present:
                    state[kind + "_event_count"] += 1
                    if offsets["first_" + kind] is None:
                        offsets["first_" + kind] = now
                        persist = True
                    offsets["last_" + kind] = now
            if content:
                state["received_character_count"] += len(delta["content"])
            if not (reasoning or content):
                state["other_event_count"] += 1
            for key in ("request_id", "response_id"):
                value = _identifier(chunk.get("id" if key == "response_id" else key))
                if value is not None:
                    state[key] = value
            usage = _usage(chunk.get("usage"))
            if usage is not None:
                state["usage"] = usage
            reason = choice.get("finish_reason")
            if reason is not None:
                state["finish_reason"] = reason if isinstance(reason, str) and reason in _FINISH_REASONS else "[REDACTED]"
                if offsets["first_finish_reason"] is None:
                    offsets["first_finish_reason"] = now
                    persist = True
        elif event == "stream_end":
            offsets["done_or_eof"] = now
            state["termination"] = values["termination"]
            if state["termination"] == "done" and offsets["first_sse"] is None:
                offsets["first_sse"] = now
                state["first_sse_type"] = "other"
        elif event == "transport_summary":
            response = values.get("response")
            if response is not None:
                state["received_bytes"] = response.num_bytes_downloaded
        elif event == "provider_return":
            offsets[event] = now
            exc = values.get("error")
            state["outcome"] = "error" if exc is not None else "success"
            state["error_type"] = type(exc).__name__ if exc is not None else None
            types, seen = [], set()
            while exc is not None and id(exc) not in seen:
                seen.add(id(exc))
                types.append(type(exc).__name__)
                if isinstance(exc, httpx.TransportError):
                    state["transport_error_type"] = type(exc).__name__
                exc = exc.__cause__ if exc.__cause__ is not None else exc.__context__
            state["exception_types"] = types
        if persist:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({**state, "event": event, "offset": now},
                                        ensure_ascii=False, allow_nan=False) + "\n")


def observe_writer(event, **values):
    """All observation work, including sanitization and I/O, fails open."""
    try:
        trace = _active_trace.get()
        if trace is not None:
            trace.handle(event, values)
    except Exception as exc:
        logger.warning("WRITER_OBSERVATION_FAILED error_type=%s", type(exc).__name__)


@contextmanager
def observe_writer_call(root, writer_attempt=1):
    token = None
    error = None
    try:
        token = _active_trace.set(_WriterTrace(root, writer_attempt))
    except Exception as exc:
        logger.warning("WRITER_OBSERVATION_FAILED error_type=%s", type(exc).__name__)
    try:
        yield
    except BaseException as exc:
        error = exc
        raise
    finally:
        observe_writer("provider_return", error=error)
        if token is not None:
            _active_trace.reset(token)
