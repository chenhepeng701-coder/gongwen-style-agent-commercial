"""Opt-in whole-article contracts. Display locations grant no evidence authority.

Strings hold canonical JSON snapshots so nested mutable dictionaries cannot mutate
an accepted version. Offsets are Unicode code points, not JS UTF-16 code units.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_serializer, model_validator


def text_sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def article_revision_digest(body_hash: str, version: int, parent: str, candidate: str = "") -> str:
    return text_sha(canonical_json({"body": body_hash, "version": version, "parent": parent, "candidate": candidate}))


class FullArticleContractError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ArticleBlock(FrozenModel):
    block_id: str
    start: int
    end: int
    text_start: int
    level: int = Field(ge=0, le=6)
    line: int


def article_blocks(content: str) -> tuple[ArticleBlock, ...]:
    """Index every nonblank line without trimming or rewriting its payload."""
    result = []
    offset = 0
    for line_no, line in enumerate(content.splitlines(keepends=True), 1):
        raw = line.rstrip("\r\n")
        if raw.strip():
            heading = re.match(r"^(#{1,6})[ \t]+", raw)
            result.append(ArticleBlock(
                block_id=f"B{len(result) + 1:04}", start=offset,
                end=offset + len(raw), text_start=offset + (heading.end() if heading else 0),
                level=len(heading[1]) if heading else 0, line=line_no,
            ))
        offset += len(line)
    return tuple(result)


class ArticleUnit(FrozenModel):
    unit_id: str
    block_id: str
    start: int
    end: int


def article_units(content: str) -> tuple[ArticleUnit, ...]:
    """Conservative review spans; UNCLASSIFIED is never silently ANALYSIS.

    Headings, introduction and signatures are included in review coverage.
    Commas/semicolons do not break propositions and detach their qualifiers.
    """
    result = []
    for block in article_blocks(content):
        text = content[block.text_start:block.end]
        for match in re.finditer(r"[^。！？!?]+[。！？!?]?", text):
            value = match[0]
            left = len(value) - len(value.lstrip())
            right = len(value.rstrip())
            if left == right:
                continue
            result.append(ArticleUnit(
                unit_id=f"U{len(result) + 1:04}", block_id=block.block_id,
                start=block.text_start + match.start() + left,
                end=block.text_start + match.start() + right,
            ))
    return tuple(result)


class FullArticleCandidate(FrozenModel):
    contract: Literal["full-article/v1"] = "full-article/v1"
    content: str
    version: int = Field(default=1, ge=1)
    parent_digest: str = ""
    evidence_json: str
    task_json: str
    provenance_json: str

    @model_validator(mode="after")
    def validate_snapshots(self):
        if self.version > 1 and not re.fullmatch(r"[0-9a-f]{64}", self.parent_digest):
            raise FullArticleContractError("ARTICLE_PARENT_VERSION_REQUIRED")
        blocks = article_blocks(self.content)
        if (not blocks or blocks[0].level != 1 or not any(b.level == 0 for b in blocks)
                or not self.content[blocks[0].text_start:blocks[0].end].strip()):
            raise FullArticleContractError("FULL_ARTICLE_BODY_OR_TITLE_MISSING")
        for value in (self.evidence_json, self.task_json, self.provenance_json):
            if canonical_json(json.loads(value)) != value:
                raise FullArticleContractError("NONCANONICAL_SNAPSHOT")
        registry = json.loads(self.evidence_json)
        if not isinstance(registry, dict) or not registry:
            raise FullArticleContractError("EVIDENCE_SNAPSHOT_REQUIRED")
        for eid, row in registry.items():
            anchor, source, fact = row["anchor"], row["source_scope"], row["fact"]
            if (eid != anchor["evidence_id"] or eid != fact["evidence_id"]
                    or not row["section_id"] or row["section_id"] != anchor["section_id"]
                    or not source["source_span"] or not source["document_id"] or not source["chunk_id"]
                    or text_sha(source["source_span"]) != source["source_span_sha256"]):
                raise FullArticleContractError("EVIDENCE_SNAPSHOT_BINDING_INVALID")
        return self

    @property
    def content_sha256(self):
        return text_sha(self.content)

    @property
    def evidence_sha256(self):
        return text_sha(self.evidence_json)

    @property
    def digest(self):
        return text_sha(canonical_json(self.model_dump()))

    @property
    def title(self):
        block = article_blocks(self.content)[0]
        return self.content[block.text_start:block.end]


class CitationProposal(FrozenModel):
    unit_id: str
    scope_id: str
    evidence_ids: tuple[str, ...]
    origin: Literal["EXACT_WHOLE_UNIT", "SUPPLIED_FOR_REVIEW"]


class CitationSidecar(FrozenModel):
    candidate_digest: str
    evidence_sha256: str
    proposals: tuple[CitationProposal, ...] = ()

    @property
    def digest(self):
        return text_sha(canonical_json(self.model_dump()))


class BoundArticleReport(FrozenModel):
    owner: Literal["FACT", "QUALITY"]
    candidate_digest: str
    evidence_sha256: str
    sidecar_digest: str
    result_json: str
    result_sha256: str

    @model_validator(mode="after")
    def verify_result(self):
        if text_sha(self.result_json) != self.result_sha256:
            raise FullArticleContractError("REPORT_DIGEST_INVALID")
        if canonical_json(json.loads(self.result_json)) != self.result_json:
            raise FullArticleContractError("REPORT_JSON_INVALID")
        return self

    def require_current(self, candidate: FullArticleCandidate, sidecar: CitationSidecar):
        if (self.candidate_digest != candidate.digest
                or self.evidence_sha256 != candidate.evidence_sha256
                or sidecar.candidate_digest != candidate.digest
                or sidecar.evidence_sha256 != candidate.evidence_sha256
                or self.sidecar_digest != sidecar.digest):
            raise FullArticleContractError("STALE_ARTICLE_REPORT")


def bind_report(owner, candidate, sidecar, result):
    payload = canonical_json(result)
    return BoundArticleReport(owner=owner, candidate_digest=candidate.digest,
        evidence_sha256=candidate.evidence_sha256, sidecar_digest=sidecar.digest,
        result_json=payload, result_sha256=text_sha(payload))


def article_check_identity(candidate):
    """Explicit report identity; the candidate digest also binds every snapshot."""
    registry = json.loads(candidate.evidence_json)
    return {
        "article_version": candidate.version, "article_hash": candidate.content_sha256,
        "candidate_digest": candidate.digest,
        "evidence_registry_hash": candidate.evidence_sha256,
        "task_identity": text_sha(candidate.task_json),
        "provenance_identity": text_sha(candidate.provenance_json),
        "source_identity": text_sha(canonical_json({
            eid: row["source_scope"] for eid, row in registry.items()})),
    }


class FullArticleProjection(FrozenModel):
    contract: Literal["full-article/v1"] = "full-article/v1"
    content_sha256: str
    candidate_digest: str
    blocks: tuple[ArticleBlock, ...]

    def require_content(self, content: str):
        if self.content_sha256 != text_sha(content) or self.blocks != article_blocks(content):
            raise FullArticleContractError("ARTICLE_PROJECTION_INVALID")


class FullArticleHandoff(FrozenModel):
    contract: Literal["flow4-full-article/v1"] = "flow4-full-article/v1"
    candidate: FullArticleCandidate
    citations: CitationSidecar
    fact_report: BoundArticleReport
    quality_report: BoundArticleReport


class FullArticleRenderResult(FrozenModel):
    handoff: FullArticleHandoff
    projection: FullArticleProjection

    @property
    def final_text(self):
        return self.handoff.candidate.content

    @property
    def title(self):
        return self.handoff.candidate.title


class CanonicalArticleBody(BaseModel):
    """Delivery metadata, not a new Candidate or evidence/safety contract.

    Full articles store only article_markdown. `content` is a compatibility view;
    legacy results keep their existing content storage and behavior.
    """
    legacy_content: str = Field(default="", validation_alias="content", exclude=True)
    article_markdown: str | None = None
    article_version: int = Field(default=1, ge=1)
    article_sha256: str = ""
    article_revision_id: str = ""
    parent_revision_id: str = ""
    source_candidate_digest: str = ""
    previous_revision: dict | None = None
    content_projection: FullArticleProjection | None = None
    full_article_check_status: dict | None = None

    @computed_field
    @property
    def content(self) -> str:
        return self.article_markdown if self.article_markdown is not None else self.legacy_content

    @content.setter
    def content(self, value: str):
        if self.article_markdown is not None:
            self.article_markdown = value
        else:
            self.legacy_content = value

    @model_validator(mode="before")
    @classmethod
    def accept_compatibility_view(cls, data):
        if not isinstance(data, dict):
            return data
        data = dict(data)
        if data.get("article_markdown") is not None or data.get("content_projection") is not None:
            body = data.get("article_markdown")
            if body is None:
                body = data.get("content", "")
            if "content" in data and data["content"] != body:
                raise FullArticleContractError("ARTICLE_BODY_ALIAS_CONFLICT")
            data["article_markdown"] = body
            data.pop("content", None)
        return data

    @model_validator(mode="after")
    def initialize_article_identity(self):
        if self.article_markdown is None:
            return self
        body = self.article_markdown
        digest = text_sha(body)
        if self.article_sha256 and self.article_sha256 != digest:
            raise FullArticleContractError("ARTICLE_HASH_INVALID")
        self.article_sha256 = digest
        expected_revision = article_revision_digest(digest, self.article_version, self.parent_revision_id,
            self.content_projection.candidate_digest if self.content_projection else "")
        if self.article_revision_id and self.article_revision_id != expected_revision:
            raise FullArticleContractError("ARTICLE_REVISION_INVALID")
        self.article_revision_id = expected_revision
        if not self.source_candidate_digest and self.content_projection:
            self.source_candidate_digest = self.content_projection.candidate_digest
        self.require_current_body()
        return self

    def require_current_body(self):
        if self.article_markdown is None:
            return
        if self.content_projection is None:
            raise FullArticleContractError("ARTICLE_PROJECTION_REQUIRED")
        self.content_projection.require_content(self.article_markdown)
        if self.article_sha256 != text_sha(self.article_markdown):
            raise FullArticleContractError("ARTICLE_HASH_INVALID")
        if self.article_revision_id != article_revision_digest(self.article_sha256, self.article_version,
                self.parent_revision_id, self.content_projection.candidate_digest):
            raise FullArticleContractError("ARTICLE_REVISION_INVALID")
        if self.article_version > 1:
            if not re.fullmatch(r"[0-9a-f]{64}", self.parent_revision_id):
                raise FullArticleContractError("ARTICLE_PARENT_VERSION_REQUIRED")
            checks = self.full_article_check_status or {}
            if checks.get("fact") != "STALE" or checks.get("quality") != "STALE" or checks.get("required_checks_complete") is not False:
                raise FullArticleContractError("EDITED_ARTICLE_CHECKS_MUST_BE_STALE")

    @model_serializer(mode="wrap")
    def serialize_body(self, handler):
        self.require_current_body()
        return handler(self)
