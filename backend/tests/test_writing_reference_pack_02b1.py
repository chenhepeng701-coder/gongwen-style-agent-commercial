"""Resource-boundary tests: invented inputs, zero real provider/network calls."""
import asyncio
import hashlib
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import config
from app.models.full_article import FullArticleContractError, canonical_json
from app.models.generation import ArticleGenRequest
from app.services.generation import writing_reference_pack as resource
from app.services.generation.full_draft_material import FullDraftMaterialPacket, case32_writing_references
from app.services.generation.official_document_drafting import build_full_draft_prompt


FIXTURE = Path(__file__).with_name("fixtures") / "writing_reference_pack_synthetic.json"


@pytest.fixture
def configured(monkeypatch, tmp_path):
    path = tmp_path / "private-pack.json"
    path.write_bytes(FIXTURE.read_bytes())
    settings = config.Settings(writing_reference_pack_path=str(path))
    monkeypatch.setattr(resource, "get_settings", lambda: settings)
    return path, settings


def test_valid_pack_keeps_writer_contract_and_stable_byte_identity(configured):
    path, _ = configured
    expected = json.loads(FIXTURE.read_bytes())
    first = resource.load_writing_reference_pack()
    assert first.writer_payload() == expected == case32_writing_references()
    assert first.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert resource.load_writing_reference_pack() == first
    returned = first.writer_payload()
    returned["S02"]["excerpts"][0]["text"] = "caller mutation"
    assert first.writer_payload() == expected
    path.write_bytes(path.read_bytes() + b"\n")
    second = resource.load_writing_reference_pack()
    assert second.writer_payload() == expected and second.sha256 != first.sha256
    assert expected["S05"]["text"] not in repr(first)


def test_reference_accessor_does_not_need_repository_assets(configured, monkeypatch, tmp_path):
    from app.services.generation import full_draft_material
    monkeypatch.setattr(full_draft_material, "_ASSETS", tmp_path / "no-bundled-assets")
    assert case32_writing_references() == json.loads(FIXTURE.read_bytes())


@pytest.mark.parametrize("failure,code", [
    ("missing", "REQUIRED"), ("absent", "NOT_FOUND"), ("directory", "UNREADABLE"),
    ("relative", "PATH_INVALID"), ("url", "PATH_INVALID"), ("unc", "PATH_INVALID"),
    ("json", "SCHEMA_INVALID"), ("utf8", "SCHEMA_INVALID"),
    ("duplicate_key", "SCHEMA_INVALID"),
])
def test_resource_failures_are_explicit_and_content_free(configured, failure, code):
    path, settings = configured
    if failure == "missing": settings.writing_reference_pack_path = "  "
    if failure == "absent": settings.writing_reference_pack_path = str(path.with_name("missing.json"))
    if failure == "directory": settings.writing_reference_pack_path = str(path.parent)
    if failure == "relative": settings.writing_reference_pack_path = "private/pack.json"
    if failure == "url": settings.writing_reference_pack_path = "https://invalid.example/pack.json"
    if failure == "unc": settings.writing_reference_pack_path = "//server/share/pack.json"
    if failure == "json": path.write_bytes(b'{"private-marker":')
    if failure == "utf8": path.write_bytes(b"\xff")
    if failure == "duplicate_key": path.write_bytes(b'{"S02": {}, "S02": {}}')
    with pytest.raises(FullArticleContractError) as error:
        resource.load_writing_reference_pack()
    assert str(error.value) == error.value.code == "WRITING_REFERENCE_PACK_" + code


@pytest.mark.parametrize("mutation", [
    "root", "missing_slot", "extra_slot", "role", "empty_text", "empty_excerpts",
    "number_as_text", "extra_field", "reverse_span", "bool_line", "duplicate_id",
    "empty_outline", "empty_limits", "null", "nonfinite",
])
def test_strict_schema_and_reference_identity(configured, mutation):
    path, _ = configured
    value = json.loads(FIXTURE.read_bytes())
    if mutation == "root": value = []
    elif mutation == "missing_slot": del value["S05"]
    elif mutation == "extra_slot": value["S99"] = {}
    elif mutation == "role": value["S02"]["role"] = "FACTS"
    elif mutation == "empty_text": value["S05"]["text"] = " \n\t"
    elif mutation == "empty_excerpts": value["S02"]["excerpts"] = []
    elif mutation == "number_as_text": value["S05"]["text"] = 42
    elif mutation == "extra_field": value["S05"]["evidence_id"] = "NOT-EVIDENCE"
    elif mutation == "reverse_span": value["S02"]["excerpts"][0]["line_end"] = 1
    elif mutation == "bool_line": value["S02"]["outline"][0]["line"] = True
    elif mutation == "duplicate_id": value["S02"]["excerpts"] *= 2
    elif mutation == "empty_outline": value["S02"]["outline"] = []
    elif mutation == "empty_limits": value["S02"]["limitations"] = []
    elif mutation == "null": value["S05"] = None
    elif mutation == "nonfinite": value["S02"]["outline"][0]["line"] = float("nan")
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(FullArticleContractError, match="^WRITING_REFERENCE_PACK_SCHEMA_INVALID$"):
        resource.load_writing_reference_pack()


def test_snapshot_survives_file_change_and_context_resets(configured):
    path, settings = configured
    first = resource.load_writing_reference_pack()
    other = first.writer_payload()
    other["S05"]["text"] = "SYNTHETIC_ONLY alternate text"
    path.write_text(json.dumps(other), encoding="utf-8")
    second = resource.load_writing_reference_pack()
    settings.writing_reference_pack_path = ""
    with pytest.raises(RuntimeError):
        with resource.use_writing_reference_pack(first):
            assert case32_writing_references() == first.writer_payload()
            with resource.use_writing_reference_pack(second):
                assert case32_writing_references() == other
            assert case32_writing_references() == first.writer_payload()
            raise RuntimeError("synthetic assembly failure")
    with pytest.raises(FullArticleContractError, match="PACK_REQUIRED"):
        case32_writing_references()


def test_concurrent_requests_do_not_share_reference_snapshots(configured):
    path, _ = configured
    first = resource.load_writing_reference_pack()
    value = first.writer_payload()
    value["S05"]["text"] = "SYNTHETIC_ONLY concurrent second"
    path.write_text(json.dumps(value), encoding="utf-8")
    second = resource.load_writing_reference_pack()

    async def read(pack):
        with resource.use_writing_reference_pack(pack):
            await asyncio.sleep(0)
            return case32_writing_references()

    async def both():
        return await asyncio.gather(read(first), read(second))

    assert asyncio.run(both()) == [first.writer_payload(), second.writer_payload()]


@pytest.mark.parametrize("yaml_exists", [False, True])
def test_existing_config_owner_reads_private_dotenv_without_serializing_path(monkeypatch, tmp_path, yaml_exists):
    backend = tmp_path / "backend"
    backend.mkdir()
    yaml = backend / "config.yaml"
    if yaml_exists: yaml.write_text("{}", encoding="utf-8")
    private_path = str(tmp_path / "synthetic-private.json")
    (backend / ".env").write_text('WRITING_REFERENCE_PACK_PATH="' + private_path + '"\n', encoding="utf-8")
    monkeypatch.setattr(config, "BACKEND_DIR", backend)
    monkeypatch.setattr(config, "CONFIG_PATH", yaml)
    monkeypatch.setattr(config, "initialize_runtime_environment", lambda: None)
    monkeypatch.delenv("WRITING_REFERENCE_PACK_PATH", raising=False)
    settings = config.load_settings()
    assert settings.writing_reference_pack_path == private_path
    assert "writing_reference_pack_path" not in settings.model_dump()
    monkeypatch.setenv("WRITING_REFERENCE_PACK_PATH", str(tmp_path / "override.json"))
    assert config.load_settings().writing_reference_pack_path.endswith("override.json")


@pytest.fixture
def synthetic_packet():
    from app.services.generation.atomic_evidence import build_full_draft_anchor_input
    from app.services.generation.generator import build_official_draft_task
    request = ArticleGenRequest(title="合成测试工作总结", document_type="工作总结", target_words=1200)
    task = build_official_draft_task(request)
    retrieval = {"retrieval_status": "SUFFICIENT", "primary_document_id": "synthetic-document",
        "auxiliary_document_ids": [], "selected_chunks": [{"document_id": "synthetic-document",
            "chunk_id": "synthetic-chunk", "metadata": {},
            "text": ("测试街道组织安全检查。测试街道正在推进隐患整改。测试街道计划开展安全培训。"
                     "资料背景仅供合成测试定位核验，以下文字用于保持测试来源片段与原子记录的区别。"
                     "这些句子由测试编写者虚构，不用于证明文章质量，也不描述任何真实机关的工作结果。")}]}
    anchors = build_full_draft_anchor_input(task, retrieval)
    packet = FullDraftMaterialPacket.from_authorized_input({"title": request.title, "target_words": 1200,
        "hard_max": 1500, "level": "合成", "org_type": "测试"}, anchors)
    ids = list(anchors["registry"])
    return packet.with_brief({"version": "material-brief-stage2-1", "input_sha256": packet.brief_input_sha256,
        "overview": "合成测试摘要", "matters": [{"matter_id": "synthetic-matter", "title": "合成测试事项",
            "summary": {"evidence_ids": ids, "text": "合成测试摘要"}, "evidence_ids": ids, "limits": []}],
        "uncertainties": []})


def test_unmodified_writer_consumes_same_reference_json(configured, synthetic_packet):
    expected = canonical_json(json.loads(FIXTURE.read_bytes()))
    prompt = build_full_draft_prompt(synthetic_packet)
    assert "## S02主写法参考 / S05问题段参考：不是事实来源\n\n```json\n" + expected + "\n```" in prompt["user"]
    assert "REFERENCE_PACK_SHA256" not in prompt["user"]
    assert set(prompt) == {"system", "user", "ruleset_id", "ruleset_hash", "host_boundary_hash", "system_hash", "user_hash"}


@pytest.fixture
def api(monkeypatch, tmp_path):
    from app.routers import generation
    from app.services.generation import full_draft_generation as route
    monkeypatch.setattr(generation, "get_output_dir", lambda: tmp_path)
    monkeypatch.setattr(generation, "get_logs_dir", lambda: tmp_path / "logs")
    monkeypatch.setattr(route, "get_output_dir", lambda: tmp_path)
    app = FastAPI()
    app.include_router(generation.router, prefix="/api/generation")
    with TestClient(app) as client:
        yield client, route


@pytest.mark.parametrize("failure,code", [("missing", "REQUIRED"), ("absent", "NOT_FOUND"), ("schema", "SCHEMA_INVALID")])
def test_public_api_fails_before_retrieval_brief_or_writer(configured, api, monkeypatch, failure, code):
    path, settings = configured
    client, route = api
    if failure == "missing": settings.writing_reference_pack_path = ""
    if failure == "absent": settings.writing_reference_pack_path = str(path.with_name("missing.json"))
    if failure == "schema": path.write_text("{}", encoding="utf-8")
    calls = []

    def forbidden(*args, **kwargs):
        calls.append("unexpected")
        raise AssertionError("Reference validation must precede all generation work")

    monkeypatch.setattr(route, "prepare_full_draft_material", forbidden)
    monkeypatch.setattr(route, "get_llm_provider", forbidden)
    response = client.post("/api/generation/generate", json={"title": "合成测试", "document_type": "工作总结",
        "generation_route": "full_article_drafting"})
    assert response.status_code == 400
    assert response.json()["detail"]["contract_error_code"] == "WRITING_REFERENCE_PACK_" + code
    assert response.json()["detail"]["fallback_used"] is False
    assert not calls
    assert "SYNTHETIC_ONLY" not in response.text


def test_production_trace_binds_the_snapshot_actually_sent_to_writer(configured, synthetic_packet, api, monkeypatch, tmp_path, caplog):
    from app.services.llm.openai_compatible_provider import OpenAICompatibleProvider
    path, _ = configured
    client, route = api
    expected = resource.load_writing_reference_pack()

    def prepare(request):
        # File changes after preflight must not invalidate the audit/content binding.
        path.write_text("{}", encoding="utf-8")
        return synthetic_packet

    monkeypatch.setattr(route, "prepare_full_draft_material", prepare)
    provider = OpenAICompatibleProvider(config.ProviderConfig(model="qwen-synthetic", api_key="synthetic-only",
        base_url="https://offline.invalid"))
    monkeypatch.setattr(route, "get_llm_provider", lambda: provider)
    sent = []

    def local_response(prompt, **kwargs):
        sent.append(prompt)
        kwargs["on_http_request_attempt"](1)
        return "# 合成测试工作总结\n\n本段是完全合成的离线测试输出，不代表真实文章质量。\n"

    monkeypatch.setattr(provider, "generate_text", local_response)
    response = client.post("/api/generation/generate", json={"title": "合成测试", "document_type": "工作总结",
        "generation_route": "full_article_drafting"})
    assert response.status_code == 200
    assert len(sent) == 1 and canonical_json(expected.writer_payload()) in sent[0]
    result = response.json()["articles"][0]
    assert result["generation_provenance"]["REFERENCE_PACK_SHA256"] == expected.sha256
    root = next((tmp_path / "full-draft").iterdir())
    for name in ("prompt_manifest.json", "call_accounting.json"):
        audit = (root / name).read_text("utf-8")
        assert json.loads(audit)["REFERENCE_PACK_SHA256"] == expected.sha256
        assert "SYNTHETIC_ONLY" not in audit
    assert "SYNTHETIC_ONLY" not in caplog.text
    assert resource._active_pack.get() is None


def test_production_has_no_bundled_reference_filename_or_content():
    app_root = Path(__file__).resolve().parents[1] / "app"
    for path in app_root.rglob("*.py"):
        assert "reference_outbound.json" not in path.read_text("utf-8").lower(), path


def test_fixture_is_entirely_synthetic():
    value = json.loads(FIXTURE.read_bytes())
    assert FIXTURE.read_bytes().isascii()
    prose = [item["text"] for item in value["S02"]["outline"]] + value["S02"]["limitations"]
    for item in value["S02"]["excerpts"]:
        prose.extend(item[key] for key in ("text", "teaches", "observation", "transfer_limit"))
    prose.extend(value["S05"][key] for key in ("text", "observation", "transfer_limit"))
    assert all(text.startswith("SYNTHETIC_ONLY") for text in prose)
