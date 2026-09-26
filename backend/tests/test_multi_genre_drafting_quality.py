"""MULTI-GENRE-FOUNDATION: pinned Writer parity and independent offline quality."""
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import types

import pytest

from app.models.full_article import FullArticleContractError, bind_report, canonical_json
from app.services.generation import material_digest, task_assembly, writing_reference_pack
from app.services.generation.full_article_candidate_adapter import import_full_article, propose_exact_citations
from app.services.generation.flow3_v3_trace import evaluate_full_article_facts
from app.services.generation.full_draft_material import FullDraftMaterialPacket
from app.services.generation.full_article_quality import evaluate_full_article_quality
from app.services.generation.genres.registry import GENRE_REGISTRY, GenreResolutionError, resolve_genre
from app.services.generation.official_document_drafting import (
    HOST_BOUNDARY, build_full_draft_prompt, build_host_boundary, load_official_drafting_rules,
)
from test_material_digest_task_assembly import no_models, reference_pack, request
from test_source_substrate import sources


BASE_SHA = "0d789f7b563acd6f95b71b795d18fbf5a5072651"
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def summary_packet(tmp_path):
    retrieval = sources()
    digest = material_digest.load_material_digest(retrieval, tmp_path).digest
    return task_assembly.assemble_task(request(), retrieval, digest).packet


def with_genre(packet, document_type):
    task = {**json.loads(packet.task_json), "document_type": document_type}
    revised = FullDraftMaterialPacket.from_authorized_input(task, {
        "registry": json.loads(packet.registry_json), "registry_sha256": packet.registry_sha256,
        "unavailable": json.loads(packet.unavailable_json), "restrictions": json.loads(packet.restrictions_json)})
    brief = json.loads(packet.brief_json)
    brief["input_sha256"] = revised.brief_input_sha256
    return revised.with_brief(brief)


def checked_article(packet):
    source = next(iter(json.loads(packet.registry_json).values()))["source_scope"]["source_span"]
    candidate = import_full_article("# 合成离线文章\n\n" + source.rstrip("。") + "。\n",
        packet.candidate_input(), {"kind": "OFFLINE_ARCHITECTURE_PROOF"})
    citations = propose_exact_citations(candidate)
    return candidate, citations, evaluate_full_article_facts(candidate, citations)


def test_summary_prompt_boundary_and_upstream_bytes_match_base(summary_packet, reference_pack):
    path = "backend/app/services/generation/official_document_drafting.py"
    source = subprocess.check_output(["git", "show", BASE_SHA + ":" + path], cwd=ROOT)
    baseline = types.ModuleType("multi_genre_baseline_drafting")
    baseline.__file__ = str(ROOT / path)
    exec(compile(source, "baseline/official_document_drafting.py", "exec"), baseline.__dict__)
    adapter = resolve_genre("工作总结")
    assert HOST_BOUNDARY == baseline.HOST_BOUNDARY == build_host_boundary(adapter)
    assert load_official_drafting_rules(adapter) == baseline.load_official_drafting_rules()
    assert adapter.drafting_rules.ruleset_id == baseline.OFFICIAL_DRAFTING_RULESET_ID
    assert adapter.drafting_rules.ruleset_hash == baseline.OFFICIAL_DRAFTING_RULESET_HASH
    assert dict(adapter.drafting_rules.integrity_locks) == baseline._LOCK
    with writing_reference_pack.use_writing_reference_pack(reference_pack):
        assert build_full_draft_prompt(summary_packet) == baseline.build_full_draft_prompt(summary_packet)


@pytest.mark.parametrize("corrupt_path", ["LICENSE", "src/adapters/offline/build.py",
    "dist/offline/default/doc-types/summary-工作总结/system_prompt.md"])
def test_summary_license_build_and_prompt_integrity_remain_fail_closed(tmp_path, corrupt_path):
    adapter = resolve_genre("工作总结")
    for name, _ in adapter.drafting_rules.integrity_locks:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        raw = (adapter.drafting_rules.root / name).read_bytes()
        target.write_bytes(raw + (b"\nchanged" if name == corrupt_path else b""))
    changed = replace(adapter, drafting_rules=replace(adapter.drafting_rules, root=tmp_path))
    with pytest.raises(FullArticleContractError, match="OFFICIAL_DRAFTING_RULESET_HASH_MISMATCH"):
        load_official_drafting_rules(changed)


def test_report_uses_own_rules_and_never_loads_summary_private_references(summary_packet, monkeypatch):
    from app.services.generation import full_draft_material
    packet = with_genre(summary_packet, "汇报材料")
    monkeypatch.setattr(full_draft_material, "case32_writing_references",
        lambda: pytest.fail("Report must not load work-summary private references"))
    prompt = build_full_draft_prompt(packet)
    adapter = resolve_genre("汇报材料", purpose="offline")
    assert prompt["ruleset_id"] == adapter.drafting_rules.ruleset_id
    assert prompt["ruleset_hash"] == adapter.drafting_rules.ruleset_hash
    assert load_official_drafting_rules(adapter) in prompt["system"]
    assert "PINNED_UPSTREAM_ORIGINAL_BEGIN" in prompt["system"]
    assert "目标文种：汇报材料（presentation）" in prompt["user"]
    assert "S02主写法参考" not in prompt["user"]
    assert packet.registry_json == summary_packet.registry_json


def test_all_genres_keep_shared_semantic_state_scope_and_must_deliver_boundary():
    summary, report = resolve_genre("工作总结"), resolve_genre("汇报材料")
    for adapter in (summary, report):
        boundary = build_host_boundary(adapter)
        for line_number in (1, 2, 4, 5, 6, 8, 9, 10):
            original = next(line for line in HOST_BOUNDARY.splitlines() if line.startswith(str(line_number) + ". "))
            assert original in boundary
        assert "必须交付完整文章" in boundary
        assert "UNKNOWN 不授权补成已完成或结果态" in boundary


@pytest.mark.parametrize("genre", ["", "未注册文种"])
def test_prompt_and_quality_unknown_genre_fail_closed(summary_packet, genre):
    packet = with_genre(summary_packet, genre)
    with pytest.raises(GenreResolutionError, match="GENRE_NOT_REGISTERED"):
        build_full_draft_prompt(packet)
    with pytest.raises(GenreResolutionError, match="GENRE_NOT_REGISTERED"):
        evaluate_full_article_quality(*checked_article(packet))


def test_summary_quality_receipt_is_exactly_preserved(summary_packet):
    from app.services.generation.work_summary_quality_gate import evaluate_full_article_quality as summary_quality
    args = checked_article(summary_packet)
    assert evaluate_full_article_quality(*args) == summary_quality(*args)
    path = "backend/app/services/generation/work_summary_quality_gate.py"
    source = subprocess.check_output(["git", "show", BASE_SHA + ":" + path], cwd=ROOT)
    baseline = types.ModuleType("multi_genre_baseline_quality")
    baseline.__file__ = str(ROOT / path)
    exec(compile(source, "baseline/work_summary_quality_gate.py", "exec"), baseline.__dict__)
    assert evaluate_full_article_quality(*args) == baseline.evaluate_full_article_quality(*args)
    payload = json.loads(evaluate_full_article_quality(*args).result_json)
    structure_check = next(check for check in payload["checks"] if check["id"] == "semantic_task_and_evidence_coverage")
    assert structure_check["migration"] == resolve_genre("工作总结").structure_contract.maturity_review_instruction


def test_report_quality_contract_is_independent_and_body_delivery_stays_identical(summary_packet):
    from app.services.flow4.handoff import build_full_article_handoff
    from app.services.flow4.renderer import render_full_article
    from app.services.generation.delivery_decision import full_article_review_result
    packet = with_genre(summary_packet, "汇报材料")
    candidate, citations, facts = checked_article(packet)
    quality = evaluate_full_article_quality(candidate, citations, facts)
    payload = json.loads(quality.result_json)
    adapter = resolve_genre("汇报材料")
    assert payload["quality_contract_id"] == adapter.quality_contract_id
    assert payload["quality_contract_id"] != resolve_genre("工作总结").quality_contract_id
    assert payload["offline_architecture_proof"] is True
    assert payload["quality_pass"] is False and payload["production_ready"] is False
    check = next(check for check in payload["checks"] if check["id"] == "semantic_task_and_evidence_coverage")
    assert check["evidence"]["section_roles"] == list(adapter.structure_contract.section_roles)
    rendered = render_full_article(build_full_article_handoff(candidate, citations, facts, quality))
    result = full_article_review_result(rendered)
    assert result.article_markdown == candidate.content == rendered.final_text
    assert result.delivery_status == "SUCCESS_WITH_REVIEW"


def test_forged_fact_report_is_rejected_before_genre_callback(summary_packet, monkeypatch):
    candidate, citations, facts = checked_article(with_genre(summary_packet, "汇报材料"))
    adapter = resolve_genre("汇报材料")
    altered = replace(adapter, quality_evaluator=lambda *args: pytest.fail("Untrusted fact report reached adapter"))
    monkeypatch.setitem(GENRE_REGISTRY._identities, adapter.document_type, altered)
    payload = json.loads(facts.result_json)
    payload["unresolved_count"] += 1
    forged = bind_report("FACT", candidate, citations, payload)
    with pytest.raises(FullArticleContractError):
        evaluate_full_article_quality(candidate, citations, forged)


@pytest.mark.parametrize("field,value", [("quality_contract_id", "wrong"), ("fact_report_sha256", "wrong"),
                                       ("article_hash", "wrong")])
def test_adapter_cannot_forge_shared_quality_identity(summary_packet, monkeypatch, field, value):
    candidate, citations, facts = checked_article(with_genre(summary_packet, "汇报材料"))
    adapter = resolve_genre("汇报材料")
    payload = json.loads(evaluate_full_article_quality(candidate, citations, facts).result_json)
    payload[field] = value
    altered = replace(adapter, quality_evaluator=lambda c, s, f: bind_report("QUALITY", c, s, payload))
    monkeypatch.setitem(GENRE_REGISTRY._identities, adapter.document_type, altered)
    with pytest.raises(FullArticleContractError, match="GENRE_QUALITY_REPORT_IDENTITY_INVALID"):
        evaluate_full_article_quality(candidate, citations, facts)


def test_adapter_cannot_promote_canonical_fact_risk_to_pass(summary_packet, monkeypatch):
    packet = with_genre(summary_packet, "汇报材料")
    candidate = import_full_article("# 合成离线文章\n\n合成部门已完成999项重大工程。\n",
        packet.candidate_input(), {"kind": "OFFLINE_ARCHITECTURE_PROOF"})
    citations = propose_exact_citations(candidate)
    facts = evaluate_full_article_facts(candidate, citations)
    assert json.loads(facts.result_json)["status"] != "PASS"
    payload = json.loads(evaluate_full_article_quality(candidate, citations, facts).result_json)
    payload.update(quality_pass=True, article_qualified=True)
    adapter = resolve_genre("汇报材料")
    altered = replace(adapter, quality_evaluator=lambda c, s, f: bind_report("QUALITY", c, s, payload))
    monkeypatch.setitem(GENRE_REGISTRY._identities, adapter.document_type, altered)
    with pytest.raises(FullArticleContractError, match="GENRE_QUALITY_CANNOT_OVERRIDE_FACT_SAFETY"):
        evaluate_full_article_quality(candidate, citations, facts)


@pytest.mark.parametrize("check_id", ["raw_structure_and_locations", "whole_article_budget", "fact_safety"])
@pytest.mark.parametrize("mutation", ["missing", "duplicate", "false_evidence"])
def test_adapter_cannot_replace_common_quality_boundary(summary_packet, monkeypatch, check_id, mutation):
    candidate, citations, facts = checked_article(with_genre(summary_packet, "汇报材料"))
    payload = json.loads(evaluate_full_article_quality(candidate, citations, facts).result_json)
    check = next(item for item in payload["checks"] if item["id"] == check_id)
    if mutation == "missing":
        payload["checks"].remove(check)
    elif mutation == "duplicate":
        payload["checks"].append(check.copy())
    else:
        check["evidence"] = {"unsupported_adapter_claim": True}
    adapter = resolve_genre("汇报材料")
    altered = replace(adapter, quality_evaluator=lambda c, s, f: bind_report("QUALITY", c, s, payload))
    monkeypatch.setitem(GENRE_REGISTRY._identities, adapter.document_type, altered)
    with pytest.raises(FullArticleContractError, match="GENRE_QUALITY_SHARED_CHECK_INVALID"):
        evaluate_full_article_quality(candidate, citations, facts)
