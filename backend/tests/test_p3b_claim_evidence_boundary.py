"""P3-B consumer contracts: discovery never authorizes a fact or changes prose."""
import copy
import hashlib
import json
import os
from pathlib import Path
import importlib

import pytest

from app.models.full_article import CitationProposal, CitationSidecar, canonical_json, text_sha
from app.services.generation.atomic_evidence import classify_semantic_state
from app.services.generation.full_article_candidate_adapter import import_full_article, propose_exact_citations
from app.services.generation import flow3_v3_trace as facts


TASK = {"user_task": {"level": "乡镇 / 街道", "org_type": "党政机关", "target_words": 1200}}


def row(source, eid="EV-TEST-1", scope="S1", subject="", action="", obj="", state=None):
    semantic = classify_semantic_state(source)
    if state:
        semantic["semantic_state"] = state
    anchor = {**semantic, "section_id": scope, "evidence_id": eid,
              "canonical_subject": subject, "canonical_action": action, "canonical_object": obj}
    return {"section_id": scope, "anchor": anchor,
            "fact": {"evidence_id": eid, "fact": source, **semantic,
                     "writability": {"status": "WRITABLE", "usage": "INDEPENDENT_OR_SUPPORTING"}},
            "source_scope": {"source_span": source, "source_span_sha256": text_sha(source),
                             "document_id": "D1", "chunk_id": eid}, "limitations": []}


def candidate(claim, rows, task=None):
    registry = {r["anchor"]["evidence_id"]: r for r in rows}
    return import_full_article("# 合成工作总结\n\n" + claim, {"registry": registry, **(task or TASK)}, {"kind": "OFFLINE_TEST"})


def evaluate(claim, rows, task=None, supplied=None):
    c = candidate(claim, rows, task)
    sidecar = propose_exact_citations(c)
    if supplied:
        sidecar = CitationSidecar(candidate_digest=c.digest, evidence_sha256=c.evidence_sha256,
            proposals=(CitationProposal(unit_id="U0002", scope_id="S1", evidence_ids=tuple(supplied), origin="SUPPLIED_FOR_REVIEW"),))
    report = facts.evaluate_full_article_facts(c, sidecar)
    return c, sidecar, report, json.loads(report.result_json)["units"][1]


SAFE_CASES = [
    ("镇里将组织多个联合检查组", "下一步，镇里将组织联合检查组开展检查。"),
    ("各村（社区）党组织书记、村（居）安全主任是本辖区安全生产的第一责任人",
     "各村（社区）党组织书记、村（居）安全主任为本辖区安全生产第一责任人。"),
    ("行动方案已经明确了总体要求、排查范围和工作步骤", "行动方案已明确总体要求、排查范围与工作步骤。"),
]


@pytest.mark.parametrize("source,claim", SAFE_CASES)
def test_safe_paraphrases_have_canonical_supported_path(source, claim):
    c, citations, report, unit = evaluate(claim, [row(source)])
    assert not citations.proposals
    assert unit["binding_origin"] == "DETERMINISTIC_CANDIDATE"
    assert unit["status"] == "SUPPORTED", unit
    assert unit["result"]["passed"] is True
    assert unit["evidence_ids"] == []  # discovered evidence is not a citation
    assert unit["candidate_evidence_ids"] == ["EV-TEST-1"]
    assert json.loads(report.result_json)["evaluator"].endswith("_evaluate_evidence_safety_fact")


def test_exact_citation_stays_exact_only():
    source = "测试部门已经完成档案整理"
    c, sidecar, _, _ = evaluate(source + "。", [row(source)])
    assert len(sidecar.proposals) == 1 and sidecar.proposals[0].origin == "EXACT_WHOLE_UNIT"
    assert not propose_exact_citations(candidate(source.replace("已经", "已") + "。", [row(source)])).proposals
    assert not propose_exact_citations(candidate(source + "。", [row(source), row(source, "EV-TEST-2")])).proposals


@pytest.mark.parametrize("source,claim,state", [
    ("镇里将组织多个联合检查组", "组织联合检查组。", "PLAN"),
    ("我们必须建立严格的闭环管理机制", "建立严格的闭环管理机制。", "RECOMMENDATION"),
    ("要常态化开展安全生产宣传五进活动", "常态化开展安全生产宣传五进活动。", "RECOMMENDATION"),
    ("测试部门正在推进档案整理", "测试部门已经完成档案整理。", "ONGOING"),
    ("测试部门部分完成档案整理", "测试部门全部完成档案整理。", "PARTIAL"),
    ("测试部门尚未完成档案整理", "测试部门已经完成档案整理。", "NOT_IMPLEMENTED"),
    ("测试部门开展档案整理", "测试部门已经完成档案整理。", "UNKNOWN"),
    ("档案管理存在明显差距", "档案管理问题已全部解决。", "GAP"),
    ("档案管理落实不到位", "档案管理问题已全部解决。", "PROBLEM"),
    ("测试部门地处城区", "测试部门已完成城区建设。", "CONTEXT"),
])
def test_state_upgrades_are_specific(source, claim, state):
    _, _, _, unit = evaluate(claim, [row(source, state=state)])
    assert unit["status"] == "REVIEW_REQUIRED"
    assert "SEMANTIC_STATE_UPGRADE" in unit["risk_codes"], unit


@pytest.mark.parametrize("source,claim", [
    ("为完成年度任务", "为圆满完成年度任务"),
    ("我们必须完成年度任务", "必须完成年度任务"),
    ("要明确整改责任人和完成时限", "要明确整改责任人及完成时限"),
])
def test_p3a_purpose_and_requirement_do_not_false_upgrade(source, claim):
    anchor = row(source)["anchor"]
    checked = facts._bound_state_check(anchor, claim, source)
    assert checked["passed"] and not checked["completion_upgrade"], checked


@pytest.mark.parametrize("source,claim,task,code", [
    ("公司上下开展业务培训", "全镇上下开展业务培训。", TASK, "SUBJECT_SCOPE_MISMATCH"),
    ("为公司产业重组成型成势创造条件", "为全镇产业重组成型成势创造条件。", TASK, "SUBJECT_SCOPE_MISMATCH"),
    ("医院已经组织业务培训", "学校已经组织业务培训。", {"user_task": {"org_type": "学校"}}, "SUBJECT_SCOPE_MISMATCH"),
    ("甲部门已经完成档案整理", "乙部门已经完成档案整理。", TASK, "SUBJECT_SCOPE_MISMATCH"),
    ("加强对各乡镇工作部署落实情况的跟踪问效", "加强对各乡镇工作部署落实情况的跟踪问效。", TASK, "JURISDICTION_SCOPE_MISMATCH"),
    ("加强对各县工作落实情况的督导", "加强对各县工作落实情况的督导。", {"user_task": {"level": "县级", "org_type": "党政机关"}}, "JURISDICTION_SCOPE_MISMATCH"),
])
def test_generic_subject_and_jurisdiction_boundaries(source, claim, task, code):
    _, _, _, unit = evaluate(claim, [row(source)], task)
    assert unit["status"] == "REVIEW_REQUIRED" and code in unit["risk_codes"], unit


@pytest.mark.parametrize("claim", [
    "专项行动取得阶段性成效。", "专项行动取得了阶段性成效。", "一批重大安全隐患完成整改销号。",
    "企业主体责任意识明显增强。", "自查自纠能力有所提升。", "未发生较大及以上生产安全事故。",
    "测试项目已经完成。", "实现年度目标。", "达到年度目标。", "业务收入上升。", "风险数量下降。", "业务收入增长。",
])
def test_result_without_citation_is_checked(claim):
    _, citations, _, unit = evaluate(claim, [row("测试部门开展资料整理")])
    assert not citations.proposals
    assert unit["status"] == "REVIEW_REQUIRED" and "UNSUPPORTED_RESULT" in unit["risk_codes"], unit


@pytest.mark.parametrize("connector", ["因此", "从而", "导致", "有力推动"])
def test_causality_without_citation_is_checked(connector):
    _, _, _, unit = evaluate("测试部门开展培训，" + connector + "业务能力提升。", [row("测试部门开展培训")])
    assert "UNSUPPORTED_CAUSALITY" in unit["risk_codes"], unit


def test_completed_source_does_not_authorize_stronger_results():
    _, _, _, unit = evaluate("测试部门已完成培训，业务能力显著提升。", [row("测试部门已经完成培训")])
    assert unit["status"] == "REVIEW_REQUIRED" and "UNSUPPORTED_RESULT" in unit["risk_codes"]


def test_candidate_match_is_not_automatic_support(monkeypatch):
    actual = facts._evaluate_evidence_safety_fact
    calls = []
    def veto(*args, **kwargs):
        calls.append(True)
        return {**actual(*args, **kwargs), "passed": False, "reason": "CANONICAL_TEST_VETO"}
    monkeypatch.setattr(facts, "_evaluate_evidence_safety_fact", veto)
    _, _, _, unit = evaluate(SAFE_CASES[0][1], [row(SAFE_CASES[0][0])])
    assert calls and unit["status"] == "REVIEW_REQUIRED"


def test_ambiguous_candidates_fail_closed():
    source, claim = SAFE_CASES[2]
    _, _, _, unit = evaluate(claim, [row(source), row(source, "EV-TEST-2", "S2")])
    assert len(unit["candidate_evidence_ids"]) == 2
    assert unit["status"] == "REVIEW_REQUIRED" and "AMBIGUOUS_EVIDENCE" in unit["risk_codes"]


@pytest.mark.parametrize("part", ["record", "anchor", "fact", "source_scope"])
def test_limitations_and_conflicts_stay_review(part):
    r = row(SAFE_CASES[2][0])
    target = r if part == "record" else r[part]
    target["limitations"] = [{"kind": "SOURCE_STATE_CONFLICT", "requires_review": True}]
    _, _, _, unit = evaluate(SAFE_CASES[2][1], [r])
    assert unit["status"] == "REVIEW_REQUIRED"


def test_declared_multi_evidence_requires_every_member():
    first = row("甲部门已经完成档案整理", subject="甲部门", action="完成", obj="档案整理")
    second = row("乙部门已经完成设备检修", "EV-TEST-2", subject="乙部门", action="完成", obj="设备检修")
    _, _, _, unit = evaluate(first["fact"]["fact"] + "。", [first, second], supplied=["EV-TEST-1", "EV-TEST-2"])
    assert unit["status"] == "REVIEW_REQUIRED"
    assert unit["result"]["reason"] == "declared_evidence_not_materialized"


def test_declared_multi_evidence_materializes_each_member_and_rejects_cross_scope():
    first = row("甲部门已经完成档案整理", subject="甲部门", action="完成", obj="档案整理")
    second = row("乙部门已经完成设备检修", "EV-TEST-2", subject="乙部门", action="完成", obj="设备检修")
    text = first["fact"]["fact"] + "；" + second["fact"]["fact"] + "。"
    _, _, _, unit = evaluate(text, [first, second], supplied=["EV-TEST-1", "EV-TEST-2"])
    assert unit["status"] == "SUPPORTED", unit
    second["section_id"] = second["anchor"]["section_id"] = "S2"
    _, _, _, unit = evaluate(text, [first, second], supplied=["EV-TEST-1", "EV-TEST-2"])
    assert unit["status"] == "REVIEW_REQUIRED" and unit["result"]["reason"] == "section_mismatch"


def test_materialized_members_do_not_authorize_an_extra_predicate():
    first = row("甲部门已经完成档案整理", subject="甲部门", action="完成", obj="档案整理")
    second = row("乙部门已经完成设备检修", "EV-TEST-2", subject="乙部门", action="完成", obj="设备检修")
    text = first["fact"]["fact"] + "；" + second["fact"]["fact"] + "；群众积极参加志愿服务。"
    _, _, _, unit = evaluate(text, [first, second], supplied=["EV-TEST-1", "EV-TEST-2"])
    assert unit["status"] == "REVIEW_REQUIRED" and unit["result"]["reason"] == "undeclared_claim_not_supported"


@pytest.mark.parametrize("source,state,subject,action,obj", [
    ("测试部门已经完成档案整理", "COMPLETED", "测试部门", "完成", "档案整理"),
    ("企业主体责任意识明显增强", "RESULT", "企业", "增强", "主体责任意识"),
])
def test_legal_action_and_result_evidence_can_support(source, state, subject, action, obj):
    _, _, _, unit = evaluate(source + "。", [row(source, state=state, subject=subject, action=action, obj=obj)])
    assert unit["status"] == "SUPPORTED", unit


@pytest.mark.parametrize("claim", [
    "镇里将组织联合检查组，已经完成隐患整改。",
    "镇里将组织联合检查组，从而使工作效率显著提升。",
    "镇里将组织联合检查组，企业已完成整改。",
])
def test_modal_prefix_does_not_launder_later_actual_result(claim):
    _, _, _, unit = evaluate(claim, [row(SAFE_CASES[0][0])])
    assert unit["status"] == "REVIEW_REQUIRED" and "UNSUPPORTED_RESULT" in unit["risk_codes"]


@pytest.mark.parametrize("claim", [
    "下一步，镇里将组织20个联合检查组开展检查。",
    "下一步，镇里将组织联合检查组完成检查。",
    "下一步，全市将组织联合检查组开展检查。",
    "下一步，镇里将组织联合检查组开展执法检查。",
])
def test_related_paraphrase_does_not_authorize_new_details(claim):
    _, _, _, unit = evaluate(claim, [row(SAFE_CASES[0][0])])
    assert unit["candidate_evidence_ids"] and unit["status"] == "REVIEW_REQUIRED"


def test_metadata_cannot_be_silently_reassigned_to_task_actor():
    source = "已经完成档案整理"
    r = row(source, action="完成", obj="档案整理")
    r["input_constraints"] = {"metadata": {"org_type": "企业", "level": "市级"}}
    _, _, _, unit = evaluate(source + "。", [r])
    assert {"SUBJECT_SCOPE_MISMATCH", "JURISDICTION_SCOPE_MISMATCH"} <= set(unit["risk_codes"])


def test_organization_object_is_not_mistaken_for_local_actor():
    source = "我镇已经完成企业检查"
    _, _, _, unit = evaluate(source + "。", [row(source, subject="我镇", action="完成", obj="企业检查")])
    assert unit["status"] == "SUPPORTED", unit


def test_conflicting_scope_metadata_never_selects_one_side():
    source = SAFE_CASES[2][0]
    r = row(source)
    r["input_constraints"] = {"metadata": {"org_type": "企业"}, "source": {"org_type": "党政机关"}}
    _, _, _, unit = evaluate(SAFE_CASES[2][1], [r])
    assert "SUBJECT_SCOPE_UNCERTAIN" in unit["risk_codes"] and unit["status"] == "REVIEW_REQUIRED"


def test_unknown_source_cannot_authorize_explicit_completion_even_if_verbatim():
    source = "测试部门已经完成档案整理"
    _, _, _, unit = evaluate(source + "。", [row(source, state="UNKNOWN", subject="测试部门", action="完成", obj="档案整理")])
    assert "SEMANTIC_STATE_UPGRADE" in unit["risk_codes"] and unit["status"] == "REVIEW_REQUIRED"


def test_uncertain_deictic_source_stays_review():
    _, _, _, unit = evaluate("我镇已完成档案整理。", [row("我们已完成档案整理")])
    assert "SUBJECT_SCOPE_UNCERTAIN" in unit["risk_codes"]


def test_supported_paraphrase_survives_handoff_and_forged_receipt_does_not():
    from app.models.full_article import FullArticleContractError, bind_report
    from app.services.generation.work_summary_quality_gate import evaluate_full_article_quality
    from app.services.flow4.handoff import build_full_article_handoff
    from app.services.flow4.renderer import render_full_article
    from app.services.generation.delivery_decision import full_article_review_result
    c, sidecar, report, unit = evaluate(SAFE_CASES[0][1], [row(SAFE_CASES[0][0])])
    quality = evaluate_full_article_quality(c, sidecar, report)
    rendered = render_full_article(build_full_article_handoff(c, sidecar, report, quality))
    assert full_article_review_result(rendered).content == c.content
    value = json.loads(report.result_json)
    value["units"][1]["candidate_evidence_ids"] = ["EV-FORGED"]
    forged = bind_report("FACT", c, sidecar, value)
    with pytest.raises(FullArticleContractError):
        evaluate_full_article_quality(c, sidecar, forged)


def test_safe_purpose_keeps_consumer_pass_but_actual_completion_does_not():
    source = "为圆满完成全年工作目标"
    anchor = row(source)["anchor"]
    assert facts._bound_state_check(anchor, "为完成全年工作目标", source)["passed"]
    assert not facts._bound_state_check(anchor, "已圆满完成全年工作目标", source)["passed"]


@pytest.mark.parametrize("module_name,test_name,path", [
    ("test_production_task_assembly", "test_protected_owners_unchanged_from_task_base", "backend/app/services/flow4/integrity_gate.py"),
    ("test_material_ingestion", "test_protected_generation_retrieval_and_legacy_owners_unchanged", "backend/app/services/generation/flow3_v3_trace.py"),
    ("test_stable_material_assets", "test_production_and_fact_owners_remain_unchanged", "backend/app/services/generation/official_document_drafting.py"),
    ("test_material_digest_task_assembly", "test_production_files_unchanged_and_no_experiment_imports", "backend/app/services/generation/official_document_drafting.py"),
])
def test_historical_guards_still_reject_future_p3b_edits(monkeypatch, module_name, test_name, path):
    root = Path(__file__).resolve().parents[2]
    original = Path.read_bytes
    def changed(p):
        data = original(p)
        return data + b"\n" if p == root / path else data
    monkeypatch.setattr(Path, "read_bytes", changed)
    with pytest.raises(AssertionError, match="differs from exact P3-B scope content"):
        getattr(importlib.import_module(module_name), test_name)()


def test_p3b_touches_only_scoped_production_functions():
    import ast
    import subprocess
    from p3b_boundary_scope import P3B_CONTENT_SHA256, assert_p3b_content
    root = Path(__file__).resolve().parents[2]
    allowed = {
        "flow3_v3_trace.py": {"_bound_state_check", "_hard_value_supported", "_result_claim_signatures", "_safe_surface_equivalent",
                              "_consumer_surface", "_subject_scope_findings", "_claim_boundary_findings",
                              "evaluate_full_article_facts", "_evaluate_evidence_safety_fact"},
        "full_article_candidate_adapter.py": {"discover_safety_candidates"},
        "official_document_drafting.py": {"_case32_user_prompt"},
        "delivery_decision.py": {"full_article_review_result"},
        "integrity_gate.py": {"validate_full_article_handoff"},
    }
    def functions(source):
        return {node.name: ast.dump(node, include_attributes=False) for node in ast.parse(source).body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
    for path in P3B_CONTENT_SHA256:
        assert_p3b_content(root, path)
        old = functions(subprocess.check_output(["git", "show", "cdf06f48f390925d3bbe2d3c9d13d67935f6b903:" + path], cwd=root))
        new = functions((root / path).read_text(encoding="utf-8"))
        assert {name for name in old.keys() | new.keys() if old.get(name) != new.get(name)} <= allowed[Path(path).name]


def test_risky_complete_article_preserves_delivery_identity():
    from app.services.generation.work_summary_quality_gate import evaluate_full_article_quality
    from app.services.flow4.handoff import build_full_article_handoff
    from app.services.flow4.renderer import render_full_article
    from app.services.generation.delivery_decision import full_article_review_result
    body = "组织联合检查组。\n\n## 工作成效\n\n专项行动取得阶段性成效。\n\n## 下一步安排\n\n继续开展资料核对。\n"
    c, sidecar, report, _ = evaluate(body, [row("镇里将组织多个联合检查组")])
    quality = evaluate_full_article_quality(c, sidecar, report)
    result = full_article_review_result(render_full_article(build_full_article_handoff(c, sidecar, report, quality)), None)
    assert result.article_exists and result.article_recoverable
    assert result.delivery_status == "SUCCESS_WITH_REVIEW"
    assert not result.official_success and not result.article_qualified and result.user_confirmation_required
    assert result.content == c.content and result.risk_items
    assert "SEMANTIC_STATE_UPGRADE" not in result.content and "[待核实]" not in result.content
    from app.models.full_article import article_units
    assert len(json.loads(report.result_json)["units"]) == len(article_units(c.content))


def test_writer_prompt_boundaries_and_no_placeholder_template():
    from app.services.generation.official_document_drafting import HOST_BOUNDARY, _case32_user_prompt
    for marker in ["RECOMMENDATION", "ONGOING", "PARTIAL", "NOT_IMPLEMENTED", "主体", "上级", "因果", "结果证据", "正文之外"]:
        assert marker in HOST_BOUNDARY
    assert "UNKNOWN 不授权补成已完成或结果态" in HOST_BOUNDARY
    assert "也不抹去原文已明确表达的结果" not in HOST_BOUNDARY
    prompt = _case32_user_prompt("任务", "材料")
    assert "更适合提纲" not in prompt and "保留占位符" not in prompt


@pytest.mark.skipif(not os.environ.get("P3B_REAL_RUN_DIR"), reason="explicit read-only Run replay required")
def test_real_run_replay(tmp_path):
    from app.models.full_article import FullArticleCandidate
    run = Path(os.environ["P3B_REAL_RUN_DIR"])
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in run.rglob("*") if p.is_file()}
    value = json.loads((run / "candidate.json").read_text(encoding="utf-8"))
    registry = json.loads(value["evidence_json"])
    # Read-only consumer replay: reclassify source spans with CURRENT P3-A owner.
    for r in registry.values():
        semantic = classify_semantic_state(r["source_scope"]["source_span"])
        r["anchor"].update(semantic)
        r["fact"].update(semantic)
        r["fact"]["semantic_constraints"] = semantic
    value["evidence_json"] = canonical_json(registry)
    c = FullArticleCandidate.model_validate(value)
    sidecar = propose_exact_citations(c)
    report = facts.evaluate_full_article_facts(c, sidecar)
    rows = json.loads(report.result_json)["units"]
    known = [("组织联合检查组", "SEMANTIC_STATE_UPGRADE"), ("建立严格的闭环管理机制", "SEMANTIC_STATE_UPGRADE"),
             ("常态化开展安全生产宣传", "SEMANTIC_STATE_UPGRADE"), ("加强对各乡镇", "JURISDICTION_SCOPE_MISMATCH"),
             ("动员全镇上下", "SUBJECT_SCOPE_MISMATCH"), ("取得了阶段性成效", "UNSUPPORTED_RESULT"),
             ("重大安全隐患完成整改销号", "UNSUPPORTED_RESULT"), ("主体责任意识明显增强", "UNSUPPORTED_RESULT"),
             ("自查自纠能力有所提升", "UNSUPPORTED_RESULT"), ("未发生较大及以上生产安全事故", "UNSUPPORTED_RESULT")]
    findings = [{"claim": claim, "expected": code, "passed": any(claim in u["observed_text"] and any(
                    f["code"] == code and claim in f["claim"] for f in u["result"].get("boundary_findings", [])) for u in rows)}
                for claim, code in known]
    assert any("为全镇产业重组成型成势" in f["claim"] and f["code"] == "SUBJECT_SCOPE_MISMATCH"
               for u in rows for f in u["result"].get("boundary_findings", []))
    safe_real = []
    for source, claim in SAFE_CASES:
        matches = [r for r in registry.values() if facts._consumer_surface(r["source_scope"]["source_span"]) == facts._consumer_surface(source)]
        assert matches
        # Duplicate source IDs in the full Run deliberately stay ambiguous;
        # each single authorized record must still admit its safe paraphrase.
        safe_real.append(evaluate(claim, [matches[0]])[3]["status"])
    assert safe_real == ["SUPPORTED"] * 3
    full_registry_safe = [evaluate(claim, list(registry.values()))[3]["status"] for _, claim in SAFE_CASES]
    assert full_registry_safe.count("SUPPORTED") > 0
    from app.services.generation.work_summary_quality_gate import evaluate_full_article_quality
    from app.services.flow4.handoff import build_full_article_handoff
    from app.services.flow4.renderer import render_full_article
    from app.services.generation.delivery_decision import full_article_review_result
    quality = evaluate_full_article_quality(c, sidecar, report)
    delivered = full_article_review_result(render_full_article(build_full_article_handoff(c, sidecar, report, quality)))
    assert delivered.content.encode("utf-8") == (run / "RAW_WRITER_ARTICLE.md").read_bytes()
    assert delivered.content.encode("utf-8") == (run / "FINAL_DELIVERED_ARTICLE.md").read_bytes()
    assert delivered.delivery_status == "SUCCESS_WITH_REVIEW"
    assert delivered.article_exists and delivered.article_recoverable
    assert not delivered.official_success and not delivered.article_qualified and delivered.user_confirmation_required
    assert {code for _, code in known} <= {code for risk in delivered.risk_items for code in risk.get("risk_codes", [])}
    assert all(marker not in delivered.content for marker in ["SEMANTIC_STATE_UPGRADE", "UNSUPPORTED_RESULT", "[待核实]", "EV-"])
    before_report = json.loads(json.loads((run / "fact_report.json").read_text(encoding="utf-8"))["result_json"])
    output = {"known": findings, "specifically_classified": sum(x["passed"] for x in findings),
              "generic_unbound_remaining": sum(u["result"]["reason"] == "UNBOUND_OR_UNCLASSIFIED" for u in rows),
              "unit_count": len(rows), "safe_paraphrase_supported_count": safe_real.count("SUPPORTED"),
              "full_registry_safe_paraphrase_supported_count": full_registry_safe.count("SUPPORTED"),
              "baseline_unit_count": len(before_report["units"]),
              "baseline_generic_unbound": sum(u["result"]["reason"] == "UNBOUND_OR_UNCLASSIFIED" for u in before_report["units"]),
              "delivery_status": delivered.delivery_status, "body_sha256": text_sha(delivered.content),
              "body_preserved": True, "risk_item_count": len(delivered.risk_items),
              "report": json.loads(report.result_json)}
    (tmp_path / "replay.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    assert output["specifically_classified"] == 10, findings
    assert c.content == (run / "RAW_WRITER_ARTICLE.md").read_text(encoding="utf-8")
    assert before == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in run.rglob("*") if p.is_file()}
