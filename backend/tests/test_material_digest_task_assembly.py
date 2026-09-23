"""W3-8B: synthetic materials, committed baseline, real offline owners.

Sentinels fail on calls; they never return fake Brief/Writer/Provider results.
Run with -s to capture metrics; detailed field comparisons go to pytest tmp_path.
"""
import copy
from dataclasses import asdict, replace
import json
from pathlib import Path
import socket
import subprocess
import types

import pytest

from app import config
from app.models.full_article import FullArticleContractError, canonical_json, text_sha
from app.models.generation import ArticleGenRequest
from app.services.generation import atomic_evidence as atomic
from app.services.generation import material_digest as cache
from app.services.generation import task_assembly as assembly
from app.services.generation import writing_reference_pack as references
from app.services.generation.full_draft_material import FullDraftMaterialPacket, validate_material_brief
from app.services.generation.generator import build_official_draft_task, derive_final_delivery_length_gate
from app.services.generation.official_document_drafting import build_full_draft_prompt
from test_source_substrate import sources
from writer_material_view_decoder import decode_materials


BASE_SHA = "54d0a88a07c0321ba0dab5e8ab2dcf3bc69ff285"
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def committed():
    source = subprocess.check_output([
        "git", "show", BASE_SHA + ":backend/app/services/generation/atomic_evidence.py"], cwd=ROOT)
    module = types.ModuleType("w3_8b_committed_atomic")
    exec(compile(source, BASE_SHA + "/atomic_evidence.py", "exec"), module.__dict__)
    return module


@pytest.fixture(autouse=True)
def no_models(monkeypatch):
    from app.services.llm import factory
    from app.services.llm.openai_compatible_provider import OpenAICompatibleProvider
    from app.services.generation import generator, full_draft_generation
    counts = {"brief": 0, "writer": 0, "provider": 0, "network": 0}

    def forbid(kind):
        def fail(*args, **kwargs):
            counts[kind] += 1
            pytest.fail("W3-8B forbidden call: " + kind)
        return fail

    monkeypatch.setattr(FullDraftMaterialPacket, "brief_prompt", forbid("brief"))
    monkeypatch.setattr(full_draft_generation, "generate_full_draft", forbid("writer"))
    monkeypatch.setattr(generator, "generate_flow3_v33_structured_draft", forbid("writer"))
    for module in (factory, generator, full_draft_generation):
        monkeypatch.setattr(module, "get_llm_provider", forbid("provider"))
    monkeypatch.setattr(factory, "get_llm_provider_from_config", forbid("provider"))
    monkeypatch.setattr(OpenAICompatibleProvider, "__init__", forbid("provider"))
    monkeypatch.setattr(OpenAICompatibleProvider, "generate_text", forbid("provider"))
    monkeypatch.setattr(socket.socket, "connect", forbid("network"))
    monkeypatch.setattr(socket.socket, "connect_ex", forbid("network"))
    yield counts
    assert counts == dict.fromkeys(counts, 0)


@pytest.fixture
def reference_pack(monkeypatch):
    # Existing explicitly synthetic fixture; no user library or default config.
    path = Path(__file__).with_name("fixtures") / "writing_reference_pack_synthetic.json"
    monkeypatch.setattr(references, "get_settings", lambda: config.Settings(
        writing_reference_pack_path=str(path.resolve())))
    return references.load_writing_reference_pack()


def request(title="2026年安全检查工作总结", **kwargs):
    return ArticleGenRequest(title=title, document_type="工作总结", target_words=1200, **kwargs)


def differences(left, right, path="$"):
    """Enumerate actual field values, including omitted keys, not just counts."""
    if type(left) is not type(right):
        return [{"field": path, "before": left, "after": right}]
    if isinstance(left, dict):
        result = []
        for key in sorted(left.keys() | right.keys()):
            if key not in left or key not in right:
                result.append({"field": path + "." + key,
                               "before": left.get(key), "after": right.get(key),
                               "presence_before": key in left, "presence_after": key in right})
            else:
                result.extend(differences(left[key], right[key], path + "." + key))
        return result
    if isinstance(left, list):
        if len(left) != len(right):
            return [{"field": path, "before": left, "after": right}]
        return [diff for i, (a, b) in enumerate(zip(left, right))
                for diff in differences(a, b, f"{path}[{i}]")]
    return [] if left == right else [{"field": path, "before": left, "after": right}]


def compare(committed, req, retrieval, loaded, output_dir, name):
    task = build_official_draft_task(req)
    old = committed.build_full_draft_anchor_input(task, retrieval)
    result = assembly.assemble_task(req, retrieval, loaded.digest)
    new = json.loads(result.anchor_input_json)
    fields = {**asdict(task), "style": req.style, "scene": req.scene,
              "hard_max": derive_final_delivery_length_gate(task.target_words)["max_words"]}
    before = FullDraftMaterialPacket.from_authorized_input(fields, old)
    packet = replace(result.packet, brief_json="")
    compared = {
        "anchor_input": (old, new),
        "pre_brief_packet": (asdict(before), asdict(packet)),
        "evidence_writing_view": (before.evidence_writing_view(), packet.evidence_writing_view()),
        "overlay": (before.overlay(), packet.overlay()),
        "candidate_input": (before.candidate_input(), packet.candidate_input()),
    }
    diffs = {key: differences(a, b) for key, (a, b) in compared.items()}
    (output_dir / (name + "-comparison.json")).write_text(canonical_json({
        "input": {"task": fields, "retrieval": retrieval},
        "comparisons": {key: {"before": a, "after": b, "differences": diffs[key]}
                        for key, (a, b) in compared.items()},
        "assembled_brief": json.loads(result.packet.brief_json),
    }), encoding="utf-8")
    assert not any(diffs.values()), canonical_json(diffs)
    brief = validate_material_brief(json.loads(result.packet.brief_json), new["registry"], packet.brief_input_sha256)
    for matter in brief["matters"]:
        expected = list(dict.fromkeys(new["registry"][eid]["fact"]["fact"] for eid in matter["evidence_ids"]))
        assert matter["summary"]["text"] == "\n".join(expected)
        for limit in matter["limits"]:
            eid, = limit["evidence_ids"]
            assert json.loads(limit["text"])["semantic_state"] == new["registry"][eid]["fact"]["semantic_state"]
    return result, diffs


def test_abcdef_contract_and_metrics(tmp_path, committed, reference_pack, no_models):
    retrieval = sources()
    original = copy.deepcopy(retrieval)
    req = request()
    first = cache.load_material_digest(retrieval, tmp_path)
    a, da = compare(committed, req, retrieval, first, tmp_path, "A")
    second = cache.load_material_digest(retrieval, tmp_path)
    b, db = compare(committed, req, retrieval, second, tmp_path, "B")
    changed_req = request("2028年宣传培训工作总结", style="简洁", scene="年度汇报")
    third = cache.load_material_digest(retrieval, tmp_path)
    c, dc = compare(committed, changed_req, retrieval, third, tmp_path, "C")
    changed = copy.deepcopy(retrieval)
    changed["selected_chunks"][0]["text"] += "测试街道开展应急演练。"
    fourth = cache.load_material_digest(changed, tmp_path)
    d, dd = compare(committed, req, changed, fourth, tmp_path, "D")
    assert not first.cache_hit and second.cache_hit and third.cache_hit and not fourth.cache_hit
    assert first.digest == second.digest == third.digest
    assert fourth.digest.cache_key != first.digest.cache_key
    assert a.packet == b.packet
    assert a.packet.task_json != c.packet.task_json
    assert a.packet.overlay() != c.packet.overlay()
    assert a.packet.brief_input_sha256 != c.packet.brief_input_sha256
    assert a.packet.brief_json != c.packet.brief_json
    assert json.loads(a.anchor_input_json)["atomic_evidence"]["required_evidence"] != json.loads(c.anchor_input_json)["atomic_evidence"]["required_evidence"]
    assert a.packet.registry_json == c.packet.registry_json
    assert retrieval == original
    # Build the REAL unchanged Writer prompt with the validated reference loader.
    # This is pure preparation: neither Writer nor Brief model is invoked.
    for name, result in zip("ABCD", (a, b, c, d)):
        with references.use_writing_reference_pack(reference_pack):
            prompt = build_full_draft_prompt(result.packet)
        evidence_view, task_view = decode_materials(prompt["user"])
        assert canonical_json(task_view) == result.packet.brief_json
        assert canonical_json(evidence_view) == canonical_json(result.packet.evidence_writing_view())
        assert result.packet.brief_json not in prompt["user"]
        assert canonical_json(result.packet.evidence_writing_view()) not in prompt["user"]
        assert canonical_json(result.packet.overlay()) in prompt["user"]
        assert result.packet.restrictions_json in prompt["user"]
        assert result.packet.task_json in prompt["user"]
        assert canonical_json(reference_pack.writer_payload()) in prompt["user"]
        (tmp_path / (name + "-writer-preparation.json")).write_text(canonical_json(prompt), encoding="utf-8")
    task_diffs = differences({"anchors": json.loads(a.anchor_input_json), "packet": asdict(a.packet)},
                             {"anchors": json.loads(c.anchor_input_json), "packet": asdict(c.packet)})
    source_diffs = differences(json.loads(a.anchor_input_json), json.loads(d.anchor_input_json))
    (tmp_path / "cross_scenario_differences.json").write_text(canonical_json({
        "A_to_C": {"reason": "Current task title/year/style/scene and dependent selection/overlay/input binding rebuilt", "fields": task_diffs},
        "A_to_D": {"reason": "Added source sentence; both paths re-extract and admit current source", "fields": source_diffs},
    }), encoding="utf-8")
    registries = [json.loads(result.packet.registry_json) for result in (a, b, c, d)]
    fact_diffs = [diff for ds in (da, db, dc, dd) for diff in ds["anchor_input"]]
    assigned = [[eid for matter in json.loads(result.packet.brief_json)["matters"] for eid in matter["evidence_ids"]]
                for result in (a, b, c, d)]
    payload = json.loads((tmp_path / "digest" / (first.digest.cache_key + ".json")).read_bytes())["payload"]
    frozen = set(payload) - {"version", "substrate_key", "rules_sha256", "reading_units"}
    for unit in payload["reading_units"]:
        frozen |= set(unit) - {"source_span", "actions", "literal_signals"}
        frozen |= set(unit["literal_signals"]) - set(cache.SIGNAL_FIELDS)
    metrics = {
        "MATERIAL_DIGEST_REUSABLE": "YES" if first.digest == second.digest == third.digest else "NO",
        "SECOND_USE_CACHE_HIT": second.cache_hit,
        "TASK_CHANGE_REUSES_DIGEST": third.cache_hit,
        "SOURCE_CHANGE_INVALIDATES_DIGEST": not fourth.cache_hit and fourth.digest.cache_key != first.digest.cache_key,
        "TASK_DEPENDENT_STATE_REBUILT_REALTIME": bool(task_diffs),
        "SOURCE_FACT_LOSS_COUNT": len(fact_diffs),
        "SEMANTIC_STATE_DRIFT_COUNT": len([diff for diff in fact_diffs if "semantic" in diff["field"]]),
        "ILLEGAL_EVIDENCE_ID_COUNT": sum(len(set(ids) - reg.keys()) for ids, reg in zip(assigned, registries)),
        "UNEXPLAINED_EVIDENCE_OMISSION_COUNT": sum(len(reg.keys() - set(ids)) for ids, reg in zip(assigned, registries)),
        "TASK_DEPENDENT_FIELDS_FROZEN_IN_DIGEST": len(frozen),
        "REALTIME_BRIEF_MODEL_CALL_COUNT_IN_EXPERIMENT": no_models["brief"],
        "PROJECT_MODEL_CALL_COUNT": no_models["provider"],
        "WRITER_CALL_COUNT": no_models["writer"],
        "DIGEST_FIRST_BUILD_MS": first.elapsed_ms, "DIGEST_CACHE_LOAD_MS": second.elapsed_ms,
        "TASK_ASSEMBLY_MS": a.elapsed_ms,
        "cases": {name: {"registry_sha256": result.packet.registry_sha256,
                         "evidence_ids": list(reg),
                         "states": {eid: row["fact"]["semantic_state"] for eid, row in reg.items()},
                         "differences": ds,
                         "unavailable": json.loads(result.packet.unavailable_json),
                         "discarded": json.loads(result.anchor_input_json)["atomic_evidence"]["discarded_evidence"]}
                  for name, result, reg, ds in zip("ABCD", (a, b, c, d), registries, (da, db, dc, dd))},
    }
    (tmp_path / "metrics.json").write_text(canonical_json(metrics), encoding="utf-8")
    print("W3_8B_METRICS=" + canonical_json({key: value for key, value in metrics.items() if key != "cases"}))


def test_hit_reuses_actual_reading_work(tmp_path, monkeypatch):
    first = cache.load_material_digest(sources(), tmp_path)
    def forbidden(*args, **kwargs):
        pytest.fail("Digest cache hit recomputed reading units")
    monkeypatch.setattr(cache, "_reading_units", forbidden)
    monkeypatch.setattr(atomic, "_source_fragments", forbidden)
    loaded = cache.load_material_digest(sources(), tmp_path)
    assert loaded.cache_hit and loaded.digest == first.digest
    result = assembly.assemble_task(request(), sources(), loaded.digest)
    assert result.packet.bound_brief()
    view = loaded.digest.reading_index()
    view.clear()
    assert loaded.digest.reading_index()


@pytest.mark.parametrize("change", ["role", "coverage", "order", "selection", "overlay", "policy", "status"])
def test_task_state_is_never_reused(tmp_path, committed, change):
    retrieval = sources()
    first = cache.load_material_digest(retrieval, tmp_path)
    before, _ = compare(committed, request(), retrieval, first, tmp_path, "before")
    if change == "role":
        retrieval["primary_document_id"] = "synthetic-aux"
        retrieval["auxiliary_document_ids"] = ["synthetic-main"]
    if change == "coverage":
        for chunk in retrieval["selected_chunks"]:
            chunk["coverage_role"] = "next_step"
            chunk["metadata"]["content_role"] = "next_step"
    if change == "order": retrieval["selected_chunks"].reverse()
    if change == "selection": retrieval["auxiliary_document_ids"] = []
    if change == "overlay": retrieval["time_risk_overlay"] = {"findings": [], "current_task": "changed"}
    if change == "policy": retrieval["source_policy"] = {"instruction": "仅供核实，保留原限制"}
    if change == "status": retrieval["retrieval_status"] = "INSUFFICIENT"
    second = cache.load_material_digest(retrieval, tmp_path)
    assert second.cache_hit and second.digest == first.digest
    if change == "status":
        with pytest.raises(FullArticleContractError, match="FULL_DRAFT_MATERIAL_UNAVAILABLE"):
            assembly.assemble_task(request(), retrieval, second.digest)
    else:
        after, _ = compare(committed, request(), retrieval, second, tmp_path, "after")
        if change == "selection":
            # This fixture's auxiliary atoms already fail primary-type-gap
            # admission. Removing them changes the selection/rejection record,
            # correctly leaving the authorized packet unchanged in BOTH paths.
            assert before.anchor_input_json != after.anchor_input_json
            assert before.packet == after.packet
        elif change != "order":
            assert before.packet != after.packet


@pytest.mark.parametrize("field,value", [
    ("text", "测试街道组织安全检查。测试街道开展培训。"),
    ("chunk_id", "new-chunk"), ("document_id", "new-document"),
    ("source_title", "来源标题改变"), ("section_index", 200),
    ("section_heading", "来源章节改变"), ("evidence_scope_id", "scope-updated"),
    ("limitations", ["仅供核验"]), ("writability", {"requires_review": True}),
    ("metadata", {"title": "来源元数据改变"}),
])
def test_effective_source_change_invalidates(tmp_path, field, value):
    retrieval = sources()
    first = cache.load_material_digest(retrieval, tmp_path)
    retrieval["selected_chunks"][0][field] = value
    with pytest.raises(FullArticleContractError, match="SOURCE_SUBSTRATE_INPUT_CHANGED"):
        assembly.assemble_task(request(), retrieval, first.digest)
    second = cache.load_material_digest(retrieval, tmp_path)
    assert not second.cache_hit and first.digest.cache_key != second.digest.cache_key


@pytest.mark.parametrize("damage", ["json", "checksum", "task", "state", "missing_span", "signal", "action", "binding", "unit_type"])
def test_corrupt_or_task_polluted_digest_fails_closed(tmp_path, damage):
    loaded = cache.load_material_digest(sources(), tmp_path)
    path = tmp_path / "digest" / (loaded.digest.cache_key + ".json")
    value = json.loads(path.read_bytes())
    if damage == "json":
        path.write_bytes(b"{broken")
    else:
        payload = value["payload"]
        if damage == "checksum": value["sha256"] = "bad"
        if damage == "task": payload["task"] = {"title": "frozen task"}
        if damage == "state": payload["reading_units"][0]["semantic_state"] = "COMPLETED"
        if damage == "missing_span": payload["reading_units"].pop()
        if damage == "signal": payload["reading_units"][0]["literal_signals"]["negation_markers"] = ["forged"]
        if damage == "action": payload["reading_units"][0]["actions"] = [{"offset": -1, "text": "fake"}]
        if damage == "binding": payload["substrate_key"] = "other-source"
        if damage == "unit_type": payload["reading_units"] = [None]
        if damage != "checksum": value["sha256"] = text_sha(canonical_json(payload))
        path.write_text(canonical_json(value), encoding="utf-8")
    with pytest.raises(FullArticleContractError, match="MATERIAL_DIGEST_CACHE_INVALID"):
        cache.load_material_digest(sources(), tmp_path)


def test_digest_rule_change_invalidates(tmp_path, monkeypatch):
    first = cache.load_material_digest(sources(), tmp_path)
    monkeypatch.setattr(cache, "_rules_sha256", lambda: "new-revision")
    second = cache.load_material_digest(sources(), tmp_path)
    assert not second.cache_hit and second.digest.cache_key != first.digest.cache_key
    with pytest.raises(FullArticleContractError, match="MATERIAL_DIGEST_RULES_CHANGED"):
        assembly.assemble_task(request(), sources(), first.digest)


def test_raw_source_and_qualifiers_are_preserved(tmp_path):
    retrieval = sources()
    loaded = cache.load_material_digest(retrieval, tmp_path)
    assert sorted(row["text"] for row in json.loads(loaded.digest.substrate.sources_json)) == sorted(
        row["text"] for row in retrieval["selected_chunks"])
    units = loaded.digest.reading_index()
    partial = next(unit for span, unit in units.items() if "基本完成" in span)
    assert "基本完成" in partial["source_span"]
    assert all("semantic_state" not in unit for unit in units.values())
    assert any("尚未" in unit["literal_signals"]["negation_markers"] for unit in units.values())


def test_no_usable_material_keeps_existing_failure(tmp_path, committed):
    retrieval = sources()
    for chunk in retrieval["selected_chunks"]:
        chunk["evidence_scope_id"] = "GLOBAL"
    loaded = cache.load_material_digest(retrieval, tmp_path)
    with pytest.raises(FullArticleContractError) as before:
        committed.build_full_draft_anchor_input(build_official_draft_task(request()), retrieval)
    with pytest.raises(FullArticleContractError) as after:
        assembly.assemble_task(request(), retrieval, loaded.digest)
    assert before.value.code == after.value.code == "FULL_DRAFT_NO_USABLE_ANCHORS"


@pytest.mark.parametrize("change", ["limits", "usage", "scope", "conflicting_identity"])
def test_source_authorization_and_restrictions_remain_owned_by_atomic(tmp_path, committed, change):
    retrieval = sources()
    row = retrieval["selected_chunks"][0]
    if change == "limits":
        row["limitations"] = ["仅供核实，不得补全结果"]
        row["metadata"]["limitation"] = "未经确认"
    if change == "usage": row["writability"] = {"requires_review": True, "instruction": "保留来源歧义"}
    if change == "scope": row["evidence_scope_id"] = "EXPLICIT-SOURCE-SCOPE"
    if change == "conflicting_identity":
        duplicate = copy.deepcopy(row)
        duplicate["text"] = "测试街道开展应急演练。"
        retrieval["selected_chunks"].append(duplicate)
    loaded = cache.load_material_digest(retrieval, tmp_path)
    if change == "conflicting_identity":
        with pytest.raises(FullArticleContractError) as before:
            committed.build_full_draft_anchor_input(build_official_draft_task(request()), retrieval)
        with pytest.raises(FullArticleContractError) as after:
            assembly.assemble_task(request(), retrieval, loaded.digest)
        assert before.value.code == after.value.code
    else:
        compare(committed, request(), retrieval, loaded, tmp_path, change)


def test_cards_are_dynamic_and_not_one_per_evidence(tmp_path, committed):
    retrieval = sources()
    retrieval["selected_chunks"][0]["text"] += "测试街道组织消防培训。"
    loaded = cache.load_material_digest(retrieval, tmp_path)
    result, _ = compare(committed, request(), retrieval, loaded, tmp_path, "grouping")
    brief = json.loads(result.packet.brief_json)
    group = next(matter for matter in brief["matters"] if matter["title"] == "原文动作词索引：组织")
    assert "消防培训" in group["summary"]["text"] and "安全检查" in group["summary"]["text"]
    assert len(group["evidence_ids"]) >= 2
    assert len(brief["matters"]) < len(json.loads(result.packet.registry_json))


@pytest.mark.parametrize("title", ["安全检查工作总结", "2026至2028年安全检查工作总结"])
def test_missing_and_multiple_task_years_are_recomputed(tmp_path, committed, title):
    loaded = cache.load_material_digest(sources(), tmp_path)
    result, _ = compare(committed, request(title), sources(), loaded, tmp_path, "ambiguous-year")
    assert any("TASK_TIME_UNKNOWN_OR_AMBIGUOUS" in finding["reasons"] for finding in result.packet.overlay()["findings"])
    assert json.loads(result.packet.brief_json)["uncertainties"]


@pytest.mark.parametrize("overlay", [[], {"findings": "not-a-list"}, {"findings": [{"issue": "missing binding"}]}])
def test_uninterpretable_overlay_has_explicit_error(tmp_path, overlay):
    retrieval = sources()
    retrieval["time_risk_overlay"] = overlay
    loaded = cache.load_material_digest(retrieval, tmp_path)
    with pytest.raises(FullArticleContractError, match="TASK_ASSEMBLY_OVERLAY_UNINTERPRETABLE"):
        assembly.assemble_task(request(), retrieval, loaded.digest)


def test_foreign_id_is_rejected_instead_of_pruned(tmp_path):
    retrieval = sources()
    retrieval["prohibited_inference"] = ["需复核 EV-FOREIGN"]
    loaded = cache.load_material_digest(retrieval, tmp_path)
    with pytest.raises(FullArticleContractError, match="MATERIAL_BRIEF_OUTBOUND_ID_INVALID"):
        assembly.assemble_task(request(), retrieval, loaded.digest)


def test_cache_io_failure_is_explicit(tmp_path):
    (tmp_path / "digest").write_text("not a directory", encoding="utf-8")
    with pytest.raises(FullArticleContractError, match="MATERIAL_DIGEST_CACHE_(WRITE_FAILED|UNREADABLE)"):
        cache.load_material_digest(sources(), tmp_path)


def test_production_files_unchanged_and_no_experiment_imports():
    for name in ("backend/app/routers/generation.py", "backend/app/services/generation/generator.py",
                 "backend/app/services/generation/atomic_evidence.py",
                 "backend/app/services/generation/official_document_drafting.py"):
        # W3-8C-3 explicitly connects full_draft_generation to Task Assembly;
        # that authorized boundary now has production-path contract coverage.
        # WRITER-VIEW-01 exempts only full_draft_material.py presentation;
        # independent wire decoding and complete backend comparison cover it.
        old = subprocess.check_output(["git", "show", BASE_SHA + ":" + name], cwd=ROOT).decode("utf-8")
        assert (ROOT / name).read_text(encoding="utf-8") == old.replace("\r\n", "\n")
        assert "material_digest" not in old and "task_assembly" not in old
