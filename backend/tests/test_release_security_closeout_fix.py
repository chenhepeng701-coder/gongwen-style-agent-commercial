"""B1/B2 regressions: synthetic credentials, real DPAPI, offline startup seam."""
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
import yaml

from app import config
from app.utils import runtime_paths, secret_store
import desktop_launcher as launcher
from test_release_security_secrets import SECRET, PROVIDER, EMBEDDING, isolated, client, write_legacy


STRIPPED_ENV = (
    "GONGWEN_LLM_API_KEY", "GONGWEN_EMBEDDING_API_KEY", "DASHSCOPE_API_KEY",
    "WRITING_REFERENCE_PACK_PATH", "QUALITY_TEST_MODE", "ENABLE_WRITING_STYLE_LIBRARY",
)


def write_placeholders(provider_env="DASHSCOPE_API_KEY", embedding_env="CUSTOM_EMBED_KEY"):
    data = {"providers": {"deepseek": {"api_key": "${" + provider_env + "}", "model": "offline"}},
            "current_provider": "deepseek", "embedding": {"api_key": "${" + embedding_env + "}"}}
    config.CONFIG_PATH.write_text(yaml.safe_dump(data), encoding="utf-8")
    return data


@pytest.fixture
def source(isolated, monkeypatch):
    monkeypatch.setattr(sys, "frozen", False)
    monkeypatch.setattr(runtime_paths, "PROJECT_ROOT", isolated)
    monkeypatch.setattr(config, "BACKEND_DIR", isolated)
    monkeypatch.setattr(secret_store, "get_config_dir", lambda: config.CONFIG_PATH.parent)
    monkeypatch.setenv("DASHSCOPE_API_KEY", SECRET)
    monkeypatch.setenv("CUSTOM_PROVIDER_KEY", SECRET)
    monkeypatch.setenv("CUSTOM_EMBED_KEY", SECRET)
    monkeypatch.setenv("EMBEDDING_KEY", SECRET)
    return isolated


def assert_placeholders(expected):
    raw = config.CONFIG_PATH.read_text(encoding="utf-8")
    saved = yaml.safe_load(raw)
    assert saved["providers"]["deepseek"]["api_key"] == expected["providers"]["deepseek"]["api_key"]
    assert saved["embedding"]["api_key"] == expected["embedding"]["api_key"]
    assert SECRET not in raw
    assert secret_store.get_secret(PROVIDER) == secret_store.get_secret(EMBEDDING) == ""


@pytest.mark.parametrize("provider_env", ["DASHSCOPE_API_KEY", "CUSTOM_PROVIDER_KEY"])
@pytest.mark.parametrize("spaced", [False, True])
def test_source_placeholder_survives_unrelated_save_and_cache_restart(source, provider_env, spaced):
    expected = write_placeholders(f" {provider_env} " if spaced else provider_env,
                                  " EMBEDDING_KEY " if spaced else "EMBEDDING_KEY")
    loaded = config.load_settings()
    assert loaded.providers["deepseek"].api_key == loaded.embedding.api_key == SECRET
    cached = config.get_settings()
    loaded.generation.max_length += 1
    config.save_settings(loaded)
    assert_placeholders(expected)
    config.get_settings.cache_clear()
    reloaded = config.get_settings()
    assert reloaded is not loaded and reloaded is not cached
    assert reloaded.providers["deepseek"].api_key == reloaded.embedding.api_key == SECRET


@pytest.mark.parametrize("spaced", [False, True])
def test_writing_reference_http_save_keeps_provider_and_embedding_placeholders(source, client, spaced):
    expected = write_placeholders(" DASHSCOPE_API_KEY " if spaced else "DASHSCOPE_API_KEY",
                                  " EMBEDDING_KEY " if spaced else "EMBEDDING_KEY")
    fixture = Path(__file__).parent / "fixtures/writing_reference_pack_synthetic.json"
    response = client.post("/api/settings/writing-reference", files={"file": ("synthetic.json", fixture.read_bytes())})
    assert response.status_code == 200 and response.json() == {"configured": True}
    assert_placeholders(expected)
    loaded = config.reload_settings()
    assert loaded.writing_reference_resource.startswith("writing-reference-")
    assert loaded.providers["deepseek"].api_key == loaded.embedding.api_key == SECRET


@pytest.mark.parametrize("identity", [PROVIDER, EMBEDDING])
def test_explicit_new_key_replaces_only_its_placeholder_and_blank_keeps_it(source, identity):
    expected = write_placeholders()
    update = (lambda value: config.update_provider_config("deepseek", config.ProviderConfig(api_key=value))) if identity == PROVIDER else (
        lambda value: config.update_embedding_config(config.EmbeddingConfig(api_key=value)))
    update("")
    assert_placeholders(expected)
    new_key = SECRET + "_NEW"
    update(new_key)
    update("")
    data = yaml.safe_load(config.CONFIG_PATH.read_text(encoding="utf-8"))
    changed, unchanged = (data["providers"]["deepseek"], data["embedding"]) if identity == PROVIDER else (
        data["embedding"], data["providers"]["deepseek"])
    assert "api_key" not in changed
    assert unchanged["api_key"].startswith("${")
    assert secret_store.get_secret(identity) == new_key
    assert SECRET not in config.CONFIG_PATH.read_text(encoding="utf-8")
    loaded = config.reload_settings()
    assert (loaded.providers["deepseek"].api_key if identity == PROVIDER else loaded.embedding.api_key) == new_key


def test_save_uses_current_disk_placeholder_not_cached_or_guessed_name(source, monkeypatch):
    write_placeholders()
    loaded = config.get_settings()
    expected = write_placeholders("CUSTOM_PROVIDER_KEY", "CUSTOM_PROVIDER_KEY")
    monkeypatch.delenv("CUSTOM_PROVIDER_KEY")
    config.save_settings(loaded)
    assert_placeholders(expected)
    monkeypatch.setenv("CUSTOM_PROVIDER_KEY", SECRET)
    assert config.reload_settings().providers["deepseek"].api_key == SECRET
    assert config.get_settings().embedding.api_key == SECRET


class BeforeHTTP(Exception):
    pass


def startup_probe(monkeypatch, events, inspect):
    initialize = launcher.initialize_runtime_environment
    migrate = config.migrate_legacy_api_keys
    def initialized():
        initialize()
        events.append("runtime")
    def migration():
        assert events == ["runtime", "guard"]
        assert all(name not in launcher.os.environ for name in STRIPPED_ENV)
        events.append("stripped")
        migrate()
        events.append("migrated")
    def before_socket(*args, **kwargs):
        assert events == ["runtime", "guard", "stripped", "migrated"]
        inspect()
        events.append("before-http")
        raise BeforeHTTP()
    for name in STRIPPED_ENV:
        monkeypatch.setenv(name, SECRET)
    monkeypatch.setattr(launcher, "initialize_runtime_environment", initialized)
    # An audit hook is process-global and irreversible; guard has its own tests.
    monkeypatch.setattr(launcher, "install_packaged_write_guard", lambda: events.append("guard"))
    monkeypatch.setattr(config, "migrate_legacy_api_keys", migration)
    monkeypatch.setattr(config, "load_settings", lambda: pytest.fail("Startup must not load Settings"))
    monkeypatch.setitem(sys.modules, "app.main", SimpleNamespace(app=object()))
    monkeypatch.setattr(launcher.socket, "socket", before_socket)


@pytest.mark.parametrize("old_llm", [False, True])
def test_packaged_migration_precedes_http_without_settings_read_and_is_idempotent(isolated, monkeypatch, old_llm):
    write_legacy(old_llm=old_llm)
    events = []
    def migrated():
        assert SECRET not in config.CONFIG_PATH.read_text(encoding="utf-8")
        assert secret_store.get_secret(PROVIDER) == secret_store.get_secret(EMBEDDING) == SECRET
    startup_probe(monkeypatch, events, migrated)
    with pytest.raises(BeforeHTTP):
        launcher.run()
    first = {p: p.read_bytes() for p in config.CONFIG_PATH.parent.rglob("*") if p.is_file()}
    monkeypatch.setattr(secret_store, "set_secret", lambda *a: pytest.fail("Second startup must not write DPAPI"))
    events.clear()
    with pytest.raises(BeforeHTTP):
        launcher.run()
    assert all(p.read_bytes() == raw for p, raw in first.items())


@pytest.mark.parametrize("failure", ["first-write", "second-write", "verify", "config-replace"])
def test_packaged_startup_failure_never_reaches_http_and_preserves_config(isolated, monkeypatch, caplog, failure):
    original = write_legacy()
    events = []
    startup_probe(monkeypatch, events, lambda: pytest.fail("HTTP must never start"))
    def failed(*args, **kwargs):
        raise OSError(SECRET)
    if failure == "first-write":
        monkeypatch.setattr(secret_store, "set_secret", failed)
    elif failure == "second-write":
        real_set = secret_store.set_secret
        monkeypatch.setattr(secret_store, "set_secret", lambda identity, value: failed() if identity == EMBEDDING else real_set(identity, value))
    elif failure == "verify":
        monkeypatch.setattr(secret_store, "set_secret", lambda *a: None)
    else:
        monkeypatch.setattr(config, "atomic_write_text", failed)
    errors = []
    monkeypatch.setattr(launcher, "show_error", errors.append)
    assert launcher.main() == 1
    assert events == ["runtime", "guard", "stripped"]
    assert config.CONFIG_PATH.read_bytes() == original
    assert not (config.CONFIG_PATH.parent / "desktop-instance.json").exists()
    log = (runtime_paths.get_logs_dir() / "launcher.log").read_text(encoding="utf-8")
    assert "SecretStoreError" in log
    assert SECRET not in log + caplog.text + str(errors)


@pytest.mark.parametrize("case", ["missing", "empty", "placeholder", "spaced-placeholder", "already-secured"])
def test_migration_only_no_legacy_is_noop_without_dpapi_or_runtime_services(isolated, monkeypatch, case):
    if case == "empty":
        config.CONFIG_PATH.write_text("embedding:\n  api_key: ''\n", encoding="utf-8")
    elif case == "placeholder":
        write_placeholders()
    elif case == "spaced-placeholder":
        write_placeholders(" DASHSCOPE_API_KEY ", " EMBEDDING_KEY ")
    elif case == "already-secured":
        write_legacy()
        config.migrate_legacy_api_keys()
    before = config.CONFIG_PATH.read_bytes() if config.CONFIG_PATH.exists() else None
    def forbidden(*args, **kwargs):
        pytest.fail("Migration-only no-op must not read/write DPAPI, initialize services or rewrite config")
    for name in ("get_secret", "set_secret"):
        monkeypatch.setattr(secret_store, name, forbidden)
    for name in ("initialize_runtime_environment", "_load_dotenv_api_keys", "_writing_reference_path", "_write_config", "load_settings"):
        monkeypatch.setattr(config, name, forbidden)
    config.migrate_legacy_api_keys()
    assert (config.CONFIG_PATH.read_bytes() if config.CONFIG_PATH.exists() else None) == before


@pytest.mark.parametrize("spaced", [False, True])
def test_packaged_placeholder_survives_migration_load_and_unrelated_save(isolated, monkeypatch, spaced):
    expected = write_placeholders(" DASHSCOPE_API_KEY " if spaced else "DASHSCOPE_API_KEY",
                                  " EMBEDDING_KEY " if spaced else "EMBEDDING_KEY")
    monkeypatch.setenv("DASHSCOPE_API_KEY", SECRET)
    monkeypatch.setenv("EMBEDDING_KEY", SECRET)
    config.migrate_legacy_api_keys()
    loaded = config.load_settings()
    assert loaded.providers["deepseek"].api_key == loaded.embedding.api_key == ""
    config.save_settings(loaded)
    assert_placeholders(expected)


@pytest.mark.parametrize("value,placeholder", [
    ("${DASHSCOPE_API_KEY}", True), ("${ DASHSCOPE_API_KEY }", True),
    (" \t${\tDASHSCOPE_API_KEY\n} \n", True), ("${CUSTOM-KEY}", True),
    ("${ }", True), ("${}", False), ("prefix${DASHSCOPE_API_KEY}", False),
    ("${DASHSCOPE_API_KEY}suffix", False), (SECRET, False), ("", False),
    (None, False), (123, False),
])
def test_placeholder_classification_preserves_existing_resolver_semantics(monkeypatch, value, placeholder):
    monkeypatch.setenv("DASHSCOPE_API_KEY", SECRET)
    monkeypatch.setenv("CUSTOM-KEY", SECRET)
    expected = ("" if value == "${ }" else SECRET) if placeholder else value
    assert config._resolve_env_placeholder(value) == expected
    assert config._is_env_placeholder(value) is placeholder


@pytest.mark.parametrize("identity", [PROVIDER, EMBEDDING])
@pytest.mark.parametrize("spaced", [False, True])
def test_settings_http_new_key_replaces_placeholder_and_blank_preserves_credential(source, client, monkeypatch, identity, spaced):
    expected = write_placeholders(" DASHSCOPE_API_KEY " if spaced else "DASHSCOPE_API_KEY",
                                  " EMBEDDING_KEY " if spaced else "EMBEDDING_KEY")
    suffix = "" if identity == PROVIDER else "/embedding"
    payload = {"api_key": "", "model": "offline"}
    if identity == PROVIDER:
        payload["provider_key"] = "deepseek"
    assert client.post("/api/settings" + suffix, json=payload).status_code == 200
    assert_placeholders(expected)
    # Empty environment values after saving distinguish Secure Store from the old placeholder.
    payload["api_key"] = SECRET
    assert client.post("/api/settings" + suffix, json=payload).status_code == 200
    monkeypatch.delenv("DASHSCOPE_API_KEY" if identity == PROVIDER else "EMBEDDING_KEY")
    payload["api_key"] = ""
    assert client.post("/api/settings" + suffix, json=payload).status_code == 200
    loaded = config.reload_settings()
    assert (loaded.providers["deepseek"].api_key if identity == PROVIDER else loaded.embedding.api_key) == SECRET
    config.save_settings(loaded)
    raw = config.CONFIG_PATH.read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    changed, unchanged = (data["providers"]["deepseek"], data["embedding"]) if identity == PROVIDER else (
        data["embedding"], data["providers"]["deepseek"])
    assert "api_key" not in changed
    assert unchanged["api_key"] == (expected["embedding"]["api_key"] if identity == PROVIDER else expected["providers"]["deepseek"]["api_key"])
    assert SECRET not in raw
    assert secret_store.get_secret(identity) == SECRET
    assert secret_store.get_secret(EMBEDDING if identity == PROVIDER else PROVIDER) == ""


@pytest.mark.parametrize("identity", [PROVIDER, EMBEDDING])
def test_packaged_mixed_spaced_placeholder_and_legacy_plaintext(isolated, identity):
    expected = write_placeholders(" DASHSCOPE_API_KEY ", " EMBEDDING_KEY ")
    legacy = expected["providers"]["deepseek"] if identity == PROVIDER else expected["embedding"]
    legacy["api_key"] = SECRET
    config.CONFIG_PATH.write_text(yaml.safe_dump(expected), encoding="utf-8")
    config.migrate_legacy_api_keys()
    raw = config.CONFIG_PATH.read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    changed, unchanged = (data["providers"]["deepseek"], data["embedding"]) if identity == PROVIDER else (
        data["embedding"], data["providers"]["deepseek"])
    assert "api_key" not in changed
    assert unchanged["api_key"] == ("${ EMBEDDING_KEY }" if identity == PROVIDER else "${ DASHSCOPE_API_KEY }")
    assert SECRET not in raw
    assert secret_store.get_secret(identity) == SECRET
    assert secret_store.get_secret(EMBEDDING if identity == PROVIDER else PROVIDER) == ""
