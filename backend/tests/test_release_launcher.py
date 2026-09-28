import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import desktop_launcher as launcher
from app.routers.health import router


def test_single_instance_lock_and_release(tmp_path):
    first = launcher.InstanceLock(tmp_path / "desktop.lock")
    second = launcher.InstanceLock(tmp_path / "desktop.lock")
    assert first.acquire()
    assert not second.acquire()
    first.close()
    assert second.acquire()
    second.close()


def test_desktop_http_boundary():
    app = FastAPI()
    app.include_router(router)
    with TestClient(launcher.DesktopBoundary(app, "127.0.0.1:8765", "identity"), base_url="http://127.0.0.1:8765") as client:
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.headers["x-gongwen-instance"] == "identity"
        assert client.get("/api/health", headers={"host": "attacker.example"}).status_code == 403
        assert client.post("/api/settings", headers={"origin": "https://attacker.example"}).status_code == 403


def test_second_launch_opens_only_verified_existing_instance(tmp_path, monkeypatch):
    state = tmp_path / "instance.json"
    state.write_text(json.dumps({"port": 8765, "identity": "verified"}))
    monkeypatch.setattr(launcher, "healthy", lambda port, identity: port == 8765 and identity == "verified")
    opened = []
    monkeypatch.setattr(launcher.webbrowser, "open", opened.append)
    launcher.open_existing(state)
    assert opened == ["http://127.0.0.1:8765/"]


def test_startup_failure_and_stale_instance_are_explicit(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "healthy", lambda *args: False)
    with pytest.raises(RuntimeError, match="启动失败"):
        launcher.wait_for_health(8765, "missing", lambda: False)
    with pytest.raises(RuntimeError, match="正在启动或退出"):
        launcher.open_existing(tmp_path / "missing", timeout=0)


def test_health_rejects_non_local_port_data_without_connecting():
    assert not launcher.healthy("https://foreign.example", "id")
    assert not launcher.healthy(80, "id")
