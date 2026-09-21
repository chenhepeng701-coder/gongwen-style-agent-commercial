"""Shared HTTP client helpers."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import os

logger = logging.getLogger(__name__)

_PROXY_ENV_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")
_BAD_PROXY_TARGETS = {
    "http://127.0.0.1:9",
    "https://127.0.0.1:9",
    "http://localhost:9",
    "https://localhost:9",
}


@dataclass
class HttpClientSettings:
    request_kwargs: dict
    proxy_mode: str
    invalid_proxy_ignored: bool
    invalid_proxy_value: str


def resolve_http_client_settings(service_name: str) -> HttpClientSettings:
    """Return httpx kwargs plus proxy diagnostics.

    By default httpx inherits system proxy settings from the environment.
    In this project that becomes a hard failure when the host environment
    injects a dead proxy like 127.0.0.1:9. Detect that case and opt out.
    """
    detected_bad_proxy = False
    detected_valid_proxy = False
    bad_proxy_value = ""

    for key in _PROXY_ENV_KEYS:
        value = os.getenv(key, "").strip()
        if not value:
            continue
        if value.lower() in _BAD_PROXY_TARGETS:
            detected_bad_proxy = True
            if not bad_proxy_value:
                bad_proxy_value = value
        else:
            detected_valid_proxy = True

    if detected_bad_proxy:
        logger.warning("INVALID_PROXY_IGNORED=%s", bad_proxy_value or "unknown")
        return HttpClientSettings(
            request_kwargs={"trust_env": False},
            proxy_mode="disabled",
            invalid_proxy_ignored=True,
            invalid_proxy_value=bad_proxy_value,
        )

    proxy_mode = "env" if detected_valid_proxy else "disabled"
    return HttpClientSettings(
        request_kwargs={},
        proxy_mode=proxy_mode,
        invalid_proxy_ignored=False,
        invalid_proxy_value="",
    )


def get_httpx_request_kwargs(service_name: str) -> dict:
    """Backward-compatible helper for existing call sites."""
    return resolve_http_client_settings(service_name).request_kwargs
