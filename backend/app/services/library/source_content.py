"""Safe, local-only reading of indexed source documents."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from app.models.library import SourceDocumentContentResponse, SourceDocumentInfo
from app.services.library.indexer import ALLOWED_EXTENSIONS, get_document
from app.services.parser.docx_parser import parse_docx_file
from app.services.parser.txt_parser import parse_text_file


DOCUMENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass(frozen=True)
class SourceContentError(RuntimeError):
    error_type: str
    message: str

    def __str__(self) -> str:
        return self.message


def validate_document_id(document_id: str) -> str:
    normalized = str(document_id or "").strip()
    if not DOCUMENT_ID_PATTERN.fullmatch(normalized):
        raise SourceContentError("NOT_FOUND", "资料已被删除或移动。")
    return normalized


def read_source_document_content(document_id: str) -> SourceDocumentContentResponse:
    """Read one document already registered in the local library index.

    The caller supplies only a library document id. The indexed path is never
    accepted from, or returned to, the client.
    """

    normalized_id = validate_document_id(document_id)
    document = get_document(normalized_id)
    if document is None:
        raise SourceContentError("NOT_FOUND", "资料已被删除或移动。")

    source_path = Path(document.file_path or "")
    extension = source_path.suffix.lower()
    if not source_path.is_file() or extension not in ALLOWED_EXTENSIONS:
        raise SourceContentError(
            "SOURCE_CONTENT_UNAVAILABLE",
            "该资料原文暂时无法读取。",
        )

    try:
        if extension == ".docx":
            parsed = parse_docx_file(str(source_path))
        else:
            parsed = parse_text_file(str(source_path))
        content = str(parsed.get("content") or "")
    except Exception as exc:
        raise SourceContentError(
            "SOURCE_CONTENT_UNAVAILABLE",
            "该资料原文暂时无法读取。",
        ) from exc

    if not content.strip():
        raise SourceContentError(
            "SOURCE_CONTENT_UNAVAILABLE",
            "该资料原文暂时无法读取。",
        )

    return SourceDocumentContentResponse(
        document=SourceDocumentInfo(
            document_id=document.id,
            title=document.title or document.original_filename or "未命名资料",
            filename=document.original_filename or document.title or "未命名资料",
            file_type=document.file_type or extension,
            word_count=document.word_count,
        ),
        content=content,
    )
