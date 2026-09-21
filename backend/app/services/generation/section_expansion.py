"""One-shot expansion for deficient FLOW3-V3.3 sections.

This module owns gap detection, the restricted expansion request, strict
response validation, duplicate rejection, and append-only merging. It never
creates prose locally and never changes an existing sentence.
"""

from __future__ import annotations

import copy
import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from app.services.generation.atomic_evidence import derive_target_length_range
from app.services.generation.new_draft_prompt_v3 import (
    build_introduction_contract,
    build_section_expansion_prompt,
)
from app.services.generation.work_summary_execution_policy import (
    build_work_summary_execution_policy,
    is_work_summary_content_plan,
)
from app.utils.storage import atomic_write_text


_PARAGRAPH_FIELDS = {
    "evidence_id",
    "fact_text",
    "expansion_text",
    "transition_text",
    "summary_text",
}
_LEGACY_WORK_PROGRESS_MINIMUM_POSITIONS = {1, 2}
_INTRODUCTION_PROTOCOL_RE = re.compile(
    r"(?:evidence_id|fact_text|sentence_type|section_id|```|"
    r"\bFACT\b|\bEV(?:IDENCE)?[-_:]|\{\s*[\"']|\[\s*\{)",
    flags=re.IGNORECASE,
)


def _write_json(path: Path, value: Any) -> str:
    atomic_write_text(
        path,
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return str(path.resolve())


def _section_body(section: dict[str, Any]) -> str:
    return "\n".join(
        str(sentence.get("text") or "").strip()
        for sentence in section.get("sentences") or []
        if isinstance(sentence, dict) and str(sentence.get("text") or "").strip()
    )


def _section_length(section: dict[str, Any]) -> int:
    return len(_section_body(section).replace("\n", ""))


def _introduction_text(draft: dict[str, Any]) -> str:
    value = draft.get("introduction")
    return value.strip() if isinstance(value, str) else ""


def _chinese_length(value: str) -> int:
    return len(re.findall(r"[\u3400-\u9fff]", str(value or "")))


def _introduction_duplicate_with_first_section(
    introduction: str,
    draft: dict[str, Any],
) -> bool:
    sections = [
        section
        for section in draft.get("sections") or []
        if isinstance(section, dict)
    ]
    if not sections:
        return False
    left = _normalized_duplicate_text(introduction)
    right = _normalized_duplicate_text(_section_body(sections[0]))
    if len(left) < 40 or len(right) < 40:
        return False
    shorter, longer = sorted((left, right), key=len)
    if shorter in longer and len(shorter) * 100 >= len(longer) * 70:
        return True
    return SequenceMatcher(None, left, right).ratio() >= 0.72


def evaluate_introduction_quality(
    *,
    draft: dict[str, Any],
    introduction_contract: dict[str, Any],
) -> dict[str, Any]:
    """Evaluate the report introduction independently from section protocol."""

    required = bool(introduction_contract.get("required"))
    value = draft.get("introduction")
    present = isinstance(value, str) and bool(value.strip())
    text = value.strip() if isinstance(value, str) else ""
    length = _chinese_length(text)
    accepted = introduction_contract.get("accepted_range") or {}
    minimum = int(accepted.get("min_chars") or 0)
    maximum = int(accepted.get("max_chars") or 0)
    invalid = bool(
        required
        and (
            ("introduction" in draft and not isinstance(value, str))
            or bool(_INTRODUCTION_PROTOCOL_RE.search(text))
        )
    )
    duplicate = bool(
        required
        and present
        and not invalid
        and _introduction_duplicate_with_first_section(text, draft)
    )
    missing = bool(required and not present and not invalid)
    too_short = bool(required and present and not invalid and length < minimum)
    too_long = bool(required and present and not invalid and length > maximum)
    if not required:
        status = "NOT_REQUIRED"
    elif invalid:
        status = "INTRODUCTION_INVALID"
    elif missing:
        status = "INTRODUCTION_MISSING"
    elif duplicate:
        status = "INTRODUCTION_DUPLICATED"
    elif too_short:
        status = "INTRODUCTION_TOO_SHORT"
    elif too_long:
        status = "INTRODUCTION_TOO_LONG"
    else:
        status = "PASS"
    return {
        "required": required,
        "introduction_present": present,
        "length": length,
        "target_length": int(introduction_contract.get("target_length") or 0),
        "accepted_range": {
            "min_chars": minimum,
            "max_chars": maximum,
        },
        "missing": missing,
        "too_short": too_short,
        "too_long": too_long,
        "duplicate_with_first_section": duplicate,
        "invalid": invalid,
        "status": status,
    }


def _introduction_requires_expansion(report: dict[str, Any]) -> bool:
    return str(report.get("status") or "") in {
        "INTRODUCTION_MISSING",
        "INTRODUCTION_TOO_SHORT",
        "INTRODUCTION_INVALID",
        "INTRODUCTION_DUPLICATED",
    }


def _introduction_artifact_report(
    *,
    initial: dict[str, Any],
    final: dict[str, Any],
    expansion_triggered: bool,
    expansion_model_call_count: int,
    safety_issue_count: int = 0,
) -> dict[str, Any]:
    final_status = str(final.get("status") or "")
    if expansion_model_call_count and final_status not in {"PASS", "NOT_REQUIRED"}:
        final_status = f"{final_status}_AFTER_EXPANSION"
    return {
        "required": bool(initial.get("required")),
        "introduction_present": bool(final.get("introduction_present")),
        "initial_length": int(initial.get("length") or 0),
        "target_length": int(initial.get("target_length") or 0),
        "accepted_range": dict(initial.get("accepted_range") or {}),
        "missing": bool(initial.get("missing")),
        "too_short": bool(initial.get("too_short")),
        "too_long": bool(initial.get("too_long")),
        "duplicate_with_first_section": bool(
            initial.get("duplicate_with_first_section")
        ),
        "invalid": bool(initial.get("invalid")),
        "initial_status": str(initial.get("status") or ""),
        "expansion_triggered": bool(expansion_triggered),
        "expansion_model_call_count": int(expansion_model_call_count),
        "final_length": int(final.get("length") or 0),
        "safety_issue_count": int(safety_issue_count),
        "final_status": final_status,
    }


def _persist_introduction_artifacts(
    root: Path,
    *,
    initial_text: str,
    final_text: str,
    report: dict[str, Any],
) -> dict[str, str]:
    initial_path = root / "initial_introduction.txt"
    final_path = root / "final_introduction.txt"
    atomic_write_text(initial_path, initial_text, encoding="utf-8")
    atomic_write_text(final_path, final_text, encoding="utf-8")
    report_path = _write_json(root / "introduction_quality_report.json", report)
    return {
        "initial_introduction": str(initial_path.resolve()),
        "final_introduction": str(final_path.resolve()),
        "introduction_quality_report": report_path,
    }


def _paragraph_count(section: dict[str, Any]) -> int:
    return sum(
        1
        for sentence in section.get("sentences") or []
        if isinstance(sentence, dict) and str(sentence.get("text") or "").strip()
    )


def render_draft_for_length(
    draft: dict[str, Any],
    content_plan: dict[str, Any],
) -> str:
    """Render the same title/heading/body shape used by the FLOW3 renderer."""
    by_id = {
        str(section.get("section_id") or ""): section
        for section in draft.get("sections") or []
        if isinstance(section, dict)
    }
    blocks = [str(draft.get("title") or "")]
    introduction = _introduction_text(draft)
    if introduction:
        blocks.append(introduction)
    for planned in content_plan.get("sections") or []:
        section = by_id.get(str(planned.get("section_id") or ""))
        if not section:
            continue
        lines = [str(section.get("heading") or "")]
        body = _section_body(section)
        if body:
            lines.append(body)
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def evaluate_section_gaps(
    *,
    draft: dict[str, Any],
    content_plan: dict[str, Any],
    target_words: int,
    introduction_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return deterministic total-length and per-section expansion gaps."""
    work_summary = is_work_summary_content_plan(content_plan)
    execution_policy = (
        build_work_summary_execution_policy(content_plan)
        if work_summary
        else None
    )
    target = max(1, int(content_plan.get("target_total") or target_words))
    contract = introduction_contract or build_introduction_contract(
        "",
        target,
        list(content_plan.get("sections") or []),
    )
    introduction_report = evaluate_introduction_quality(
        draft=draft,
        introduction_contract=contract,
    )
    target_range = derive_target_length_range(target)
    initial_total_length = len(render_draft_for_length(draft, content_plan))
    draft_by_id = {
        str(section.get("section_id") or ""): section
        for section in draft.get("sections") or []
        if isinstance(section, dict)
    }
    initial_section_lengths: dict[str, int] = {}
    section_deficits: dict[str, int] = {}
    deficient_section_details: list[dict[str, Any]] = []

    body_budgets = list(contract.get("section_body_budgets") or [])
    for position, planned in enumerate(content_plan.get("sections") or []):
        section_id = str(planned.get("section_id") or "")
        section = draft_by_id.get(section_id, {})
        actual = _section_length(section)
        budget = max(
            0,
            int(
                body_budgets[position]
                if position < len(body_budgets)
                else planned.get("recommended_words") or 0
            ),
        )
        deficit = max(0, budget - actual)
        paragraph_count = _paragraph_count(section)
        reasons: list[str] = []
        if work_summary:
            # Required FACT coverage and empty fact sections belong to the
            # existing structured / Fact Anchor validators.  ANALYSIS-only
            # expansion is retained only for an actually empty analysis-only
            # section, never for a word-budget deficit.
            if (
                (not section or actual == 0)
                and str(planned.get("evidence_policy") or "") == "analysis_only"
            ):
                reasons.append("STRUCTURAL_ANALYSIS_SECTION_EMPTY")
        else:
            if not section or actual == 0:
                reasons.append("SECTION_EMPTY")
            if paragraph_count == 1 and budget and actual < (budget * 80 + 99) // 100:
                reasons.append("SINGLE_THIN_PARAGRAPH")
            if budget and actual * 100 < budget * 80:
                reasons.append("BELOW_80_PERCENT_SECTION_BUDGET")
            if (
                position in _LEGACY_WORK_PROGRESS_MINIMUM_POSITIONS
                and str(planned.get("section_role") or "") == "work_progress"
                and paragraph_count < 2
            ):
                reasons.append("WORK_PROGRESS_MINIMUM_PARAGRAPHS_NOT_MET")
            if initial_total_length < target_range["min_words"] and deficit > 0:
                reasons.append("TOTAL_UNDER_MIN_WITH_SECTION_DEFICIT")

        initial_section_lengths[section_id] = actual
        if reasons:
            section_deficits[section_id] = deficit
            deficient_section_details.append(
                {
                    "section_id": section_id,
                    "heading": str(section.get("heading") or ""),
                    "target_budget": budget,
                    "actual_length": actual,
                    "section_deficit": deficit,
                    "paragraph_count": paragraph_count,
                    "reasons": list(dict.fromkeys(reasons)),
                }
            )

    deficient_sections = [
        item["section_id"] for item in deficient_section_details
    ]
    introduction_expansion_required = _introduction_requires_expansion(
        introduction_report
    )
    expansion_triggered = introduction_expansion_required or (
        bool(deficient_section_details)
        and (
            work_summary
            or initial_total_length <= target_range["max_words"]
        )
    )
    return {
        "expansion_triggered": expansion_triggered,
        "initial_total_length": initial_total_length,
        "initial_section_lengths": initial_section_lengths,
        "target_total": target,
        "target_range": target_range,
        "deficient_sections": deficient_sections,
        "deficient_section_details": deficient_section_details,
        "section_deficits": section_deficits,
        "body_target": int(contract.get("body_target") or target),
        "section_body_budgets": body_budgets,
        "introduction_quality": introduction_report,
        "introduction_expansion_required": introduction_expansion_required,
        "work_summary_execution_policy": execution_policy,
        "expansion_status": (
            "EXPANSION_REQUIRED"
            if expansion_triggered
            else (
                "NOT_APPLICABLE_OVER_LENGTH"
                if deficient_sections
                else "NOT_NEEDED"
            )
        ),
    }


def _section_scoped_items(values: Any, section_id: str) -> list[dict[str, Any]]:
    return [
        copy.deepcopy(item)
        for item in values or []
        if isinstance(item, dict) and str(item.get("section_id") or "") == section_id
    ]


def build_section_expansion_request(
    *,
    task: Any,
    draft: dict[str, Any],
    content_plan: dict[str, Any],
    fact_anchor_pack: dict[str, Any],
    gap_report: dict[str, Any],
) -> dict[str, Any]:
    """Build the allowlisted payload for all deficient sections in one call."""
    work_summary = is_work_summary_content_plan(content_plan)
    execution_policy = (
        build_work_summary_execution_policy(content_plan)
        if work_summary
        else None
    )
    draft_by_id = {
        str(section.get("section_id") or ""): section
        for section in draft.get("sections") or []
        if isinstance(section, dict)
    }
    plan_by_id = {
        str(section.get("section_id") or ""): section
        for section in content_plan.get("sections") or []
        if isinstance(section, dict)
    }
    sections: list[dict[str, Any]] = []
    for deficient in gap_report.get("deficient_section_details") or []:
        section_id = str(deficient.get("section_id") or "")
        draft_section = draft_by_id.get(section_id, {})
        planned = plan_by_id.get(section_id, {})
        section_request = {
                "section_id": section_id,
                "section_title": str(draft_section.get("heading") or ""),
                "section_actual_length": int(
                    deficient.get("actual_length") or 0
                ),
                "existing_section_body": _section_body(draft_section),
                "fact_anchors": _section_scoped_items(
                    fact_anchor_pack.get("fact_anchors"),
                    section_id,
                ),
                "analysis_support": _section_scoped_items(
                    fact_anchor_pack.get("analysis_support_pack"),
                    section_id,
                ),
                "reference_structure_requirements": copy.deepcopy(
                    planned.get("reference_structure_guidance") or {}
                ),
                "writing_subsections": [
                    {
                        "subsection_id": str(
                            subsection.get("subsection_id") or ""
                        ),
                        "lead": str(subsection.get("lead") or ""),
                        "order": int(subsection.get("order") or 0),
                    }
                    for subsection in (
                        draft_section.get("subsections") or []
                    )
                    if isinstance(subsection, dict)
                ],
            }
        if work_summary:
            section_request.update({
                "repair_reason": "STRUCTURAL_ANALYSIS_SECTION_EMPTY",
                "capacity_status": str(planned.get("capacity_status") or ""),
                "capacity_gap": str((execution_policy or {}).get("capacity_gap") or ""),
                "length_mode": str((execution_policy or {}).get("length_mode") or ""),
                "section_requested_word_budget": int(
                    planned.get("requested_word_budget")
                    or planned.get("recommended_words")
                    or 0
                ),
                "recommended_capacity_aware_word_budget": int(
                    planned.get("recommended_capacity_aware_word_budget") or 0
                ),
                "capacity_estimate_is_binding": False,
                "length_deficit_must_be_filled": False,
            })
        else:
            section_request.update({
                "section_target_budget": int(
                    deficient.get("target_budget") or 0
                ),
                "section_deficit": int(
                    deficient.get("section_deficit") or 0
                ),
            })
        sections.append(section_request)
    introduction_quality = gap_report.get("introduction_quality") or {}
    request = {
        "document_title": str(draft.get("title") or getattr(task, "title", "")),
        "task_context": {
            "document_type": str(getattr(task, "document_type", "") or ""),
            "level": str(getattr(task, "level", "") or ""),
            "org_type": str(getattr(task, "org_type", "") or ""),
            "writing_purpose": str(getattr(task, "writing_purpose", "") or ""),
        },
        "sections_to_expand": sections,
    }
    if work_summary:
        request["work_summary_execution_policy"] = execution_policy
        request["capacity_aware_length_context"] = {
            "requested_target_words": int(
                (execution_policy or {}).get("requested_target_words") or 0
            ),
            "length_mode": str(
                (execution_policy or {}).get("length_mode") or ""
            ),
            "capacity_gap": str(
                (execution_policy or {}).get("capacity_gap") or ""
            ),
            "estimate_is_binding": False,
            "length_deficit_must_be_filled": False,
        }
    else:
        request["length_contract"] = {
            "target_total": int(gap_report.get("target_total") or 0),
            "min_words": int(
                (gap_report.get("target_range") or {}).get("min_words") or 0
            ),
            "max_words": int(
                (gap_report.get("target_range") or {}).get("max_words") or 0
            ),
        }
    if gap_report.get("introduction_expansion_required"):
        request["introduction_deficit"] = {
            "required": True,
            "target_length": int(
                introduction_quality.get("target_length") or 0
            ),
            "accepted_range": dict(
                introduction_quality.get("accepted_range") or {}
            ),
            "current_text": _introduction_text(draft),
            "current_status": str(introduction_quality.get("status") or ""),
        }
    return request


def _normalized_duplicate_text(value: str) -> str:
    return re.sub(r"[\W_]+", "", value or "", flags=re.UNICODE)


def _is_duplicate(candidate: str, existing: list[str]) -> bool:
    normalized = _normalized_duplicate_text(candidate)
    if not normalized:
        return True
    for prior in existing:
        prior_normalized = _normalized_duplicate_text(prior)
        if not prior_normalized:
            continue
        if normalized == prior_normalized:
            return True
        if SequenceMatcher(None, normalized, prior_normalized).ratio() >= 0.88:
            return True
    return False


def _analysis_paragraph_text(paragraph: dict[str, Any]) -> str:
    return "".join(
        str(paragraph.get(field) or "").strip()
        for field in ("expansion_text", "transition_text", "summary_text")
    )


def parse_and_merge_expansion(
    *,
    raw_response: str,
    draft: dict[str, Any],
    requested_section_ids: list[str],
    content_plan: dict[str, Any],
    introduction_contract: dict[str, Any] | None = None,
    introduction_requested: bool = False,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Validate the expansion protocol and append accepted ANALYSIS only."""
    requested = set(requested_section_ids)
    work_summary = is_work_summary_content_plan(content_plan)
    parsed: dict[str, Any] = {}
    invalid_section_count = 0
    invalid_evidence_id_count = 0
    duplicate_rejection_count = 0
    protocol_issues: list[dict[str, Any]] = []
    introduction_protocol_issues: list[dict[str, Any]] = []
    introduction_candidate = ""
    accepted_by_section: dict[str, list[dict[str, str]]] = {
        section_id: [] for section_id in requested_section_ids
    }
    allowed_subsection_ids_by_section = {
        str(section.get("section_id") or ""): {
            str(subsection.get("subsection_id") or "")
            for subsection in section.get("subsections") or []
            if isinstance(subsection, dict)
        }
        for section in draft.get("sections") or []
        if isinstance(section, dict)
    }

    try:
        value = str(raw_response or "").strip()
        fenced = re.fullmatch(
            r"```(?:json)?\s*\n([\s\S]*?)\n```",
            value,
            flags=re.IGNORECASE,
        )
        if fenced:
            value = fenced.group(1).strip()
        parsed_value = json.loads(value)
        if not isinstance(parsed_value, dict):
            raise ValueError("expansion response root must be one JSON object")
        parsed = parsed_value
    except (json.JSONDecodeError, ValueError) as exc:
        protocol_issues.append(
            {
                "rule": "json_parse",
                "error_type": type(exc).__name__,
                "message": str(exc),
            }
        )

    expected_root_fields = {"expanded_sections"}
    if introduction_requested:
        expected_root_fields.add("introduction")
    if parsed and set(parsed) != expected_root_fields:
        actual_root_fields = set(parsed)
        missing_root_fields = expected_root_fields - actual_root_fields
        extra_root_fields = actual_root_fields - expected_root_fields
        if (
            introduction_requested
            and missing_root_fields == {"introduction"}
            and not extra_root_fields
        ):
            introduction_protocol_issues.append(
                {"rule": "introduction_missing"}
            )
        else:
            protocol_issues.append(
                {"rule": "root_fields", "actual": sorted(parsed)}
            )
    expanded_sections = parsed.get("expanded_sections") if parsed else None
    if parsed and not isinstance(expanded_sections, list):
        protocol_issues.append({"rule": "expanded_sections_array"})
    if parsed and introduction_requested:
        raw_introduction = parsed.get("introduction")
        if not isinstance(raw_introduction, str):
            introduction_protocol_issues.append(
                {"rule": "introduction_type"}
            )
        elif _INTRODUCTION_PROTOCOL_RE.search(raw_introduction):
            introduction_protocol_issues.append(
                {"rule": "introduction_protocol_residue"}
            )
        else:
            introduction_candidate = raw_introduction.strip()

    if not protocol_issues:
        seen_section_ids: list[str] = []
        for section in expanded_sections:
            if not isinstance(section, dict):
                protocol_issues.append({"rule": "expanded_section_object"})
                continue
            body_field = "sentences" if work_summary else "paragraphs"
            if set(section) != {"section_id", body_field}:
                protocol_issues.append(
                    {
                        "rule": "expanded_section_fields",
                        "actual": sorted(section),
                    }
                )
                continue
            section_id = str(section.get("section_id") or "")
            if section_id not in requested:
                invalid_section_count += 1
                continue
            if section_id in seen_section_ids:
                protocol_issues.append(
                    {"rule": "duplicate_section_id", "section_id": section_id}
                )
                continue
            seen_section_ids.append(section_id)
            output_items = section.get(body_field)
            if not isinstance(output_items, list):
                protocol_issues.append(
                    {"rule": f"{body_field}_array", "section_id": section_id}
                )
                continue
            allowed_subsection_ids = (
                allowed_subsection_ids_by_section.get(section_id) or set()
            )
            if work_summary:
                for index, sentence in enumerate(output_items):
                    if (
                        not isinstance(sentence, dict)
                        or set(sentence) != {"sentence_type", "evidence_id", "text"}
                    ):
                        protocol_issues.append({
                            "rule": "sentence_fields",
                            "section_id": section_id,
                            "sentence_index": index,
                        })
                        continue
                    if not all(
                        isinstance(sentence.get(field), str)
                        for field in ("sentence_type", "evidence_id", "text")
                    ):
                        protocol_issues.append({
                            "rule": "sentence_field_types",
                            "section_id": section_id,
                            "sentence_index": index,
                        })
                        continue
                    if (
                        str(sentence.get("sentence_type") or "") != "ANALYSIS"
                        or str(sentence.get("evidence_id") or "").strip()
                    ):
                        invalid_evidence_id_count += 1
                        continue
                    text = str(sentence.get("text") or "").strip()
                    if not text:
                        protocol_issues.append({
                            "rule": "sentence_text_blank",
                            "section_id": section_id,
                            "sentence_index": index,
                        })
                        continue
                    accepted_by_section[section_id].append({
                        "text": text,
                        "subsection_id": "",
                    })
            else:
                expected_paragraph_fields = set(_PARAGRAPH_FIELDS)
                if allowed_subsection_ids:
                    expected_paragraph_fields.add("subsection_id")
                for index, paragraph in enumerate(output_items):
                    if (
                        not isinstance(paragraph, dict)
                        or set(paragraph) != expected_paragraph_fields
                    ):
                        protocol_issues.append(
                            {
                                "rule": "paragraph_fields",
                                "section_id": section_id,
                                "paragraph_index": index,
                            }
                        )
                        continue
                    subsection_id = str(
                        paragraph.get("subsection_id") or ""
                    )
                    if (
                        allowed_subsection_ids
                        and subsection_id not in allowed_subsection_ids
                    ):
                        protocol_issues.append(
                            {
                                "rule": "subsection_id",
                                "section_id": section_id,
                                "paragraph_index": index,
                                "subsection_id": subsection_id,
                            }
                        )
                        continue
                    if not all(
                        isinstance(paragraph.get(field), str)
                        for field in expected_paragraph_fields
                    ):
                        protocol_issues.append(
                            {
                                "rule": "paragraph_field_types",
                                "section_id": section_id,
                                "paragraph_index": index,
                            }
                        )
                        continue
                    if (
                        str(paragraph.get("evidence_id") or "").strip()
                        or str(paragraph.get("fact_text") or "").strip()
                    ):
                        invalid_evidence_id_count += 1
                        continue
                    text = _analysis_paragraph_text(paragraph)
                    if not text:
                        protocol_issues.append(
                            {
                                "rule": "paragraph_text_blank",
                                "section_id": section_id,
                                "paragraph_index": index,
                            }
                        )
                        continue
                    accepted_by_section[section_id].append(
                        {
                            "text": text,
                            "subsection_id": subsection_id,
                        }
                    )
        missing_section_ids = sorted(requested - set(seen_section_ids))
        if missing_section_ids:
            protocol_issues.append(
                {
                    "rule": "missing_requested_sections",
                    "section_ids": missing_section_ids,
                }
            )

    if protocol_issues:
        return copy.deepcopy(draft), parsed, {
            "duplicate_rejection_count": 0,
            "invalid_section_count": invalid_section_count,
            "invalid_evidence_id_count": invalid_evidence_id_count,
            "protocol_issues": protocol_issues,
            "expanded_section_lengths": {},
            "expansion_status": "EXPANSION_INVALID",
            "introduction_accepted": False,
            "introduction_protocol_issues": introduction_protocol_issues,
        }

    merged = copy.deepcopy(draft)
    merged_by_id = {
        str(section.get("section_id") or ""): section
        for section in merged.get("sections") or []
        if isinstance(section, dict)
    }
    expanded_section_lengths: dict[str, int] = {}
    for section_id in requested_section_ids:
        target_section = merged_by_id.get(section_id)
        if not target_section:
            invalid_section_count += 1
            continue
        existing_texts = [
            str(item.get("text") or "")
            for item in target_section.get("sentences") or []
            if isinstance(item, dict)
        ]
        accepted_length = 0
        for accepted in accepted_by_section.get(section_id, []):
            text = accepted["text"]
            if _is_duplicate(text, existing_texts):
                duplicate_rejection_count += 1
                continue
            sentence = {
                "sentence_type": "ANALYSIS",
                "evidence_id": "",
                "text": text,
            }
            if accepted["subsection_id"]:
                sentence["subsection_id"] = accepted[
                    "subsection_id"
                ]
            target_section.setdefault("sentences", []).append(sentence)
            existing_texts.append(text)
            accepted_length += len(text)
        expanded_section_lengths[section_id] = accepted_length

    introduction_accepted = False
    introduction_candidate_status = ""
    if introduction_requested and not introduction_protocol_issues:
        candidate_draft = copy.deepcopy(merged)
        candidate_draft["introduction"] = introduction_candidate
        candidate_report = evaluate_introduction_quality(
            draft=candidate_draft,
            introduction_contract=introduction_contract or {},
        )
        introduction_candidate_status = str(candidate_report.get("status") or "")
        if introduction_candidate_status == "PASS":
            merged["introduction"] = introduction_candidate
            introduction_accepted = True
        else:
            introduction_protocol_issues.append(
                {
                    "rule": "introduction_quality",
                    "status": introduction_candidate_status,
                }
            )

    merged_total = len(render_draft_for_length(merged, content_plan))
    target_range = derive_target_length_range(
        int(content_plan.get("target_total") or 1)
    )
    if invalid_section_count or invalid_evidence_id_count:
        expansion_status = (
            "EXPANSION_PARTIAL"
            if any(expanded_section_lengths.values())
            else "EXPANSION_INVALID"
        )
    elif introduction_requested and not introduction_accepted:
        expansion_status = "INTRODUCTION_INSUFFICIENT_AFTER_EXPANSION"
    elif not work_summary and merged_total < target_range["min_words"]:
        expansion_status = "LENGTH_INSUFFICIENT_AFTER_EXPANSION"
    elif merged_total > target_range["max_words"]:
        expansion_status = "LENGTH_EXCEEDED_AFTER_EXPANSION"
    else:
        expansion_status = (
            "EXPANDED_STRUCTURAL_WORK_SUMMARY"
            if work_summary
            else "EXPANDED"
        )
    return merged, parsed, {
        "duplicate_rejection_count": duplicate_rejection_count,
        "invalid_section_count": invalid_section_count,
        "invalid_evidence_id_count": invalid_evidence_id_count,
        "protocol_issues": [],
        "expanded_section_lengths": expanded_section_lengths,
        "expansion_status": expansion_status,
        "introduction_accepted": introduction_accepted,
        "introduction_candidate_status": introduction_candidate_status,
        "introduction_protocol_issues": introduction_protocol_issues,
    }


def execute_one_shot_section_expansion(
    *,
    task: Any,
    draft: dict[str, Any],
    content_plan: dict[str, Any],
    fact_anchor_pack: dict[str, Any],
    provider: Any,
    artifact_root: Path,
    temperature: float,
    introduction_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Perform zero or one expansion model call and return the merged draft."""
    root = Path(artifact_root)
    introduction_contract = introduction_contract or build_introduction_contract(
        str(getattr(task, "document_type", "") or ""),
        int(content_plan.get("target_total") or getattr(task, "target_words", 0) or 0),
        list(content_plan.get("sections") or []),
    )
    gap_report = evaluate_section_gaps(
        draft=draft,
        content_plan=content_plan,
        target_words=int(getattr(task, "target_words", 0) or 0),
        introduction_contract=introduction_contract,
    )
    initial_lengths = dict(gap_report["initial_section_lengths"])
    initial_introduction_quality = dict(gap_report["introduction_quality"])
    initial_introduction_text = _introduction_text(draft)
    if not gap_report["expansion_triggered"]:
        final_introduction_quality = evaluate_introduction_quality(
            draft=draft,
            introduction_contract=introduction_contract,
        )
        introduction_report = _introduction_artifact_report(
            initial=initial_introduction_quality,
            final=final_introduction_quality,
            expansion_triggered=False,
            expansion_model_call_count=0,
        )
        introduction_paths = (
            _persist_introduction_artifacts(
                root,
                initial_text=initial_introduction_text,
                final_text=_introduction_text(draft),
                report=introduction_report,
            )
            if introduction_contract.get("required")
            else {}
        )
        return {
            "structured_draft": copy.deepcopy(draft),
            "model_call_count": 0,
            "report": {
                **gap_report,
                "introduction_quality_report": introduction_report,
                "expansion_model_call_count": 0,
                "expanded_section_lengths": {},
                "merged_total_length": gap_report["initial_total_length"],
                "merged_section_lengths": initial_lengths,
                "duplicate_rejection_count": 0,
                "invalid_section_count": 0,
                "invalid_evidence_id_count": 0,
            },
            "paths": introduction_paths,
        }

    expansion_request = build_section_expansion_request(
        task=task,
        draft=draft,
        content_plan=content_plan,
        fact_anchor_pack=fact_anchor_pack,
        gap_report=gap_report,
    )
    request_path = _write_json(
        root / "section_expansion_request.json",
        expansion_request,
    )
    prompt = build_section_expansion_prompt(expansion_request)
    raw_response = provider.generate_text(
        prompt=prompt,
        system_prompt="只输出严格符合协议的一个JSON对象。",
        temperature=temperature,
    )
    raw_text = str(raw_response)
    raw_path = root / "raw_section_expansion_response.txt"
    atomic_write_text(raw_path, raw_text, encoding="utf-8")
    requested_ids = [
        str(section.get("section_id") or "")
        for section in gap_report["deficient_section_details"]
    ]
    introduction_requested = bool(
        gap_report.get("introduction_expansion_required")
    )
    merged, parsed, merge_report = parse_and_merge_expansion(
        raw_response=raw_text,
        draft=draft,
        requested_section_ids=requested_ids,
        content_plan=content_plan,
        introduction_contract=introduction_contract,
        introduction_requested=introduction_requested,
    )
    parsed_path = _write_json(
        root / "parsed_section_expansion.json",
        parsed,
    )
    merged_section_lengths = {
        str(section.get("section_id") or ""): _section_length(section)
        for section in merged.get("sections") or []
        if isinstance(section, dict)
    }
    final_introduction_quality = evaluate_introduction_quality(
        draft=merged,
        introduction_contract=introduction_contract,
    )
    introduction_report = _introduction_artifact_report(
        initial=initial_introduction_quality,
        final=final_introduction_quality,
        expansion_triggered=True,
        expansion_model_call_count=1,
    )
    introduction_paths = (
        _persist_introduction_artifacts(
            root,
            initial_text=initial_introduction_text,
            final_text=_introduction_text(merged),
            report=introduction_report,
        )
        if introduction_contract.get("required")
        else {}
    )
    report = {
        **gap_report,
        "introduction_quality_report": introduction_report,
        "expansion_model_call_count": 1,
        "expanded_section_lengths": merge_report["expanded_section_lengths"],
        "merged_total_length": len(render_draft_for_length(merged, content_plan)),
        "merged_section_lengths": merged_section_lengths,
        "duplicate_rejection_count": merge_report["duplicate_rejection_count"],
        "invalid_section_count": merge_report["invalid_section_count"],
        "invalid_evidence_id_count": merge_report[
            "invalid_evidence_id_count"
        ],
        "protocol_issues": merge_report["protocol_issues"],
        "introduction_protocol_issues": merge_report[
            "introduction_protocol_issues"
        ],
        "expansion_status": merge_report["expansion_status"],
    }
    report_path = _write_json(
        root / "section_expansion_report.json",
        report,
    )
    return {
        "structured_draft": merged,
        "model_call_count": 1,
        "report": report,
        "paths": {
            **introduction_paths,
            "section_expansion_request": request_path,
            "raw_section_expansion_response": str(raw_path.resolve()),
            "parsed_section_expansion": parsed_path,
            "section_expansion_report": report_path,
        },
    }
