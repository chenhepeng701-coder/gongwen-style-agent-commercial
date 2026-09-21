"""Deterministic, evidence-bound planning for work summaries.

The planner consumes the frozen Work Summary Quality Contract V1.0, task
metadata, fact-free reference structure metadata, and already validated atomic
evidence.  It only organizes existing evidence IDs; it never creates facts,
fact anchors, evidence relationships, or prose.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any, Mapping

from app.services.generation.quality.models import ContentFunction
from app.services.generation.quality.work_summary_v1 import (
    WORK_SUMMARY_QUALITY_CONTRACT_V1,
)


PLANNER_SCHEMA_VERSION = "work-summary-dynamic-planner-1"
EVIDENCE_CONSTRAINED = "evidence_constrained"
SPARSE_EVIDENCE = "SPARSE_EVIDENCE"
REFERENCE_COMPATIBLE = "COMPATIBLE"
REFERENCE_PARTIAL = "PARTIAL"
REFERENCE_INCOMPATIBLE = "INCOMPATIBLE"

_CONTRACT = WORK_SUMMARY_QUALITY_CONTRACT_V1
_PROFILES = {profile.scenario_id: profile for profile in _CONTRACT.scenario_profiles}
_HAN_ALNUM_RE = re.compile(r"[^\u3400-\u9fffA-Za-z0-9]+")
_TRAILING_STATUS_RE = re.compile(
    r"(?:工作|任务|事项|项目|情况|进展|成效|成果|建设|服务|管理|治理|保障|"
    r"推进|落实|开展|完成|提升|改进|整改|问题|短板|安排|计划|\d+)$"
)
_TOPIC_STOP_GRAMS = {
    "工作", "任务", "开展", "推进", "落实", "有关", "相关", "情况", "重点",
    "部门", "单位", "责任", "主体", "年度", "半年", "阶段", "总结",
}
_PROBLEM_MARKERS = (
    "问题", "不足", "短板", "差距", "制约", "瓶颈", "尚未", "未完成",
    "不够", "薄弱", "顽疾", "两张皮", "有待",
)
_FUTURE_MARKERS = ("下一步", "下半年", "后续", "计划", "拟", "将", "待", "继续", "未完成")
_UNFINISHED_MARKERS = ("尚未", "未完成", "待", "正在", "推进中", "部分完成")
_RESULT_MARKERS = ("完成", "实现", "形成", "达到", "成效", "成果", "结果")
_REFORM_MARKERS = ("改革", "创新", "机制", "制度", "流程再造")
_EXPLICIT_IRRELEVANCE_MARKERS = ("完全无关", "无关事项", "噪声")


def is_work_summary_task(task: Any) -> bool:
    return str(getattr(task, "document_type", "") or "").strip() == _CONTRACT.document_type


def _task_value(task: Any, name: str) -> str:
    return str(getattr(task, name, "") or "").strip()


def infer_work_summary_scenario(task: Any) -> dict[str, Any]:
    """Select one contract profile without turning the profile into a template."""

    title = _task_value(task, "title")
    explicit_scene = " ".join(
        value for value in (
            _task_value(task, "scene"),
            _task_value(task, "scene_compat"),
        ) if value
    )
    metadata = " ".join(
        value for value in (
            _task_value(task, "writing_purpose"),
            _task_value(task, "business_domain"),
            _task_value(task, "level"),
            _task_value(task, "org_type"),
        ) if value
    )

    # Specific title evidence outranks broad purpose text such as "汇报总结".
    title_rules: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("long_period_summary", ("五年", "任期", "换届", "届期", "三年回顾", "四年回顾")),
        ("learning_education_summary", ("学习教育", "专题教育", "主题教育")),
        ("party_building_summary", ("党建", "全面从严治党", "党风廉政", "抓基层党建")),
        ("special_summary", ("专项", "整治", "攻坚行动", "专项行动", "治理行动")),
        ("stage_summary", ("阶段性", "阶段总结", "阶段工作")),
        ("half_year_summary", ("上半年", "下半年", "半年")),
        ("township_comprehensive_summary", ("乡镇综合", "镇综合", "街道综合")),
        ("annual_summary", ("年度", "全年", "年终")),
    )
    for scenario_id, markers in title_rules:
        matched = [marker for marker in markers if marker in title]
        if matched:
            return {
                "scenario_id": scenario_id,
                "confidence": "HIGH",
                "matched_by": "title_explicit",
                "signals": matched,
            }

    scene_rules: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("long_period_summary", ("五年", "任期", "换届", "长周期")),
        ("special_summary", ("专项", "整治", "攻坚")),
        ("stage_summary", ("阶段",)),
        ("half_year_summary", ("半年",)),
        ("learning_education_summary", ("学习教育", "主题教育")),
        ("party_building_summary", ("党建", "全面从严治党")),
        ("township_comprehensive_summary", ("乡镇", "街道")),
        ("annual_summary", ("年度", "全年")),
    )
    for scenario_id, markers in scene_rules:
        matched = [marker for marker in markers if marker in explicit_scene]
        if matched:
            return {
                "scenario_id": scenario_id,
                "confidence": "HIGH",
                "matched_by": "explicit_scene",
                "signals": matched,
            }

    metadata_rules = (
        ("learning_education_summary", ("学习教育", "主题教育")),
        ("party_building_summary", ("党建", "全面从严治党")),
        ("township_comprehensive_summary", ("乡镇", "街道", "镇级")),
    )
    for scenario_id, markers in metadata_rules:
        matched = [marker for marker in markers if marker in metadata]
        if matched:
            return {
                "scenario_id": scenario_id,
                "confidence": "MEDIUM",
                "matched_by": "task_metadata",
                "signals": matched,
            }

    if re.search(r"(?:19|20)\d{2}年", title) and "总结" in title:
        return {
            "scenario_id": "annual_summary",
            "confidence": "MEDIUM",
            "matched_by": "title_year_summary",
            "signals": ["年份", "总结"],
        }
    return {
        "scenario_id": "department_comprehensive_summary",
        "confidence": "FALLBACK",
        "matched_by": "safe_generic_fallback",
        "signals": [],
    }


def infer_reference_scenario(title: str, document_type: str, sample: str = "") -> dict[str, Any]:
    """Infer controlled reference metadata without exposing source wording."""

    proxy = type(
        "ReferenceScenarioTask",
        (),
        {
            "title": title,
            "document_type": document_type,
            "writing_purpose": "",
            "business_domain": sample[:80],
            "level": "",
            "org_type": "",
            "scene": "",
            "scene_compat": "",
        },
    )()
    if document_type != _CONTRACT.document_type:
        return {
            "scenario_id": "not_work_summary",
            "confidence": "HIGH",
            "matched_by": "reference_document_type",
            "signals": [],
        }
    return infer_work_summary_scenario(proxy)


def assess_reference_compatibility(
    task: Any,
    scenario: Mapping[str, Any],
    reference_structure: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Judge semantic compatibility; section count is deliberately irrelevant."""

    if not reference_structure or reference_structure.get("status") != "EXTRACTED":
        return {
            "status": REFERENCE_INCOMPATIBLE,
            "reasons": ["no_extracted_reference_structure"],
            "reference_scenario_id": "unknown",
            "section_count_considered": False,
        }
    reference_use = str(reference_structure.get("reference_use") or "STRUCTURE_CANDIDATE")
    if reference_use != "STRUCTURE_CANDIDATE" or "写法" in _task_value(task, "reference_scope"):
        return {
            "status": REFERENCE_INCOMPATIBLE,
            "reasons": ["reference_is_writing_style_only"],
            "reference_scenario_id": "unknown",
            "section_count_considered": False,
        }
    if str(reference_structure.get("document_type") or "") != _CONTRACT.document_type:
        return {
            "status": REFERENCE_INCOMPATIBLE,
            "reasons": ["document_type_conflict"],
            "reference_scenario_id": "not_work_summary",
            "section_count_considered": False,
        }

    reference_profile = reference_structure.get("scenario_profile") or {}
    reference_scenario = str(reference_profile.get("scenario_id") or "department_comprehensive_summary")
    target_scenario = str(scenario.get("scenario_id") or "department_comprehensive_summary")
    if reference_scenario == target_scenario:
        status, reasons = REFERENCE_COMPATIBLE, ["same_document_type_and_scenario"]
    else:
        long_conflict = "long_period_summary" in {target_scenario, reference_scenario}
        special_set = {"special_summary", "stage_summary"}
        one_special = bool({target_scenario, reference_scenario} & special_set)
        both_special = {target_scenario, reference_scenario} <= special_set
        broad_set = {
            "annual_summary", "half_year_summary", "department_comprehensive_summary",
            "township_comprehensive_summary",
        }
        if long_conflict or (one_special and not both_special and ({target_scenario, reference_scenario} & broad_set)):
            status, reasons = REFERENCE_INCOMPATIBLE, ["obvious_time_or_scope_conflict"]
        else:
            status, reasons = REFERENCE_PARTIAL, ["document_type_matches_but_scenario_differs"]
    return {
        "status": status,
        "reasons": reasons,
        "reference_scenario_id": reference_scenario,
        "section_count_considered": False,
    }


def _visible_evidence_text(item: Mapping[str, Any]) -> str:
    return "".join(
        str(item.get(name) or "")
        for name in (
            "canonical_subject", "subject", "canonical_action", "action",
            "canonical_object", "object", "result", "condition", "qualifier",
            "business_block", "planner_business_block", "source_content_role",
        )
    )


def _planner_fact_candidate(item: Mapping[str, Any]) -> bool:
    subject = str(item.get("canonical_subject") or item.get("subject") or "").strip()
    action = str(item.get("canonical_action") or item.get("action") or "").strip()
    obj = str(
        item.get("canonical_object") or item.get("object") or item.get("result") or ""
    ).strip()
    return bool(
        subject and action and obj
        and subject not in {"相关责任主体", "有关责任主体"}
        and action not in {"反映", "呈现"}
    )


def _object_label(item: Mapping[str, Any]) -> str:
    explicit = str(item.get("business_block") or item.get("planner_business_block") or "").strip()
    if explicit:
        return _HAN_ALNUM_RE.sub("", explicit)[:32]
    value = str(
        item.get("canonical_object") or item.get("object") or item.get("result")
        or item.get("canonical_action") or item.get("action") or "重点工作"
    ).strip()
    prefix = re.split(r"[：:/／|｜—-]", value, maxsplit=1)[0]
    normalized = _HAN_ALNUM_RE.sub("", prefix)
    previous = ""
    while normalized and normalized != previous:
        previous = normalized
        normalized = _TRAILING_STATUS_RE.sub("", normalized)
    return (normalized or _HAN_ALNUM_RE.sub("", value) or "重点工作")[:32]


def _topic_keys(items: list[Mapping[str, Any]]) -> dict[str, str]:
    labels = {str(item.get("evidence_id") or ""): _object_label(item) for item in items}
    # A repeated two-character gram is not a business block.  Prefer complete
    # section-derived labels; exact object labels are the conservative fallback.
    return labels


def _content_functions(item: Mapping[str, Any], scenario_id: str) -> list[ContentFunction]:
    text = _visible_evidence_text(item)
    category = str(item.get("evidence_type") or "")
    functions: list[ContentFunction] = [ContentFunction.MAJOR_WORK]
    if item.get("quantities") or item.get("times"):
        functions.append(ContentFunction.INDICATOR_STATUS)
    if item.get("result") or category == "result" or any(marker in text for marker in _RESULT_MARKERS):
        functions.append(ContentFunction.VERIFIABLE_RESULT)
    if category in {"action", "mechanism", "scene"}:
        functions.append(ContentFunction.CONCRETE_OBJECT_PROGRESS)
    if category == "risk" or any(marker in text for marker in _PROBLEM_MARKERS):
        functions.append(ContentFunction.PROBLEM_ANALYSIS)
    if any(marker in text for marker in _UNFINISHED_MARKERS):
        functions.append(ContentFunction.UNFINISHED_TASK)
    if any(marker in text for marker in _FUTURE_MARKERS):
        functions.append(ContentFunction.NEXT_STEP_ARRANGEMENT)
    if any(marker in text for marker in _REFORM_MARKERS):
        functions.append(ContentFunction.REFORM_INNOVATION)
    scenario_function = {
        "annual_summary": ContentFunction.PERIOD_TASK_PROGRESS,
        "half_year_summary": ContentFunction.PERIOD_TASK_PROGRESS,
        "stage_summary": ContentFunction.PERIOD_TASK_PROGRESS,
        "special_summary": ContentFunction.GOVERNANCE_CHAIN,
        "long_period_summary": ContentFunction.LONG_PERIOD_CHANGE,
        "party_building_summary": ContentFunction.GOVERNANCE_CHAIN,
        "learning_education_summary": ContentFunction.GOVERNANCE_CHAIN,
    }.get(scenario_id)
    if scenario_function:
        functions.append(scenario_function)
    return list(dict.fromkeys(functions))


def _importance(item: Mapping[str, Any]) -> float:
    explicit = str(item.get("importance") or item.get("planner_importance") or "").lower()
    score = {"high": 4.0, "medium": 2.0, "low": 1.0}.get(explicit, 1.5)
    if item.get("quantities") or item.get("times") or item.get("results") or item.get("result"):
        score += 1.0
    if str(item.get("evidence_type") or "") in {"action", "mechanism", "result"}:
        score += 0.5
    return score


def _merge_blocks(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    representative = max(
        (left, right),
        key=lambda item: (
            float(item.get("importance") or 0.0),
            -int(item.get("first_index") or 0),
        ),
    )
    representative_label = str(representative.get("label") or "重点工作")
    merged_from = list(dict.fromkeys([
        *left.get("merged_from", [left["label"]]),
        *right.get("merged_from", [right["label"]]),
    ]))
    merged_label = (
        representative_label
        if len(merged_from) == 1
        else f"{representative_label.removesuffix('等重点工作')}等重点工作"
    )[:32]
    return {
        "label": merged_label,
        "evidence_ids": list(dict.fromkeys([*left["evidence_ids"], *right["evidence_ids"]])),
        "evidence_focus": list(dict.fromkeys([*left["evidence_focus"], *right["evidence_focus"]]))[:6],
        "content_functions": list(dict.fromkeys([*left["content_functions"], *right["content_functions"]])),
        "importance": float(left["importance"]) + float(right["importance"]),
        "first_index": min(int(left["first_index"]), int(right["first_index"])),
        "merged_from": merged_from,
    }


def _merged_values(left: Mapping[str, Any], right: Mapping[str, Any], field: str) -> list[Any]:
    return list(dict.fromkeys([*(left.get(field) or []), *(right.get(field) or [])]))


def replan_work_summary_sections(
    content_plan: Mapping[str, Any],
    *,
    insufficient_section_ids: list[str],
    safe_merge_candidates: Mapping[str, list[Mapping[str, Any]]],
    preserved_evidence_ids: Mapping[str, list[str]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Merge locally under-supported fact sections using pre-validated edges.

    The Planner owns the structural mutation, while the Evidence Selection
    owner supplies deterministic, evidence-derived compatibility edges.  This
    function never creates evidence, changes evidence writability, or turns a
    fact section into an analysis-only section.
    """

    original = copy.deepcopy(dict(content_plan))
    sections = copy.deepcopy(list(original.get("sections") or []))
    insufficient = list(dict.fromkeys(str(value) for value in insufficient_section_ids if value))
    metadata: dict[str, Any] = {
        "replan_applied": False,
        "replan_reason": "NOT_REQUIRED" if not insufficient else "NO_SAFE_MERGE",
        "pre_replan_section_count": len(sections),
        "post_replan_section_count": len(sections),
        "insufficient_section_ids": insufficient,
        "merged_section_mapping": {},
        "preserved_evidence_ids": [],
        "dropped_evidence_ids": [],
        "reselection_performed": False,
        "fact_anchor_rebuild_performed": False,
    }
    if not insufficient:
        return original, metadata

    by_id = {str(section.get("section_id") or ""): section for section in sections}
    inadequate = set(insufficient)
    chosen: dict[str, str] = {}
    for weak_id in insufficient:
        weak = by_id.get(weak_id)
        if not weak or str(weak.get("evidence_policy") or "fact_required") != "fact_required":
            return original, metadata
        candidates = sorted(
            safe_merge_candidates.get(weak_id) or [],
            key=lambda item: (
                -int(item.get("compatibility_score") or 0),
                int(item.get("target_priority") or 0),
                str(item.get("target_section_id") or ""),
            ),
        )
        target_id = next(
            (
                str(item.get("target_section_id") or "")
                for item in candidates
                if str(item.get("target_section_id") or "") in by_id
                and str(item.get("target_section_id") or "") not in inadequate
                and str(by_id[str(item.get("target_section_id") or "")].get("evidence_policy") or "fact_required")
                == "fact_required"
            ),
            "",
        )
        if not target_id:
            return original, metadata
        chosen[weak_id] = target_id

    for weak_id in insufficient:
        weak = by_id[weak_id]
        target = by_id[chosen[weak_id]]
        target_block = str(target.get("business_block") or "").strip()
        weak_block = str(weak.get("business_block") or "").strip()
        merged_blocks = list(dict.fromkeys(value for value in (target_block, weak_block) if value))
        target["business_block"] = "与".join(merged_blocks)
        target["section_purpose"] = (
            f"围绕{target['business_block']}组织现有工作事实和进展"
            if target.get("business_block")
            else str(target.get("section_purpose") or weak.get("section_purpose") or "")
        )
        target["section_goal"] = target["section_purpose"]
        for field in (
            "content_functions", "evidence_focus", "section_focus", "writing_focus",
            "expansion_path", "required_evidence_ids", "optional_evidence_ids",
            "related_evidence_ids", "allowed_fact_subjects", "allowed_fact_actions",
            "prohibited_specifics", "merged_from",
        ):
            target[field] = _merged_values(target, weak, field)
        target["replan_preserved_evidence_ids"] = list(dict.fromkeys([
            *(target.get("replan_preserved_evidence_ids") or []),
            *(preserved_evidence_ids.get(weak_id) or []),
        ]))
        target["importance"] = float(target.get("importance") or 0.0) + float(weak.get("importance") or 0.0)
        target["relative_weight"] = float(target.get("relative_weight") or 0.0) + float(weak.get("relative_weight") or 0.0)
        target["recommended_words"] = int(target.get("recommended_words") or 0) + int(weak.get("recommended_words") or 0)
        target["priority"] = min(int(target.get("priority") or 1), int(weak.get("priority") or 1))

    remaining = [section for section in sections if str(section.get("section_id") or "") not in inadequate]
    old_to_new: dict[str, str] = {}
    for index, section in enumerate(remaining, start=1):
        old_id = str(section.get("section_id") or "")
        new_id = f"SEC-{index:02d}"
        old_to_new[old_id] = new_id
        section["section_id"] = new_id
        section["priority"] = index
    merged_mapping = {
        weak_id: old_to_new[target_id]
        for weak_id, target_id in chosen.items()
    }
    for section in remaining:
        for field in (
            "selected_evidence_ids", "core_evidence_ids", "supporting_evidence_ids",
            "work_chain_ids", "ordered_chain_evidence_ids", "work_chain_summaries",
        ):
            section.pop(field, None)

    replanned = original
    replanned["sections"] = remaining
    replanned["section_count"] = len(remaining)
    replanned["required_structure_roles"] = [
        str(section.get("section_role") or "") for section in remaining
    ]
    dynamic_plan = copy.deepcopy(replanned.get("work_summary_planner") or {})
    if dynamic_plan:
        dynamic_plan["sections"] = [
            {
                "compatibility_role": str(section.get("section_role") or "work_progress"),
                "content_functions": list(section.get("content_functions") or []),
                "section_purpose": str(section.get("section_purpose") or ""),
                "business_block": str(section.get("business_block") or ""),
                "evidence_focus": list(section.get("evidence_focus") or section.get("section_focus") or []),
                "evidence_ids": list(section.get("required_evidence_ids") or []),
                "related_evidence_ids": list(section.get("related_evidence_ids") or []),
                "importance": float(section.get("importance") or 1.0),
                "priority": int(section.get("priority") or 1),
                "problem_relationship": str(section.get("problem_relationship") or "not_supported"),
                "unfinished_relationship": str(section.get("unfinished_relationship") or "not_supported"),
                "next_step_relationship": str(section.get("next_step_relationship") or "not_supported"),
                "relative_weight": float(section.get("relative_weight") or 1.0),
                "merged_from": list(section.get("merged_from") or []),
            }
            for section in remaining
        ]
        dynamic_plan["business_blocks"] = [
            {
                "business_block": str(section.get("business_block") or ""),
                "evidence_ids": list(section.get("required_evidence_ids") or []),
                "importance": float(section.get("importance") or 1.0),
                "priority": int(section.get("priority") or 1),
            }
            for section in remaining
            if section.get("required_evidence_ids")
        ]
        dynamic_plan["section_capacity_replan_applied"] = True
        replanned["work_summary_planner"] = dynamic_plan
    seed = json.dumps(
        {"base_plan_id": original.get("plan_id"), "merged_section_mapping": merged_mapping},
        ensure_ascii=False,
        sort_keys=True,
    )
    replanned["plan_id"] = f"PLAN-{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:12].upper()}"
    metadata.update({
        "replan_applied": True,
        "replan_reason": "WORK_SUMMARY_SECTION_REPLAN",
        "post_replan_section_count": len(remaining),
        "merged_section_mapping": merged_mapping,
        "preserved_evidence_ids": sorted({
            evidence_id
            for weak_id in insufficient
            for evidence_id in preserved_evidence_ids.get(weak_id) or []
        }),
    })
    replanned["work_summary_section_replan"] = copy.deepcopy(metadata)
    return replanned, metadata


def degrade_work_summary_plan_for_sparse_evidence(
    content_plan: Mapping[str, Any],
    *,
    section_anchor_counts: Mapping[str, int],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Remove unsupported factual sections without moving or creating evidence.

    This is the bounded fallback after normal selection and the existing safe
    merge replan have failed on density alone.  A factual section with one
    valid anchor is retained; a factual section with zero anchors is removed.
    Existing analysis-only sections remain analysis-only.
    """

    original = copy.deepcopy(dict(content_plan))
    sections = copy.deepcopy(list(original.get("sections") or []))
    counts = {
        str(section_id): max(0, int(count or 0))
        for section_id, count in section_anchor_counts.items()
    }
    factual_sections = [
        section
        for section in sections
        if str(section.get("evidence_policy") or "fact_required") == "fact_required"
    ]
    retained_fact_ids = [
        str(section.get("section_id") or "")
        for section in factual_sections
        if counts.get(str(section.get("section_id") or ""), 0) >= 1
    ]
    removed_fact_ids = [
        str(section.get("section_id") or "")
        for section in factual_sections
        if counts.get(str(section.get("section_id") or ""), 0) == 0
    ]
    sparse_fact_ids = [
        str(section.get("section_id") or "")
        for section in factual_sections
        if counts.get(str(section.get("section_id") or ""), 0) < 2
    ]
    metadata: dict[str, Any] = {
        "degraded_plan_applied": False,
        "degrade_reason": "NOT_REQUIRED" if not sparse_fact_ids else "NO_VALID_FACT_SECTION",
        "planning_mode": EVIDENCE_CONSTRAINED,
        "pre_degrade_section_count": len(sections),
        "post_degrade_section_count": len(sections),
        "retained_section_ids": [str(section.get("section_id") or "") for section in sections],
        "removed_or_downgraded_section_ids": [],
        "removed_section_ids": [],
        "downgraded_section_ids": [],
        "one_anchor_fact_section_ids": [
            section_id for section_id in retained_fact_ids if counts.get(section_id) == 1
        ],
        "creates_fact_anchors": False,
        "copies_fact_anchors": False,
        "cross_section_rebinding": False,
    }
    if not sparse_fact_ids or not retained_fact_ids:
        return original, metadata

    removed = set(removed_fact_ids)
    retained_sections = [
        section
        for section in sections
        if str(section.get("section_id") or "") not in removed
    ]
    retained_ids = [str(section.get("section_id") or "") for section in retained_sections]
    original["sections"] = retained_sections
    original["section_count"] = len(retained_sections)
    original["required_structure_roles"] = [
        str(section.get("section_role") or "") for section in retained_sections
    ]
    original["planning_mode"] = EVIDENCE_CONSTRAINED

    dynamic_plan = copy.deepcopy(original.get("work_summary_planner") or {})
    if dynamic_plan:
        retained_indexes = [
            index
            for index, section in enumerate(sections)
            if str(section.get("section_id") or "") not in removed
        ]
        dynamic_sections = list(dynamic_plan.get("sections") or [])
        dynamic_plan["sections"] = [
            dynamic_sections[index]
            for index in retained_indexes
            if index < len(dynamic_sections)
        ]
        retained_blocks = {
            str(section.get("business_block") or "")
            for section in retained_sections
            if section.get("required_evidence_ids")
        }
        dynamic_plan["business_blocks"] = [
            block
            for block in dynamic_plan.get("business_blocks") or []
            if str(block.get("business_block") or "") in retained_blocks
        ]
        dynamic_plan["sparse_evidence_degrade_applied"] = True
        original["work_summary_planner"] = dynamic_plan

    seed = json.dumps(
        {
            "base_plan_id": original.get("plan_id"),
            "planning_mode": EVIDENCE_CONSTRAINED,
            "retained_section_ids": retained_ids,
            "removed_section_ids": removed_fact_ids,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    original["plan_id"] = f"PLAN-{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:12].upper()}"
    metadata.update({
        "degraded_plan_applied": True,
        "degrade_reason": SPARSE_EVIDENCE,
        "post_degrade_section_count": len(retained_sections),
        "retained_section_ids": retained_ids,
        "removed_or_downgraded_section_ids": removed_fact_ids,
        "removed_section_ids": removed_fact_ids,
    })
    original["work_summary_sparse_evidence"] = copy.deepcopy(metadata)
    return original, metadata


def _form_business_blocks(
    required_items: list[Mapping[str, Any]], scenario_id: str, target_words: int,
) -> tuple[list[dict[str, Any]], float]:
    topic_keys = _topic_keys(required_items)
    grouped: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(required_items):
        evidence_id = str(item.get("evidence_id") or "")
        key = topic_keys.get(evidence_id) or _object_label(item)
        block = grouped.setdefault(
            key,
            {
                "label": key,
                "evidence_ids": [],
                "evidence_focus": [],
                "content_functions": [],
                "importance": 0.0,
                "first_index": index,
                "merged_from": [key],
            },
        )
        block["evidence_ids"].append(evidence_id)
        block["evidence_focus"].append(_object_label(item))
        block["content_functions"] = list(dict.fromkeys([
            *block["content_functions"],
            *(function.value for function in _content_functions(item, scenario_id)),
        ]))
        block["importance"] += _importance(item)

    blocks = list(grouped.values())
    observed_block_count = max(1, len(blocks))
    # Smooth structural capacity: more requested space permits more independent
    # evidence-derived blocks, without imposing a fixed work-summary template.
    structural_capacity = max(1, round((max(1, target_words) / 75.0) ** 0.5))
    granularity = min(1.0, structural_capacity / observed_block_count)
    while len(blocks) > structural_capacity:
        merge_index = min(
            range(len(blocks)),
            key=lambda index: (float(blocks[index]["importance"]), int(blocks[index]["first_index"])),
        )
        partner_index = merge_index - 1 if merge_index else 1
        first, second = sorted((merge_index, partner_index))
        merged = _merge_blocks(blocks[first], blocks[second])
        blocks = [
            block for index, block in enumerate(blocks)
            if index not in {first, second}
        ]
        blocks.insert(first, merged)
    blocks.sort(key=lambda block: (-float(block["importance"]), int(block["first_index"])))
    return blocks, granularity


def _reference_guidance(
    section_role: str,
    compatibility: Mapping[str, Any],
    reference_structure: Mapping[str, Any] | None,
    used_indexes: set[int],
) -> dict[str, Any] | None:
    if compatibility.get("status") == REFERENCE_INCOMPATIBLE or not reference_structure:
        return None
    candidates = list(reference_structure.get("primary_structure") or [])
    for index, candidate in enumerate(candidates):
        if index in used_indexes:
            continue
        if str(candidate.get("section_role") or "") != section_role:
            continue
        used_indexes.add(index)
        return {
            "source_role": "REFERENCE_STRUCTURE_ONLY_NOT_FACT_SOURCE",
            "compatibility": str(compatibility.get("status")),
            "section_roles": [section_role],
            "organization_focus": [str(candidate.get("organization_focus") or "")],
            "development_logic": list(candidate.get("development_logic") or []),
            "paragraph_functions": list(candidate.get("paragraph_functions") or []),
            "fact_boundary": "仅指导兼容的组织方式；所有现实事实仍须来自本section合法Fact Anchor",
        }
    return None


def plan_work_summary(
    task: Any,
    evidence_pack: Mapping[str, Any],
    reference_structure: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return dynamic section intentions while preserving all evidence IDs."""

    scenario = infer_work_summary_scenario(task)
    scenario_id = str(scenario["scenario_id"])
    profile = _PROFILES[scenario_id]
    compatibility = assess_reference_compatibility(task, scenario, reference_structure)
    evidence_by_id = {
        str(item.get("evidence_id") or ""): item
        for item in evidence_pack.get("evidence") or []
        if item.get("evidence_id")
    }
    required_ids = [
        str(value) for value in evidence_pack.get("required_evidence") or []
        if str(value) in evidence_by_id
    ]
    eligible_ids = required_ids or list(evidence_by_id)
    all_items = [
        evidence_by_id[value] for value in eligible_ids
        if not any(
            marker in _visible_evidence_text(evidence_by_id[value])
            for marker in _EXPLICIT_IRRELEVANCE_MARKERS
        )
    ]
    eligible_ids = [str(item.get("evidence_id") or "") for item in all_items]
    problem_ids = [
        str(item.get("evidence_id") or "")
        for item in all_items
        if str(item.get("semantic_state") or "") in {"PROBLEM", "GAP", "NOT_IMPLEMENTED"}
        or str(item.get("evidence_type") or "") == "risk"
        or any(marker in _visible_evidence_text(item) for marker in _PROBLEM_MARKERS)
    ]
    future_ids = [
        str(item.get("evidence_id") or "")
        for item in all_items
        if str(item.get("semantic_state") or "") in {"PLAN", "RECOMMENDATION"}
        or any(marker in _visible_evidence_text(item) for marker in _FUTURE_MARKERS)
    ]
    problem_ids = [value for value in problem_ids if value not in set(future_ids)]
    problem_fact_ids = [
        value for value in problem_ids
        if _planner_fact_candidate(evidence_by_id[value])
    ]
    future_fact_ids = [
        value for value in future_ids
        if _planner_fact_candidate(evidence_by_id[value])
    ]
    independent_problem = bool(problem_fact_ids)
    independent_next = bool(future_fact_ids)
    work_ids = [
        evidence_id for evidence_id in eligible_ids
        if (not independent_problem or evidence_id not in set(problem_ids))
        and (not independent_next or evidence_id not in set(future_ids))
    ]
    required_items = [evidence_by_id[evidence_id] for evidence_id in work_ids]
    target_words = max(1, int(getattr(task, "target_words", 0) or 0))
    blocks, granularity = _form_business_blocks(required_items, scenario_id, target_words)
    if not blocks and evidence_by_id:
        fallback_ids = list(evidence_by_id)[:1]
        blocks = [{
            "label": "重点工作",
            "evidence_ids": fallback_ids,
            "evidence_focus": [_object_label(evidence_by_id[fallback_ids[0]])],
            "content_functions": [ContentFunction.MAJOR_WORK.value],
            "importance": 1.0,
            "first_index": 0,
            "merged_from": ["重点工作"],
        }]

    unfinished_ids = [
        evidence_id for evidence_id, item in evidence_by_id.items()
        if any(marker in _visible_evidence_text(item) for marker in _UNFINISHED_MARKERS)
    ]
    sections: list[dict[str, Any]] = []
    for priority, block in enumerate(blocks, start=1):
        section_functions = list(block["content_functions"])
        sections.append({
            "compatibility_role": "work_progress",
            "content_functions": section_functions,
            "section_purpose": f"围绕{block['label']}组织现有工作事实和进展",
            "business_block": str(block["label"]),
            "evidence_focus": list(dict.fromkeys(block["evidence_focus"]))[:6],
            "evidence_ids": list(block["evidence_ids"]),
            "importance": round(float(block["importance"]), 3),
            "priority": priority,
            "problem_relationship": (
                "embedded_in_business_block"
                if problem_ids and not independent_problem
                and bool(set(problem_ids) & set(block["evidence_ids"]))
                else "independent_section" if independent_problem else "not_supported"
            ),
            "unfinished_relationship": (
                "embedded_in_business_block"
                if bool(set(unfinished_ids) & set(block["evidence_ids"]))
                else "not_supported"
            ),
            "next_step_relationship": (
                "embedded_in_business_block"
                if future_ids and not independent_next
                and bool(set(future_ids) & set(block["evidence_ids"]))
                else "independent_section" if independent_next else "not_supported"
            ),
            "relative_weight": max(0.1, float(block["importance"])),
            "merged_from": list(block.get("merged_from") or []),
            "minimum_fact_anchors": 2,
        })

    if independent_problem:
        sections.append({
            "compatibility_role": "problem_analysis",
            "content_functions": [ContentFunction.PROBLEM_ANALYSIS.value],
            "section_purpose": "在可靠问题信号范围内安排问题分析位置，不创造具体问题事实",
            "business_block": "问题与短板",
            "evidence_focus": [
                _object_label(evidence_by_id[value]) for value in problem_ids[:6]
            ],
            "evidence_ids": problem_ids,
            "related_evidence_ids": problem_ids,
            "importance": max(1.0, len(problem_ids) * 0.5),
            "priority": len(sections) + 1,
            "problem_relationship": "independent_section",
            "unfinished_relationship": "related_if_supported",
            "next_step_relationship": "may_merge_or_carry_over",
            "relative_weight": max(0.35, sum(float(item["relative_weight"]) for item in sections) / max(1, len(sections)) * 0.45),
            "merged_from": [],
            "minimum_fact_anchors": min(2, len(problem_fact_ids)),
        })
    if independent_next:
        sections.append({
            "compatibility_role": "next_step_arrangement",
            "content_functions": [ContentFunction.NEXT_STEP_ARRANGEMENT.value],
            "section_purpose": "根据任务与材料中的后续性信号安排下一步位置，不把计划写成已完成事实",
            "business_block": "后续任务",
            "evidence_focus": [
                _object_label(evidence_by_id[value]) for value in future_ids[:6]
            ],
            "evidence_ids": future_ids,
            "related_evidence_ids": list(dict.fromkeys([*future_ids, *unfinished_ids])),
            "importance": max(1.0, len(future_ids) * 0.5),
            "priority": len(sections) + 1,
            "problem_relationship": "carry_over_if_supported",
            "unfinished_relationship": "carry_over_if_supported",
            "next_step_relationship": "independent_section",
            "relative_weight": max(0.35, sum(float(item["relative_weight"]) for item in sections) / max(1, len(sections)) * 0.45),
            "merged_from": [],
            "minimum_fact_anchors": min(2, len(future_fact_ids)),
        })

    used_reference_indexes: set[int] = set()
    for section in sections:
        guidance = _reference_guidance(
            str(section["compatibility_role"]),
            compatibility,
            reference_structure,
            used_reference_indexes,
        )
        if guidance:
            section["reference_structure_guidance"] = guidance

    selected_functions = list(dict.fromkeys(
        function for section in sections for function in section["content_functions"]
    ))
    return {
        "schema_version": PLANNER_SCHEMA_VERSION,
        "quality_contract_id": _CONTRACT.contract_id,
        "quality_contract_version": _CONTRACT.contract_version,
        "scenario": scenario,
        "scenario_profile": {
            "scenario_id": profile.scenario_id,
            "purpose": profile.purpose,
            "preferred_content_functions": [item.value for item in profile.preferred_content_functions],
            "optional_content_functions": [item.value for item in profile.optional_content_functions],
            "prohibited_template_assumptions": list(profile.prohibited_template_assumptions),
        },
        "reference_compatibility": compatibility,
        "selected_content_functions": selected_functions,
        "business_blocks": [
            {
                "business_block": section["business_block"],
                "evidence_ids": list(section["evidence_ids"]),
                "importance": section["importance"],
                "priority": section["priority"],
            }
            for section in sections if section["evidence_ids"]
        ],
        "granularity_score": round(granularity, 6),
        "problem_policy": {
            "universal_section_required": _CONTRACT.problem_policy.universal_section_required,
            "signal_count": len(problem_ids),
            "placement": "independent" if independent_problem else "embedded" if problem_ids else "omitted",
        },
        "next_step_policy": {
            "universal_section_required": _CONTRACT.next_step_policy.universal_section_required,
            "signal_count": len(future_ids),
            "placement": "independent" if independent_next else "embedded" if future_ids else "omitted",
        },
        "sections": sections,
        "uses_work_chain_grouping": False,
        "creates_facts": False,
        "uses_section_count_template": False,
    }
