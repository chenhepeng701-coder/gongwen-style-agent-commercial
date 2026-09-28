"""Real CurrentUser DPAPI, isolated runtime, synthetic credentials, no network."""
import json
import logging
from pathlib import Path
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
import yaml

from app import config
from app.routers import settings as routes
from app.utils import runtime_paths, secret_store

SECRET = "TEST_SECRET_DO_NOT_USE_123"
PROVIDER = "provider/deepseek/api_key"
EMBEDDING = "embedding/api_key"


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "install"), raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    monkeypatch.setattr(config, "CONFIG_PATH", runtime_paths.get_config_path())
    for name in ("GONGWEN_LLM_API_KEY", "GONGWEN_EMBEDDING_API_KEY", "DASHSCOPE_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    config.get_settings.cache_clear()
    yield tmp_path
    config.get_settings.cache_clear()


def write_legacy(*, old_llm=False):
    data = {"embedding": {"api_key": SECRET}, "current_provider": "deepseek"}
    provider = {"provider": "deepseek", "api_key": SECRET, "model": "offline"}
    data.update({"llm": provider} if old_llm else {"providers": {"deepseek": provider}})
    config.CONFIG_PATH.write_text(yaml.safe_dump(data), encoding="utf-8")
    return config.CONFIG_PATH.read_bytes()


def assert_no_plaintext(root):
    for path in root.rglob("*"):
        if path.is_file():
            raw = path.read_bytes()
            assert SECRET.encode() not in raw, path.name
            assert SECRET.encode("utf-16-le") not in raw, path.name


@pytest.mark.parametrize("identity", [PROVIDER, EMBEDDING])
def test_dpapi_round_trip_delete_and_reload(isolated, identity):
    assert secret_store.get_secret(identity) == ""
    secret_store.set_secret(identity, SECRET)
    assert secret_store.get_secret(identity) == SECRET
    assert_no_plaintext(isolated)
    # No in-memory cache: every read decrypts persisted CurrentUser bytes.
    first = secret_store._path(identity).read_bytes()
    secret_store.set_secret(identity, SECRET)
    assert secret_store._path(identity).read_bytes() != first
    assert secret_store.get_secret(identity) == SECRET
    secret_store.delete_secret(identity)
    assert secret_store.get_secret(identity) == ""


def test_ciphertext_identity_binding_and_tamper_fail_closed(isolated):
    secret_store.set_secret(PROVIDER, SECRET)
    secret_store._path(EMBEDDING).write_bytes(secret_store._path(PROVIDER).read_bytes())
    with pytest.raises(secret_store.SecretStoreError):
        secret_store.get_secret(EMBEDDING)
    raw = bytearray(secret_store._path(PROVIDER).read_bytes())
    raw[-1] ^= 1
    secret_store._path(PROVIDER).write_bytes(raw)
    with pytest.raises(secret_store.SecretStoreError):
        secret_store.get_secret(PROVIDER)


def test_explicit_saves_and_blank_values_keep_secure_keys(isolated):
    config.update_provider_config("deepseek", config.ProviderConfig(api_key=SECRET, model="offline"))
    config.update_embedding_config(config.EmbeddingConfig(api_key=SECRET, model="offline"))
    for _ in range(2):
        config.reload_settings()
        assert config.get_settings().providers["deepseek"].api_key == SECRET
        assert config.get_settings().embedding.api_key == SECRET
        config.update_provider_config("deepseek", config.ProviderConfig(api_key="", model="updated"))
        config.update_embedding_config(config.EmbeddingConfig(api_key="", model="updated"))
    data = yaml.safe_load(config.CONFIG_PATH.read_text(encoding="utf-8"))
    assert "api_key" not in data["providers"]["deepseek"]
    assert "api_key" not in data["embedding"]
    assert SECRET not in repr(config.get_settings())
    assert_no_plaintext(isolated)


@pytest.mark.parametrize("old_llm", [False, True])
def test_legacy_migration_verifies_then_atomically_removes_plaintext(isolated, monkeypatch, old_llm):
    write_legacy(old_llm=old_llm)
    original = config.atomic_write_text
    events = []
    def checked_replace(path, text, **kwargs):
        assert secret_store.get_secret(PROVIDER) == SECRET
        assert secret_store.get_secret(EMBEDDING) == SECRET
        assert SECRET in config.CONFIG_PATH.read_text(encoding="utf-8")
        assert SECRET not in text
        events.append("verified-before-atomic-rewrite")
        return original(path, text, **kwargs)
    monkeypatch.setattr(config, "atomic_write_text", checked_replace)
    assert config.load_settings().providers["deepseek"].api_key == SECRET
    assert events == ["verified-before-atomic-rewrite"]
    assert_no_plaintext(isolated)


@pytest.mark.parametrize("failure", ["write", "verify", "config-replace"])
def test_migration_failure_retains_exact_legacy_bytes(isolated, monkeypatch, failure):
    original = write_legacy()
    def fail(*args, **kwargs):
        raise OSError(SECRET)
    if failure == "write":
        monkeypatch.setattr(secret_store, "_crypt", fail)
    elif failure == "verify":
        monkeypatch.setattr(secret_store, "set_secret", lambda *args: None)
    else:
        monkeypatch.setattr(config, "atomic_write_text", fail)
    with pytest.raises(secret_store.SecretStoreError) as error:
        config.load_settings()
    assert SECRET not in str(error.value)
    assert config.CONFIG_PATH.read_bytes() == original


def test_migration_is_idempotent_and_never_overwrites_existing_secret(isolated, monkeypatch):
    write_legacy()
    config.load_settings()
    before = {p: p.read_bytes() for p in isolated.rglob("*") if p.is_file()}
    monkeypatch.setattr(secret_store, "set_secret", lambda *args: pytest.fail("Repeated migration"))
    for _ in range(2):
        assert config.reload_settings().embedding.api_key == SECRET
    assert all(p.read_bytes() == raw for p, raw in before.items())


def test_conflicting_existing_secure_value_does_not_erase_legacy(isolated, monkeypatch):
    before = write_legacy()
    # Model a different already-correct credential without introducing another test secret.
    monkeypatch.setattr(secret_store, "get_secret", lambda identity: "existing-credential")
    monkeypatch.setattr(secret_store, "set_secret", lambda *args: pytest.fail("Must not overwrite"))
    with pytest.raises(secret_store.SecretStoreError):
        config.load_settings()
    assert config.CONFIG_PATH.read_bytes() == before


def test_source_environment_overrides_without_automatic_persistence(isolated, monkeypatch):
    monkeypatch.setattr(sys, "frozen", False)
    monkeypatch.setattr(runtime_paths, "PROJECT_ROOT", isolated)
    monkeypatch.setattr(config, "BACKEND_DIR", isolated)
    monkeypatch.setattr(secret_store, "get_config_dir", lambda: config.CONFIG_PATH.parent)
    config.CONFIG_PATH.write_text("providers:\n  deepseek:\n    model: offline\ncurrent_provider: deepseek\n", encoding="utf-8")
    before = config.CONFIG_PATH.read_bytes()
    monkeypatch.setenv("GONGWEN_LLM_API_KEY", SECRET)
    monkeypatch.setenv("GONGWEN_EMBEDDING_API_KEY", SECRET)
    settings = config.load_settings()
    assert settings.providers["deepseek"].api_key == settings.embedding.api_key == SECRET
    assert config.CONFIG_PATH.read_bytes() == before
    config.save_settings(settings)  # Saving unrelated settings must not capture env values.
    assert secret_store.get_secret(PROVIDER) == secret_store.get_secret(EMBEDDING) == ""
    assert_no_plaintext(isolated)
    config.update_provider_config("deepseek", config.ProviderConfig(api_key=SECRET))
    monkeypatch.delenv("GONGWEN_LLM_API_KEY")
    assert config.reload_settings().providers["deepseek"].api_key == SECRET


def test_packaged_ignores_environment_and_placeholders_even_without_launcher(isolated, monkeypatch):
    monkeypatch.setenv("GONGWEN_LLM_API_KEY", SECRET)
    monkeypatch.setenv("GONGWEN_EMBEDDING_API_KEY", SECRET)
    config.CONFIG_PATH.write_text("providers:\n  deepseek:\n    api_key: ${GONGWEN_LLM_API_KEY}\nembedding:\n  api_key: ${GONGWEN_EMBEDDING_API_KEY}\n", encoding="utf-8")
    settings = config.load_settings()
    assert settings.providers["deepseek"].api_key == settings.embedding.api_key == ""
    assert secret_store.get_secret(PROVIDER) == secret_store.get_secret(EMBEDDING) == ""
    assert_no_plaintext(isolated)


def test_source_env_wins_over_store_without_replacing_stored_secret(isolated, monkeypatch):
    config.update_provider_config("deepseek", config.ProviderConfig(api_key=SECRET))
    config.update_embedding_config(config.EmbeddingConfig(api_key=SECRET))
    monkeypatch.setattr(sys, "frozen", False)
    monkeypatch.setattr(runtime_paths, "PROJECT_ROOT", isolated)
    monkeypatch.setattr(config, "BACKEND_DIR", isolated)
    monkeypatch.setattr(secret_store, "get_config_dir", lambda: config.CONFIG_PATH.parent)
    environment_value = SECRET + "_ENV_OVERRIDE"
    monkeypatch.setenv("GONGWEN_LLM_API_KEY", environment_value)
    monkeypatch.setenv("GONGWEN_EMBEDDING_API_KEY", environment_value)
    before = config.CONFIG_PATH.read_bytes()
    loaded = config.load_settings()
    assert loaded.providers["deepseek"].api_key == loaded.embedding.api_key == environment_value
    assert config.CONFIG_PATH.read_bytes() == before
    config.save_settings(loaded)
    assert secret_store.get_secret(PROVIDER) == secret_store.get_secret(EMBEDDING) == SECRET
    assert_no_plaintext(isolated)


@pytest.fixture
def client(isolated):
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/settings")
    with TestClient(app) as client:
        yield client


@pytest.mark.parametrize("path,runner", [("", "run_llm_connection_test"), ("/embedding", "run_embedding_connection_test")])
def test_api_redacts_get_connection_exception_validation_and_logs(client, isolated, monkeypatch, caplog, path, runner):
    caplog.set_level(logging.INFO)
    assert client.post("/api/settings" + path, json={"api_key": SECRET}).status_code == 200
    response = client.get("/api/settings" + path)
    assert response.json()["api_key_configured"] is True
    assert SECRET not in response.text
    monkeypatch.setattr(routes, runner, lambda cfg: {"success": False, "message": cfg.api_key, "response": cfg.api_key})
    response = client.post("/api/settings" + path + "/test", json={})
    assert response.status_code == 200 and SECRET not in response.text
    def failed(cfg):
        raise RuntimeError(SECRET)
    monkeypatch.setattr(routes, runner, failed)
    response = client.post("/api/settings" + path + "/test", json={})
    assert response.status_code == 200 and not response.json()["success"]
    assert SECRET not in response.text
    response = client.post("/api/settings" + path, json={"api_key": [SECRET]})
    assert response.status_code == 422 and SECRET not in response.text
    assert SECRET not in caplog.text
    assert_no_plaintext(isolated)


def test_failed_migration_api_returns_public_503_without_success_or_secret(client, monkeypatch, caplog):
    original = write_legacy()
    monkeypatch.setattr(secret_store, "_crypt", lambda *a, **kw: (_ for _ in ()).throw(OSError(SECRET)))
    response = client.get("/api/settings")
    assert response.status_code == 503
    assert response.json()["detail"]["error_code"] == "SECRET_STORE_UNAVAILABLE"
    assert SECRET not in response.text + caplog.text
    assert config.CONFIG_PATH.read_bytes() == original
