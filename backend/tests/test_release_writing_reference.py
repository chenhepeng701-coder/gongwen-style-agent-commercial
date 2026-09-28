import hashlib
from pathlib import Path

import pytest

from app import config
from app.services.generation.writing_reference_pack import load_writing_reference_pack
from app.utils.runtime_paths import get_resources_dir
from test_release_model_configuration import client

FIXTURE = Path(__file__).parent / "fixtures/writing_reference_pack_synthetic.json"


def test_upload_validates_copies_exact_bytes_and_reloads_same_owner(client):
    raw = FIXTURE.read_bytes()
    assert client.get("/api/settings/writing-reference").json() == {"configured": False}
    result = client.post("/api/settings/writing-reference", files={"file": ("../../private.json", raw, "application/json")})
    assert result.json() == {"configured": True}
    config.reload_settings()
    path = Path(config.get_settings().writing_reference_pack_path)
    assert path.parent == get_resources_dir()
    assert path.read_bytes() == raw
    assert load_writing_reference_pack().sha256 == hashlib.sha256(raw).hexdigest()
    assert client.get("/api/settings/writing-reference").json() == {"configured": True}
    assert str(path) not in result.text
    assert "writing_reference_pack_path" not in config.get_settings().model_dump()
    config.update_embedding_config(config.EmbeddingConfig(api_key="SYNTHETIC"))
    assert Path(config.reload_settings().writing_reference_pack_path) == path


@pytest.mark.parametrize("raw", [b"{}", b"{invalid", b"\xff", b'{"S02":{},"S02":{}}', b"x" * (5 * 1024 * 1024 + 1)], ids=["schema", "json", "utf8", "duplicate", "oversize"])
def test_invalid_upload_does_not_replace_last_valid_configuration(client, raw):
    assert client.post("/api/settings/writing-reference", files={"file": ("ok.json", FIXTURE.read_bytes())}).status_code == 200
    before = config.get_settings().writing_reference_pack_path
    response = client.post("/api/settings/writing-reference", files={"file": ("private-name.json", raw)})
    assert response.status_code == 422
    assert "private-name" not in response.text
    assert config.reload_settings().writing_reference_pack_path == before
    assert len(list(get_resources_dir().iterdir())) == 1


def test_selected_resource_tampering_is_fail_closed(client):
    client.post("/api/settings/writing-reference", files={"file": ("ok.json", FIXTURE.read_bytes())})
    Path(config.get_settings().writing_reference_pack_path).write_bytes(b"{}")
    assert client.get("/api/settings/writing-reference").json() == {"configured": False}
    with pytest.raises(Exception, match="WRITING_REFERENCE_PACK_SCHEMA_INVALID"):
        load_writing_reference_pack()


def test_configuration_cannot_escape_resources(client):
    config.CONFIG_PATH.write_text('writing_reference_resource: ../../private.json', encoding="utf-8")
    with pytest.raises(ValueError, match="配置无效"):
        config.reload_settings()
