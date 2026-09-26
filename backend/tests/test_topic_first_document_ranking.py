"""P1: generic synthetic ranking regression against the exact PR #11 baseline.

Run with run_w3_8c_2a_offline.py; no real library, model or embedding is used.
"""
import ast
import copy
import json
from importlib import import_module
from pathlib import Path
import subprocess
from types import ModuleType, SimpleNamespace

import pytest

from app.services.retrieval import document_retriever as owner
from app.services.retrieval.official_document_classifier import classify_document_role


BASE_SHA = "b989aeb16fe38733136dfdd7a5b1074b094673ca"
OWNER_PATH = "backend/app/services/retrieval/document_retriever.py"
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def baseline():
    source = subprocess.check_output(["git", "show", f"{BASE_SHA}:{OWNER_PATH}"], cwd=ROOT)
    module = ModuleType("p1_committed_retrieval")
    exec(compile(source, f"{BASE_SHA}/{OWNER_PATH}", "exec"), module.__dict__)
    return module


def task(topic="星图归档流程", document_type="工作总结"):
    # Genre is intentionally separate from the title; no real business domain.
    return SimpleNamespace(title=topic, document_type=document_type,
                           business_domain="合成主题域", reference_scope="资料库")


def candidate(doc_id, title, similarity, document_type, *, domain="", index=0, role=None):
    metadata = {"document_type": document_type, "business_domain": domain}
    if role is not None:
        metadata["document_role"] = role
    return {"doc_id": doc_id, "chunk_id": f"{doc_id}:{index}", "chunk_index": index,
            "title": title, "chunk_text": title + "合成测试内容。",
            "score": similarity, "vector_score": similarity,
            "score_type": "vector_similarity", "metadata": metadata}


def contrasting_candidates(current_task):
    opposite = ("工作总结" if classify_document_role(document_type=current_task.document_type)
                == "expression_source" else "领导讲话")
    rows = [candidate("A", current_task.title, .94 - .02 * i, opposite,
                      domain=current_task.business_domain, index=i) for i in range(3)]
    rows += [candidate("B", current_task.title[:2], .84 - .02 * i,
                       current_task.document_type, index=i) for i in range(3)]
    rows.append(candidate("rejected", current_task.title, .40, current_task.document_type))
    return sorted(rows, key=lambda row: -row["vector_score"])


def aggregate(module, rows, current_task):
    return module._aggregate_documents(copy.deepcopy(rows), module.prepare_retrieval_request(current_task))


@pytest.mark.parametrize("topic", ["星图归档流程", "样本编码校核"])
@pytest.mark.parametrize("document_type", ["工作总结", "调研报告", "领导讲话"])
def test_strong_topic_precedes_compatible_genre(baseline, topic, document_type):
    current_task = task(topic, document_type)
    rows = [row for row in contrasting_candidates(current_task) if row["doc_id"] in {"A", "B"}]
    before, after = (aggregate(module, rows, current_task) for module in (baseline, owner))
    assert [row["document_id"] for row in before[:2]] == ["B", "A"]
    assert [row["document_id"] for row in after[:2]] == ["A", "B"]
    a, b = after[:2]
    for signal in ("semantic_aggregate", "title_topic_match", "content_topic_match", "business_domain_match"):
        assert a["score_components"][signal] > b["score_components"][signal]
    assert a["score_components"]["source_role_compatibility"] == -1
    assert b["score_components"]["source_role_compatibility"] == 1
    assert b["score_components"]["document_type_match"] == 1
    # Previously even a larger document_score lost to the hard role priority.
    assert before[1]["document_score"] > before[0]["document_score"]
    assert a["document_score"] > b["document_score"]


def test_role_weight_cannot_overturn_clear_topic_advantage(baseline):
    current_task = task()
    rows = [candidate("A", current_task.title, .84, "领导讲话", domain=current_task.business_domain),
            candidate("B", "星图归档", .82, "工作总结")]
    before = aggregate(baseline, rows, current_task)
    after = aggregate(owner, rows, current_task)
    # Removing the hard priority alone is insufficient for this second failure.
    assert before[0]["document_id"] == "B"
    assert before[0]["document_score"] > before[1]["document_score"]
    assert after[0]["document_id"] == "A"
    assert after[0]["document_score"] > after[1]["document_score"]


def test_clear_topic_gap_beats_all_secondary_preferences_combined():
    current_task = task()
    current_task.org_type = "合成机构"
    rows = [candidate("A", current_task.title, .90, "领导讲话"),
            candidate("B", current_task.title, .80, "工作总结")]
    rows[1]["metadata"]["org_type"] = current_task.org_type
    a, b = aggregate(owner, rows, current_task)
    assert a["document_id"] == "A" and b["document_id"] == "B"
    assert a["score_components"]["source_role_compatibility"] == -1
    for signal in ("source_role_compatibility", "document_type_match", "auxiliary_match"):
        assert b["score_components"][signal] == 1
    for signal in ("document_type_match", "auxiliary_match"):
        assert a["score_components"][signal] == 0
    # The 0.0625 semantic contribution exceeds the full 0.05 preference span.
    assert a["document_score"] > b["document_score"]


@pytest.mark.parametrize("semantic_gap", [0.0, .01])
@pytest.mark.parametrize("document_type", ["工作总结", "领导讲话"])
def test_source_role_still_breaks_close_topic_matches(semantic_gap, document_type):
    current_task = task(document_type=document_type)
    compatible = classify_document_role(document_type=document_type)
    opposite = "expression_source" if compatible == "fact_source" else "fact_source"
    rows = [candidate("A", current_task.title, .85 + semantic_gap, "其他", role=opposite),
            candidate("B", current_task.title, .85, "其他", role=compatible)]
    after = aggregate(owner, rows, current_task)
    assert [row["document_id"] for row in after] == ["B", "A"]
    b, a = after
    for signal in ("title_topic_match", "content_topic_match", "business_domain_match", "document_type_match"):
        assert a["score_components"][signal] == b["score_components"][signal]
    assert a["score_components"]["semantic_aggregate"] >= b["score_components"]["semantic_aggregate"]
    assert b["score_components"]["source_role_compatibility"] == 1
    assert a["score_components"]["source_role_compatibility"] == -1


def test_raw_gate_roles_and_search_call_parity_with_changed_selection(baseline, monkeypatch, tmp_path):
    current_task = task()
    rows = contrasting_candidates(current_task)
    untouched = copy.deepcopy(rows)
    results, call_logs = [], []
    for module in (baseline, owner):
        calls = []

        def search(query, **kwargs):
            calls.append((query, kwargs))
            return {"results": copy.deepcopy(rows), "raw_results_count": len(rows),
                    "retrieval_mode": "vector_only", "filtered_samples": [], "rebuilt_doc_ids": []}

        # Exercise the production search branch, including vector_only.
        monkeypatch.setattr(module, "search_detailed", search)
        results.append(module.retrieve_documents(current_task, persist_trace=False))
        call_logs.append(calls)
    before, after = results
    assert rows == untouched
    assert call_logs[0] == call_logs[1]
    assert len(call_logs[1]) == 1
    assert call_logs[1][0][1] == {"top_k": 200, "retrieval_mode": "vector_only"}
    for key in ("query_text", "query_fields", "positive_title", "negative_concepts",
                "raw_chunk_candidates", "retrieval_diagnostics", "relevance_threshold",
                "gate_result", "retrieval_status", "insufficient_reason"):
        assert before[key] == after[key], key
    old_docs = {row["document_id"]: row for row in aggregate(baseline, rows, current_task)}
    for row in aggregate(owner, rows, current_task):
        old = old_docs[row["document_id"]]
        for key in ("score_components", "passed_primary_gate", "gate_reasons", "negative_title_conflict",
                    "_requested_source_role", "_candidate_source_role", "_chunks", "candidate_chunk_count"):
            assert row[key] == old[key], (row["document_id"], key)
    assert before["primary_document_id"] == "B"
    assert after["primary_document_id"] == "A"
    assert before["auxiliary_document_ids"] == ["A"]
    assert after["auxiliary_document_ids"] == ["B"]
    assert len(before["selected_chunks"]) == len(after["selected_chunks"]) == 5
    for row in after["ranked_documents"]:
        old = next(item for item in before["ranked_documents"] if item["document_id"] == row["document_id"])
        assert row["gate_reasons"] == old["gate_reasons"]
        assert row["passed_primary_gate"] == old["passed_primary_gate"]

    # The actual PR #11 trace owner and persistence consume the new score as-is.
    from app.services.generation import full_draft_generation as route
    snapshot = copy.deepcopy(after)
    trace = route._build_full_draft_retrieval_trace(after)
    route._persist_material_traces(tmp_path, {route.RAG_TRACE_FILE: trace})
    assert json.loads((tmp_path / route.RAG_TRACE_FILE).read_bytes()) == trace
    assert after == snapshot
    assert [row["selection_status"] for row in trace["ranked_documents"]] == ["PRIMARY", "AUXILIARY", "REJECTED"]
    assert [row["raw_similarity"] for row in trace["raw_chunk_candidates"]] == [round(row["vector_score"], 6) for row in rows]
    for public, diagnostic in zip(after["ranked_documents"], trace["ranked_documents"]):
        for key in ("document_id", "rank", "document_score", "score_components", "gate_reasons", "passed_primary_gate"):
            assert public[key] == diagnostic[key]
        c = diagnostic["score_components"]
        expected_score = (.625 * c["semantic_aggregate"] + .180 * c["title_topic_match"]
                          + .060 * c["content_topic_match"] + .015 * c["business_domain_match"]
                          + .020 * c["source_role_compatibility"] + .005 * c["document_type_match"]
                          + .005 * c["auxiliary_match"] - .350 * c["negative_title_match"])
        assert diagnostic["document_score"] == pytest.approx(expected_score, abs=1e-6)
    for forbidden in ("chunk_text", "合成测试内容", "embedding", "api_key"):
        assert forbidden not in json.dumps(trace, ensure_ascii=False)
    (tmp_path / "p1_ranking_comparison.json").write_text(json.dumps({
        "before": route._build_full_draft_retrieval_trace(before), "after": trace,
    }, ensure_ascii=False, indent=2), encoding="utf-8")


@pytest.mark.parametrize("similarity,title,negative", [
    (.49, "星图归档流程", ""), (.50, "星图归档流程", ""),
    (.57, "异类", ""), (.58, "异类", ""),
    (.95, "星图归档流程旧版", "，排除旧版"),
])
def test_relevance_gate_boundaries_unchanged(baseline, similarity, title, negative):
    current_task = task("星图归档流程" + negative)
    rows = [candidate("A", title, similarity, "领导讲话")]
    before = aggregate(baseline, rows, current_task)[0]
    after = aggregate(owner, rows, current_task)[0]
    for key in ("score_components", "passed_primary_gate", "gate_reasons", "negative_title_conflict"):
        assert after[key] == before[key]


def test_deterministic_ties_do_not_depend_on_raw_candidate_order():
    current_task = task()
    rows = [candidate(name, current_task.title, .85, "工作总结") for name in ("b", "a", "c")]
    forward = aggregate(owner, rows, current_task)
    backward = aggregate(owner, rows[::-1], current_task)
    assert forward == backward
    assert [row["document_id"] for row in forward] == ["a", "b", "c"]


def test_only_document_aggregation_changes_in_production_owner():
    before = subprocess.check_output(["git", "show", f"{BASE_SHA}:{OWNER_PATH}"], cwd=ROOT)
    from multi_genre_scope import assert_multi_genre_content, assert_p1_core_unchanged, migration_base_source
    current = (ROOT / OWNER_PATH).read_bytes().replace(b"\r\n", b"\n")
    migration_base = migration_base_source(ROOT, OWNER_PATH)
    if current != migration_base:
        assert_multi_genre_content(ROOT, OWNER_PATH)
        assert_p1_core_unchanged(ROOT)
    # The original pre-P1 oracle still proves the P1 delta exactly. This task's
    # additional profile extraction is constrained above and by full payload
    # parity tests; no historical source or expected ranking has been replaced.
    trees = [ast.parse(source) for source in (before, migration_base)]
    for tree in trees:
        tree.body = [node for node in tree.body if not isinstance(node, ast.FunctionDef)
                     or node.name != "_aggregate_documents"]
    assert ast.dump(trees[0]) == ast.dump(trees[1])


@pytest.mark.parametrize("module_name,test_name", [
    ("test_rag_observability", "test_retrieval_business_code_and_protected_owners_unchanged"),
    ("test_production_task_assembly", "test_protected_owners_unchanged_from_task_base"),
    ("test_material_ingestion", "test_protected_generation_retrieval_and_legacy_owners_unchanged"),
    ("test_stable_material_assets", "test_production_and_fact_owners_remain_unchanged"),
])
def test_every_protected_guard_rejects_future_owner_changes(monkeypatch, module_name, test_name):
    # Simulate a one-byte edit in memory only; never mutate the approved owner.
    guard = getattr(import_module(module_name), test_name)
    read_bytes = Path.read_bytes

    def changed(path):
        data = read_bytes(path)
        return data + b"\n" if path == ROOT / OWNER_PATH else data

    monkeypatch.setattr(Path, "read_bytes", changed)
    with pytest.raises(AssertionError, match="differs from the exact P1-approved content"):
        guard()
