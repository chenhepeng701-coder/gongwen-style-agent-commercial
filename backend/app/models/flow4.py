"""Frozen data models for the FLOW3-V3.3 to FLOW4 handoff boundary."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator, model_serializer, model_validator


FLOW4_V2_CONTRACT_VERSION = "flow3-v3.3-to-flow4/v2"
FLOW4_CONTRACT_VERSION = "flow3-v3.3-to-flow4/v3"
PARAGRAPH_FIELDS = ("paragraph_id", "paragraph_order")
FLOW3_CONTENT_PLAN_VERSION = "flow3-v3.3-content-plan-1"
FLOW3_FACT_ANCHOR_VERSION = "flow3-v3.3-fact-anchor-1"

WRITER_EXECUTION_FIELDS = (
    "plan_step",
    "writing_focus_role",
    "expansion_stage",
    "content_function",
    "work_chain_role",
)


def canonical_payload_sha256(payload: Mapping[str, Any]) -> str:
    """Return the frozen contract digest for JSON-compatible core payload data."""

    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _projection_value(value: Any, field_name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(field_name, default)
    return getattr(value, field_name, default)


def paragraph_contract_issues(sections: Sequence[Any]) -> list[dict[str, Any]]:
    """Validate declared boundaries without inferring identity from sentence order."""
    issues: list[dict[str, Any]] = []
    owners: dict[str, str] = {}
    for section in sections:
        section_id = _projection_value(section, "section_id", "")
        sentences = _projection_value(section, "sentences", ()) or ()
        if not isinstance(sentences, (list, tuple)):
            continue  # The existing section validator owns malformed arrays.
        orders: dict[str, int] = {}
        ids_by_order: dict[int, str] = {}
        subsections: dict[str, Any] = {}
        aware = False
        flat = False
        for index, sentence in enumerate(sentences):
            pid = _projection_value(sentence, "paragraph_id")
            order = _projection_value(sentence, "paragraph_order")
            location = {"section_id": section_id, "index": index}
            if pid is None and order is None:
                flat = True
                continue
            aware = True
            if pid is None or order is None:
                issues.append({"rule": "PARAGRAPH_PAIR_INCOMPLETE", **location})
                continue
            if not isinstance(pid, str) or not pid.strip():
                issues.append({"rule": "PARAGRAPH_ID_INVALID", **location})
                continue
            if type(order) is not int or order < 0:
                issues.append({"rule": "PARAGRAPH_ORDER_INVALID", **location})
                continue
            if pid in owners and owners[pid] != section_id:
                issues.append({"rule": "PARAGRAPH_CROSS_SECTION", **location})
            owners[pid] = section_id
            if pid in orders and orders[pid] != order:
                issues.append({"rule": "PARAGRAPH_ORDER_INCONSISTENT", **location})
            if order in ids_by_order and ids_by_order[order] != pid:
                issues.append({"rule": "PARAGRAPH_ORDER_CONFLICT", **location})
            sub = _projection_value(sentence, "subsection_id")
            if pid in subsections and subsections[pid] != sub:
                issues.append({"rule": "PARAGRAPH_CROSS_SUBSECTION", **location})
            orders[pid] = order
            ids_by_order[order] = pid
            subsections[pid] = sub
        if aware and flat:
            issues.append({"rule": "PARAGRAPH_MIXED_SECTION", "section_id": section_id})
        # Explicit paragraph ordering must not reorder or reopen subsection groups.
        if aware:
            raw_subsections = _projection_value(section, "subsections", ()) or ()
            if (not isinstance(raw_subsections, (list, tuple))
                    or any(type(_projection_value(item, "order")) is not int
                           for item in raw_subsections)):
                issues.append({"rule": "PARAGRAPH_SUBSECTION_STRUCTURE_INVALID", "section_id": section_id})
                continue
            declared = sorted(
                raw_subsections,
                key=lambda item: _projection_value(item, "order", 0),
            )
            expected = [_projection_value(item, "subsection_id") for item in declared]
            observed = [subsections[pid] for pid in sorted(orders, key=orders.get)]
            groups = [value for index, value in enumerate(observed)
                      if index == 0 or value != observed[index - 1]]
            present = [value for value in expected if value in groups]
            if None in groups:
                present.append(None)  # Legacy unscoped body follows subsection bodies.
            if groups != present:
                issues.append({"rule": "PARAGRAPH_SUBSECTION_ORDER_CONFLICT", "section_id": section_id})
    return issues


def _require_paragraph_contract(sections: Sequence[Any]) -> None:
    issues = paragraph_contract_issues(sections)
    if issues:
        raise ValueError("; ".join(item["rule"] for item in issues))


def preserve_paragraph_identity(
    original_sections: Sequence[Mapping[str, Any]],
    candidate_sections: Sequence[dict[str, Any]],
    *,
    positional_text_identity: bool = False,
    fact_safety_isolation_trace: Sequence[Mapping[str, Any]] = (),
) -> None:
    """Transport metadata; never use it to choose or assign evidence.

    Binding is a positional, text-preserving projection. Repair must retain
    identifiable FACT bindings or unchanged ANALYSIS atoms in the same order.
    A Fact Safety-proved evidence subset may identify the same positional FACT;
    expansion, rebinding, and unproved pruning remain identity failures.  An
    ambiguous/new atom fails instead of guessing its paragraph ownership.
    """
    if not any(_projection_value(sentence, field) is not None
               for section in original_sections
               for sentence in _projection_value(section, "sentences", ()) or ()
               for field in PARAGRAPH_FIELDS):
        return
    _require_paragraph_contract(original_sections)
    if not all(isinstance(section, Mapping) for section in candidate_sections):
        raise ValueError("PARAGRAPH_IDENTITY_LOST: section is not an object")
    if [s.get("section_id") for s in original_sections] != [s.get("section_id") for s in candidate_sections]:
        raise ValueError("PARAGRAPH_IDENTITY_LOST: section identity changed")
    pending = []
    for original, candidate in zip(original_sections, candidate_sections):
        before = original.get("sentences") or []
        after = candidate.get("sentences") or []
        if not any(s.get("paragraph_id") is not None for s in before):
            continue
        if not isinstance(after, (list, tuple)) or not all(isinstance(s, dict) for s in after):
            raise ValueError("PARAGRAPH_IDENTITY_LOST: invalid sentence structure")
        if len(before) != len(after):
            raise ValueError("PARAGRAPH_IDENTITY_LOST: sentence cardinality changed")

        def identity(sentence):
            if positional_text_identity or sentence.get("sentence_type") != "FACT":
                relation = sentence.get("text")
            else:
                relation = (sentence.get("evidence_ids") or
                            ([sentence["evidence_id"]] if sentence.get("evidence_id") else None) or
                            sentence.get("fact_unit_ids"))
                if not relation:
                    raise ValueError("PARAGRAPH_IDENTITY_LOST: FACT identity unavailable")
            return json.dumps([sentence.get("sentence_type"), sentence.get("subsection_id"),
                               relation], ensure_ascii=False, sort_keys=True)

        def evidence_ids(sentence: Mapping[str, Any]) -> tuple[str, ...]:
            values = sentence.get("evidence_ids")
            if isinstance(values, (list, tuple)):
                return tuple(str(value) for value in values if str(value))
            value = str(sentence.get("evidence_id") or "")
            return (value,) if value else ()

        def proved_safety_narrowing(
            index: int,
            old: Mapping[str, Any],
            new: Mapping[str, Any],
        ) -> bool:
            if (
                positional_text_identity
                or old.get("sentence_type") != "FACT"
                or new.get("sentence_type") != "FACT"
                or old.get("subsection_id") != new.get("subsection_id")
                or old.get("semantic_state") != new.get("semantic_state")
            ):
                return False
            original_ids = evidence_ids(old)
            retained_ids = evidence_ids(new)
            retained_set = set(retained_ids)
            if (
                not retained_ids
                or len(original_ids) != len(set(original_ids))
                or len(retained_ids) != len(retained_set)
                or not retained_set < set(original_ids)
                or retained_ids != tuple(
                    value for value in original_ids if value in retained_set
                )
            ):
                return False
            removed_ids = tuple(
                value for value in original_ids if value not in retained_set
            )
            return any(
                isinstance(record, Mapping)
                and str(record.get("section_id") or "") == str(original.get("section_id") or "")
                and type(record.get("sentence_index")) is int
                and record.get("sentence_index") == index
                and record.get("post_isolation_validation_status") == "PASS"
                and tuple(record.get("original_evidence_ids") or ()) == original_ids
                and tuple(record.get("retained_evidence_ids") or ()) == retained_ids
                and tuple(record.get("removed_evidence_ids") or ()) == removed_ids
                for record in fact_safety_isolation_trace
            )

        before_keys = [identity(sentence) for sentence in before]
        after_keys = [identity(sentence) for sentence in after]
        if any(
            old_key != new_key and not proved_safety_narrowing(index, old, new)
            for index, (old, new, old_key, new_key) in enumerate(
                zip(before, after, before_keys, after_keys)
            )
        ):
            raise ValueError("PARAGRAPH_IDENTITY_LOST: sentence identity/order changed")
        if not positional_text_identity and len(set(before_keys)) != len(before_keys):
            if ([s.get("text") for s in before] != [s.get("text") for s in after]
                    or any(new.get(field) != old.get(field)
                           for old, new in zip(before, after) for field in PARAGRAPH_FIELDS)):
                raise ValueError("PARAGRAPH_IDENTITY_LOST: ambiguous sentence identity")
        for old, new in zip(before, after):
            for field in PARAGRAPH_FIELDS:
                if new.get(field) is not None and new[field] != old.get(field):
                    raise ValueError("PARAGRAPH_IDENTITY_LOST: ownership changed")
            pending.append((old, new))
    for old, new in pending:
        for field in PARAGRAPH_FIELDS:
            new[field] = old[field]
    _require_paragraph_contract(candidate_sections)


class _ParagraphSentence(_StrictModel):
    paragraph_id: StrictStr | None = None
    paragraph_order: StrictInt | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_paragraph_pair(self) -> "_ParagraphSentence":
        if (self.paragraph_id is None) != (self.paragraph_order is None):
            raise ValueError("PARAGRAPH_PAIR_INCOMPLETE")
        if self.paragraph_id is not None and not self.paragraph_id.strip():
            raise ValueError("PARAGRAPH_ID_INVALID")
        return self

    @model_serializer(mode="wrap")
    def serialize_legacy_shape(self, handler):
        payload = handler(self)
        # Preserve the v2 canonical digest when optional v3 fields are absent.
        for field in PARAGRAPH_FIELDS:
            if payload.get(field) is None:
                payload.pop(field, None)
        return payload


def project_section_body_lines(
    sentences: Sequence[Any],
    subsections: Sequence[Any],
    *,
    join_adjacent_facts: bool = False,
) -> tuple[str, ...]:
    """Project one section without changing any sentence text.

    Subsection sentences are grouped by the declared subsection order while
    retaining their original relative order. Gaps in ``order`` are meaningful
    FLOW3 output and are therefore preserved rather than filled or renumbered.
    """

    sentence_items = list(sentences)
    if any(_projection_value(item, "paragraph_id") is not None
           or _projection_value(item, "paragraph_order") is not None
           for item in sentence_items):
        return tuple(item.text for item in project_rendered_paragraphs(sentence_items, subsections))
    subsection_items = sorted(
        subsections,
        key=lambda item: int(_projection_value(item, "order", 0) or 0),
    )
    lines: list[str] = []
    rendered_sentence_indexes: set[int] = set()

    for subsection in subsection_items:
        subsection_id = str(
            _projection_value(subsection, "subsection_id", "") or ""
        )
        body_parts: list[str] = []
        for sentence_index, sentence in enumerate(sentence_items):
            if str(
                _projection_value(sentence, "subsection_id", "") or ""
            ) != subsection_id:
                continue
            rendered_sentence_indexes.add(sentence_index)
            text = _projection_value(sentence, "text", "")
            if isinstance(text, str) and text:
                body_parts.append(text)
        if not body_parts:
            continue
        lead = _projection_value(subsection, "lead", "")
        lead_text = lead if isinstance(lead, str) else str(lead or "")
        body_text = "".join(body_parts)
        lines.append(
            body_text
            if lead_text and body_text.startswith(lead_text)
            else lead_text + body_text
        )

    fact_run: list[str] = []
    for sentence_index, sentence in enumerate(sentence_items):
        if sentence_index in rendered_sentence_indexes:
            continue
        text = _projection_value(sentence, "text", "")
        if not isinstance(text, str) or not text:
            continue
        if (
            join_adjacent_facts
            and _projection_value(sentence, "sentence_type", "") == "FACT"
        ):
            fact_run.append(text)
            continue
        if fact_run:
            lines.append("".join(fact_run))
            fact_run = []
        lines.append(text)
    if fact_run:
        lines.append("".join(fact_run))
    return tuple(lines)


def _validate_sentence_contract(
    *,
    sentence_type: str,
    evidence_ids: Sequence[str],
    execution_metadata: Mapping[str, str | None],
) -> None:
    if any(not evidence_id.strip() for evidence_id in evidence_ids):
        raise ValueError("sentence evidence_ids must contain only non-blank values")
    if len(evidence_ids) != len(set(evidence_ids)):
        raise ValueError("sentence evidence_ids must not contain duplicates")
    if sentence_type == "FACT" and not evidence_ids:
        raise ValueError("FACT sentence evidence_ids must not be empty")
    if sentence_type == "ANALYSIS" and evidence_ids:
        raise ValueError("ANALYSIS sentence evidence_ids must be empty")

    present = [
        field_name
        for field_name, value in execution_metadata.items()
        if value is not None
    ]
    if present and len(present) != len(WRITER_EXECUTION_FIELDS):
        raise ValueError(
            "Writer Execution metadata must provide all five fields or none"
        )
    if any(
        value is not None and not value.strip()
        for value in execution_metadata.values()
    ):
        raise ValueError("Writer Execution metadata values must not be blank")


class StructuredSentence(_ParagraphSentence):
    """The canonical sentence atom produced by FLOW3 and consumed by FLOW4."""

    sentence_type: Literal["FACT", "ANALYSIS"]
    evidence_ids: list[StrictStr]
    text: StrictStr
    subsection_id: StrictStr | None = None
    plan_step: StrictStr | None = None
    writing_focus_role: StrictStr | None = None
    expansion_stage: StrictStr | None = None
    content_function: StrictStr | None = None
    work_chain_role: StrictStr | None = None

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("sentence text must not be blank")
        return value

    @model_validator(mode="after")
    def validate_evidence_shape(self) -> "StructuredSentence":
        _validate_sentence_contract(
            sentence_type=self.sentence_type,
            evidence_ids=self.evidence_ids,
            execution_metadata={
                field_name: getattr(self, field_name)
                for field_name in WRITER_EXECUTION_FIELDS
            },
        )
        if self.subsection_id is not None and not self.subsection_id.strip():
            raise ValueError("sentence subsection_id must not be blank")
        return self


class StructuredSubsection(_StrictModel):
    """FLOW3 subsection metadata preserved for deterministic rendering."""

    subsection_id: StrictStr
    lead: StrictStr
    order: StrictInt = Field(ge=1)

    @field_validator("subsection_id")
    @classmethod
    def validate_subsection_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("subsection_id must not be blank")
        return value


class StructuredSection(_StrictModel):
    section_id: StrictStr
    heading: StrictStr
    sentences: list[StructuredSentence]
    subsections: list[StructuredSubsection] = Field(default_factory=list)

    @field_validator("section_id", "heading")
    @classmethod
    def validate_non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("section_id and heading must not be blank")
        return value

    @model_validator(mode="after")
    def validate_subsection_contract(self) -> "StructuredSection":
        _require_paragraph_contract([self])
        subsection_ids = [item.subsection_id for item in self.subsections]
        if len(subsection_ids) != len(set(subsection_ids)):
            raise ValueError("section subsection_id values must be unique")
        subsection_orders = [item.order for item in self.subsections]
        if (
            len(subsection_orders) != len(set(subsection_orders))
            or subsection_orders != sorted(subsection_orders)
        ):
            raise ValueError(
                "section subsections must use unique increasing order values; gaps are allowed"
            )

        for sentence in self.sentences:
            subsection_id = sentence.subsection_id
            if subsection_id is None:
                continue
            if subsection_id not in subsection_ids:
                raise ValueError(
                    f"sentence subsection_id {subsection_id!r} is absent from section subsections"
                )
        return self


class ParsedStructuredDraft(_StrictModel):
    title: StrictStr
    sections: list[StructuredSection] = Field(min_length=1)

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("draft title must not be blank")
        return value

    @model_validator(mode="after")
    def validate_unique_sections(self) -> "ParsedStructuredDraft":
        _require_paragraph_contract(self.sections)
        section_ids = [section.section_id for section in self.sections]
        if len(section_ids) != len(set(section_ids)):
            raise ValueError("structured draft section_id values must be unique")
        return self


class ContentPlanSection(BaseModel):
    """FLOW3 plan section with all original audit fields preserved."""

    model_config = ConfigDict(extra="allow")

    section_id: StrictStr

    @field_validator("section_id")
    @classmethod
    def validate_section_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content plan section_id must not be blank")
        return value


class ContentPlan(BaseModel):
    """Minimum typed plan contract; FLOW3-specific fields remain untouched."""

    model_config = ConfigDict(extra="allow")

    schema_version: Literal["flow3-v3.3-content-plan-1"]
    plan_id: StrictStr
    sections: list[ContentPlanSection] = Field(min_length=1)
    target_total: StrictInt = Field(ge=0)

    @field_validator("plan_id")
    @classmethod
    def validate_plan_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content plan plan_id must not be blank")
        return value

    @model_validator(mode="after")
    def validate_unique_sections(self) -> "ContentPlan":
        section_ids = [section.section_id for section in self.sections]
        if len(section_ids) != len(set(section_ids)):
            raise ValueError("content plan section_id values must be unique")
        return self


class FactAnchor(BaseModel):
    """Minimum anchor contract; additional FLOW3 audit fields are preserved."""

    model_config = ConfigDict(extra="allow")

    evidence_id: StrictStr
    section_id: StrictStr
    role: Literal["core", "supporting"]
    canonical_subject: StrictStr
    canonical_action: StrictStr
    canonical_object: StrictStr
    exact_values: dict[str, Any]
    forbidden_detail_types: list[Any]

    @field_validator("evidence_id", "section_id")
    @classmethod
    def validate_non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("anchor evidence_id and section_id must not be blank")
        return value


class FactAnchorPack(_StrictModel):
    """The only fact-anchor shape accepted by FLOW4."""

    schema_version: Literal["flow3-v3.3-fact-anchor-1"]
    fact_anchors: list[FactAnchor]
    analysis_support_pack: list[Any]
    source_span_dedup_report: dict[str, Any]

    @model_validator(mode="after")
    def validate_unique_evidence_ids(self) -> "FactAnchorPack":
        evidence_ids = [anchor.evidence_id for anchor in self.fact_anchors]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("fact anchor evidence_id values must be unique")
        return self


class ValidationReports(_StrictModel):
    """FLOW3 reports carried verbatim; only frozen minimum keys are checked."""

    structured: dict[str, Any]
    fact: dict[str, Any]
    analysis: dict[str, Any]
    source_paraphrase: dict[str, Any] | None = None
    preflight: dict[str, Any] | None = None
    content_plan_quality: dict[str, Any] | None = None
    evidence_selection: dict[str, Any] | None = None
    fact_anchor_quality: dict[str, Any] | None = None
    atomic_evidence_quality: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_minimum_gate_fields(self) -> "ValidationReports":
        required = {
            "structured": ("passed", "invalid_evidence_reference_count"),
            "fact": ("passed", "unsupported_count", "out_of_plan_count"),
            "analysis": ("passed", "analysis_fact_leak_count"),
        }
        for report_name, keys in required.items():
            report = getattr(self, report_name)
            missing = [key for key in keys if key not in report]
            if missing:
                raise ValueError(f"{report_name} report missing required fields: {', '.join(missing)}")
        return self


class Flow4Task(_StrictModel):
    """JSON snapshot of OfficialDraftTask; never a live dataclass reference."""

    title: StrictStr
    document_type: StrictStr
    target_words: StrictInt = Field(gt=0)
    export_format: Literal["txt", "docx"]
    topic: StrictStr | None = None
    level: StrictStr | None = None
    org_type: StrictStr | None = None
    business_domain: StrictStr | None = None
    writing_purpose: StrictStr | None = None
    reference_scope: StrictStr | None = None
    scene_compat: StrictStr | None = None
    document_type_from_scene_compat: bool | None = None

    @field_validator("title", "document_type")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("task title and document_type must not be blank")
        return value


class Flow3OutputEnvelope(_StrictModel):
    """Typed FLOW3 output consumed by the handoff adapter, never by FLOW4 directly."""

    parsed_structured_draft: ParsedStructuredDraft
    content_plan: ContentPlan
    fact_anchor_pack: FactAnchorPack
    validation_reports: ValidationReports
    task: Flow4Task


class Flow4Source(_StrictModel):
    flow: Literal["FLOW3"]
    flow_version: Literal["V3.3"]
    run_id: StrictStr
    case_id: StrictStr | None = None
    created_at: datetime
    payload_sha256: StrictStr
    artifact_root: StrictStr | None = None
    flow3_status: StrictStr

    @field_validator("run_id", "flow3_status")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("source run_id and flow3_status must not be blank")
        return value

    @field_validator("payload_sha256")
    @classmethod
    def validate_payload_sha256(cls, value: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("payload_sha256 must be a lowercase SHA-256 hex digest")
        return value

    @field_validator("created_at")
    @classmethod
    def validate_created_at_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("source created_at must include a timezone")
        return value


class Flow4RenderOptions(_StrictModel):
    """Optional formatting-only controls reserved by the frozen input contract."""

    formats: list[Literal["txt", "docx"]] | None = None
    include_title_in_plain_text: bool = True
    paragraph_mode: Literal["section_sentences", "paragraph_aware"] = "section_sentences"
    output_name: StrictStr | None = None
    output_dir: StrictStr | None = None


class Flow4Input(_StrictModel):
    """The sole formal input accepted by later FLOW4 stages."""

    contract_version: Literal["flow3-v3.3-to-flow4/v2", "flow3-v3.3-to-flow4/v3"]
    handoff_id: StrictStr
    source: Flow4Source
    parsed_structured_draft: ParsedStructuredDraft
    content_plan: ContentPlan
    fact_anchor_pack: FactAnchorPack
    validation_reports: ValidationReports
    task: Flow4Task
    render_options: Flow4RenderOptions | None = None
    extensions: dict[str, Any] | None = None

    @field_validator("handoff_id")
    @classmethod
    def validate_handoff_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("handoff_id must not be blank")
        return value

    @model_validator(mode="after")
    def validate_handoff_integrity(self) -> "Flow4Input":
        if self.contract_version == FLOW4_V2_CONTRACT_VERSION and (
            any(sentence.paragraph_id is not None
                for section in self.parsed_structured_draft.sections
                for sentence in section.sentences)
            or (self.render_options and self.render_options.paragraph_mode != "section_sentences")
        ):
            raise ValueError("PARAGRAPH_REQUIRES_FLOW4_V3")
        if (self.render_options and self.render_options.paragraph_mode == "paragraph_aware"
                and any(section.sentences and section.sentences[0].paragraph_id is None
                        for section in self.parsed_structured_draft.sections)):
            raise ValueError("PARAGRAPH_METADATA_REQUIRED")
        plan_section_ids = [section.section_id for section in self.content_plan.sections]
        draft_section_ids = [section.section_id for section in self.parsed_structured_draft.sections]
        if draft_section_ids != plan_section_ids:
            raise ValueError(
                "structured draft section identity/order does not match content plan: "
                f"{draft_section_ids!r} != {plan_section_ids!r}"
            )

        anchors = {
            anchor.evidence_id: anchor.section_id
            for anchor in self.fact_anchor_pack.fact_anchors
        }
        for section in self.parsed_structured_draft.sections:
            for index, sentence in enumerate(section.sentences):
                if sentence.sentence_type != "FACT":
                    continue
                for evidence_id in sentence.evidence_ids:
                    anchor_section = anchors.get(evidence_id)
                    if anchor_section is None:
                        raise ValueError(
                            f"FACT evidence_id {evidence_id!r} is absent from fact_anchor_pack "
                            f"(section {section.section_id}, sentence {index})"
                        )
                    if anchor_section != section.section_id:
                        raise ValueError(
                            f"FACT evidence_id {evidence_id!r} maps to section "
                            f"{anchor_section!r}, not {section.section_id!r}"
                        )

        core_payload = {
            "parsed_structured_draft": self.parsed_structured_draft.model_dump(mode="json"),
            "content_plan": self.content_plan.model_dump(mode="json"),
            "fact_anchor_pack": self.fact_anchor_pack.model_dump(mode="json"),
            "validation_reports": self.validation_reports.model_dump(mode="json"),
            "task": self.task.model_dump(mode="json"),
        }
        actual_digest = canonical_payload_sha256(core_payload)
        if actual_digest != self.source.payload_sha256:
            raise ValueError("source payload_sha256 does not match the canonical core payload")
        return self


class Flow4RenderTraceItem(_ParagraphSentence):
    """One deterministic mapping from an output sentence to its FLOW4 input atom."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    section_id: StrictStr
    sentence_index: StrictInt = Field(ge=0)
    sentence_type: Literal["FACT", "ANALYSIS"]
    evidence_ids: tuple[StrictStr, ...]
    rendered_text_sha256: StrictStr
    anchor_roles: tuple[Literal["core", "supporting"], ...]
    trace_status: Literal["MAPPED", "ANALYSIS_UNANCHORED"]
    subsection_id: StrictStr | None = None
    plan_step: StrictStr | None = None
    writing_focus_role: StrictStr | None = None
    expansion_stage: StrictStr | None = None
    content_function: StrictStr | None = None
    work_chain_role: StrictStr | None = None

    @field_validator("rendered_text_sha256")
    @classmethod
    def validate_rendered_text_sha256(cls, value: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("rendered_text_sha256 must be a lowercase SHA-256 hex digest")
        return value

    @model_validator(mode="after")
    def validate_evidence_trace_shape(self) -> "Flow4RenderTraceItem":
        _validate_sentence_contract(
            sentence_type=self.sentence_type,
            evidence_ids=self.evidence_ids,
            execution_metadata={
                field_name: getattr(self, field_name)
                for field_name in WRITER_EXECUTION_FIELDS
            },
        )
        if self.subsection_id is not None and not self.subsection_id.strip():
            raise ValueError("render trace subsection_id must not be blank")
        if self.sentence_type == "FACT":
            if (
                not self.evidence_ids
                or len(self.evidence_ids) != len(self.anchor_roles)
                or self.trace_status != "MAPPED"
            ):
                raise ValueError(
                    "FACT render trace must retain every mapped evidence ID and anchor role"
                )
        elif (
            self.evidence_ids
            or self.anchor_roles
            or self.trace_status != "ANALYSIS_UNANCHORED"
        ):
            raise ValueError("ANALYSIS render trace must remain explicitly unanchored")
        return self


class Flow4RenderValidationSummary(_StrictModel):
    """Verbatim authoritative reports plus the non-upgradable handoff risk state."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    structured: dict[str, Any]
    fact: dict[str, Any]
    analysis: dict[str, Any]
    flow3_status: StrictStr
    risk_status: Literal["NO_RECORDED_RISK", "REVIEW_REQUIRED"]


class Flow4RenderedSentence(_ParagraphSentence):
    """Immutable copy of one rendered sentence atom."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sentence_type: Literal["FACT", "ANALYSIS"]
    evidence_ids: tuple[StrictStr, ...]
    text: StrictStr
    subsection_id: StrictStr | None = None
    plan_step: StrictStr | None = None
    writing_focus_role: StrictStr | None = None
    expansion_stage: StrictStr | None = None
    content_function: StrictStr | None = None
    work_chain_role: StrictStr | None = None

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("rendered sentence text must not be blank")
        return value

    @model_validator(mode="after")
    def validate_evidence_shape(self) -> "Flow4RenderedSentence":
        _validate_sentence_contract(
            sentence_type=self.sentence_type,
            evidence_ids=self.evidence_ids,
            execution_metadata={
                field_name: getattr(self, field_name)
                for field_name in WRITER_EXECUTION_FIELDS
            },
        )
        if self.subsection_id is not None and not self.subsection_id.strip():
            raise ValueError("rendered sentence subsection_id must not be blank")
        return self


class Flow4RenderedParagraph(_StrictModel):
    """One body paragraph; owning section and sentence atoms live in the parent."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    paragraph_id: StrictStr
    paragraph_order: StrictInt = Field(ge=0)
    sentence_indexes: tuple[StrictInt, ...] = Field(min_length=1)
    text: StrictStr

    @model_validator(mode="after")
    def validate_paragraph_record(self) -> "Flow4RenderedParagraph":
        if not self.paragraph_id.strip() or not self.text.strip():
            raise ValueError("PARAGRAPH_RENDER_RECORD_BLANK")
        if (any(index < 0 for index in self.sentence_indexes)
                or list(self.sentence_indexes) != sorted(set(self.sentence_indexes))):
            raise ValueError("PARAGRAPH_SENTENCE_INDEX_INVALID")
        return self


def project_rendered_paragraphs(
    sentences: Sequence[Any], subsections: Sequence[Any],
) -> tuple[Flow4RenderedParagraph, ...]:
    _require_paragraph_contract([{"section_id": "", "sentences": sentences, "subsections": subsections}])
    groups: dict[str, list[int]] = {}
    for index, sentence in enumerate(sentences):
        pid = _projection_value(sentence, "paragraph_id")
        if pid is not None:
            groups.setdefault(pid, []).append(index)
    leads = {_projection_value(sub, "subsection_id"): _projection_value(sub, "lead", "")
             for sub in subsections}
    emitted_subsections: set[str] = set()
    result = []
    for pid, indexes in sorted(groups.items(), key=lambda item: _projection_value(sentences[item[1][0]], "paragraph_order")):
        first = sentences[indexes[0]]
        text = "".join(_projection_value(sentences[index], "text", "") for index in indexes)
        sub = _projection_value(first, "subsection_id")
        if sub is not None and sub not in emitted_subsections:
            lead = leads.get(sub, "")
            if lead and not text.startswith(lead):
                text = lead + text
            emitted_subsections.add(sub)
        result.append(Flow4RenderedParagraph(
            paragraph_id=pid, paragraph_order=_projection_value(first, "paragraph_order"),
            sentence_indexes=tuple(indexes), text=text,
        ))
    return tuple(result)


class Flow4RenderedSection(_StrictModel):
    """Frozen structural snapshot used by non-semantic artifact formatters."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    section_id: StrictStr
    heading: StrictStr
    sentences: tuple[Flow4RenderedSentence, ...]
    subsections: tuple[StructuredSubsection, ...] = ()
    body_lines: tuple[StrictStr, ...] = ()
    join_adjacent_facts: bool = False
    paragraphs: tuple[Flow4RenderedParagraph, ...] = ()

    @model_serializer(mode="wrap")
    def serialize_legacy_shape(self, handler):
        payload = handler(self)
        if not self.paragraphs:
            payload.pop("paragraphs", None)
        return payload

    @field_validator("section_id", "heading")
    @classmethod
    def validate_non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("rendered section identity and heading must not be blank")
        return value

    @model_validator(mode="after")
    def validate_subsection_contract(self) -> "Flow4RenderedSection":
        StructuredSection.model_validate({
            "section_id": self.section_id,
            "heading": self.heading,
            "sentences": [
                sentence.model_dump(mode="python")
                for sentence in self.sentences
            ],
            "subsections": [
                subsection.model_dump(mode="python")
                for subsection in self.subsections
            ],
        })
        expected_lines = project_section_body_lines(
            self.sentences,
            self.subsections,
            join_adjacent_facts=self.join_adjacent_facts,
        )
        if self.body_lines != expected_lines:
            raise ValueError(
                "rendered body_lines do not match the canonical section projection"
            )
        if self.paragraphs != project_rendered_paragraphs(self.sentences, self.subsections):
            raise ValueError("PARAGRAPH_RENDER_SNAPSHOT_MISMATCH")
        return self


def render_section_lines(
    section: Flow4RenderedSection,
) -> list[str]:
    """Expose the canonical lines frozen by the FLOW4 renderer."""

    return [section.heading, *section.body_lines]


class Flow4RenderResult(_StrictModel):
    """The complete output of deterministic FLOW4 phase-one rendering."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    title: StrictStr
    include_title_in_final_text: bool
    sections: tuple[Flow4RenderedSection, ...]
    final_text: StrictStr
    render_trace: tuple[Flow4RenderTraceItem, ...]
    validation_summary: Flow4RenderValidationSummary

    @model_validator(mode="after")
    def validate_deterministic_render_snapshot(self) -> "Flow4RenderResult":
        _require_paragraph_contract(self.sections)
        if not self.title.strip():
            raise ValueError("rendered title must not be blank")
        if not self.sections:
            raise ValueError("rendered sections must not be empty")
        if not self.final_text.strip():
            raise ValueError("final_text must not be blank")

        blocks: list[str] = []
        if self.include_title_in_final_text:
            blocks.append(self.title)

        expected_trace: list[tuple[Any, ...]] = []
        for section in self.sections:
            section_lines = render_section_lines(section)
            for sentence_index, sentence in enumerate(section.sentences):
                expected_trace.append(
                    (
                        section.section_id,
                        sentence_index,
                        sentence.sentence_type,
                        sentence.evidence_ids,
                        hashlib.sha256(sentence.text.encode("utf-8")).hexdigest(),
                        sentence.subsection_id,
                        sentence.plan_step,
                        sentence.writing_focus_role,
                        sentence.expansion_stage,
                        sentence.content_function,
                        sentence.work_chain_role,
                        sentence.paragraph_id,
                        sentence.paragraph_order,
                    )
                )
            blocks.append("\n".join(section_lines))

        if self.final_text != "\n\n".join(blocks):
            raise ValueError("final_text does not match the frozen rendered structure")

        actual_trace = [
            (
                item.section_id,
                item.sentence_index,
                item.sentence_type,
                item.evidence_ids,
                item.rendered_text_sha256,
                item.subsection_id,
                item.plan_step,
                item.writing_focus_role,
                item.expansion_stage,
                item.content_function,
                item.work_chain_role,
                item.paragraph_id,
                item.paragraph_order,
            )
            for item in self.render_trace
        ]
        if actual_trace != expected_trace:
            raise ValueError("render_trace does not match the frozen rendered structure")

        leak_count = self.validation_summary.analysis.get("analysis_fact_leak_count")
        if (
            type(leak_count) is int
            and leak_count > 0
            and self.validation_summary.risk_status != "REVIEW_REQUIRED"
        ):
            raise ValueError("positive analysis_fact_leak_count must remain REVIEW_REQUIRED")
        return self
