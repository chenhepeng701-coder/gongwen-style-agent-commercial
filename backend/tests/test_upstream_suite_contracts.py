"""Every adopted genre owns distinct reading/structure/quality contracts."""
from dataclasses import replace
import json

import pytest

from app.models.full_article import FullArticleContractError
from app.services.generation.full_article_candidate_adapter import import_full_article, propose_exact_citations
from app.services.generation.flow3_v3_trace import evaluate_full_article_facts
from app.services.generation.full_article_quality import evaluate_full_article_quality
from app.services.generation.genres import registry
from app.services.generation.genres.commercial_gate import CommercialAdmissionError, validate_commercial_contract
from app.services.generation.genres.upstream_adapters import UPSTREAM_ADAPTERS
from app.services.generation.genres.upstream_genre_contracts import GENRE_SEMANTICS, genre_spec
from app.services.generation.genres.upstream_genre_quality import observe_genre_content
from app.services.generation.genres.work_summary import WORK_SUMMARY_ADAPTER
from app.services.generation.official_document_drafting import build_full_draft_prompt, load_official_drafting_rules
from test_commercial_genre_parity import summary_packet
from test_multi_genre_drafting_quality import with_genre


ALL_ADAPTERS = (WORK_SUMMARY_ADAPTER, *UPSTREAM_ADAPTERS)


@pytest.mark.parametrize("upstream_id", tuple(GENRE_SEMANTICS))
def test_every_genre_profile_covers_own_roles_without_changing_shared_p1_ranking(upstream_id):
    from types import SimpleNamespace
    from app.services.retrieval.document_retriever import retrieve_documents
    spec = genre_spec(upstream_id)
    adapter = next(a for a in UPSTREAM_ADAPTERS if a.drafting_rules.ruleset_id.endswith("/" + upstream_id))
    assert adapter.structure_contract.section_roles == tuple(role.role_id for role in spec.roles)
    assert adapter.retrieval_profile.coverage_roles == tuple(role.role_id for role in spec.roles) + ("context",)
    rows = []
    for index, role in enumerate(spec.roles):
        row = dict(doc_id="synthetic-primary", title="合成档案整理", chunk_id=f"part-{index}",
            chunk_index=index, section_index=index, section_heading=role.label,
            document_type=adapter.document_type, score=.96, vector_score=.96,
            chunk_text="测试部门正在开展档案整理。该条内容仅为合成离线测试，不描述真实业务情况。",
            metadata={"document_type": adapter.document_type, "document_role": "fact_source",
                      "section_heading": role.label, "section_path": [role.label]})
        assert adapter.retrieval_profile.classify_coverage(row) == role.role_id
        rows.append(row)
    fallback = {**rows[0], "chunk_id": "unclassified", "chunk_index": len(rows),
        "section_index": len(rows), "section_heading": "未标注片段",
        "chunk_text": "甲乙丙丁戊己庚辛壬癸，仅供离线验证，不描述真实业务。",
        "metadata": {**rows[0]["metadata"], "section_heading": "未标注片段", "section_path": []}}
    assert adapter.retrieval_profile.classify_coverage(fallback) == "context"
    rows.append(fallback)
    task = SimpleNamespace(title="合成档案整理", document_type=adapter.document_type,
                           business_domain="", reference_scope="资料库")
    calls = []

    def synthetic_search(query, **kwargs):
        calls.append((query, kwargs))
        return {"results": rows}

    own = retrieve_documents(task, search_fn=synthetic_search, persist_trace=False,
                             retrieval_profile=adapter.retrieval_profile)
    control = retrieve_documents(task, search_fn=synthetic_search, persist_trace=False,
                                 retrieval_profile=WORK_SUMMARY_ADAPTER.retrieval_profile)
    assert own["ranked_documents"] == control["ranked_documents"]
    assert own["primary_document_id"] == control["primary_document_id"]
    assert own["auxiliary_document_ids"] == control["auxiliary_document_ids"]
    assert {row["coverage_role"] for row in own["selected_chunks"]} == set(adapter.retrieval_profile.coverage_roles)
    assert len(calls) == 2


@pytest.mark.parametrize("upstream_id", tuple(GENRE_SEMANTICS))
def test_each_genre_observes_functional_omissions_identity_and_contamination(upstream_id):
    spec = genre_spec(upstream_id)
    own_title = "合成" + spec.identity_terms[0]
    body = "# " + own_title + "\n\n" + "\n\n".join(role.label + "。" for role in spec.roles)
    present = observe_genre_content(upstream_id, body)
    assert present["required_elements"]["status"] == "PASS"
    assert present["document_identity"]["status"] == "PASS"
    missing = observe_genre_content(upstream_id, "# " + own_title + "\n\n待核实。")
    assert set(missing["required_elements"]["missing_roles"]) == {role.role_id for role in spec.roles if role.required}
    assert missing["required_elements"]["status"] == "REVIEW_REQUIRED"
    wrong_identity = observe_genre_content(upstream_id, "# 合成资料\n\n待核实。")
    assert wrong_identity["document_identity"]["status"] == ("REVIEW_REQUIRED" if spec.title_identity_required else "NOT_REQUIRED")
    contaminated = observe_genre_content(upstream_id, body + "\n\n" + spec.inappropriate_phrases[0])
    assert contaminated["inappropriate_mixing"]["status"] == "REVIEW_REQUIRED"
    assert contaminated["fact_authority"] == "SHARED_CANONICAL_FACT_ONLY"
    assert contaminated["semantic_maturity_evaluated"] is False


@pytest.mark.parametrize("upstream_id,body,missing", [
    ("request", "# 关于合成事项的请示\n\n根据现有材料，请求开展合成核对。\n", "approval_closing"),
    ("notice", "# 关于合成事项的通知\n\n为做好合成核对，现将具体事项通知如下。\n", None),
    ("minutes", "# 合成会议纪要\n\n会议基本信息待核实。会议议定有关事项。\n", None),
    ("work-plan", "# 合成工作方案\n\n制定背景、总体要求、工作目标、主要任务、保障措施。\n", "schedule"),
    ("speech", "# 合成会议讲话\n\n同志们，今天我讲重点任务。\n", "closing"),
    ("report", "# 合成事项报告\n\n基本情况及工作开展情况如下。请予批准。\n", None),
    ("presentation", "# 合成工作汇报\n\n工作进展、主要成效、存在问题、下一步措施。\n", None),
    ("letter", "# 关于合成事项的函\n\n事项缘由待核。主要意见是请求协商，支持为盼。\n", None),
])
def test_materially_different_categories_have_real_distinct_expectations(upstream_id, body, missing):
    observed = observe_genre_content(upstream_id, body)
    if missing:
        assert missing in observed["required_elements"]["missing_roles"]
    else:
        assert observed["required_elements"]["status"] == "PASS"
    if upstream_id == "request":
        assert observed["closing_convention"]["status"] == "REVIEW_REQUIRED"
        corrected = observe_genre_content(upstream_id, body + "妥否，请批示。\n")
        assert corrected["closing_convention"]["status"] == "PASS"
        assert corrected["required_elements"]["status"] == "PASS"
    if upstream_id == "notice":
        assert observed["closing_convention"]["status"] == "NOT_REQUIRED"
    if upstream_id == "report":
        assert observed["inappropriate_mixing"]["status"] == "REVIEW_REQUIRED"


@pytest.mark.parametrize("upstream_id", ("request", "notice", "minutes", "speech", "work-plan"))
def test_ordering_misplacement_is_a_review_finding_not_a_fact_override(upstream_id):
    spec = genre_spec(upstream_id)
    before, after = spec.order_pairs[0]
    labels = {role.role_id: role.label for role in spec.roles}
    observed = observe_genre_content(upstream_id, "# 合成" + spec.identity_terms[0] + "\n\n" + labels[after] + "。\n\n" + labels[before] + "。")
    assert observed["ordering"]["status"] == "REVIEW_REQUIRED"
    assert {"before": before, "after": after} in [
        {key: pair[key] for key in ("before", "after")} for pair in observed["ordering"]["reversed_pairs"]]


@pytest.mark.parametrize("body", [
    "# 合成事项请示：妥否，请批示\n\n根据现有材料，请求开展合成核对。\n",
    "# 合成事项请示\n\n根据现有材料，请求开展合成核对。妥否，请批示。\n\n" + "资料待核。" * 60,
])
def test_request_closing_cue_in_title_or_distant_body_does_not_satisfy_closing(body):
    observed = observe_genre_content("request", body)
    assert observed["closing_convention"]["status"] == "REVIEW_REQUIRED"
    assert observed["closing_convention"]["locations"] == []


def test_all_twenty_two_adapters_are_registered_once_without_alias_ambiguity():
    assert len(ALL_ADAPTERS) == 22
    assert len({adapter.genre_id for adapter in ALL_ADAPTERS}) == 22
    identities = {}
    isolated = registry.GenreRegistry()
    for adapter in ALL_ADAPTERS:
        isolated.register(adapter)
        for identity in set((adapter.genre_id, adapter.document_type, *adapter.aliases)):
            assert identity not in identities
            identities[identity] = adapter
            assert registry.resolve_genre(identity) is adapter
    assert set(registry.GENRE_REGISTRY._identities) == set(identities)
    assert {id(a) for a in registry.GENRE_REGISTRY._identities.values()} == {id(a) for a in ALL_ADAPTERS}


def test_every_genre_has_distinct_real_contracts_not_summary_renames():
    for attribute in ("quality_contract_id",):
        assert len({getattr(adapter, attribute) for adapter in ALL_ADAPTERS}) == 22
    assert len({adapter.structure_contract.contract_id for adapter in ALL_ADAPTERS}) == 22
    assert len({adapter.retrieval_profile.profile_id for adapter in ALL_ADAPTERS}) == 22
    assert len({adapter.drafting_rules.ruleset_id for adapter in ALL_ADAPTERS}) == 22
    assert len({adapter.drafting_rules.ruleset_hash for adapter in ALL_ADAPTERS}) == 22
    for adapter in UPSTREAM_ADAPTERS:
        assert adapter.structure_contract.section_roles
        assert adapter.retrieval_profile.coverage_roles
        assert adapter.drafting_rules.provenance == "pinned_upstream"
        assert adapter.drafting_rules.reference_policy == "none"
        assert adapter.quality_evaluator is not WORK_SUMMARY_ADAPTER.quality_evaluator
    for names in (("请示", "通知", "报告", "纪要"), ("工作方案", "讲话稿", "工作总结")):
        contracts = [registry.resolve_genre(name).structure_contract.section_roles for name in names]
        assert len(set(contracts)) == len(names), names


@pytest.mark.parametrize("adapter", UPSTREAM_ADAPTERS, ids=lambda a: a.genre_id)
def test_each_genre_prompt_consumes_own_upstream_rules_and_trusted_material_path(adapter, summary_packet):
    packet = with_genre(summary_packet, adapter.document_type)
    before = packet.registry_json
    prompt = build_full_draft_prompt(packet)
    own_rules = load_official_drafting_rules(adapter)
    assert own_rules in prompt["system"]
    assert prompt["ruleset_id"] == adapter.drafting_rules.ruleset_id
    assert prompt["ruleset_hash"] == adapter.drafting_rules.ruleset_hash
    assert "<!-- PINNED_UPSTREAM_ORIGINAL_BEGIN -->" in prompt["system"]
    assert "<!-- PINNED_HOST_GENRE_RULES_BEGIN -->" not in prompt["system"]
    assert load_official_drafting_rules(WORK_SUMMARY_ADAPTER) not in prompt["system"]
    assert packet.materials_markdown() in prompt["user"]
    assert packet.task_json in prompt["user"]
    assert "S02主写法参考" not in prompt["user"]
    assert packet.registry_json == before
    for boundary in ("事实唯一来源", "UNKNOWN 不授权", "scope证据隔离", "必须交付完整文章",
                     "不得偷换主体", "不授权新增更强结果"):
        assert boundary in prompt["system"]


@pytest.mark.parametrize("adapter", UPSTREAM_ADAPTERS, ids=lambda a: a.genre_id)
@pytest.mark.parametrize("missing", ("planning", "evidence_organization", "writer_control", "writer_calls", "recovery", "delivery"))
def test_incomplete_adapter_never_obtains_a_commercial_contract(adapter, missing):
    incomplete = replace(adapter, commercial=replace(adapter.commercial, **{missing: None}))
    with pytest.raises(CommercialAdmissionError, match="COMMERCIAL_CAPABILITY_REQUIRED"):
        validate_commercial_contract(incomplete)


@pytest.mark.parametrize("adapter", ALL_ADAPTERS, ids=lambda a: a.genre_id)
def test_tampering_with_any_adopted_prompt_fails_closed(adapter, monkeypatch):
    from pathlib import Path
    original = Path.read_bytes
    target = adapter.drafting_rules.root / adapter.drafting_rules.prompt_path

    def corrupted(path):
        data = original(path)
        return data + b"\nchanged" if path == target else data

    monkeypatch.setattr(Path, "read_bytes", corrupted)
    with pytest.raises(FullArticleContractError, match="OFFICIAL_DRAFTING_RULESET_HASH_MISMATCH"):
        load_official_drafting_rules(adapter)


def test_foundation_report_is_the_same_upstream_presentation_not_a_second_truth():
    from app.services.generation.genres.report import REPORT_ADAPTER
    from app.services.generation.genres.report_retrieval import REPORT_RETRIEVAL_PROFILE
    presentation = registry.resolve_genre("汇报材料")
    assert REPORT_ADAPTER is presentation
    assert REPORT_RETRIEVAL_PROFILE is presentation.retrieval_profile
    assert registry.resolve_genre("report") is presentation
    statutory = registry.resolve_genre("报告")
    assert statutory is not presentation
    assert statutory.genre_id == "statutory_report"
    assert presentation.drafting_rules.ruleset_id.endswith("/presentation")
    assert statutory.drafting_rules.ruleset_id.endswith("/report")


@pytest.mark.parametrize("adapter", UPSTREAM_ADAPTERS, ids=lambda a: a.genre_id)
def test_genre_quality_reports_its_own_structure_and_does_not_claim_calibration(adapter, summary_packet):
    packet = with_genre(summary_packet, adapter.document_type)
    candidate = import_full_article("# 合成" + adapter.document_type + "\n\n材料仍需核实。\n",
        packet.candidate_input(), {"kind": "SYNTHETIC_GENRE_OMISSION"})
    citations = propose_exact_citations(candidate)
    facts = evaluate_full_article_facts(candidate, citations)
    quality = json.loads(evaluate_full_article_quality(candidate, citations, facts).result_json)
    assert quality["quality_contract_id"] == adapter.quality_contract_id
    assert quality["production_ready"] is False
    assert quality["quality_pass"] is False and quality["article_qualified"] is False
    assert quality["automatic_rewrite"] is False
    shared = {check["id"]: check for check in quality["checks"]}
    assert shared["fact_safety"]["status"] == json.loads(facts.result_json)["status"]
    assert shared["semantic_task_and_evidence_coverage"]["evidence"]["section_roles"] == list(adapter.structure_contract.section_roles)
    assert quality["fact_report_sha256"] == facts.result_sha256
