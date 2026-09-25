"""P2: synthetic facts through real Atomic/Registry/Assembly; no Writer calls."""
import copy
from importlib import import_module
import json
from pathlib import Path
import re
import subprocess
import types

import pytest

from p3a_semantic_approval import historical_atomic_with_p3a_recognition

from p3a_semantic_approval import assert_p3a_atomic_evidence

from app.models.full_article import FullArticleContractError, canonical_json, text_sha
from app.models.generation import ArticleGenRequest
from app.services.generation import atomic_evidence as atomic
from app.services.generation import stable_material_assets, task_assembly
from app.services.generation.material_digest import load_material_digest
from app.services.generation.full_draft_material import FullDraftMaterialPacket, validate_material_brief
from app.services.generation.generator import build_official_draft_task
from test_material_digest_task_assembly import no_models, reference_pack
from app.services.generation import writing_reference_pack
from app.services.generation.official_document_drafting import build_full_draft_prompt
from writer_material_view_decoder import decode_materials
from p2_admission_approval import historical_atomic_with_p2_admission


ROOT = Path(__file__).resolve().parents[2]
BASE_SHA = "8c7b57130a897c8b51e0b432a344abd0cbc08675"
OWNER_PATH = "backend/app/services/generation/atomic_evidence.py"
OLD_REASON = "auxiliary_does_not_fill_primary_type_gap"


@pytest.fixture(scope="module")
def baseline():
    source = subprocess.check_output(["git", "show", f"{BASE_SHA}:{OWNER_PATH}"], cwd=ROOT)
    module = types.ModuleType("p2_unmodified_baseline")
    exec(compile(historical_atomic_with_p3a_recognition(source), BASE_SHA + "/atomic_evidence.py", "exec"), module.__dict__)
    return module


def request():
    return ArticleGenRequest(title="2026年综合业务工作总结", document_type="工作总结", target_words=1200)


def chunk(doc, text, index=0):
    return {"document_id": doc, "chunk_id": f"{doc}:c{index}", "text": text,
            "source_title": "合成来源标题禁止外发", "section_index": 0, "chunk_index": index}


def retrieval():
    return {"retrieval_status": "SUFFICIENT", "primary_document_id": "primary-doc",
            "auxiliary_document_ids": ["aux-doc", "aux-doc-2"], "selected_chunks": [
                chunk("aux-doc", "测试乙部门组织业务培训。"),
                chunk("aux-doc-2", "测试丙部门维护周边秩序。"),
                chunk("primary-doc", "测试甲部门开展档案核查。"),
            ]}


@pytest.mark.parametrize("exact", [False, True])
def test_same_type_distinct_facts_and_both_auxiliary_documents_survive(baseline, exact):
    payload = retrieval()
    before_payload = copy.deepcopy(payload)
    task = build_official_draft_task(request())
    old = baseline.build_atomic_evidence_pack(task, payload, exact_source_spans=exact)
    new = atomic.build_atomic_evidence_pack(task, payload, exact_source_spans=exact)
    assert len(old["evidence"]) == 1
    assert sum(row["reason"] == OLD_REASON for row in old["discarded_evidence"]) == 2
    assert len(new["evidence"]) == 3
    assert {row["evidence_type"] for row in new["evidence"]} == {"action"}
    assert len({tuple(row[key] for key in ("subject", "action", "object", "result"))
                for row in new["evidence"]}) == 3
    assert new["evidence"][0] == old["evidence"][0]
    aux = [row for row in new["evidence"] if row["document_role"] == "auxiliary"]
    assert {row["document_id"] for row in aux} == {"aux-doc", "aux-doc-2"}
    assert [row["document_role"] for row in new["evidence"]] == ["primary", "auxiliary", "auxiliary"]
    assert not any(row["reason"] == OLD_REASON for row in new["discarded_evidence"])
    assert atomic.validate_atomic_evidence_pack(new, payload)["passed"]
    assert new == atomic.build_atomic_evidence_pack(task, payload, exact_source_spans=exact)
    assert payload == before_payload


def test_duplicate_proposition_keeps_existing_document_scope_and_first_identity(baseline):
    payload = retrieval()
    for doc in ("primary-doc", "aux-doc", "aux-doc-2"):
        payload["selected_chunks"].extend([
            chunk(doc, "测试单位开展资料核验。", 1),
            chunk(doc, "测试单位开展资料核验。", 2),
        ])
    task = build_official_draft_task(request())
    new = atomic.build_atomic_evidence_pack(task, payload, exact_source_spans=True)
    rejects = [row for row in new["discarded_evidence"] if row["reason"] == "duplicate_proposition"]
    assert {row["document_id"] for row in rejects} == {"primary-doc", "aux-doc", "aux-doc-2"}
    assert len(rejects) == 3
    # Existing key includes document_id: identical facts in different documents
    # remain separate. P2 must not introduce cross-document semantic merging.
    for doc in ("primary-doc", "aux-doc", "aux-doc-2"):
        isolated = copy.deepcopy(payload)
        isolated["selected_chunks"] = [row for row in payload["selected_chunks"] if row["document_id"] == doc]
        old = baseline.build_atomic_evidence_pack(task, isolated, exact_source_spans=True)
        assert [row for row in new["evidence"] if row["document_id"] == doc] == old["evidence"]
        assert [row for row in rejects if row["document_id"] == doc] == old["discarded_evidence"]


@pytest.mark.parametrize("text,state", [
    ("测试单位计划开展业务培训。", "PLAN"),
    ("测试单位落实工作不到位。", "PROBLEM"),
    ("测试单位已完成档案整理。", "COMPLETED"),
    ("测试单位开展业务培训。", "UNKNOWN"),
    ("测试单位正在推进档案整理。", "ONGOING"),
    ("测试单位部分完成档案整理。", "PARTIAL"),
    ("测试单位尚未完成档案整理。", "NOT_IMPLEMENTED"),
    ("测试单位与目标相比存在明显差距。", "GAP"),
    ("测试单位取得明显成效。", "RESULT"),
    ("测试单位建议开展业务培训。", "RECOMMENDATION"),
    ("如条件具备时开展业务培训。", "CONDITIONAL"),
    ("测试单位位于城区。", "CONTEXT"),
])
def test_auxiliary_role_never_rewrites_semantic_state_or_qualifier(baseline, text, state):
    payload = retrieval()
    payload["selected_chunks"] = [
        chunk("primary-doc", "测试甲部门开展核查。测试甲部门完成登记。测试甲部门位于城区。"),
        chunk("aux-doc", text),
    ]
    task = build_official_draft_task(request())
    new = atomic.build_atomic_evidence_pack(task, payload, exact_source_spans=True)
    auxiliary = [row for row in new["evidence"] if row["document_id"] == "aux-doc"]
    isolated = copy.deepcopy(payload)
    isolated["selected_chunks"] = payload["selected_chunks"][1:]
    old = baseline.build_atomic_evidence_pack(task, isolated, exact_source_spans=True)
    assert auxiliary and auxiliary == old["evidence"]  # All fields, IDs and qualifiers.
    assert {row["semantic_state"] for row in auxiliary} == {state}
    isolated["primary_document_id"] = "aux-doc"
    isolated["auxiliary_document_ids"] = []
    as_primary = atomic.build_atomic_evidence_pack(task, isolated, exact_source_spans=True)
    identity_fields = {"evidence_id", "document_role"}
    def strip_role(rows):
        return sorted(canonical_json({k: v for k, v in row.items() if k not in identity_fields}) for row in rows)

    assert strip_role(auxiliary) == strip_role(as_primary["evidence"])


def test_auxiliary_source_state_conflict_remains_reviewable_in_registry_and_packet(tmp_path, baseline):
    payload = retrieval()
    payload["selected_chunks"][0]["text"] = "测试乙部门计划开展业务培训、已完成档案登记。"
    for row in payload["selected_chunks"]:
        row["chunk_text"] = row["text"]
    task = build_official_draft_task(request())
    isolated = copy.deepcopy(payload)
    isolated["selected_chunks"] = payload["selected_chunks"][:1]
    expected = baseline.build_full_draft_anchor_input(task, isolated)
    assets = tuple(stable_material_assets.load_stable_material_asset(row, tmp_path).asset
                   for row in payload["selected_chunks"])
    result = task_assembly.assemble_task_from_assets(request(), payload, assets)
    anchors = json.loads(result.anchor_input_json)
    rows = {eid: row for eid, row in anchors["registry"].items() if row["source_scope"]["document_id"] == "aux-doc"}
    assert rows == expected["registry"]
    assert {row["fact"]["semantic_state"] for row in rows.values()} == {"PLAN", "COMPLETED"}
    for row in rows.values():
        conflict, = row["limitations"]
        assert conflict["kind"] == "SOURCE_STATE_CONFLICT" and conflict["requires_review"] is True
        assert conflict["evidence_states"] == {eid: item["fact"]["semantic_state"] for eid, item in rows.items()}
    findings = [row for row in result.packet.overlay()["findings"] if row["evidence_id"] in rows]
    assert len(findings) == len(rows)
    assert all(row["blocks_strong_conclusion"] and "TIME_STATE_CONFLICT" in row["reasons"] for row in findings)
    brief = json.loads(result.packet.brief_json)
    assert any("SOURCE_STATE_CONFLICT" in note["text"] for matter in brief["matters"] for note in matter["limits"])


def test_conflict_with_unavailable_state_still_fails_closed(tmp_path, baseline):
    payload = retrieval()
    payload["selected_chunks"][0]["text"] = "测试乙部门计划开展培训并明确完成时限。"
    isolated = copy.deepcopy(payload)
    isolated["selected_chunks"] = payload["selected_chunks"][:1]
    task = build_official_draft_task(request())
    old = baseline.build_full_draft_anchor_input(task, isolated)
    new = atomic.build_full_draft_anchor_input(task, payload)
    assert [row for row in new["unavailable"] if row["atomic_evidence"]["document_id"] == "aux-doc"] == old["unavailable"]
    assert old["unavailable"]
    for eid, row in old["registry"].items():
        assert new["registry"][eid] == row
    digest = load_material_digest(payload, tmp_path).digest
    for anchors in (old, new):
        packet = FullDraftMaterialPacket.from_authorized_input(request().model_dump(), anchors)
        # No conflict state may be silently pruned from the reading brief.
        with pytest.raises(FullArticleContractError, match="MATERIAL_BRIEF_CONFLICT_CONTEXT_REQUIRED"):
            task_assembly._assemble_reading_brief(packet, digest)


def test_auxiliary_reaches_real_registry_packet_and_writer_view_without_source_expansion(tmp_path, reference_pack):
    payload = retrieval()
    for row in payload["selected_chunks"]:
        # Long original document exists in assets; only authorized prefix may be used.
        row["text"] += "合成来源标题禁止外发。" + "孤立背景内容" * 12 + "。测试单位建立XX机制。"
        row["chunk_text"] = row["text"] + "完整文档私密尾部不得外发" * 100
    original = copy.deepcopy(payload)
    assets = tuple(stable_material_assets.load_stable_material_asset(row, tmp_path).asset
                   for row in payload["selected_chunks"])
    result = task_assembly.assemble_task_from_assets(request(), payload, assets)
    anchors = json.loads(result.anchor_input_json)
    registry = json.loads(result.packet.registry_json)
    assert anchors["registry"] == registry
    assert {row["source_scope"]["document_id"] for row in registry.values()} == {"primary-doc", "aux-doc", "aux-doc-2"}
    legal = set(registry)
    accepted = {row["evidence_id"]: row for row in anchors["atomic_evidence"]["evidence"]}
    assert legal == accepted.keys()
    assert all(re.fullmatch(r"EV-[0-9A-F]{12}", eid) for eid in legal)
    for eid, row in registry.items():
        source = row["source_scope"]
        chunk_row = next(c for c in payload["selected_chunks"] if c["chunk_id"] == source["chunk_id"])
        assert row["anchor"]["evidence_id"] == row["fact"]["evidence_id"] == eid
        assert source["source_span"] in chunk_row["text"]
        assert text_sha(source["source_span"]) == source["source_span_sha256"] == accepted[eid]["source_span_sha256"]
        assert row["input_constraints"]["atomic"] == accepted[eid]
    brief = validate_material_brief(json.loads(result.packet.brief_json), registry, result.packet.brief_input_sha256)
    assert {eid for matter in brief["matters"] for eid in matter["evidence_ids"]} == legal
    reasons = {row["reason"] for row in anchors["atomic_evidence"]["discarded_evidence"]}
    assert {"source_title_residue", "unsafe_placeholder", "unreliable_unparsed_clause"} <= reasons
    assert OLD_REASON not in reasons
    # Pure prompt serialization and independent decoding; never call the Writer.
    with writing_reference_pack.use_writing_reference_pack(reference_pack):
        prompt = build_full_draft_prompt(result.packet)
    view, decoded_brief = decode_materials(prompt["user"])
    assert view == result.packet.evidence_writing_view() and decoded_brief == brief
    assert {row["evidence_id"] for row in view} == legal
    visible = canonical_json(prompt)
    assert set(re.findall(r"EV-[A-Za-z0-9_-]+", visible)) == legal
    for row in payload["selected_chunks"]:
        assert row["text"] not in visible and row["chunk_text"] not in visible
    for forbidden in ("合成来源标题禁止外发", "完整文档私密尾部不得外发", "孤立背景内容" * 12,
                      '"complete_chunk"', '"complete_document"', '"chunk_text"', '"source_title"'):
        assert forbidden not in visible
    assert payload == original
    print("P2_METRICS=" + canonical_json({
        "AUXILIARY_EVIDENCE_ACCEPTED": sum(a["document_role"] == "auxiliary" for a in accepted.values()),
        "AUXILIARY_DOC_1_CONTRIBUTES": "YES", "AUXILIARY_DOC_2_CONTRIBUTES": "YES",
        "AUXILIARY_TYPE_EXCLUSION_COUNT": 0, "ILLEGAL_EVIDENCE_IDS": 0,
        "DOWNSTREAM_REGISTRY_CONTAINS_AUXILIARY": True, "MATERIAL_PACKET_CONTAINS_AUXILIARY": True,
    }))


def test_p2_historical_delta_and_exact_p3a_current_approval():
    before = subprocess.check_output(["git", "show", f"{BASE_SHA}:{OWNER_PATH}"], cwd=ROOT)
    after = (ROOT / OWNER_PATH).read_bytes().replace(b"\r\n", b"\n")
    p2 = subprocess.check_output(["git", "show", f"72af33cf4e6283c04c18ab87a30e2b506471feaa:{OWNER_PATH}"], cwd=ROOT)
    assert p2 == historical_atomic_with_p2_admission(before)
    from hashlib import sha256
    from p2_admission_approval import P2_ATOMIC_EVIDENCE_SHA256
    assert sha256(p2).hexdigest() == P2_ATOMIC_EVIDENCE_SHA256
    assert after == historical_atomic_with_p3a_recognition(p2)
    assert_p3a_atomic_evidence(ROOT)
    assert b"primary_types" not in after and b"auxiliary_overlap" not in after
    assert OLD_REASON.encode() not in after


@pytest.mark.parametrize("module_name,test_name", [
    ("test_rag_observability", "test_retrieval_business_code_and_protected_owners_unchanged"),
    ("test_production_task_assembly", "test_protected_owners_unchanged_from_task_base"),
    ("test_material_ingestion", "test_protected_generation_retrieval_and_legacy_owners_unchanged"),
    ("test_stable_material_assets", "test_production_and_fact_owners_remain_unchanged"),
    ("test_material_digest_task_assembly", "test_production_files_unchanged_and_no_experiment_imports"),
])
def test_every_protected_guard_rejects_future_atomic_changes(monkeypatch, module_name, test_name):
    guard = getattr(import_module(module_name), test_name)
    read_bytes = Path.read_bytes

    def changed(path):
        data = read_bytes(path)
        return data + b"\n" if path == ROOT / OWNER_PATH else data

    # Simulate a one-byte edit in memory; never modify the approved production file.
    monkeypatch.setattr(Path, "read_bytes", changed)
    with pytest.raises(AssertionError, match="differs from the exact P3-A-approved content"):
        guard()
