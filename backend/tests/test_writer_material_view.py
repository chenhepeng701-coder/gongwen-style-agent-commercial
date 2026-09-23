"""WRITER-VIEW-01: invented materials only; independent sent-string round trips."""
from dataclasses import asdict
import ast
import json
from pathlib import Path
import subprocess
import sys
import types

import pytest

from app.models.full_article import FullArticleContractError, canonical_json
from app.services.generation.full_draft_material import FullDraftMaterialPacket
from app.services.generation import material_digest, task_assembly, writing_reference_pack
from app.services.generation.official_document_drafting import build_full_draft_prompt
from app.services.generation.writer_material_view import build_writer_material_view
from test_material_digest_task_assembly import reference_pack, request
from test_source_substrate import sources
from writer_material_view_decoder import decode_materials


def sent_projection(value):
    # Pure representation cases exercise structures beyond today's strict Brief
    # schema. Only serialized wire data, never original objects, reaches decoder.
    return "## 合成写作材料\n\n```json\n" + canonical_json(value) + "\n```"


def synthetic(count=8):
    rows = []
    for i in range(count):
        rows.append({"evidence_id": f"EV-SYN-{i}", "section_id": "SEC-SYN",
            "source": {"document_id": "invented", "chunk_id": "synthetic-chunk"},
            "text": '合成单位尚未完成核验，计划于2029年前完成3项。\n保留“引号”、"双引号"、|,[]{}及```。',
            "semantic_state": "PLAN", "exact_values": {"count": 3, "year": 2029},
            "semantic_constraints": {"negated": True, "completed": False},
            "writability": {"fact": True, "result": False},
            "forbidden_detail_types": ["causality", "completion"],
            "limitations": {"record": {"limitations": [{"kind": "SOURCE_STATE_CONFLICT", "requires_review": True}]}},
            "source_constraints": {"source": {"scope": "only-this-source"}, "metadata": {"approved": False}}})
    # Lexical flag intentionally disagrees with contextual semantic constraint.
    ids = [r["evidence_id"] for r in rows]
    limits = []
    for row in rows:
        details = {key: row[key] for key in ("semantic_state", "semantic_constraints", "writability", "limitations", "source_constraints")}
        details["literal_signals"] = {"negated": False, "completed": True}
        limits.append({"evidence_ids": [row["evidence_id"]], "text": canonical_json(details)})
    brief = {"version": "material-brief-stage2-1", "input_sha256": "synthetic-only",
        "overview": "合成动作词导航", "matters": [{"matter_id": "READING-SYN", "title": "原文动作词索引：完成",
            "evidence_ids": ids, "summary": {"evidence_ids": ids, "text": rows[0]["text"]}, "limits": limits}],
        "uncertainties": [{"evidence_ids": ids, "issue": "跨年目标须复核", "blocks_strong_conclusion": True}]}
    return rows, brief


def assert_roundtrip(view, brief):
    before = canonical_json([view, brief])
    projection = build_writer_material_view(view, brief)
    sent = sent_projection(projection)
    decoded = decode_materials(sent)
    assert canonical_json(decoded) == before
    assert canonical_json([view, brief]) == before
    assert sent == sent_projection(build_writer_material_view(view, brief))
    return projection, sent


def test_complete_synthetic_contract_order_scope_states_and_signals():
    view, brief = synthetic()
    # A/B/A source ordering must survive grouping, without merging permissions.
    view[1]["section_id"] = "SEC-OTHER"
    view[2]["source"]["document_id"] = "invented-other"
    view[3]["writability"] = {"fact": False, "result": False}
    view[4]["semantic_state"] = "NEGATED"
    view[5]["semantic_state"] = "UNKNOWN"
    view[6]["exact_values"] = {"zero": 0, "target": "2029年以前", "decimal": "3.00"}
    view.reverse()
    brief["matters"][0]["limits"].reverse()
    projection, _ = assert_roundtrip(view, brief)
    assert len(projection["source_groups"]) == 3
    assert len(projection["rule_catalogue"]["writability"]) == 2
    assert projection["assembly_exceptions"]  # different original limits stay explicit


@pytest.mark.parametrize("field", ["section_id", "source", "text", "source_span",
                                  "exact_values", "semantic_state", "writability",
                                  "semantic_constraints", "limitations", "source_constraints",
                                  "forbidden_detail_types", "unknown_extension"])
def test_missing_null_empty_false_zero_and_types_never_conflate(field):
    view, brief = synthetic(9)
    values = [None, {}, [], False, 0, True, 1, 0.0]
    view[0].pop(field, None)
    for row, value in zip(view[1:], values):
        row[field] = value
    projection, _ = assert_roundtrip(view, brief)
    if field in {"section_id", "source", "text", "source_span"}:
        assert len(projection["source_groups"]) == 9
    if field in projection["rule_catalogue"]:
        # Both Evidence and distinct Assembly values belong to this field's
        # terminal pool; compare the complete typed union, not just row counts.
        expected = {canonical_json(row[field]) for row in view if field in row}
        expected.update(canonical_json(details[field]) for limit in brief["matters"][0]["limits"]
                        if field in (details := json.loads(limit["text"])))
        assert {canonical_json(v) for v in projection["rule_catalogue"][field].values()} == expected


def test_unknown_fields_and_same_named_task_differences_are_retained():
    view, brief = synthetic(3)
    view[0].update(literal_signals={"evidence_only": True}, extensions={"not_metadata": 0},
                   missing_fields=["user-data"], semantic_constraints_ref="user-data")
    view[0]["source"]["future_permissions"] = {"scope": [False, 0, None]}
    view[0]["source_constraints"] = {"nonempty": ["仅限合成来源甲"]}
    brief["future_root"] = {"reviewed": False}
    matter = brief["matters"][0]
    matter["future_matter"] = [0, False, None, {}, []]
    matter["summary"]["future_summary"] = {"unchanged": True}
    limit = matter["limits"][0]
    details = json.loads(limit["text"])
    details.update(unknown_detail={"do_not_drop": True}, semantic_constraints={"distinct": False}, literal_signals=None)
    details.pop("source_constraints")
    limit.update(text=canonical_json(details), future_limit={"approved": False})
    matter["limits"].append({"evidence_ids": [view[0]["evidence_id"]], "text": canonical_json({"literal_signals": 0})})
    assert_roundtrip(view, brief)


@pytest.mark.parametrize("kind", ["empty_limits", "plain_text", "noncanonical_json", "multi_id",
                                  "empty_details", "subset_summary", "different_summary"])
def test_general_current_brief_observations_are_lossless(kind):
    view, brief = synthetic(3)
    matter = brief["matters"][0]
    if kind == "empty_limits": matter["limits"] = []
    if kind == "plain_text": matter["limits"][0]["text"] = '不得把"计划"写成完成\n范围|保持。'
    if kind == "noncanonical_json": matter["limits"][0]["text"] = '{ "literal_signals" : false, "zero": 0 }'
    if kind == "multi_id": matter["limits"][0]["evidence_ids"] = matter["evidence_ids"][:2]
    if kind == "empty_details": matter["limits"][0]["text"] = "{}"
    if kind == "subset_summary": matter["summary"]["evidence_ids"] = matter["evidence_ids"][::-2]
    if kind == "different_summary": matter["summary"]["text"] = "独有说明：原限制仍然生效。"
    assert_roundtrip(view, brief)


def test_empty_view_and_empty_navigation():
    assert_roundtrip([], {"matters": [], "extra": None})


def test_detached_projection_does_not_share_mutable_originals():
    view, brief = synthetic()
    original = canonical_json([view, brief])
    output = build_writer_material_view(view, brief)
    output["source_groups"][0]["source"]["document_id"] = "mutated-result"
    output["rule_catalogue"]["writability"]["W1"].clear()
    assert canonical_json([view, brief]) == original


@pytest.mark.parametrize("bad", ["duplicate_id", "unknown_id", "missing_matter", "missing_summary",
                                "set_value", "tuple_value", "nonstring_key", "nan", "cycle"])
def test_unrepresentable_input_fails_with_content_free_contract_error(bad):
    view, brief = synthetic(2)
    if bad == "duplicate_id": view[1]["evidence_id"] = view[0]["evidence_id"]
    if bad == "unknown_id": brief["matters"][0]["limits"][0]["evidence_ids"] = ["EV-UNKNOWN"]
    if bad == "missing_matter": brief["matters"] = []
    if bad == "missing_summary": brief["matters"][0].pop("summary")
    if bad == "set_value": view[0]["extra"] = {"PRIVATE-MARKER"}
    if bad == "tuple_value": view[0]["extra"] = (False, 0)
    if bad == "nonstring_key": view[0]["extra"] = {0: "PRIVATE-MARKER"}
    if bad == "nan": view[0]["extra"] = float("nan")
    if bad == "cycle": view[0]["extra"] = view
    with pytest.raises(FullArticleContractError) as error:
        build_writer_material_view(view, brief)
    assert str(error.value) == "WRITER_MATERIAL_VIEW_UNREPRESENTABLE"


@pytest.mark.parametrize("damage", ["orphan", "wrong", "cross_field", "cycle", "short_row", "missing_id",
                                    "unknown_matter", "bad_limit_binding"])
def test_independent_decoder_rejects_damaged_sent_references(damage):
    view, brief = synthetic(2)
    projection = build_writer_material_view(view, brief)
    cells = projection["source_groups"][0]["evidence"][0]
    col = projection["evidence_columns"].index("writability_ref")
    if damage == "orphan": projection["rule_catalogue"]["writability"]["W999"] = {"unbound": True}
    if damage == "wrong": cells[col] = "W999"
    if damage == "cross_field": cells[col] = "C1"
    if damage == "cycle": cells[col] = {"writability_ref": "W1"}
    if damage == "short_row": cells.pop()
    if damage == "missing_id": projection["projection_contract"]["evidence_order"].pop()
    if damage == "unknown_matter": projection["assembly_exceptions"]["999"] = {}
    if damage == "bad_limit_binding": projection["assembly_exceptions"]["0"] = {"limits": [{"evidence_id": "EV-UNKNOWN"}]}
    with pytest.raises((AssertionError, KeyError, TypeError)):
        decode_materials(sent_projection(projection))


def test_large_synthetic_input_shares_rules_without_per_row_expansion():
    sizes = []
    for count in (12, 120, 1200):
        view, brief = synthetic(count)
        long_rule = {"instruction": "合成规则仅限本来源，不得扩张权限。" * 100}
        for row in view:
            row["writability"] = long_rule
        for limit in brief["matters"][0]["limits"]:
            details = json.loads(limit["text"])
            details["writability"] = long_rule
            limit["text"] = canonical_json(details)
        projection, sent = assert_roundtrip(view, brief)
        assert len(projection["rule_catalogue"]["writability"]) == 1
        assert sent.count(canonical_json(long_rule)) == 1
        assert not projection["assembly_exceptions"]
        sizes.append(len(sent))
    assert sizes[2] - sizes[1] < 350 * (1200 - 120)


def test_distinct_assembly_rules_are_shared_and_remain_separate_from_evidence():
    view, brief = synthetic(160)
    different = {"instruction": "合成来源有不同限制，不能当作已完成。" * 80, "approved": False}
    for limit in brief["matters"][0]["limits"]:
        details = json.loads(limit["text"])
        details["limitations"] = different
        limit["text"] = canonical_json(details)
    projection, sent = assert_roundtrip(view, brief)
    assert len(projection["rule_catalogue"]["limitations"]) == 2
    assert sent.count(canonical_json(different)) == 1
    assert all(limit["rule_refs"] == {"limitations": "L2"}
               for limit in projection["assembly_exceptions"]["0"]["limits"])


def test_rule_values_and_extensions_are_terminal_data_not_recursive_references():
    view, brief = synthetic(2)
    view[0]["limitations"] = {"writability_ref": "W1", "future_rule": {"limitations_ref": "L1"}}
    view[0]["unknown_extension"] = {"evidence_id": "external-literal-data", "ref": "C1"}
    assert_roundtrip(view, brief)


def test_legal_nonstandard_brief_through_formal_materials(tmp_path, reference_pack):
    retrieval = sources()
    packet = task_assembly.assemble_task(request(), retrieval,
        material_digest.load_material_digest(retrieval, tmp_path).digest).packet
    brief = json.loads(packet.brief_json)
    for i, matter in enumerate(brief["matters"]):
        matter["summary"]["text"] = "合成阅读辅助原话，不能提供新事实。"
        matter["limits"] = [{"evidence_ids": matter["evidence_ids"],
                             "text": '保留独有限制：范围|不扩张，"目标"不得写成成果。\n'}] if i % 2 else []
    packet = packet.with_brief(brief)  # Real current Brief validator, no bypass.
    with writing_reference_pack.use_writing_reference_pack(reference_pack):
        prompt = build_full_draft_prompt(packet)
    view, restored = decode_materials(prompt["user"])
    assert canonical_json(view) == canonical_json(packet.evidence_writing_view())
    assert canonical_json(restored) == packet.brief_json


def test_formal_prompt_roundtrip_and_backend_objects_unchanged(tmp_path, reference_pack):
    retrieval = sources()
    digest = material_digest.load_material_digest(retrieval, tmp_path).digest
    packet = task_assembly.assemble_task(request(), retrieval, digest).packet
    before = canonical_json({"packet": asdict(packet), "digest": packet.digest,
        "candidate": packet.candidate_input(), "evidence": packet.evidence_writing_view(),
        "brief_input": packet.brief_input_sha256, "bound_brief": packet.bound_brief()})
    root = Path(__file__).resolve().parents[2]
    source = subprocess.check_output(["git", "show", "e5cc40b4c89edfc3f0d9dd30f630e7b6ee0844f3:backend/app/services/generation/full_draft_material.py"], cwd=root)
    old_module = types.ModuleType("writer_view_test_baseline")
    old_module.__file__ = str(root / "backend/app/services/generation/full_draft_material.py")
    sys.modules[old_module.__name__] = old_module
    try:
        exec(compile(source, "committed/full_draft_material.py", "exec"), old_module.__dict__)
        old_packet = old_module.FullDraftMaterialPacket(**asdict(packet))
        with writing_reference_pack.use_writing_reference_pack(reference_pack):
            old_prompt = build_full_draft_prompt(old_packet)
            prompt = build_full_draft_prompt(packet)
            assert prompt == build_full_draft_prompt(packet)
            assert packet.materials_markdown() in prompt["user"]
        view, brief = decode_materials(prompt["user"])
        assert canonical_json(view) == canonical_json(old_packet.evidence_writing_view())
        assert canonical_json(brief) == old_packet.brief_json
        assert packet.brief_json not in prompt["user"]
        assert canonical_json(packet.evidence_writing_view()) not in prompt["user"]
        assert prompt["system"] == old_prompt["system"]
        # Remaining four material blocks stay byte-for-byte the same.
        marker = "## 原时间风险 Overlay"
        assert prompt["user"].split(marker, 1)[1] == old_prompt["user"].split(marker, 1)[1]
        # Common definitions cost a fixed number of characters even for a tiny
        # fixture. Do not enforce the frozen large case's reduction ratio here.
        assert len(prompt["user"]) < len(old_prompt["user"])
        assert asdict(packet) == asdict(old_packet)
        assert packet.candidate_input() == old_packet.candidate_input()
        assert packet.digest == old_packet.digest
    finally:
        sys.modules.pop(old_module.__name__, None)
    assert before == canonical_json({"packet": asdict(packet), "digest": packet.digest,
        "candidate": packet.candidate_input(), "evidence": packet.evidence_writing_view(),
        "brief_input": packet.brief_input_sha256, "bound_brief": packet.bound_brief()})


def test_entry_uses_new_projection_and_never_falls_back(tmp_path, reference_pack, monkeypatch):
    retrieval = sources()
    packet = task_assembly.assemble_task(request(), retrieval,
        material_digest.load_material_digest(retrieval, tmp_path).digest).packet
    def invalid_view(self):
        return [{"evidence_id": "EV-UNBOUND"}]
    monkeypatch.setattr(FullDraftMaterialPacket, "evidence_writing_view", invalid_view)
    with writing_reference_pack.use_writing_reference_pack(reference_pack):
        with pytest.raises(FullArticleContractError, match="WRITER_MATERIAL_VIEW_UNREPRESENTABLE"):
            build_full_draft_prompt(packet)


def test_only_materials_method_changes_in_existing_production_owner():
    root = Path(__file__).resolve().parents[2]
    path = "backend/app/services/generation/full_draft_material.py"
    before = subprocess.check_output(["git", "show", "e5cc40b4c89edfc3f0d9dd30f630e7b6ee0844f3:" + path], cwd=root).decode("utf-8")
    after = (root / path).read_text(encoding="utf-8")
    def without_materials(source):
        tree = ast.parse(source)
        packet_class = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "FullDraftMaterialPacket")
        packet_class.body = [node for node in packet_class.body
                             if not (isinstance(node, ast.FunctionDef) and node.name == "materials_markdown")]
        return ast.dump(tree, include_attributes=False)
    assert without_materials(before) == without_materials(after)
    helper = (root / "backend/app/services/generation/writer_material_view.py").read_text(encoding="utf-8")
    imports = [node.module for node in ast.walk(ast.parse(helper)) if isinstance(node, ast.ImportFrom)]
    assert imports == ["copy", "app.models.full_article"]
    assert "tmp/" not in helper and "experiments" not in helper
