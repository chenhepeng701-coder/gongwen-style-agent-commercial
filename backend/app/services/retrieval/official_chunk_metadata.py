"""Metadata helpers for official-document semantic chunks."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from app.services.retrieval.official_document_classifier import (
    UNKNOWN,
    classify_document_role,
    classify_official_document,
)


SCHEMA_VERSION = "v0.4R-7.6"
LEGACY_SCHEMA_VERSION = "legacy"

CONTENT_ROLES = {
    "title", "background", "problem", "requirement", "measure", "responsibility",
    "progress", "result", "data", "example", "mechanism", "summary", "conclusion",
    "attachment", "mixed", "unknown",
}
CONTENT_REFERENCE_TYPES = {"content", "style", "both", "unknown"}

DEFAULT_LEGACY_METADATA: dict[str, Any] = {
    "schema_version": LEGACY_SCHEMA_VERSION,
    "document_id": "",
    "chunk_id": "",
    "source_file": "",
    "source_path": "",
    "title": "",
    "document_title": "",
    "document_title_source": "unknown",
    "document_type": UNKNOWN,
    "level": UNKNOWN,
    "org_type": UNKNOWN,
    "business_domain": UNKNOWN,
    "writing_purpose": UNKNOWN,
    "document_role": "mixed",
    "section_title": "",
    "section_heading": "",
    "section_path": [],
    "section_level": 0,
    "chunk_index": 0,
    "paragraph_start": None,
    "paragraph_end": None,
    "content_role": "unknown",
    "content_reference_type": "unknown",
    "paragraph_type": "unknown",
    "is_title_chunk": False,
    "is_attachment": False,
    "char_count": 0,
    "chunk_length": 0,
    "style_tags": [],
    "quality_flags": [],
    "is_official_semantic_chunk": False,
    "chunk_strategy": "legacy",
}


def _normalized_fingerprint_text(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def make_document_id(*, source_path: str = "", source_file: str = "", title: str = "", text: str = "") -> str:
    """Build a deterministic document id without depending on an absolute path."""
    normalized = _normalized_fingerprint_text(text)
    seed = "\n".join([source_file or Path(source_path).name, title or "", str(len(normalized)), normalized])
    return hashlib.sha256(seed.encode("utf-8", errors="ignore")).hexdigest()[:16]


def make_chunk_id(
    document_id: str,
    chunk_index: int = 0,
    *,
    content: str = "",
    paragraph_start: int | None = None,
    paragraph_end: int | None = None,
    section_path: list[str] | None = None,
) -> str:
    """Return a stable id; legacy callers without content retain the old readable form."""
    if not content:
        return f"{document_id}:{chunk_index}"
    seed = "\n".join(
        [
            document_id,
            str(paragraph_start if paragraph_start is not None else ""),
            str(paragraph_end if paragraph_end is not None else ""),
            " > ".join(section_path or []),
            _normalized_fingerprint_text(content),
        ]
    )
    digest = hashlib.sha256(seed.encode("utf-8", errors="ignore")).hexdigest()[:20]
    return f"{document_id}:{digest}"


def infer_style_tags(content_role: str, *, document_type: str, business_domain: str, text: str) -> list[str]:
    tags: list[str] = []
    if document_type in {"领导讲话", "党课讲稿"} or "同志们" in text:
        tags.append("讲话语气")
    if business_domain == "纪检 / 廉政" or any(word in text for word in ("廉政", "纪律", "作风")):
        tags.append("廉政提醒")
    if content_role in {"measure", "requirement", "responsibility", "mechanism"}:
        tags.append("行动导向")
    return tags


def build_chunk_metadata(
    *,
    document_id: str,
    chunk_id: str,
    source_file: str,
    source_path: str,
    title: str,
    chunk_text: str,
    chunk_index: int,
    paragraph_type: str = "unknown",
    section_heading: str = "",
    section_index: int = 0,
    chunk_strategy: str = "official_semantic",
    document_metadata: dict[str, Any] | None = None,
    document_title_source: str = "detected",
    section_path: list[str] | None = None,
    section_level: int = 0,
    paragraph_start: int | None = None,
    paragraph_end: int | None = None,
    content_role: str = "unknown",
    content_reference_type: str = "unknown",
    is_title_chunk: bool = False,
    is_attachment: bool = False,
    quality_flags: list[str] | None = None,
) -> dict[str, Any]:
    doc_meta = dict(document_metadata or {})
    if not doc_meta:
        doc_meta = classify_official_document(title=title, source_file=source_file, text=chunk_text)

    safe_role = content_role if content_role in CONTENT_ROLES else "unknown"
    safe_reference = content_reference_type if content_reference_type in CONTENT_REFERENCE_TYPES else "unknown"
    resolved_path = list(section_path or [])
    resolved_section_title = resolved_path[-1] if resolved_path else section_heading
    flags = list(dict.fromkeys(quality_flags or []))

    return {
        "schema_version": SCHEMA_VERSION,
        "document_id": document_id,
        "chunk_id": chunk_id,
        "source_file": source_file,
        "source_path": source_path,
        "title": title,
        "document_title": title,
        "document_title_source": document_title_source,
        "document_type": doc_meta.get("document_type") or UNKNOWN,
        "level": doc_meta.get("level") or UNKNOWN,
        "org_type": doc_meta.get("org_type") or UNKNOWN,
        "business_domain": doc_meta.get("business_domain") or UNKNOWN,
        "writing_purpose": doc_meta.get("writing_purpose") or UNKNOWN,
        "document_role": doc_meta.get("document_role") or classify_document_role(
            document_type=doc_meta.get("document_type") or UNKNOWN,
            writing_purpose=doc_meta.get("writing_purpose") or UNKNOWN,
        ),
        "section_title": resolved_section_title,
        "section_heading": resolved_section_title,
        "section_path": resolved_path,
        "section_level": int(section_level or 0),
        "section_index": int(section_index or 0),
        "chunk_index": int(chunk_index),
        "paragraph_start": paragraph_start,
        "paragraph_end": paragraph_end,
        "content_role": safe_role,
        "content_reference_type": safe_reference,
        "paragraph_type": paragraph_type or "unknown",
        "is_title_chunk": bool(is_title_chunk),
        "is_attachment": bool(is_attachment),
        "char_count": len(chunk_text),
        "chunk_length": len(chunk_text),
        "style_tags": infer_style_tags(
            safe_role,
            document_type=doc_meta.get("document_type") or UNKNOWN,
            business_domain=doc_meta.get("business_domain") or UNKNOWN,
            text=chunk_text,
        ),
        "quality_flags": flags,
        "is_official_semantic_chunk": True,
        "chunk_strategy": chunk_strategy,
    }


def normalize_chunk_metadata(
    chunk: dict[str, Any] | None = None,
    *,
    doc_id: str = "",
    title: str = "",
    file_path: str = "",
    chunk_index: int = 0,
) -> dict[str, Any]:
    """Normalize new metadata and give schema-less legacy chunks safe defaults."""
    chunk = chunk or {}
    existing = chunk.get("metadata")
    metadata = dict(existing) if isinstance(existing, dict) else {}
    resolved_index = int(chunk.get("chunk_index", metadata.get("chunk_index", chunk_index)) or 0)
    source_file = metadata.get("source_file") or (Path(file_path).name if file_path else "")
    document_id = metadata.get("document_id") or doc_id

    normalized = dict(DEFAULT_LEGACY_METADATA)
    normalized.update(
        {
            "document_id": document_id,
            "chunk_id": metadata.get("chunk_id") or (make_chunk_id(document_id, resolved_index) if document_id else ""),
            "source_file": source_file,
            "source_path": metadata.get("source_path") or file_path or "",
            "title": metadata.get("title") or metadata.get("document_title") or title or "",
            "document_title": metadata.get("document_title") or metadata.get("title") or title or "",
            "chunk_index": resolved_index,
            "char_count": int(metadata.get("char_count") or len(str(chunk.get("text") or ""))),
            "chunk_length": int(metadata.get("chunk_length") or len(str(chunk.get("text") or ""))),
        }
    )
    for key, value in metadata.items():
        if value not in (None, ""):
            normalized[key] = value

    # Older embedding files may carry body-driven document metadata.  Reapply
    # stable title/file-name signals at read time so code fixes take effect
    # without mutating user files or rebuilding the stored index.
    title_classification = classify_official_document(
        title=str(normalized.get("document_title") or normalized.get("title") or title or ""),
        source_file=str(normalized.get("source_file") or source_file or ""),
        text="",
    )
    for key in ("document_type", "business_domain", "writing_purpose"):
        value = title_classification.get(key)
        if value not in (None, "", UNKNOWN):
            normalized[key] = value
    normalized["document_role"] = classify_document_role(
        document_type=str(normalized.get("document_type") or ""),
        writing_purpose=str(normalized.get("writing_purpose") or ""),
    )

    normalized["section_path"] = list(normalized.get("section_path") or [])
    normalized["quality_flags"] = list(normalized.get("quality_flags") or [])
    normalized["style_tags"] = list(normalized.get("style_tags") or [])
    normalized["content_role"] = (
        normalized["content_role"] if normalized.get("content_role") in CONTENT_ROLES else "unknown"
    )
    normalized["content_reference_type"] = (
        normalized["content_reference_type"]
        if normalized.get("content_reference_type") in CONTENT_REFERENCE_TYPES
        else "unknown"
    )
    return normalized


def document_level_metadata(
    *,
    title: str = "",
    source_file: str = "",
    source_path: str = "",
    text: str = "",
) -> dict[str, Any]:
    metadata = classify_official_document(title=title, source_file=source_file, text=text)
    metadata.update({"source_file": source_file, "source_path": source_path, "title": title})
    return metadata
