"""Deterministic sanitization for non-deliverable local quality-test previews."""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from app.services.generation.flow3_v3_trace import validate_analysis_sentences


INVALID_FACT_REFERENCE_RULES = {
    "fact_evidence_id_blank",
    "fact_evidence_id_unknown",
    "fact_evidence_id_cross_section",
}
UNCLASSIFIED_SENTENCE_RULES = {"sentence_type"}


def _normalized_text(value: Any) -> str:
    return "".join(str(value or "").split())


def _risk_item(
    *,
    section_id: str,
    section_heading: str,
    paragraph_index: int,
    risk_type: str,
    internal_error_type: str,
) -> dict[str, Any]:
    descriptions = {
        "UNSUPPORTED_FACT": "该事实未通过自动证据支持校验，需要对照原始资料人工复核。",
        "OUT_OF_PLAN_FACT": "该事实超出当前章节允许的证据范围，需要人工复核。",
        "INVALID_EVIDENCE_REFERENCE": "该事实的 evidence_id 绑定无效，需要人工核对证据来源。",
        "UNCLASSIFIED_CONTENT": "该内容无法按 FACT 或 ANALYSIS 合同确认，需要人工复核。",
        "ANALYSIS_FACT_LEAK": "分析句可能包含未经证据支持的具体事实，需要人工复核。",
    }
    return {
        "section_id": section_id,
        "section_heading": section_heading,
        "paragraph_index": paragraph_index,
        "field_name": "sentences.text",
        "risk_type": risk_type,
        "internal_error_type": internal_error_type,
        "risk_description": descriptions.get(
            risk_type,
            "该内容存在无法自动确认的事实风险，需要人工复核。",
        ),
    }


def _analysis_sentence_units(text: str) -> list[str]:
    return [
        match.group(0).strip()
        for match in re.finditer(r"[^。！？；]+[。！？；]?", str(text or ""))
        if match.group(0).strip()
    ]


def _sanitize_analysis_atom(text: str, *, section_id: str) -> str:
    """Keep only natural sentences that independently pass the same analysis gate."""

    units = _analysis_sentence_units(text)
    if not units:
        return ""
    retained: list[str] = []
    for unit in units:
        probe = {
            "sections": [{
                "section_id": section_id,
                "sentences": [{
                    "sentence_type": "ANALYSIS",
                    "evidence_id": "",
                    "text": unit,
                }],
            }],
        }
        if validate_analysis_sentences(probe).get("passed"):
            retained.append(unit)
    return "".join(retained)


def sanitize_quality_test_draft(
    structured_draft: dict[str, Any],
    *,
    structured_report: dict[str, Any],
    fact_report: dict[str, Any],
    analysis_report: dict[str, Any],
) -> dict[str, Any]:
    """Clear unsafe sentence fields without rewriting or inserting review text."""

    cleaned = deepcopy(structured_draft)
    sections = [
        section
        for section in cleaned.get("sections") or []
        if isinstance(section, dict)
    ]
    section_by_id = {
        str(section.get("section_id") or ""): section
        for section in sections
    }
    fact_positions_to_clear: set[tuple[str, int]] = set()
    analysis_positions_to_sanitize: set[tuple[str, int]] = set()
    risk_items: list[dict[str, Any]] = []

    for claim in fact_report.get("unsupported_claims") or []:
        section_id = str(claim.get("section_id") or "")
        section = section_by_id.get(section_id)
        claim_text = _normalized_text(claim.get("sentence"))
        candidate_sections = [section] if section is not None else sections
        matching_positions: list[tuple[dict[str, Any], int]] = []
        for candidate_section in candidate_sections:
            for index, sentence in enumerate(candidate_section.get("sentences") or []):
                if (
                    isinstance(sentence, dict)
                    and sentence.get("sentence_type") == "FACT"
                    and claim_text
                    and _normalized_text(sentence.get("text")) == claim_text
                ):
                    matching_positions.append((candidate_section, index))
        if not matching_positions:
            reported_index = claim.get("sentence_index")
            if (
                isinstance(reported_index, int)
                and section is not None
                and 0 <= reported_index < len(section.get("sentences") or [])
            ):
                matching_positions = [(section, reported_index)]
        if not matching_positions:
            matching_positions = [
                (candidate_section, index)
                for candidate_section in candidate_sections
                for index, sentence in enumerate(
                    candidate_section.get("sentences") or []
                )
                if isinstance(sentence, dict)
                and sentence.get("sentence_type") == "FACT"
            ]
        for matched_section, index in matching_positions:
            matched_section_id = str(matched_section.get("section_id") or "")
            fact_positions_to_clear.add((matched_section_id, index))
            risk_items.append(
                _risk_item(
                    section_id=matched_section_id,
                    section_heading=str(matched_section.get("heading") or ""),
                    paragraph_index=index,
                    risk_type="UNSUPPORTED_FACT",
                    internal_error_type=str(
                        claim.get("mapping_status")
                        or claim.get("component")
                        or "UNSUPPORTED"
                    ),
                )
            )

    for issue in structured_report.get("hard_issues") or []:
        rule = str(issue.get("rule") or "")
        section_id = str(issue.get("section_id") or "")
        section = section_by_id.get(section_id)
        if rule == "analysis_only_fact_present" and section is not None:
            for index, sentence in enumerate(section.get("sentences") or []):
                if (
                    isinstance(sentence, dict)
                    and sentence.get("sentence_type") == "FACT"
                ):
                    fact_positions_to_clear.add((section_id, index))
                    risk_items.append(
                        _risk_item(
                            section_id=section_id,
                            section_heading=str(section.get("heading") or ""),
                            paragraph_index=index,
                            risk_type="OUT_OF_PLAN_FACT",
                            internal_error_type=rule,
                        )
                    )
            continue
        if rule in UNCLASSIFIED_SENTENCE_RULES and section is not None:
            index = issue.get("index")
            if isinstance(index, int):
                fact_positions_to_clear.add((section_id, index))
                risk_items.append(
                    _risk_item(
                        section_id=section_id,
                        section_heading=str(section.get("heading") or ""),
                        paragraph_index=index,
                        risk_type="UNCLASSIFIED_CONTENT",
                        internal_error_type=rule,
                    )
                )
            continue
        if rule not in INVALID_FACT_REFERENCE_RULES:
            continue
        index = issue.get("index")
        if section is None:
            continue
        sentences = section.get("sentences") or []
        matching_indexes = [index] if isinstance(index, int) else []
        if not matching_indexes:
            issue_evidence_id = str(issue.get("evidence_id") or "")
            matching_indexes = [
                sentence_index
                for sentence_index, sentence in enumerate(sentences)
                if isinstance(sentence, dict)
                and sentence.get("sentence_type") == "FACT"
                and str(sentence.get("evidence_id") or "") == issue_evidence_id
            ]
        for matching_index in matching_indexes:
            if not (0 <= matching_index < len(sentences)):
                continue
            fact_positions_to_clear.add((section_id, matching_index))
            risk_items.append(
                _risk_item(
                    section_id=section_id,
                    section_heading=str(section.get("heading") or ""),
                    paragraph_index=matching_index,
                    risk_type="INVALID_EVIDENCE_REFERENCE",
                    internal_error_type=rule,
                )
            )

    for leak in analysis_report.get("leaks") or []:
        section_id = str(leak.get("section_id") or "")
        section = section_by_id.get(section_id)
        candidate_sections = [section] if section is not None else sections
        leak_text = _normalized_text(leak.get("sentence"))
        matching_positions = [
            (candidate_section, index)
            for candidate_section in candidate_sections
            for index, sentence in enumerate(
                candidate_section.get("sentences") or []
            )
            if (
                isinstance(sentence, dict)
                and sentence.get("sentence_type") == "ANALYSIS"
                and leak_text
                and _normalized_text(sentence.get("text")) == leak_text
            )
        ]
        if not matching_positions:
            reported_index = leak.get("paragraph_index")
            if isinstance(reported_index, int) and section is not None:
                matching_positions = [(section, reported_index)]
            else:
                matching_positions = [
                    (candidate_section, index)
                    for candidate_section in candidate_sections
                    for index, sentence in enumerate(
                        candidate_section.get("sentences") or []
                    )
                    if isinstance(sentence, dict)
                    and sentence.get("sentence_type") == "ANALYSIS"
                ]
        for matched_section, index in matching_positions:
            sentences = matched_section.get("sentences") or []
            if not (0 <= index < len(sentences)):
                continue
            matched_section_id = str(matched_section.get("section_id") or "")
            analysis_positions_to_sanitize.add((matched_section_id, index))
            risk_items.append(
                _risk_item(
                    section_id=matched_section_id,
                    section_heading=str(matched_section.get("heading") or ""),
                    paragraph_index=index,
                    risk_type="ANALYSIS_FACT_LEAK",
                    internal_error_type=str(leak.get("rule") or "local_fact_leak"),
                )
            )

    for section in sections:
        section_id = str(section.get("section_id") or "")
        for index, sentence in enumerate(section.get("sentences") or []):
            if not isinstance(sentence, dict):
                continue
            if (section_id, index) in fact_positions_to_clear:
                sentence["text"] = ""
            elif (section_id, index) in analysis_positions_to_sanitize:
                sentence["text"] = _sanitize_analysis_atom(
                    str(sentence.get("text") or ""),
                    section_id=section_id,
                )
            sentence["evidence_id"] = ""

    unique_risks: list[dict[str, Any]] = []
    seen_risks: set[tuple[Any, ...]] = set()
    for item in risk_items:
        key = (
            item["section_id"],
            item["paragraph_index"],
            item["risk_type"],
            item["internal_error_type"],
        )
        if key not in seen_risks:
            seen_risks.add(key)
            unique_risks.append(item)

    return {
        "structured_draft": cleaned,
        "fact_risk_count": int(fact_report.get("unsupported_count") or 0),
        "analysis_risk_count": int(
            analysis_report.get("analysis_fact_leak_count") or 0
        ),
        "fact_gap_count": len(fact_positions_to_clear),
        "risk_items": unique_risks,
    }


def render_quality_test_draft(structured_draft: dict[str, Any]) -> str:
    """Render only parsed title, headings and sanitized sentence text."""

    blocks = [str(structured_draft.get("title") or "")]
    for section in structured_draft.get("sections") or []:
        if not isinstance(section, dict):
            continue
        lines = [str(section.get("heading") or "")]
        lines.extend(
            str(sentence.get("text") or "")
            for sentence in section.get("sentences") or []
            if isinstance(sentence, dict)
        )
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
