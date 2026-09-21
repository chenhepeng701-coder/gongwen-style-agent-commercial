"""Retrieval API — semantic search + scene classification over the document library."""

import json
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.models.library import (
    RetrievalRequest,
    RetrievalResponse,
    RetrievalStatsResponse,
    SceneClassifyRequest,
    SceneClassifyResponse,
    SceneTaxonomyResponse,
    SceneStatsResponse,
    BatchClassifyResponse,
    SceneStyleProfile,
    SceneStyleSummary,
    SceneStyleListResponse,
)
from app.services.retrieval.retriever import search, get_retrieval_stats
from app.services.retrieval.official_chunk_metadata import normalize_chunk_metadata
from app.services.library.indexer import list_documents
from app.services.retrieval.scene_classifier import (
    classify_by_title_or_query,
    classify_document,
    batch_classify_all,
    get_scene_taxonomy,
    get_scene_stats,
)
from app.services.retrieval.style_aggregator import (
    aggregate_scene_style,
    get_all_scene_profiles,
    get_scene_profile_summary,
)
from app.utils.runtime_paths import get_library_embeddings_dir
from app.utils.api_errors import internal_error

router = APIRouter()
logger = logging.getLogger(__name__)


def _normalize_text(value: str | None) -> str:
    return (value or "").strip().lower()


def _file_name(file_path: str) -> str:
    try:
        return Path(file_path).name
    except Exception:
        return file_path or ""


def _make_snippet(text: str, query: str, max_chars: int = 260) -> str:
    clean = " ".join((text or "").split())
    if not clean:
        return ""
    idx = clean.lower().find(query.lower())
    if idx < 0:
        return clean[:max_chars] + ("..." if len(clean) > max_chars else "")
    start = max(idx - 80, 0)
    end = min(idx + len(query) + 160, len(clean))
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(clean) else ""
    return f"{prefix}{clean[start:end]}{suffix}"


def _result_key(item: dict) -> tuple[str, int]:
    return (str(item.get("doc_id", "")), int(item.get("chunk_index", 0) or 0))


def _keyword_fallback_results(query: str, docs: list, limit: int) -> list[dict]:
    normalized_query = _normalize_text(query)
    if not normalized_query:
        return []

    results: list[dict] = []
    matched_keys: set[tuple[str, int]] = set()

    def add_result(
        doc,
        match_type: str,
        match_reason: str,
        chunk_text: str,
        score: float,
        chunk_index: int = 0,
        chunk: dict | None = None,
    ):
        key = (doc.id, chunk_index)
        if key in matched_keys or len(results) >= limit:
            return
        matched_keys.add(key)
        metadata = normalize_chunk_metadata(
            chunk or {"chunk_index": chunk_index},
            doc_id=doc.id,
            title=doc.title,
            file_path=doc.file_path,
            chunk_index=chunk_index,
        )
        results.append({
            "doc_id": doc.id,
            "title": doc.title,
            "file_path": doc.file_path,
            "file_type": doc.file_type,
            "word_count": doc.word_count,
            "scene_tags": doc.scene_tags or [],
            "chunk_index": chunk_index,
            "chunk_text": _make_snippet(chunk_text, query),
            "score": score,
            "match_type": match_type,
            "match_reason": match_reason,
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
        })

    for doc in docs:
        if len(results) >= limit:
            break
        title = doc.title or ""
        filename = _file_name(doc.file_path)
        preview = doc.content_preview or ""
        if normalized_query in _normalize_text(title):
            add_result(doc, "标题命中", "标题中包含该关键词", title or preview, 1.0)
        elif normalized_query in _normalize_text(filename):
            add_result(doc, "文件名命中", "文件名中包含该关键词", filename or title or preview, 0.96)
        elif normalized_query in _normalize_text(preview):
            add_result(doc, "正文命中", "正文预览中包含该关键词", preview, 0.9)

    if len(results) >= limit:
        return results

    emb_dir = get_library_embeddings_dir()
    doc_map = {doc.id: doc for doc in docs}
    for emb_file in sorted(emb_dir.glob("*.json")):
        if len(results) >= limit:
            break
        doc = doc_map.get(emb_file.stem)
        if doc is None:
            continue
        try:
            payload = json.loads(emb_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        for chunk in payload.get("chunks", []):
            if len(results) >= limit:
                break
            text = str(chunk.get("text", "") or "")
            if normalized_query not in _normalize_text(text):
                continue
            add_result(
                doc,
                "正文命中",
                "正文段落中包含该关键词",
                text,
                0.88,
                int(chunk.get("chunk_index", 0) or 0),
                chunk,
            )

    return results


def _merge_search_results(semantic_results: list[dict], fallback_results: list[dict], limit: int) -> list[dict]:
    merged: list[dict] = []
    seen: set[tuple[str, int]] = set()

    for item in fallback_results:
        key = _result_key(item)
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)

    for item in semantic_results:
        key = _result_key(item)
        if key in seen:
            continue
        enriched = dict(item)
        enriched.setdefault("match_type", "语义相关")
        enriched.setdefault("match_reason", "语义相关")
        seen.add(key)
        merged.append(enriched)

    return merged[:limit]


@router.post("/search", response_model=RetrievalResponse)
async def search_documents(req: RetrievalRequest):
    """Search documents by semantic similarity."""
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="查询文本不能为空")

    docs = list_documents().get("items", [])
    if not docs:
        return RetrievalResponse(
            query=req.query,
            results=[],
            total=0,
            total_documents=0,
            library_empty=True,
        )

    semantic_results = []
    semantic_error = ""
    try:
        semantic_results = search(req.query, req.top_k)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("RETRIEVAL_SEMANTIC_SEARCH_ERROR")
        semantic_error = "语义检索暂时不可用，已使用关键词兜底。"

    fallback_results = _keyword_fallback_results(req.query, docs, req.top_k)
    results = _merge_search_results(semantic_results, fallback_results, req.top_k)

    return RetrievalResponse(
        query=req.query,
        results=results,
        total=len(results),
        total_documents=len(docs),
        library_empty=False,
        semantic_error=semantic_error,
    )


@router.get("/stats", response_model=RetrievalStatsResponse)
async def retrieval_stats():
    """Get retrieval system readiness stats."""
    try:
        stats = get_retrieval_stats()
        return RetrievalStatsResponse(**stats)
    except Exception as e:
        raise internal_error(logger, "RETRIEVAL_STATS_ERROR", e)


# ── Scene Classification ──────────────────────────────────────────────

@router.post("/scene/classify", response_model=SceneClassifyResponse)
async def classify_scene(req: SceneClassifyRequest):
    """Classify a title/query into scene categories."""
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="文本不能为空")
    try:
        result = classify_by_title_or_query(req.text)
        return SceneClassifyResponse(**result)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise internal_error(logger, "RETRIEVAL_SCENE_CLASSIFY_ERROR", e)


@router.post("/scene/classify/{doc_id}", response_model=SceneClassifyResponse)
async def classify_document_scene(doc_id: str):
    """Auto-classify a document's scene and update its tags."""
    try:
        result = classify_document(doc_id)
        return SceneClassifyResponse(**result)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise internal_error(logger, "RETRIEVAL_DOCUMENT_SCENE_CLASSIFY_ERROR", e)


@router.post("/scene/classify-all", response_model=BatchClassifyResponse)
async def batch_classify_scenes():
    """Run scene classification on all un-tagged documents."""
    try:
        result = batch_classify_all()
        return BatchClassifyResponse(**result)
    except Exception as e:
        raise internal_error(logger, "RETRIEVAL_BATCH_SCENE_CLASSIFY_ERROR", e)


@router.get("/scene/taxonomy", response_model=SceneTaxonomyResponse)
async def scene_taxonomy():
    """Get the scene taxonomy list."""
    items = get_scene_taxonomy()
    return SceneTaxonomyResponse(items=items)


@router.get("/scene/stats", response_model=SceneStatsResponse)
async def scene_stats():
    """Get document distribution across scenes."""
    try:
        stats = get_scene_stats()
        return SceneStatsResponse(**stats)
    except Exception as e:
        raise internal_error(logger, "RETRIEVAL_SCENE_STATS_ERROR", e)


# ── Scene Style Aggregation ─────────────────────────────────────────

@router.get("/scene/style/{scene_name}", response_model=SceneStyleProfile)
async def scene_style_profile(scene_name: str):
    """Get aggregated style profile for a specific scene."""
    profile = aggregate_scene_style(scene_name)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"场景 '{scene_name}' 没有足够的文档或风格卡片")
    return SceneStyleProfile(**profile)


@router.get("/scene/style/{scene_name}/summary", response_model=SceneStyleSummary)
async def scene_style_summary(scene_name: str):
    """Get a brief style summary for a specific scene."""
    summary = get_scene_profile_summary(scene_name)
    if summary is None:
        raise HTTPException(status_code=404, detail=f"场景 '{scene_name}' 没有足够的文档或风格卡片")
    return SceneStyleSummary(**summary)


@router.get("/scene/styles", response_model=SceneStyleListResponse)
async def all_scene_profiles():
    """Get style profiles for all scenes that have documents."""
    profiles = get_all_scene_profiles()
    items = [SceneStyleProfile(**p) for p in profiles]
    return SceneStyleListResponse(items=items, total=len(items))
