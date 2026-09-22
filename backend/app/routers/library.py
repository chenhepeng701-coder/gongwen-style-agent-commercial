"""Library management API — file upload, index, list, delete."""

import os
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, UploadFile, File, HTTPException, Query
from starlette.concurrency import run_in_threadpool

from app.models.library import (
    LibraryDocument,
    DocumentListResponse,
    UploadResponse,
    DeleteResponse,
    EmbedResponse,
    CardExtractResponse,
    StyleExtractResponse,
    LibraryStatsResponse,
    BatchEmbedResponse,
    BatchCardsResponse,
    BatchStyleResponse,
    SourceDocumentContentResponse,
)
from app.services.library.indexer import (
    index_file,
    list_documents,
    get_document,
    update_scene_tags,
    ALLOWED_EXTENSIONS,
)
from app.services.library.embedder import (
    generate_embeddings,
    get_embedding_info,
)
from app.services.library.card_extractor import extract_cards, get_cards_info
from app.services.library.style_extractor import extract_style, get_style_info
from app.utils.runtime_paths import get_uploads_dir
from app.utils.api_errors import internal_error, public_batch_error
from app.services.library.source_content import SourceContentError, read_source_document_content
from app.services.library.material_ingestion import (
    schedule_processing, document_readiness, documents_readiness,
    delete_material_document, MaterialProcessingBusy,
)

router = APIRouter()
logger = logging.getLogger(__name__)


def _cleanup_saved_upload(path: Path) -> None:
    """Remove the newly saved upload if downstream indexing fails."""
    try:
        if path.exists() and path.is_file():
            path.unlink()
    except Exception:
        logger.exception("LIBRARY_UPLOAD_CLEANUP_ERROR path=%s", path)


@router.post("/upload", response_model=UploadResponse)
async def upload_document(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    """Upload a file, parse it, and add to the library index.

    The original file is temporarily saved to the uploads directory for parsing,
    but is NOT permanently stored in the library. Only the file path index is kept.
    """
    # Validate extension
    filename = file.filename or "unknown"
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型: {ext}。仅支持 {', '.join(ALLOWED_EXTENSIONS)}",
        )

    # Save uploaded file to a temp location for parsing
    uploads_dir = get_uploads_dir().resolve()
    uploads_dir.mkdir(parents=True, exist_ok=True)
    dest_path = (uploads_dir / f"{uuid.uuid4().hex}{ext}").resolve()
    if dest_path.parent != uploads_dir:
        raise HTTPException(status_code=400, detail="Invalid upload path")
    try:
        with open(dest_path, "xb") as f:
            content = await file.read()
            f.write(content)
    except FileExistsError:
        raise HTTPException(status_code=409, detail="Upload path already exists")
    except Exception as e:
        raise internal_error(logger, "LIBRARY_UPLOAD_SAVE_ERROR", e)

    # Index the file (this also records the file path)
    try:
        doc = await run_in_threadpool(index_file, str(dest_path), original_filename=filename)
    except ValueError as e:
        _cleanup_saved_upload(dest_path)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        _cleanup_saved_upload(dest_path)
        raise internal_error(logger, "LIBRARY_INDEX_ERROR", e)

    try:
        doc = await run_in_threadpool(schedule_processing, doc.id, background_tasks)
    except Exception as e:
        # Indexing succeeded: retain the source so an explicit retry is possible.
        raise internal_error(logger, "LIBRARY_PROCESS_SCHEDULE_ERROR", e)
    return UploadResponse(success=True, message=f"入库成功，材料处理中: {doc.title}", document=doc)


@router.post("/documents/{doc_id}/process", response_model=LibraryDocument, status_code=202)
async def process_library_document(doc_id: str, background_tasks: BackgroundTasks):
    try:
        return await run_in_threadpool(schedule_processing, doc_id, background_tasks)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="文档不存在")
    except MaterialProcessingBusy:
        raise HTTPException(status_code=409, detail={
            "error_type": "MATERIAL_PROCESSING_BUSY", "message": "文档正在处理或删除，请稍后重试。",
        })
    except Exception as e:
        raise internal_error(logger, "LIBRARY_PROCESS_SCHEDULE_ERROR", e)


@router.get("/stats", response_model=LibraryStatsResponse)
async def library_stats():
    """Get library stats: total docs and scene distribution."""
    docs = list_documents()["items"]
    scene_counts: dict[str, int] = {}
    for doc in docs:
        tags = doc.scene_tags or []
        if tags:
            for tag in tags:
                scene_counts[tag] = scene_counts.get(tag, 0) + 1
        else:
            scene_counts["未分类"] = scene_counts.get("未分类", 0) + 1
    return LibraryStatsResponse(
        total_docs=len(docs),
        scene_distribution=dict(sorted(scene_counts.items(), key=lambda x: x[1], reverse=True)),
    )


@router.get("/documents", response_model=DocumentListResponse)
async def get_documents(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=-1),
):
    """List indexed documents in the library, with optional pagination.

    Set page_size=-1 (default) to return all documents.
    """
    result = list_documents(page=page, page_size=page_size)
    result["items"] = await run_in_threadpool(documents_readiness, result["items"])
    return DocumentListResponse(**result)


@router.get("/documents/{doc_id}", response_model=LibraryDocument)
async def get_single_document(doc_id: str):
    """Get a single indexed document by id."""
    doc = get_document(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    try:
        return await run_in_threadpool(document_readiness, doc)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="文档不存在")


@router.get("/documents/{doc_id}/source-content", response_model=SourceDocumentContentResponse)
async def get_source_document_content(doc_id: str):
    """Return the real local text of one indexed document without exposing its path."""

    try:
        return await run_in_threadpool(read_source_document_content, doc_id)
    except SourceContentError as exc:
        status_code = 404 if exc.error_type == "NOT_FOUND" else 409
        raise HTTPException(
            status_code=status_code,
            detail={"error_type": exc.error_type, "message": exc.message},
        )


@router.delete("/documents/{doc_id}", response_model=DeleteResponse)
async def remove_document(doc_id: str):
    """Remove a document from the library index. Also cleans up embeddings."""
    try:
        ok = await run_in_threadpool(delete_material_document, doc_id)
    except Exception as e:
        raise internal_error(logger, "LIBRARY_DELETE_ERROR", e)
    if not ok:
        raise HTTPException(status_code=404, detail="文档不存在")
    return DeleteResponse(success=True, message="已从资料库移除")


@router.post("/documents/{doc_id}/embed", response_model=EmbedResponse)
async def embed_document(doc_id: str):
    """Generate embedding vectors for a document."""
    try:
        result = await run_in_threadpool(generate_embeddings, doc_id)
        return EmbedResponse(**result)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise internal_error(logger, "LIBRARY_EMBED_ERROR", e)


@router.get("/documents/{doc_id}/embed", response_model=EmbedResponse)
async def get_embed_status(doc_id: str):
    """Get embedding status/info for a document."""
    info = get_embedding_info(doc_id)
    if info is None:
        return EmbedResponse(success=False, message="未生成向量", chunks=0, model="")
    return EmbedResponse(
        success=True,
        message=f"已向量化: {info.get('chunks', 0)} 个分块",
        chunks=len(info.get("chunks", [])),
        model=info.get("model", ""),
    )


@router.put("/documents/{doc_id}/tags", response_model=LibraryDocument)
async def set_document_tags(doc_id: str, tags: list[str]):
    """Update scene tags for a document."""
    doc = update_scene_tags(doc_id, tags)
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    return doc


@router.post("/documents/{doc_id}/cards", response_model=CardExtractResponse)
async def extract_document_cards(doc_id: str, card_count: int | None = None):
    """Extract material cards from a document using LLM."""
    try:
        result = extract_cards(doc_id, card_count)
        return CardExtractResponse(**result)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise internal_error(logger, "LIBRARY_CARD_EXTRACT_ERROR", e)


@router.get("/documents/{doc_id}/cards", response_model=CardExtractResponse)
async def get_document_cards(doc_id: str):
    """Get previously extracted material cards for a document."""
    info = get_cards_info(doc_id)
    if info is None:
        return CardExtractResponse(
            success=False,
            message="尚未提取素材卡片",
            cards=[],
            card_count=0,
            model="",
        )
    from app.models.library import MaterialCard
    cards = [MaterialCard(**c) for c in info.get("cards", [])]
    return CardExtractResponse(
        success=True,
        message=f"已提取 {info.get('card_count', 0)} 张素材卡片",
        cards=cards,
        card_count=info.get("card_count", 0),
        model=info.get("model", ""),
    )


@router.post("/documents/{doc_id}/style", response_model=StyleExtractResponse)
async def extract_document_style(doc_id: str):
    """Extract style card from a document using LLM."""
    try:
        result = extract_style(doc_id)
        return StyleExtractResponse(**result)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise internal_error(logger, "LIBRARY_STYLE_EXTRACT_ERROR", e)


@router.get("/documents/{doc_id}/style", response_model=StyleExtractResponse)
async def get_document_style(doc_id: str):
    """Get previously extracted style card for a document."""
    info = get_style_info(doc_id)
    if info is None:
        return StyleExtractResponse(
            success=False,
            message="尚未提取风格卡片",
            style_card=None,
            model="",
        )
    from app.models.library import StyleCard
    card = StyleCard(**info.get("style_card", {}))
    return StyleExtractResponse(
        success=True,
        message=f"已提取风格卡片 ({info.get('model', '')})",
        style_card=card,
        model=info.get("model", ""),
    )


@router.post("/embed-all", response_model=BatchEmbedResponse)
def batch_embed_all():
    """Generate embeddings for all documents that don't have them yet."""
    docs = list_documents()["items"]
    ok = 0
    fail = 0
    errors = []
    for doc in docs:
        if doc.has_embedding:
            continue
        try:
            generate_embeddings(doc.id)
            ok += 1
        except Exception as e:
            logger.exception("LIBRARY_BATCH_EMBED_ITEM_ERROR")
            fail += 1
            if len(errors) < 3:
                errors.append(public_batch_error(doc.title))
    return BatchEmbedResponse(
        success=True,
        message=f"完成: {ok} 篇成功, {fail} 篇失败",
        embedded=ok,
        failed=fail,
        errors=errors,
    )


@router.post("/extract-cards-all", response_model=BatchCardsResponse)
def batch_extract_cards():
    """Extract material cards for all documents that don't have them yet."""
    docs = list_documents()["items"]
    ok = 0
    fail = 0
    errors = []
    for doc in docs:
        try:
            info = get_cards_info(doc.id)
            if info and info.get("card_count", 0) > 0:
                continue
        except Exception:
            pass
        try:
            extract_cards(doc.id)
            ok += 1
        except Exception as e:
            logger.exception("LIBRARY_BATCH_CARD_ITEM_ERROR")
            fail += 1
            if len(errors) < 3:
                errors.append(public_batch_error(doc.title))
    return BatchCardsResponse(success=True, message=f"完成: {ok} 篇成功, {fail} 篇失败", extracted=ok, failed=fail, errors=errors)


@router.post("/extract-style-all", response_model=BatchStyleResponse)
def batch_extract_style():
    """Extract style cards for all documents that don't have them yet."""
    docs = list_documents()["items"]
    ok = 0
    fail = 0
    errors = []
    for doc in docs:
        try:
            info = get_style_info(doc.id)
            if info and info.get("style_card"):
                continue
        except Exception:
            pass
        try:
            extract_style(doc.id)
            ok += 1
        except Exception as e:
            logger.exception("LIBRARY_BATCH_STYLE_ITEM_ERROR")
            fail += 1
            if len(errors) < 3:
                errors.append(public_batch_error(doc.title))
    return BatchStyleResponse(success=True, message=f"完成: {ok} 篇成功, {fail} 篇失败", extracted=ok, failed=fail, errors=errors)
