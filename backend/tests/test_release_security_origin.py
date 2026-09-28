"""Strict desktop HTTP boundary; actual stateful router calls remain offline."""
import pytest
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient

import desktop_launcher
from app.routers import health, library, settings
from test_release_security_secrets import isolated

ORIGIN = "http://127.0.0.1:8765"


@pytest.fixture
def client(isolated, monkeypatch):
    app = FastAPI()
    app.include_router(health.router)
    app.include_router(settings.router, prefix="/api/settings")
    app.include_router(library.router, prefix="/api/library")
    app.add_api_route("/", lambda: HTMLResponse("<html>offline</html>"), methods=["GET", "HEAD"])
    monkeypatch.setattr(library, "update_scene_tags", lambda *args: None)
    monkeypatch.setattr(library, "delete_material_document", lambda *args: False)
    with TestClient(desktop_launcher.DesktopBoundary(app, "127.0.0.1:8765", "test"), base_url=ORIGIN) as client:
        yield client


@pytest.mark.parametrize("method,path", [("GET", "/api/health"), ("GET", "/"), ("HEAD", "/")])
def test_read_requests_without_origin_still_work(client, method, path):
    assert client.request(method, path).status_code == 200


@pytest.mark.parametrize("method,path,body,allowed_status", [
    ("POST", "/api/settings", {}, 200),
    ("PUT", "/api/library/documents/missing/tags", [], 404),
    ("DELETE", "/api/library/documents/missing", None, 404),
    # No product PATCH route exists; still reject it at the desktop boundary.
    ("PATCH", "/api/settings", {}, 405),
])
@pytest.mark.parametrize("origin", [None, "null", "http://localhost:8765", "http://127.0.0.1:8766",
    "https://127.0.0.1:8765", "https://external.invalid", ORIGIN + "/", ORIGIN + " evil", "malformed", ORIGIN])
def test_exact_origin_on_all_stateful_methods(client, method, path, body, allowed_status, origin):
    headers = {} if origin is None else {"Origin": origin}
    response = client.request(method, path, headers=headers, json=body)
    assert response.status_code == (allowed_status if origin == ORIGIN else 403)
    assert response.headers.get("access-control-allow-origin") != "*"


@pytest.mark.parametrize("headers", [
    {"host": "localhost:8765", "origin": ORIGIN},
    {"host": "127.0.0.1:9999", "origin": ORIGIN},
    [("host", "127.0.0.1:8765"), ("host", "evil.invalid"), ("origin", ORIGIN)],
    [("origin", ORIGIN), ("origin", "null")],
    [("origin", b"\xff")],
])
def test_wrong_duplicate_or_malformed_headers_fail_closed(client, headers):
    assert client.post("/api/settings", headers=headers, json={}).status_code == 403
