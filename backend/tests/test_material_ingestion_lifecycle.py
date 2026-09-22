"""R1 external-review reproductions, synthetic sources and offline fake vectors."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import threading

from fastapi import BackgroundTasks
import pytest
from pydantic import ValidationError
from starlette.datastructures import UploadFile
from starlette.responses import JSONResponse

from app.services.library import embedder, indexer, material_ingestion as ingestion
from app.routers import library as router
from test_material_ingestion import TEXT, indexed, local, manifest, run


@pytest.mark.parametrize("recovery", ["query", "retry"])
def test_response_send_failure_abandoned_job_expires_and_retries(local, monkeypatch, recovery):
    clock = [1000.0]
    monkeypatch.setattr(ingestion, "_now", lambda: clock[0], raising=False)
    background = BackgroundTasks()

    async def failed_response():
        result = await router.upload_document(
            background, UploadFile(BytesIO(TEXT.encode()), filename="abandoned.txt"))
        async def fail_send(event):
            raise ConnectionError("synthetic disconnected client")
        with pytest.raises(ConnectionError, match="disconnected"):
            await JSONResponse(result.model_dump(), background=background)({"type": "http"}, None, fail_send)
        return result.document

    doc = asyncio.run(failed_response())
    old_token = ingestion._jobs[doc.id]
    try:
        assert local[1]["embedding"] == 0
        assert doc.ingestion_status == "PROCESSING"
        assert getattr(doc, "ingestion_lease_expires_at", 0) > clock[0]
        # Still within the lease: queries/retries do not claim failure or duplicate jobs.
        assert local[2].get(f"/library/documents/{doc.id}").json()["ingestion_status"] == "PROCESSING"
        duplicate = BackgroundTasks()
        ingestion.schedule_processing(doc.id, duplicate)
        assert not duplicate.tasks and ingestion._jobs[doc.id] == old_token

        clock[0] = doc.ingestion_lease_expires_at + 1
        failures = []
        actual_fail = ingestion._fail
        def observe_failure(*args, **kwargs):
            result = actual_fail(*args, **kwargs)
            failures.append(result)
            return result
        monkeypatch.setattr(ingestion, "_fail", observe_failure)
        if recovery == "query":
            state = local[2].get(f"/library/documents/{doc.id}").json()
            assert state["ingestion_status"] == "FAILED"
            assert state["ingestion_error"]["error_type"] == "MATERIAL_PROCESSING_LEASE_EXPIRED"
            assert doc.id not in ingestion._jobs
        replacement = BackgroundTasks()
        ingestion.schedule_processing(doc.id, replacement)
        assert failures and failures[0].ingestion_status == "FAILED"
        new_token = ingestion._jobs[doc.id]
        assert new_token != old_token
        # The old response may resume late; it cannot run or invalidate the new job.
        asyncio.run(background())
        assert local[1]["embedding"] == 0
        assert ingestion._jobs[doc.id] == new_token
        asyncio.run(replacement())
        assert indexer.get_document(doc.id).ingestion_status == "READY"
        assert local[1]["embedding"] == 1
    finally:
        asyncio.run(background())


def test_expired_background_cannot_start_without_a_query(local, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(ingestion, "_now", lambda: clock[0], raising=False)
    doc = indexed(local)
    background = BackgroundTasks()
    ingestion.schedule_processing(doc.id, background)
    clock[0] += 100000
    asyncio.run(background())
    assert indexer.get_document(doc.id).ingestion_status == "FAILED"
    assert local[1]["embedding"] == 0
    assert doc.id not in ingestion._jobs
    assert run(doc.id).ingestion_status == "READY"


def test_running_worker_is_not_expired_by_elapsed_queue_lease(local, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(ingestion, "_now", lambda: clock[0], raising=False)
    doc = indexed(local)
    started, release = threading.Event(), threading.Event()
    actual = embedder._call_embedding_api
    def slow(*args):
        started.set()
        assert release.wait(10)
        # Also check the reentrant-lock path from the worker thread itself.
        assert ingestion.document_readiness(indexer.get_document(doc.id)).ingestion_status == "PROCESSING"
        return actual(*args)
    monkeypatch.setattr(embedder, "_call_embedding_api", slow)
    background = BackgroundTasks()
    ingestion.schedule_processing(doc.id, background)
    token = ingestion._jobs[doc.id]
    with ThreadPoolExecutor(max_workers=1) as executor:
        worker = executor.submit(asyncio.run, background())
        try:
            assert started.wait(10)
            clock[0] += 100000
            for _ in range(2):
                assert local[2].get(f"/library/documents/{doc.id}").json()["ingestion_status"] == "PROCESSING"
                assert local[2].post(f"/library/documents/{doc.id}/process").status_code == 202
                assert ingestion._jobs[doc.id] == token
            assert local[1]["embedding"] == 0
        finally:
            release.set()
            worker.result(timeout=10)
    assert local[1]["embedding"] == 1
    assert indexer.get_document(doc.id).ingestion_status == "READY"
    assert not ingestion._running_jobs


@pytest.mark.parametrize("failure", ["asset", "manifest_write", "manifest_validation"])
def test_failure_after_embedding_cleans_success_artifacts(local, monkeypatch, failure):
    doc = indexed(local)
    observed = []
    if failure == "asset":
        name, actual = "_load_asset", ingestion._load_asset
    elif failure == "manifest_write":
        name, actual = "atomic_write_text", ingestion.atomic_write_text
    else:
        name, actual = "_validate_manifest", ingestion._validate_manifest
    def fail_after_vector(*args, **kwargs):
        assert embedder._embedding_path(doc.id).exists()
        assert indexer.get_document(doc.id).has_embedding
        observed.append(True)
        raise OSError("synthetic asset/manifest failure")
    monkeypatch.setattr(ingestion, name, fail_after_vector)
    failed = run(doc.id)
    assert observed and failed.ingestion_status == "FAILED" and not failed.has_embedding
    assert not embedder._embedding_path(doc.id).exists()
    assert not (ingestion.assets_directory(doc.id) / "manifest.json").exists()
    status = local[2].get(f"/library/documents/{doc.id}/embed").json()
    assert status["success"] is False and status["chunks"] == 0
    monkeypatch.setattr(ingestion, name, actual)
    assert run(doc.id).ingestion_status == "READY"
    assert local[1]["embedding"] == 2


@pytest.mark.parametrize("endpoint", ["single", "batch"])
@pytest.mark.parametrize("succeeds", [True, False])
def test_legacy_embedding_invalidates_manifest_before_api_and_allows_process(local, monkeypatch, endpoint, succeeds):
    doc = indexed(local)
    assert run(doc.id).ingestion_status == "READY"
    directory = ingestion.assets_directory(doc.id)
    original_keys = manifest(doc.id)["stable_asset_cache_keys"]
    actual = embedder._call_embedding_api
    calls = []
    def inspect_invalidated(*args):
        assert not (directory / "manifest.json").exists()
        assert indexer.get_document(doc.id).ingestion_status != "READY"
        calls.append(True)
        if not succeeds:
            raise ValueError("synthetic embedding failure")
        return actual(*args)
    monkeypatch.setattr(embedder, "_call_embedding_api", inspect_invalidated)
    if endpoint == "batch":
        # Existing batch API skips has_embedding=true; reproduce a stale READY
        # manifest paired with a document selected for re-embedding.
        indexer.update_document(doc.id, has_embedding=False)
        if succeeds:
            response = local[2].post("/library/embed-all")
            assert response.status_code == 200 and response.json()["embedded"] == 1
        else:
            # Confirmed before R1: public_batch_error returns dict while the old
            # response declares list[str]. Preserve/report this separate debt;
            # still assert actual FAILED state and cache cleanup below.
            with pytest.raises(ValidationError, match=r"errors\.0"):
                local[2].post("/library/embed-all")
    else:
        response = local[2].post(f"/library/documents/{doc.id}/embed")
        assert response.status_code == (200 if succeeds else 400)
    assert calls
    assert not (directory / "manifest.json").exists()
    assert indexer.get_document(doc.id).ingestion_status == ("INDEXED" if succeeds else "FAILED")
    # Content-addressed caches may survive, but no manifest certifies them READY.
    monkeypatch.setattr(embedder, "_call_embedding_api", actual)
    assert run(doc.id).ingestion_status == "READY"
    assert manifest(doc.id)["stable_asset_cache_keys"] == original_keys


def test_actual_legacy_embedding_lock_returns_409_without_scheduling(local, monkeypatch):
    doc = indexed(local)
    assert run(doc.id).ingestion_status == "READY"
    started, release = threading.Event(), threading.Event()
    actual = embedder._call_embedding_api
    def slow(*args):
        started.set()
        assert release.wait(10)
        return actual(*args)
    monkeypatch.setattr(embedder, "_call_embedding_api", slow)
    scheduled = []
    add_task = BackgroundTasks.add_task
    def record_task(self, *args, **kwargs):
        scheduled.append(args)
        return add_task(self, *args, **kwargs)
    monkeypatch.setattr(BackgroundTasks, "add_task", record_task)
    with ThreadPoolExecutor(max_workers=1) as executor:
        worker = executor.submit(embedder.generate_embeddings, doc.id)
        try:
            assert started.wait(10)
            before = indexer.get_document(doc.id).model_dump()
            for _ in range(2):
                response = local[2].post(f"/library/documents/{doc.id}/process")
                assert response.status_code == 409
                assert response.json()["detail"]["error_type"] == "MATERIAL_PROCESSING_BUSY"
                assert indexer.get_document(doc.id).model_dump() == before
            assert not scheduled and doc.id not in ingestion._jobs
        finally:
            release.set()
            assert worker.result(timeout=10)["success"]
    response = local[2].post(f"/library/documents/{doc.id}/process")
    assert response.status_code == 202 and len(scheduled) == 1
    assert indexer.get_document(doc.id).ingestion_status == "READY"


def test_legacy_embedding_cancels_unstarted_process_token(local):
    doc = indexed(local)
    assert run(doc.id).ingestion_status == "READY"
    delayed_background = BackgroundTasks()
    ingestion.schedule_processing(doc.id, delayed_background)
    assert local[2].post(f"/library/documents/{doc.id}/embed").status_code == 200
    assert doc.id not in ingestion._jobs
    assert not (ingestion.assets_directory(doc.id) / "manifest.json").exists()
    count = local[1]["embedding"]
    asyncio.run(delayed_background())
    assert local[1]["embedding"] == count
    assert indexer.get_document(doc.id).ingestion_status == "INDEXED"
    assert run(doc.id).ingestion_status == "READY"
