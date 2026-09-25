"""W3-8C-3: real local ingestion/retrieval/assembly/delivery, synthetic I/O only."""
import asyncio
import copy
from dataclasses import asdict, replace
import json
from pathlib import Path
import subprocess

from fastapi import BackgroundTasks
import pytest

from app import config
from app.models.full_article import FullArticleContractError, canonical_json
from app.models.generation import ArticleGenRequest
from app.services.generation import atomic_evidence as atomic
from app.services.generation import full_draft_generation as route
from app.services.generation import generator, task_assembly, stable_material_assets as stable
from app.services.generation.full_draft_material import FullDraftMaterialPacket
from app.services.library import embedder, indexer, material_ingestion as ingestion
from app.services.library import semantic_preparation as preparation
from app.services.retrieval import document_retriever as retrieval_owner, retriever
from app.services.llm.openai_compatible_provider import OpenAICompatibleProvider
from app.utils import runtime_paths
from test_writing_reference_pack_02b1 import configured, api
from writer_material_view_decoder import decode_materials


BASE_SHA = "a6d93a54ac1d417eb0f8ae23d92569677e8e4f40"
REQUEST = {"title": "2026年安全检查工作总结", "document_type": "工作总结",
           "generation_route": "full_article_drafting", "target_words": 1200,
           "temperature": 0.45}
ARTICLE = "# 合成安全检查工作总结\n\n测试街道组织安全检查。\n\n## 存在的问题\n\n相关情况仍需核实。\n"


def forbidden(*args, **kwargs):
    pytest.fail("Generation must not process sources, embed, call Brief or fall back")


@pytest.fixture
def library(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime_paths, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(embedder, "_get_embedding_config", lambda: (config.EmbeddingConfig(), "fake-local"))
    monkeypatch.setattr(embedder, "_call_embedding_api", lambda c, m, texts: [[1.0, 0.5] for _ in texts])
    candidates, docs = [], []
    # Actual ingestion creates complete >800-character assets before generation.
    # The body includes unsupported placeholders, negation, qualifiers and plans.
    for n in range(3):
        path = tmp_path / f"synthetic-{n}.txt"
        path.write_text("2026年安全检查工作总结\n一、工作情况\n"
            + "测试街道组织安全检查。测试街道基本完成隐患整改。测试街道尚未建立应急机制。"
            + "测试街道建立XX机制。测试街道计划于2027年开展安全培训。"
            + "合成背景文字。" * 105 + "测试街道完成授权外尾部标记项目。\n"
            + "二、存在问题\n测试街道在隐患整改工作中仍存在衔接不足。\n"
            + "三、下一步安排\n测试街道计划开展宣传培训。", encoding="utf-8")
        doc = indexer.index_file(str(path))
        tasks = BackgroundTasks()
        ingestion.schedule_processing(doc.id, tasks)
        asyncio.run(tasks())
        doc = indexer.get_document(doc.id)
        assert doc.ingestion_status == "READY"
        docs.append(doc)
        for chunk in embedder.get_embedding_info(doc.id)["chunks"]:
            candidates.append({**retriever._chunk_result(doc, doc.id, chunk),
                               "score": 0.95 - n * 0.01, "score_type": "vector_similarity"})
    calls = {"search": 0, "assembly": 0, "writer": 0}

    def search(query, **kwargs):
        calls["search"] += 1
        assert kwargs == {"top_k": retrieval_owner.RAW_CANDIDATE_LIMIT, "retrieval_mode": "vector_only"}
        return {"results": copy.deepcopy(candidates)}

    monkeypatch.setattr(retrieval_owner, "search_detailed", search)
    # All remaining stages must read the prepared assets. No real preprocessing
    # function can execute after this point, including cache-repair entry points.
    for owner, name in [(preparation, "prepare_semantic_chunks"), (preparation, "parse_text_file"),
                        (preparation, "parse_docx_file"), (preparation, "semantic_chunk_document"),
                        (embedder, "generate_embeddings"), (embedder, "_call_embedding_api"),
                        (ingestion, "process_document"), (ingestion, "_load_asset"),
                        (ingestion, "document_readiness"), (stable, "load_stable_material_asset"),
                        (FullDraftMaterialPacket, "brief_prompt"),
                        (generator, "generate_flow3_v33_structured_draft"),
                        (generator, "prepare_flow3_v33_structured_generation")]:
        monkeypatch.setattr(owner, name, forbidden)
    actual = task_assembly.assemble_task_from_assets
    assembled = []

    def assemble(req, retrieval, assets):
        calls["assembly"] += 1
        result = actual(req, retrieval, assets)
        assembled.append((copy.deepcopy(retrieval), result))
        return result

    monkeypatch.setattr(route, "assemble_task_from_assets", assemble)
    provider = OpenAICompatibleProvider(config.ProviderConfig(
        model="qwen-synthetic", api_key="synthetic-only", base_url="https://offline.invalid"))
    sent = []

    def writer(prompt, **kwargs):
        calls["writer"] += 1
        sent.append((prompt, kwargs))
        assert calls["assembly"] == 1
        assert "deterministic_task_assembly" in prompt
        assert kwargs["temperature"] == REQUEST["temperature"]
        assert kwargs["retry_limit"] == 0 and kwargs["preserve_whitespace"] is True
        assert "reasoning_effort" not in kwargs
        kwargs["on_http_request_attempt"](1)
        return ARTICLE

    monkeypatch.setattr(provider, "generate_text", writer)
    monkeypatch.setattr(route, "get_llm_provider", lambda: provider)
    return docs, calls, sent, assembled


def snapshots(root):
    return {str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in (root / "data" / "library").rglob("*") if path.is_file()}


def test_public_production_route_uses_assets_and_one_writer(configured, api, library, tmp_path):
    client, _ = api
    docs, calls, sent, assembled = library
    before = snapshots(tmp_path)
    response = client.post("/api/generation/generate", json=REQUEST)
    assert response.status_code == 200, response.text
    assert calls == {"search": 1, "assembly": 1, "writer": 1}
    assert before == snapshots(tmp_path)  # no cache/status/source writes
    retrieval, result = assembled[0]
    request = ArticleGenRequest(**REQUEST)
    task = generator.build_official_draft_task(request)
    original = atomic.build_full_draft_anchor_input(task, retrieval)
    assert json.loads(result.anchor_input_json) == original
    fields = {**asdict(task), "style": request.style, "scene": request.scene,
              "hard_max": generator.derive_final_delivery_length_gate(task.target_words)["max_words"]}
    baseline = FullDraftMaterialPacket.from_authorized_input(fields, original)
    assert replace(result.packet, brief_json="") == baseline
    assert result.packet.overlay() == baseline.overlay()
    assert result.packet.evidence_writing_view() == baseline.evidence_writing_view()
    registry = json.loads(result.packet.registry_json)
    reading = json.loads(result.packet.brief_json)
    assert {eid for matter in reading["matters"] for eid in matter["evidence_ids"]} == registry.keys()
    unavailable = json.loads(result.packet.unavailable_json)
    assert unavailable
    prompt = sent[0][0]
    evidence_view, task_view = decode_materials(prompt)
    assert canonical_json(evidence_view) == canonical_json(result.packet.evidence_writing_view())
    assert canonical_json(task_view) == result.packet.brief_json
    assert canonical_json(result.packet.evidence_writing_view()) not in prompt
    assert result.packet.brief_json not in prompt
    assert all(row["evidence_id"] not in prompt for row in unavailable)
    assert "授权外尾部标记" not in prompt
    assert len(retrieval["auxiliary_document_ids"]) == 2
    for doc_id in retrieval["auxiliary_document_ids"]:
        assert sum(row["document_id"] == doc_id for row in retrieval["selected_chunks"]) <= 2
    assert all(len(row["text"]) <= (800 if row["document_role"] == "primary" else 300)
               for row in retrieval["selected_chunks"])
    assert any(len(row["chunk_text"]) > len(row["text"]) for row in retrieval["selected_chunks"])
    assert any("授权外尾部标记" in row["chunk_text"] for row in retrieval["selected_chunks"])
    assert all("授权外尾部标记" not in row["text"] for row in retrieval["selected_chunks"])
    for record in registry.values():
        scope = record["source_scope"]
        assert any(scope["chunk_id"] == row["chunk_id"] and scope["source_span"] in row["text"]
                   for row in retrieval["selected_chunks"])
    root, = (tmp_path / "full-draft").iterdir()
    accounting = json.loads((root / "call_accounting.json").read_bytes())
    assert accounting["material_brief_calls"] == accounting["brief_http_attempts"] == 0
    assert accounting["writer_calls"] == accounting["writer_http_attempts"] == accounting["http_attempts"] == 1
    assert accounting["fallback_used"] is False
    assert accounting["material_preparation"] == "deterministic_task_assembly"
    assert not list(root.glob("BRIEF*")) and not (root / "MATERIAL_BRIEF_BOUND.json").exists()
    assert json.loads((root / "TASK_ASSEMBLY.json").read_bytes())["kind"] == "deterministic_task_assembly"
    for name in ("candidate.json", "citations.json", "fact_report.json", "quality_report.json"):
        assert (root / name).is_file()
    assert not (root / "checks_unavailable.json").exists()
    assert (root / "RAW_WRITER_ARTICLE.md").read_bytes() == (root / "FINAL_DELIVERED_ARTICLE.md").read_bytes()
    assert response.json()["articles"][0]["article_markdown"] == ARTICLE


@pytest.mark.parametrize("damage", [
    "missing_manifest", "missing_asset", "corrupt_manifest", "corrupt_asset", "checksum",
    "asset_identity", "asset_schema", "source_changed", "rules_changed", "embedding_changed",
    "not_ready", "wrong_document", "wrong_chunk", "wrong_text", "wrong_metadata",
])
def test_unusable_assets_fail_closed_without_mutating_library(configured, api, library, tmp_path, monkeypatch, damage):
    client, _ = api
    docs, calls, sent, _ = library
    doc = docs[0]
    directory = ingestion.assets_directory(doc.id)
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    asset_path = directory / (manifest["assets"][0]["cache_key"] + ".json")
    if damage == "missing_manifest": manifest_path.unlink()
    elif damage == "missing_asset": asset_path.unlink()
    elif damage == "corrupt_manifest": manifest_path.write_bytes(b"{bad")
    elif damage == "corrupt_asset": asset_path.write_bytes(b"{bad")
    elif damage in {"checksum", "asset_identity", "asset_schema"}:
        value = json.loads(asset_path.read_bytes())
        if damage == "checksum": value["sha256"] = "invalid"
        elif damage == "asset_identity": value["payload"]["document_id"] = "foreign"
        else: value["payload"]["reading_units"] = [{}]
        if damage != "checksum": value["sha256"] = stable.text_sha(canonical_json(value["payload"]))
        asset_path.write_text(canonical_json(value), encoding="utf-8")
        manifest["assets"][0]["sha256"] = ingestion._sha(asset_path)
        manifest_path.write_text(canonical_json(manifest), encoding="utf-8")
    elif damage == "source_changed": Path(doc.file_path).write_text("合成变化", encoding="utf-8")
    elif damage == "rules_changed": monkeypatch.setattr(ingestion, "rule_fingerprint", lambda: "stale")
    elif damage == "embedding_changed":
        with embedder._embedding_path(doc.id).open("ab") as stream: stream.write(b" ")
    elif damage == "not_ready": indexer.update_document(doc.id, ingestion_status="INDEXED")
    else:
        original = route.retrieve_documents
        def altered(*args, **kwargs):
            result = original(*args, **kwargs)
            row = result["selected_chunks"][0]
            if damage == "wrong_document": row["document_id"] = "foreign"
            if damage == "wrong_chunk": row["chunk_id"] = "foreign"
            if damage == "wrong_text": row["chunk_text"] += "合成篡改"
            if damage == "wrong_metadata": row["metadata"]["source_path"] = "foreign"
            return result
        monkeypatch.setattr(route, "retrieve_documents", altered)
    before = snapshots(tmp_path)
    response = client.post("/api/generation/generate", json=REQUEST)
    assert response.status_code == 400, response.text
    assert response.json()["detail"]["contract_error_code"] == "FULL_DRAFT_STABLE_ASSETS_UNAVAILABLE"
    assert response.json()["detail"]["fallback_used"] is False
    assert calls["assembly"] == calls["writer"] == 0 and not sent
    assert before == snapshots(tmp_path)
    assert not (tmp_path / "full-draft").exists()


def test_missing_deterministic_view_cannot_fall_back(configured, api, library, monkeypatch):
    client, _ = api
    _, calls, _, _ = library
    actual = route.prepare_full_draft_material
    monkeypatch.setattr(route, "prepare_full_draft_material",
                        lambda request: replace(actual(request), brief_json=""))
    monkeypatch.setattr(route, "get_llm_provider", forbidden)
    response = client.post("/api/generation/generate", json=REQUEST)
    assert response.status_code == 400
    assert response.json()["detail"]["contract_error_code"] == "FULL_DRAFT_TASK_ASSEMBLY_REQUIRED"
    assert calls["writer"] == 0


def test_bound_overlay_and_current_restrictions_preserved(library, monkeypatch):
    original = route.retrieve_documents
    overlay = {"findings": [], "synthetic_marker": "already_bound_do_not_recompute"}
    def restricted(*args, **kwargs):
        value = original(*args, **kwargs)
        value.update(time_risk_overlay=overlay, limitations=["仅供当前合成任务，不能补全成果"])
        return value
    monkeypatch.setattr(route, "retrieve_documents", restricted)
    import app.services.generation.full_draft_material as material
    monkeypatch.setattr(material, "temporal_review_overlay", forbidden)
    packet = route.prepare_full_draft_material(ArticleGenRequest(**REQUEST))
    assert packet.overlay() == overlay
    assert "仅供当前合成任务" in packet.restrictions_json


def test_no_selected_assets_fails_before_provider(configured, api, library, monkeypatch):
    client, _ = api
    monkeypatch.setattr(retrieval_owner, "search_detailed", lambda *a, **kw: {"results": []})
    monkeypatch.setattr(route, "get_llm_provider", forbidden)
    response = client.post("/api/generation/generate", json=REQUEST)
    assert response.status_code == 400
    assert response.json()["detail"]["contract_error_code"] == "FULL_DRAFT_STABLE_ASSETS_REQUIRED"


def test_writer_failure_is_not_retried_or_fallen_back(configured, library, monkeypatch, tmp_path):
    monkeypatch.setattr(route, "get_output_dir", lambda: tmp_path)
    calls = []
    def fail(prompt, **kwargs):
        calls.append(prompt)
        kwargs["on_http_request_attempt"](1)
        raise RuntimeError("synthetic_writer_failure")
    monkeypatch.setattr(route.get_llm_provider(), "generate_text", fail)
    with pytest.raises(RuntimeError, match="synthetic_writer_failure"):
        asyncio.run(route.generate_full_draft(ArticleGenRequest(**REQUEST)))
    assert len(calls) == 1
    root, = (tmp_path / "full-draft").iterdir()
    accounting = json.loads((root / "call_accounting.json").read_bytes())
    assert accounting["status"] == "FAILED"
    assert accounting["material_brief_calls"] == accounting["brief_http_attempts"] == 0
    assert accounting["writer_calls"] == accounting["writer_http_attempts"] == 1
    assert accounting["fallback_used"] is False and not list(root.glob("BRIEF*"))


def test_protected_owners_unchanged_from_task_base():
    root = Path(__file__).resolve().parents[2]
    paths = subprocess.check_output(["git", "ls-files", "backend/app/services/generation",
        "backend/app/services/flow4", "backend/app/services/retrieval", "backend/app/routers/generation.py",
        "backend/app/models", "backend/app/services/llm", "frontend"], cwd=root).decode().splitlines()
    allowed = {"backend/app/services/generation/full_draft_generation.py",
               "backend/app/services/generation/task_assembly.py",
               # WRITER-VIEW-01: exact presentation-only paths, no owner wildcard.
               "backend/app/services/generation/full_draft_material.py",
               "backend/app/services/generation/writer_material_view.py",
               # NEXT-PERF-01: observation hooks only; baseline request/response
               # parity and failure semantics are checked by test_writer_observability.
               "backend/app/services/llm/openai_compatible_provider.py",
               "backend/app/services/llm/writer_observability.py"}
    # FRONTEND-ROUTE-01 / PR #10 正式授权以下两个前端文件变化。
    approved_refs = {
        "frontend/src/utils/batchTaskParams.js": "bbe63b297ce60861b375ead5685dea457bb76f00",
        "frontend/tests/utils/batchTaskParams.test.mjs": "bbe63b297ce60861b375ead5685dea457bb76f00",
    }
    for path in paths:
        if path in allowed: continue
        approved_ref = approved_refs.get(path, BASE_SHA)
        before = subprocess.check_output(["git", "show", f"{approved_ref}:{path}"], cwd=root)
        assert (root / path).read_bytes().replace(b"\r\n", b"\n") == before.replace(b"\r\n", b"\n"), path
