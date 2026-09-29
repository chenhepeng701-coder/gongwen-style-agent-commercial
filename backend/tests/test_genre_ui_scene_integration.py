"""Registry/UI task integration; only synthetic inputs and guarded offline I/O.

The exact merged baseline is executed for empty-scene retrieval/Prompt parity.
These tests establish wiring and safety boundaries, not prose quality acceptance.
"""
import ast
from dataclasses import asdict, replace
import json
from pathlib import Path
import subprocess
import sys
from types import ModuleType, SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.models.generation import ArticleGenRequest, ArticleGenBatchResult, ArticleGenResult
from app.routers import generation, retrieval as retrieval_router
from app.services.generation import material_digest, task_assembly, writing_reference_pack
from app.services.generation.generator import build_official_draft_task
from app.services.generation.genres import registry
from app.services.generation.genres.catalog import genre_catalog
from app.services.generation.genres.commercial_contract import CommercialMaturity
from app.services.generation.official_document_drafting import build_full_draft_prompt
from app.services.retrieval import document_retriever
from app.services.retrieval.scene_classifier import get_scene_taxonomy
from test_commercial_genre_parity import summary_packet
import test_commercial_genre_parity as commercial_parity
from test_material_digest_task_assembly import reference_pack
from test_multi_genre_retrieval import candidates, run as retrieve_fixture
from test_source_substrate import sources


BASE_SHA = "d9b6e68d698e9d7b97dc92f23f8feffd80360c5a"
ROOT = Path(__file__).resolve().parents[2]
RETRIEVAL_PATH = "backend/app/services/retrieval/document_retriever.py"


def request(**overrides):
    return ArticleGenRequest(**{
        "title": "合成档案整理工作总结", "document_type": "工作总结",
        "generation_route": "full_article_drafting", "target_words": 1200,
        "level": "街道", "org_type": "党政机关", "business_domain": "党建",
        "writing_purpose": "总结汇报", "reference_scope": "自动匹配资料库",
        **overrides,
    })


@pytest.fixture
def committed(monkeypatch):
    def load(path):
        name = "genre_ui_scene_base_" + Path(path).stem
        module = ModuleType(name)
        module.__file__ = str(ROOT / path)
        monkeypatch.setitem(sys.modules, name, module)
        source = subprocess.check_output(["git", "show", f"{BASE_SHA}:{path}"], cwd=ROOT)
        exec(compile(source, BASE_SHA + "/" + path, "exec"), module.__dict__)
        return module
    return load


@pytest.fixture
def client(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Catalog and task integration must not call scene classifiers or real generation")
    monkeypatch.setattr(retrieval_router, "classify_by_title_or_query", forbidden)
    monkeypatch.setattr(generation._generator, "generate_commercial_flow", forbidden)
    app = FastAPI()
    app.include_router(generation.router, prefix="/api/generation")
    app.include_router(retrieval_router.router, prefix="/api/retrieval")
    with TestClient(app) as value:
        yield value


def test_catalog_api_is_exact_registry_read_only_projection(client):
    response = client.get("/api/generation/genres")
    assert response.status_code == 200
    payload = response.json()
    adapters = registry.GENRE_REGISTRY.all()
    assert isinstance(adapters, tuple)
    assert len(adapters) == len(payload["genres"]) == 22
    assert [row["genre_id"] for row in payload["genres"]] == [item.genre_id for item in adapters]
    assert len({item.genre_id for item in adapters}) == 22
    allowed = {"genre_id", "document_type", "display_name", "aliases", "capability_status",
               "production_ready", "commercial_maturity", "export_formats", "generation_route"}
    for row, adapter in zip(payload["genres"], adapters):
        assert set(row) == allowed
        assert row["document_type"] == row["display_name"] == adapter.document_type
        assert row["aliases"] == list(adapter.aliases)
        assert row["export_formats"] == list(adapter.capabilities.export_formats)
        assert row["production_ready"] is True
        assert row["generation_route"] == "full_article_drafting"
        assert row["commercial_maturity"] == "PRODUCTION_ENABLED"
    assert client.post("/api/generation/genres", json={"production_ready": True}).status_code == 405
    payload["genres"].clear()
    assert len(genre_catalog()["genres"]) == 22


def test_future_production_catalog_entry_needs_no_name_branch_and_gate_is_rechecked(monkeypatch):
    local = registry.GenreRegistry()
    for adapter in registry.GENRE_REGISTRY.all():
        local.register(adapter)
    sample = registry.resolve_genre("work_summary")
    future = replace(sample, genre_id="synthetic_future", document_type="合成未来文种", aliases=(),
                     commercial=replace(sample.commercial, genre_id="synthetic_future"))
    malformed = replace(sample, genre_id="synthetic_bad", document_type="合成缺陷文种", aliases=(),
                        commercial=replace(sample.commercial, genre_id="synthetic_bad",
                            writer_calls=replace(sample.commercial.writer_calls, max_calls=2)))
    premature = replace(sample, genre_id="synthetic_premature", document_type="合成未验收文种", aliases=(),
                        commercial=replace(sample.commercial, genre_id="synthetic_premature",
                            maturity=CommercialMaturity.ENGINEERING_READY))
    for adapter in (future, malformed, premature):
        local.register(adapter)
    monkeypatch.setattr(registry, "GENRE_REGISTRY", local)
    rows = {row["genre_id"]: row for row in genre_catalog()["genres"]}
    assert rows[future.genre_id]["generation_route"] == "full_article_drafting"
    assert rows[future.genre_id]["production_ready"] is True
    for adapter in (malformed, premature):
        assert rows[adapter.genre_id]["production_ready"] is False
        assert rows[adapter.genre_id]["generation_route"] is None
    assert request(document_type=future.document_type).document_type == future.document_type


def test_catalog_of_isolated_registry_has_no_legacy_targets_outside_registry(monkeypatch):
    sample = registry.resolve_genre("work_summary")
    future = replace(sample, genre_id="synthetic_only", document_type="合成独立文种", aliases=(),
                     commercial=replace(sample.commercial, genre_id="synthetic_only"))
    local = registry.GenreRegistry()
    local.register(future)
    monkeypatch.setattr(registry, "GENRE_REGISTRY", local)
    catalog = genre_catalog()
    assert [row["document_type"] for row in catalog["genres"]] == [future.document_type]
    assert catalog["genres"][0]["production_ready"] is True
    assert catalog["legacy_mappings"] == []


def test_legacy_catalog_mappings_use_canonical_genres_and_existing_scene_taxonomy(client):
    catalog = client.get("/api/generation/genres").json()
    taxonomy = client.get("/api/retrieval/scene/taxonomy").json()["items"]
    assert taxonomy == get_scene_taxonomy()
    scene_names = {row["name"] for row in taxonomy}
    expected = {
        "领导讲话稿": ("讲话稿", None), "工作汇报": ("汇报材料", None), "实施方案": ("工作方案", None),
        "党课讲稿": ("讲话稿", "党课讲稿"),
        "民主生活会对照检查材料": ("讲话稿", "民主生活会对照检查"),
        "组织生活会个人发言材料": ("讲话稿", "组织生活会"),
    }
    assert {row["legacy_value"]: (row["document_type"], row["scene"])
            for row in catalog["legacy_mappings"]} == expected
    for row in catalog["legacy_mappings"]:
        assert registry.resolve_genre(row["document_type"], purpose="production").document_type == row["document_type"]
        assert row["scene"] is None or row["scene"] in scene_names


@pytest.mark.parametrize("document_type", [
    "合成未生产文种", "述职报告", "调研报告", "预案", "心得体会",
    "心得体会/研讨发言", "研讨发言", "其他", "完全未注册文种",
])
def test_http_production_gate_rejects_nonproduction_unknown_and_forged_flags(client, document_type, monkeypatch):
    sample = registry.resolve_genre("work_summary")
    synthetic = replace(sample, genre_id="synthetic_engineering", document_type="合成未生产文种",
        aliases=(), commercial=replace(sample.commercial, genre_id="synthetic_engineering",
            maturity=CommercialMaturity.ENGINEERING_READY))
    local = registry.GenreRegistry()
    for adapter in (*registry.GENRE_REGISTRY.all(), synthetic):
        local.register(adapter)
    monkeypatch.setattr(registry, "GENRE_REGISTRY", local)
    payload = request().model_dump()
    payload.update(document_type=document_type, production_ready=True, commercial_maturity="PRODUCTION_ENABLED",
                   capabilities={"production_ready": True}, genre_purpose="offline",
                   maturity="COMMERCIALLY_VERIFIED", commercial={"maturity": "PRODUCTION_ENABLED"})
    response = client.post("/api/generation/generate", json=payload)
    assert response.status_code == 422
    assert ("COMMERCIAL_MATURITY_NOT_ADMITTED" if document_type == synthetic.document_type
            else "GENRE_NOT_REGISTERED") in response.text


def test_http_formal_count_above_one_is_rejected_before_writer(client):
    payload = request().model_dump()
    payload["count"] = 2
    assert client.post("/api/generation/generate", json=payload).status_code == 422


@pytest.mark.parametrize("scene", [None, ""])
@pytest.mark.parametrize("count", [0, 1, 8, 30])
def test_empty_scene_complete_retrieval_and_queries_match_exact_base(committed, scene, count):
    task = build_official_draft_task(request(scene=scene))
    baseline = committed(RETRIEVAL_PATH)
    assert document_retriever.prepare_retrieval_request(task) == baseline.prepare_retrieval_request(task)
    assert retrieve_fixture(document_retriever, candidates(count), task) == retrieve_fixture(
        baseline, candidates(count), task)


@pytest.mark.parametrize("scene", ["专题汇报", "民主生活会对照检查", "党课讲稿"])
def test_manual_scene_changes_auxiliary_query_not_p1_score_or_evidence_admission(scene):
    plain_task = build_official_draft_task(request())
    task = build_official_draft_task(request(scene=scene, style="补充要求不进入检索"))
    prepared = document_retriever.prepare_retrieval_request(task)
    assert prepared["query_fields"]["scene"] == scene
    assert scene in prepared["query_text"].split("辅助限定：")[1]
    assert "scene" in prepared["semantic_fields"]
    assert prepared["excluded_semantic_fields"] == ["target_words", "reference_scope", "style"]
    assert "补充要求" not in prepared["query_text"]
    before, old_calls = retrieve_fixture(document_retriever, candidates(20), plain_task)
    after, new_calls = retrieve_fixture(document_retriever, candidates(20), task)
    assert old_calls[0][0] != new_calls[0][0]
    for key in ("ranked_documents", "primary_document_id", "auxiliary_document_ids", "selected_chunks"):
        assert after[key] == before[key]


def test_retrieval_parameter_responsibilities_and_auto_sentinel():
    req = request(scene="专题汇报", style="虚构完成999项成果的文字只能是要求数据")
    task = build_official_draft_task(req)
    prepared = document_retriever.prepare_retrieval_request(task)
    assert prepared["query_text"].splitlines() == [
        "主题：合成档案整理工作总结", "检索限定：工作总结；党建", "辅助限定：党政机关；街道；总结汇报；专题汇报"]
    assert prepared["semantic_fields"] == [
        "title", "document_type", "business_domain", "org_type", "level", "writing_purpose", "scene"]
    assert "style" not in prepared["query_fields"]
    assert prepared["query_fields"]["target_words"] == 1200
    assert prepared["query_fields"]["reference_scope"] == "自动匹配资料库"
    automatic = document_retriever.prepare_retrieval_request(build_official_draft_task(request()))
    for scene in (None, "", "auto", "自动", "未指定", "自动/未指定", "自动 / 未指定", "自动识别场景"):
        value = SimpleNamespace(**asdict(task), scene=scene)
        assert document_retriever.prepare_retrieval_request(value) == automatic
    # Existing scene-only legacy mode used this value as the document type.
    legacy = build_official_draft_task(ArticleGenRequest(title="合成主题", scene="工作总结"))
    assert legacy.document_type_from_scene_compat is True
    assert "scene" not in document_retriever.prepare_retrieval_request(legacy)["query_fields"]


def test_p1_and_all_retrieval_functions_outside_query_seam_are_exact_base_ast():
    source = subprocess.check_output(["git", "show", f"{BASE_SHA}:{RETRIEVAL_PATH}"], cwd=ROOT)
    trees = [ast.parse(value) for value in (source, (ROOT / RETRIEVAL_PATH).read_bytes())]
    for tree in trees:
        tree.body = [node for node in tree.body if not (
            isinstance(node, ast.FunctionDef) and node.name in {"_field_payload", "prepare_retrieval_request"})]
    assert ast.dump(trees[0]) == ast.dump(trees[1])


@pytest.mark.parametrize("scene", [None, "", "民主生活会对照检查"])
def test_scene_reaches_unchanged_trusted_task_prompt_without_creating_evidence(
        committed, tmp_path, reference_pack, scene):
    retrieval = sources()
    digest = material_digest.load_material_digest(retrieval, tmp_path / "synthetic-digest").digest
    req = request(scene=scene, style="保持简洁")
    assembled = task_assembly.assemble_task(req, retrieval, digest)
    baseline = committed("backend/app/services/generation/task_assembly.py").assemble_task(req, retrieval, digest)
    assert asdict(assembled.packet) == asdict(baseline.packet)
    fields = json.loads(assembled.packet.task_json)
    assert fields["scene"] == scene
    for name in ("document_type", "business_domain", "level", "org_type", "writing_purpose", "reference_scope", "style", "target_words"):
        assert fields[name] == getattr(req, name)
    plain = task_assembly.assemble_task(request(style="保持简洁"), retrieval, digest).packet
    assert assembled.packet.registry_json == plain.registry_json
    assert assembled.packet.evidence_writing_view() == plain.evidence_writing_view()
    assert assembled.packet.restrictions_json == plain.restrictions_json
    assert assembled.packet.unavailable_json == plain.unavailable_json
    with writing_reference_pack.use_writing_reference_pack(reference_pack):
        prompt = build_full_draft_prompt(assembled.packet)
        old = committed("backend/app/services/generation/official_document_drafting.py").build_full_draft_prompt(baseline.packet)
        assert prompt == old
        assert assembled.packet.task_json in prompt["user"]
        assert prompt["system"] == build_full_draft_prompt(plain)["system"]


@pytest.mark.parametrize("scene", [None, "年度工作总结", "专题汇报"])
def test_success_response_and_history_preserve_explicit_scene(client, monkeypatch, scene):
    observed, logs = [], []
    body = "# 合成任务\n\n仅用于离线保存合同检查。\n"

    async def generate(req):
        observed.append(req.model_dump())
        return ArticleGenBatchResult(articles=[ArticleGenResult(
            title="合成任务", content=body, scene="工作总结", generation_route="full_article_drafting",
            delivery_status="SUCCESS_WITH_REVIEW", official_success=False,
            user_confirmation_required=True,
        )])

    monkeypatch.setattr(generation._generator, "generate_commercial_flow", generate)
    monkeypatch.setattr(generation, "_append_log", lambda entry: logs.append(entry.model_dump()))
    response = client.post("/api/generation/generate", json=request(scene=scene).model_dump())
    assert response.status_code == 200
    expected_scene = scene or ""  # New records must not inherit a legacy genre-as-scene.
    article = response.json()["articles"][0]
    assert article["scene"] == logs[0]["scene"] == logs[0]["articles"][0]["scene"] == expected_scene
    assert observed[0]["scene"] == scene
    assert article["content"] == body
    assert article["delivery_status"] == "SUCCESS_WITH_REVIEW"
    assert article["official_success"] is False
    assert logs[0]["document_type"] == "工作总结"


@pytest.mark.parametrize("unsafe", [False, True])
def test_work_summary_full_formal_writer_fact_quality_delivery_and_flow4_match_exact_base(
        committed, summary_packet, reference_pack, monkeypatch, tmp_path, unsafe):
    def baseline(name, path=None):
        return committed(path or "backend/app/services/generation/" + name + ".py")

    # Reuse the independent complete route comparison: same synthetic Writer,
    # one call/no retries, exact prompt/body/facts/quality and TXT/DOCX delivery.
    # Only the baseline loader changes to THIS task's precise merge SHA.
    commercial_parity.test_full_route_writer_body_facts_quality_and_delivery_match_base(
        baseline, summary_packet, reference_pack, monkeypatch, tmp_path, "qwen-synthetic", unsafe)
