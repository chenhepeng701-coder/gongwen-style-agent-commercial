"""Strict models for deterministic FLOW4 artifact delivery packages."""

from __future__ import annotations

import re
import hashlib
from datetime import datetime
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    field_validator,
    model_serializer,
    model_validator,
)

from app.models.flow4 import (
    WRITER_EXECUTION_FIELDS,
    Flow4RenderTraceItem,
    Flow4RenderValidationSummary,
    Flow4RenderedParagraph,
)


ArtifactFileType = Literal["txt", "docx"]


class _FrozenStrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ArtifactEvidenceTraceItem(_FrozenStrictModel):
    section_id: StrictStr
    sentence_index: StrictInt = Field(ge=0)
    sentence_type: Literal["FACT", "ANALYSIS"]
    evidence_ids: tuple[StrictStr, ...]
    text_sha256: StrictStr
    anchor_roles: tuple[Literal["core", "supporting"], ...]
    trace_status: Literal["MAPPED", "ANALYSIS_UNANCHORED"]
    subsection_id: StrictStr | None = None
    plan_step: StrictStr | None = None
    writing_focus_role: StrictStr | None = None
    expansion_stage: StrictStr | None = None
    content_function: StrictStr | None = None
    work_chain_role: StrictStr | None = None

    @field_validator("text_sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("text_sha256 must be a lowercase SHA-256 hex digest")
        return value

    @model_validator(mode="after")
    def validate_trace_shape(self) -> "ArtifactEvidenceTraceItem":
        execution_values = [
            getattr(self, field_name)
            for field_name in WRITER_EXECUTION_FIELDS
        ]
        if any(value is not None for value in execution_values) and not all(
            isinstance(value, str) and value.strip()
            for value in execution_values
        ):
            raise ValueError(
                "artifact Writer Execution metadata must provide all five fields or none"
            )
        if self.subsection_id is not None and not self.subsection_id.strip():
            raise ValueError("artifact trace subsection_id must not be blank")
        if self.sentence_type == "FACT":
            if (
                not self.evidence_ids
                or len(self.evidence_ids) != len(self.anchor_roles)
                or self.trace_status != "MAPPED"
            ):
                raise ValueError("FACT evidence trace must retain all mapped anchors")
        elif (
            self.evidence_ids
            or self.anchor_roles
            or self.trace_status != "ANALYSIS_UNANCHORED"
        ):
            raise ValueError("ANALYSIS evidence trace must remain explicitly unanchored")
        return self


class ArtifactParagraph(Flow4RenderedParagraph):
    """Persist only paragraph ownership and its rendered sentence/text relation."""

    section_id: StrictStr
    sentence_texts: tuple[StrictStr, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_sentence_relation(self) -> "ArtifactParagraph":
        if not self.section_id.strip() or any(not text.strip() for text in self.sentence_texts):
            raise ValueError("PARAGRAPH_ARTIFACT_TEXT_BLANK")
        if len(self.sentence_indexes) != len(self.sentence_texts):
            raise ValueError("PARAGRAPH_ARTIFACT_SENTENCE_COUNT_MISMATCH")
        # A subsection lead may precede the unmodified sentence bodies.
        if not self.text.endswith("".join(self.sentence_texts)):
            raise ValueError("PARAGRAPH_ARTIFACT_TEXT_MISMATCH")
        return self


class ArtifactMetadata(_FrozenStrictModel):
    artifact_id: StrictStr
    source_render_hash: StrictStr
    generated_time: datetime
    file_type: ArtifactFileType
    file_path: StrictStr
    file_size: StrictInt = Field(ge=1)
    file_sha256: StrictStr
    validation_summary: Flow4RenderValidationSummary
    review_status: Literal["NO_RECORDED_RISK", "REVIEW_REQUIRED"]
    render_trace: tuple[Flow4RenderTraceItem, ...] = Field(min_length=1)
    evidence_trace: tuple[ArtifactEvidenceTraceItem, ...] = Field(min_length=1)
    paragraphs: tuple[ArtifactParagraph, ...] = ()

    @model_serializer(mode="wrap")
    def serialize_legacy_shape(self, handler):
        payload = handler(self)
        if not self.paragraphs:
            payload.pop("paragraphs", None)
        return payload

    @field_validator("artifact_id", "file_path")
    @classmethod
    def validate_non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("artifact identity and file path must not be blank")
        return value

    @field_validator("source_render_hash", "file_sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("artifact hashes must be lowercase SHA-256 hex digests")
        return value

    @field_validator("generated_time")
    @classmethod
    def validate_generated_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("generated_time must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_review_status(self) -> "ArtifactMetadata":
        paragraph_aware = any(item.paragraph_id is not None for item in self.render_trace)
        if paragraph_aware and not self.paragraphs:
            raise ValueError("PARAGRAPH_ARTIFACT_SNAPSHOT_MISSING")
        if self.paragraphs:
            expected = {}
            paragraph_ids = set()
            section_orders: dict[str, list[int]] = {}
            paragraph_sections = []
            for paragraph in self.paragraphs:
                if paragraph.paragraph_id in paragraph_ids:
                    raise ValueError("PARAGRAPH_ARTIFACT_ID_DUPLICATE")
                paragraph_ids.add(paragraph.paragraph_id)
                section_orders.setdefault(paragraph.section_id, []).append(paragraph.paragraph_order)
                if not paragraph_sections or paragraph_sections[-1] != paragraph.section_id:
                    paragraph_sections.append(paragraph.section_id)
                for index, text in zip(paragraph.sentence_indexes, paragraph.sentence_texts):
                    key = (paragraph.section_id, index)
                    if key in expected:
                        raise ValueError("PARAGRAPH_ARTIFACT_SENTENCE_DUPLICATE")
                    expected[key] = (paragraph.paragraph_id, paragraph.paragraph_order,
                                     hashlib.sha256(text.encode("utf-8")).hexdigest())
            if any(orders != sorted(set(orders)) for orders in section_orders.values()):
                raise ValueError("PARAGRAPH_ARTIFACT_ORDER_INVALID")
            trace_sections = list(dict.fromkeys(item.section_id for item in self.render_trace if item.paragraph_id is not None))
            if paragraph_sections != trace_sections:
                raise ValueError("PARAGRAPH_ARTIFACT_SECTION_ORDER_INVALID")
            actual = {(item.section_id, item.sentence_index):
                      (item.paragraph_id, item.paragraph_order, item.rendered_text_sha256)
                      for item in self.render_trace if item.paragraph_id is not None}
            if actual != expected or len(actual) != sum(item.paragraph_id is not None for item in self.render_trace):
                raise ValueError("PARAGRAPH_ARTIFACT_TRACE_MISMATCH")
        if self.review_status != self.validation_summary.risk_status:
            raise ValueError("review_status must preserve validation_summary.risk_status")
        render_projection = [
            (
                item.section_id,
                item.sentence_index,
                item.sentence_type,
                item.evidence_ids,
                item.rendered_text_sha256,
                item.anchor_roles,
                item.trace_status,
                item.subsection_id,
                item.plan_step,
                item.writing_focus_role,
                item.expansion_stage,
                item.content_function,
                item.work_chain_role,
            )
            for item in self.render_trace
        ]
        evidence_projection = [
            (
                item.section_id,
                item.sentence_index,
                item.sentence_type,
                item.evidence_ids,
                item.text_sha256,
                item.anchor_roles,
                item.trace_status,
                item.subsection_id,
                item.plan_step,
                item.writing_focus_role,
                item.expansion_stage,
                item.content_function,
                item.work_chain_role,
            )
            for item in self.evidence_trace
        ]
        if render_projection != evidence_projection:
            raise ValueError(
                "artifact evidence trace must preserve the complete render trace"
            )
        return self


class ArtifactManifestEntry(_FrozenStrictModel):
    artifact_id: StrictStr
    file_type: ArtifactFileType
    file_path: StrictStr
    file_size: StrictInt = Field(ge=1)
    sha256: StrictStr
    source_render_hash: StrictStr
    metadata_path: StrictStr
    metadata_size: StrictInt = Field(ge=1)
    metadata_sha256: StrictStr

    @field_validator("artifact_id", "file_path", "metadata_path")
    @classmethod
    def validate_non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("manifest identity and paths must not be blank")
        return value

    @field_validator("sha256", "source_render_hash", "metadata_sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("manifest hashes must be lowercase SHA-256 hex digests")
        return value


class ArtifactManifest(_FrozenStrictModel):
    manifest_version: Literal["flow4-artifact-manifest/v2", "flow4-artifact-manifest/v3"]
    package_id: StrictStr
    source_render_hash: StrictStr
    generated_time: datetime
    review_status: Literal["NO_RECORDED_RISK", "REVIEW_REQUIRED"]
    validation_summary: Flow4RenderValidationSummary
    artifacts: tuple[ArtifactManifestEntry, ...] = Field(min_length=1)

    @field_validator("package_id")
    @classmethod
    def validate_package_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("package_id must not be blank")
        return value

    @field_validator("source_render_hash")
    @classmethod
    def validate_source_render_hash(cls, value: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("source_render_hash must be a lowercase SHA-256 hex digest")
        return value

    @field_validator("generated_time")
    @classmethod
    def validate_generated_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("generated_time must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_preserved_state(self) -> "ArtifactManifest":
        if self.review_status != self.validation_summary.risk_status:
            raise ValueError("manifest review_status must preserve renderer risk_status")
        for entry in self.artifacts:
            if entry.source_render_hash != self.source_render_hash:
                raise ValueError("manifest entry source_render_hash must match manifest")
        return self


class ArtifactDeliveryResult(_FrozenStrictModel):
    package_id: StrictStr
    package_path: StrictStr
    manifest_path: StrictStr
    source_render_hash: StrictStr
    artifacts: tuple[ArtifactManifestEntry, ...]
