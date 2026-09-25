"""Synthetic upload integration and failure/lifecycle contracts; zero external calls."""
import asyncio
import json
from pathlib import Path
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor

from fastapi import BackgroundTasks, FastAPI
from fastapi.testclient import TestClient
import pytest

from app.config import EmbeddingConfig
from app.models.library import LibraryDocument
from app.routers import library as router
from app.services.library import embedder, indexer, material_ingestion as ingestion
from app.services.library import semantic_preparation as preparation
from app.services.generation import stable_material_assets as stable
from app.utils import runtime_paths

TEXT = "合成测试工作总结\n一、工作情况\n" + "测试单位组织安全检查，建立问题记录并明确整改责任。" * 65


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime_paths, "PROJECT_ROOT", tmp_path)
    calls = {"embedding": 0, "chunking": 0, "scene": 0, "card": 0, "style": 0,
             "writer": 0, "brief": 0}
    from app.services.generation import full_draft_generation, generator
    from app.services.generation.full_draft_material import FullDraftMaterialPacket
    for module, name, kind in [(full_draft_generation, "generate_full_draft", "writer"),
                                (generator, "generate_flow3_v33_structured_draft", "writer"),
                                (FullDraftMaterialPacket, "brief_prompt", "brief")]:
        def forbidden_model(*args, _kind=kind, **kwargs):
            calls[_kind] += 1
            pytest.fail("Ingestion must not call " + _kind)
        monkeypatch.setattr(module, name, forbidden_model)
    actual = preparation.semantic_chunk_document

    def chunk(*args, **kwargs):
        calls["chunking"] += 1
        return actual(*args, **kwargs)

    def vectors(config, model, texts):
        calls["embedding"] += 1
        return [[float(i + 1), 0.5] for i in range(len(texts))]

    monkeypatch.setattr(preparation, "semantic_chunk_document", chunk)
    monkeypatch.setattr(embedder, "_get_embedding_config", lambda: (EmbeddingConfig(), "fake-local"))
    monkeypatch.setattr(embedder, "_call_embedding_api", vectors)
    from app.services.library import card_extractor, style_extractor
    from app.services.retrieval import scene_classifier
    for module, name, key in [(card_extractor, "extract_cards", "card"),
                               (style_extractor, "extract_style", "style"),
                               (router, "extract_cards", "card"), (router, "extract_style", "style")]:
        def forbidden(*args, _key=key, **kwargs):
            calls[_key] += 1
            pytest.fail("Legacy extraction must not run")
        monkeypatch.setattr(module, name, forbidden)
    for name in dir(scene_classifier):
        if name.startswith("classify") and callable(getattr(scene_classifier, name)):
            def forbidden_scene(*args, **kwargs):
                calls["scene"] += 1
                pytest.fail("Scene classification must not run")
            monkeypatch.setattr(scene_classifier, name, forbidden_scene)
    app = FastAPI()
    app.include_router(router.router, prefix="/library")
    yield tmp_path, calls, TestClient(app)
    assert calls["scene"] == calls["card"] == calls["style"] == 0
    assert calls["writer"] == calls["brief"] == 0
    assert not ingestion._jobs
    assert not ingestion._running_jobs


def indexed(local):
    path = local[0] / "synthetic.txt"
    path.write_text(TEXT, encoding="utf-8")
    return indexer.index_file(str(path))


def run(doc_id):
    background = BackgroundTasks()
    state = ingestion.schedule_processing(doc_id, background)
    assert state.ingestion_status == "PROCESSING"
    assert not state.has_embedding
    asyncio.run(background())
    return indexer.get_document(doc_id)


def manifest(doc_id):
    return json.loads((ingestion.assets_directory(doc_id) / "manifest.json").read_text(encoding="utf-8"))


def test_upload_to_ready_full_chunk_same_contract(local):
    _, calls, client = local
    response = client.post("/library/upload", files={"file": ("synthetic.txt", TEXT.encode(), "text/plain")})
    assert response.status_code == 200
    uploaded = response.json()["document"]
    assert uploaded["ingestion_status"] == "PROCESSING"
    assert not uploaded["has_embedding"]
    doc_id = uploaded["id"]
    doc = client.get(f"/library/documents/{doc_id}").json()
    assert doc["ingestion_status"] == "READY"
    assert calls["embedding"] == calls["chunking"] == 1
    data = manifest(doc_id)
    chunks = embedder.get_embedding_info(doc_id)["chunks"]
    assert data["chunk_count"] == len(chunks) == doc["embedding_chunks"] > 1
    forbidden = {"text", "rank", "score", "selection_reason", "document_role", "evidence_id", "section_assignment"}
    assert max(len(chunk["text"]) for chunk in chunks) > 300
    for item, chunk in zip(data["assets"], chunks):
        payload = json.loads((ingestion.assets_directory(doc_id) / (item["cache_key"] + ".json")).read_bytes())["payload"]
        assert payload["chunk_text"] == chunk["text"]
        assert payload["chunk_id"] == chunk["metadata"]["chunk_id"]
        assert not forbidden.intersection(payload)
        assert not forbidden.intersection(payload["source_metadata"]["chunk"])
        assert not forbidden.intersection(payload["source_metadata"]["metadata"])
    assert client.get("/library/documents").json()["items"][0]["ingestion_status"] == "READY"


@pytest.mark.parametrize("difference", [-1, 1])
def test_vector_count_failure_and_retry(local, monkeypatch, difference):
    doc = indexed(local)
    fake = embedder._call_embedding_api
    monkeypatch.setattr(embedder, "_call_embedding_api", lambda c, m, t: [[1.0]] * (len(t) + difference))
    result = run(doc.id)
    assert result.ingestion_status == "FAILED" and not result.has_embedding
    assert embedder.get_embedding_info(doc.id) is None
    assert not (ingestion.assets_directory(doc.id) / "manifest.json").exists()
    monkeypatch.setattr(embedder, "_call_embedding_api", fake)
    response = local[2].post(f"/library/documents/{doc.id}/process")
    assert response.status_code == 202 and response.json()["ingestion_status"] == "PROCESSING"
    assert indexer.get_document(doc.id).ingestion_status == "READY"


def test_asset_partial_failure_retry_reuses_completed_assets(local, monkeypatch):
    doc = indexed(local)
    actual = stable.load_stable_material_asset
    seen = []

    def fail_second(chunk, directory):
        if seen:
            raise OSError("synthetic failure with PRIVATE content")
        result = actual(chunk, directory)
        seen.append(result.asset.cache_key)
        return result

    monkeypatch.setattr(stable, "load_stable_material_asset", fail_second)
    result = run(doc.id)
    assert result.ingestion_status == "FAILED" and not result.has_embedding
    assert "PRIVATE" not in result.model_dump_json()
    directory = ingestion.assets_directory(doc.id)
    assert not (directory / "manifest.json").exists()
    cached = directory / (seen[0] + ".json")
    before = cached.read_bytes(), cached.stat().st_mtime_ns
    monkeypatch.setattr(stable, "load_stable_material_asset", actual)
    assert run(doc.id).ingestion_status == "READY"
    assert before == (cached.read_bytes(), cached.stat().st_mtime_ns)
    first = manifest(doc.id)
    snapshots = {entry["cache_key"]: (directory / (entry["cache_key"] + ".json")).read_bytes()
                 for entry in first["assets"]}
    assert run(doc.id).ingestion_status == "READY"
    assert first["stable_asset_cache_keys"] == manifest(doc.id)["stable_asset_cache_keys"]
    assert all((directory / (key + ".json")).read_bytes() == value for key, value in snapshots.items())


def test_delete_all_assets_and_pending_worker_cannot_resurrect(local):
    doc = indexed(local)
    assert run(doc.id).ingestion_status == "READY"
    for directory in (runtime_paths.get_library_cards_dir(), runtime_paths.get_library_styles_dir()):
        (directory / (doc.id + ".json")).write_text("{}", encoding="utf-8")
    background = BackgroundTasks()
    ingestion.schedule_processing(doc.id, background)
    assert local[2].delete(f"/library/documents/{doc.id}").status_code == 200
    asyncio.run(background())
    assert indexer.get_document(doc.id) is None
    assert embedder.get_embedding_info(doc.id) is None
    assert not ingestion.assets_directory(doc.id).exists()
    for directory in (runtime_paths.get_library_cards_dir(), runtime_paths.get_library_styles_dir()):
        assert not (directory / (doc.id + ".json")).exists()
    assert Path(doc.file_path).exists()  # Original-source deletion was never part of the API.


def test_legacy_json_loads_without_becoming_ready(local):
    doc = indexed(local)
    data = doc.model_dump()
    for field in ("ingestion_status", "ingestion_stage", "ingestion_error", "processed_at",
                  "ingestion_lease_expires_at"):
        data.pop(field)
    data["has_embedding"] = True
    indexer._index_path(doc.id).write_text(json.dumps(data), encoding="utf-8")
    assert LibraryDocument(**data).ingestion_status == "INDEXED"
    assert local[2].get(f"/library/documents/{doc.id}").json()["ingestion_status"] == "INDEXED"


@pytest.mark.parametrize("damage", ["asset_missing", "asset_corrupt", "manifest", "embedding", "source", "rules"])
def test_stale_or_incomplete_never_reports_ready(local, monkeypatch, damage):
    doc = indexed(local)
    assert run(doc.id).ingestion_status == "READY"
    directory = ingestion.assets_directory(doc.id)
    key = manifest(doc.id)["stable_asset_cache_keys"][0]
    if damage == "asset_missing": (directory / (key + ".json")).unlink()
    if damage == "asset_corrupt": (directory / (key + ".json")).write_text("{}")
    if damage == "manifest": (directory / "manifest.json").write_text("{}")
    if damage == "embedding": embedder._embedding_path(doc.id).write_text("{}")
    if damage == "source": Path(doc.file_path).write_text(TEXT + "合成变化", encoding="utf-8")
    if damage == "rules": monkeypatch.setattr(ingestion, "rule_fingerprint", lambda: "new-rule")
    response = local[2].get(f"/library/documents/{doc.id}")
    assert response.status_code == 200
    assert response.json()["ingestion_status"] == "FAILED"
    assert response.json()["has_embedding"] is False
    assert run(doc.id).ingestion_status == "READY"


def test_changed_source_during_embedding_fails_then_recovers(local, monkeypatch):
    doc = indexed(local)
    actual = embedder._call_embedding_api
    def change(*args):
        Path(doc.file_path).write_text(TEXT + "新增合成材料。", encoding="utf-8")
        return actual(*args)
    monkeypatch.setattr(embedder, "_call_embedding_api", change)
    assert run(doc.id).ingestion_status == "FAILED"
    assert not indexer.get_document(doc.id).has_embedding
    monkeypatch.setattr(embedder, "_call_embedding_api", actual)
    assert run(doc.id).ingestion_status == "READY"


@pytest.mark.parametrize("vector", [[], [float("nan")], [float("inf")]])
def test_empty_nonfinite_vectors_not_ready(local, monkeypatch, vector):
    doc = indexed(local)
    monkeypatch.setattr(embedder, "_call_embedding_api", lambda c, m, t: [vector] * len(t))
    assert run(doc.id).ingestion_status == "FAILED"
    assert embedder.get_embedding_info(doc.id) is None


def test_legacy_embed_entry_preserved_and_cannot_claim_ready(local, monkeypatch):
    doc = indexed(local)
    assert run(doc.id).ingestion_status == "READY"
    response = local[2].post(f"/library/documents/{doc.id}/embed")
    assert response.status_code == 200
    assert indexer.get_document(doc.id).has_embedding
    assert indexer.get_document(doc.id).ingestion_status == "INDEXED"
    monkeypatch.setattr(embedder, "_call_embedding_api", lambda c, m, t: [[1.0]] * (len(t) - 1))
    assert local[2].post(f"/library/documents/{doc.id}/embed").status_code == 400
    assert not indexer.get_document(doc.id).has_embedding
    assert indexer.get_document(doc.id).ingestion_status == "FAILED"
    assert embedder.get_embedding_info(doc.id) is None


def test_interrupted_processing_and_unknown_document_retry(local):
    doc = indexed(local)
    indexer.update_document(doc.id, ingestion_status="PROCESSING", ingestion_stage="embedding")
    result = local[2].get(f"/library/documents/{doc.id}").json()
    assert result["ingestion_status"] == "FAILED"
    assert result["ingestion_error"]["error_type"] == "MATERIAL_PROCESSING_INTERRUPTED"
    assert run(doc.id).ingestion_status == "READY"
    assert local[2].post("/library/documents/not-found/process").status_code == 404


def test_upload_response_precedes_heavy_work_and_duplicate_coalesces(local, monkeypatch):
    # Exercise ASGI send ordering; TestClient itself waits for background completion.
    _, calls, _ = local
    sent = []
    actual = embedder._call_embedding_api

    def fake(config, model, texts):
        assert any(event["type"] == "http.response.body" for event in sent)
        doc = indexer.list_documents()["items"][0]
        duplicate = BackgroundTasks()
        assert ingestion.schedule_processing(doc.id, duplicate).ingestion_status == "PROCESSING"
        assert not duplicate.tasks
        return actual(config, model, texts)

    monkeypatch.setattr(embedder, "_call_embedding_api", fake)
    from starlette.datastructures import UploadFile
    from io import BytesIO
    from starlette.responses import JSONResponse

    async def invoke():
        background = BackgroundTasks()
        result = await router.upload_document(background, UploadFile(BytesIO(TEXT.encode()), filename="test.txt"))
        assert calls["embedding"] == 0
        response = JSONResponse(result.model_dump(), background=background)
        async def send(event): sent.append(event)
        await response({"type": "http"}, None, send)
        assert indexer.get_document(result.document.id).ingestion_status == "READY"

    asyncio.run(invoke())


def test_delete_waits_for_running_ingestion_then_removes_everything(local, monkeypatch):
    doc = indexed(local)
    started, release = threading.Event(), threading.Event()
    original = embedder._call_embedding_api

    def delayed(*args):
        started.set()
        assert release.wait(10)
        return original(*args)

    monkeypatch.setattr(embedder, "_call_embedding_api", delayed)
    background = BackgroundTasks()
    ingestion.schedule_processing(doc.id, background)
    with ThreadPoolExecutor(max_workers=2) as executor:
        worker = executor.submit(asyncio.run, background())
        assert started.wait(10)
        deletion = executor.submit(ingestion.delete_material_document, doc.id)
        release.set()
        worker.result(timeout=10)
        assert deletion.result(timeout=10)
    assert indexer.get_document(doc.id) is None
    assert not ingestion.assets_directory(doc.id).exists()
    assert embedder.get_embedding_info(doc.id) is None


def test_slow_document_does_not_block_other_upload_or_duplicate_retry(local, monkeypatch):
    doc = indexed(local)
    started, release = threading.Event(), threading.Event()
    actual = embedder._call_embedding_api

    def delayed(*args):
        started.set()
        assert release.wait(10)
        return actual(*args)

    monkeypatch.setattr(embedder, "_call_embedding_api", delayed)
    first = BackgroundTasks()
    ingestion.schedule_processing(doc.id, first)
    from starlette.datastructures import UploadFile
    from io import BytesIO
    other = BackgroundTasks()
    with ThreadPoolExecutor(max_workers=2) as executor:
        worker = executor.submit(asyncio.run, first())
        try:
            assert started.wait(10)
            duplicate = BackgroundTasks()
            response = executor.submit(ingestion.schedule_processing, doc.id, duplicate).result(timeout=3)
            assert response.ingestion_status == "PROCESSING" and not duplicate.tasks
            upload = router.upload_document(other, UploadFile(BytesIO(TEXT.encode()), filename="other.txt"))
            response = executor.submit(asyncio.run, upload).result(timeout=3)
            assert response.document.ingestion_status == "PROCESSING"
        finally:
            release.set()
            worker.result(timeout=10)
    asyncio.run(other())
    assert indexer.get_document(response.document.id).ingestion_status == "READY"


def test_deleted_after_status_snapshot_returns_not_found(local, monkeypatch):
    doc = indexed(local)
    ready = run(doc.id)
    actual = ingestion.document_readiness
    def delete_then_read(snapshot):
        ingestion.delete_material_document(snapshot.id)
        return actual(snapshot)
    monkeypatch.setattr(router, "document_readiness", delete_then_read)
    assert local[2].get(f"/library/documents/{doc.id}").status_code == 404
    assert ingestion.documents_readiness([ready]) == []


def test_deletion_shares_index_update_lock(local):
    doc = indexed(local)
    started = threading.Event()
    def delete():
        started.set()
        return indexer.delete_document(doc.id)
    with ThreadPoolExecutor(max_workers=1) as executor:
        with indexer._index_lock:
            future = executor.submit(delete)
            assert started.wait(3)
            assert not future.done()
        assert future.result(timeout=3)
    assert indexer.get_document(doc.id) is None


@pytest.mark.parametrize("difference", [-1, 1])
def test_each_api_batch_checks_count_before_concatenation(local, monkeypatch, difference):
    # Load the actual transport code and replace only HTTP, so the offline runner's
    # default API sentinel remains installed for every other test.
    namespace = {"__name__": "synthetic_embedding_transport"}
    exec(compile(Path(embedder.__file__).read_text(encoding="utf-8"), embedder.__file__, "exec"), namespace)
    calls = []
    def fake_http(*args, **kwargs):
        calls.append(kwargs["json"]["input"])
        class Response:
            def raise_for_status(self): pass
            def json(self):
                return {"data": [{"index": i, "embedding": [1.0]} for i in range(10 + difference)]}
        return Response()
    monkeypatch.setattr(embedder.httpx, "post", fake_http)
    with pytest.raises(ValueError, match="batch vector count mismatch"):
        namespace["_call_embedding_api"](EmbeddingConfig(base_url="https://synthetic.invalid"), "fake", ["合成"] * 11)
    assert len(calls) == 1


def test_protected_generation_retrieval_and_legacy_owners_unchanged():
    from p1_ranking_approval import DOCUMENT_RETRIEVER_PATH, assert_p1_document_retriever

    root = Path(__file__).resolve().parents[2]
    base = "90b5a2141580610e9c60e1805db7a034affb57b5"
    files = subprocess.check_output(["git", "ls-files", "backend/app/services/generation",
        "backend/app/services/retrieval", "backend/app/routers/generation.py",
        "backend/app/services/library/card_extractor.py", "backend/app/services/library/style_extractor.py",
        "frontend"], cwd=root).decode().splitlines()
    # FRONTEND-ROUTE-01 / PR #10 正式授权以下两个前端文件变化。
    approved_refs = {
        "frontend/src/utils/batchTaskParams.js": "bbe63b297ce60861b375ead5685dea457bb76f00",
        "frontend/tests/utils/batchTaskParams.test.mjs": "bbe63b297ce60861b375ead5685dea457bb76f00",
    }
    for file in files:
        # W3-8C-2B explicitly authorizes these readiness UI files (including new
        # files once staged); their behavior has dedicated Node coverage.
        # W3-8C-3 additionally authorizes only the two generation wiring owners;
        # their production contract is covered by test_production_task_assembly.
        # WRITER-VIEW-01 adds only the two exact presentation paths; independent
        # sent-string decoding and an AST check protect the unchanged contracts.
        if file in {"frontend/src/api.js", "frontend/src/pages/LibraryPage.jsx",
                    "frontend/src/utils/libraryReadiness.js",
                    "frontend/tests/utils/libraryReadiness.test.mjs",
                    "backend/app/services/generation/full_draft_generation.py",
                    "backend/app/services/generation/task_assembly.py",
                    "backend/app/services/generation/full_draft_material.py",
                    "backend/app/services/generation/writer_material_view.py"}:
            continue
        if file == DOCUMENT_RETRIEVER_PATH:
            assert_p1_document_retriever(root)
            continue
        approved_ref = approved_refs.get(file, base)
        before = subprocess.check_output(["git", "show", f"{approved_ref}:{file}"], cwd=root)
        assert (root / file).read_bytes().replace(b"\r\n", b"\n") == before.replace(b"\r\n", b"\n"), file
