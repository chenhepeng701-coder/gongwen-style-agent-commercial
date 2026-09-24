"""Shared HTTP client helpers."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import os
import sys
import urllib.request

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


def describe_effective_proxy(url: str, request_kwargs: dict) -> dict:
    """Read-only, credential-free snapshot of HTTPX's proxy selection for a URL.

    HTTPX 0.28.1 uses urllib.getproxies (including Windows registry fallback),
    then sorted URLPattern mounts for NO_PROXY. Reuse those exact rules without
    constructing a client, modifying kwargs, or opening a network connection.
    This is configuration evidence, not proof of the eventual network route.
    """
    state = {"method": "httpx_environment_proxies_and_urlpattern",
             "trust_env": request_kwargs.get("trust_env", True),
             "resolution_source": "unknown", "effective_proxy": "unknown",
             "proxy_configured": None, "bypassed": None}
    try:
        if request_kwargs.get("proxy") is not None:
            state.update(resolution_source="explicit", effective_proxy="explicit",
                         proxy_configured=True, bypassed=False)
        elif not state["trust_env"]:
            state.update(resolution_source="disabled", effective_proxy="none",
                         proxy_configured=False, bypassed=False)
        else:
            import httpx
            from httpx._utils import URLPattern, get_environment_proxies

            # A nonempty environment mapping suppresses the registry fallback,
            # even if it contains only NO_PROXY or an unrelated proxy scheme.
            env = urllib.request.getproxies_environment()
            source = "environment" if env else (
                "windows_system" if sys.platform == "win32" else "system")
            mounts = get_environment_proxies()
            selected = next(((pattern, value) for pattern, value in sorted(
                (URLPattern(key), value) for key, value in mounts.items())
                if pattern.matches(httpx.URL(url))), None)
            configured = any(value is not None for value in mounts.values())
            state.update(resolution_source=source,
                         effective_proxy=source if selected is not None and selected[1] is not None else "none",
                         proxy_configured=configured,
                         bypassed=(selected is not None and selected[1] is None)
                         or ("*" in (host.strip() for host in env.get("no", "").split(","))))
    except Exception as exc:
        # Never turn an inspection failure into a false claim of direct access.
        state.update(effective_proxy="unknown", diagnostic_error_type=type(exc).__name__)
    return state
