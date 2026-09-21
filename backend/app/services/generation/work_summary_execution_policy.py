"""Capacity-aware execution policy for work-summary generation.

This owner translates already computed work-summary capacity metadata into
generation behavior.  Capacity values remain advisory engineering estimates;
this module does not calculate evidence capacity, define quality thresholds, or
change any Evidence / Fact Anchor / FACT / ANALYSIS safety contract.
"""

from __future__ import annotations

from typing import Any, Mapping

from app.services.generation.work_summary_capacity_budget import (
    CAPACITY_MATERIALLY_SPARSE,
    CAPACITY_SLIGHTLY_SPARSE,
    CAPACITY_SUPPORTED,
    ESTIMATE_KIND,
)


POLICY_VERSION = "work-summary-execution-policy-1"
WORK_SUMMARY_DOCUMENT_TYPE = "工作总结"

TARGET_ORIENTED = "TARGET_ORIENTED"
DENSITY_FIRST = "DENSITY_FIRST"
MATERIAL_LIMITED = "MATERIAL_LIMITED"

_LENGTH_MODE_BY_CAPACITY_GAP = {
    CAPACITY_SUPPORTED: TARGET_ORIENTED,
    CAPACITY_SLIGHTLY_SPARSE: DENSITY_FIRST,
    CAPACITY_MATERIALLY_SPARSE: MATERIAL_LIMITED,
}


def is_work_summary_content_plan(content_plan: Mapping[str, Any]) -> bool:
    """Identify the dynamic work-summary plan without changing its schema."""

    return str(content_plan.get("planner_owner") or "") == "work_summary_planner_v1"


def build_work_summary_execution_policy(
    content_plan: Mapping[str, Any],
    *,
    document_type: str = WORK_SUMMARY_DOCUMENT_TYPE,
) -> dict[str, Any]:
    """Build one non-binding execution policy from existing capacity state."""

    if str(document_type or "").strip() != WORK_SUMMARY_DOCUMENT_TYPE:
        raise ValueError("work-summary execution policy only applies to 工作总结")
    if not is_work_summary_content_plan(content_plan):
        raise ValueError("work-summary execution policy requires dynamic work-summary plan")

    capacity_budget = content_plan.get("evidence_capacity_budget") or {}
    capacity_gap = str(
        content_plan.get("capacity_gap")
        or capacity_budget.get("capacity_gap")
        or CAPACITY_SUPPORTED
    )
    length_mode = _LENGTH_MODE_BY_CAPACITY_GAP.get(
        capacity_gap,
        TARGET_ORIENTED,
    )
    requested_target_words = int(
        content_plan.get("requested_target_words")
        or capacity_budget.get("requested_target_words")
        or content_plan.get("target_total")
        or 0
    )
    supported_words = int(
        content_plan.get("evidence_supported_words")
        or capacity_budget.get("evidence_supported_words")
        or requested_target_words
    )
    supported_range = dict(
        capacity_budget.get("evidence_supported_word_range") or {}
    )

    sections: list[dict[str, Any]] = []
    for section in content_plan.get("sections") or []:
        if not isinstance(section, Mapping):
            continue
        sections.append({
            "section_id": str(section.get("section_id") or ""),
            "capacity_status": str(section.get("capacity_status") or ""),
            "business_block": str(section.get("business_block") or ""),
            "section_purpose": str(section.get("section_purpose") or ""),
            "priority": int(section.get("priority") or 0),
            "importance": float(section.get("importance") or 0.0),
            "requested_word_budget": int(
                section.get("requested_word_budget")
                or section.get("recommended_words")
                or 0
            ),
            "recommended_capacity_aware_word_budget": int(
                section.get("recommended_capacity_aware_word_budget") or 0
            ),
            "capacity_aware_budget_is_soft_guidance": True,
            "section_length_is_hard_requirement": False,
        })

    under_target_is_blocking = length_mode == TARGET_ORIENTED
    return {
        "policy_version": POLICY_VERSION,
        "document_type": WORK_SUMMARY_DOCUMENT_TYPE,
        "length_mode": length_mode,
        "requested_target_words": requested_target_words,
        "capacity_gap": capacity_gap,
        "supported_words_estimate": supported_words,
        "supported_word_range_estimate": supported_range,
        "capacity_estimate_kind": str(
            capacity_budget.get("estimate_kind") or ESTIMATE_KIND
        ),
        "capacity_is_engineering_estimate": True,
        "estimate_is_binding": False,
        "under_target_is_blocking": under_target_is_blocking,
        "section_length_is_hard_requirement": False,
        "analysis_padding_allowed": False,
        "length_only_expansion_allowed": False,
        "sections": sections,
    }


def work_summary_length_report(
    *,
    content_plan: Mapping[str, Any],
    actual_length: int,
    under_target_blocked: bool,
) -> dict[str, Any]:
    """Return report-only length semantics for runtime artifacts and UI follow-up."""

    policy = build_work_summary_execution_policy(content_plan)
    under_target = int(actual_length) < int(policy["requested_target_words"])
    if not under_target:
        reason = "NOT_UNDER_REQUESTED_TARGET"
    elif under_target_blocked:
        reason = "UNDER_TARGET_BLOCKED_TARGET_ORIENTED"
    elif policy["length_mode"] == MATERIAL_LIMITED:
        reason = "UNDER_TARGET_ACCEPTED_MATERIAL_LIMITED"
    elif policy["length_mode"] == TARGET_ORIENTED:
        reason = "UNDER_TARGET_WITHIN_TARGET_ORIENTED_GATE"
    else:
        reason = "UNDER_TARGET_ACCEPTED_DENSITY_FIRST"
    return {
        "document_type": WORK_SUMMARY_DOCUMENT_TYPE,
        "requested_target_words": int(policy["requested_target_words"]),
        "actual_length": int(actual_length),
        "length_mode": str(policy["length_mode"]),
        "capacity_gap": str(policy["capacity_gap"]),
        "evidence_supported_words_estimate": int(
            policy["supported_words_estimate"]
        ),
        "estimate_is_binding": False,
        "under_target_blocked": bool(under_target_blocked),
        "under_target_reason": reason,
    }
