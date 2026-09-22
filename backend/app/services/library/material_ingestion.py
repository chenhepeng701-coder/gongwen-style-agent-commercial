"""Single-document ingestion/readiness owner. No task assembly or model writing.

FastAPI runs the synchronous worker in its background thread pool after sending
the response. Jobs are process-local (single-server deployment); an interrupted
job becomes FAILED when queried and requires an explicit retry, never a scan of
the user's library or an automatic external-call retry.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import re
import shutil
import threading
from time import time as _now
import uuid

from app.models.library import LibraryDocument
from app.services.generation import stable_material_assets as stable
from app.services.library import embedder, indexer, semantic_preparation
from app.utils.runtime_paths import get_library_material_assets_dir
from app.utils.storage import atomic_write_text

logger = logging.getLogger(__name__)
MANIFEST_VERSION = "material-ingestion-1"
PROCESSING_LEASE_SECONDS = 300
_jobs: dict[str, str] = {}
_running_jobs: dict[str, str] = {}
_jobs_lock = threading.Lock()


class MaterialProcessingBusy(Exception):
    """An existing legacy embedding/deletion owns the document lifecycle lock."""


def assets_directory(doc_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", doc_id):
        raise ValueError("Invalid document ID")
    root = get_library_material_assets_dir().resolve()
    path = (root / doc_id).resolve()
    if path.parent != root:
        raise ValueError("Invalid material asset path")
    return path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rule_fingerprint() -> str:
    # All source/rule owners affecting parsing, chunk identity and stable assets.
    from app.services.parser import txt_parser, docx_parser
    from app.services.retrieval import official_semantic_chunker, official_chunk_metadata
    modules = (semantic_preparation, txt_parser, docx_parser,
               official_semantic_chunker, official_chunk_metadata,
               stable, stable.source_owner, stable.digest_owner)
    hashes = [_sha(Path(module.__file__)) for module in modules]
    hashes += [stable.source_owner._parser_sha256(), MANIFEST_VERSION, stable.VERSION]
    return hashlib.sha256("\n".join(hashes).encode("utf-8")).hexdigest()


def _fail(doc_id: str, stage: str, error_type: str = "MATERIAL_INGESTION_FAILED"):
    doc = indexer.update_document(
        doc_id, ingestion_status="FAILED", ingestion_stage=stage,
        ingestion_error={"error_type": error_type, "message": "材料处理未完成，请重试。"},
        has_embedding=False, embedding_chunks=0, embedding_model="", embedded_at="",
        processed_at="", ingestion_lease_expires_at=0.0,
    )
    # FAILED must not leave the old embedding endpoint reporting success.
    embedder.delete_embeddings(doc_id)
    (assets_directory(doc_id) / "manifest.json").unlink(missing_ok=True)
    return doc


def invalidate_readiness_for_embedding(doc_id: str) -> None:
    """Legacy embedding cannot keep a completion manifest or a queued worker."""
    with indexer.document_lock(doc_id):
        with _jobs_lock:
            _jobs.pop(doc_id, None)
        indexer.update_document(doc_id, ingestion_status="INDEXED", ingestion_stage="",
                                ingestion_error={}, processed_at="", ingestion_lease_expires_at=0.0)
        (assets_directory(doc_id) / "manifest.json").unlink(missing_ok=True)


def _recover_processing(doc_id: str) -> LibraryDocument | None:
    """Expire abandoned registrations, never preempt a worker holding its lock.

The persisted lease covers a response that never starts its background task.
Running token ownership is recorded only by the actual worker and removed in
finally. Slow live work is not killed just because a stage outlasts the lease.
"""
    lock = indexer.document_lock(doc_id)
    if not lock.acquire(blocking=False):
        return indexer.get_document(doc_id)
    try:
        doc = indexer.get_document(doc_id)
        if doc is None or doc.ingestion_status != "PROCESSING":
            return doc
        with _jobs_lock:
            token = _jobs.get(doc_id)
            if token and (_running_jobs.get(doc_id) == token
                          or doc.ingestion_lease_expires_at > _now()):
                return doc
            _jobs.pop(doc_id, None)
        error = "MATERIAL_PROCESSING_LEASE_EXPIRED" if token else "MATERIAL_PROCESSING_INTERRUPTED"
        return _fail(doc_id, doc.ingestion_stage, error)
    finally:
        lock.release()


def _set_stage(doc_id: str, stage: str) -> None:
    indexer.update_document(doc_id, ingestion_stage=stage,
                            ingestion_lease_expires_at=_now() + PROCESSING_LEASE_SECONDS)


def _load_asset(chunk: dict, directory: Path):
    try:
        return stable.load_stable_material_asset(chunk, directory)
    except stable.FullArticleContractError as exc:
        if str(exc) != "STABLE_MATERIAL_CACHE_INVALID":
            raise
        # Only the deterministic cache for this complete source is replaceable.
        # I/O failures still fail closed. This is local repair, not a model retry.
        key = stable.text_sha(stable.canonical_json(stable._binding(chunk)))
        (directory / (key + ".json")).unlink()
        return stable.load_stable_material_asset(chunk, directory)


def _validate_manifest(doc: LibraryDocument) -> dict:
    directory = assets_directory(doc.id)
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if (manifest["version"] != MANIFEST_VERSION
            or manifest["document_id"] != doc.id
            or manifest["stable_asset_version"] != stable.VERSION
            or manifest["source_fingerprint"] != semantic_preparation.source_fingerprint(doc)
            or manifest["rule_fingerprint"] != rule_fingerprint()):
        raise ValueError("Material manifest is stale")
    embedding_path = embedder._embedding_path(doc.id)
    if manifest["embedding_sha256"] != _sha(embedding_path):
        raise ValueError("Embedding changed after ingestion")
    embedding = embedder.get_embedding_info(doc.id)
    entries = manifest["assets"]
    chunks = embedding["chunks"]
    if (embedding["doc_id"] != doc.id or not chunks
            or len(chunks) != manifest["chunk_count"] or len(entries) != len(chunks)
            or len(set(manifest["stable_asset_cache_keys"])) != len(chunks)
            or manifest["stable_asset_cache_keys"] != [entry["cache_key"] for entry in entries]
            or not doc.has_embedding or doc.embedding_chunks != len(chunks)):
        raise ValueError("Incomplete material manifest")
    for chunk, entry in zip(chunks, entries):
        key = entry["cache_key"]
        if not re.fullmatch(r"[0-9a-f]{64}", key):
            raise ValueError("Invalid asset key")
        path = directory / (key + ".json")
        if _sha(path) != entry["sha256"] or not chunk["vector"]:
            raise ValueError("Missing or corrupt asset/vector")
        envelope = json.loads(path.read_text(encoding="utf-8"))
        payload = envelope["payload"]
        if (payload["document_id"] != doc.id or payload["chunk_id"] != entry["chunk_id"]
                or payload["chunk_text"] != chunk["text"]
                or payload["chunk_id"] != chunk["metadata"]["chunk_id"]):
            raise ValueError("Asset does not match embedding chunk")
    return manifest


@dataclass(frozen=True)
class _SelectedMaterialAsset:
    """In-memory read binding for retrieval's projection, not a new stable asset.

    The persisted payload/key stay unchanged. Only the selected-source lookup
    key differs, because retrieval projects/normalizes ingestion metadata.
    """
    asset: stable.StableMaterialAsset
    source_json: str
    selected_binding_json: str

    @property
    def cache_key(self):
        return stable.text_sha(self.selected_binding_json)

    def source_for(self, chunk):
        if stable.canonical_json(stable._binding(chunk)) != self.selected_binding_json:
            raise stable.FullArticleContractError("STABLE_MATERIAL_ASSET_INPUT_CHANGED")
        return self.asset.source_for(json.loads(self.source_json))


def read_selected_material_assets(retrieval):
    """Read verified W3-8C-2A assets; never process, repair or change readiness.

    Reuse the manifest validator and actual retrieval projection owners. Current
    selected text/roles/limitations remain untouched and confer no new authority.
    Source fingerprinting reads bytes for freshness; it does not parse the file.
    """
    from app.services.retrieval.retriever import _chunk_result
    from app.services.retrieval.document_retriever import _selected_chunk

    selected = retrieval.get("selected_chunks") or []
    if not selected:
        raise stable.FullArticleContractError("FULL_DRAFT_STABLE_ASSETS_REQUIRED")
    documents = {}
    try:
        for chunk in selected:
            doc_id = chunk["document_id"]
            assets_directory(doc_id)  # Validate identity before index/path access.
            if chunk.get("doc_id", doc_id) != doc_id:
                raise ValueError("Conflicting document identity")
            documents.setdefault(doc_id, []).append(chunk)
        result = {}
        for doc_id, chunks in documents.items():
            with indexer.document_lock(doc_id):
                doc = indexer.get_document(doc_id)
                if doc is None or doc.ingestion_status != "READY":
                    raise ValueError("Material is not READY")
                manifest = _validate_manifest(doc)
                entries = {entry["chunk_id"]: entry for entry in manifest["assets"]}
                stored = {row["metadata"]["chunk_id"]: row
                          for row in embedder.get_embedding_info(doc_id)["chunks"]}
                for chunk in chunks:
                    entry = entries[chunk["chunk_id"]]
                    path = assets_directory(doc_id) / (entry["cache_key"] + ".json")
                    raw = path.read_bytes()
                    if hashlib.sha256(raw).hexdigest() != entry["sha256"]:
                        raise ValueError("Asset changed during read")
                    envelope = json.loads(raw)
                    payload = envelope["payload"]
                    if (set(envelope) != {"payload", "sha256"}
                            or envelope["sha256"] != stable.text_sha(stable.canonical_json(payload))):
                        raise ValueError("Asset checksum")
                    source = {**payload["source_metadata"]["chunk"],
                              "metadata": payload["source_metadata"]["metadata"],
                              "document_id": payload["document_id"],
                              "chunk_id": payload["chunk_id"], "chunk_text": payload["chunk_text"]}
                    asset = stable.StableMaterialAsset(stable.canonical_json(payload), entry["cache_key"])
                    asset.source_for(source)  # Validate schema, original key and current rule fingerprints.
                    expected = _selected_chunk(
                        _chunk_result(doc, doc_id, stored[chunk["chunk_id"]]),
                        {"rank": chunk["document_rank"], "document_score": chunk["document_score"]},
                        chunk["document_role"], chunk["raw_rank"])
                    binding = stable.canonical_json(stable._binding(chunk))
                    if (binding != stable.canonical_json(stable._binding(expected))
                            or chunk["metadata"] != expected["metadata"]):
                        raise ValueError("Selected source does not match ingestion")
                    result[id(chunk)] = _SelectedMaterialAsset(
                        asset, stable.canonical_json(source), binding)
        return tuple(result[id(chunk)] for chunk in selected)
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise stable.FullArticleContractError("FULL_DRAFT_STABLE_ASSETS_UNAVAILABLE") from exc


def document_readiness(doc: LibraryDocument) -> LibraryDocument:
    """Validate READY without rebuilding chunks/assets or making external calls."""
    doc = _recover_processing(doc.id)
    if doc is None:
        raise FileNotFoundError("文档不存在")
    if doc.ingestion_status != "READY":
        return doc
    with indexer.document_lock(doc.id):
        current = _recover_processing(doc.id)
        if current is None:
            raise FileNotFoundError("文档不存在")
        if current.ingestion_status == "READY":
            try:
                _validate_manifest(current)
            except (OSError, ValueError, TypeError, KeyError):
                return _fail(doc.id, "validation", "MATERIAL_ASSETS_STALE_OR_INCOMPLETE")
        return current


def schedule_processing(doc_id: str, background_tasks) -> LibraryDocument:
    """Persist PROCESSING before returning; coalesce duplicate active requests."""
    if _recover_processing(doc_id) is None:
        raise FileNotFoundError("文档不存在")
    with _jobs_lock:
        if doc_id in _jobs:
            doc = indexer.get_document(doc_id)
            if doc is None:
                raise FileNotFoundError("文档不存在")
            return doc
    lock = indexer.document_lock(doc_id)
    if not lock.acquire(blocking=False):
        with _jobs_lock:
            if doc_id in _jobs:
                return indexer.get_document(doc_id)
        raise MaterialProcessingBusy()
    try:
        with _jobs_lock:
            if doc_id in _jobs:
                return indexer.get_document(doc_id)
            token = uuid.uuid4().hex
            doc = indexer.update_document(
                doc_id, ingestion_status="PROCESSING", ingestion_stage="queued",
                ingestion_error={}, has_embedding=False, embedding_chunks=0, processed_at="",
                ingestion_lease_expires_at=_now() + PROCESSING_LEASE_SECONDS,
            )
            _jobs[doc_id] = token
        try:
            background_tasks.add_task(process_document, doc_id, token)
        except Exception:
            with _jobs_lock:
                _jobs.pop(doc_id, None)
            _fail(doc_id, "queued")
            raise
        return doc
    finally:
        lock.release()


def documents_readiness(documents: list[LibraryDocument]) -> list[LibraryDocument]:
    """A document removed after the list snapshot should not fail the whole page."""
    result = []
    for doc in documents:
        try:
            result.append(document_readiness(doc))
        except FileNotFoundError:
            continue
    return result


def process_document(doc_id: str, token: str) -> None:
    """One attempt, shared prepared chunks, final manifest before READY commit."""
    with indexer.document_lock(doc_id):
        with _jobs_lock:
            if _jobs.get(doc_id) != token:
                return
        _recover_processing(doc_id)
        with _jobs_lock:
            if _jobs.get(doc_id) != token:
                return
            _running_jobs[doc_id] = token
        stage = "semantic_chunk"
        try:
            doc = indexer.get_document(doc_id)
            if doc is None:
                return
            _set_stage(doc_id, stage)
            rules = rule_fingerprint()
            prepared = semantic_preparation.prepare_semantic_chunks(doc)
            directory = assets_directory(doc_id)
            # A prior manifest is not a completion marker for the current attempt.
            (directory / "manifest.json").unlink(missing_ok=True)
            stage = "embedding"
            _set_stage(doc_id, stage)
            embedder.generate_embeddings(doc_id, prepared=prepared)
            stage = "stable_assets"
            _set_stage(doc_id, stage)
            entries = []
            for chunk in prepared.chunks:
                loaded = _load_asset(chunk, directory)
                loaded.asset.source_for(chunk)
                key = loaded.asset.cache_key
                entries.append({"chunk_id": chunk["chunk_id"], "cache_key": key,
                                "sha256": _sha(directory / (key + ".json"))})
            stage = "validation"
            _set_stage(doc_id, stage)
            current = indexer.get_document(doc_id)
            if (prepared.source_fingerprint != semantic_preparation.source_fingerprint(current)
                    or rules != rule_fingerprint()):
                raise ValueError("Source/rules changed during ingestion")
            processed_at = datetime.now(timezone.utc).isoformat()
            manifest = {
                "version": MANIFEST_VERSION, "document_id": doc_id,
                "chunk_count": len(prepared.chunks), "assets": entries,
                "stable_asset_cache_keys": [entry["cache_key"] for entry in entries],
                "stable_asset_version": stable.VERSION,
                "source_fingerprint": prepared.source_fingerprint,
                "rule_fingerprint": rules, "processed_at": processed_at,
                "embedding_sha256": _sha(embedder._embedding_path(doc_id)),
            }
            atomic_write_text(directory / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            _validate_manifest(current)
            indexer.update_document(doc_id, ingestion_status="READY", ingestion_stage="complete",
                                    ingestion_error={}, processed_at=processed_at,
                                    ingestion_lease_expires_at=0.0)
        except Exception as exc:
            # Public state never contains source text, prompts, keys or raw provider errors.
            logger.error("MATERIAL_INGESTION_FAILED stage=%s type=%s", stage, type(exc).__name__)
            if indexer.get_document(doc_id) is not None:
                _fail(doc_id, stage)
        finally:
            with _jobs_lock:
                if _running_jobs.get(doc_id) == token:
                    _running_jobs.pop(doc_id)
                if _jobs.get(doc_id) == token:
                    _jobs.pop(doc_id)


def delete_material_document(doc_id: str) -> bool:
    """Serialize deletion with workers; remove all document-scoped runtime assets."""
    from app.services.library.card_extractor import delete_cards
    from app.services.library.style_extractor import delete_style

    with indexer.document_lock(doc_id):
        if indexer.get_document(doc_id) is None:
            return False
        with _jobs_lock:
            _jobs.pop(doc_id, None)
        _fail(doc_id, "deleting")
        directory = assets_directory(doc_id)
        if directory.exists():
            shutil.rmtree(directory)
        embedder.delete_embeddings(doc_id)
        delete_cards(doc_id)
        delete_style(doc_id)
        return indexer.delete_document(doc_id)
