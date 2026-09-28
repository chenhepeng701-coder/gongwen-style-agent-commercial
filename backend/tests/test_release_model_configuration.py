"""Exercise the existing settings API using synthetic credentials only."""
import logging
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import config
from app.routers import settings
from app.utils import runtime_paths


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "install"), raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    for name in ("GONGWEN_LLM_API_KEY", "GONGWEN_EMBEDDING_API_KEY", "WRITING_REFERENCE_PACK_PATH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config, "CONFIG_PATH", runtime_paths.get_config_path())
    config.get_settings.cache_clear()
    app = FastAPI()
    app.include_router(settings.router, prefix="/api/settings")
    with TestClient(app) as client:
        yield client
    config.get_settings.cache_clear()


def test_keys_persist_and_existing_connection_apis_use_saved_credentials(client, monkeypatch, caplog):
    from app.services.llm import connection_test
    from app.services.library import embedder
    caplog.set_level(logging.INFO)
    llm = dict(provider_key="deepseek", api_key="SYNTHETIC_LLM_SECRET", base_url="https://invalid.example", model="offline")
    embedding = dict(api_key="SYNTHETIC_EMBED_SECRET", base_url="https://invalid.example", model="offline")
    assert client.post("/api/settings", json=llm).json()["success"]
    assert client.post("/api/settings/embedding", json=embedding).json()["success"]
    config.reload_settings()
    seen = []

    class OfflineProvider:
        def generate_text(self, *args, **kwargs):
            return "合成连接成功"

    def provider(cfg):
        seen.append(cfg.api_key)
        return OfflineProvider()

    def vectors(cfg, model, texts):
        seen.append(cfg.api_key)
        return [[0.1, 0.2]]

    monkeypatch.setattr(connection_test, "get_llm_provider_from_config", provider)
    monkeypatch.setattr(embedder, "_call_embedding_api", vectors)
    for path, payload in (("", llm), ("/embedding", embedding)):
        public = client.get("/api/settings" + path)
        assert public.json()["api_key_configured"] is True
        assert payload["api_key"] not in public.text
        blank = {**payload, "api_key": ""}
        assert client.post("/api/settings" + path + "/test", json=blank).json()["success"]
        assert client.post("/api/settings" + path, json=blank).json()["success"]
    assert seen == [llm["api_key"], embedding["api_key"]]
    assert config.load_settings().embedding.api_key == embedding["api_key"]
    assert all(secret not in caplog.text for secret in seen)
    assert config.CONFIG_PATH.parts[-3:] == ("GongwenWritingAssistant", "config", "config.yaml")


@pytest.mark.parametrize("path,runner", [("/test", "run_llm_connection_test"), ("/embedding/test", "run_embedding_connection_test")])
def test_provider_echo_cannot_expose_key_to_browser(client, monkeypatch, path, runner):
    secret = "SYNTHETIC_ECHO_SECRET"
    monkeypatch.setattr(settings, runner, lambda cfg: {"success": False, "message": "failed " + cfg.api_key, "response": cfg.api_key})
    response = client.post("/api/settings" + path, json={"api_key": secret})
    assert response.status_code == 200
    assert secret not in response.text
    assert "REDACTED_API_KEY" in response.text


def test_packaged_config_does_not_read_dotenv(client, monkeypatch):
    class ForbiddenSource:
        @property
        def parent(self):
            raise AssertionError("Packaged apps must not look for a source .env")
    monkeypatch.setattr(config, "BACKEND_DIR", ForbiddenSource())
    config.load_settings()
