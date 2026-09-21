"""Experimental writing-style guidance library loader and plan overlay.

The library may guide structure and expression planning. It never participates
in retrieval, atomic evidence, fact anchors, or fact validation.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

from app.config import BASE_DIR, get_settings


REPOSITORY_ROOT = BASE_DIR.parent
STYLE_LIBRARY_ROOT = REPOSITORY_ROOT / "writing_style_library"
EXPECTED_SOURCE_ROLE = "WRITING_STYLE_CONSTRAINT_ONLY_NOT_FACT_SOURCE"

WORK_SUMMARY = "\u5de5\u4f5c\u603b\u7ed3"
SAFETY_PRODUCTION = "\u5b89\u5168\u751f\u4ea7"
ANNUAL_SUMMARY_MODE = "\u5e74\u5ea6\u603b\u7ed3\u6a21\u5f0f"
SPECIAL_CAMPAIGN_MODE = "\u4e13\u9879\u6574\u6cbb\u6a21\u5f0f"
STAGE_REPORT_MODE = "\u9636\u6bb5\u6c47\u62a5\u6a21\u5f0f"
MEETING_SUMMARY_MODE = "\u4f1a\u8bae\u603b\u7ed3\u90e8\u7f72\u6a21\u5f0f"

WRITING_STYLE_LIBRARY_REGISTRY = (
    {
        "document_type_keywords": (WORK_SUMMARY,),
        "business_domain_keywords": (SAFETY_PRODUCTION,),
        "path": Path("safety_production") / "work_summary_v1.json",
    },
)


def _contains_any(value: Any, keywords: tuple[str, ...]) -> bool:
    text = str(value or "")
    return any(keyword in text for keyword in keywords)


def _task_matches(task: Any, entry: dict[str, Any]) -> bool:
    document_type = getattr(task, "document_type", "")
    business_domain = getattr(task, "business_domain", "")
    title = getattr(task, "title", "")
    return (
        _contains_any(document_type, tuple(entry["document_type_keywords"]))
        and (
            _contains_any(business_domain, tuple(entry["business_domain_keywords"]))
            or _contains_any(title, tuple(entry["business_domain_keywords"]))
        )
    )


def _boundary_report(pack: dict[str, Any], path: Path) -> dict[str, Any]:
    source_role = str(pack.get("source_role") or "")
    forbidden_top_level = [
        key for key in ("evidence", "fact_anchors", "atomic_evidence", "rag_results")
        if key in pack
    ]
    fact_boundary = pack.get("fact_boundary")
    passed = (
        source_role == EXPECTED_SOURCE_ROLE
        and isinstance(fact_boundary, dict)
        and not forbidden_top_level
    )
    return {
        "enabled": True,
        "loaded": passed,
        "path": str(path),
        "source_role": source_role,
        "expected_source_role": EXPECTED_SOURCE_ROLE,
        "usage_role": "WRITING_GUIDANCE_ONLY_NOT_FACT_SOURCE",
        "blocked_from": [
            "RAG",
            "Atomic Evidence",
            "Fact Anchor",
            "FACT validation",
        ],
        "forbidden_top_level_keys": forbidden_top_level,
        "passed": passed,
    }


def _sanitize_pack(pack: dict[str, Any], path: Path) -> dict[str, Any]:
    sanitized = copy.deepcopy(pack)
    sanitized["runtime_boundary"] = {
        "usage_role": "WRITING_GUIDANCE_ONLY_NOT_FACT_SOURCE",
        "loaded_from": str(path),
        "must_not_supply": [
            "evidence_id",
            "fact source",
            "factual claims",
            "numbers",
            "time",
            "organizations",
            "duties",
            "mechanisms",
            "results",
        ],
    }
    return sanitized


def _string_list(values: Any, *, limit: int = 4) -> list[str]:
    if not isinstance(values, list):
        return []
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _first_matching_scenario(
    pack: dict[str, Any],
    task: Any,
) -> dict[str, Any] | None:
    scenarios = [
        item for item in pack.get("scenario_modes") or []
        if isinstance(item, dict)
    ]
    if not scenarios:
        return None
    title = str(getattr(task, "title", "") or "")
    purpose = str(getattr(task, "writing_purpose", "") or "")
    document_type = str(getattr(task, "document_type", "") or "")
    combined = f"{title} {purpose}"
    by_mode = {str(item.get("mode") or ""): item for item in scenarios}

    def selected(mode: str, matched_by: str) -> dict[str, Any] | None:
        scenario = by_mode.get(mode)
        if not scenario:
            return None
        result = copy.deepcopy(scenario)
        result["matched_by"] = matched_by
        return result

    if any(token in title for token in ("会议", "讲话", "部署")):
        meeting = selected(MEETING_SUMMARY_MODE, "explicit_meeting_phrase")
        if meeting:
            return meeting

    if WORK_SUMMARY in document_type:
        if any(token in title for token in ("专项整治", "专项行动", "专项")):
            special = selected(SPECIAL_CAMPAIGN_MODE, "explicit_special_campaign")
            if special:
                return special
        if any(token in combined for token in ("阶段性", "当前阶段", "近期工作", "阶段工作汇报")):
            stage = selected(STAGE_REPORT_MODE, "explicit_stage_phrase")
            if stage:
                return stage
        if (
            any(token in title for token in ("年度", "全年"))
            or re.search(r"20\d{2}年.+工作总结", title)
            or re.search(r"20\d{2}年度.+工作总结", title)
        ):
            annual = selected(ANNUAL_SUMMARY_MODE, "title_year_work_summary")
            if annual:
                return annual
        annual = selected(ANNUAL_SUMMARY_MODE, "document_type_default")
        if annual:
            return annual

    for scenario in scenarios:
        mode = str(scenario.get("mode") or "")
        if mode and any(token in combined for token in ("年度", "全年")) and "年度" in mode:
            return selected(mode, "explicit_annual_phrase")
        if mode and any(token in combined for token in ("专项", "整治")) and "专项" in mode:
            return selected(mode, "explicit_special_campaign")
        if mode and any(token in combined for token in ("阶段性", "当前阶段", "近期工作", "阶段工作汇报")) and "阶段" in mode:
            return selected(mode, "explicit_stage_phrase")
        if mode and any(token in combined for token in ("会议", "讲话", "部署")) and "会议" in mode:
            return selected(mode, "explicit_meeting_phrase")
    fallback = copy.deepcopy(scenarios[0])
    fallback["matched_by"] = "pack_default"
    return fallback


def _section_guidance_by_name(pack: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in pack.get("section_guidance") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("section") or "").strip()
        if name:
            result[name] = item
    return result


def _paragraph_pattern_summary(pack: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in pack.get("paragraph_pattern") or []:
        if not isinstance(item, dict):
            continue
        result.append(
            {
                "name": str(item.get("name") or ""),
                "purpose": str(item.get("purpose") or ""),
                "steps": _string_list(item.get("steps"), limit=4),
                "avoid": _string_list(item.get("avoid"), limit=3),
            }
        )
        if len(result) >= 5:
            break
    return result


def _choose_section_guidance(
    section: dict[str, Any],
    index: int,
    section_count: int,
    guidance_by_name: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    role = str(section.get("section_role") or "")
    if role == "problem_analysis":
        return guidance_by_name.get("问题短板")
    if role == "next_step_arrangement":
        return guidance_by_name.get("下一步安排")
    if role == "conclusion":
        return guidance_by_name.get("结尾")
    if index == 0 and section_count <= 3:
        return guidance_by_name.get("主要工作")
    return guidance_by_name.get("主要工作") or guidance_by_name.get("成效表达")


def apply_writing_style_guidance_to_content_plan(
    content_plan: dict[str, Any],
    writing_style_guidance_pack: dict[str, Any] | None,
    *,
    task: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Overlay fact-free style guidance onto the content plan."""
    if not writing_style_guidance_pack:
        return content_plan, {
            "applied": False,
            "reason": "no loaded writing style guidance pack",
            "modified_fields": [],
        }
    pack = writing_style_guidance_pack
    plan = copy.deepcopy(content_plan)
    sections = [
        section for section in plan.get("sections") or []
        if isinstance(section, dict)
    ]
    scenario = _first_matching_scenario(pack, task)
    guidance_by_name = _section_guidance_by_name(pack)
    paragraph_patterns = _paragraph_pattern_summary(pack)
    modified_fields: set[str] = set()
    scenario_flow = _string_list((scenario or {}).get("recommended_flow"), limit=5)
    scenario_avoid = _string_list((scenario or {}).get("avoid"), limit=4)
    for index, section in enumerate(sections):
        guidance = _choose_section_guidance(
            section,
            index,
            len(sections),
            guidance_by_name,
        )
        style_overlay = {
            "source_role": "WRITING_STYLE_STRUCTURE_GUIDANCE_NOT_FACT_SOURCE",
            "section_guidance": str((guidance or {}).get("section") or ""),
            "purpose": str((guidance or {}).get("purpose") or ""),
            "must_include": _string_list((guidance or {}).get("must_include"), limit=4),
            "recommended_expansion": _string_list(
                (guidance or {}).get("recommended_expansion"),
                limit=4,
            ),
            "avoid": _string_list((guidance or {}).get("avoid"), limit=4),
        }
        section["writing_style_structure_guidance"] = style_overlay
        modified_fields.add("sections[].writing_style_structure_guidance")
        focus = list(section.get("writing_focus") or section.get("section_focus") or [])
        for item in style_overlay["must_include"]:
            if item and item not in focus:
                focus.append(item)
        if focus:
            section["writing_focus"] = focus[:8]
            modified_fields.add("sections[].writing_focus")
        expansion_path = list(section.get("expansion_path") or [])
        for item in style_overlay["recommended_expansion"]:
            if item and item not in expansion_path:
                expansion_path.append(item)
        if expansion_path:
            section["expansion_path"] = expansion_path[:8]
            modified_fields.add("sections[].expansion_path")
        prohibited = list(section.get("prohibited_specifics") or [])
        for item in style_overlay["avoid"] + scenario_avoid:
            if item and item not in prohibited:
                prohibited.append(item)
        if prohibited:
            section["prohibited_specifics"] = prohibited[:12]
            modified_fields.add("sections[].prohibited_specifics")
    if sections:
        plan["sections"] = sections
    plan["writing_style_library_applied"] = bool(sections)
    plan["writing_style_planning_guidance"] = {
        "source_role": "WRITING_STYLE_STRUCTURE_GUIDANCE_NOT_FACT_SOURCE",
        "scenario_mode": str((scenario or {}).get("mode") or ""),
        "scenario_matched_by": str((scenario or {}).get("matched_by") or ""),
        "scenario_purpose": str((scenario or {}).get("purpose") or ""),
        "recommended_flow": scenario_flow,
        "avoid": scenario_avoid,
        "fact_boundary": pack.get("fact_boundary") or {},
        "expression_rules": pack.get("expression_rules") or {},
        "paragraph_patterns": paragraph_patterns,
    }
    modified_fields.add("writing_style_planning_guidance")
    return plan, {
        "applied": bool(sections),
        "modified_fields": sorted(modified_fields),
        "section_count": len(sections),
        "scenario_mode": str((scenario or {}).get("mode") or ""),
        "scenario_matched_by": str((scenario or {}).get("matched_by") or ""),
        "blocked_from": [
            "RAG",
            "Atomic Evidence",
            "Fact Anchor",
            "FACT validation",
        ],
    }


def load_writing_style_guidance_pack(task: Any) -> dict[str, Any]:
    if not bool(get_settings().writing_style_library_enabled):
        return {
            "enabled": False,
            "loaded": False,
            "pack": None,
            "report": {
                "enabled": False,
                "loaded": False,
                "reason": "ENABLE_WRITING_STYLE_LIBRARY is false",
                "usage_role": "WRITING_GUIDANCE_ONLY_NOT_FACT_SOURCE",
            },
        }

    for entry in WRITING_STYLE_LIBRARY_REGISTRY:
        if not _task_matches(task, entry):
            continue
        path = STYLE_LIBRARY_ROOT / entry["path"]
        if not path.exists():
            return {
                "enabled": True,
                "loaded": False,
                "pack": None,
                "report": {
                    "enabled": True,
                    "loaded": False,
                    "reason": "matched library file is missing",
                    "path": str(path),
                    "usage_role": "WRITING_GUIDANCE_ONLY_NOT_FACT_SOURCE",
                },
            }
        with path.open("r", encoding="utf-8") as file:
            pack = json.load(file)
        if not isinstance(pack, dict):
            return {
                "enabled": True,
                "loaded": False,
                "pack": None,
                "report": {
                    "enabled": True,
                    "loaded": False,
                    "reason": "library file is not a JSON object",
                    "path": str(path),
                    "usage_role": "WRITING_GUIDANCE_ONLY_NOT_FACT_SOURCE",
                },
            }
        report = _boundary_report(pack, path)
        return {
            "enabled": True,
            "loaded": bool(report["passed"]),
            "pack": _sanitize_pack(pack, path) if report["passed"] else None,
            "report": report,
        }

    return {
        "enabled": True,
        "loaded": False,
        "pack": None,
        "report": {
            "enabled": True,
            "loaded": False,
            "reason": "no matching writing style library",
            "usage_role": "WRITING_GUIDANCE_ONLY_NOT_FACT_SOURCE",
        },
    }
