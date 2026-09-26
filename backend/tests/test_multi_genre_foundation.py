"""MULTI-GENRE-FOUNDATION: synthetic route-to-delivery architecture proof.

No Writer or Embedding is invoked. The authored body below is a fixture, never
evidence of production writing quality. All fact/quality/delivery owners are real.
"""
import asyncio
from dataclasses import fields, replace
import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.models.generation import ArticleGenRequest
from app.models.full_article import FullArticleContractError
from app.services.generation.genres import registry
from app.services.generation.genres.contracts import GenreAdapter
from app.services.generation.genres.report import REPORT_ADAPTER
from app.services.generation.genres.work_summary import WORK_SUMMARY_ADAPTER
from app.services.generation import full_draft_generation
from app.services.generation.generator import build_official_draft_task
from app.services.retrieval.document_retriever import retrieve_documents
from app.services.generation.stable_material_assets import load_stable_material_asset
from app.services.generation.task_assembly import assemble_task_from_assets
from app.services.generation.official_document_drafting import build_full_draft_prompt
from app.services.generation.full_article_candidate_adapter import import_full_article, propose_exact_citations
from app.services.generation.flow3_v3_trace import evaluate_full_article_facts
from app.services.generation.full_article_quality import evaluate_full_article_quality
from app.services.flow4.handoff import build_full_article_handoff
from app.services.flow4.renderer import render_full_article
from app.services.generation.delivery_decision import full_article_review_result
from test_writing_reference_pack_02b1 import api


ROOT = Path(__file__).resolve().parents[2]
REQUEST = dict(title="合成档案整理情况", document_type="汇报材料",
               generation_route="full_article_drafting", target_words=1200)


@pytest.fixture(autouse=True)
def no_services(monkeypatch):
    from app.services.llm.openai_compatible_provider import OpenAICompatibleProvider
    from app.services.library import embedder

    def forbidden(*args, **kwargs):
        pytest.fail("Architecture proof must not invoke Writer, Provider or Embedding")

    monkeypatch.setattr(OpenAICompatibleProvider, "generate_text", forbidden)
    monkeypatch.setattr(full_draft_generation, "get_llm_provider", forbidden)
    monkeypatch.setattr(embedder, "_call_embedding_api", forbidden)


@pytest.mark.parametrize("name", ["工作总结", "work_summary"])
def test_work_summary_route_resolves_to_verified_adapter(name):
    request = ArticleGenRequest(**{**REQUEST, "document_type": name})
    assert request.document_type == "工作总结"
    assert registry.resolve_genre(request.document_type, purpose="production") is WORK_SUMMARY_ADAPTER


@pytest.mark.parametrize("name,code", [
    (None, "GENRE_NOT_REGISTERED"), ("不存在的文种", "GENRE_NOT_REGISTERED"),
    ("汇报材料", "GENRE_OFFLINE_ONLY"), ("report", "GENRE_OFFLINE_ONLY"),
])
def test_public_request_fails_closed(name, code):
    with pytest.raises(ValidationError, match=code):
        ArticleGenRequest(**{**REQUEST, "document_type": name})
    # A JSON field cannot request trusted Pydantic validation context.
    with pytest.raises(ValidationError, match=code):
        ArticleGenRequest.model_validate({**REQUEST, "document_type": name,
                                        "genre_purpose": "offline", "production_ready": True})


def test_production_service_revalidates_offline_request_before_any_io(monkeypatch):
    request = ArticleGenRequest.model_validate(REQUEST, context={"genre_purpose": "offline"})
    monkeypatch.setattr(full_draft_generation, "prepare_full_draft_material",
                        lambda *_: pytest.fail("Offline adapter reached production materials"))
    monkeypatch.setattr(full_draft_generation, "load_writing_reference_pack",
                        lambda *_: pytest.fail("Offline adapter reached production reference I/O"))
    with pytest.raises(ValidationError, match="GENRE_OFFLINE_ONLY"):
        asyncio.run(full_draft_generation.generate_full_draft(request))


@pytest.mark.parametrize("genre", ["汇报材料", "未注册文种"])
def test_actual_http_route_does_not_expose_offline_capability(api, monkeypatch, genre):
    client, route = api
    monkeypatch.setattr(route, "prepare_full_draft_material",
                        lambda *_: pytest.fail("Rejected genre reached material preparation"))
    response = client.post("/api/generation/generate", json={**REQUEST,
        "document_type": genre, "genre_purpose": "offline"})
    assert response.status_code == 422
    assert "GENRE_" in response.text


def test_registry_rejects_duplicate_alias_without_partial_registration():
    isolated = registry.GenreRegistry()
    isolated.register(WORK_SUMMARY_ADAPTER)
    conflict = replace(REPORT_ADAPTER, aliases=("work_summary",))
    with pytest.raises(registry.GenreResolutionError, match="GENRE_IDENTITY_ALREADY_REGISTERED"):
        isolated.register(conflict)
    with pytest.raises(registry.GenreResolutionError, match="GENRE_NOT_REGISTERED"):
        isolated.resolve("report")


def test_adapter_has_no_evidence_or_fact_authority_switch():
    assert {field.name for field in fields(GenreAdapter)} == {
        "genre_id", "document_type", "aliases", "retrieval_profile", "drafting_rules",
        "structure_contract", "quality_contract_id", "quality_evaluator", "capabilities",
        "fact_policy_extensions",
    }
    for extension in ("BYPASS_FACT_SAFETY", "ALLOW_UNKNOWN", "CROSS_SCOPE", "EXPAND_EVIDENCE"):
        with pytest.raises(ValueError, match="GENRE_FACT_POLICY_EXTENSION_UNSUPPORTED"):
            replace(REPORT_ADAPTER, fact_policy_extensions=(extension,))


def _raw_candidates():
    sections = [
        ("基本情况", "progress", "测试部门正在推进档案整理。"),
        ("需要说明的问题", "problem", "测试部门档案整理仍存在衔接不足。"),
        ("意见建议", "measure", "测试部门计划开展档案复核。"),
    ]
    return [dict(doc_id="synthetic-report", title=REQUEST["title"], chunk_id=f"report-{index}",
                 chunk_index=index, section_index=index, section_heading=heading,
                 document_type="汇报材料", score=.94, vector_score=.94,
                 chunk_text=text + "以下为离线测试专用合成背景，不描述真实业务情况。",
                 metadata={"document_type": "汇报材料", "document_role": "fact_source",
                           "content_role": role, "section_heading": heading})
            for index, (heading, role, text) in enumerate(sections)]


def test_second_adapter_registration_runs_complete_offline_chain_without_core_edits(tmp_path, monkeypatch):
    shared_paths = [ROOT / "backend" / path for path in (
        "app/models/generation.py", "app/services/retrieval/document_retriever.py",
        "app/services/generation/task_assembly.py", "app/services/generation/official_document_drafting.py",
        "app/services/generation/full_article_quality.py", "app/services/generation/full_draft_generation.py",
        "app/services/generation/atomic_evidence.py", "app/services/generation/flow3_v3_trace.py",
        "app/services/generation/delivery_decision.py")]
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in shared_paths}
    isolated = registry.GenreRegistry()
    isolated.register(WORK_SUMMARY_ADAPTER)
    monkeypatch.setattr(registry, "GENRE_REGISTRY", isolated)
    with pytest.raises(ValidationError, match="GENRE_NOT_REGISTERED"):
        ArticleGenRequest.model_validate(REQUEST, context={"genre_purpose": "offline"})
    isolated.register(REPORT_ADAPTER)  # The only extension operation.
    request = ArticleGenRequest.model_validate(REQUEST, context={"genre_purpose": "offline"})
    adapter = registry.resolve_genre(request.document_type, purpose="offline")
    assert not adapter.capabilities.production_ready
    task = build_official_draft_task(request)
    searches = []

    def offline_search(query, **kwargs):
        searches.append((query, kwargs))
        return {"results": _raw_candidates()}

    retrieval = retrieve_documents(task, search_fn=offline_search, persist_trace=False,
                                   retrieval_profile=adapter.retrieval_profile)
    assert len(searches) == 1
    assert {row["coverage_role"] for row in retrieval["selected_chunks"]} == set(adapter.structure_contract.section_roles)
    assets = tuple(load_stable_material_asset(row, tmp_path / "synthetic-assets").asset
                   for row in retrieval["selected_chunks"])
    assembly = assemble_task_from_assets(request, retrieval, assets)
    evidence_snapshot = assembly.packet.registry_json
    prompt = build_full_draft_prompt(assembly.packet)
    assert prompt["ruleset_id"] == adapter.drafting_rules.ruleset_id
    assert "基本情况—需要说明的问题—意见建议" in prompt["system"]
    assert "S02主写法参考" not in prompt["user"]
    assert "工作总结（summary）" not in prompt["user"]
    # Intentional unsupported completion tests P3 + Must-Deliver under the
    # second genre; this is a fixture, never a generated article.
    body = "# 合成档案整理情况汇报\n\n## 基本情况\n\n测试部门已经完成档案整理。\n\n## 意见建议\n\n测试部门计划开展档案复核。\n"
    candidate = import_full_article(body, assembly.packet.candidate_input(), {"kind": "OFFLINE_ARCHITECTURE_PROOF"})
    citations = propose_exact_citations(candidate)
    facts = evaluate_full_article_facts(candidate, citations)
    fact_result = json.loads(facts.result_json)
    assert fact_result["status"] == "REVIEW_REQUIRED"
    assert any("SEMANTIC_STATE_UPGRADE" in unit.get("risk_codes", []) for unit in fact_result["units"])
    quality = evaluate_full_article_quality(candidate, citations, facts)
    quality_result = json.loads(quality.result_json)
    assert quality_result["quality_contract_id"] == adapter.quality_contract_id
    assert "work_summary_quality_contract" not in quality.result_json
    assert quality_result["quality_pass"] is False
    rendered = render_full_article(build_full_article_handoff(candidate, citations, facts, quality))
    delivered = full_article_review_result(rendered)
    assert delivered.delivery_status == "SUCCESS_WITH_REVIEW"
    assert delivered.risk_items
    assert delivered.content == rendered.final_text == candidate.content == body
    assert candidate.evidence_json == assembly.packet.registry_json == evidence_snapshot
    assert before == {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in shared_paths}


def test_shared_pipeline_does_not_import_concrete_adapters():
    import ast
    for relative in (
        "backend/app/services/retrieval/document_retriever.py",
        "backend/app/services/generation/official_document_drafting.py",
        "backend/app/services/generation/full_draft_generation.py",
        "backend/app/services/generation/full_article_quality.py",
        "backend/app/services/generation/task_assembly.py",
    ):
        tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
        imports = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        assert not any(name.endswith(("genres.work_summary", "genres.report", "work_summary_quality_gate")) for name in imports), relative
