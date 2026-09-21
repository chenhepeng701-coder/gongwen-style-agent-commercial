"""Evidence-capacity estimates for dynamic work-summary content plans.

The estimates are deliberately relative engineering guidance.  They are not a
quality-proven threshold and contain no evidence-to-word conversion formula.
"""

from __future__ import annotations

import copy
from statistics import median
from typing import Any, Mapping, Sequence


CAPACITY_SCHEMA_VERSION = "work-summary-evidence-capacity-1"
ESTIMATE_KIND = "ENGINEERING_ESTIMATE"
NO_EVIDENCE_WORD_FORMULA = True

FACT_RICH = "FACT_RICH"
FACT_ADEQUATE = "FACT_ADEQUATE"
FACT_SPARSE = "FACT_SPARSE"
FACT_INSUFFICIENT = "FACT_INSUFFICIENT"
NOT_FACT_DRIVEN = "NOT_FACT_DRIVEN"

CAPACITY_SUPPORTED = "SUPPORTED"
CAPACITY_SLIGHTLY_SPARSE = "SLIGHTLY_SPARSE"
CAPACITY_MATERIALLY_SPARSE = "MATERIALLY_SPARSE"

_STATUS_FACTOR = {
    FACT_RICH: 1.0,
    FACT_ADEQUATE: 0.85,
    FACT_SPARSE: 0.60,
    FACT_INSUFFICIENT: 0.35,
    NOT_FACT_DRIVEN: 0.45,
}
_CHAIN_VALUE = {"PARTIAL": 1, "MULTI_STEP": 2, "CLOSED_LOOP": 3}


def _allocate(total: int, weights: Sequence[float]) -> list[int]:
    if not weights:
        return []
    normalized = [max(0.01, float(value)) for value in weights]
    raw = [total * value / sum(normalized) for value in normalized]
    allocations = [int(value) for value in raw]
    residue = total - sum(allocations)
    order = sorted(
        range(len(raw)),
        key=lambda index: (raw[index] - allocations[index], -index),
        reverse=True,
    )
    for index in order[:residue]:
        allocations[index] += 1
    return allocations


def _span_id(item: Mapping[str, Any]) -> str:
    return str(item.get("source_span_sha256") or f"EVIDENCE:{item.get('evidence_id')}")


def _section_maps(work_chain_pack: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {
        str(row.get("section_id") or ""): row
        for row in work_chain_pack.get("section_evidence_map") or []
    }


def _section_fact_minimum(section: Mapping[str, Any]) -> int:
    if str(section.get("evidence_policy") or "fact_required") != "fact_required":
        return 0
    return max(1, int(section.get("minimum_fact_anchors") or 2))


def build_evidence_capacity_budget(
    content_plan: Mapping[str, Any],
    evidence: Sequence[Mapping[str, Any]],
    work_chain_pack: Mapping[str, Any],
    *,
    requested_target_words: int,
    fact_anchor_pack: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute section/total relative capacity without changing requested words."""

    by_id = {
        str(item.get("evidence_id") or ""): item
        for item in evidence if item.get("evidence_id")
    }
    section_maps = _section_maps(work_chain_pack)
    chains = list(work_chain_pack.get("chains") or [])
    anchors = list((fact_anchor_pack or {}).get("fact_anchors") or [])
    analysis_pack = list((fact_anchor_pack or {}).get("analysis_support_pack") or [])
    sections = list(content_plan.get("sections") or [])

    fact_span_counts: list[int] = []
    for section in sections:
        if str(section.get("evidence_policy") or "fact_required") != "fact_required":
            continue
        row = section_maps.get(str(section.get("section_id") or ""), {})
        fact_ids = [str(value) for value in row.get("fact_anchorable_evidence_ids") or []]
        fact_span_counts.append(len({_span_id(by_id[value]) for value in fact_ids if value in by_id}))
    relative_median = float(median(fact_span_counts)) if fact_span_counts else 0.0

    section_capacity: list[dict[str, Any]] = []
    for section in sections:
        section_id = str(section.get("section_id") or "")
        evidence_policy = str(section.get("evidence_policy") or "fact_required")
        row = section_maps.get(section_id, {})
        available_fact_ids = [
            str(value) for value in row.get("fact_anchorable_evidence_ids") or []
            if str(value) in by_id
        ]
        analysis_ids = [
            str(value) for value in row.get("analysis_support_only_evidence_ids") or []
            if str(value) in by_id
        ]
        distinct_spans = len({_span_id(by_id[value]) for value in available_fact_ids})
        section_chains = [chain for chain in chains if str(chain.get("section_id") or "") == section_id]
        chain_member_ids = {
            str(value)
            for chain in section_chains
            for value in chain.get("member_evidence_ids") or []
        }
        completeness_value = max(
            (_CHAIN_VALUE.get(str(chain.get("chain_completeness") or ""), 0) for chain in section_chains),
            default=0,
        )
        minimum_facts = _section_fact_minimum(section)
        if evidence_policy != "fact_required":
            status = NOT_FACT_DRIVEN
        elif distinct_spans < minimum_facts:
            status = FACT_INSUFFICIENT
        elif distinct_spans == minimum_facts and not section_chains:
            # Meeting only the existing safety minimum without organizational
            # continuity is writable but sparse; this is not an absolute
            # commercial-quality threshold.
            status = FACT_SPARSE
        elif section_chains and completeness_value >= 2 and distinct_spans >= relative_median:
            status = FACT_RICH
        elif section_chains or distinct_spans >= relative_median:
            status = FACT_ADEQUATE
        else:
            status = FACT_SPARSE

        confirmed_anchors = [item for item in anchors if str(item.get("section_id") or "") == section_id]
        confirmed_analysis = [item for item in analysis_pack if str(item.get("section_id") or "") == section_id]
        requested_budget = max(0, int(section.get("recommended_words") or 0))
        factor = _STATUS_FACTOR[status]
        importance = max(0.1, float(section.get("importance") or section.get("relative_weight") or 1.0))
        section_capacity.append({
            "section_id": section_id,
            "section_purpose": str(section.get("section_purpose") or ""),
            "business_block": str(section.get("business_block") or ""),
            "priority": int(section.get("priority") or len(section_capacity) + 1),
            "importance": importance,
            "minimum_fact_anchors": minimum_facts,
            "available_fact_anchorable": len(available_fact_ids),
            "distinct_fact_source_spans": distinct_spans,
            "available_work_chains": len(section_chains),
            "chain_member_count": len(chain_member_ids),
            "supporting_evidence_count": max(0, len(available_fact_ids) - len(chain_member_ids)),
            "analysis_support_only_count": len(analysis_ids),
            "confirmed_fact_anchor_count": len(confirmed_anchors),
            "confirmed_analysis_support_count": len(confirmed_analysis),
            "capacity_status": status,
            "capacity_factor": factor,
            "requested_word_budget": requested_budget,
            "recommended_relative_budget": round(importance * factor, 6),
            "work_chain_ids": [str(chain.get("chain_id") or "") for chain in section_chains],
        })

    requested_total = max(1, int(requested_target_words))
    estimated_supported_total = max(
        1,
        min(
            requested_total,
            round(sum(
                int(row["requested_word_budget"]) * float(row["capacity_factor"])
                for row in section_capacity
            )),
        ),
    )
    fact_rows = [
        row for row in section_capacity
        if row["capacity_status"] != NOT_FACT_DRIVEN
    ]
    raw_fact_ids = {
        str(value)
        for row in section_maps.values()
        for value in row.get("fact_anchorable_evidence_ids") or []
        if str(value) in by_id
    }
    raw_fact_candidate_count = len(raw_fact_ids)
    fact_section_count = len(fact_rows)
    fact_minimum_total = sum(
        int(row.get("minimum_fact_anchors") or 0) for row in fact_rows
    )
    covered_section_count = sum(
        int(row.get("distinct_fact_source_spans") or 0)
        >= int(row.get("minimum_fact_anchors") or 0)
        for row in fact_rows
    )
    true_sparse = bool(fact_rows) and (
        covered_section_count < fact_section_count
        or raw_fact_candidate_count < fact_minimum_total
    )
    full_target_supported = (
        not true_sparse
        and (
            not fact_rows
            or raw_fact_candidate_count >= fact_minimum_total + fact_section_count
            or (raw_fact_candidate_count >= fact_minimum_total and bool(chains))
        )
    )
    if true_sparse:
        capacity_gap = CAPACITY_MATERIALLY_SPARSE
        sparse_trigger = "RAW_FACT_CAPACITY_OR_BUSINESS_BLOCK_COVERAGE_BELOW_SAFETY_MINIMUM"
        supported_total = estimated_supported_total
    elif full_target_supported:
        capacity_gap = CAPACITY_SUPPORTED
        sparse_trigger = "NONE"
        supported_total = requested_total
    else:
        capacity_gap = CAPACITY_SLIGHTLY_SPARSE
        sparse_trigger = "SAFETY_MINIMUM_MET_WITHOUT_DENSITY_OR_CHAIN_CONTINUITY"
        supported_total = estimated_supported_total

    allocations = _allocate(
        supported_total,
        [float(row["recommended_relative_budget"]) for row in section_capacity],
    )
    for row, allocation in zip(section_capacity, allocations):
        row["recommended_capacity_aware_word_budget"] = allocation

    return {
        "schema_version": CAPACITY_SCHEMA_VERSION,
        "estimate_kind": ESTIMATE_KIND,
        "quality_proven_threshold": False,
        "uses_evidence_to_word_formula": not NO_EVIDENCE_WORD_FORMULA,
        "requested_target_words": requested_total,
        "evidence_supported_words": supported_total,
        "evidence_supported_word_range": {
            "lower": max(1, round(supported_total * 0.85)),
            "upper": min(requested_total, round(supported_total * 1.15)),
        },
        "capacity_gap": capacity_gap,
        "raw_available_evidence_count": len(by_id),
        "raw_fact_candidate_count": raw_fact_candidate_count,
        "business_block_count": fact_section_count,
        "business_block_minimum_fact_total": fact_minimum_total,
        "business_block_coverage_count": covered_section_count,
        "work_chain_count": len(chains),
        "sparse_trigger": sparse_trigger,
        "replan_recommendation": (
            "REPLAN_RECOMMENDED"
            if true_sparse
            else "NOT_REQUIRED"
        ),
        "section_capacity": section_capacity,
        "method_note": (
            "Relative engineering estimate from section purpose, Planner importance, "
            "raw legal fact capacity, business-block coverage, Fact Anchor safety minimum, "
            "source-span independence and Work Chain coverage; "
            "not a calibrated quality threshold and not an Evidence-to-words conversion."
        ),
    }


def refresh_capacity_budget_with_anchors(
    capacity_budget: Mapping[str, Any],
    content_plan: Mapping[str, Any],
    fact_anchor_pack: Mapping[str, Any],
) -> dict[str, Any]:
    """Refresh an estimate after Fact Anchor classification without new facts."""

    refreshed = {
        key: value
        for key, value in capacity_budget.items()
        if key != "section_capacity"
    }
    anchors = list(fact_anchor_pack.get("fact_anchors") or [])
    analysis = list(fact_anchor_pack.get("analysis_support_pack") or [])
    section_by_id = {
        str(section.get("section_id") or ""): section
        for section in content_plan.get("sections") or []
    }
    rows: list[dict[str, Any]] = []
    for original in capacity_budget.get("section_capacity") or []:
        row = dict(original)
        section_id = str(row.get("section_id") or "")
        section = section_by_id.get(section_id, {})
        row["requested_word_budget"] = max(0, int(section.get("recommended_words") or 0))
        row["confirmed_fact_anchor_count"] = sum(
            str(item.get("section_id") or "") == section_id for item in anchors
        )
        row["confirmed_analysis_support_count"] = sum(
            str(item.get("section_id") or "") == section_id for item in analysis
        )
        if (
            str(section.get("evidence_policy") or "fact_required") == "fact_required"
            and int(row["confirmed_fact_anchor_count"]) < _section_fact_minimum(section)
        ):
            row["capacity_status"] = FACT_INSUFFICIENT
            row["capacity_factor"] = _STATUS_FACTOR[FACT_INSUFFICIENT]
            row["recommended_relative_budget"] = round(
                max(0.1, float(row.get("importance") or 1.0))
                * _STATUS_FACTOR[FACT_INSUFFICIENT],
                6,
            )
        rows.append(row)

    requested_total = max(1, int(capacity_budget.get("requested_target_words") or 1))
    estimated_supported_total = max(
        1,
        min(
            requested_total,
            round(sum(
                int(row.get("requested_word_budget") or 0)
                * float(row.get("capacity_factor") or 0.0)
                for row in rows
            )),
        ),
    )
    fact_rows = [row for row in rows if row.get("capacity_status") != NOT_FACT_DRIVEN]
    confirmed_fact_ids = {
        str(item.get("evidence_id") or "") for item in anchors
        if item.get("evidence_id")
    }
    confirmed_covered_sections = sum(
        int(row.get("confirmed_fact_anchor_count") or 0)
        >= int(row.get("minimum_fact_anchors") or 0)
        for row in fact_rows
    )
    confirmed_minimum_total = sum(
        int(row.get("minimum_fact_anchors") or 0) for row in fact_rows
    )
    true_sparse = bool(fact_rows) and (
        confirmed_covered_sections < len(fact_rows)
        or len(confirmed_fact_ids) < confirmed_minimum_total
    )
    if true_sparse:
        gap = CAPACITY_MATERIALLY_SPARSE
        supported_total = estimated_supported_total
        sparse_trigger = "CONFIRMED_FACT_ANCHORS_OR_SECTION_COVERAGE_BELOW_SAFETY_MINIMUM"
    elif capacity_budget.get("capacity_gap") == CAPACITY_SUPPORTED:
        gap = CAPACITY_SUPPORTED
        supported_total = requested_total
        sparse_trigger = "NONE"
    else:
        gap = CAPACITY_SLIGHTLY_SPARSE
        supported_total = estimated_supported_total
        sparse_trigger = str(capacity_budget.get("sparse_trigger") or "DENSITY_CONTINUITY_NOT_CONFIRMED")

    allocations = _allocate(
        supported_total,
        [float(row.get("recommended_relative_budget") or 0.01) for row in rows],
    )
    for row, allocation in zip(rows, allocations):
        row["recommended_capacity_aware_word_budget"] = allocation

    refreshed.update({
        "requested_target_words": requested_total,
        "evidence_supported_words": supported_total,
        "evidence_supported_word_range": {
            "lower": max(1, round(supported_total * 0.85)),
            "upper": min(requested_total, round(supported_total * 1.15)),
        },
        "capacity_gap": gap,
        "confirmed_fact_anchor_count": len(confirmed_fact_ids),
        "confirmed_business_block_coverage_count": confirmed_covered_sections,
        "sparse_trigger": sparse_trigger,
        "replan_recommendation": (
            "REPLAN_RECOMMENDED" if true_sparse else "NOT_REQUIRED"
        ),
        "section_capacity": rows,
    })
    return refreshed


def project_capacity_budget_onto_plan(
    content_plan: Mapping[str, Any],
    capacity_budget: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a plan copy with fields sourced from one final capacity budget."""

    plan = copy.deepcopy(dict(content_plan))
    capacity_by_section = {
        str(row.get("section_id") or ""): row
        for row in capacity_budget.get("section_capacity") or []
        if isinstance(row, Mapping)
    }
    section_fields = (
        "capacity_status",
        "available_fact_anchorable",
        "distinct_fact_source_spans",
        "available_work_chains",
        "chain_member_count",
        "supporting_evidence_count",
        "analysis_support_only_count",
        "confirmed_fact_anchor_count",
        "confirmed_analysis_support_count",
        "requested_word_budget",
        "recommended_relative_budget",
        "recommended_capacity_aware_word_budget",
    )
    for section in plan.get("sections") or []:
        row = capacity_by_section.get(str(section.get("section_id") or ""), {})
        for field in section_fields:
            if field in row:
                section[field] = copy.deepcopy(row[field])
            else:
                section.pop(field, None)
    plan["evidence_capacity_budget"] = copy.deepcopy(dict(capacity_budget))
    plan["requested_target_words"] = int(
        capacity_budget.get("requested_target_words") or 0
    )
    plan["evidence_supported_words"] = int(
        capacity_budget.get("evidence_supported_words") or 0
    )
    plan["capacity_gap"] = str(capacity_budget.get("capacity_gap") or "")
    return plan
