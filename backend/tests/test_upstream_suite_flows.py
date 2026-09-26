"""Synthetic upstream-genre route proofs, never real commercial acceptance.

The local fixture Writer returns authored text. Canonical FACT, quality, shared
runtime, Must-Deliver and FLOW4 execute normally. Promotion exists only inside
an isolated test registry; no production configuration is changed.
"""
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
from types import ModuleType

import pytest
from pydantic import ValidationError

from app import config
from app.models.full_article import FullArticleContractError, bind_report
from app.models.generation import ArticleGenRequest
from app.services.generation import full_draft_generation as route
from app.services.generation import material_digest, task_assembly, writing_reference_pack
from app.services.generation.commercial_genre_runtime import CommercialGenreRuntime
from app.services.generation.flow3_v3_trace import evaluate_full_article_facts
from app.services.generation.full_article_candidate_adapter import import_full_article, propose_exact_citations
from app.services.generation.full_article_quality import evaluate_full_article_quality
from app.services.generation.genres import registry
from app.services.generation.genres.commercial_contract import CommercialMaturity
from app.services.generation.genres.commercial_gate import validate_commercial_contract
from app.services.generation.genres.upstream_adapters import UPSTREAM_ADAPTERS
from app.services.generation.genres.work_summary import WORK_SUMMARY_ADAPTER
from app.services.generation.official_document_drafting import build_full_draft_prompt
from app.services.llm.openai_compatible_provider import OpenAICompatibleProvider
from test_commercial_genre_parity import summary_packet, summary_request
import test_commercial_genre_parity as reference_parity
from test_material_digest_task_assembly import reference_pack
from test_multi_genre_drafting_quality import with_genre
from test_source_substrate import sources


BASE_SHA = "ecfa1b9da98101fe74ecabc26b026ec8818a3488"
ROOT = Path(__file__).resolve().parents[2]
ALL_ADAPTERS = (WORK_SUMMARY_ADAPTER, *UPSTREAM_ADAPTERS)


def _isolated(monkeypatch, *adapters):
    isolated = registry.GenreRegistry()
    for adapter in adapters:
        isolated.register(adapter)
    monkeypatch.setattr(registry, "GENRE_REGISTRY", isolated)
    return isolated


@pytest.mark.parametrize("adapter", UPSTREAM_ADAPTERS, ids=lambda a: a.genre_id)
def test_every_integrated_contract_is_complete_without_production_admission(adapter):
    contract = validate_commercial_contract(adapter)
    assert contract.maturity is CommercialMaturity.ENGINEERING_READY
    assert adapter.capabilities.production_ready is False
    assert adapter.capabilities.offline_architecture_proof is True
    assert registry.resolve_genre(adapter.document_type, purpose="offline") is adapter
    with pytest.raises(registry.GenreResolutionError, match="GENRE_OFFLINE_ONLY"):
        CommercialGenreRuntime.admit(adapter.document_type)
    payload = dict(title="合成离线文种任务", document_type=adapter.document_type,
        generation_route="full_article_drafting", target_words=1200,
        genre_purpose="offline", production_ready=True,
        maturity="COMMERCIALLY_VERIFIED", commercial={"maturity": "PRODUCTION_ENABLED"},
        context={"genre_purpose": "offline"})
    with pytest.raises(ValidationError, match="GENRE_OFFLINE_ONLY"):
        ArticleGenRequest.model_validate(payload)


@pytest.mark.parametrize("adapter", ALL_ADAPTERS, ids=lambda a: a.genre_id)
def test_every_genre_uses_the_shared_formal_runtime_fact_and_delivery(
        adapter, reference_pack, monkeypatch, tmp_path):
    # Trusted test registration proves runtime completeness; the original
    # engineering-only adapter remains immutable and unavailable to payloads.
    promoted = replace(adapter,
        capabilities=replace(adapter.capabilities, status="VERIFIED_CORE_GENRE", production_ready=True),
        commercial=replace(adapter.commercial, maturity=CommercialMaturity.PRODUCTION_ENABLED))
    _isolated(monkeypatch, promoted)
    request = ArticleGenRequest(title="合成安全检查" + adapter.document_type,
        document_type=adapter.document_type, generation_route="full_article_drafting", target_words=1200)
    retrieval = sources()
    digest = material_digest.load_material_digest(retrieval, tmp_path / "synthetic-assets").digest
    packet = task_assembly.assemble_task(request, retrieval, digest).packet
    original_evidence = packet.registry_json
    with writing_reference_pack.use_writing_reference_pack(reference_pack):
        expected_prompt = build_full_draft_prompt(packet)
    assert expected_prompt["ruleset_id"] == adapter.drafting_rules.ruleset_id
    body = ("# 合成安全检查" + adapter.document_type + "\n\n"
            "测试街道组织安全检查。\n\n测试街道已完成999项重大工程，取得全面成效。\n")
    provider = OpenAICompatibleProvider(config.ProviderConfig(
        model="qwen-synthetic", api_key="synthetic-only", base_url="https://offline.invalid"))
    calls = []

    def authored_writer(prompt, **kwargs):
        calls.append((prompt, kwargs))
        kwargs["on_http_request_attempt"](1)
        return body

    monkeypatch.setattr(provider, "generate_text", authored_writer)
    monkeypatch.setattr(route, "get_llm_provider", lambda: provider)
    monkeypatch.setattr(route, "prepare_full_draft_material", lambda request: packet)
    monkeypatch.setattr(route, "load_writing_reference_pack", lambda: reference_pack)
    monkeypatch.setattr(route, "get_output_dir", lambda: tmp_path / "output")
    result = asyncio.run(route.generate_full_draft(request)).articles[0]
    assert len(calls) == 1
    assert calls[0][0] == expected_prompt["user"]
    assert calls[0][1]["system_prompt"] == expected_prompt["system"]
    assert result.delivery_status == "SUCCESS_WITH_REVIEW" and result.risk_items
    assert result.article_markdown == body
    assert result.save_path and result.docx_path
    assert Path(result.save_path).read_bytes() == body.encode("utf-8")
    output, = (tmp_path / "output" / "full-draft").iterdir()
    assert not (output / "checks_unavailable.json").exists()
    assert (output / "RAW_WRITER_ARTICLE.md").read_bytes() == body.encode("utf-8")
    assert (output / "FINAL_DELIVERED_ARTICLE.md").read_bytes() == body.encode("utf-8")
    accounting = json.loads((output / "call_accounting.json").read_bytes())
    assert accounting["commercial_runtime"]["genre_id"] == adapter.genre_id
    assert accounting["writer_calls"] == 1
    assert accounting["canonical_fact_status"] == "REVIEW_REQUIRED"
    facts = json.loads(json.loads((output / "fact_report.json").read_bytes())["result_json"])
    quality = json.loads(json.loads((output / "quality_report.json").read_bytes())["result_json"])
    assert facts["status"] == "REVIEW_REQUIRED"
    assert quality["quality_contract_id"] == adapter.quality_contract_id
    assert quality["quality_pass"] is False
    assert packet.registry_json == original_evidence


@pytest.mark.parametrize("adapter", UPSTREAM_ADAPTERS, ids=lambda a: a.genre_id)
def test_no_migrated_genre_can_upgrade_a_canonical_fact_failure(adapter, summary_packet, monkeypatch):
    packet = with_genre(summary_packet, adapter.document_type)
    candidate = import_full_article("# 合成" + adapter.document_type + "\n\n测试街道完成999项工程。\n",
        packet.candidate_input(), {"kind": "SYNTHETIC_FACT_AUTHORITY_NEGATIVE_CONTROL"})
    citations = propose_exact_citations(candidate)
    facts = evaluate_full_article_facts(candidate, citations)
    assert json.loads(facts.result_json)["status"] != "PASS"
    result = json.loads(evaluate_full_article_quality(candidate, citations, facts).result_json)
    result.update(status="PASS", quality_pass=True, article_qualified=True)
    malicious = replace(adapter, quality_evaluator=lambda c, s, f: bind_report("QUALITY", c, s, result))
    _isolated(monkeypatch, malicious)
    with pytest.raises(FullArticleContractError, match="GENRE_QUALITY_CANNOT_OVERRIDE_FACT_SAFETY"):
        evaluate_full_article_quality(candidate, citations, facts)


@pytest.fixture
def exact_base(monkeypatch):
    loaded = {}

    def load(name, path=None):
        if name not in loaded:
            path = path or "backend/app/services/generation/" + name + ".py"
            source = subprocess.check_output(["git", "show", BASE_SHA + ":" + path], cwd=ROOT)
            module = ModuleType("upstream_suite_exact_base_" + name)
            module.__file__ = str(ROOT / path)
            monkeypatch.setitem(sys.modules, module.__name__, module)
            exec(compile(source, BASE_SHA + "/" + path, "exec"), module.__dict__)
            loaded[name] = module
        return loaded[name]

    return load


def test_work_summary_full_prompt_and_rules_match_exact_migration_base(exact_base, summary_packet, reference_pack):
    reference_parity.test_full_prompt_and_locked_rules_remain_exactly_base(
        exact_base, summary_packet, reference_pack)


def test_work_summary_formal_route_body_fact_quality_flow4_match_exact_base(
        exact_base, summary_packet, reference_pack, monkeypatch, tmp_path):
    reference_parity.test_full_route_writer_body_facts_quality_and_delivery_match_base(
        exact_base, summary_packet, reference_pack, monkeypatch, tmp_path, "qwen-synthetic", True)
