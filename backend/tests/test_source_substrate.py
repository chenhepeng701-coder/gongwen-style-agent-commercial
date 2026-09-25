"""W3-8A-P: deterministic synthetic sources, real owners, zero model calls."""
import copy
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import time
import types

import pytest

from app.models.full_article import FullArticleContractError, canonical_json, text_sha
from app.models.generation import ArticleGenRequest
from app.services.generation import atomic_evidence as atomic
from app.services.generation import source_substrate as cache
from app.services.generation.full_draft_material import FullDraftMaterialPacket
from app.services.generation.generator import build_official_draft_task
from p2_admission_approval import historical_atomic_with_p2_admission


@pytest.fixture(scope="module")
def baseline():
    # Execute the actual committed owner, not a copied/reimplemented algorithm.
    root = Path(__file__).resolve().parents[2]
    pre_p2 = "8c7b57130a897c8b51e0b432a344abd0cbc08675"
    source = subprocess.check_output([
        "git", "show", pre_p2 + ":backend/app/services/generation/atomic_evidence.py"], cwd=root)
    module = types.ModuleType("committed_atomic_evidence")
    # Pin the original pre-P2 owner; admit only the explicit P2 contract delta.
    exec(compile(historical_atomic_with_p2_admission(source), pre_p2 + "/atomic_evidence.py", "exec"), module.__dict__)
    return module


@pytest.fixture(autouse=True)
def no_model(monkeypatch):
    from app.services.llm import factory
    from app.services.llm.openai_compatible_provider import OpenAICompatibleProvider

    def forbidden(*args, **kwargs):
        pytest.fail("Source Substrate must never call a project model")
    monkeypatch.setattr(factory, "get_llm_provider", forbidden)
    monkeypatch.setattr(OpenAICompatibleProvider, "generate_text", forbidden)


def task(title="2026年安全检查工作总结"):
    return build_official_draft_task(ArticleGenRequest(
        title=title, document_type="工作总结", target_words=1200))


def sources():
    # Invented test text only. Covers qualifier/negation, plan dates, duplicate,
    # unsafe placeholder, headings/residue, contextual state and auxiliary gaps.
    texts = [
        "测试街道组织安全检查。测试街道计划于2027年开展安全培训。",
        "测试街道基本完成隐患整改。测试街道尚未建立应急机制。",
        "测试街道正在推进隐患整改。测试街道检查工作明显提升。",
        "一、工作总结\n测试街道组织安全检查。中国长安网 https://example.invalid",
        "测试街道建立XX机制。测试街道在隐患整改工作中，仍存在衔接不足。",
        "测试街道开展宣传培训。测试街道组织安全检查。",
    ]
    return {"retrieval_status": "SUFFICIENT", "primary_document_id": "synthetic-main",
        "auxiliary_document_ids": ["synthetic-aux"], "selected_chunks": [
            {"document_id": "synthetic-main" if i < 5 else "synthetic-aux",
             "chunk_id": f"chunk-{i}", "section_index": i, "chunk_index": i,
             "text": text, "metadata": {}}
            for i, text in enumerate(texts)]}


def compare(baseline, current_task, retrieval, substrate):
    old = baseline.build_full_draft_anchor_input(current_task, retrieval)
    new = atomic.build_full_draft_anchor_input(current_task, retrieval, source_substrate=substrate)
    # Entire structures, including unavailable, restrictions, input constraints,
    # discard reasons, exact numbers and order. This is stricter than counts.
    assert canonical_json(new) == canonical_json(old)
    old_packet = FullDraftMaterialPacket.from_authorized_input(asdict(current_task), old)
    new_packet = FullDraftMaterialPacket.from_authorized_input(asdict(current_task), new)
    assert old_packet.evidence_writing_view() == new_packet.evidence_writing_view()
    assert old_packet.overlay() == new_packet.overlay()
    assert old_packet.candidate_input() == new_packet.candidate_input()
    assert old_packet == new_packet  # Complete pre-Brief packet, including its input binding.
    return new, new_packet


def test_abcd_equivalence_and_timings(tmp_path, baseline):
    retrieval = sources()
    original = copy.deepcopy(retrieval)
    first = cache.load_source_substrate(retrieval, tmp_path)
    assert not first.cache_hit
    a, packet_a = compare(baseline, task(), retrieval, first.substrate)
    second = cache.load_source_substrate(retrieval, tmp_path)
    assert second.cache_hit and second.substrate == first.substrate
    b, _ = compare(baseline, task(), retrieval, second.substrate)
    assert a == b
    changed_task = task("2028年宣传培训工作总结")
    third = cache.load_source_substrate(retrieval, tmp_path)
    started = time.perf_counter()
    c = atomic.build_full_draft_anchor_input(changed_task, retrieval, source_substrate=third.substrate)
    task_ms = (time.perf_counter() - started) * 1000
    assert third.cache_hit and third.substrate == first.substrate
    expected_c, packet_c = compare(baseline, changed_task, retrieval, third.substrate)
    assert c == expected_c
    assert a["atomic_evidence"]["required_evidence"] != c["atomic_evidence"]["required_evidence"]
    assert packet_a.overlay() != packet_c.overlay()
    assert packet_a.brief_input_sha256 != packet_c.brief_input_sha256
    assert packet_a.registry_json == packet_c.registry_json
    changed = copy.deepcopy(retrieval)
    changed["selected_chunks"][0]["text"] += "测试街道开展应急演练。"
    fourth = cache.load_source_substrate(changed, tmp_path)
    assert not fourth.cache_hit and fourth.substrate.cache_key != first.substrate.cache_key
    d, _ = compare(baseline, task(), changed, fourth.substrate)
    assert d["registry"] != a["registry"]
    assert retrieval == original
    report = {"FIRST_BUILD_MS": first.elapsed_ms, "CACHE_LOAD_MS": second.elapsed_ms,
              "TASK_REBUILD_MS": task_ms, "cases": {}}
    for name, value, scenario_task, scenario_sources in zip(
            "ABCD", (a, b, c, d), (task(), task(), changed_task, task()),
            (retrieval, retrieval, retrieval, changed)):
        committed = baseline.build_full_draft_anchor_input(scenario_task, scenario_sources)
        assert committed == value
        (tmp_path / f"{name}-comparison.json").write_text(canonical_json({
            "task": asdict(scenario_task), "retrieval": scenario_sources,
            "baseline": committed, "cached_path": value}), encoding="utf-8")
        report["cases"][name] = {
            "registry_sha256": value["registry_sha256"], "evidence_ids": sorted(value["registry"]),
            "states": {eid: row["fact"]["semantic_state"] for eid, row in value["registry"].items()},
            "unavailable_reasons": [row["reason"] for row in value["unavailable"]],
            "discarded_reasons": [row["reason"] for row in value["atomic_evidence"]["discarded_evidence"]],
            "fact_differences": [], "id_differences": [], "semantic_differences": [],
        }
    (tmp_path / "metrics.json").write_text(canonical_json(report), encoding="utf-8")
    print("W3_8A_P_METRICS=" + canonical_json(report))


@pytest.mark.parametrize("exact", [True, False])
def test_cache_hit_really_skips_parser(tmp_path, monkeypatch, baseline, exact):
    retrieval = sources()
    first = cache.load_source_substrate(retrieval, tmp_path, exact_source_spans=exact)
    def forbidden(*args, **kwargs):
        pytest.fail("parser executed despite cache hit")
    monkeypatch.setattr(atomic, "_source_fragments", forbidden)
    hit = cache.load_source_substrate(retrieval, tmp_path, exact_source_spans=exact)
    assert hit.cache_hit and hit.substrate == first.substrate
    result = atomic.build_atomic_evidence_pack(task(), retrieval,
        exact_source_spans=exact, source_substrate=hit.substrate)
    assert result == baseline.build_atomic_evidence_pack(task(), retrieval, exact_source_spans=exact)
    if exact:
        compare(baseline, task(), retrieval, hit.substrate)


@pytest.mark.parametrize("change", ["role", "coverage", "order", "overlay", "selection"])
def test_task_state_rebuilt_not_cached(tmp_path, baseline, change):
    retrieval = sources()
    first = cache.load_source_substrate(retrieval, tmp_path)
    before, _ = compare(baseline, task(), retrieval, first.substrate)
    if change == "role":
        retrieval["primary_document_id"] = "synthetic-aux"
        retrieval["auxiliary_document_ids"] = ["synthetic-main"]
    elif change == "coverage":
        for chunk in retrieval["selected_chunks"]:
            chunk["coverage_role"] = "next_step"
            chunk["metadata"]["content_role"] = "next_step"
    elif change == "order":
        retrieval["selected_chunks"].reverse()
    elif change == "overlay":
        retrieval["time_risk_overlay"] = {"findings": [], "test_current_task": "changed"}
    elif change == "selection":
        retrieval["auxiliary_document_ids"] = []
    second = cache.load_source_substrate(retrieval, tmp_path)
    assert second.cache_hit and second.substrate == first.substrate
    after, _ = compare(baseline, task(), retrieval, second.substrate)
    if change in {"role", "coverage", "overlay", "selection"}:
        assert before != after


@pytest.mark.parametrize("field,value", [
    ("text", "测试街道组织安全检查。测试街道开展培训。"),
    ("chunk_id", "new-chunk"), ("document_id", "new-document"),
    ("source_title", "来源标题发生改变"), ("section_index", 200),
    ("section_heading", "来源章节发生改变"),
    ("evidence_scope_id", "scope-updated"), ("limitations", ["仅供核验"]),
    ("metadata", {"title": "来源元数据发生改变"}),
])
def test_effective_source_change_invalidates(tmp_path, field, value):
    retrieval = sources()
    first = cache.load_source_substrate(retrieval, tmp_path)
    retrieval["selected_chunks"][0][field] = value
    with pytest.raises(FullArticleContractError, match="INPUT_CHANGED"):
        atomic.build_full_draft_anchor_input(task(), retrieval, source_substrate=first.substrate)
    second = cache.load_source_substrate(retrieval, tmp_path)
    assert not second.cache_hit and second.substrate.cache_key != first.substrate.cache_key


def test_retains_raw_source_and_no_task_fields(tmp_path):
    retrieval = sources()
    loaded = cache.load_source_substrate(retrieval, tmp_path)
    payload = json.loads((tmp_path / (loaded.substrate.cache_key + ".json")).read_bytes())["payload"]
    assert set(payload) == {"version", "parser_sha256", "exact_source_spans", "sources", "fragments_by_text"}
    for row in payload["sources"]:
        assert set(row) == {"document_id", "chunk_id", "text", "source_attributes_sha256"}
    assert sorted(row["text"] for row in payload["sources"]) == sorted(
        row["text"] for row in retrieval["selected_chunks"])
    # Return mutation cannot poison the immutable in-memory substrate.
    text = retrieval["selected_chunks"][0]["text"]
    loaded.substrate.fragments(text, exact_source_spans=True).clear()
    assert loaded.substrate.fragments(text, exact_source_spans=True)
    with pytest.raises(FullArticleContractError, match="MODE_MISMATCH"):
        atomic.build_atomic_evidence_pack(task(), retrieval, source_substrate=loaded.substrate)


@pytest.mark.parametrize("damage", ["json", "checksum", "extra_field", "wrong_source", "fragments"])
def test_corrupt_cache_fails_closed(tmp_path, damage):
    retrieval = sources()
    loaded = cache.load_source_substrate(retrieval, tmp_path)
    path = tmp_path / (loaded.substrate.cache_key + ".json")
    envelope = json.loads(path.read_bytes())
    if damage == "json":
        path.write_bytes(b"{broken")
    else:
        if damage == "checksum": envelope["sha256"] = "wrong"
        if damage == "extra_field": envelope["payload"]["required_evidence"] = []
        if damage == "wrong_source": envelope["payload"]["sources"][0]["text"] = "changed"
        if damage == "fragments": envelope["payload"]["fragments_by_text"][0]["fragments"] = [42]
        if damage != "checksum": envelope["sha256"] = text_sha(canonical_json(envelope["payload"]))
        path.write_text(canonical_json(envelope), encoding="utf-8")
    with pytest.raises(FullArticleContractError, match="CACHE_INVALID"):
        cache.load_source_substrate(retrieval, tmp_path)


def test_parser_revision_and_mode_invalidate(tmp_path, monkeypatch):
    retrieval = sources()
    first = cache.load_source_substrate(retrieval, tmp_path)
    other_mode = cache.load_source_substrate(retrieval, tmp_path, exact_source_spans=False)
    assert not other_mode.cache_hit and other_mode.substrate.cache_key != first.substrate.cache_key
    monkeypatch.setattr(cache, "_parser_sha256", lambda: "new-parser-revision")
    second = cache.load_source_substrate(retrieval, tmp_path)
    assert not second.cache_hit and second.substrate.cache_key != first.substrate.cache_key
    with pytest.raises(FullArticleContractError, match="INPUT_CHANGED"):
        first.substrate.validate_sources(retrieval)


@pytest.mark.parametrize("change", ["scope", "limitations", "writability", "duplicate_identity", "insufficient"])
def test_admission_and_source_restrictions_still_owned_realtime(tmp_path, baseline, change):
    retrieval = sources()
    if change == "scope": retrieval["selected_chunks"][0]["evidence_scope_id"] = "GLOBAL"
    if change == "limitations": retrieval["selected_chunks"][0]["limitations"] = ["存在来源歧义，需核实"]
    if change == "writability": retrieval["selected_chunks"][0]["writability"] = {"requires_review": True}
    if change == "duplicate_identity":
        row = copy.deepcopy(retrieval["selected_chunks"][0])
        row["text"] = "测试街道组织应急演练。"
        retrieval["selected_chunks"].append(row)
    if change == "insufficient": retrieval["retrieval_status"] = "INSUFFICIENT"
    loaded = cache.load_source_substrate(retrieval, tmp_path)
    if change in {"duplicate_identity", "insufficient"}:
        with pytest.raises(FullArticleContractError) as before:
            baseline.build_full_draft_anchor_input(task(), retrieval)
        with pytest.raises(FullArticleContractError) as after:
            atomic.build_full_draft_anchor_input(task(), retrieval, source_substrate=loaded.substrate)
        assert before.value.code == after.value.code
    else:
        compare(baseline, task(), retrieval, loaded.substrate)


def test_cannot_use_substrate_to_upgrade_supplied_evidence(tmp_path, baseline):
    retrieval = sources()
    loaded = cache.load_source_substrate(retrieval, tmp_path)
    pack = baseline.build_atomic_evidence_pack(task(), retrieval, exact_source_spans=True)
    pack["evidence"][0]["semantic_state"] = "FORGED"
    with pytest.raises(FullArticleContractError, match="FULL_DRAFT_ATOMIC_INPUT_CHANGED"):
        atomic.build_full_draft_anchor_input(task(), retrieval,
            source_substrate=loaded.substrate, evidence_pack=pack)


def test_cache_write_failure_is_explicit(tmp_path):
    not_directory = tmp_path / "file"
    not_directory.write_text("test", encoding="utf-8")
    with pytest.raises(FullArticleContractError, match="SOURCE_SUBSTRATE_CACHE_(UNREADABLE|WRITE_FAILED)"):
        cache.load_source_substrate(sources(), not_directory)
