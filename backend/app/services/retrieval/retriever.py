"""Semantic retrieval engine backed by stored embeddings."""

from __future__ import annotations

import json
import logging
import math
import time
from pathlib import Path
from typing import Any

from app.models.library import LibraryDocument
from app.config import get_settings
from app.services.library.embedder import _call_embedding_api, _get_embedding_config, generate_embeddings
from app.services.library.indexer import list_documents
from app.services.retrieval.bm25_index import BM25Index
from app.services.retrieval.hybrid_ranker import reciprocal_rank_fusion, result_identity
from app.services.retrieval.official_chunk_metadata import normalize_chunk_metadata
from app.utils.runtime_paths import get_library_embeddings_dir

logger = logging.getLogger(__name__)

_DOCX_BINARY_MARKERS = ("PK\x03\x04", "docProps/", "word/styles.xml", "word/document.xml")
_BM25_CACHE: dict[str, Any] = {
    "fingerprint": None,
    "parameters": None,
    "index": None,
    "build_count": 0,
    "cache_hit_count": 0,
    "build_elapsed_ms": None,
}


class RetrievalError(RuntimeError):
    """Raised when every requested retrieval channel fails."""


def _load_all_embeddings() -> dict[str, dict]:
    """Load all embedding files into memory."""
    emb_dir = get_library_embeddings_dir()
    all_emb = {}
    for file_path in sorted(emb_dir.glob("*.json")):
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
            doc_id = data.get("doc_id", file_path.stem)
            chunks = data.get("chunks", [])
            if chunks:
                all_emb[doc_id] = data
        except Exception:
            continue
    return all_emb


def _embedding_path(doc_id: str) -> Path:
    return get_library_embeddings_dir() / f"{doc_id}.json"


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _embed_query(query: str) -> list[float]:
    """Generate embedding vector for a query string."""
    provider, model = _get_embedding_config()
    vectors = _call_embedding_api(provider, model, [query])
    return vectors[0]


def _text_filter_reason(text: str) -> str:
    sample = (text or "").strip()
    if not sample:
        return "empty_text"

    window = sample[:500]
    if any(marker in window for marker in _DOCX_BINARY_MARKERS):
        return "binary_docx_payload"

    allowed_controls = {"\n", "\r", "\t"}
    control_count = sum(1 for char in window if ord(char) < 32 and char not in allowed_controls)
    if control_count > max(3, len(window) // 20):
        return "non_printable_text"

    printable_count = sum(1 for char in window if char.isprintable() or char in allowed_controls)
    printable_ratio = printable_count / max(len(window), 1)
    if printable_ratio < 0.9:
        return "non_printable_text"

    return ""


def _looks_like_text(text: str) -> bool:
    return not _text_filter_reason(text)


def _is_stale_embedding_payload(emb_data: dict[str, Any]) -> bool:
    chunks = emb_data.get("chunks", [])
    if not chunks:
        return False

    inspected = chunks[: min(3, len(chunks))]
    if not inspected:
        return False

    reasons = [_text_filter_reason(str(chunk.get("text", ""))) for chunk in inspected]
    if any(reason == "binary_docx_payload" for reason in reasons):
        return True

    return all(reason in {"empty_text", "non_printable_text"} for reason in reasons)


def _reload_embedding_file(doc_id: str) -> dict[str, Any] | None:
    path = _embedding_path(doc_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _ensure_valid_embedding_payload(doc_id: str, emb_data: dict[str, Any]) -> tuple[dict[str, Any], bool, str]:
    if not _is_stale_embedding_payload(emb_data):
        return emb_data, False, ""

    logger.warning("STALE_EMBEDDINGS_DETECTED doc_id=%s", doc_id)
    try:
        generate_embeddings(doc_id)
    except Exception as exc:
        reason = f"rebuild_failed:{exc}"
        logger.warning("STALE_EMBEDDINGS_REBUILD_FAILED doc_id=%s reason=%s", doc_id, reason)
        return emb_data, False, reason

    reloaded = _reload_embedding_file(doc_id)
    if not reloaded:
        logger.warning("STALE_EMBEDDINGS_REBUILD_FAILED doc_id=%s reason=missing_rebuilt_file", doc_id)
        return emb_data, False, "missing_rebuilt_file"

    logger.info("STALE_EMBEDDINGS_REBUILT doc_id=%s", doc_id)
    return reloaded, True, ""


def _make_filtered_entry(doc: LibraryDocument, chunk: dict[str, Any], reason: str) -> dict[str, Any]:
    text = str(chunk.get("text", "") or "")
    metadata = normalize_chunk_metadata(
        chunk,
        doc_id=doc.id,
        title=doc.title,
        file_path=doc.file_path,
        chunk_index=int(chunk.get("chunk_index", 0) or 0),
    )
    return {
        "doc_id": doc.id,
        "title": doc.title,
        "file_path": doc.file_path,
        "chunk_index": chunk.get("chunk_index", 0),
        "chunk_text": text,
        "score": None,
        "filtered": True,
        "filter_reason": reason,
        "metadata": metadata,
        "chunk_id": metadata.get("chunk_id", ""),
        "source_file": metadata.get("source_file", ""),
        "document_type": metadata.get("document_type", "未知"),
        "level": metadata.get("level", "未知"),
        "org_type": metadata.get("org_type", "未知"),
        "business_domain": metadata.get("business_domain", "未知"),
        "writing_purpose": metadata.get("writing_purpose", "未知"),
        "paragraph_type": metadata.get("paragraph_type", "unknown"),
        "section_heading": metadata.get("section_heading", ""),
        "section_index": metadata.get("section_index", 0),
        "schema_version": metadata.get("schema_version", "legacy"),
    }


def _empty_payload(query: str, *, retrieval_mode: str = "vector_only") -> dict[str, Any]:
    return {
        "query": query,
        "results": [],
        "raw_results_count": 0,
        "filtered_samples": [],
        "top_score": 0.0,
        "top_source": "",
        "rebuilt_doc_ids": [],
        "retrieval_mode": retrieval_mode,
        "vector_candidate_count": 0,
        "bm25_candidate_count": 0,
        "merged_candidate_count": 0,
        "final_result_count": 0,
        "fallback_to_vector_only": False,
        "fallback_reason": "",
        "vector_failed": False,
        "bm25_failed": False,
        "degraded": False,
        "performance": {},
    }


def _embedding_fingerprint() -> tuple[int, int, int]:
    files = list(get_library_embeddings_dir().glob("*.json"))
    stats = [file_path.stat() for file_path in files]
    return (len(files), sum(stat.st_size for stat in stats), max((stat.st_mtime_ns for stat in stats), default=0))


def _chunk_result(doc: LibraryDocument, doc_id: str, chunk: dict[str, Any]) -> dict[str, Any]:
    chunk_text = str(chunk.get("text", "") or "")
    metadata = normalize_chunk_metadata(
        chunk,
        doc_id=doc_id,
        title=doc.title,
        file_path=doc.file_path,
        chunk_index=int(chunk.get("chunk_index", 0) or 0),
    )
    return {
        "doc_id": doc_id,
        "title": doc.title,
        "file_path": doc.file_path,
        "file_type": doc.file_type,
        "word_count": doc.word_count,
        "scene_tags": doc.scene_tags,
        "chunk_index": chunk.get("chunk_index", 0),
        "chunk_text": chunk_text,
        "filtered": False,
        "filter_reason": "",
        "metadata": metadata,
        "chunk_id": metadata.get("chunk_id", ""),
        "source_file": metadata.get("source_file", ""),
        "document_type": metadata.get("document_type", "未知"),
        "level": metadata.get("level", "未知"),
        "org_type": metadata.get("org_type", "未知"),
        "business_domain": metadata.get("business_domain", "未知"),
        "writing_purpose": metadata.get("writing_purpose", "未知"),
        "paragraph_type": metadata.get("paragraph_type", "unknown"),
        "section_heading": metadata.get("section_heading", ""),
        "section_path": metadata.get("section_path", []),
        "section_index": metadata.get("section_index", 0),
        "content_role": metadata.get("content_role", "unknown"),
        "is_attachment": bool(metadata.get("is_attachment", False)),
        "paragraph_start": metadata.get("paragraph_start"),
        "paragraph_end": metadata.get("paragraph_end"),
        "schema_version": metadata.get("schema_version", "legacy"),
    }


def _get_bm25_index(all_emb: dict[str, dict], docs: dict[str, LibraryDocument]) -> BM25Index:
    fingerprint = _embedding_fingerprint()
    config = get_settings().retrieval
    parameters = (
        float(config.bm25_k1),
        float(config.bm25_b),
        float(config.bm25_title_weight),
    )
    cached = _BM25_CACHE.get("index")
    if (
        cached is not None
        and _BM25_CACHE.get("fingerprint") == fingerprint
        and _BM25_CACHE.get("parameters") == parameters
    ):
        _BM25_CACHE["cache_hit_count"] = int(_BM25_CACHE.get("cache_hit_count") or 0) + 1
        return cached

    started = time.perf_counter()
    documents: list[dict[str, Any]] = []
    for doc_id, payload in all_emb.items():
        doc = docs.get(doc_id)
        if doc is None:
            continue
        for chunk in payload.get("chunks", []):
            text = str(chunk.get("text", "") or "")
            if _text_filter_reason(text):
                continue
            documents.append(_chunk_result(doc, doc_id, chunk))
    index = BM25Index(
        documents,
        k1=parameters[0],
        b=parameters[1],
        title_weight=parameters[2],
    )
    _BM25_CACHE.update({
        "fingerprint": fingerprint,
        "parameters": parameters,
        "index": index,
        "build_count": int(_BM25_CACHE.get("build_count") or 0) + 1,
        "build_elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
    })
    return index


def _resolve_retrieval_mode(requested: str | None) -> str:
    configured = requested if requested is not None else get_settings().retrieval.mode
    mode = str(configured or "vector_only").strip().lower()
    if mode not in {"vector_only", "bm25_only", "hybrid"}:
        raise ValueError(f"unknown retrieval.mode: {configured!r}")
    return mode


def _as_bm25_results(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for item in items:
        result = dict(item)
        score = float(result.get("bm25_score") or 0.0)
        result["score"] = score
        result["score_type"] = "bm25"
        result["matched_channels"] = ["bm25"]
        result["retrieval_sources"] = "bm25"
        result.setdefault("vector_rank", None)
        result.setdefault("vector_score", None)
        result.setdefault("rrf_score", None)
        results.append(result)
    return results


def search_detailed(query: str, top_k: int = 20, retrieval_mode: str | None = None) -> dict[str, Any]:
    """Vector or hybrid search with diagnostics and safe fallback."""
    normalized_query = (query or "").strip()
    retrieval_cfg = get_settings().retrieval
    mode = _resolve_retrieval_mode(retrieval_mode)
    if not normalized_query:
        return _empty_payload(normalized_query, retrieval_mode=mode)

    docs = {doc.id: doc for doc in list_documents().get("items", [])}
    all_emb = _load_all_embeddings()
    if not all_emb:
        return _empty_payload(normalized_query, retrieval_mode=mode)

    query_vector: list[float] | None = None
    vector_error = ""
    vector_started = time.perf_counter()
    if mode != "bm25_only":
        try:
            query_vector = _embed_query(normalized_query)
        except Exception as exc:
            vector_error = f"{type(exc).__name__}: {exc}"
            logger.warning("VECTOR_RETRIEVAL_FAILED error_type=%s", type(exc).__name__)
            if mode == "vector_only":
                raise ValueError(f"查询向量化失败: {exc}") from exc
    vector_elapsed_ms = round((time.perf_counter() - vector_started) * 1000, 3)

    scored_results: list[dict[str, Any]] = []
    filtered_samples: list[dict[str, Any]] = []
    rebuilt_doc_ids: list[str] = []

    for doc_id, emb_data in all_emb.items() if mode != "bm25_only" else []:
        doc = docs.get(doc_id)
        if doc is None:
            continue

        emb_payload, rebuilt, rebuild_reason = _ensure_valid_embedding_payload(doc_id, emb_data)
        if rebuilt:
            rebuilt_doc_ids.append(doc_id)
        elif rebuild_reason:
            logger.warning("STALE_EMBEDDINGS_USING_OLD_PAYLOAD doc_id=%s reason=%s", doc_id, rebuild_reason)

        for chunk in emb_payload.get("chunks", []):
            chunk_text = str(chunk.get("text", "") or "")
            filter_reason = _text_filter_reason(chunk_text)
            if filter_reason:
                if len(filtered_samples) < 20:
                    filtered_samples.append(_make_filtered_entry(doc, chunk, filter_reason))
                continue

            vector = chunk.get("vector", [])
            if not vector:
                if len(filtered_samples) < 20:
                    filtered_samples.append(_make_filtered_entry(doc, chunk, "missing_vector"))
                continue

            if query_vector is not None:
                result = _chunk_result(doc, doc_id, chunk)
                result["score"] = round(_cosine_similarity(query_vector, vector), 4)
                result["score_type"] = "vector_similarity"
                result["vector_score"] = result["score"]
                result["matched_channels"] = ["vector"]
                result["retrieval_sources"] = "vector"
                scored_results.append(result)

    scored_results.sort(key=lambda item: item["score"], reverse=True)

    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in scored_results:
        dedupe_key = result_identity(item)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        deduped.append(item)
        candidate_limit = top_k if mode == "vector_only" else max(top_k, int(retrieval_cfg.vector_candidate_k))
        if len(deduped) >= candidate_limit:
            break

    if mode == "vector_only":
        results = deduped[:top_k]
        top_source = results[0]["title"] if results else ""
        top_score = results[0]["score"] if results else 0.0
        payload = _empty_payload(normalized_query, retrieval_mode=mode)
        payload.update({
            "results": results,
            "raw_results_count": len(scored_results),
            "filtered_samples": filtered_samples[:10],
            "top_score": top_score,
            "top_source": top_source,
            "rebuilt_doc_ids": rebuilt_doc_ids,
            "vector_candidate_count": len(deduped),
            "merged_candidate_count": len(deduped),
            "final_result_count": len(results),
            "performance": {"vector_query_ms": vector_elapsed_ms},
        })
        return payload

    bm25_results: list[dict[str, Any]] = []
    bm25_error = ""
    bm25_started = time.perf_counter()
    try:
        bm25_results = _get_bm25_index(all_emb, docs).search(
            normalized_query,
            top_k=max(top_k, int(retrieval_cfg.bm25_candidate_k)),
        )
    except Exception as exc:
        bm25_error = f"{type(exc).__name__}: {exc}"
        logger.warning("BM25_RETRIEVAL_FAILED error_type=%s", type(exc).__name__)
    bm25_elapsed_ms = round((time.perf_counter() - bm25_started) * 1000, 3)

    if mode == "bm25_only":
        if bm25_error:
            raise RetrievalError(f"BM25 retrieval failed: {bm25_error}")
        results = _as_bm25_results(bm25_results[:top_k])
        payload = _empty_payload(normalized_query, retrieval_mode="bm25_only")
        payload.update({
            "results": results,
            "bm25_candidate_count": len(bm25_results),
            "merged_candidate_count": len(bm25_results),
            "final_result_count": len(results),
            "top_score": results[0]["score"] if results else 0.0,
            "top_source": results[0].get("title", "") if results else "",
            "performance": {
                "bm25_query_ms": bm25_elapsed_ms,
                "bm25_build_elapsed_ms": _BM25_CACHE.get("build_elapsed_ms"),
            },
        })
        return payload

    fusion_started = time.perf_counter()
    if vector_error and bm25_error:
        raise RetrievalError(
            f"all retrieval channels failed; vector={vector_error}; bm25={bm25_error}"
        )
    if bm25_error:
        results = deduped[:top_k]
        fallback = True
        fallback_reason = f"bm25_failed:{bm25_error}"
        actual_mode = "vector_only"
    elif vector_error:
        results = _as_bm25_results(bm25_results[:top_k])
        fallback = False
        fallback_reason = f"vector_failed:{vector_error}"
        actual_mode = "bm25_only"
    else:
        results = reciprocal_rank_fusion(
            deduped,
            bm25_results,
            top_k=top_k,
            rrf_k=int(retrieval_cfg.rrf_k),
            vector_weight=float(retrieval_cfg.vector_weight),
            bm25_weight=float(retrieval_cfg.bm25_weight),
        )
        fallback = False
        fallback_reason = f"vector_failed:{vector_error}" if vector_error else ""
        actual_mode = "hybrid"
    fusion_elapsed_ms = round((time.perf_counter() - fusion_started) * 1000, 3)

    top_source = results[0]["title"] if results else ""
    top_score = results[0]["score"] if results else 0.0
    payload = {
        "query": normalized_query,
        "results": results,
        "raw_results_count": len(scored_results),
        "filtered_samples": filtered_samples[:10],
        "top_score": top_score,
        "top_source": top_source,
        "rebuilt_doc_ids": rebuilt_doc_ids,
        "retrieval_mode": actual_mode,
        "vector_candidate_count": len(deduped),
        "bm25_candidate_count": len(bm25_results),
        "merged_candidate_count": len({str(item.get("chunk_id") or (item.get("doc_id"), item.get("chunk_index"))) for item in deduped + bm25_results}),
        "final_result_count": len(results),
        "fallback_to_vector_only": fallback,
        "fallback_reason": fallback_reason,
        "vector_failed": bool(vector_error),
        "bm25_failed": bool(bm25_error),
        "degraded": bool(vector_error or bm25_error),
        "performance": {
            "vector_query_ms": vector_elapsed_ms,
            "bm25_query_ms": bm25_elapsed_ms,
            "rrf_ms": fusion_elapsed_ms if actual_mode == "hybrid" else 0.0,
            "bm25_build_elapsed_ms": _BM25_CACHE.get("build_elapsed_ms"),
        },
    }
    logger.info(
        "HYBRID_SEARCH mode=%s vector=%s bm25=%s merged=%s final=%s fallback=%s reason=%s",
        actual_mode, len(deduped), len(bm25_results), payload["merged_candidate_count"],
        len(results), fallback, fallback_reason,
    )
    return payload


def search(query: str, top_k: int = 20) -> list[dict]:
    """Compatibility wrapper for semantic search."""
    return search_detailed(query=query, top_k=top_k)["results"]


def get_retrieval_stats() -> dict:
    """Get retrieval system statistics."""
    all_emb = _load_all_embeddings()
    total_embeddings = len(all_emb)
    total_chunks = sum(len(entry.get("chunks", [])) for entry in all_emb.values())
    return {
        "documents_with_embeddings": total_embeddings,
        "total_chunks": total_chunks,
    }
