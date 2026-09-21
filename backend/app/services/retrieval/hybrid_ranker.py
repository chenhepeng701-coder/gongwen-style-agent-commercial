"""Reciprocal-rank fusion and chunk-level deduplication."""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


def result_identity(item: dict[str, Any]) -> str:
    chunk_id = str(item.get("chunk_id") or "").strip()
    if chunk_id:
        return f"chunk:{chunk_id}"
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    source_file = str(item.get("source_file") or metadata.get("source_file") or "").strip().lower()
    paragraph_start = item.get("paragraph_start", metadata.get("paragraph_start"))
    paragraph_end = item.get("paragraph_end", metadata.get("paragraph_end"))
    text = re.sub(r"\s+", "", str(item.get("chunk_text") or item.get("text") or "")).lower()
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"fallback:{source_file}:{paragraph_start}:{paragraph_end}:{digest}"


def normalize_exact_body_for_dedup(text: Any) -> str | None:
    if text is None:
        return None
    body = str(text).replace("\r\n", "\n").replace("\r", "\n")
    body = body.strip()
    lines = [line.rstrip(" \t") for line in body.split("\n")]
    normalized = "\n".join(lines)
    if not normalized:
        return None
    return normalized


def exact_body_fingerprint(item: dict[str, Any]) -> str | None:
    normalized = normalize_exact_body_for_dedup(item.get("chunk_text", item.get("text")))
    if normalized is None:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _source_record(item: dict[str, Any], hybrid_rank: int) -> dict[str, Any]:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    source_filename = (
        item.get("source_filename")
        or item.get("source_file")
        or metadata.get("source_filename")
        or metadata.get("source_file")
        or item.get("file_path")
        or item.get("title")
        or ""
    )
    return {
        "doc_id": item.get("doc_id") or metadata.get("doc_id") or "",
        "chunk_id": item.get("chunk_id") or metadata.get("chunk_id") or "",
        "source_filename": source_filename,
        "chunk_index": item.get("chunk_index", metadata.get("chunk_index")),
        "original_hybrid_rank": hybrid_rank,
        "vector_rank": item.get("vector_rank"),
        "bm25_rank": item.get("bm25_rank"),
    }


def _append_unique_source(
    sources: list[dict[str, Any]],
    seen: set[tuple[Any, ...]],
    item: dict[str, Any],
    hybrid_rank: int,
) -> None:
    source = _source_record(item, hybrid_rank)
    key = (
        source.get("doc_id"),
        source.get("chunk_id"),
        source.get("source_filename"),
        source.get("chunk_index"),
        source.get("original_hybrid_rank"),
        source.get("vector_rank"),
        source.get("bm25_rank"),
    )
    if key in seen:
        return
    seen.add(key)
    sources.append(source)


def deduplicate_cross_doc_exact_body(sorted_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    representatives_by_fingerprint: dict[str, dict[str, Any]] = {}
    source_seen_by_fingerprint: dict[str, set[tuple[Any, ...]]] = {}
    output: list[dict[str, Any]] = []

    for hybrid_rank, item in enumerate(sorted_items, start=1):
        fingerprint = exact_body_fingerprint(item)
        doc_id = str(item.get("doc_id") or "").strip()
        if not fingerprint:
            output.append(item)
            continue

        representative = representatives_by_fingerprint.get(fingerprint)
        if representative is None:
            representatives_by_fingerprint[fingerprint] = item
            source_seen_by_fingerprint[fingerprint] = set()
            item["_original_hybrid_rank_for_dedup"] = hybrid_rank
            output.append(item)
            continue

        representative_doc_id = str(representative.get("doc_id") or "").strip()
        if not doc_id or not representative_doc_id or doc_id == representative_doc_id:
            output.append(item)
            continue

        sources = representative.setdefault("duplicate_sources", [])
        seen = source_seen_by_fingerprint.setdefault(fingerprint, set())
        if not sources:
            _append_unique_source(
                sources,
                seen,
                representative,
                int(representative.get("_original_hybrid_rank_for_dedup") or output.index(representative) + 1),
            )
        _append_unique_source(sources, seen, item, hybrid_rank)
        representative["duplicate_count"] = len(sources)

    for item in output:
        item.pop("_original_hybrid_rank_for_dedup", None)
    return output


def reciprocal_rank_fusion(
    vector_results: list[dict[str, Any]],
    bm25_results: list[dict[str, Any]],
    *,
    top_k: int,
    rrf_k: int = 60,
    vector_weight: float = 1.0,
    bm25_weight: float = 1.0,
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}

    for rank, source_item in enumerate(vector_results, start=1):
        key = result_identity(source_item)
        item = merged.setdefault(key, dict(source_item))
        item["vector_rank"] = rank
        item["vector_score"] = source_item.get("score")
        item["matched_channels"] = ["vector"]

    for rank, source_item in enumerate(bm25_results, start=1):
        key = result_identity(source_item)
        if key in merged:
            item = merged[key]
            item["matched_channels"] = ["vector", "bm25"]
        else:
            item = merged.setdefault(key, dict(source_item))
            item["matched_channels"] = ["bm25"]
        item["bm25_rank"] = rank
        item["bm25_score"] = source_item.get("bm25_score")

    for item in merged.values():
        vector_rank = item.get("vector_rank")
        bm25_rank = item.get("bm25_rank")
        score = 0.0
        if vector_rank:
            score += vector_weight / (rrf_k + int(vector_rank))
        if bm25_rank:
            score += bm25_weight / (rrf_k + int(bm25_rank))
        item["rrf_score"] = round(score, 8)
        item.setdefault("vector_rank", None)
        item.setdefault("bm25_rank", None)
        item.setdefault("vector_score", None)
        item.setdefault("bm25_score", None)
        item["score"] = item["rrf_score"]
        item["score_type"] = "rrf"
        item["retrieval_sources"] = "+".join(item["matched_channels"])

    sorted_items = sorted(
        merged.values(),
        key=lambda item: (
            -float(item.get("rrf_score") or 0.0),
            int(item.get("vector_rank") or 10**9),
            int(item.get("bm25_rank") or 10**9),
            result_identity(item),
        ),
    )

    try:
        sorted_items = deduplicate_cross_doc_exact_body(sorted_items)
    except Exception as exc:
        logger.warning(
            "HYBRID_EXACT_BODY_DEDUP_FAILED error_type=%s",
            type(exc).__name__,
            exc_info=True,
        )
        for item in sorted_items:
            item.pop("_original_hybrid_rank_for_dedup", None)

    return sorted_items[:max(0, top_k)]
