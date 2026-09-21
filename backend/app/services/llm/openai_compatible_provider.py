import hashlib
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import parse_qsl, urlsplit, urlunsplit

import httpx

from app.config import ProviderConfig
from app.services.llm.base import BaseLLMProvider
from app.utils.http_client import get_httpx_request_kwargs
from app.utils.runtime_paths import get_logs_dir

logger = logging.getLogger(__name__)

LLM_CONNECTION_ERROR_MESSAGE = "外部模型连接中断，未收到完整生成内容。系统已自动重试但仍失败，请稍后重试，或降低字数后再生成。"
LLM_INVALID_RESPONSE_MESSAGE = "外部模型未返回有效正文。"
MAX_LLM_REQUEST_RETRIES = 2
RETRYABLE_LLM_ERROR_MARKERS = (
    "incomplete chunked read",
    "peer closed connection without sending complete message body",
    "connection reset",
    "connection aborted",
    "timeout",
    "read timeout",
    "remote protocol error",
    "server disconnected",
    "network connection lost",
)
LLM_PROVIDER_DIAGNOSTICS_FILENAME = "llm_provider_diagnostics.jsonl"
HTTP_ERROR_BODY_LIMIT = 65536


def _http_error_diagnostic(exc: httpx.HTTPStatusError, request_kwargs: dict) -> dict:
    """Capture an error before stream closure; never persist the raw body/prompt.

    Unknown JSON fields and response headers are excluded. Echoes of credentials,
    quoted values and prompt fragments are redacted from provider-owned text.
    Reading is bounded and uses the existing request timeout, without a new call.
    """
    response, request = exc.response, exc.request
    payload = request_kwargs["json"]
    url = urlsplit(str(request.url))
    secrets = [str(v) for k, v in request_kwargs["headers"].items()
               if k.lower() not in {"content-type", "accept"}]
    secrets += [v.removeprefix("Bearer ") for v in secrets]
    secrets += [v for _, v in parse_qsl(url.query)]
    secrets += [url.username or "", url.password or ""]
    prompts = [str(m.get("content", "")) for m in payload.get("messages", [])]

    def safe(value, limit=2048):
        if value is None:
            return None
        if not isinstance(value, (str, int, float)) or isinstance(value, bool):
            return "[REDACTED_NON_SCALAR]"
        value = str(value)
        for secret in sorted(set(secrets), key=len, reverse=True):
            if secret:
                value = value.replace(secret, "[REDACTED]")
        value = value[:limit]
        value = re.sub(r"(?i)\b(?:bearer\s+\S+|sk-[a-z0-9_-]+)", "[REDACTED]", value)
        value = re.sub(r"(?i)\b(?:authorization|cookie|secret|api[_-]?key)\s*[:=]\s*[^\r\n]+",
                       "[REDACTED]", value)
        # Conservative: a server may echo only a fragment, not the full prompt.
        if any(value[i:i + 8] in prompt for prompt in prompts if prompt
               for i in range(max(0, len(value) - 7))):
            return "[REDACTED_PROMPT_ECHO]"
        if any(prompt and len(prompt) < 8 and prompt in value for prompt in prompts):
            return "[REDACTED_PROMPT_ECHO]"
        value = re.sub(r'"[^"\r\n]*"|\'[^\'\r\n]*\'', '[REDACTED_QUOTED_VALUE]', value)
        return " ".join(value.split())[:limit]

    try:
        wire = request.content
    except httpx.RequestNotRead:
        wire = httpx.Request("POST", request_kwargs["url"], json=payload).content
    diagnostic = {
        "event": "LLM_HTTP_STATUS_ERROR", "timestamp": datetime.now(timezone.utc).isoformat(),
        "status_code": response.status_code, "reason": safe(response.reason_phrase, 128),
        "request_method": request.method,
        "endpoint": safe(urlunsplit((url.scheme, url.netloc.rsplit('@', 1)[-1], url.path, '', '')), 512),
        "model": safe(payload.get("model"), 128), "stream": bool(payload.get("stream", False)),
        "request_payload_sha256": hashlib.sha256(wire).hexdigest(), "request_payload_bytes": len(wire),
        "provider_error_code": None, "provider_error_message": None, "request_id": None,
        "response_headers": {}, "error_body_truncated": False,
    }
    for key in ("content-type", "content-length", "retry-after", "x-request-id",
                "x-dashscope-request-id", "x-trace-id"):
        if key in response.headers:
            diagnostic["response_headers"][key] = safe(response.headers[key], 256)
    diagnostic["request_id"] = (diagnostic["response_headers"].get("x-request-id")
                                or diagnostic["response_headers"].get("x-dashscope-request-id"))
    try:
        raw = bytearray()
        for chunk in response.iter_bytes(chunk_size=4096):
            raw.extend(chunk[:HTTP_ERROR_BODY_LIMIT + 1 - len(raw)])
            if len(raw) > HTTP_ERROR_BODY_LIMIT:
                diagnostic["error_body_truncated"] = True
                break
        if not diagnostic["error_body_truncated"]:
            body = json.loads(raw)
            error = body.get("error", body) if isinstance(body, dict) else None
            if isinstance(error, dict):
                diagnostic["provider_error_code"] = safe(error.get("code"), 256)
                diagnostic["provider_error_message"] = safe(error.get("message"))
            if isinstance(body, dict) and not diagnostic["request_id"]:
                diagnostic["request_id"] = safe(body.get("request_id"), 256)
    except Exception as read_error:
        diagnostic["error_body_read_error"] = type(read_error).__name__
    return diagnostic


class LLMConnectionError(RuntimeError):
    """Raised when the external LLM connection fails after retries."""


class LLMInvalidResponseError(RuntimeError):
    """Raised when the external LLM returns no usable content after retries."""


class SSEParseError(LLMInvalidResponseError):
    """Malformed SSE data; raw response text must never enter logs."""


def _is_retryable_llm_connection_error(exc: Exception) -> bool:
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.RequestError):
        return True

    message = str(exc).lower()
    return any(marker in message for marker in RETRYABLE_LLM_ERROR_MARKERS)


def _append_invalid_response_diagnostic(entry: dict[str, object]) -> None:
    """Best-effort persistence for non-sensitive invalid-response metadata."""
    try:
        diagnostics_path = get_logs_dir() / LLM_PROVIDER_DIAGNOSTICS_FILENAME
        with diagnostics_path.open("a", encoding="utf-8") as diagnostics_file:
            diagnostics_file.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.warning(
            "LLM_INVALID_RESPONSE_DIAGNOSTIC_WRITE_FAILED error_type=%s",
            type(exc).__name__,
        )


def _value_diagnostic(container: object, field_name: str) -> dict[str, object]:
    exists = isinstance(container, dict) and field_name in container
    value = container.get(field_name) if isinstance(container, dict) else None
    if isinstance(value, str):
        is_empty = not value.strip()
        character_length = len(value)
    elif value is None:
        is_empty = True
        character_length = 0
    else:
        is_empty = hasattr(value, "__len__") and len(value) == 0
        character_length = len(str(value))
    return {
        "exists": exists,
        "type": type(value).__name__ if exists else "missing",
        "empty": is_empty,
        "length": character_length,
    }


def _redact_sensitive_text(value: object, *, secret: str) -> object:
    if not isinstance(value, str) or not secret:
        return value
    return value.replace(secret, "[REDACTED]")


def _build_invalid_response_diagnostic(
    *,
    payload: object,
    response_status_code: int,
    attempt: int,
    model: str,
    api_key: str,
) -> dict[str, object]:
    payload_keys = (
        sorted(_redact_sensitive_text(str(key), secret=api_key) for key in payload)
        if isinstance(payload, dict)
        else []
    )
    choices_exists = isinstance(payload, dict) and "choices" in payload
    choices = payload.get("choices") if isinstance(payload, dict) else None
    choices_count = len(choices) if isinstance(choices, list) else 0
    first_choice = choices[0] if choices_count else None
    first_choice_keys = (
        sorted(_redact_sensitive_text(str(key), secret=api_key) for key in first_choice)
        if isinstance(first_choice, dict)
        else []
    )
    message_exists = isinstance(first_choice, dict) and "message" in first_choice
    message = first_choice.get("message") if isinstance(first_choice, dict) else None
    message_fields = (
        sorted(_redact_sensitive_text(str(key), secret=api_key) for key in message)
        if isinstance(message, dict)
        else []
    )
    other_message_text_fields = []
    if isinstance(message, dict):
        for key, value in message.items():
            if key in {"content", "reasoning_content"} or not isinstance(value, str):
                continue
            other_message_text_fields.append(
                {
                    "field_name": _redact_sensitive_text(str(key), secret=api_key),
                    "type": type(value).__name__,
                    "empty": not value.strip(),
                    "length": len(value),
                }
            )

    diagnostic_entry: dict[str, object] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "attempt": attempt,
        "model": _redact_sensitive_text(model, secret=api_key),
        "http_status": response_status_code,
        "response_json_type": type(payload).__name__,
        "response_json_keys": payload_keys,
        "choices": {
            "exists": choices_exists,
            "type": type(choices).__name__ if choices_exists else "missing",
            "count": choices_count,
        },
        "first_choice": {
            "type": type(first_choice).__name__ if choices_count else "missing",
            "keys": first_choice_keys,
        },
        "message": {
            "exists": message_exists,
            "type": type(message).__name__ if message_exists else "missing",
            "keys": message_fields,
        },
        "content": _value_diagnostic(message, "content"),
        "reasoning_content": _value_diagnostic(message, "reasoning_content"),
        "other_message_text_fields": other_message_text_fields,
        # Preserve established diagnostic fields for existing offline consumers.
        "finish_reason": _redact_sensitive_text(
            first_choice.get("finish_reason") if isinstance(first_choice, dict) else None,
            secret=api_key,
        ),
        "message_fields": message_fields,
        "content_length": _value_diagnostic(message, "content")["length"],
        "reasoning_content_present": (
            isinstance(message, dict) and "reasoning_content" in message
        ),
        "reasoning_content_length": _value_diagnostic(message, "reasoning_content")[
            "length"
        ],
        "choices_count": choices_count,
    }
    return diagnostic_entry


class OpenAICompatibleProvider(BaseLLMProvider):
    """Chat completions with opt-in SSE, aggregated entirely inside this owner.

    ``config.stream`` defaults to False. Streaming uses the same timeout, proxy
    policy and retry budget as POST. A partial stream is never returned as a
    completed response. Last-attempt transport metadata is available locally in
    ``last_response_metadata`` without changing the public string interface.
    """

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self.last_response_metadata: dict[str, object] = {}

    def _raise_for_status(self, response: httpx.Response, request_kwargs: dict, metadata: dict) -> None:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            metadata["http_status"] = response.status_code
            metadata["error_type"] = type(exc).__name__
            try:
                diagnostic = _http_error_diagnostic(exc, request_kwargs)
                metadata["http_error"] = diagnostic
                metadata["request_id"] = diagnostic["request_id"]
                _append_invalid_response_diagnostic(diagnostic)
            except Exception as diagnostic_error:
                # Diagnostics must never replace the HTTP error or enable retry.
                metadata["http_error_diagnostic_failure"] = type(diagnostic_error).__name__
            raise

    def _receive_stream(self, request_kwargs: dict, metadata: dict) -> tuple[dict, int]:
        """Read UTF-8 SSE events, independent of HTTP byte/line boundaries.

        Only delta.content contributes to the answer. DONE or a finish_reason
        is required at EOF, so a clean but premature close cannot deliver a
        truncated answer. Metadata and counters describe this attempt only.
        """
        parts: list[str] = []
        started = time.monotonic()

        def consume_event(data: str) -> bool:
            if data.strip() == "[DONE]":
                metadata["done"] = True
                return True
            metadata["chunk_count"] += 1
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError as exc:
                raise SSEParseError("SSE parse error: invalid JSON") from exc
            if not isinstance(chunk, dict) or "error" in chunk:
                raise SSEParseError("SSE parse error: invalid completion event")
            for source, target in (("model", "response_model"), ("id", "response_id")):
                if isinstance(chunk.get(source), str):
                    metadata[target] = chunk[source]
            if isinstance(chunk.get("request_id"), str):
                metadata["request_id"] = chunk["request_id"]
            choices = chunk.get("choices")
            if not isinstance(choices, list):
                raise SSEParseError("SSE parse error: choices must be a list")
            if not choices:  # usage-only event
                return False
            choice = choices[0]
            if not isinstance(choice, dict):
                raise SSEParseError("SSE parse error: invalid choice")
            finish_reason = choice.get("finish_reason")
            if finish_reason is not None:
                if not isinstance(finish_reason, str):
                    raise SSEParseError("SSE parse error: invalid finish_reason")
                metadata["finish_reason"] = finish_reason
            delta = choice.get("delta")
            if delta is None:
                delta = {}
            if not isinstance(delta, dict):
                raise SSEParseError("SSE parse error: invalid delta")
            content = delta.get("content")
            if content is not None:
                if not isinstance(content, str):
                    raise SSEParseError("SSE parse error: content must be a string")
                parts.append(content)
                metadata["received_character_count"] += len(content)
            return False

        try:
            with httpx.stream("POST", **request_kwargs) as response:
                self._raise_for_status(response, request_kwargs, metadata)
                metadata["http_status"] = response.status_code
                metadata["request_id"] = (
                    response.headers.get("x-request-id")
                    or response.headers.get("x-dashscope-request-id")
                )
                # SSE is UTF-8, even when a server omits/misstates its charset.
                response.encoding = "utf-8-sig"  # Also consume an optional initial BOM.
                data_lines: list[str] = []
                for line in response.iter_lines():
                    if not line:
                        if data_lines:
                            done = consume_event("\n".join(data_lines))
                            data_lines.clear()
                            if done:
                                break
                        continue
                    field, separator, value = line.partition(":")
                    if field == "data":
                        data_lines.append(value.removeprefix(" ") if separator else "")
                    # SSE comments, event, id and retry fields carry no content.
                if data_lines:
                    consume_event("\n".join(data_lines))
                content = "".join(parts)
                if content.strip() and not (metadata["done"] or metadata["finish_reason"]):
                    raise httpx.RemoteProtocolError(
                        "SSE stream ended before completion marker", request=response.request
                    )
                return {
                    "choices": [{
                        "message": {"content": content},
                        "finish_reason": metadata["finish_reason"],
                    }]
                }, response.status_code
        except Exception as exc:
            metadata["error_type"] = type(exc).__name__
            raise
        finally:
            metadata["elapsed"] = round(time.monotonic() - started, 3)
            # Do not log raw exception messages or server-controlled metadata:
            # either can echo credentials, prompts or private evidence.
            logger.info(
                "LLM_STREAM_TRANSPORT stream=true elapsed=%s chunk_count=%s "
                "received_character_count=%s error_type=%s",
                metadata["elapsed"], metadata["chunk_count"],
                metadata["received_character_count"], metadata.get("error_type"),
            )

    def generate_text(
        self,
        prompt: str,
        system_prompt: str = "",
        temperature: float = 0.7,
        *,
        retry_limit: int | None = None,
        preserve_whitespace: bool = False,
        reasoning_effort: str | None = None,
        on_http_request_attempt: Callable[[int], None] | None = None,
        on_invalid_response_diagnostic: Callable[[dict[str, object]], None] | None = None,
    ) -> str:
        self._validate_config()
        # Opt-in per request; never mutate shared configuration or Writer defaults.
        # DashScope Chat Completions uses a top-level reasoning_effort field.
        if reasoning_effort is not None:
            if reasoning_effort not in ("none", "low", "medium", "xhigh"):
                raise ValueError("Unsupported reasoning_effort")
            if (self._build_url() != "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
                    or self.config.model not in ("qwen3.8-max", "qwen3.8-max-0902", "qwen3.8-max-2026-09-02")):
                raise ValueError("reasoning_effort is not verified for this endpoint/model")
            # W3-6E: none is enabled only for the current exact model;
            # retain the existing low/medium/xhigh capability boundary.
            if reasoning_effort == "none" and self.config.model != "qwen3.8-max-0902":
                raise ValueError("reasoning_effort is not verified for this endpoint/model")

        effective_retry_limit = (
            MAX_LLM_REQUEST_RETRIES if retry_limit is None else retry_limit
        )
        if not isinstance(effective_retry_limit, int) or effective_retry_limit < 0:
            raise ValueError("retry_limit must be a non-negative integer or None")
        total_attempts = effective_retry_limit + 1

        for attempt in range(1, total_attempts + 1):
            metadata = {
                "stream": self.config.stream, "attempt": attempt,
                "finish_reason": None, "response_model": None,
                "response_id": None, "request_id": None, "done": False,
                "chunk_count": 0, "received_character_count": 0,
            }
            self.last_response_metadata = metadata
            try:
                if on_http_request_attempt is not None:
                    on_http_request_attempt(attempt)
                request_kwargs = dict(
                    url=self._build_url(),
                    headers=self._build_headers(),
                    json={
                        "model": self.config.model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": temperature,
                    },
                    timeout=self.config.timeout,
                    follow_redirects=False,
                    **get_httpx_request_kwargs("LLM_HTTP"),
                )
                if reasoning_effort is not None:
                    request_kwargs["json"]["reasoning_effort"] = reasoning_effort
                if self.config.stream:
                    request_kwargs["json"]["stream"] = True
                    payload, response_status_code = self._receive_stream(request_kwargs, metadata)
                else:
                    response = httpx.post(**request_kwargs)
            except Exception as exc:
                if isinstance(exc, (httpx.HTTPStatusError, SSEParseError)):
                    raise
                if not _is_retryable_llm_connection_error(exc):
                    raise
                logger.warning(
                    "LLM_CONNECTION_INTERRUPTED attempt=%s/%s stream=%s error_type=%s",
                    attempt,
                    total_attempts,
                    self.config.stream,
                    type(exc).__name__,
                )
                if attempt >= total_attempts:
                    raise LLMConnectionError(LLM_CONNECTION_ERROR_MESSAGE) from exc
                time.sleep(min(attempt, 2))
                continue

            if not self.config.stream:
                self._raise_for_status(response, request_kwargs, metadata)
                payload = response.json()
                response_status_code = response.status_code

            choices = payload.get("choices") if isinstance(payload, dict) else None
            choices_count = len(choices) if isinstance(choices, list) else 0
            first_choice = choices[0] if choices_count else None
            message = first_choice.get("message") if isinstance(first_choice, dict) else None
            content = message.get("content") if isinstance(message, dict) else None

            if content and str(content).strip():
                return str(content) if preserve_whitespace else str(content).strip()

            diagnostic_entry = _build_invalid_response_diagnostic(
                payload=payload,
                response_status_code=response_status_code,
                attempt=attempt,
                model=self.config.model,
                api_key=self.config.api_key,
            )
            if self.config.stream:
                metadata["error_type"] = "LLMInvalidResponseError"
                # Keep raw metadata in memory, but never persist server-echoed text.
                if diagnostic_entry["finish_reason"] not in {
                    None, "stop", "length", "tool_calls", "content_filter", "function_call",
                }:
                    diagnostic_entry["finish_reason"] = "[REDACTED]"
            logger.warning(
                "LLM_INVALID_RESPONSE attempt=%s/%s http_status=%s model=%s "
                "finish_reason=%s message_fields=%s content_length=%s "
                "reasoning_content_present=%s reasoning_content_length=%s choices_count=%s",
                attempt,
                total_attempts,
                response_status_code,
                diagnostic_entry["model"],
                diagnostic_entry["finish_reason"],
                diagnostic_entry["message_fields"],
                diagnostic_entry["content_length"],
                diagnostic_entry["reasoning_content_present"],
                diagnostic_entry["reasoning_content_length"],
                choices_count,
            )
            _append_invalid_response_diagnostic(diagnostic_entry)
            if on_invalid_response_diagnostic is not None:
                try:
                    on_invalid_response_diagnostic(dict(diagnostic_entry))
                except Exception as exc:
                    logger.warning(
                        "LLM_INVALID_RESPONSE_DIAGNOSTIC_OBSERVER_FAILED error_type=%s",
                        type(exc).__name__,
                    )
            if attempt >= total_attempts:
                if self.config.stream:
                    raise LLMInvalidResponseError(LLM_INVALID_RESPONSE_MESSAGE) from ValueError(
                        "response completed without content"
                    )
                raise LLMInvalidResponseError(LLM_INVALID_RESPONSE_MESSAGE)
            time.sleep(min(attempt, 2))

        raise LLMInvalidResponseError(LLM_INVALID_RESPONSE_MESSAGE)  # pragma: no cover

    def _validate_config(self) -> None:
        missing_fields = []
        if not self.config.base_url.strip():
            missing_fields.append("base_url")
        if not self.config.api_key.strip():
            missing_fields.append("api_key")
        if not self.config.model.strip():
            missing_fields.append("model")

        if missing_fields:
            raise ValueError(f"缺少必要配置：{', '.join(missing_fields)}")

    def _build_url(self) -> str:
        base_url = self.config.base_url.rstrip("/")
        endpoint_path = self.config.endpoint_path.strip() or "/v1/chat/completions"
        if not endpoint_path.startswith("/"):
            endpoint_path = f"/{endpoint_path}"
        return f"{base_url}{endpoint_path}"

    def _build_headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        headers.update({str(key): str(value) for key, value in self.config.headers.items()})
        return headers
