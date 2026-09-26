"""Genre profile extraction parity against the task's actual production base."""
import ast
import copy
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import subprocess
from types import ModuleType, SimpleNamespace

import pytest

from app.services.generation.genres.report_retrieval import REPORT_RETRIEVAL_PROFILE
from app.services.generation.genres.work_summary_retrieval import (
    LEGACY_RETRIEVAL_PROFILE, WORK_SUMMARY_RETRIEVAL_PROFILE,
)
from app.services.retrieval import document_retriever as owner


BASE_SHA = "0d789f7b563acd6f95b71b795d18fbf5a5072651"
ROOT = Path(__file__).resolve().parents[2]
OWNER_PATH = "backend/app/services/retrieval/document_retriever.py"


@pytest.fixture(scope="module")
def baseline_source():
    return subprocess.check_output(["git", "show", f"{BASE_SHA}:{OWNER_PATH}"], cwd=ROOT)


@pytest.fixture(scope="module")
def baseline(baseline_source):
    module = ModuleType("multi_genre_committed_retrieval")
    exec(compile(baseline_source, f"{BASE_SHA}/{OWNER_PATH}", "exec"), module.__dict__)
    return module


def task(document_type="工作总结"):
    return SimpleNamespace(title="合成星图归档", document_type=document_type,
                           business_domain="合成业务", reference_scope="资料库")


def candidates(count):
    rows = []
    roles = ("result", "measure", "problem", "background", "progress", "responsibility", "data", "mixed")
    headings = ("总体进展", "主要做法", "存在问题", "资料背景", "成效", "下一步安排", "数据情况", "综合情况")
    for doc_id, budget, score in (("primary", count, .96), ("auxiliary", 4, .88)):
        for index in range(budget):
            text = "合成星图归档资料。" + ("已经完成了台账更新。" if index % 3 else "拟开展合成校验。")
            if index % 11 == 0:
                text += "校验了X个条目。"
            metadata = {"document_type": "工作总结", "document_role": "fact_source",
                        "business_domain": "合成业务", "content_role": roles[index % len(roles)],
                        "section_level": 1 + index % 2, "section_index": index // 2,
                        "section_heading": headings[index % len(headings)],
                        "section_path": ["合成星图归档", headings[index % len(headings)]]}
            rows.append({"doc_id": doc_id, "chunk_id": f"{doc_id}:{index}", "chunk_index": index,
                         "title": "合成星图归档", "chunk_text": text, "metadata": metadata,
                         "vector_score": score - .003 * index, "score": score - .003 * index})
    return rows


def run(module, rows, current_task, profile=None):
    calls = []

    def search(query, **kwargs):
        calls.append((query, kwargs))
        return {"results": copy.deepcopy(rows), "raw_results_count": len(rows),
                "retrieval_mode": "vector_only", "filtered_samples": [], "rebuilt_doc_ids": []}

    kwargs = {"retrieval_profile": profile} if profile is not None else {}
    result = module.retrieve_documents(current_task, search_fn=search, persist_trace=False, **kwargs)
    result.pop("request_time")
    return result, calls


@pytest.mark.parametrize("count", [0, 1, 6, 8, 9, 12, 24, 40])
def test_work_summary_full_retrieval_contract_matches_base(baseline, count):
    rows = candidates(count)
    current_task = task()
    before, old_calls = run(baseline, rows, current_task)
    after, new_calls = run(owner, rows, current_task, WORK_SUMMARY_RETRIEVAL_PROFILE)
    assert after == before
    assert new_calls == old_calls


@pytest.mark.parametrize("document_type", ["调研报告", "领导讲话", "其他文种"])
def test_legacy_relevance_only_callers_preserve_base_contract(baseline, document_type):
    rows = candidates(30)
    before, _ = run(baseline, rows, task(document_type))
    after, _ = run(owner, rows, task(document_type), LEGACY_RETRIEVAL_PROFILE)
    assert after == before


def test_offline_report_profile_is_not_selected_by_current_production_route(baseline):
    from app.models.generation import ArticleGenRequest
    from app.services.generation.generator import build_official_draft_task
    from app.services.generation.genres.registry import retrieval_profile_for, resolve_genre

    request = ArticleGenRequest(title="合成星图归档", document_type="汇报材料",
                                generation_route="current", business_domain="合成业务")
    current_task = build_official_draft_task(request)
    rows = candidates(30)
    before, _ = run(baseline, rows, current_task)
    default, _ = run(owner, rows, current_task)
    assert default == before
    assert retrieval_profile_for(request.document_type) is LEGACY_RETRIEVAL_PROFILE
    offline_profile = resolve_genre(request.document_type, purpose="offline").retrieval_profile
    explicit_offline, _ = run(owner, rows, current_task, offline_profile)
    assert {row["coverage_role"] for row in explicit_offline["selected_chunks"]} == {
        "context", "progress", "issues", "recommendations"}
    assert explicit_offline["selected_chunks"] != default["selected_chunks"]


def test_every_summary_coverage_and_chunk_preference_matches_base(baseline):
    for row in candidates(40):
        assert WORK_SUMMARY_RETRIEVAL_PROFILE.classify_coverage(row) == baseline._chunk_coverage_role(row)
        assert WORK_SUMMARY_RETRIEVAL_PROFILE.chunk_preference(row) == baseline._fact_chunk_bonus(row)
    assert WORK_SUMMARY_RETRIEVAL_PROFILE.coverage_roles == (
        "achievement_status", "major_work", "problem", "next_step")


def test_p1_ranking_primary_auxiliary_and_safety_gates_remain_identical_ast(baseline_source):
    trees = [ast.parse(source) for source in (baseline_source, (ROOT / OWNER_PATH).read_bytes())]
    names = ("_aggregate_documents", "_select_documents", "_source_role", "_role_compatibility",
             "_coverage", "_field_match", "prepare_retrieval_request")
    for name in names:
        nodes = [next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
                 for tree in trees]
        assert ast.dump(nodes[0]) == ast.dump(nodes[1]), name


def test_report_profile_changes_coverage_without_changing_shared_document_ranking():
    rows = candidates(40)
    summary, _ = run(owner, rows, task(), WORK_SUMMARY_RETRIEVAL_PROFILE)
    report, _ = run(owner, rows, task(), REPORT_RETRIEVAL_PROFILE)
    assert report["ranked_documents"] == summary["ranked_documents"]
    assert report["primary_document_id"] == summary["primary_document_id"]
    assert report["auxiliary_document_ids"] == summary["auxiliary_document_ids"]
    assert {row["coverage_role"] for row in report["selected_chunks"]} == {
        "context", "progress", "issues", "recommendations"}
    # Genre reading semantics need not change the shared chunk-count budget.
    # The adopted upstream profile changes coverage/reading order, while the
    # existing P1 document ranking above remains identical.
    assert [row["chunk_id"] for row in report["selected_chunks"]] != [
        row["chunk_id"] for row in summary["selected_chunks"]]


def test_report_source_role_policy_applies_chunk_preference_without_changing_document_score():
    rows = candidates(2)[:2]
    rows[0].update(score=.82, vector_score=.82)
    rows[0]["metadata"].update(content_role="background", section_heading="基本情况",
                               section_path=["基本情况"])
    rows[1].update(score=.80, vector_score=.80)
    rows[1]["metadata"].update(content_role="problem", section_heading="需要说明的问题",
                               section_path=["需要说明的问题"])
    current_task = task("汇报材料")
    document = owner._aggregate_documents(rows, owner.prepare_retrieval_request(current_task))[0]
    snapshot = copy.deepcopy(document)
    assert (document["_requested_source_role"], document["_candidate_source_role"]) == (
        "mixed", "fact_source")
    semantic_only = replace(REPORT_RETRIEVAL_PROFILE, chunk_preference_source_roles=())
    assert owner._rank_chunks_for_document(document, semantic_only)[0]["chunk_id"] == "primary:0"
    assert owner._rank_chunks_for_document(document, REPORT_RETRIEVAL_PROFILE)[0]["chunk_id"] == "primary:1"
    assert document == snapshot
    plain, _ = run(owner, rows, current_task, semantic_only)
    profiled, _ = run(owner, rows, current_task, REPORT_RETRIEVAL_PROFILE)
    assert profiled["ranked_documents"] == plain["ranked_documents"]


def test_new_profile_is_consumed_without_core_edit_or_evidence_authority():
    rows = candidates(30)
    unchanged = copy.deepcopy(rows)
    reading_inputs = []

    def classify(chunk):
        reading_inputs.append(set(chunk))
        assert not {"evidence_id", "evidence_registry", "writability", "semantic_state"} & set(chunk)
        return "synthetic_section"

    plugin = replace(REPORT_RETRIEVAL_PROFILE, profile_id="synthetic_extension",
                     coverage_roles=("synthetic_section",), classify_coverage=classify,
                     chunk_preference=lambda chunk: 0.0)
    source_before = (ROOT / OWNER_PATH).read_bytes()
    result, _ = run(owner, rows, task(), plugin)
    assert (ROOT / OWNER_PATH).read_bytes() == source_before
    assert rows == unchanged
    assert reading_inputs
    assert {row["coverage_role"] for row in result["selected_chunks"]} == {"synthetic_section"}
    with pytest.raises(FrozenInstanceError):
        plugin.profile_id = "mutated"


def test_shared_retrieval_contains_no_concrete_genre_implementation_dependency():
    source = (ROOT / OWNER_PATH).read_text(encoding="utf-8")
    for forbidden in ("work_summary", "工作总结", "achievement_status", "major_work", "next_step",
                      "report_retrieval", "WORK_SUMMARY_RETRIEVAL_PROFILE", "REPORT_RETRIEVAL_PROFILE"):
        assert forbidden not in source


def test_profile_cannot_mutate_source_text_or_nested_metadata_before_atomic_input(baseline):
    rows = candidates(30)
    source_snapshot = copy.deepcopy(rows)
    attempts = []

    def mutate_copy(chunk):
        chunk["chunk_text"] = "未经授权的虚构成果。"
        chunk["doc_id"] = "forged-source"
        chunk["metadata"]["content_role"] = "result"
        chunk["metadata"]["section_path"].append("虚构成果")
        attempts.append(True)

    def classify(chunk):
        result = WORK_SUMMARY_RETRIEVAL_PROFILE.classify_coverage(chunk)
        mutate_copy(chunk)
        return result

    def preference(chunk):
        result = WORK_SUMMARY_RETRIEVAL_PROFILE.chunk_preference(chunk)
        mutate_copy(chunk)
        return result

    accidental_mutator = replace(WORK_SUMMARY_RETRIEVAL_PROFILE,
                                profile_id="mutation-isolation-proof",
                                classify_coverage=classify, chunk_preference=preference)
    expected, _ = run(baseline, rows, task())
    actual, _ = run(owner, rows, task(), accidental_mutator)
    assert attempts
    assert rows == source_snapshot
    assert actual == expected
    assert all("虚构成果" not in row["chunk_text"]
               and "虚构成果" not in row["metadata"]["section_path"]
               for row in actual["selected_chunks"])


@pytest.mark.parametrize("changes", [
    {"primary_min_coverage_chunks": 0}, {"primary_max_coverage_chunks": 1},
    {"coverage_roles": ("same", "same")}, {"auxiliary_chunk_budget": True},
    {"chunk_preference_source_roles": (("fact_source", "ALLOW_UNKNOWN"),)},
])
def test_profile_rejects_invalid_coverage_configuration(changes):
    with pytest.raises(ValueError):
        replace(WORK_SUMMARY_RETRIEVAL_PROFILE, **changes)
