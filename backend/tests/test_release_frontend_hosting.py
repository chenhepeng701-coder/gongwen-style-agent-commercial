from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.routers import health
from app.utils.frontend_hosting import mount_frontend


@pytest.fixture
def client(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text('<html><div id="root">React production</div></html>', encoding="utf-8")
    (tmp_path / "assets/main.js").write_text("window.production = true", encoding="utf-8")
    app = FastAPI()
    app.include_router(health.router)
    assert mount_frontend(app, tmp_path)
    with TestClient(app) as client:
        yield client


def test_index_assets_and_spa(client):
    assert 'id="root"' in client.get("/").text
    assert 'id="root"' in client.get("/writing/editor").text
    assert client.get("/assets/main.js").text == "window.production = true"
    assert client.get("/assets/missing.js").status_code == 404
    assert client.get("/static/missing").status_code == 404


def test_api_and_unknown_api_are_never_spa(client):
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/health").headers["content-type"].startswith("application/json")
    for method in (client.get, client.post):
        response = method("/api/unknown")
        assert response.status_code == 404
        assert response.json() == {"detail": "Not Found"}


def test_missing_source_build_allows_vite(tmp_path):
    assert not mount_frontend(FastAPI(), tmp_path)


def test_missing_packaged_build_fails_closed(tmp_path, monkeypatch):
    from app.utils import frontend_hosting
    monkeypatch.setattr(frontend_hosting, "is_packaged", lambda: True)
    with pytest.raises(RuntimeError, match="前端资源缺失"):
        mount_frontend(FastAPI(), tmp_path)
