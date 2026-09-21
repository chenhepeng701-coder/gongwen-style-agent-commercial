"""Read-only article-to-source traceability assembled from saved local artifacts."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from app.services.library.indexer import get_document
from app.services.library.source_content import SourceContentError, validate_document_id
from app.services.app_settings import get_output_dir
from app.utils.runtime_paths import get_library_embeddings_dir


SENTENCE_TERMINAL = re.compile(r"[。！？；!?]$")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _path_identity(value: str) -> str:
    if not value:
        return ""
    try:
        return str(Path(value).resolve(strict=False)).casefold()
    except (OSError, RuntimeError, ValueError):
        return ""


def _find_flow3_run(article_save_path: str) -> Path | None:
    target = _path_identity(article_save_path)
    if not target:
        return None

    flow3_root = (get_output_dir() / "flow3").resolve()
    if not flow3_root.is_dir():
        return None

    try:
        run_dirs = sorted(
            (path for path in flow3_root.iterdir() if path.is_dir()),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return None

    for run_dir in run_dirs:
        try:
            resolved_run = run_dir.resolve()
            if not resolved_run.is_relative_to(flow3_root):
                continue
            manifest = _read_json(resolved_run / "runtime_manifest.json")
            artifact_path = str(manifest.get("flow4_artifact_path") or "")
            if _path_identity(artifact_path) == target:
                return resolved_run
        except (OSError, ValueError, json.JSONDecodeError, AttributeError):
            continue
    return None


def _embedding_chunks(document_id: str) -> dict[str, dict[str, Any]]:
    try:
        normalized_id = validate_document_id(document_id)
    except SourceContentError:
        return {}
    embedding_path = get_library_embeddings_dir() / f"{normalized_id}.json"
    try:
        payload = _read_json(embedding_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return {}

    chunks: dict[str, dict[str, Any]] = {}
    for item in payload.get("chunks") or []:
        if not isinstance(item, dict):
            continue
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        chunk_id = str(metadata.get("chunk_id") or "").strip()
        if chunk_id:
            chunks[chunk_id] = item
    return chunks


def _flush_paragraph(
    paragraphs: list[dict[str, Any]],
    buffer: dict[str, Any] | None,
) -> None:
    if not buffer or not str(buffer.get("text") or "").strip():
        return
    buffer["paragraph_index"] = len(paragraphs)
    paragraphs.append(buffer)


def _structured_paragraphs(
    structured_draft: dict[str, Any],
    fact_support: dict[str, Any],
) -> list[dict[str, Any]]:
    paragraphs: list[dict[str, Any]] = []
    for section in structured_draft.get("sections") or []:
        if not isinstance(section, dict):
            continue
        section_id = str(section.get("section_id") or "")
        section_heading = str(section.get("heading") or "").strip()
        buffer: dict[str, Any] | None = None
        for sentence in section.get("sentences") or []:
            if not isinstance(sentence, dict):
                continue
            text = str(sentence.get("text") or "").strip()
            if not text:
                continue
            if buffer and SENTENCE_TERMINAL.search(str(buffer.get("text") or "")):
                _flush_paragraph(paragraphs, buffer)
                buffer = None
            if buffer is None:
                buffer = {
                    "text": "",
                    "section_id": section_id,
                    "section_heading": section_heading,
                    "supports": [],
                }
            buffer["text"] += text
            evidence_id = str(sentence.get("evidence_id") or "").strip()
            support = fact_support.get(evidence_id) if evidence_id else None
            if isinstance(support, dict):
                buffer["supports"].append(support)
            if SENTENCE_TERMINAL.search(text):
                _flush_paragraph(paragraphs, buffer)
                buffer = None
        _flush_paragraph(paragraphs, buffer)
    return paragraphs


def _citation_id(document_id: str, chunk_id: str) -> str:
    digest = hashlib.sha256(f"{document_id}\n{chunk_id}".encode("utf-8")).hexdigest()[:16]
    return f"citation-{digest}"


def _document_metadata(document_id: str) -> dict[str, Any]:
    document = get_document(document_id)
    if document is None:
        return {
            "document_id": document_id,
            "title": "未命名资料",
            "filename": "未命名资料",
            "file_type": "",
            "source_available": False,
        }
    return {
        "document_id": document.id,
        "title": document.title or document.original_filename or "未命名资料",
        "filename": document.original_filename or document.title or "未命名资料",
        "file_type": document.file_type,
        "source_available": Path(document.file_path or "").is_file(),
    }


def build_article_source_traceability(article: dict[str, Any]) -> dict[str, Any]:
    """Build a path-free read model for one stored article snapshot."""

    unavailable = {
        "status": "UNAVAILABLE",
        "message": "这篇历史文章没有保存具体引用位置。",
        "sources": [],
        "paragraph_sources": [],
    }
    run_dir = _find_flow3_run(str(article.get("save_path") or article.get("docx_path") or ""))
    if run_dir is None:
        return unavailable

    try:
        fact_support = _read_json(run_dir / "fact_source_support.json")
        structured_draft = _read_json(run_dir / "parsed_structured_draft.json")
    except (OSError, ValueError, json.JSONDecodeError):
        return unavailable
    if not isinstance(fact_support, dict) or not isinstance(structured_draft, dict):
        return unavailable

    paragraphs = _structured_paragraphs(structured_draft, fact_support)
    sources: dict[str, dict[str, Any]] = {}
    citation_maps: dict[str, dict[str, dict[str, Any]]] = {}
    paragraph_sources: list[dict[str, Any]] = []

    for paragraph in paragraphs:
        references: list[dict[str, str]] = []
        seen_references: set[tuple[str, str]] = set()
        for support in paragraph.get("supports") or []:
            document_id = str(support.get("document_id") or "").strip()
            chunk_id = str(support.get("chunk_id") or "").strip()
            try:
                validate_document_id(document_id)
            except SourceContentError:
                continue
            if not chunk_id:
                continue

            if document_id not in sources:
                sources[document_id] = _document_metadata(document_id)
                citation_maps[document_id] = {}
            citations = citation_maps[document_id]
            citation = citations.get(chunk_id)
            if citation is None:
                chunk = _embedding_chunks(document_id).get(chunk_id) or {}
                citation = {
                    "citation_id": _citation_id(document_id, chunk_id),
                    "chunk_id": chunk_id,
                    "excerpt": str(support.get("source_sentence") or chunk.get("text") or support.get("source_span") or "").strip(),
                    "hit_texts": [],
                    "paragraph_indexes": [],
                    "section_headings": [],
                }
                citations[chunk_id] = citation
            hit_text = str(support.get("source_span") or "").strip()
            if hit_text and hit_text not in citation["hit_texts"]:
                citation["hit_texts"].append(hit_text)
            paragraph_index = int(paragraph["paragraph_index"])
            if paragraph_index not in citation["paragraph_indexes"]:
                citation["paragraph_indexes"].append(paragraph_index)
            heading = str(paragraph.get("section_heading") or "").strip()
            if heading and heading not in citation["section_headings"]:
                citation["section_headings"].append(heading)

            key = (document_id, chunk_id)
            if key not in seen_references:
                references.append(
                    {
                        "document_id": document_id,
                        "citation_id": citation["citation_id"],
                        "chunk_id": chunk_id,
                    }
                )
                seen_references.add(key)

        if references:
            paragraph_sources.append(
                {
                    "paragraph_index": paragraph["paragraph_index"],
                    "section_heading": paragraph.get("section_heading") or "",
                    "sources": references,
                }
            )

    public_sources: list[dict[str, Any]] = []
    for document_id, source in sources.items():
        citations = list(citation_maps[document_id].values())
        citations.sort(key=lambda item: min(item["paragraph_indexes"] or [10**9]))
        public_sources.append(
            {
                **source,
                "citation_count": len(citations),
                "citations": citations,
            }
        )
    public_sources.sort(key=lambda item: str(item.get("title") or ""))

    if not public_sources or not paragraph_sources:
        return unavailable
    return {
        "status": "AVAILABLE",
        "message": "",
        "sources": public_sources,
        "paragraph_sources": paragraph_sources,
    }
