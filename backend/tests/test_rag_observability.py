"""RAG-OBS-01: committed-baseline parity, real local owners, synthetic I/O.

Run via tests/run_w3_8c_2a_offline.py to guard network and real user data.
No production material, external model or Embedding service is used.
"""
import asyncio
import copy
import json
from collections import Counter
from pathlib import Path
import subprocess
from types import ModuleType, SimpleNamespace

import pytest

from app.models.full_article import canonical_json
from app.models.generation import ArticleGenRequest
from app.services.generation import atomic_evidence as atomic
from app.services.generation import full_draft_generation as route
from app.services.retrieval import document_retriever as retrieval_owner
from test_production_task_assembly import library, REQUEST, ARTICLE
from test_source_substrate import sources, task
from test_writing_reference_pack_02b1 import configured
from p1_ranking_approval import DOCUMENT_RETRIEVER_PATH, assert_p1_document_retriever


BASE_SHA = "7b86df5084bbd81d1fc442316c4e98059bfbc426"
ROOT = Path(__file__).resolve().parents[2]


def committed(path):
    source = subprocess.check_output(["git", "show", BASE_SHA + ":" + path], cwd=ROOT)
    module = ModuleType("rag_obs_baseline")
    exec(compile(source, BASE_SHA + "/" + path, "exec"), module.__dict__)
    return module


def read_traces(root):
    return [json.loads((root / name).read_bytes()) for name in
            (route.RAG_TRACE_FILE, route.ATOMIC_TRACE_FILE)]


@pytest.mark.parametrize("model", ["qwen-synthetic", "qwen3.8-flash"])
def test_committed_baseline_parity(configured, library, monkeypatch, tmp_path, model):
    _, calls, sent, assembled = library
    base = committed("backend/app/services/generation/full_draft_generation.py")
    base_retrieval = committed("backend/app/services/retrieval/document_retriever.py")
    base_retrieval.search_detailed = retrieval_owner.search_detailed
    base.retrieve_documents = base_retrieval.retrieve_documents
    base.read_selected_material_assets = route.read_selected_material_assets
    base.assemble_task_from_assets = route.assemble_task_from_assets
    base.get_llm_provider = route.get_llm_provider
    route.get_llm_provider().config.model = model
    base.get_output_dir = lambda: tmp_path / "before"
    monkeypatch.setattr(route, "get_output_dir", lambda: tmp_path / "after")
    actual_build = atomic.build_atomic_evidence_pack
    builds = []

    def build(*args, **kwargs):
        builds.append(1)
        return actual_build(*args, **kwargs)

    monkeypatch.setattr(atomic, "build_atomic_evidence_pack", build)
    # Record the exact Writer interface (callback identity is inherently per Run).
    def writer(prompt, **kwargs):
        calls["writer"] += 1
        sent.append((prompt, {k: v for k, v in kwargs.items() if k != "on_http_request_attempt"}))
        kwargs["on_http_request_attempt"](1)
        return ARTICLE

    monkeypatch.setattr(route.get_llm_provider(), "generate_text", writer)
    before = asyncio.run(base.generate_full_draft(ArticleGenRequest(**REQUEST)))
    assert calls == {"search": 1, "assembly": 1, "writer": 1} and len(builds) == 1
    calls.update(dict.fromkeys(calls, 0))
    after = asyncio.run(route.generate_full_draft(ArticleGenRequest(**REQUEST)))
    assert calls == {"search": 1, "assembly": 1, "writer": 1} and len(builds) == 2
    old_retrieval, old = assembled[0]
    new_retrieval, new = assembled[1]
    # P1 authorizes only the score delta here; this same-topic fixture keeps
    # its ordering, selected identities, chunks, Writer input and all contracts.
    expected_retrieval = copy.deepcopy(old_retrieval)
    components = {row["document_id"]: row["score_components"]
                  for row in old_retrieval["ranked_documents"]}
    for field in ("ranked_documents", "rejected_documents", "selected_chunks"):
        for row in expected_retrieval[field]:
            c = components[row["document_id"]]
            row["document_score"] = round(row["document_score"]
                - .060 * c["source_role_compatibility"]
                - .015 * c["document_type_match"] - .015 * c["auxiliary_match"], 6)
    for field in ("raw_chunk_candidates", "ranked_documents", "primary_document_id",
                  "auxiliary_document_ids", "selected_chunks", "retrieval_diagnostics"):
        assert expected_retrieval[field] == new_retrieval[field], field
    assert old.anchor_input_json == new.anchor_input_json  # includes accepted/discarded
    assert old.packet == new.packet
    assert old.packet.registry_sha256 == new.packet.registry_sha256
    assert old.packet.digest == new.packet.digest
    assert sent[0] == sent[1]  # both Prompt strings and all Writer options
    assert before.articles[0].article_markdown == after.articles[0].article_markdown == ARTICLE
    assert before.articles[0].delivery_status == after.articles[0].delivery_status
    root, = (tmp_path / "after" / "full-draft").iterdir()
    rag, atom = read_traces(root)
    anchors = json.loads(new.anchor_input_json)
    assert len(rag["raw_chunk_candidates"]) == len(new_retrieval["raw_chunk_candidates"])
    assert rag["primary_document_id"] == new_retrieval["primary_document_id"]
    assert rag["auxiliary_document_ids"] == new_retrieval["auxiliary_document_ids"]
    assert atom["accepted_count"] == len(anchors["atomic_evidence"]["evidence"])
    assert atom["discarded_evidence"] == anchors["atomic_evidence"]["discarded_evidence"]
    assert atom["registry_evidence_ids"] == list(anchors["registry"])
    assert atom["registry_sha256"] == new.packet.registry_sha256
    assert atom["packet_digest"] == new.packet.digest
    expected_report = copy.deepcopy(anchors["atomic_report"])
    for field in ("hard_issues", "warnings"):
        for issue in expected_report[field]:
            issue.pop("detail", None)
    assert atom["atomic_report"] == expected_report
    assert atom["unavailable_count"] == len(anchors["unavailable"]) > 0
    assert atom["unavailable_reason_counts"] == dict(Counter(x["reason"] for x in anchors["unavailable"]))
    assert route._material_traces.get() is None


def test_rag_trace_complete_redacted_and_nonmutating():
    candidates = [{"doc_id": name, "chunk_id": name + ":0", "chunk_index": 0,
                   "title": "安全生产工作总结", "chunk_text": "PRIVATE_COMPLETE_CHUNK",
                   "score": score, "vector_score": score,
                   "metadata": {"document_type": "工作总结", "api_key": "PRIVATE_SECRET"},
                   "embedding": [12345.6789]}
                  for name, score in (("main", .95), ("aux", .94), ("rejected", .1))]
    payload = retrieval_owner.retrieve_documents(task(), persist_trace=False, search_fn=lambda *a, **k: {
        "results": candidates, "raw_results_count": 3, "retrieval_mode": "vector_only",
        "filtered_samples": [{**candidates[0], "filter_reason": "missing_vector"}],
        "rebuilt_doc_ids": [],
    })
    before = copy.deepcopy(payload)
    global_before = retrieval_owner.build_retrieval_trace(payload)
    trace = route._build_full_draft_retrieval_trace(payload)
    assert payload == before
    assert retrieval_owner.build_retrieval_trace(payload) == global_before
    baseline = committed("backend/app/services/retrieval/document_retriever.py")
    assert global_before == baseline.build_retrieval_trace(payload)
    assert "raw_chunk_candidates" not in global_before
    assert [r["raw_rank"] for r in trace["raw_chunk_candidates"]] == [1, 2, 3]
    assert [r["document_id"] for r in trace["raw_chunk_candidates"]] == ["main", "aux", "rejected"]
    assert [r["selection_status"] for r in trace["ranked_documents"]] == ["PRIMARY", "AUXILIARY", "REJECTED"]
    for old, new in zip(payload["ranked_documents"], trace["ranked_documents"]):
        for field in ("rank", "document_score", "score_components", "passed_primary_gate", "gate_reasons"):
            assert old[field] == new[field]
    for old, new in zip(payload["selected_chunks"], trace["selected_chunks"]):
        for field in ("chunk_id", "raw_rank", "score", "coverage_role", "selection_reason"):
            assert old[field] == new[field]
    assert trace["retrieval_mode"] == "vector_only"
    assert trace["retrieval_diagnostics"]["filtered_samples"][0]["filter_reason"] == "missing_vector"
    serialized = canonical_json(trace)
    for forbidden in ("PRIVATE_", "chunk_text", "embedding", "api_key", "12345.6789"):
        assert forbidden not in serialized


@pytest.mark.parametrize("scope", ["none", "资料库"])
def test_empty_retrieval_trace(scope):
    payload = retrieval_owner.retrieve_documents({"title": "合成", "reference_scope": scope},
        persist_trace=False, search_fn=lambda *a, **k: {"results": [], "retrieval_mode": "vector_only"})
    trace = route._build_full_draft_retrieval_trace(payload)
    assert trace["retrieval_status"] == "INSUFFICIENT"
    assert trace["raw_chunk_candidates"] == trace["ranked_documents"] == trace["selected_chunks"] == []


def test_existing_retrieval_trace_persistence_unchanged(monkeypatch, tmp_path):
    monkeypatch.setattr(retrieval_owner, "get_logs_dir", lambda: tmp_path)
    payload = retrieval_owner.retrieve_documents(task(), search_fn=lambda *a, **k: {"results": []})
    assert json.loads(Path(payload["trace_path"]).read_bytes()) == retrieval_owner.build_retrieval_trace(payload)
    baseline = committed("backend/app/services/retrieval/document_retriever.py")
    assert json.loads(Path(payload["trace_path"]).read_bytes()) == baseline.build_retrieval_trace(payload)


def test_retrieval_business_code_and_protected_owners_unchanged():
    from p3a_semantic_approval import ATOMIC_EVIDENCE_PATH, assert_p3a_atomic_evidence
    from multi_genre_scope import MULTI_GENRE_CONTENT_SHA256, assert_multi_genre_content

    for path in ("backend/app/services/retrieval/document_retriever.py",
                 "backend/app/services/generation/atomic_evidence.py",
                 "backend/app/services/generation/task_assembly.py",
                 "backend/app/services/generation/full_draft_material.py"):
        if path == ATOMIC_EVIDENCE_PATH:
            assert_p3a_atomic_evidence(ROOT)
            continue
        if path == DOCUMENT_RETRIEVER_PATH:
            assert_p1_document_retriever(ROOT)
            continue
        if path in MULTI_GENRE_CONTENT_SHA256:
            assert_multi_genre_content(ROOT, path)
            continue
        before = subprocess.check_output(["git", "show", BASE_SHA + ":" + path], cwd=ROOT)
        assert before.replace(b"\r\n", b"\n") == (ROOT / path).read_bytes().replace(b"\r\n", b"\n")


def test_actual_discard_reasons_and_atomic_redaction():
    retrieval = sources()
    retrieval["selected_chunks"].extend([
        {"document_id": "synthetic-main", "chunk_id": "missing-text"},
        {"document_id": "synthetic-main", "chunk_id": "residue", "source_title": "合成来源标题",
         "text": "合成来源标题。"},
        {"document_id": "synthetic-main", "chunk_id": "safety-limit",
         "text": "。".join(f"测试街道开展第{i}项安全检查" for i in range(atomic.MAX_ATOMS_PER_CHUNK + 2))},
    ])
    anchors = atomic.build_full_draft_anchor_input(task(), retrieval)
    expected = Counter(row["reason"] for row in anchors["atomic_evidence"]["discarded_evidence"])
    assert expected["auxiliary_does_not_fill_primary_type_gap"] == 0
    for reason in ("duplicate_proposition",
                   "source_title_residue", "missing_source_identity_or_text", "chunk_atom_safety_limit"):
        assert expected[reason] > 0, (reason, expected)
    # Sensitive extras never leak from nested objects, reports, registry or atoms.
    for row in anchors["atomic_evidence"]["evidence"] + anchors["atomic_evidence"]["discarded_evidence"]:
        row.update(chunk_text="PRIVATE_COMPLETE_CHUNK", embedding=[12345.6789], api_key="PRIVATE_SECRET")
    anchors["atomic_report"]["secret"] = "PRIVATE_SECRET"
    assembly = SimpleNamespace(anchor_input_json=canonical_json(anchors), packet=SimpleNamespace(digest="packet-hash"))
    before = assembly.anchor_input_json
    trace = route._build_atomic_trace(assembly)
    assert assembly.anchor_input_json == before
    assert trace["discard_reason_counts"] == dict(expected)
    assert trace["discarded_count"] == sum(expected.values())
    serialized = canonical_json(trace)
    for forbidden in ("PRIVATE_", "chunk_text", "embedding", "api_key", "12345.6789", "测试街道"):
        assert forbidden not in serialized


@pytest.mark.parametrize("detail", [
    "PRIVATE_SOURCE_SENTENCE_AND_SECRET",
    {"nested": [{"detail": {"chunk_text": "PRIVATE_SOURCE_SENTENCE_AND_SECRET"}}]},
])
def test_atomic_report_nested_detail_never_persisted(tmp_path, detail):
    anchors = atomic.build_full_draft_anchor_input(task(), sources())
    anchors["atomic_report"]["detail"] = detail
    expected_issue = {"rule": "source_title", "evidence_id": "synthetic-evidence",
                      "field": "object", "length": 32}
    for field in ("hard_issues", "warnings"):
        anchors["atomic_report"][field] = [{**expected_issue, "detail": detail}]
    assembly = SimpleNamespace(anchor_input_json=canonical_json(anchors), packet=SimpleNamespace(digest="packet-hash"))
    before = assembly.anchor_input_json
    trace = route._build_atomic_trace(assembly)
    route._persist_material_traces(tmp_path, {route.ATOMIC_TRACE_FILE: trace})
    persisted = (tmp_path / route.ATOMIC_TRACE_FILE).read_text(encoding="utf-8")
    assert "PRIVATE_SOURCE_SENTENCE_AND_SECRET" not in persisted
    assert '"detail"' not in persisted and '"chunk_text"' not in persisted
    for field in ("hard_issues", "warnings"):
        assert json.loads(persisted)["atomic_report"][field] == [expected_issue]
    assert assembly.anchor_input_json == before


@pytest.mark.parametrize("failure", ["rag_build", "atomic_build", "rag_write", "atomic_write", "writer"])
def test_trace_fail_open_and_writer_failure_retention(configured, library, monkeypatch, tmp_path, caplog, failure):
    _, calls, _, assembled = library
    monkeypatch.setattr(route, "get_output_dir", lambda: tmp_path)

    def fail(*args, **kwargs):
        raise OSError("PRIVATE_SECRET must never be logged")

    if failure.endswith("build"):
        monkeypatch.setattr(route, "_build_full_draft_retrieval_trace" if failure == "rag_build" else "_build_atomic_trace", fail)
    elif failure.endswith("write"):
        actual = route._save_json
        failed_file = route.RAG_TRACE_FILE if failure == "rag_write" else route.ATOMIC_TRACE_FILE
        monkeypatch.setattr(route, "_save_json", lambda root, name, data:
            fail() if name == failed_file else actual(root, name, data))
    else:
        monkeypatch.setattr(route.get_llm_provider(), "generate_text", fail)
    if failure == "writer":
        with pytest.raises(OSError):
            asyncio.run(route.generate_full_draft(ArticleGenRequest(**REQUEST)))
    else:
        result = asyncio.run(route.generate_full_draft(ArticleGenRequest(**REQUEST)))
        assert result.articles[0].article_markdown == ARTICLE
        assert calls["writer"] == 1
    root, = (tmp_path / "full-draft").iterdir()
    for name, prefix in ((route.RAG_TRACE_FILE, "rag"), (route.ATOMIC_TRACE_FILE, "atomic")):
        assert (root / name).exists() == (not failure.startswith(prefix))
    assert calls["search"] == calls["assembly"] == len(assembled) == 1
    assert "PRIVATE_SECRET" not in caplog.text
    assert route._material_traces.get() is None
    if failure == "writer":
        assert json.loads((root / "call_accounting.json").read_bytes())["status"] == "FAILED"
        assert not (root / "RAW_WRITER_ARTICLE.md").exists()


def test_material_trace_context_resets_on_preparation_failure(configured, monkeypatch):
    def fail(request):
        assert route._material_traces.get() == {}
        raise RuntimeError("synthetic preparation failure")
    monkeypatch.setattr(route, "prepare_full_draft_material", fail)
    with pytest.raises(RuntimeError):
        asyncio.run(route.generate_full_draft(ArticleGenRequest(**REQUEST)))
    assert route._material_traces.get() is None
