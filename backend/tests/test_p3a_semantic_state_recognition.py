"""P3-A: local predicate recognition and unchanged downstream propagation."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import types

import pytest

from app.models.full_article import canonical_json, text_sha
from app.models.generation import ArticleGenRequest
from app.services.generation import atomic_evidence as atomic
from app.services.generation.full_draft_material import FullDraftMaterialPacket
from app.services.generation.generator import build_official_draft_task
from app.services.generation.material_digest import load_material_digest
from app.services.generation.task_assembly import _assemble_reading_brief
from app.services.generation.writer_material_view import build_writer_material_view
from writer_material_view_decoder import decode_materials
from test_material_digest_task_assembly import no_models


ROOT = Path(__file__).resolve().parents[2]
BASE_SHA = "72af33cf4e6283c04c18ab87a30e2b506471feaa"
OWNER = "backend/app/services/generation/atomic_evidence.py"
CASES = [
    ("镇里将组织多个联合检查组", "PLAN", "PLAN"),
    ("我们必须建立严格的闭环管理机制", "UNKNOWN", "RECOMMENDATION"),
    ("要常态化开展安全生产宣传“五进”活动", "UNKNOWN", "RECOMMENDATION"),
    ("要严格落实有关要求", "UNKNOWN", "RECOMMENDATION"),
    ("责任清单台账要明确整改责任人和完成时限", "COMPLETED", "RECOMMENDATION"),
    ("要形成齐抓共管的强大合力", "COMPLETED", "RECOMMENDATION"),
    ("为圆满完成全年安全生产目标", "COMPLETED", "PLAN"),
]


@pytest.fixture(scope="module")
def p3a_baseline():
    source = subprocess.check_output(["git", "show", f"{BASE_SHA}:{OWNER}"], cwd=ROOT)
    module = types.ModuleType("p3a_unmodified_baseline")
    exec(compile(source, BASE_SHA + "/atomic_evidence.py", "exec"), module.__dict__)
    return module


def assert_state(text, expected, *, evidence_type=""):
    value = atomic.classify_semantic_state(text, evidence_type=evidence_type)
    assert value["semantic_state"] == expected
    compact = atomic._compact(text)
    assert all(marker in compact for name in ("state_evidence", "modality_markers", "completion_markers")
               for marker in value[name])
    assert value["state_qualifier"] == (value["state_evidence"][0] if value["state_evidence"] else "")
    if expected in {"PLAN", "RECOMMENDATION"}:
        assert value["state_evidence"] and value["modality_markers"]
        assert set(value["state_evidence"]) <= set(value["modality_markers"])
        assert not value["completion_markers"]
    return value


@pytest.mark.parametrize("text,old,new", CASES)
@pytest.mark.parametrize("evidence_type", ["", "result"])
def test_real_counterexample_contract(p3a_baseline, text, old, new, evidence_type):
    assert p3a_baseline.classify_semantic_state(text)["semantic_state"] == old
    assert_state(text, new, evidence_type=evidence_type)


@pytest.mark.parametrize("text", [
    "要建立协作机制", "要形成工作合力", "要落实有关要求", "要开展业务培训",
    "必须建立协作机制", "必须落实有关要求", "应当落实有关要求", "需要进一步完善管理制度",
    "要完成整改", "我们必须圆满完成年度任务", "各部门要严格落实有关要求",
])
def test_requirements_are_predicate_modality(text):
    assert_state(text, "RECOMMENDATION", evidence_type="result")


@pytest.mark.parametrize("text", ["为完成年度任务", "为圆满完成年度目标", "以实现年度目标", "为了顺利完成年度任务", "测试部门为完成年度目标"])
def test_purpose_is_plan_even_with_result_type(text):
    assert_state(text, "PLAN", evidence_type="result")


@pytest.mark.parametrize("text", [
    "已完成年度任务", "已圆满完成年度任务", "主要工作已完成", "已形成协作机制",
    "已建立闭环机制", "已形成齐抓共管的工作机制", "完成了整改任务", "已完成整改",
    "主要任务已经顺利完成", "重要工作已完成",
])
def test_true_completion_is_preserved(text):
    value = assert_state(text, "COMPLETED")
    assert value["completion_markers"] and not value["modality_markers"]


@pytest.mark.parametrize("text", ["完成时限", "完成标准", "完成要求", "明确完成时限", "明确完成标准", "明确完成要求", "主要工作", "重要任务"])
def test_nouns_are_not_completion_or_requirement(text):
    value = assert_state(text, "UNKNOWN")
    assert not value["completion_markers"] and not value["modality_markers"]


@pytest.mark.parametrize("text,state,evidence_type", [
    ("正在推进档案整理", "ONGOING", ""), ("部分完成档案整理", "PARTIAL", ""),
    ("落实不到位", "PROBLEM", ""), ("尚未完成档案整理", "NOT_IMPLEMENTED", ""),
    ("与目标相比存在明显差距", "GAP", ""), ("取得明显成效", "RESULT", ""),
    ("如条件具备时开展培训", "CONDITIONAL", ""), ("地处城区", "CONTEXT", "context"),
    ("开展业务培训", "UNKNOWN", ""), ("开展业务培训", "RESULT", "result"),
])
def test_existing_states_and_unknown_fallback(text, state, evidence_type):
    assert_state(text, state, evidence_type=evidence_type)


@pytest.mark.parametrize("text,verb,subject,prefix", [
    ("我们必须建立严格的闭环管理机制", "建立", "我们", "必须"),
    ("要常态化开展业务培训", "开展", "", "要常态化"),
    ("要严格落实有关要求", "严格落实", "", "要"),
    ("镇里将组织多个联合检查组", "组织", "镇里", "将"),
    ("部门已完成整改", "完成", "部门", "已"),
    ("部门已圆满完成整改", "完成", "部门", "已圆满"),
    ("部门正在推进整改", "推进", "部门", "正在"),
    ("部门尚未完成整改", "完成", "部门", "尚未"),
])
def test_predicate_boundary_and_extracted_subject(text, verb, subject, prefix):
    start, actual_prefix = atomic._predicate_start(text, text.index(verb))
    assert text[:start] == subject and actual_prefix == prefix
    atoms, _ = atomic._atoms_from_clause(text, document_id="synthetic", chunk_id="c1", document_role="primary")
    assert atoms
    assert all(row["subject"] == (subject or "相关责任主体") for row in atoms)
    assert all(row["action"].startswith(prefix) for row in atoms)


@pytest.mark.parametrize("connector", ["、", "并", "并且", "同时", "随后"])
def test_coordinated_plan_does_not_erase_explicit_completed_predicate(connector):
    text = f"计划开展培训{connector}已完成档案登记"
    value = atomic.classify_semantic_state(text)
    assert value["semantic_state"] == "PLAN"
    assert value["completion_markers"] == ["已完成"]


@pytest.mark.parametrize("noun", ["提要", "概要", "纪要", "摘要", "纲要", "主要", "重要"])
@pytest.mark.parametrize("prefix", ["", "工作", "各部门工作"])
def test_review_yao_inside_nominal_words_is_not_a_modal_boundary(noun, prefix):
    text = prefix + noun + "形成后报审"
    value = atomic.classify_semantic_state(text)
    assert value["semantic_state"] != "RECOMMENDATION"
    assert not value["modality_markers"] and "要" not in value["state_evidence"]
    start, state_prefix = atomic._predicate_start(text, text.index("形成"))
    assert text[:start] == prefix + noun and state_prefix == ""


@pytest.mark.parametrize("text,state", [
    ("纪要形成后印发", "UNKNOWN"),
    ("主要工作已完成", "COMPLETED"),
    ("各部门要形成工作合力", "RECOMMENDATION"),
    ("我们必须建立闭环机制", "RECOMMENDATION"),
    ("我们要形成工作合力", "RECOMMENDATION"),
    ("要形成工作合力", "RECOMMENDATION"),
    ("各部门，要形成工作合力", "RECOMMENDATION"),
    ("已完成年度任务", "COMPLETED"),
    ("完成了整改任务", "COMPLETED"),
    ("已形成工作机制", "COMPLETED"),
])
def test_review_positive_predicate_boundaries(text, state):
    assert_state(text, state)


@pytest.mark.parametrize("text", [
    "汇报项目完成情况", "项目完成情况总体较好", "围绕年度目标完成情况开展分析",
    "项目完成率达到90%", "汇报机制形成过程", "围绕机制形成过程开展分析",
    "汇报任务完成程度", "评估任务完成进度", "分析机制形成条件",
])
@pytest.mark.parametrize("evidence_type", ["", "result"])
def test_review_nominalized_actions_do_not_assert_completion(text, evidence_type):
    value = assert_state(text, "RESULT" if evidence_type == "result" else "UNKNOWN", evidence_type=evidence_type)
    assert not value["completion_markers"] and not value["state_evidence"]


@pytest.mark.parametrize("text", ["完成年度任务", "形成工作机制", "基本完成任务", "达到90%"])
def test_unmarked_actions_require_evidence_instead_of_guessing_completed_aspect(text):
    assert_state(text, "UNKNOWN")
    assert_state(text, "RESULT", evidence_type="result")


@pytest.mark.parametrize("text,marker", [
    ("计划开展培训并完成了档案登记", "完成了"),
    ("计划开展培训并已圆满完成档案登记", "已圆满完成"),
])
def test_completion_aspect_survives_its_own_coordinated_clause(text, marker):
    value = atomic.classify_semantic_state(text)
    assert value["semantic_state"] == "PLAN"
    assert value["completion_markers"] == [marker]


@pytest.mark.parametrize("source", ["要形成协作机制", "必须建立闭环机制", "为圆满完成年度目标", "镇里将组织检查"])
def test_existing_fact_safety_contract_still_rejects_completion_upgrade(source):
    anchor = atomic.classify_semantic_state(source)
    result = atomic.check_semantic_state_consistency(anchor, "已圆满完成年度任务")
    assert result["contract_applies"] and not result["passed"]
    assert result["candidate_state"] == "COMPLETED"


def test_exact_approval_preserves_p2_history_and_rejects_unapproved_bytes(p3a_baseline, tmp_path):
    from p2_admission_approval import P2_ATOMIC_EVIDENCE_SHA256
    from p3a_semantic_approval import assert_p3a_atomic_evidence, historical_atomic_with_p3a_recognition
    old = subprocess.check_output(["git", "show", f"{BASE_SHA}:{OWNER}"], cwd=ROOT)
    assert hashlib.sha256(old).hexdigest() == P2_ATOMIC_EVIDENCE_SHA256
    current = (ROOT / OWNER).read_bytes().replace(b"\r\n", b"\n")
    assert historical_atomic_with_p3a_recognition(old) == current
    assert_p3a_atomic_evidence(ROOT)
    copied = tmp_path / OWNER
    copied.parent.mkdir(parents=True)
    copied.write_bytes(current + b"\n")
    with pytest.raises(AssertionError, match="exact P3-A-approved content"):
        assert_p3a_atomic_evidence(tmp_path)
    # An edit outside the approved recognition regions is never erased by the
    # historical adapter; current content approval must still reject it.
    assert historical_atomic_with_p3a_recognition(old + b"\n") != current


@pytest.mark.parametrize("text,old,state", CASES)
def test_source_atomic_registry_packet_writer_view_roundtrip(text, old, state, monkeypatch, tmp_path):
    # These propagation fixtures supply an explicit synthetic subject. The
    # original subjectless counterexamples are tested separately below; never
    # invent a subject in production to force admission.
    if text.startswith(("要", "为")):
        text = "测试部门" + text
    request = ArticleGenRequest(title="综合业务工作安排", document_type="工作总结", target_words=1200)
    retrieval = {"retrieval_status": "SUFFICIENT", "primary_document_id": "synthetic",
                 "auxiliary_document_ids": [], "selected_chunks": [
                     {"document_id": "synthetic", "chunk_id": "c1", "text": text}]}
    before = copy.deepcopy(retrieval)
    anchors = atomic.build_full_draft_anchor_input(build_official_draft_task(request), retrieval)
    atoms = {row["evidence_id"]: row for row in anchors["atomic_evidence"]["evidence"]}
    assert atoms and all(row["semantic_state"] == state for row in atoms.values())
    digest = load_material_digest(retrieval, tmp_path).digest

    # Every downstream stage must copy the source owner output; reclassification
    # is an error, even if it happened to produce the same enum in this fixture.
    def forbid(*args, **kwargs):
        pytest.fail("downstream reclassified source state")
    monkeypatch.setattr(atomic, "classify_semantic_state", forbid)
    packet = FullDraftMaterialPacket.from_authorized_input(request.model_dump(), anchors)
    registry = json.loads(packet.registry_json)
    view = packet.evidence_writing_view()
    for row in view:
        eid = row["evidence_id"]
        expected = atomic.semantic_state_payload(atoms[eid])
        assert registry[eid]["input_constraints"]["atomic"] == atoms[eid]
        assert atomic.semantic_state_payload(registry[eid]["anchor"]) == expected
        assert registry[eid]["fact"]["semantic_constraints"] == expected
        assert row["semantic_constraints"] == expected
        assert row["semantic_state"] == registry[eid]["fact"]["semantic_state"] == state
        assert registry[eid]["source_scope"]["source_span"] == text
    brief = _assemble_reading_brief(packet, digest)
    packet.with_brief(brief)
    projection = build_writer_material_view(view, brief)
    decoded = decode_materials("## 写作材料\n\n```json\n" + canonical_json(projection) + "\n```")
    assert decoded == (view, brief)
    assert retrieval == before


@pytest.mark.parametrize("text", [CASES[i][0] for i in (2, 3, 5, 6)])
def test_subjectless_source_remains_unavailable_without_losing_new_state(text):
    expected = atomic.classify_semantic_state(text)
    request = ArticleGenRequest(title="综合业务工作安排", document_type="工作总结")
    retrieval = {"retrieval_status": "SUFFICIENT", "primary_document_id": "synthetic",
                 "auxiliary_document_ids": [], "selected_chunks": [
                     {"document_id": "synthetic", "chunk_id": "c1", "text": text},
                     {"document_id": "synthetic", "chunk_id": "c2", "text": "测试部门开展资料核查"}]}
    anchors = atomic.build_full_draft_anchor_input(build_official_draft_task(request), retrieval)
    unavailable = [row for row in anchors["unavailable"] if row["atomic_evidence"]["chunk_id"] == "c1"]
    assert unavailable
    for row in unavailable:
        assert row["reason"] == "NOT_FACT_ANCHORABLE"
        atom = row["atomic_evidence"]
        assert atom["subject"] == "相关责任主体"
        assert atomic.semantic_state_payload(atom) == expected


def test_real_run_offline_replay(p3a_baseline):
    run_dir = os.environ.get("P3A_REAL_RUN_DIR")
    if not run_dir:
        pytest.skip("Use run_p3a_offline.py --real-run for the explicitly authorized private Run")
    root = Path(run_dir)
    before = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in root.rglob("*") if p.is_file()}
    packet = json.loads((root / "material_packet.json").read_text(encoding="utf-8"))
    registry = json.loads(packet["registry_json"])
    # IDs pin the seven actual stored spans, including their original spacing.
    ids = ["EV-64C8D756547C", "EV-5BF16B617858", "EV-07A1DDDE03F4", "EV-7CEDF40341ED",
           "EV-14614BDFF31D", "EV-4E55000C7B67", "EV-745B9167802E"]
    for index, (eid, (_, old, new)) in enumerate(zip(ids, CASES), 1):
        row = registry[eid]
        source = row["source_scope"]
        span = source["source_span"]
        assert text_sha(span) == source["source_span_sha256"]
        assert row["fact"]["semantic_state"] == old
        assert p3a_baseline.classify_semantic_state(span)["semantic_state"] == old
        assert_state(span, new)
        assert_state(span, new, evidence_type="result")
        print(f"REAL_CASE_{index} = {old} -> {new}; evidence_id={eid}; span_sha256={source['source_span_sha256']}")
    after = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in root.rglob("*") if p.is_file()}
    assert after == before
    print("REAL_RUN_ARTIFACTS_UNCHANGED = YES")
