"""Prepare one immutable, complete semantic chunk contract for both consumers."""
from dataclasses import dataclass
import hashlib
import json
import logging
from pathlib import Path

from app.models.library import LibraryDocument
from app.services.parser.docx_parser import parse_docx_file
from app.services.parser.txt_parser import parse_text_file
from app.services.retrieval.official_semantic_chunker import semantic_chunk_document

logger = logging.getLogger(__name__)


def source_fingerprint(doc: LibraryDocument) -> str:
    identity = json.dumps([doc.id, doc.title, str(Path(doc.file_path).resolve())],
                          ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(identity + b"\0" + Path(doc.file_path).read_bytes()).hexdigest()


@dataclass(frozen=True)
class PreparedSemanticChunks:
    document_id: str
    source_fingerprint: str
    chunks_json: str

    @property
    def chunks(self) -> list[dict]:
        return json.loads(self.chunks_json)


def prepare_semantic_chunks(doc: LibraryDocument) -> PreparedSemanticChunks:
    path = Path(doc.file_path)
    before = source_fingerprint(doc)
    parsed = (parse_docx_file if path.suffix.lower() == ".docx" else parse_text_file)(str(path))
    chunks = semantic_chunk_document(
        parsed.get("content", ""), document_id=doc.id, source_file=path.name,
        source_path=str(path.resolve()), title=doc.title, paragraphs=parsed.get("paragraphs"),
    )
    if not chunks:
        raise ValueError("文档内容为空，无法处理。")
    if before != source_fingerprint(doc):
        raise ValueError("处理期间原始材料发生变化，请重试。")
    missing_fields = sorted({key for chunk in chunks
        for key in ("document_type", "level", "org_type", "business_domain", "writing_purpose")
        if chunk.get(key) in (None, "", "未知", "unknown")})
    logger.info("SEMANTIC_CHUNK_READY doc_id=%s paragraphs=%s chunks=%s short_merges=%s "
                "secondary_splits=%s missing_metadata=%s", doc.id,
                len(parsed.get("paragraphs") or []), len(chunks),
                sum(bool(chunk.get("short_merged")) for chunk in chunks),
                sum(bool(chunk.get("secondary_split")) for chunk in chunks),
                ",".join(missing_fields) or "none")
    return PreparedSemanticChunks(doc.id, before, json.dumps(chunks, ensure_ascii=False))
