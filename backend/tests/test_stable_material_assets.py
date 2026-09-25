"""W3-8C-1: complete synthetic chunks, actual owners, committed baseline."""
import copy
from dataclasses import asdict, replace
import json
from pathlib import Path
import subprocess
import types

import pytest

from app.models.full_article import FullArticleContractError, canonical_json, text_sha
from app.models.generation import ArticleGenRequest
from app.services.generation import atomic_evidence as atomic
from app.services.generation import material_digest as digest_owner
from app.services.generation import source_substrate as source_owner
from app.services.generation import stable_material_assets as stable
from app.services.generation import task_assembly as assembly
from app.services.generation.full_draft_material import FullDraftMaterialPacket
from app.services.generation.generator import build_official_draft_task, derive_final_delivery_length_gate
from test_material_digest_task_assembly import no_models  # real-call failure sentinels
from test_source_substrate import sources
from p2_admission_approval import (
    ATOMIC_EVIDENCE_PATH, assert_p2_atomic_evidence, historical_atomic_with_p2_admission,
)


BASE_SHA = "87711c3c63e5ab280b31c4eaacfd43a7f2914ee7"
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def committed():
    source = subprocess.check_output([
        "git", "show", BASE_SHA + ":backend/app/services/generation/atomic_evidence.py"], cwd=ROOT)
    module = types.ModuleType("w3_8c_1_committed_atomic")
    exec(compile(historical_atomic_with_p2_admission(source), BASE_SHA + "/atomic_evidence.py", "exec"), module.__dict__)
    return module


def request(title="2026年安全检查工作总结"):
    return ArticleGenRequest(title=title, document_type="工作总结", target_words=1200)


def long_chunk():
    text = "测试街道组织安全检查。测试街道尚未建立应急机制。测试街道基本完成隐患整改。"
    text += "合成背景文字。" * 40
    text += "测试街道开展中段消防培训。测试街道正在推进中段隐患整改。"
    text += "合成背景文字。" * 70
    text += "测试街道完成尾段保密标记项目。测试街道计划于2028年组织尾段应急演练。"
    text += "合成背景文字。" * 50
    assert len(text) > 1200 and text.index("尾段保密标记") > 800
    return {"document_id": "synthetic-main", "chunk_id": "long-1", "chunk_text": text,
            "source_title": "合成来源", "source_file": "synthetic.txt", "section_index": 1,
            "chunk_index": 1, "metadata": {"title": "合成来源", "section_path": ["工作情况"]}}


def retrieval_for(chunk, budget=800, role="primary"):
    row = copy.deepcopy(chunk)
    row.update(text=chunk["chunk_text"][:budget], document_role=role, rank=1,
               score=0.9, selection_reason="semantic_relevance")
    return {"retrieval_status": "SUFFICIENT",
            "primary_document_id": chunk["document_id"] if role == "primary" else "other-primary",
            "auxiliary_document_ids": [] if role == "primary" else [chunk["document_id"]],
            "selected_chunks": [row]}


def precompute(retrieval, directory):
    return tuple(stable.load_stable_material_asset(chunk, directory).asset
                 for chunk in retrieval["selected_chunks"])


def assert_equivalent(committed, req, retrieval, assets):
    original = copy.deepcopy(retrieval)
    task = build_official_draft_task(req)
    before = committed.build_full_draft_anchor_input(task, retrieval)
    result = assembly.assemble_task_from_assets(req, retrieval, assets)
    after = json.loads(result.anchor_input_json)
    assert before == after  # Entire atoms, IDs, states, restrictions, discards and anchors.
    fields = {**asdict(task), "style": req.style, "scene": req.scene,
              "hard_max": derive_final_delivery_length_gate(task.target_words)["max_words"]}
    expected_packet = FullDraftMaterialPacket.from_authorized_input(fields, before)
    assert replace(result.packet, brief_json="") == expected_packet
    assert expected_packet.evidence_writing_view() == result.packet.evidence_writing_view()
    legal = json.loads(result.packet.registry_json)
    ids = {eid for matter in json.loads(result.packet.brief_json)["matters"]
           for eid in matter["evidence_ids"]}
    assert ids == legal.keys()  # No illegal IDs or omitted admitted facts.
    assert retrieval == original
    return result


def test_precompute_without_task_then_800_300_reuse(tmp_path, monkeypatch, committed, no_models):
    chunk = long_chunk()
    first = stable.load_stable_material_asset(chunk, tmp_path)
    assert not first.cache_hit
    scenarios = [retrieval_for(chunk, 800), retrieval_for(chunk, 300, "auxiliary")]
    outputs = []
    for number, retrieval in enumerate(scenarios):
        loaded = stable.load_stable_material_asset(retrieval["selected_chunks"][0], tmp_path)
        assert loaded.cache_hit and loaded.asset == first.asset
        view = digest_owner.compose_material_digest(retrieval, [loaded.asset])
        authorized = retrieval["selected_chunks"][0]["text"]
        assert json.loads(view.substrate.sources_json)[0]["text"] == authorized
        assert all(span in authorized for span in view.reading_index())
        assert "尾段保密标记" not in view.units_json
        if number == 1:
            assert "中段消防培训" not in view.units_json
        result = assert_equivalent(committed, request(), retrieval, [loaded.asset])
        assert "尾段保密标记" not in canonical_json(asdict(result.packet))
        outputs.append(json.loads(result.anchor_input_json))
    assert len(list(tmp_path.glob("*.json"))) == 1
    assert "中段消防培训" in canonical_json(outputs[0])
    assert "中段消防培训" not in canonical_json(outputs[1])
    actual = atomic.build_full_draft_anchor_input

    def inspect_boundary(task, payload, **kwargs):
        assert all("chunk_text" not in row for row in payload["selected_chunks"])
        assert all(len(row["text"]) <= 300 for row in payload["selected_chunks"])
        return actual(task, payload, **kwargs)

    monkeypatch.setattr(assembly, "build_full_draft_anchor_input", inspect_boundary)
    assembly.assemble_task_from_assets(request(), scenarios[1], [first.asset])
    (tmp_path / "comparison-evidence.txt").write_text(canonical_json({
        "asset_key": first.asset.cache_key, "full_length": len(chunk["chunk_text"]),
        "authorized_lengths": [800, 300], "anchor_inputs": outputs,
        "differences": [], "call_counts": no_models,
    }), encoding="utf-8")


@pytest.mark.parametrize("field,value", [
    ("task_title", "另一任务"), ("rank", 88), ("raw_rank", 90), ("document_rank", 3),
    ("score", 0.01), ("document_score", 0.2), ("document_role", "auxiliary"),
    ("selection_reason", "coverage"), ("evidence_id", "EV-DO-NOT-CACHE"),
    ("section_assignment", "SEC-TEST"), ("coverage_role", "next_step"),
])
@pytest.mark.parametrize("location", ["chunk", "metadata"])
def test_task_fields_never_rebuild(tmp_path, monkeypatch, field, value, location):
    chunk = long_chunk()
    first = stable.load_stable_material_asset(chunk, tmp_path)
    target = chunk if location == "chunk" else chunk["metadata"]
    target[field] = value
    chunk["text"] = chunk["chunk_text"][:300]

    def forbidden(*args, **kwargs):
        pytest.fail("Stable cache hit rebuilt parsing/reading work")

    monkeypatch.setattr(atomic, "_source_fragments", forbidden)
    monkeypatch.setattr(digest_owner, "_reading_units", forbidden)
    second = stable.load_stable_material_asset(chunk, tmp_path)
    assert second.cache_hit and second.asset == first.asset
    assert field not in json.loads(second.asset.payload_json)


@pytest.mark.parametrize("field,value", [
    ("chunk_text", "测试街道建立新的工作机制。"), ("chunk_id", "new-chunk"),
    ("document_id", "new-document"), ("source_title", "来源变化"),
    ("source_file", "new-source.txt"), ("section_index", 5),
    ("limitations", ["需要复核"]), ("writability", {"requires_review": True}),
    ("evidence_scope_id", "SOURCE-NEW"),
    ("metadata", {"title": "来源变化", "source_path": "synthetic/new.txt"}),
])
def test_stable_source_change_invalidates(tmp_path, field, value):
    chunk = long_chunk()
    first = stable.load_stable_material_asset(chunk, tmp_path)
    chunk[field] = value
    second = stable.load_stable_material_asset(chunk, tmp_path)
    assert not second.cache_hit and second.asset.cache_key != first.asset.cache_key
    with pytest.raises(FullArticleContractError, match="INPUT_CHANGED"):
        first.asset.source_for(chunk)


@pytest.mark.parametrize("owner,function", [
    (source_owner, "_parser_sha256"), (digest_owner, "_rules_sha256"), (stable, "_rules_sha256"),
])
def test_rule_changes_invalidate_persisted_and_memory_assets(tmp_path, monkeypatch, owner, function):
    chunk = long_chunk()
    first = stable.load_stable_material_asset(chunk, tmp_path)
    monkeypatch.setattr(owner, function, lambda: "changed-rules")
    second = stable.load_stable_material_asset(chunk, tmp_path)
    assert not second.cache_hit and first.asset.cache_key != second.asset.cache_key
    with pytest.raises(FullArticleContractError, match="INPUT_CHANGED"):
        first.asset.source_for(chunk)


def test_source_module_fingerprint_invalidates(tmp_path, monkeypatch):
    chunk = long_chunk()
    first = stable.load_stable_material_asset(chunk, tmp_path)
    changed_source = tmp_path / "source-owner.py"
    changed_source.write_text("changed source parsing implementation", encoding="utf-8")
    monkeypatch.setattr(source_owner, "__file__", str(changed_source))
    second = stable.load_stable_material_asset(chunk, tmp_path)
    assert not second.cache_hit and first.asset.cache_key != second.asset.cache_key


@pytest.mark.parametrize("budget", [1, 4, 12, 20, 299, 300, 301, 799, 800, 801, 1200])
def test_cut_boundaries_match_current_atomic_parser(tmp_path, committed, budget):
    chunk = long_chunk()
    retrieval = retrieval_for(chunk, budget)
    assets = precompute(retrieval, tmp_path)
    view = digest_owner.compose_material_digest(retrieval, assets)
    task = build_official_draft_task(request())
    expected = committed.build_atomic_evidence_pack(task, retrieval, exact_source_spans=True)
    actual = atomic.build_atomic_evidence_pack(task, stable.authorized_retrieval_view(retrieval),
                                             exact_source_spans=True, source_substrate=view.substrate)
    assert expected == actual
    try:
        committed.build_full_draft_anchor_input(task, retrieval)
    except (FullArticleContractError, committed.InvalidEvidencePackError) as before:
        error_type = (atomic.InvalidEvidencePackError
                      if isinstance(before, committed.InvalidEvidencePackError) else type(before))
        with pytest.raises(error_type) as after:
            assembly.assemble_task_from_assets(request(), retrieval, assets)
        assert str(before) == str(after.value)
    else:
        assert_equivalent(committed, request(), retrieval, assets)


@pytest.mark.parametrize("text", [
    "测试街道尚未完成专项整改。", "测试街道在隐患整改工作中，仍存在衔接不足。",
    "测试街道计划开展培训，并且尚未完成整改。", "一、工作总结\n测试街道组织安全检查。",
    "测试街道组织“检查，整改”工作。中国长安网 https://example.invalid",
])
def test_clipped_context_and_literal_signals_are_recomputed(tmp_path, committed, text):
    chunk = long_chunk()
    chunk["chunk_text"] = text
    asset = stable.load_stable_material_asset(chunk, tmp_path).asset
    for limit in range(1, len(text) + 1):
        retrieval = retrieval_for(chunk, limit)
        digest = digest_owner.compose_material_digest(retrieval, [asset])
        expected = tuple(committed._source_fragments(text[:limit], exact_source_spans=True))
        assert digest.substrate.fragments_by_text == ((text[:limit], expected),)
        assert json.loads(digest.units_json) == digest_owner._reading_units(digest.substrate)
        task = build_official_draft_task(request())
        assert atomic.build_atomic_evidence_pack(task, retrieval, exact_source_spans=True,
            source_substrate=digest.substrate) == committed.build_atomic_evidence_pack(
                task, retrieval, exact_source_spans=True)


def test_full_view_skips_both_parsing_and_reading(tmp_path, monkeypatch, committed):
    chunk = long_chunk()
    retrieval = retrieval_for(chunk, len(chunk["chunk_text"]))
    assets = precompute(retrieval, tmp_path)

    def forbidden(*args, **kwargs):
        pytest.fail("Identical full text should reuse precomputed fragments and units")

    monkeypatch.setattr(atomic, "_source_fragments", forbidden)
    monkeypatch.setattr(digest_owner, "_reading_units", forbidden)
    assert_equivalent(committed, request(), retrieval, assets)


def test_subsets_order_and_current_restrictions_are_not_frozen(tmp_path, committed):
    retrieval = sources()
    for chunk in retrieval["selected_chunks"]:
        chunk["chunk_text"] = chunk["text"] + "测试街道完成授权外尾部工作。"
        chunk["limitations"] = ["仅供核实，不得补全结果"]
        chunk["metadata"]["limitation"] = "保留来源歧义"
        chunk["writability"] = {"requires_review": True}
    assets = precompute(retrieval, tmp_path)
    for index in range(3):
        selected = copy.deepcopy(retrieval)
        if index == 1:
            selected["selected_chunks"].reverse()
            selected["primary_document_id"] = "synthetic-aux"
            selected["auxiliary_document_ids"] = ["synthetic-main"]
        if index == 2:
            selected["selected_chunks"] = selected["selected_chunks"][:2]
            for row in selected["selected_chunks"]:
                row["coverage_role"] = "next_step"
        assert_equivalent(committed, request("2028年宣传培训工作总结"), selected, assets)
        assert all(stable.load_stable_material_asset(row, tmp_path).cache_hit
                   for row in selected["selected_chunks"])


def test_missing_empty_or_foreign_authorization_never_falls_back(tmp_path, committed):
    chunk = long_chunk()
    asset = stable.load_stable_material_asset(chunk, tmp_path).asset
    retrieval = retrieval_for(chunk)
    for value in (None, 300, "outside text"):
        retrieval["selected_chunks"][0]["text"] = value
        with pytest.raises(FullArticleContractError, match="AUTHORIZED_TEXT"):
            assembly.assemble_task_from_assets(request(), retrieval, [asset])
    retrieval["selected_chunks"][0].pop("text")
    with pytest.raises(FullArticleContractError, match="AUTHORIZED_TEXT_REQUIRED"):
        assembly.assemble_task_from_assets(request(), retrieval, [asset])
    retrieval["selected_chunks"][0]["text"] = ""
    digest = digest_owner.compose_material_digest(retrieval, [asset])
    assert digest.reading_index() == {}
    assert digest.substrate.fragments_by_text == (("", ()),)
    empty_view = stable.authorized_retrieval_view(retrieval)
    task = build_official_draft_task(request())
    pack = committed.build_atomic_evidence_pack(task, empty_view, exact_source_spans=True)
    assert pack["evidence"] == []
    with pytest.raises(committed.InvalidEvidencePackError) as before:
        committed.build_full_draft_anchor_input(task, empty_view)
    with pytest.raises(atomic.InvalidEvidencePackError) as after:
        assembly.assemble_task_from_assets(request(), retrieval, [asset])
    assert str(before.value) == str(after.value)


def test_non_prefix_authorization_is_not_enlarged(tmp_path, committed):
    chunk = long_chunk()
    retrieval = retrieval_for(chunk)
    retrieval["selected_chunks"][0]["text"] = "测试街道正在推进中段隐患整改。"
    assets = precompute(retrieval, tmp_path)
    result = assert_equivalent(committed, request(), retrieval, assets)
    assert "尚未建立" not in result.packet.registry_json


def test_missing_full_source_and_stale_assets_fail_closed(tmp_path):
    chunk = long_chunk()
    asset = stable.load_stable_material_asset(chunk, tmp_path).asset
    partial = copy.deepcopy(chunk)
    partial["text"] = partial.pop("chunk_text")[:300]
    with pytest.raises(FullArticleContractError, match="FULL_SOURCE_REQUIRED"):
        stable.load_stable_material_asset(partial, tmp_path)
    retrieval = retrieval_for(chunk)
    retrieval["selected_chunks"][0]["chunk_text"] += "原文变化。"
    with pytest.raises(FullArticleContractError, match="MISSING_OR_STALE"):
        assembly.assemble_task_from_assets(request(), retrieval, [asset])


@pytest.mark.parametrize("damage", ["json", "checksum", "identity", "task_field", "fragments", "unit"])
def test_corrupt_assets_fail_closed(tmp_path, damage):
    chunk = long_chunk()
    first = stable.load_stable_material_asset(chunk, tmp_path)
    path = tmp_path / (first.asset.cache_key + ".json")
    envelope = json.loads(path.read_bytes())
    if damage == "json":
        path.write_bytes(b"{broken")
    else:
        payload = envelope["payload"]
        if damage == "checksum": envelope["sha256"] = "bad"
        if damage == "identity": payload["document_id"] = "foreign"
        if damage == "task_field": payload["evidence_id"] = "EV-FOREIGN"
        if damage == "fragments": payload["fragments"] = [None]
        if damage == "unit": payload["reading_units"][0]["semantic_state"] = "COMPLETED"
        if damage != "checksum": envelope["sha256"] = text_sha(canonical_json(payload))
        path.write_text(canonical_json(envelope), encoding="utf-8")
    with pytest.raises(FullArticleContractError, match="CACHE_INVALID"):
        stable.load_stable_material_asset(chunk, tmp_path)


def test_asset_immutable_and_io_error_explicit(tmp_path):
    chunk = long_chunk()
    loaded = stable.load_stable_material_asset(chunk, tmp_path)
    detached = loaded.asset.source_for(chunk)
    detached["fragments"].clear()
    assert loaded.asset.source_for(chunk)["fragments"]
    not_dir = tmp_path / "not-dir"
    not_dir.write_text("test", encoding="utf-8")
    with pytest.raises(FullArticleContractError, match="CACHE_(UNREADABLE|WRITE_FAILED)"):
        stable.load_stable_material_asset(chunk, not_dir)


@pytest.mark.parametrize("change", ["global_scope", "explicit_scope", "conflict", "status", "overlay"])
def test_admission_failures_and_task_restrictions_match_committed(tmp_path, committed, change):
    retrieval = retrieval_for(long_chunk())
    row = retrieval["selected_chunks"][0]
    if change == "global_scope": row["evidence_scope_id"] = "GLOBAL"
    if change == "explicit_scope": row["evidence_scope_id"] = "SYNTHETIC-SCOPE"
    if change == "conflict":
        duplicate = copy.deepcopy(row)
        duplicate["chunk_text"] = duplicate["text"] = "测试街道组织另一项专项检查。"
        retrieval["selected_chunks"].append(duplicate)
    if change == "status": retrieval["retrieval_status"] = "INSUFFICIENT"
    if change == "overlay":
        retrieval["time_risk_overlay"] = {"findings": [], "current_task": "synthetic"}
        retrieval["source_policy"] = {"instruction": "仅供核实，不得补全结果"}
        retrieval["limitations"] = ["当前任务限制"]
    assets = precompute(retrieval, tmp_path)
    try:
        committed.build_full_draft_anchor_input(build_official_draft_task(request()), retrieval)
    except (FullArticleContractError, committed.InvalidEvidencePackError) as before:
        error_type = (atomic.InvalidEvidencePackError
                      if isinstance(before, committed.InvalidEvidencePackError) else type(before))
        with pytest.raises(error_type) as after:
            assembly.assemble_task_from_assets(request(), retrieval, assets)
        assert str(before) == str(after.value)
    else:
        assert_equivalent(committed, request(), retrieval, assets)


def test_production_and_fact_owners_remain_unchanged():
    from p1_ranking_approval import DOCUMENT_RETRIEVER_PATH, assert_p1_document_retriever

    # W3-8C-3 authorizes full_draft_generation wiring; its behavior and the
    # remaining protected owners are also checked against the W3-8C-3 base.
    files = ["backend/app/routers/generation.py",
             "backend/app/services/generation/generator.py",
             "backend/app/services/generation/atomic_evidence.py",
             "backend/app/services/generation/official_document_drafting.py",
             "backend/app/services/retrieval/document_retriever.py"]
    # WRITER-VIEW-01 exempts full_draft_material.py only; its original packet,
    # candidate input and complete decoded views retain deep comparison coverage.
    for name in files:
        if name == ATOMIC_EVIDENCE_PATH:
            assert_p2_atomic_evidence(ROOT)
            continue
        if name == DOCUMENT_RETRIEVER_PATH:
            assert_p1_document_retriever(ROOT)
            continue
        before = subprocess.check_output(["git", "show", BASE_SHA + ":" + name], cwd=ROOT).decode("utf-8")
        assert (ROOT / name).read_text(encoding="utf-8") == before.replace("\r\n", "\n")
