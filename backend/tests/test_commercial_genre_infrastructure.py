"""Commercial admission, authority and extension tests; invented offline inputs.

No supplied body here is evidence of real writing quality. Failures exercise
actual request/runtime/FACT/FLOW4 owners, not precomputed verdicts.
"""
import ast
import asyncio
import copy
from dataclasses import fields, replace
import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.models.full_article import FullArticleContractError, bind_report
from app.models.generation import ArticleGenRequest
from app.services.generation import full_draft_generation as route
from app.services.generation import material_digest, task_assembly
from app.services.generation.commercial_genre_runtime import (
    CommercialGenreRuntime, planning_instruction, reading_group_key,
)
from app.services.generation.genres import registry
from app.services.generation.genres.commercial_contract import CommercialMaturity
from app.services.generation.genres.commercial_gate import (
    CommercialAdmissionError, require_commercial_production, validate_commercial_contract,
)
from app.services.generation.genres.contracts import GenreCapabilities
from app.services.generation.genres.report import REPORT_ADAPTER
from app.services.generation.genres.work_summary import WORK_SUMMARY_ADAPTER
from app.services.generation.full_article_candidate_adapter import import_full_article, propose_exact_citations
from app.services.generation.flow3_v3_trace import evaluate_full_article_facts
from app.services.generation.full_article_quality import evaluate_full_article_quality
from app.services.generation.official_document_drafting import build_full_draft_prompt
from app.services.generation.delivery_decision import full_article_review_result
from app.services.flow4.handoff import build_full_article_handoff
from app.services.flow4.renderer import render_full_article
from test_commercial_genre_parity import summary_packet, summary_request
from test_material_digest_task_assembly import reference_pack
from test_writing_reference_pack_02b1 import api, configured
from test_production_task_assembly import library, REQUEST
from test_source_substrate import sources


ROOT = Path(__file__).resolve().parents[2]
CONTRACT = WORK_SUMMARY_ADAPTER.commercial
CAPABILITIES = ("planning", "evidence_organization", "writer_control", "writer_calls", "recovery", "delivery")


def install(monkeypatch, *adapters):
    isolated = registry.GenreRegistry()
    for adapter in adapters:
        isolated.register(adapter)
    monkeypatch.setattr(registry, "GENRE_REGISTRY", isolated)
    return isolated


def forbidden(*args, **kwargs):
    pytest.fail("Rejected capability reached material/reference/Writer work")


def test_reference_contract_is_complete_without_upgrading_quality():
    contract = require_commercial_production(WORK_SUMMARY_ADAPTER)
    assert contract is CONTRACT
    assert contract.maturity is CommercialMaturity.PRODUCTION_ENABLED
    assert contract.maturity is not CommercialMaturity.COMMERCIALLY_VERIFIED
    runtime = CommercialGenreRuntime.admit("work_summary")
    assert runtime.adapter is WORK_SUMMARY_ADAPTER
    assert runtime.claim_writer_call() == {"retry_limit": 0, "preserve_whitespace": True}
    with pytest.raises(FullArticleContractError, match="COMMERCIAL_WRITER_BUDGET_EXHAUSTED"):
        runtime.claim_writer_call()
    with pytest.raises(FullArticleContractError, match="COMMERCIAL_RECOVERY_DISABLED"):
        runtime.authorize_recovery()


@pytest.mark.parametrize("missing", ("commercial", *CAPABILITIES))
def test_missing_capability_fails_before_any_material_work(monkeypatch, missing):
    contract = None if missing == "commercial" else replace(CONTRACT, **{missing: None})
    install(monkeypatch, replace(WORK_SUMMARY_ADAPTER, commercial=contract))
    for name in ("prepare_full_draft_material", "load_writing_reference_pack", "get_llm_provider"):
        monkeypatch.setattr(route, name, forbidden)
    # model_construct deliberately bypasses the HTTP schema. Service must still reject.
    request = ArticleGenRequest.model_construct(**REQUEST)
    with pytest.raises(ValidationError, match="COMMERCIAL_.*REQUIRED"):
        asyncio.run(route.generate_full_draft(request))
    with pytest.raises(registry.GenreResolutionError, match="COMMERCIAL_.*REQUIRED"):
        CommercialGenreRuntime.admit("work_summary")


@pytest.mark.parametrize("stage", list(CommercialMaturity)[:3])
def test_registration_offline_and_engineering_maturity_do_not_grant_production(monkeypatch, stage):
    adapter = replace(WORK_SUMMARY_ADAPTER, commercial=replace(CONTRACT, maturity=stage))
    isolated = install(monkeypatch, adapter)
    assert isolated.resolve(adapter.genre_id) is adapter
    assert isolated.resolve(adapter.genre_id, purpose="offline") is adapter
    assert validate_commercial_contract(adapter).maturity is stage
    with pytest.raises(registry.GenreResolutionError, match="COMMERCIAL_MATURITY_NOT_ADMITTED"):
        isolated.resolve(adapter.genre_id, purpose="production")


@pytest.mark.parametrize("field,value", [
    ("genre_id", "other"), ("version", ""), ("version", "private source\ntext"),
    ("maturity", "PRODUCTION_ENABLED"),
    ("planning", replace(CONTRACT.planning, mode="frozen_plan")),
    ("planning", replace(CONTRACT.planning, instruction="")),
    ("evidence_organization", replace(CONTRACT.evidence_organization, grouping_key=None)),
    ("writer_control", replace(CONTRACT.writer_control, source_scope_binding="all_evidence")),
    ("writer_control", replace(CONTRACT.writer_control, body_policy="rewrite")),
    ("writer_calls", replace(CONTRACT.writer_calls, max_calls=2)),
    ("writer_calls", replace(CONTRACT.writer_calls, max_calls=True)),
    ("writer_calls", replace(CONTRACT.writer_calls, material_calls=1)),
    ("writer_calls", replace(CONTRACT.writer_calls, retry_limit=1)),
    ("writer_calls", replace(CONTRACT.writer_calls, preserve_whitespace=False)),
    ("recovery", replace(CONTRACT.recovery, mode="enabled")),
    ("recovery", replace(CONTRACT.recovery, max_rounds=1)),
    ("recovery", replace(CONTRACT.recovery, writer_calls=1)),
    ("recovery", replace(CONTRACT.recovery, max_rounds=False)),
    ("delivery", replace(CONTRACT.delivery, formats=("txt",))),
    ("delivery", replace(CONTRACT.delivery, body_policy="normalized")),
    ("delivery", replace(CONTRACT.delivery, flow4_mode="genre_owned")),
])
def test_inconsistent_contract_is_rejected(field, value):
    adapter = replace(WORK_SUMMARY_ADAPTER, commercial=replace(CONTRACT, **{field: value}))
    with pytest.raises(CommercialAdmissionError, match="COMMERCIAL_"):
        require_commercial_production(adapter)


@pytest.mark.parametrize("field,value", [
    ("retrieval_profile", None), ("drafting_rules", None), ("structure_contract", None),
    ("quality_evaluator", None), ("quality_contract_id", ""), ("capabilities", None),
    ("fact_policy_extensions", ("ALLOW_UNKNOWN",)),
])
def test_missing_foundation_capabilities_cannot_hide_behind_commercial_label(field, value):
    # Simulate an internally corrupted object rather than only the normal
    # frozen dataclass constructor, which already rejects some of these.
    adapter = copy.copy(WORK_SUMMARY_ADAPTER)
    object.__setattr__(adapter, field, value)
    with pytest.raises(CommercialAdmissionError, match="COMMERCIAL_"):
        require_commercial_production(adapter)


@pytest.mark.parametrize("owner,field,value", [
    ("drafting_rules", "integrity_locks", ()),
    ("drafting_rules", "ruleset_hash", "0" * 64),
    ("drafting_rules", "integrity_locks", (("../outside", "0" * 64),)),
    ("structure_contract", "section_roles", ()),
    ("retrieval_profile", "coverage_roles", ()),
    ("retrieval_profile", "primary_relevance_seed", True),
    ("capabilities", "production_ready", 1),
    ("capabilities", "export_formats", ("html",)),
])
def test_inconsistent_foundation_descriptor_fails_closed(owner, field, value):
    descriptor = copy.copy(getattr(WORK_SUMMARY_ADAPTER, owner))
    object.__setattr__(descriptor, field, value)
    adapter = copy.copy(WORK_SUMMARY_ADAPTER)
    object.__setattr__(adapter, owner, descriptor)
    with pytest.raises(CommercialAdmissionError, match="COMMERCIAL_"):
        require_commercial_production(adapter)


@pytest.mark.parametrize("owner,field", [
    ("retrieval_profile", "classify_coverage"), ("retrieval_profile", "chunk_preference"),
    ("commercial", "evidence_organization"), ("adapter", "quality_evaluator"),
])
def test_wrong_callback_signature_rejected_without_invoking_callback(owner, field):
    called = []

    def no_arguments():
        called.append(True)
        pytest.fail("A gate must not execute capabilities")

    if owner == "adapter":
        adapter = replace(WORK_SUMMARY_ADAPTER, quality_evaluator=no_arguments)
    elif owner == "commercial":
        capability = replace(CONTRACT.evidence_organization, grouping_key=no_arguments)
        adapter = replace(WORK_SUMMARY_ADAPTER, commercial=replace(CONTRACT, evidence_organization=capability))
    else:
        profile = replace(WORK_SUMMARY_ADAPTER.retrieval_profile, **{field: no_arguments})
        adapter = replace(WORK_SUMMARY_ADAPTER, retrieval_profile=profile)
    with pytest.raises(CommercialAdmissionError, match="COMMERCIAL_"):
        require_commercial_production(adapter)
    assert not called


@pytest.mark.parametrize("name", ["不存在", "", None])
def test_unregistered_genres_never_enter_runtime(name):
    with pytest.raises(registry.GenreResolutionError, match="GENRE_NOT_REGISTERED"):
        CommercialGenreRuntime.admit(name)


@pytest.mark.parametrize("forge_maturity", [False, True])
def test_report_production_flag_cannot_substitute_for_capabilities(monkeypatch, forge_maturity):
    # Even a trusted caller constructing a production-like flag cannot supply
    # only a maturity label while omitting the runtime capabilities.
    contract = None if not forge_maturity else replace(CONTRACT, genre_id="report", planning=None,
                                                       evidence_organization=None, writer_calls=None)
    forged = replace(REPORT_ADAPTER, capabilities=GenreCapabilities(
        status="VERIFIED_CORE_GENRE", production_ready=True, offline_architecture_proof=True),
        commercial=contract)
    install(monkeypatch, forged)
    assert registry.resolve_genre("report", purpose="offline") is forged
    with pytest.raises(registry.GenreResolutionError, match="COMMERCIAL_.*REQUIRED"):
        CommercialGenreRuntime.admit("report")


@pytest.mark.parametrize("name", ["synthetic_engineering", "未知文种"])
def test_json_cannot_forge_maturity_capabilities_or_validation_context(api, monkeypatch, name):
    client, _ = api
    synthetic = replace(WORK_SUMMARY_ADAPTER, genre_id="synthetic_engineering",
        document_type="合成工程文种", aliases=(), commercial=replace(CONTRACT,
            genre_id="synthetic_engineering", maturity=CommercialMaturity.ENGINEERING_READY))
    install(monkeypatch, WORK_SUMMARY_ADAPTER, synthetic)
    monkeypatch.setattr(route, "prepare_full_draft_material", forbidden)
    payload = {**REQUEST, "document_type": name, "genre_purpose": "offline", "production_ready": True,
               "maturity": "COMMERCIALLY_VERIFIED", "commercial": {"genre_id": name,
               "maturity": "PRODUCTION_ENABLED", "writer_calls": {"max_calls": 1}},
               "capabilities": {"production_ready": True, "status": "VERIFIED_CORE_GENRE"},
               "context": {"genre_purpose": "offline"}}
    result = client.post("/api/generation/generate", json=payload)
    assert result.status_code == 422
    assert ("COMMERCIAL_MATURITY_NOT_ADMITTED" if name == synthetic.genre_id
            else "GENRE_NOT_REGISTERED") in result.text


def test_actual_rule_bytes_fail_before_material_io(monkeypatch, tmp_path):
    adapter = WORK_SUMMARY_ADAPTER
    for name, _ in adapter.drafting_rules.integrity_locks:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((adapter.drafting_rules.root / name).read_bytes() + b"changed")
    install(monkeypatch, replace(adapter, drafting_rules=replace(adapter.drafting_rules, root=tmp_path)))
    for name in ("prepare_full_draft_material", "load_writing_reference_pack", "get_llm_provider"):
        monkeypatch.setattr(route, name, forbidden)
    with pytest.raises(FullArticleContractError, match="RULESET_HASH_MISMATCH"):
        asyncio.run(route.generate_full_draft(summary_request()))


@pytest.mark.parametrize("key", [("invented_action",), ["组织"], ("组织", "组织"), (3,)])
def test_reading_capability_cannot_invent_signals_or_return_mutable_keys(monkeypatch, key):
    policy = replace(CONTRACT.evidence_organization, grouping_key=lambda actions: key)
    install(monkeypatch, replace(WORK_SUMMARY_ADAPTER, commercial=replace(CONTRACT, evidence_organization=policy)))
    with pytest.raises(FullArticleContractError, match="COMMERCIAL_READING_KEY_INVALID"):
        reading_group_key("工作总结", ("组织", "开展"))


def test_synthetic_genre_composes_real_offline_chain_without_core_branch(monkeypatch, tmp_path):
    paths = [ROOT / "backend/app/services/generation" / name for name in (
        "commercial_genre_runtime.py", "full_draft_generation.py", "task_assembly.py",
        "official_document_drafting.py", "full_article_quality.py")]
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    seen = []

    def grouping(actions):
        assert type(actions) is tuple and all(type(x) is str for x in actions)
        seen.append(actions)
        return ()  # A single reading index is permitted, never a fact/section merge.

    commercial = replace(CONTRACT, genre_id="synthetic_contract", version="synthetic_contract_v1",
        maturity=CommercialMaturity.ENGINEERING_READY,
        planning=replace(CONTRACT.planning, identity="synthetic_internal_plan_v1",
                         instruction="按合成离线文种的结构规则自主安排完整文章。"),
        evidence_organization=replace(CONTRACT.evidence_organization,
                                      identity="synthetic_navigation_v1", grouping_key=grouping))
    adapter = replace(REPORT_ADAPTER, genre_id="synthetic_contract", document_type="合成合同文种",
                      aliases=(), commercial=commercial)
    isolated = install(monkeypatch, WORK_SUMMARY_ADAPTER, REPORT_ADAPTER)
    with pytest.raises(registry.GenreResolutionError, match="GENRE_NOT_REGISTERED"):
        isolated.resolve(adapter.genre_id)
    isolated.register(adapter)
    validate_commercial_contract(adapter)
    with pytest.raises(registry.GenreResolutionError, match="COMMERCIAL_MATURITY_NOT_ADMITTED"):
        CommercialGenreRuntime.admit(adapter.genre_id)
    request = ArticleGenRequest.model_validate({**REQUEST, "document_type": adapter.document_type},
                                               context={"genre_purpose": "offline"})
    retrieval = sources()
    digest = material_digest.load_material_digest(retrieval, tmp_path / "synthetic-digest").digest
    packet = task_assembly.assemble_task(request, retrieval, digest).packet
    assert seen
    brief, evidence = json.loads(packet.brief_json), json.loads(packet.registry_json)
    assert len(brief["matters"]) == 1
    assert brief["matters"][0]["evidence_ids"] == list(evidence)
    assert planning_instruction(adapter.document_type) == commercial.planning.instruction
    prompt = build_full_draft_prompt(packet)
    assert commercial.planning.instruction in prompt["user"]
    assert "S02主写法参考" not in prompt["user"]
    body = "# 合成离线证明\n\n测试街道完成999个未获授权项目。\n"
    candidate = import_full_article(body, packet.candidate_input(), {"kind": "OFFLINE_CONTRACT_TEST"})
    sidecar = propose_exact_citations(candidate)
    facts = evaluate_full_article_facts(candidate, sidecar)
    assert json.loads(facts.result_json)["status"] != "PASS"
    quality = evaluate_full_article_quality(candidate, sidecar, facts)
    result = full_article_review_result(render_full_article(build_full_article_handoff(candidate, sidecar, facts, quality)))
    assert result.delivery_status == "SUCCESS_WITH_REVIEW"
    assert result.article_markdown == body and result.risk_items
    assert candidate.evidence_json == packet.registry_json
    assert before == {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def test_capability_evaluator_cannot_override_canonical_fact_authority(summary_packet, monkeypatch):
    candidate = import_full_article("# 风险测试\n\n测试街道完成999项工程。\n",
                                   summary_packet.candidate_input(), {"kind": "OFFLINE_NEGATIVE_CONTROL"})
    sidecar = propose_exact_citations(candidate)
    facts = evaluate_full_article_facts(candidate, sidecar)
    report = evaluate_full_article_quality(candidate, sidecar, facts)
    assert json.loads(facts.result_json)["status"] != "PASS"
    value = json.loads(report.result_json)
    value.update(quality_pass=True, article_qualified=True, status="PASS")
    malicious = replace(WORK_SUMMARY_ADAPTER,
                        quality_evaluator=lambda c, s, f: bind_report("QUALITY", c, s, value))
    install(monkeypatch, malicious)
    with pytest.raises(FullArticleContractError, match="GENRE_QUALITY_CANNOT_OVERRIDE_FACT_SAFETY"):
        evaluate_full_article_quality(candidate, sidecar, facts)
    # A stale canonical report cannot authorize a different candidate/body.
    changed = import_full_article(candidate.content + "新增不支持的结论。\n",
                                  summary_packet.candidate_input(), {"kind": "OFFLINE_NEGATIVE_CONTROL"})
    with pytest.raises(FullArticleContractError):
        evaluate_full_article_quality(changed, propose_exact_citations(changed), facts)


def test_runtime_diagnostics_reuse_accounting_without_source_text(configured, api, library, tmp_path):
    client, _ = api
    response = client.post("/api/generation/generate", json=REQUEST)
    assert response.status_code == 200
    root, = (tmp_path / "full-draft").iterdir()
    accounting = json.loads((root / "call_accounting.json").read_bytes())
    diagnostic = accounting["commercial_runtime"]
    assert diagnostic == CommercialGenreRuntime.admit("work_summary").diagnostics()
    assert diagnostic["production_admission"] == "ADMITTED"
    assert diagnostic["maturity"] == "PRODUCTION_ENABLED"
    assert diagnostic["recovery_mode"] == "disabled"
    fact_report = json.loads((root / "fact_report.json").read_bytes())
    assert accounting["canonical_fact_status"] == json.loads(fact_report["result_json"])["status"]
    assert accounting["status"] == response.json()["articles"][0]["delivery_status"]
    encoded = json.dumps(diagnostic, ensure_ascii=False)
    for private in ("测试街道", "合成背景文字", "Evidence ID", "system_prompt", "api_key", "source_span"):
        assert private not in encoded


def test_direct_runtime_construction_cannot_self_authorize():
    with pytest.raises(FullArticleContractError, match="COMMERCIAL_RUNTIME_ADMISSION_MISMATCH"):
        CommercialGenreRuntime(REPORT_ADAPTER, CONTRACT)
    with pytest.raises(FullArticleContractError, match="COMMERCIAL_RUNTIME_ADMISSION_MISMATCH"):
        CommercialGenreRuntime(WORK_SUMMARY_ADAPTER, replace(CONTRACT, version="forged"))


def test_quality_failure_keeps_canonical_fact_diagnostic_and_must_deliver(configured, api, library, monkeypatch, tmp_path):
    client, _ = api

    def unavailable(*args):
        raise FullArticleContractError("SYNTHETIC_QUALITY_UNAVAILABLE")

    monkeypatch.setattr(route, "evaluate_full_article_quality", unavailable)
    result = client.post("/api/generation/generate", json=REQUEST)
    assert result.status_code == 200
    article = result.json()["articles"][0]
    assert article["delivery_status"] == "SUCCESS_WITH_REVIEW"
    root, = (tmp_path / "full-draft").iterdir()
    assert (root / "checks_unavailable.json").exists()
    assert (root / "RAW_WRITER_ARTICLE.md").read_bytes() == (root / "FINAL_DELIVERED_ARTICLE.md").read_bytes()
    accounting = json.loads((root / "call_accounting.json").read_bytes())
    assert accounting["canonical_fact_status"] in {"PASS", "REVIEW_REQUIRED"}
    assert accounting["writer_calls"] == 1


def test_shared_formal_runtime_does_not_branch_on_genre_identity():
    for name in ("commercial_genre_runtime.py", "full_draft_generation.py", "task_assembly.py",
                 "official_document_drafting.py", "full_article_quality.py"):
        tree = ast.parse((ROOT / "backend/app/services/generation" / name).read_text("utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.If, ast.IfExp)):
                literals = [n.value for n in ast.walk(node.test) if isinstance(n, ast.Constant)]
                assert not any(x in literals for x in ("work_summary", "工作总结", "report", "汇报材料")), name


def test_contract_contains_no_fact_admission_or_verdict_callback():
    assert {f.name for f in fields(CONTRACT)} == {
        "genre_id", "version", "maturity", *CAPABILITIES}
    for name in CAPABILITIES:
        for field in fields(getattr(CONTRACT, name)):
            assert field.name not in {"admit_evidence", "fact_evaluator", "safe_verdict", "rewrite_body",
                                     "provider", "allow_unknown", "override_canonical_fact"}
