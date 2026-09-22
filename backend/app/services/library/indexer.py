"""Library indexer — parse files and manage the document index."""

import json
import math
import os
import threading
import uuid
import weakref
from datetime import datetime
from pathlib import Path

from app.models.library import LibraryDocument
from app.services.parser.docx_parser import parse_docx_file
from app.services.parser.txt_parser import parse_text_file
from app.utils.runtime_paths import get_library_index_dir
from app.utils.storage import atomic_write_text

ALLOWED_EXTENSIONS = {".docx", ".txt", ".md"}

_index_lock = threading.Lock()
_document_locks = weakref.WeakValueDictionary()
_document_locks_guard = threading.Lock()


def document_lock(doc_id: str):
    """Per-document locks; unused locks expire without blocking unrelated uploads."""
    with _document_locks_guard:
        lock = _document_locks.get(doc_id)
        if lock is None:
            lock = threading.RLock()
            _document_locks[doc_id] = lock
        return lock


def update_document(doc_id: str, **fields) -> LibraryDocument:
    with _index_lock:
        doc = get_document(doc_id)
        if doc is None:
            raise FileNotFoundError("文档不存在")
        doc = LibraryDocument(**{**doc.model_dump(), **fields})
        atomic_write_text(_index_path(doc_id), doc.model_dump_json(indent=2), encoding="utf-8")
        return doc


def _make_preview(content: str, max_chars: int = 200) -> str:
    return content[:max_chars] + ("…" if len(content) > max_chars else "")


def _count_words(content: str) -> int:
    """Rough Chinese word count (chars * 1 for simplicity)."""
    return len(content.replace("\n", "").replace(" ", ""))


def _index_path(doc_id: str) -> Path:
    """Return path to the index JSON file for a document."""
    return get_library_index_dir() / f"{doc_id}.json"


def index_file(file_path: str, original_filename: str = "") -> LibraryDocument:
    """Parse a file and create an index entry. Does NOT copy the file."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {file_path}")
    if path.suffix.lower() not in ALLOWED_EXTENSIONS:
        raise ValueError(f"不支持的文件类型: {path.suffix}。仅支持 {', '.join(ALLOWED_EXTENSIONS)}")

    ext = path.suffix.lower()

    # Parse
    if ext == ".docx":
        result = parse_docx_file(str(path))
    else:
        result = parse_text_file(str(path))

    display_name = Path(original_filename).name if original_filename else path.name
    title = result["title"] or Path(display_name).stem or path.stem
    content = result["content"]
    file_size = path.stat().st_size

    doc = LibraryDocument(
        id=str(uuid.uuid4())[:8],
        title=title,
        original_filename=original_filename or path.name,
        file_path=str(path.resolve()),
        file_type=ext,
        file_size=file_size,
        word_count=_count_words(content),
        content_preview=_make_preview(content),
        scene_tags=[],
        indexed_at=datetime.now().isoformat(),
    )

    # Save index
    atomic_write_text(_index_path(doc.id), doc.model_dump_json(indent=2), encoding="utf-8")
    return doc


def list_documents(page: int = 1, page_size: int = -1) -> dict:
    """List indexed documents with optional pagination.

    Args:
        page: Page number (1-based).
        page_size: Number of items per page. -1 returns all documents.

    Returns:
        dict with keys: items, total, page, page_size, total_pages
    """
    idx_dir = get_library_index_dir()
    docs = []
    for f in sorted(idx_dir.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            docs.append(LibraryDocument(**data))
        except Exception:
            continue

    total = len(docs)

    if page_size == -1:
        return {
            "items": docs,
            "total": total,
            "page": 1,
            "page_size": total,
            "total_pages": 1,
        }

    total_pages = max(1, math.ceil(total / page_size))
    start = (page - 1) * page_size
    items = docs[start:start + page_size]

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    }


def get_document(doc_id: str) -> LibraryDocument | None:
    """Get a single document by id."""
    p = _index_path(doc_id)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return LibraryDocument(**data)
    except Exception:
        return None


def delete_document(doc_id: str) -> bool:
    """Delete an index entry. Does NOT delete the original file."""
    with _index_lock:
        p = _index_path(doc_id)
        if not p.exists():
            return False
        p.unlink()
        return True


def get_documents_by_scene(scene_name: str, limit: int = 5) -> list[dict]:
    """Return full document contents filtered by scene tag."""
    all_docs = list_documents()["items"]
    matched = [d for d in all_docs if scene_name in (d.scene_tags or [])]
    matched = matched[:limit]
    results = []
    for doc in matched:
        content = ""
        file_path = doc.file_path
        if file_path and Path(file_path).exists():
            ext = Path(file_path).suffix.lower()
            if ext == ".docx":
                from app.services.parser.docx_parser import parse_docx_file
                result = parse_docx_file(file_path)
                content = result.get("content", "")
            elif ext in (".txt", ".md"):
                from app.services.parser.txt_parser import parse_text_file
                result = parse_text_file(file_path)
                content = result.get("content", "")
        results.append({
            "id": doc.id,
            "title": doc.title,
            "content": content,
            "scene_tags": doc.scene_tags or [],
        })
    return results


def update_scene_tags(doc_id: str, tags: list[str]) -> LibraryDocument | None:
    """Update scene tags for a document."""
    with _index_lock:
        doc = get_document(doc_id)
        if doc is None:
            return None
        doc.scene_tags = tags
        atomic_write_text(_index_path(doc_id), doc.model_dump_json(indent=2), encoding="utf-8")
        return doc
