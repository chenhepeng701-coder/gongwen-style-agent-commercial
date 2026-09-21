"""Deterministic FLOW3-V3.3 to FLOW4 input adapter."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import uuid4

from app.models.flow4 import (
    FLOW3_FACT_ANCHOR_VERSION,
    FLOW4_CONTRACT_VERSION,
    FactAnchorPack,
    Flow3OutputEnvelope,
    Flow4Input,
    Flow4Source,
    canonical_payload_sha256,
)
from app.services.flow4.integrity_gate import run_handoff_integrity_gate


def build_full_article_handoff(candidate, citations, fact_report, quality_report):
    """Explicit opt-in contract, not a relaxation of FLOW4 v2/v3."""
    from app.models.full_article import FullArticleHandoff
    from app.services.flow4.integrity_gate import validate_full_article_handoff
    handoff = FullArticleHandoff(candidate=candidate, citations=citations,
        fact_report=fact_report, quality_report=quality_report)
    return validate_full_article_handoff(handoff)


def normalize_structured_draft_contract(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    """Project legacy single-evidence sentences into the canonical FLOW4 shape.

    This is the only compatibility adapter for ``evidence_id``. Canonical
    sentence payloads use ``evidence_ids`` exclusively; ambiguous payloads that
    provide both fields are rejected instead of being merged or truncated.
    """

    payload = dict(value)
    sections = payload.get("sections")
    if not isinstance(sections, list):
        return payload

    normalized_sections: list[Any] = []
    for section in sections:
        if not isinstance(section, Mapping):
            normalized_sections.append(section)
            continue
        normalized_section = dict(section)
        sentences = normalized_section.get("sentences")
        if not isinstance(sentences, list):
            normalized_sections.append(normalized_section)
            continue

        normalized_sentences: list[Any] = []
        for sentence in sentences:
            if not isinstance(sentence, Mapping):
                normalized_sentences.append(sentence)
                continue
            normalized_sentence = dict(sentence)
            has_canonical = "evidence_ids" in normalized_sentence
            has_legacy = "evidence_id" in normalized_sentence
            if has_canonical and has_legacy:
                raise ValueError(
                    "sentence cannot provide both evidence_ids and legacy evidence_id"
                )
            if has_legacy:
                legacy_id = normalized_sentence.pop("evidence_id")
                normalized_sentence["evidence_ids"] = (
                    [] if legacy_id == "" else [legacy_id]
                )
            normalized_sentences.append(normalized_sentence)
        normalized_section["sentences"] = normalized_sentences
        normalized_sections.append(normalized_section)
    payload["sections"] = normalized_sections
    return payload


def normalize_fact_anchor_pack(
    value: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    *,
    analysis_support_pack: Sequence[Any] | None = None,
    source_span_dedup_report: Mapping[str, Any] | None = None,
) -> FactAnchorPack:
    """Normalize either FLOW3 anchor representation into the sole FLOW4 shape.

    Runtime FLOW3 provides the complete object. Historical artifacts provide
    only the anchor array and therefore must be accompanied explicitly by the
    separately persisted analysis support and dedup report. Nothing is inferred
    or regenerated.
    """

    if isinstance(value, Mapping):
        if analysis_support_pack is not None or source_span_dedup_report is not None:
            raise ValueError("companion anchor data is only valid with the historical array shape")
        payload = dict(value)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if analysis_support_pack is None or source_span_dedup_report is None:
            raise ValueError(
                "historical fact_anchor_pack array requires analysis_support_pack "
                "and source_span_dedup_report"
            )
        payload = {
            "schema_version": FLOW3_FACT_ANCHOR_VERSION,
            "fact_anchors": list(value),
            "analysis_support_pack": list(analysis_support_pack),
            "source_span_dedup_report": dict(source_span_dedup_report),
        }
    else:
        raise TypeError("fact_anchor_pack must be a complete mapping or historical anchor sequence")
    return FactAnchorPack.model_validate(payload)


def create_flow3_output_envelope(
    *,
    parsed_structured_draft: Mapping[str, Any],
    content_plan: Mapping[str, Any],
    fact_anchor_pack: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    validation_reports: Mapping[str, Any],
    task: Mapping[str, Any] | Any,
    analysis_support_pack: Sequence[Any] | None = None,
    source_span_dedup_report: Mapping[str, Any] | None = None,
) -> Flow3OutputEnvelope:
    """Create a detached, typed snapshot from FLOW3 output components."""

    if is_dataclass(task) and not isinstance(task, type):
        task_snapshot = asdict(task)
    elif isinstance(task, Mapping):
        task_snapshot = dict(task)
    elif hasattr(task, "model_dump"):
        task_snapshot = task.model_dump(mode="python")
    else:
        raise TypeError("task must be a mapping, dataclass instance, or Pydantic model")
    normalized_pack = normalize_fact_anchor_pack(
        fact_anchor_pack,
        analysis_support_pack=analysis_support_pack,
        source_span_dedup_report=source_span_dedup_report,
    )
    return Flow3OutputEnvelope.model_validate(
        {
            "parsed_structured_draft": normalize_structured_draft_contract(
                parsed_structured_draft
            ),
            "content_plan": dict(content_plan),
            "fact_anchor_pack": normalized_pack,
            "validation_reports": dict(validation_reports),
            "task": task_snapshot,
        }
    )


def calculate_core_payload_sha256(envelope: Flow3OutputEnvelope) -> str:
    """Hash the canonical JSON representation of the five frozen core fields."""

    return canonical_payload_sha256(envelope.model_dump(mode="json"))


def build_flow4_input(
    envelope: Flow3OutputEnvelope | Mapping[str, Any],
    *,
    run_id: str,
    flow3_status: str,
    handoff_id: str | None = None,
    case_id: str | None = None,
    created_at: datetime | None = None,
    artifact_root: str | Path | None = None,
    render_options: Mapping[str, Any] | None = None,
    extensions: Mapping[str, Any] | None = None,
) -> Flow4Input:
    """Build the formal FLOW4 input and release it only after the integrity gate."""

    if not isinstance(envelope, Flow3OutputEnvelope):
        envelope_payload = dict(envelope)
        structured_draft = envelope_payload.get("parsed_structured_draft")
        if isinstance(structured_draft, Mapping):
            envelope_payload["parsed_structured_draft"] = (
                normalize_structured_draft_contract(structured_draft)
            )
        envelope = Flow3OutputEnvelope.model_validate(envelope_payload)

    source = Flow4Source(
        flow="FLOW3",
        flow_version="V3.3",
        run_id=run_id,
        case_id=case_id,
        created_at=created_at or datetime.now(timezone.utc),
        payload_sha256=calculate_core_payload_sha256(envelope),
        artifact_root=str(artifact_root) if artifact_root is not None else None,
        flow3_status=flow3_status,
    )
    flow4_input = Flow4Input.model_validate(
        {
            "contract_version": FLOW4_CONTRACT_VERSION,
            "handoff_id": handoff_id or f"FLOW4-HANDOFF-{uuid4().hex.upper()}",
            "source": source,
            **envelope.model_dump(mode="python"),
            "render_options": dict(render_options) if render_options is not None else None,
            "extensions": dict(extensions) if extensions is not None else None,
        }
    )
    run_handoff_integrity_gate(envelope, flow4_input)
    return flow4_input
