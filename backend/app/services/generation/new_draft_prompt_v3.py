"""Prompt for Flow 3 v3 new-draft generation from atomic evidence only."""

from __future__ import annotations

import copy
import json
import math
from typing import Any

from app.services.generation.atomic_evidence import (
    build_content_plan,
    build_outbound_evidence_record,
)
from app.services.generation.official_draft_rules import build_official_draft_rule_prompt_block
from app.services.generation.work_summary_execution_policy import (
    build_work_summary_execution_policy,
    is_work_summary_content_plan,
)
from app.services.generation.work_summary_evidence_group import (
    build_work_summary_evidence_groups,
    writer_visible_evidence_groups,
)


REPORT_INTRODUCTION_DOCUMENT_TYPES = frozenset({
    "情况报告",
    "工作报告",
    "调研报告",
})

WRITER_PLAN_CONTROL_SCHEMA_VERSION = "writer-plan-control-1"
WRITER_EXECUTION_CONTRACT_SCHEMA_VERSION = "writer-execution-contract-1"
WRITER_NO_CHAIN_ROLE = "NO_CHAIN"
LEGACY_WRITER_PLAN_SECTION_FIELDS = (
    "section_id",
    "section_role",
    "theme",
    "purpose",
    "priority",
    "word_budget",
    "evidence_group_ids",
)
REQUIRED_WRITER_PLAN_SECTION_FIELDS = (
    "section_goal",
    "writing_focus",
    "expansion_path",
    "evidence_usage_plan",
    "reference_structure_guidance",
    "subsections",
    "work_chain_organization",
    "length_control",
    "execution_contract",
)


def requires_report_introduction(document_type: str) -> bool:
    """Return whether the explicit task document type requires an introduction."""

    return str(document_type or "").strip() in REPORT_INTRODUCTION_DOCUMENT_TYPES


def build_introduction_contract(
    document_type: str,
    target_total: int,
    sections: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a dynamic introduction/body budget without mutating Content Plan."""

    total = max(1, int(target_total or 0))
    required = requires_report_introduction(document_type)
    original_budgets = [
        max(0, int(section.get("recommended_words") or 0))
        for section in sections or []
    ]
    if not required:
        return {
            "required": False,
            "target_length": 0,
            "accepted_range": {"min_chars": 0, "max_chars": 0},
            "body_target": total,
            "section_body_budgets": original_budgets,
        }

    target_length = max(100, min(260, int(round(total * 0.06))))
    min_chars = max(80, math.ceil(target_length * 5 / 6))
    max_chars = min(320, math.ceil(target_length * 25 / 18))
    body_target = max(1, total - target_length)
    budget_sum = sum(original_budgets)
    if not original_budgets:
        section_body_budgets: list[int] = []
    elif budget_sum <= 0:
        quotient, remainder = divmod(body_target, len(original_budgets))
        section_body_budgets = [
            quotient + (1 if index < remainder else 0)
            for index in range(len(original_budgets))
        ]
    else:
        raw = [body_target * budget / budget_sum for budget in original_budgets]
        section_body_budgets = [int(value) for value in raw]
        residue = body_target - sum(section_body_budgets)
        order = sorted(
            range(len(raw)),
            key=lambda index: (raw[index] - section_body_budgets[index], -index),
            reverse=True,
        )
        for index in order[:residue]:
            section_body_budgets[index] += 1
    return {
        "required": True,
        "target_length": target_length,
        "accepted_range": {
            "min_chars": min_chars,
            "max_chars": max_chars,
        },
        "body_target": body_target,
        "section_body_budgets": section_body_budgets,
    }


def _value(value: Any) -> str:
    return str(value or "未指定").strip()


def _fact_anchored_genre_boundary(document_type: str) -> str:
    raw = build_official_draft_rule_prompt_block(document_type)
    risky_markers = (
        "段落展开应尽量包含", "再落到措施、责任和结果", "正文必须优先吸收资料库中的具体事实",
        "如果资料中包含具体做法", "段落展开要体现具体工作场景",
    )
    retained = [line for line in raw.splitlines() if not any(marker in line for marker in risky_markers)]
    retained.extend([
        "",
        "【事实锚定段落边界】",
        "- 只有事实锚点明确提供措施、责任或结果时，正文才能陈述相应内容。",
        "- 事实锚点未提供时，只允许进行一般性分析，不得补充现实措施、部门职责、运行机制、设备、时间、频次或工作结果。",
        "- 允许写一般工作场景分析，但不得将其写成本地已经发生的事实。",
    ])
    return "\n".join(retained)


def _build_writing_reference(
    content_plan: dict[str, Any],
    writing_style_guidance_pack: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Expression guidance only; never a fact source."""
    sections = []
    for section in content_plan.get("sections") or []:
        analysis_only = section.get("evidence_policy") == "analysis_only"
        item = {
            "section_id": str(section.get("section_id") or ""),
            "section_role": str(section.get("section_role") or ""),
            "paragraph_method": (
                [
                    "write_analysis_without_evidence_id",
                    "keep_fact_text_empty",
                    "use_expansion_text_for_general_risk_analysis_or_future_arrangements",
                    "use_transition_text_to_advance_section_logic",
                    "use_summary_text_for_future_or_advisory_closure",
                ]
                if analysis_only
                else [
                    "start_from_one_legal_evidence_id",
                    "write_subject_action_object_as_fact_text",
                    "use_expansion_text_for_background_process_or_organization_logic_only",
                    "use_transition_text_to_advance_background_work_measure_or_progression",
                    "use_summary_text_for_general_method_or_work_orientation_without_formulaic_explanation",
                ]
            ),
        }
        if section.get("reference_structure_guidance"):
            item["reference_structure_guidance"] = section["reference_structure_guidance"]
        if section.get("writing_style_structure_guidance"):
            item["writing_style_structure_guidance"] = section["writing_style_structure_guidance"]
        sections.append(item)
    planning_guidance = content_plan.get("writing_style_planning_guidance") or {}
    style_increment: dict[str, Any] = {}
    if planning_guidance:
        style_increment = {
            "source_role": "WRITING_STYLE_INCREMENT_ONLY_NOT_FACT_SOURCE",
            "selected_scenario": {
                "mode": str(planning_guidance.get("scenario_mode") or ""),
                "matched_by": str(planning_guidance.get("scenario_matched_by") or ""),
                "purpose": str(planning_guidance.get("scenario_purpose") or ""),
                "recommended_flow": list(planning_guidance.get("recommended_flow") or []),
                "avoid": list(planning_guidance.get("avoid") or []),
            },
            "expression_rules": planning_guidance.get("expression_rules") or {},
            "fact_boundary": planning_guidance.get("fact_boundary") or {},
        }
    elif writing_style_guidance_pack:
        style_increment = {
            "source_role": "WRITING_STYLE_INCREMENT_ONLY_NOT_FACT_SOURCE",
            "source_pack_role": str(writing_style_guidance_pack.get("source_role") or ""),
            "runtime_boundary": writing_style_guidance_pack.get("runtime_boundary") or {},
            "expression_rules": writing_style_guidance_pack.get("expression_rules") or {},
            "fact_boundary": writing_style_guidance_pack.get("fact_boundary") or {},
            "guidance_phrases": list(writing_style_guidance_pack.get("guidance_phrases") or [])[:8],
        }
    result = {
        "source_role": "WRITING_REFERENCE_ONLY_NOT_FACT_SOURCE",
        "must_not_supply": [
            "evidence_id",
            "numbers",
            "time",
            "organization",
            "duty",
            "place",
            "equipment",
            "mechanism",
            "result",
        ],
        "sections": sections,
        "official_expression_profile": {
            "prefer": [
                "坚持", "围绕", "聚焦", "结合", "通过", "持续", "进一步", "扎实", "有序推进",
                "压实责任", "统筹推进", "贯通落实", "抓实抓细", "常态推进",
            ],
            "limit_repetition": [
                "关键在于", "核心是", "有利于", "旨在", "体现了", "反映出", "有助于",
            ],
            "rhythm": "按背景引入、工作展开、措施说明、逻辑递进、自然收束组织段落，不固定写成事实一句、解释一句、总结一句。",
        },
    }
    if content_plan.get("uses_reference_structure") and content_plan.get("reference_structure"):
        result["reference_structure"] = content_plan["reference_structure"]
    if style_increment:
        result["writing_style_increment"] = style_increment
    return result


def _compact_work_summary_style_reference(
    content_plan: dict[str, Any],
    writing_style_guidance_pack: dict[str, Any] | None,
) -> dict[str, Any]:
    """Project style into a fact-free Writer view without analysis-shape rules."""

    planning_guidance = content_plan.get("writing_style_planning_guidance") or {}
    expression_rules = (
        planning_guidance.get("expression_rules")
        or (writing_style_guidance_pack or {}).get("expression_rules")
        or {}
    )
    scenario_avoid = list(planning_guidance.get("avoid") or [])
    result = {
        "source_role": "STYLE_ONLY_NOT_FACT_OR_PLAN_SOURCE",
        "tone": list(expression_rules.get("tone") or [])[:6],
        "sentence_guidance": [
            "使用成熟、克制、务实的机关材料句式，避免教学口吻。",
            "相邻FACT可使用不改变事实含义的短承接词，形成连续工作段。",
            "句式和节奏可以变化，但不得固定为事实一句、解释一句、总结一句。",
        ],
        "avoid": scenario_avoid[:6] + [
            "宏大意义、一般规律、管理启示或万能总结",
            "把Style当作事实来源",
        ],
        "ignored_conflicting_guidance": [
            "每个FACT后必须分析",
            "每段必须总结或理论升华",
            "为填满篇幅补写措施、机制、闭环或成效",
        ],
    }
    if planning_guidance:
        result["selected_scenario"] = {
            "mode": str(planning_guidance.get("scenario_mode") or ""),
            "matched_by": str(planning_guidance.get("scenario_matched_by") or ""),
            "purpose": str(planning_guidance.get("scenario_purpose") or ""),
            "recommended_flow": list(planning_guidance.get("recommended_flow") or []),
        }
        result["paragraph_patterns"] = list(
            planning_guidance.get("paragraph_patterns") or []
        )[:4]
    return result


def _writer_binding_index(
    evidence_group_pack: dict[str, Any],
) -> dict[str, dict[str, str]]:
    """Map internal Evidence IDs to the existing Writer unit contract."""

    result: dict[str, dict[str, str]] = {}
    for group in evidence_group_pack.get("groups") or []:
        if not isinstance(group, dict):
            continue
        group_id = str(group.get("group_id") or "")
        section_id = str(group.get("section_id") or "")
        for fact in group.get("facts") or []:
            if not isinstance(fact, dict):
                continue
            evidence_id = str(fact.get("evidence_id") or "")
            fact_unit_id = str(fact.get("fact_unit_id") or "")
            if evidence_id and group_id and fact_unit_id:
                result[evidence_id] = {
                    "section_id": section_id,
                    "group_id": group_id,
                    "fact_unit_id": fact_unit_id,
                }
    return result


def _writer_units_for_evidence_ids(
    evidence_ids: Any,
    *,
    section_id: str,
    binding_index: dict[str, dict[str, str]],
) -> list[dict[str, str]]:
    units: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for raw_id in evidence_ids or []:
        binding = binding_index.get(str(raw_id or "")) or {}
        if binding.get("section_id") != section_id:
            continue
        key = (str(binding.get("group_id") or ""), str(binding.get("fact_unit_id") or ""))
        if not all(key) or key in seen:
            continue
        seen.add(key)
        units.append({"group_id": key[0], "fact_unit_id": key[1]})
    return units


def _writer_evidence_usage_plan(
    raw_plan: Any,
    *,
    section_id: str,
    binding_index: dict[str, dict[str, str]],
) -> dict[str, Any]:
    source = raw_plan if isinstance(raw_plan, dict) else {}
    core_units = _writer_units_for_evidence_ids(
        source.get("core_evidence_ids"),
        section_id=section_id,
        binding_index=binding_index,
    )
    supporting_units = _writer_units_for_evidence_ids(
        source.get("supporting_evidence_ids"),
        section_id=section_id,
        binding_index=binding_index,
    )
    fact_unit_roles: list[dict[str, Any]] = []
    for role in source.get("evidence_roles") or []:
        if not isinstance(role, dict):
            continue
        units = _writer_units_for_evidence_ids(
            [role.get("evidence_id")],
            section_id=section_id,
            binding_index=binding_index,
        )
        if not units:
            continue
        fact_unit_roles.append({
            **units[0],
            "role": str(role.get("role") or ""),
            "write_as": str(role.get("write_as") or ""),
        })
    source_id_count = len({
        str(value or "")
        for key in ("core_evidence_ids", "supporting_evidence_ids")
        for value in source.get(key) or []
        if str(value or "")
    })
    mapped_id_count = len({
        row["fact_unit_id"] for row in core_units + supporting_units
    })
    return {
        "core_writer_units": core_units,
        "supporting_writer_units": supporting_units,
        "usage_steps": copy.deepcopy(list(source.get("usage_steps") or [])),
        "fact_unit_roles": fact_unit_roles,
        "unavailable_writer_unit_count": max(0, source_id_count - mapped_id_count),
        "identifier_contract": "group_id_and_fact_unit_id",
    }


def _writer_work_chain(
    chain: dict[str, Any],
    *,
    section_id: str,
    binding_index: dict[str, dict[str, str]],
) -> dict[str, Any]:
    member_units = _writer_units_for_evidence_ids(
        chain.get("member_evidence_ids"),
        section_id=section_id,
        binding_index=binding_index,
    )
    ordered_units = _writer_units_for_evidence_ids(
        chain.get("ordered_evidence_ids"),
        section_id=section_id,
        binding_index=binding_index,
    )
    order_status = str(chain.get("order_status") or "")
    return {
        "chain_id": str(chain.get("chain_id") or ""),
        "business_block": str(chain.get("business_block") or ""),
        "relation_basis": copy.deepcopy(chain.get("relation_basis") or []),
        "chain_completeness": str(chain.get("chain_completeness") or ""),
        "member_writer_units": member_units,
        "ordered_writer_units": ordered_units,
        "organization_semantics": (
            "PRESERVE_AVAILABLE_ORDER"
            if order_status == "ORDER_AVAILABLE" and ordered_units
            else "RELATED_FACTS_WITHOUT_INFERRED_ORDER"
        ),
    }


def _writer_execution_contract(
    section: dict[str, Any],
    *,
    section_id: str,
    theme: str,
    work_chains: list[dict[str, Any]],
) -> dict[str, Any]:
    expansion_path = [
        str(value).strip()
        for value in section.get("expansion_path") or []
        if str(value).strip()
    ]
    if not expansion_path:
        expansion_path = [str(section.get("section_goal") or "完成章节目标")]
    raw_evidence_usage_plan = section.get("evidence_usage_plan")
    evidence_usage_plan = (
        raw_evidence_usage_plan if isinstance(raw_evidence_usage_plan, dict) else {}
    )
    evidence_usage_steps = [
        str(value).strip()
        for value in evidence_usage_plan.get("usage_steps") or []
        if str(value).strip()
    ]
    if not evidence_usage_steps:
        evidence_usage_steps = list(expansion_path)
    writing_focus = [
        str(value).strip()
        for value in section.get("writing_focus") or []
        if str(value).strip()
    ]
    if not writing_focus:
        writing_focus = [str(section.get("section_goal") or "完成章节重点")]
    content_functions = [
        str(value).strip()
        for value in section.get("content_functions") or []
        if str(value).strip()
    ]
    if not content_functions:
        content_functions = [
            str(section.get("section_role") or "section_goal")
        ]
    work_chain_roles = [WRITER_NO_CHAIN_ROLE]
    for chain in work_chains:
        chain_id = str(chain.get("chain_id") or "")
        if chain_id and chain_id not in work_chain_roles:
            work_chain_roles.append(chain_id)
    return {
        "schema_version": WRITER_EXECUTION_CONTRACT_SCHEMA_VERSION,
        "heading_contract": {
            "forbidden_exact_copy": [theme] if theme else [],
            "must_summarize": ["section_goal", "writing_focus"],
        },
        "plan_steps": [
            {
                "plan_step": f"{section_id}-STEP-{index:02d}",
                "instruction": instruction,
                "order": index,
            }
            for index, instruction in enumerate(evidence_usage_steps, 1)
        ],
        "expansion_stages": [
            {
                "expansion_stage": stage,
                "order": index,
            }
            for index, stage in enumerate(expansion_path, 1)
        ],
        "writing_focus_roles": [
            {
                "writing_focus_role": f"{section_id}-FOCUS-{index:02d}",
                "focus": focus,
                "order": index,
            }
            for index, focus in enumerate(writing_focus, 1)
        ],
        "allowed_content_functions": content_functions,
        "allowed_work_chain_roles": work_chain_roles,
        "max_consecutive_fact_units": 2,
        "required_coverage": {
            "all_plan_steps": True,
            "all_expansion_stages": True,
            "all_writing_focus_roles": True,
            "all_content_functions": True,
            "work_chain_order": True,
        },
    }


def build_writer_plan_control_view(
    *,
    content_plan: dict[str, Any],
    work_summary_execution_policy: dict[str, Any],
    evidence_group_pack: dict[str, Any],
    writing_structure_overlay: dict[str, Any] | None = None,
    frozen_article_plan: Any | None = None,
) -> dict[str, Any]:
    """Build the complete, fact-safe planning projection consumed by Writer."""

    frozen_payload: dict[str, Any] = {}
    if frozen_article_plan is not None:
        frozen_article_plan.assert_integrity()
        frozen_payload = frozen_article_plan.to_dict()
    canonical_plan = copy.deepcopy(
        frozen_payload.get("content_plan") or content_plan
    )
    capacity_budget = copy.deepcopy(frozen_payload.get("capacity_budget") or {})
    frozen_chains = [
        chain
        for chain in (frozen_payload.get("work_chain_pack") or {}).get("chains") or []
        if isinstance(chain, dict)
    ]
    chains_by_section: dict[str, list[dict[str, Any]]] = {}
    for chain in frozen_chains:
        chains_by_section.setdefault(str(chain.get("section_id") or ""), []).append(chain)

    policy_sections = {
        str(section.get("section_id") or ""): section
        for section in work_summary_execution_policy.get("sections") or []
        if isinstance(section, dict)
    }
    overlay_sections = {
        str(section.get("section_id") or ""): section
        for section in (writing_structure_overlay or {}).get("sections") or []
        if isinstance(section, dict)
    }
    group_ids_by_section = evidence_group_pack.get("group_ids_by_section") or {}
    binding_index = _writer_binding_index(evidence_group_pack)
    sections: list[dict[str, Any]] = []
    for section in canonical_plan.get("sections") or []:
        if not isinstance(section, dict):
            continue
        section_id = str(section.get("section_id") or "")
        policy = policy_sections.get(section_id) or {}
        subsection_source = list(section.get("subsections") or [])
        if not subsection_source:
            subsection_source = list(
                (overlay_sections.get(section_id) or {}).get("subsections") or []
            )
        raw_chains = [
            chain for chain in section.get("work_chain_summaries") or []
            if isinstance(chain, dict)
        ] or chains_by_section.get(section_id, [])
        theme = str(
            section.get("business_block") or section.get("section_purpose") or ""
        )
        work_chains = [
            _writer_work_chain(
                chain,
                section_id=section_id,
                binding_index=binding_index,
            )
            for chain in raw_chains
        ]
        sections.append({
            "section_id": section_id,
            "section_role": str(section.get("section_role") or ""),
            "theme": theme,
            "purpose": str(section.get("section_purpose") or ""),
            "priority": copy.deepcopy(section.get("importance") or section.get("priority") or ""),
            "section_goal": copy.deepcopy(section.get("section_goal") or ""),
            "writing_focus": copy.deepcopy(list(section.get("writing_focus") or [])),
            "expansion_path": copy.deepcopy(list(section.get("expansion_path") or [])),
            "content_functions": copy.deepcopy(list(section.get("content_functions") or [])),
            "evidence_usage_plan": _writer_evidence_usage_plan(
                section.get("evidence_usage_plan"),
                section_id=section_id,
                binding_index=binding_index,
            ),
            "reference_structure_guidance": copy.deepcopy(
                section.get("reference_structure_guidance") or {}
            ),
            "subsections": copy.deepcopy(subsection_source),
            "work_chain_organization": work_chains,
            "execution_contract": _writer_execution_contract(
                section,
                section_id=section_id,
                theme=theme,
                work_chains=work_chains,
            ),
            "length_control": {
                "requested_word_budget": int(policy.get("requested_word_budget") or 0),
                "capacity_status": str(policy.get("capacity_status") or ""),
                "recommended_capacity_aware_word_budget": int(
                    policy.get("recommended_capacity_aware_word_budget") or 0
                ),
                "capacity_aware_budget_is_soft_guidance": bool(
                    policy.get("capacity_aware_budget_is_soft_guidance", True)
                ),
                "section_length_is_hard_requirement": bool(
                    policy.get("section_length_is_hard_requirement", False)
                ),
            },
            "evidence_group_ids": list(group_ids_by_section.get(section_id) or []),
        })
    return {
        "schema_version": WRITER_PLAN_CONTROL_SCHEMA_VERSION,
        "source_role": (
            "FROZEN_ARTICLE_PLAN_VALIDATED_READ_ONLY"
            if frozen_payload else "CONTENT_PLAN_COMPATIBILITY_READ_ONLY"
        ),
        "frozen_plan_sha256": str(
            frozen_payload.get("frozen_plan_sha256") or ""
        ),
        "plan_id": str(canonical_plan.get("plan_id") or ""),
        "planner_owner": str(canonical_plan.get("planner_owner") or ""),
        "document_type": str(frozen_payload.get("document_type") or ""),
        "planning_basis": copy.deepcopy(canonical_plan.get("planning_basis") or {}),
        "required_structure_roles": copy.deepcopy(
            list(canonical_plan.get("required_structure_roles") or [])
        ),
        "length_control": {
            "target_total": int(canonical_plan.get("target_total") or 0),
            "requested_target_words": int(
                work_summary_execution_policy.get("requested_target_words") or 0
            ),
            "capacity_gap": str(work_summary_execution_policy.get("capacity_gap") or ""),
            "supported_word_range_estimate": copy.deepcopy(
                work_summary_execution_policy.get("supported_word_range_estimate")
                or capacity_budget.get("evidence_supported_word_range")
                or {}
            ),
            "capacity_estimate_kind": str(
                work_summary_execution_policy.get("capacity_estimate_kind")
                or capacity_budget.get("estimate_kind")
                or ""
            ),
            "estimate_is_binding": bool(
                work_summary_execution_policy.get("estimate_is_binding", False)
            ),
        },
        "sections": sections,
    }


def build_writer_plan_injection_report(
    writer_plan_control: dict[str, Any],
) -> dict[str, Any]:
    section_reports: list[dict[str, Any]] = []
    all_missing: list[dict[str, str]] = []
    for section in writer_plan_control.get("sections") or []:
        section_id = str(section.get("section_id") or "")
        missing = [
            field for field in REQUIRED_WRITER_PLAN_SECTION_FIELDS
            if field not in section
        ]
        all_missing.extend(
            {"section_id": section_id, "field": field} for field in missing
        )
        section_reports.append({
            "section_id": section_id,
            "writer_section_fields": sorted(section),
            "missing_required_fields": missing,
            "subsection_count": len(section.get("subsections") or []),
            "work_chain_count": len(section.get("work_chain_organization") or []),
        })
    serialized = json.dumps(writer_plan_control, ensure_ascii=False, sort_keys=True)
    return {
        "schema_version": "writer-plan-injection-report-1",
        "source_role": str(writer_plan_control.get("source_role") or ""),
        "frozen_plan_sha256": str(writer_plan_control.get("frozen_plan_sha256") or ""),
        "before_writer_section_fields": list(LEGACY_WRITER_PLAN_SECTION_FIELDS),
        "required_injected_section_fields": list(REQUIRED_WRITER_PLAN_SECTION_FIELDS),
        "writer_plan_top_level_fields": sorted(writer_plan_control),
        "section_reports": section_reports,
        "missing_required_fields": all_missing,
        "complete_plan_control_injected": not all_missing,
        "internal_evidence_ids_exposed": '"evidence_id"' in serialized,
        "writer_binding_identifier_contract": "group_id_and_fact_unit_id",
        "evidence_binding_contract_changed": False,
    }


def _build_work_summary_writer_prompt(
    *,
    task_fields: dict[str, Any],
    content_plan: dict[str, Any],
    fact_anchor_pack: dict[str, Any],
    work_summary_execution_policy: dict[str, Any],
    writing_style_guidance_pack: dict[str, Any] | None,
    evidence_group_pack: dict[str, Any],
    writing_structure_overlay: dict[str, Any] | None = None,
    frozen_article_plan: Any | None = None,
) -> str:
    """Render the compact Article Plan + Evidence Groups Writer contract."""
    frozen_plan_view = build_writer_plan_control_view(
        content_plan=content_plan,
        work_summary_execution_policy=work_summary_execution_policy,
        evidence_group_pack=evidence_group_pack,
        writing_structure_overlay=writing_structure_overlay,
        frozen_article_plan=frozen_article_plan,
    )
    protocol = {
        "title": "文章标题",
        "sections": [
            {
                "section_id": "SEC-01",
                "heading": "部分标题",
                "sentences": [
                    {
                        "sentence_type": "FACT",
                        "group_id": "EG-001",
                        "fact_unit_ids": ["EG-001-U01", "EG-001-U02"],
                        "plan_step": "SEC-01-STEP-01",
                        "writing_focus_role": "SEC-01-FOCUS-01",
                        "expansion_stage": "事实依据",
                        "content_function": "fact_basis",
                        "work_chain_role": "CHAIN-01",
                        "text": "由同组合法事实综合写成的成熟段落",
                    },
                    {
                        "sentence_type": "ANALYSIS",
                        "group_id": "",
                        "fact_unit_ids": [],
                        "plan_step": "SEC-01-STEP-02",
                        "writing_focus_role": "SEC-01-FOCUS-01",
                        "expansion_stage": "措施过程",
                        "content_function": "process",
                        "work_chain_role": WRITER_NO_CHAIN_ROLE,
                        "text": "必要且克制的归纳或承接",
                    },
                ],
            }
        ],
    }
    style_view = _compact_work_summary_style_reference(
        content_plan,
        writing_style_guidance_pack,
    )
    return "\n".join([
        "【任务】",
        json.dumps(task_fields, ensure_ascii=False, separators=(",", ":")),
        "",
        "【文章规划】",
        "规划只决定各节写什么、主次、顺序和篇幅。保持section数量、ID和顺序，不自行增删章节。",
        json.dumps(frozen_plan_view, ensure_ascii=False, separators=(",", ":")),
        "",
        "【规划执行合同】",
        "文章规划中每节的execution_contract是强制执行合同，不是参考信息。每个内容单元必须填写plan_step、writing_focus_role、expansion_stage、content_function、work_chain_role；字段值只能从本节execution_contract对应清单逐字复制，不得自造、改写或跨节使用。没有工作链归属时work_chain_role只能填NO_CHAIN。",
        "必须覆盖本节全部plan_steps、expansion_stages、writing_focus_roles和allowed_content_functions，并分别按plan_steps、expansion_stages及work_chain_organization给定顺序展开。plan_step和expansion_stage必须分别从对应清单逐字复制。可靠素材组只决定事实来源，不能代替章节目标、标题或文章提纲。",
        "标题必须根据section_goal和writing_focus重新概括；禁止直接复制theme、business_block或可靠素材组名称作为标题，即使添加序号或标点也不允许。段落应依规划形成事实依据、措施过程、工作成效或问题判断、意义提升等必要推进；不得为了凑齐阶段虚构事实或把计划写成完成态。",
        "不得出现三个及以上连续FACT。确需承接、说明过程逻辑或总结提升时使用ANALYSIS；ANALYSIS不得引入新事实，且group_id、fact_unit_ids必须为空。",
        "",
        "【可靠素材组】",
        "每组是同一业务主题下已经清洗、验真的材料。可把多个事实写成一个完整句子或成熟段落，但只能合并同组事实；不要逐条照抄。FACT只返回该段实际采用的group_id和fact_unit_ids，不得生成或抄写evidence_id。组内未表达的fact_unit_id不得声明为已使用。",
        json.dumps(writer_visible_evidence_groups(evidence_group_pack), ensure_ascii=False, separators=(",", ":")),
        "",
        "【事实边界】",
        "只能使用素材组中已有的事实、数字、时间、主体、状态和结果；不得补写或推断组外现实信息。PLAN/RECOMMENDATION不得写成已完成，PROBLEM/GAP不得写成成绩。素材不足时收敛表达，不用空泛分析凑字数。",
        "",
        "【写作要求】",
        "写成可交付的成熟工作总结：开头简洁统领，主体有主次和工作链，小标题概括准确，段落充分展开并自然过渡，问题具体克制，下一步与前文承接。避免流水账、机械排比、同义重复、解释口吻和事实清单。",
        json.dumps(style_view, ensure_ascii=False, separators=(",", ":")),
        "",
        "【输出合同】",
        "只输出一个JSON对象，不输出Markdown或解释。根字段为title、sections；每节为section_id、heading、sentences；每个内容单元必须且只能包含sentence_type、group_id、fact_unit_ids、plan_step、writing_focus_role、expansion_stage、content_function、work_chain_role、text。FACT必须返回一个同节group_id及本段实际使用的一个或多个fact_unit_id；ANALYSIS的group_id为空字符串、fact_unit_ids为空数组且不得引入新事实。程序将确定性绑定真实evidence_id，并验证规划字段、标题、展开顺序、工作链顺序和FACT连续堆叠。",
        json.dumps(protocol, ensure_ascii=False, separators=(",", ":")),
        "",
        "输出前核对：文章完整成熟；每个FACT只陈述其fact_unit_ids共同支持的内容；数字、时间、机构、项目、状态、因果和结果均未越界；无×、XX、待补和残句。",
    ])


def build_writing_structure_overlay(
    content_plan: dict[str, Any],
    document_type: str,
) -> dict[str, Any]:
    """Derive fact-free subsection organization without mutating Content Plan."""
    empty = {
        "schema_version": "writing-structure-overlay-1",
        "sections": [],
    }
    if not requires_report_introduction(document_type):
        return empty
    reference = content_plan.get("reference_structure")
    if (
        not isinstance(reference, dict)
        or reference.get("schema_version") != "reference-structure-2"
        or reference.get("status") != "EXTRACTED"
    ):
        return empty
    reference_sections = list(reference.get("primary_structure") or [])
    plan_sections = list(content_plan.get("sections") or [])
    overlay_sections: list[dict[str, Any]] = []
    for index, planned in enumerate(plan_sections):
        if index >= len(reference_sections):
            break
        section_id = str(planned.get("section_id") or "")
        source_subsections = list(
            reference_sections[index].get("subsections") or []
        )
        subsections: list[dict[str, Any]] = []
        for expected_order, subsection in enumerate(
            source_subsections,
            start=1,
        ):
            if not isinstance(subsection, dict):
                continue
            subsection_id = str(subsection.get("subsection_id") or "")
            if (
                subsection_id
                != f"{section_id}-SUB-{expected_order:02d}"
            ):
                continue
            subsections.append(
                {
                    "subsection_id": subsection_id,
                    "topic": str(subsection.get("topic") or ""),
                    "function": str(subsection.get("function") or ""),
                    "order": expected_order,
                }
            )
        if subsections:
            overlay_sections.append(
                {
                    "section_id": section_id,
                    "subsections": subsections,
                }
            )
    return {
        "schema_version": "writing-structure-overlay-1",
        "sections": overlay_sections,
    }


def build_atomic_new_draft_prompt(
    task: Any,
    evidence_pack: dict[str, Any],
    content_plan: dict[str, Any] | None = None,
    selected_evidence_outbound: list[dict[str, Any]] | None = None,
    fact_anchor_pack: dict[str, Any] | None = None,
    writing_structure_overlay: dict[str, Any] | None = None,
    writing_style_guidance_pack: dict[str, Any] | None = None,
    evidence_group_pack: dict[str, Any] | None = None,
    frozen_article_plan: Any | None = None,
) -> str:
    """Render the V3.3 structured-draft request with no source prose."""
    task_fields = {
        "title": _value(task.title),
        "document_type": _value(task.document_type),
        "writing_purpose": _value(task.writing_purpose),
        "business_domain": _value(task.business_domain),
        "level": _value(task.level),
        "org_type": _value(task.org_type),
        "target_words": int(task.target_words),
        "reference_scope": _value(task.reference_scope),
    }
    content_plan = content_plan or build_content_plan(task, evidence_pack)
    is_work_summary = (
        str(task.document_type or "").strip() == "工作总结"
        and is_work_summary_content_plan(content_plan)
    )
    work_summary_execution_policy = (
        build_work_summary_execution_policy(
            content_plan,
            document_type=str(task.document_type or ""),
        )
        if is_work_summary
        else None
    )
    prompt_content_plan = copy.deepcopy(content_plan)
    bound_ids = {
        evidence_id
        for section in content_plan.get("sections") or []
        for evidence_id in (
            list(section.get("selected_evidence_ids") or [])
            or (
                list(section.get("required_evidence_ids") or [])
                + list(section.get("optional_evidence_ids") or [])
            )
        )
    }
    if selected_evidence_outbound is None:
        by_id = {str(item.get("evidence_id") or ""): item for item in evidence_pack.get("evidence") or []}
        selected_evidence_outbound = [
            build_outbound_evidence_record(by_id[evidence_id])
            for evidence_id in sorted(bound_ids) if evidence_id in by_id
        ]
    if fact_anchor_pack is None:
        # Compatibility for local V3.2 maintenance callers. Production V3.3
        # always supplies the already validated pack built after selection.
        section_by_id = {
            evidence_id: str(section.get("section_id") or "")
            for section in content_plan.get("sections") or []
            for evidence_id in section.get("selected_evidence_ids") or []
        }
        core_ids = {
            evidence_id
            for section in content_plan.get("sections") or []
            for evidence_id in section.get("core_evidence_ids") or []
        }
        fact_anchor_pack = {
            "fact_anchors": [
                {
                    "evidence_id": item.get("evidence_id", ""),
                    "section_id": section_by_id.get(item.get("evidence_id"), ""),
                    "role": "core" if item.get("evidence_id") in core_ids else "supporting",
                    "canonical_subject": item.get("canonical_subject", ""),
                    "canonical_action": item.get("canonical_action", ""),
                    "canonical_object": item.get("canonical_object", ""),
                    "exact_values": item.get("exact_values") or {},
                    "forbidden_detail_types": [],
                }
                for item in selected_evidence_outbound
                if item.get("canonical_subject") not in {"相关责任主体", "有关方面", "各方", "相关单位"}
                and item.get("canonical_action") not in {"反映", "呈现"}
            ],
            "analysis_support_pack": [
                {
                    "evidence_id": item.get("evidence_id", ""),
                    "section_id": section_by_id.get(item.get("evidence_id"), ""),
                    "category": item.get("category", ""),
                    "canonical_subject": item.get("canonical_subject", ""),
                    "canonical_action": item.get("canonical_action", ""),
                    "canonical_object": item.get("canonical_object", ""),
                }
                for item in selected_evidence_outbound
                if item.get("canonical_subject") in {"相关责任主体", "有关方面", "各方", "相关单位"}
                or item.get("canonical_action") in {"反映", "呈现"}
            ],
        }
    if is_work_summary:
        assert work_summary_execution_policy is not None
        evidence_group_pack = evidence_group_pack or build_work_summary_evidence_groups(
            content_plan,
            fact_anchor_pack,
        )
        return _build_work_summary_writer_prompt(
            task_fields=task_fields,
            content_plan=content_plan,
            fact_anchor_pack=fact_anchor_pack,
            work_summary_execution_policy=work_summary_execution_policy,
            writing_style_guidance_pack=writing_style_guidance_pack,
            evidence_group_pack=evidence_group_pack,
            writing_structure_overlay=writing_structure_overlay,
            frozen_article_plan=frozen_article_plan,
        )
    genre_boundary = _fact_anchored_genre_boundary(task.document_type)
    target_total = int(content_plan.get("target_total") or 0)
    length_range = content_plan.get("recommended_length_range") or {}
    introduction_contract = build_introduction_contract(
        task.document_type,
        target_total,
        list(content_plan.get("sections") or []),
    )
    section_budget_values = list(introduction_contract["section_body_budgets"])
    section_words = {
        str(section.get("section_id") or ""): int(section_budget_values[index])
        for index, section in enumerate(content_plan.get("sections") or [])
    }
    legal_fact_evidence_ids_by_section: dict[str, list[str]] = {}
    for anchor in fact_anchor_pack.get("fact_anchors") or []:
        section_id = str(anchor.get("section_id") or "")
        evidence_id = str(anchor.get("evidence_id") or "")
        if section_id and evidence_id:
            legal_fact_evidence_ids_by_section.setdefault(section_id, []).append(evidence_id)
    legal_fact_evidence_ids_by_section = {
        section_id: sorted(set(evidence_ids))
        for section_id, evidence_ids in sorted(legal_fact_evidence_ids_by_section.items())
    }
    required_core_anchor_ids_by_section: dict[str, list[str]] = {}
    for anchor in fact_anchor_pack.get("fact_anchors") or []:
        if str(anchor.get("role") or "") != "core":
            continue
        section_id = str(anchor.get("section_id") or "")
        evidence_id = str(anchor.get("evidence_id") or "")
        if section_id and evidence_id:
            required_core_anchor_ids_by_section.setdefault(section_id, []).append(evidence_id)
    required_core_anchor_ids_by_section = {
        section_id: sorted(set(evidence_ids))
        for section_id, evidence_ids in sorted(required_core_anchor_ids_by_section.items())
    }
    section_execution_contract: dict[str, dict[str, Any]] = {}
    for section in content_plan.get("sections") or []:
        section_id = str(section.get("section_id") or "")
        core_anchor_count = len(required_core_anchor_ids_by_section.get(section_id, []))
        if is_work_summary:
            fact_required = str(section.get("evidence_policy") or "") == "fact_required"
            section_execution_contract[section_id] = {
                "requested_word_budget": int(section_words.get(section_id) or 0),
                "capacity_status": str(section.get("capacity_status") or ""),
                "recommended_capacity_aware_word_budget": int(
                    section.get("recommended_capacity_aware_word_budget") or 0
                ),
                "capacity_aware_budget_is_soft_guidance": True,
                "minimum_paragraphs": (
                    max(1, core_anchor_count) if fact_required else 1
                ),
                "paragraphs_must_be_nonempty": True,
                "minimum_distinct_paragraph_functions": (
                    2 if core_anchor_count >= 2 else 1
                ),
                "section_length_is_hard_requirement": False,
            }
        else:
            section_execution_contract[section_id] = {
                "recommended_words": int(section_words.get(section_id) or 0),
                "minimum_paragraphs": max(2, core_anchor_count),
                "paragraphs_must_be_nonempty": True,
                "minimum_distinct_paragraph_functions": 2,
            }
    writing_reference = _build_writing_reference(
        prompt_content_plan,
        writing_style_guidance_pack=writing_style_guidance_pack,
    )
    writing_structure_overlay = (
        writing_structure_overlay
        if writing_structure_overlay is not None
        else build_writing_structure_overlay(
            content_plan,
            task.document_type,
        )
    )
    overlay_applied = bool(
        writing_structure_overlay.get("sections")
    )
    uses_reference_structure = bool(
        content_plan.get("uses_reference_structure")
        and content_plan.get("reference_structure")
    )
    structure_planning_rule = (
        "文章结构根据用户标题、文种、写作目的、既有文种边界和【D3. Writing Reference】中的抽象Reference Structure重新规划；Reference Structure只提供章节角色、展开顺序和段落功能。"
        if uses_reference_structure
        else "文章结构只能根据用户标题、文种、写作目的和既有文种边界重新规划；事实记录只限定可写内容。"
    )
    reference_execution_rule = (
        "2e. 按reference_structure_guidance吸收范文的章节角色、二级展开逻辑和段落功能；不得复制来源标题或原句，不得把结构标签写成现实事实。结构指导与Fact Anchor冲突时必须服从Fact Anchor和FACT/ANALYSIS规则。"
        if uses_reference_structure
        else "2e. 本次没有可用Reference Structure，按既有section规划正常生成。"
    )
    source_copy_rule = (
        "20. 不得复制来源标题、章节标题或原句；只能使用Reference Structure中已经抽象化的章节角色、展开顺序和段落功能，不得从分析支持字段或结构参考制造FACT句。"
        if uses_reference_structure
        else "20. 不得复制来源结构或原句，不得从分析支持字段制造FACT句。"
    )
    planning_execution_rule = (
        "2d. 每个section必须按section_purpose和business_block完成实际内容职能，优先覆盖全部合法core Fact Anchor并执行Work Chain组织指令；requested_word_budget和capacity-aware budget均不得驱动机械扩写。"
        if is_work_summary
        else "2d. 每个section必须按content_plan.sections[n].section_goal确定本章节写作目的，按writing_focus确定本章节重点，按expansion_path安排段落推进顺序，按evidence_usage_plan安排core/supporting证据使用；不得只按recommended_words机械扩写。"
    )
    protocol: dict[str, Any] = {
        "title": "文章标题",
    }
    if introduction_contract["required"]:
        protocol["introduction"] = (
            "说明报告时间范围、总体要求和总体推进情况，并自然引出下文的完整自然段。"
        )
    paragraph_protocol = {
        "evidence_id": (
            "fact_required复制本section合法ID；"
            "analysis_only必须为空字符串"
        ),
        "fact_text": (
            "fact_required写锚定事实；analysis_only必须为空字符串"
        ),
        "expansion_text": (
            "围绕本section目标展开安全的一般分析或同一事实的过程逻辑。"
        ),
        "transition_text": "使用公文衔接表达推进本部分。",
        "summary_text": (
            "作一般性总结或未来态部署，不写未提供的现实成效。"
        ),
    }
    if overlay_applied:
        protocol["sections"] = [
            {
                "section_id": "SEC-01",
                "heading": "部分标题",
                "subsections": [
                    {
                        "subsection_id": "SEC-01-SUB-01",
                        "lead": "一是动态生成的分项引领语。",
                        "paragraphs": [paragraph_protocol],
                    }
                ],
            }
        ]
    else:
        protocol["sections"] = [
            {
                "section_id": "SEC-01",
                "heading": "部分标题",
                "paragraphs": [paragraph_protocol],
            }
        ]
    subsection_prompt_rules = (
        [
            "【D4. Writing Structure Overlay】",
            (
                "Content Plan决定section内容边界和合法事实；"
                "Writing Structure Overlay只决定同一section内部的分项组织。"
            ),
            (
                "Overlay不得改变section级Evidence、Fact Anchor、"
                "evidence_id合法清单或使用次数。"
            ),
            json.dumps(
                writing_structure_overlay,
                ensure_ascii=False,
                indent=2,
            ),
            "",
        ]
        if overlay_applied
        else []
    )
    subsection_hard_rules = (
        [
            (
                "2f. 对Overlay中列出的section，必须输出subsections数组；"
                "subsection_id、数量和顺序必须与Overlay完全一致，"
                "不得新增、跨section移动或合并分项。"
            ),
            (
                "2g. 每个subsection必须承担独立内容功能，不得把多个分项"
                "混成一个泛化大段；lead须动态生成“一是、二是、三是”等"
                "自然引领语，不得复制参考材料的完整二级标题或原句。"
            ),
            (
                "2h. subsection内的FACT仍只按父section_id对应的合法"
                "evidence_id清单校验；不得创建subsection级白名单、重新绑定"
                "Evidence或跨section借用锚点。"
            ),
            (
                "2i. 有合法事实时优先写清做了什么、围绕什么推进；"
                "无合法事实的分项只能使用分析态、应然态或未来态，"
                "不得写成本地已经完成的工作。"
            ),
            (
                "2j. 不得为填满分项新增部门、文件、次数、制度、职责、"
                "会议、成果或残缺数量，不得输出“*次”或“共召开但无数量”。"
            ),
            (
                "2k. 控制“这一做法、有助于、进一步提升、奠定基础”等"
                "解释性套话；不得用意义分析替代全部材料内容。"
            ),
        ]
        if overlay_applied
        else []
    )
    introduction_prompt_rules = (
        [
            "【B2. 报告导语与正文预算合同】",
            json.dumps(introduction_contract, ensure_ascii=False, indent=2),
            (
                "本次报告类文种必须输出introduction字符串，字段顺序位于title与sections之间；"
                "introduction只写一个不带“导语”“前言”“背景介绍”等标签的完整自然段。"
            ),
            (
                f"导语目标约{int(introduction_contract['target_length'])}个汉字，"
                f"合格范围为{int(introduction_contract['accepted_range']['min_chars'])}"
                f"—{int(introduction_contract['accepted_range']['max_chars'])}个汉字；"
                "全文字数包含导语。"
            ),
            (
                "导语只说明任务提供的时间范围、总体要求或政治方向、审慎的总体推进概况，"
                "并自然引出后续各部分；不得提前展开各部分详细内容，不得与第一部分重复。"
            ),
            (
                "导语是ANALYSIS类文本，不得包含evidence_id、fact_text或任何FACT协议，"
                "不得新增数字、次数、会议、文件、机构、职责、制度、具体措施、现实成果或完成态结论。"
            ),
            (
                f"五个sections共同按body_target={int(introduction_contract['body_target'])}执行；"
                f"各部分本次正文预算为{json.dumps(section_words, ensure_ascii=False)}。"
                "该换算只用于本次Prompt和完成度检查，不修改原始Content Plan运行产物。"
            ),
            "",
        ]
        if introduction_contract["required"]
        else []
    )
    if is_work_summary:
        assert work_summary_execution_policy is not None
        length_mode = str(work_summary_execution_policy["length_mode"])
        length_prompt_rules = [
            "【B. 工作总结 Capacity-Aware 篇幅执行策略】",
            "WORK_SUMMARY_EXECUTION_POLICY:",
            json.dumps(
                work_summary_execution_policy,
                ensure_ascii=False,
                indent=2,
            ),
            (
                f"用户 requested target={int(task.target_words)} 字，表示用户期望篇幅；"
                "必须原样保留该意图，不得把 task.target_words 静默改写为 supported estimate。"
            ),
            (
                "Capacity 只决定是否继续追逐用户目标篇幅；supported estimate、"
                "supported range 与 section capacity-aware budget 均为 non-binding "
                "ENGINEERING_ESTIMATE，不是正文 exact target、hard minimum 或 hard maximum。"
            ),
            (
                "TARGET_ORIENTED：材料没有明显容量不足信号，正常以用户 target 为写作方向，"
                "但不得为最后的长度差距重复事实、增加空话或理论填充。"
            ),
            (
                "DENSITY_FIRST：用户 target 是期望值；事实覆盖、Work Chain 连续、"
                "段落成熟和必要分析优先，不得为达到 requested target 强制扩写。"
            ),
            (
                "MATERIAL_LIMITED：允许正文显著短于 requested target；必须优先保证"
                "事实真实、信息密度和结构完整，禁止通过意义、理论、规律、启示、"
                "机制泛化或重复总结补足篇幅。"
            ),
            f"本次 length_mode={length_mode}。",
            "各 section 的 requested_word_budget 仅保留用户篇幅意图；capacity-aware budget 只能作软性组织参考，不得要求逐节填满。",
            "SECTION_EXECUTION_CONTRACT:",
            json.dumps(section_execution_contract, ensure_ascii=False, indent=2),
            "",
        ]
        section_execution_rules = [
            "2c. 每个section应先完整覆盖计划职能和合法Fact Anchor，再形成成熟自然段；不得把requested_word_budget或capacity-aware budget当作必须填满的逐节硬命令。",
            "2c-1. 必须逐section执行SECTION_EXECUTION_CONTRACT：所有计划section和paragraph均不得为空；fact_required部分的段落数量主要由合法core Fact Anchor决定，analysis_only部分只写任务与材料确有依据的必要内容。",
            "2c-2. 不得按第二、第三个work_progress或任何固定section位置追加段落；结构、段落数量和内容职能只服从Dynamic Planner、合法Fact Anchor与本section实际任务。",
            "2c-3. 输出前复核标题、section完整性、core Fact Anchor覆盖和事实安全；length_mode为DENSITY_FIRST或MATERIAL_LIMITED时，不得因低于requested target而补写ANALYSIS。",
        ]
        fact_short_anchor_rule = (
            "6b. 锚点原文较短时，必须保持fact_text短而准确；不得通过扩写FACT或追加ANALYSIS来追逐requested target。"
        )
        analysis_execution_rules = [
            "12b. 工作总结中的ANALYSIS只允许承担极短背景衔接、工作链之间的逻辑连接、基于前文事实的克制归纳、非事实性结构过渡、有材料依据的问题解释，以及有材料或任务依据的未来安排。",
            "13. 禁止把ANALYSIS作为篇幅填充器承担工作意义反复解释、方法意义泛化、管理启示扩写、可迁移经验泛化、一般规律长篇分析、“为什么重要”式理论补充、同一事实多轮解释或可替换到任何单位的万能段落。",
            "13a. analysis_only问题或下一步部分不得因预算高而膨胀；禁止用“加强学习、完善机制、提升能力、强化创新”等万能问题或万能安排凑篇幅。",
            "14. 一般分析不得替代FACT承担现实事实描述；任何现实状态、完成结果、实际效果必须由fact_text使用合法evidence_id提供证据支持。",
        ]
        work_chain_rules = [
            "17. 对WORK_SUMMARY_EXECUTION_POLICY中ordered_evidence_ids非空的ORDER_AVAILABLE chain，必须让对应FACT paragraph保持相邻并按所列顺序连续组织；每个evidence_id仍是独立FACT paragraph和独立Fact Anchor。",
            "18. 不得因属于同一Work Chain而把多个Anchor合并成一个新FACT，不得把多个evidence_id拼成一个事实句。多个合法事实存在时，优先让FACT自身承担正文厚度，禁止用大段ANALYSIS把FACT彼此隔开。",
            "19. 对ORDER_UNAVAILABLE chain，只能视为同一业务板块的相关事实集合；按section purpose和正常写作逻辑保守组织，严禁自行推断时间先后、因果先后或治理闭环。",
        ]
        closing_length_rule = (
            "21. 可以进行必要且克制的一般分析；每个部分只能使用该部分绑定的evidence_id。"
            "不得因FACT约束、低于requested target或低于section软预算而增加理论、意义、规律、万能问题或万能下一步。"
        )
    else:
        length_prompt_rules = [
            "【B. 本次唯一有效篇幅命令】",
            f"用户明确输入的target_words={int(task.target_words)}是本次实际篇幅控制的唯一权威。",
            f"content_plan.target_total必须等于用户target_words；本次二者均为{target_total}字。",
            f"必须以content_plan.target_total={target_total}和本次各部分正文预算={json.dumps(section_words, ensure_ascii=False)}为唯一实际篇幅执行标准。",
            f"必须在{int(length_range.get('min_words') or 0)}—{int(length_range.get('max_words') or 0)}字范围内生成，本次有效target_total为{target_total}字。",
            "各section的recommended_words是实际写作预算，必须通过内容组织和段落充分展开落实，不得静默降档。",
            "各section预算均为非零硬约束；任何一个必需部分不得为空、不得只剩标题、不得用一个薄段代替完整展开。",
            "SECTION_EXECUTION_CONTRACT:",
            json.dumps(section_execution_contract, ensure_ascii=False, indent=2),
            "",
        ]
        section_execution_rules = [
            "2c. 每个section应围绕recommended_words组织段落数量和段落长度；不得明显低于章节目标字数。内容厚度只能通过展开已有措施的组织逻辑、工作衔接、一般性风险分析、经验启示、未来工作安排、自然过渡和总结收束形成。",
            "2c-1. 必须逐section执行SECTION_EXECUTION_CONTRACT：paragraphs数量不得低于minimum_paragraphs，每个paragraph必须有非空正文，不同paragraph必须承担至少两种不同内容功能，不得同义改写上一段。",
            "2c-2. 第二、第三个work_progress部分均不得退化为单个正文段；应分别围绕其writing_focus和expansion_path形成至少两个内容功能不同的自然段。",
            "2c-3. 输出前按“标题、各部分标题、各paragraph正文”计算实际正文长度并逐节复核；总长度和各部分预算必须同时满足，不得以JSON标点、字段名或重复内容计入正文篇幅。",
        ]
        fact_short_anchor_rule = (
            "6b. 锚点原文较短时，必须保持fact_text短而准确；章节篇幅只能由安全的一般分析、组织逻辑、衔接递进、审慎问题分析和未来态安排补足，不得通过扩写FACT补足字数。"
        )
        analysis_execution_rules = [
            "12b. 需要补足章节预算时，优先写一般工作逻辑、风险约束、环节衔接、方法要求、条件分析和常态化取向；仍无法安全展开时宁可使用明确的建议态、应然态或未来态，也不得把推测写成已经发生的本地事实。",
            "13. 一般分析可以表达工作意义总结、方法总结、管理启示、工作机制分析、可迁移经验、逻辑推理、方法解释、经验提炼、公文常用提升表达或一般规律；不得表达已发生现实结果、已验证工作效果、已完成影响或当前材料未提供的现实状态。",
            "14. 一般分析不得替代FACT承担现实事实描述；任何现实状态、完成结果、实际效果必须由fact_text使用合法evidence_id提供证据支持，段落展开只能解释其方法意义或推理价值。",
        ]
        work_chain_rules = [
            "17. 段落展开职责限制：仅用于方法意义分析、管理思路分析、工作机制解释、工作意义总结、可迁移经验提炼和启示总结；禁止描述已经发生的现实效果、已经完成的变化、已经验证的工作结果。",
            "18. 措施与结果分离：当写到某项工作措施、机制或做法时，段落展开可以回答“围绕什么重点组织推进”“结合什么逻辑承接上下文”“下一步应坚持什么工作取向”“哪些环节需要统筹贯通”；不得回答“该措施已经实现什么效果”“已经避免什么问题”“已经提升什么能力”。",
            "19. 段落展开输出转换：遇到“专班运作、统一框架、协同作业、联动机制、闭环管理、分级响应”等措施性主语时，不得续写现实结果；应转换为工作取向、组织推进、衔接递进、常态要求或机关材料常用总结提升表达，避免连续使用同一种解释句式。",
        ]
        closing_length_rule = (
            "21. 可以进行一般性原因分析和经验提炼；每个部分只能使用该部分绑定的evidence_id；不得因采用FACT约束而退化为明显低于recommended_words的提纲式短稿。"
        )
    return "\n".join([
        "【任务性质】",
        "本任务是依据本次用户要求起草一篇独立新文章，不是改写任何资料。",
        structure_planning_rule,
        "无论是否使用Reference Structure，事实记录只限定可写内容，所有现实事实均须经过Evidence和Fact Anchor链路。",
        "不得从事实记录推断目录，不得沿用记录排列顺序组织文章。",
        "",
        "【A. 用户八项参数】",
        json.dumps(task_fields, ensure_ascii=False, indent=2),
        "",
        *length_prompt_rules,
        *introduction_prompt_rules,
        "【C. 文种与任务边界】",
        genre_boundary,
        "",
        "【D. 确定性内容施工图】",
        "content_plan不仅是篇幅表，也是每个section的写作施工说明。生成每个section时，必须同时执行section_goal、writing_focus、expansion_path和evidence_usage_plan：先完成章节目标，再按展开路径把事实组织为“事实—过程说明—组织逻辑—必要总结”的正式公文段落。",
        "evidence_usage_plan只说明已绑定证据的使用顺序和角色，不得把其中的说明改写为新事实；所有现实事实仍只能来自【E. 本地事实锚点】和【G. FACT evidence_id只读清单】。",
        json.dumps(prompt_content_plan, ensure_ascii=False, indent=2),
        "",
        "【D2. Evidence paragraph support】",
        "The following records are still Evidence, but writing_support is derived only from atomic fields. Use it to understand paragraph role, expansion angles, and prohibited expansion. Do not treat writing_support as a new fact source.",
        json.dumps(selected_evidence_outbound, ensure_ascii=False, indent=2),
        "",
        "【D3. Writing Reference】",
        "Writing Reference only provides paragraph organization and official-document expression structure. It must never supply FACT evidence_id, numbers, time, organizations, duties, places, equipment, mechanisms, or results.",
        "Writing Reference may be used only for sentence pattern, section rhythm, transition style, and official-document wording. It is prohibited as a fact, data, event, result, mechanism, organization, or duty source.",
        "writing_style_increment, when present, is experimental writing guidance only. It is prohibited from supplying FACT text, evidence_id values, factual claims, completed actions, mechanisms, duties, results, or evidence judgment.",
        json.dumps(writing_reference, ensure_ascii=False, indent=2),
        "",
        *subsection_prompt_rules,
        "【E. 本地事实锚点】",
        json.dumps(fact_anchor_pack.get("fact_anchors") or [], ensure_ascii=False, indent=2),
        "",
        "【F. 分析支持字段】",
        json.dumps(fact_anchor_pack.get("analysis_support_pack") or [], ensure_ascii=False, indent=2),
        "",
        "【G. FACT evidence_id只读清单】",
        "以下清单是FACT段落唯一合法的evidence_id来源；content_plan中的required_evidence_ids、optional_evidence_ids、supporting_evidence_ids或任何其他ID字段都不得作为输出ID来源。",
        "MACHINE_CONSTRAINT: LEGAL_FACT_EVIDENCE_IDS_BY_SECTION. For every FACT paragraph, copy evidence_id exactly from the list for the same section_id only. Do not invent, modify, normalize, translate, abbreviate, or repair evidence_id values.",
        json.dumps(legal_fact_evidence_ids_by_section, ensure_ascii=False, indent=2),
        "MACHINE_CONSTRAINT: REQUIRED_CORE_ANCHOR_IDS_BY_SECTION. Every listed core evidence_id must appear exactly once as a FACT paragraph in its own section_id. Do not move it to another section and do not replace it with unanchored analysis.",
        json.dumps(required_core_anchor_ids_by_section, ensure_ascii=False, indent=2),
        "",
        "【H. 唯一输出协议】",
        json.dumps(protocol, ensure_ascii=False, indent=2),
        "",
        "【I. 结构化生成硬规则】",
        "1. 只输出一个JSON对象；除协议字段外不得输出其他字段或说明。",
        *(
            [
                "1a. 本次报告类输出根字段必须且只能是title、introduction、sections；introduction必须是字符串，位于title与sections之间。",
                "1b. introduction不得返回对象、数组或额外协议字段，不得包含evidence_id、fact_text或其他JSON协议残留。",
            ]
            if introduction_contract["required"]
            else []
        ),
        "2. sections的数量、section_id和顺序必须与content_plan完全一致。",
        (
            "2a. Overlay适用的section必须输出subsections数组且每个"
            "subsection输出paragraphs数组；Overlay不适用时保持section."
            "paragraphs旧协议。不得输出sentences数组；每个paragraph必须且"
            "只能包含evidence_id、fact_text、expansion_text、"
            "transition_text、summary_text五个字段。"
            if overlay_applied
            else
            "2a. 每个section必须输出paragraphs数组，不得继续输出sentences数组；每个paragraph必须且只能包含evidence_id、fact_text、expansion_text、transition_text、summary_text五个字段。"
        ),
        "2b. 每个paragraph是一个自然段生成单元。fact_required section的paragraph以fact_text提供FACT核心事实，其余字段作同事实安全展开；analysis_only section的paragraph必须令evidence_id和fact_text均为空，只用其余三个字段组织一般分析、未来部署和公文衔接。",
        *section_execution_rules,
        planning_execution_rule,
        reference_execution_rule,
        *subsection_hard_rules,
        "3. fact_required section的paragraph.evidence_id是只读字段，只能从【G. FACT evidence_id只读清单】复制已有ID原文；不得创建、改写、补全、推断、转换或格式化任何新ID。",
        "4. fact_required paragraph.evidence_id必须完全属于当前section_id对应的只读清单；analysis_only paragraph不得携带任何evidence_id或FACT文本。",
        "5. 不得把content_plan中的required_evidence_ids、optional_evidence_ids、supporting_evidence_ids当作可输出ID；FACT输出ID只以【G. FACT evidence_id只读清单】为准。",
        "5a. 输出前必须逐section核对LEGAL_FACT_EVIDENCE_IDS_BY_SECTION：paragraph.evidence_id与当前section清单做逐字符匹配；不在清单中的ID即使看起来相似也不得输出。",
        "5b. 输出前必须逐section核对REQUIRED_CORE_ANCHOR_IDS_BY_SECTION：每个core evidence_id都要在同section内出现且只出现一次；不得遗漏、跨section使用或用相近ID替代。",
        "6. fact_text只能引用当前部分一个事实锚点，事实内容必须来自该锚点的canonical_subject、canonical_action、canonical_object和exact_values；不得加入锚点没有的时间、频次、数字、机构、职责、设备、机制、地点或结果。",
        "6a. fact_text必须先做“锚点原文投影”：按当前锚点的canonical_subject、canonical_action、canonical_object原文顺序组织为单一事实句。除必要虚词和标点外，不得把主语改写为本地机构全称，不得把对象扩写为事项清单，不得增加第二个动作、过程、目的、职责、机制或成效。",
        fact_short_anchor_rule,
        "7. FACT段落保持事实锚定而非机械短句化：允许围绕同一evidence_id进行正常公文段落组织，允许使用必要的承接词、限定语和同义表达，但不得合并两个不同事实锚点，不得新增第二个独立动作、职责、结果或作用。",
        "8. fact_text可以把同一锚点的主语、动作、对象和已明示细节组织成信息密度适中的完整句；锚点未明示的原因、目的、作用、结果、职责扩展、机制扩展、保障措施仍不得补充。",
        "9. expansion_text、transition_text、summary_text只能围绕同一事实作背景铺垫、过程逻辑、衔接和一般性总结，不得承载新的现实事实；“推动、保障、确保、提升、形成机制”等只有在该evidence_id锚点明示相应动作或结果时才能作为事实陈述使用。",
        "10. FACT段落出现逗号、分号或顿号连接时，必须确认连接内容仍属于同一evidence_id的同一事实表达、同一锚点已明示细节或一般性公文衔接；无法确认时删除扩写内容。",
        "11. fact_required section中每个core锚点必须使用且只使用一次，supporting锚点最多使用一次，并继续满足当前有效的至少两个FACT段落要求；analysis_only section允许0个FACT且不得为了满足数量规则借用其他section的锚点。",
        "12. expansion_text、transition_text、summary_text只能作一般分析和公文衔接；不得陈述本地已完成、正在执行、已经取得或已经验证的事实。",
        "12a. expansion_text、transition_text、summary_text应分别写成边界清楚、语义完整的自然句；work_progress部分的这些字段不得以本地机构、科室、领导或部门为现实动作主体，不得补写具体督查方式、协同部门、会议频次、任务清单、考核安排或完成成效。",
        *analysis_execution_rules,
        "15. 段落展开禁止把措施写成结果，禁止使用带有结果完成事实含义的表达，包括但不限于“实现了”“避免了”“确保了”“取得了”“形成了”“提升了”“推动了”“达到了”“达到……”“取得……”“产生……”“带来……”“实现……”“形成……”“提升……”“促进了……”“推动了……”“确保了……”“避免了……”“有效提升了……”“显著提升……”“已经导致……”“从而实现……”“有效避免……”“确保完成……”。",
        "16. 段落展开优先使用机关材料中带有行动感和组织感的表达，如“坚持……”“围绕……”“聚焦……”“结合……”“通过……”“持续……”“进一步……”“扎实……”“有序推进……”“压实责任、统筹推进、贯通落实、抓实抓细、常态推进”等；这些表达只能用于承接、取向和方法说明，不得借此新增证据未提供的措施、机制、职责、结果或成效。",
        "16a. “关键在于……”“核心是……”“有利于……”“旨在……”“体现了……”“反映出……”“有助于……”等解释型句式只能低频、必要时使用，不得连续段落重复，不得作为summary_text的默认收束方式；同一section内出现一次后，应改用工作推进、组织落实、衔接递进或常态要求类表达。",
        "16b. 每个section的段落节奏应形成背景引入、工作展开、措施说明、逻辑递进、自然收束，不得把每个paragraph机械写成“事实一句、解释一句、总结一句”，不得把transition_text和summary_text写成解释句列表。",
        *work_chain_rules,
        source_copy_rule,
        closing_length_rule,
        "21a. problem_analysis只能分析一般性问题、风险和改进空间，不得编造具体事件、数字、机构或现实变化。",
        "21b. next_step_arrangement必须使用“下一步、将、要、需、应、持续”等未来态、建议态或部署态表达，不得写成已经实施、已经完成或已经取得成效。",
        "21c. 禁止通过重复事实、同义反复、政策词堆砌、空泛套话、复制范文原句或增加未支持现实细节凑字数。",
        "22. 不得虚构红黄蓝分级，不得虚构每日、每周等频次，不得虚构响应、到场、处置时限，不得虚构部门具体职责。",
        "23. 不得新增证据未提供的现实细节，不得输出Markdown标题符号。",
    ])


def build_section_expansion_prompt(expansion_request: dict[str, Any]) -> str:
    """Build the restricted one-shot deficient-section expansion prompt."""
    work_summary_policy = expansion_request.get("work_summary_execution_policy")
    work_summary = isinstance(work_summary_policy, dict)
    introduction_required = bool(
        (expansion_request.get("introduction_deficit") or {}).get("required")
    )
    subsection_expansion = any(
        section.get("writing_subsections")
        for section in expansion_request.get("sections_to_expand") or []
        if isinstance(section, dict)
    )
    protocol: dict[str, Any] = {}
    if introduction_required:
        protocol["introduction"] = (
            "只补写缺失或不合格的报告导语；一个自然段，不带标签。"
        )
    if work_summary:
        protocol["expanded_sections"] = [
            {
                "section_id": "只能复制sections_to_expand中的section_id",
                "sentences": [
                    {
                        "sentence_type": "ANALYSIS",
                        "evidence_id": "",
                        "text": "仅修复空analysis-only section所需的克制问题解释或未来安排。",
                    }
                ],
            }
        ]
    else:
        expansion_paragraph = {
            "evidence_id": "",
            "fact_text": "",
            "expansion_text": "安全的一般分析、机制阐释或方法说明。",
            "transition_text": "与本章节已有正文自然衔接的过渡。",
            "summary_text": "审慎的工作要求、逻辑收束或未来态安排。",
        }
        if subsection_expansion:
            expansion_paragraph = {
                "subsection_id": (
                    "只能复制当前section.writing_subsections中的ID"
                ),
                **expansion_paragraph,
            }
        protocol["expanded_sections"] = [
            {
                "section_id": "只能复制sections_to_expand中的section_id",
                "paragraphs": [expansion_paragraph],
            }
        ]
    root_field_rule = (
        "1. 只输出一个JSON对象，根字段必须且只能是introduction、expanded_sections。"
        if introduction_required
        else "1. 只输出一个JSON对象，根字段只能是expanded_sections。"
    )
    introduction_rules = (
        [
            "1a. introduction必须是字符串，只在introduction_deficit.required=true时补写；不得修改标题或任何已有合格章节。",
            "1b. introduction只能说明时间范围、总体要求、审慎的总体推进概况并自然引出下文；不得与第一部分重复。",
            "1c. introduction不得包含evidence_id、fact_text、FACT协议、数字、次数、会议、文件、机构、职责、制度、具体措施、现实成果或完成态结论。",
            "1d. introduction须按introduction_deficit.target_length和accepted_range一次补足；不得请求再次调用。",
        ]
        if introduction_required
        else []
    )
    task_rule = (
        "只修复请求中实际为空的analysis-only结构性缺失；不得为requested target、section预算、80%比例或任何纯字数缺口扩写。"
        if work_summary
        else "只对请求中列出的缺口章节进行一次定向扩写。不得重写全文，不得返回其他章节。"
    )
    functional_rule = (
        "8. 新增段落只能承担有材料依据的问题解释或有材料/任务依据的未来安排；禁止意义、理论、规律、启示、机制泛化、万能问题和万能下一步。"
        if work_summary
        else "8. 新增段落必须承担背景解释、工作机制说明、工作逻辑分析、方法意义、责任链条说明、问题原因分析、下一步措施展开或逻辑衔接中的明确功能。"
    )
    length_rule = (
        "9. length_mode、capacity_status、capacity_gap与capacity estimate均按请求执行；estimate_is_binding=false，不得补足deficit，不得追逐requested target。"
        if work_summary
        else "9. 按section_deficit补足实际缺口，不得使用“尽量写长”等模糊策略，不得明显超过section_target_budget。"
    )
    return "\n".join(
        [
            "【任务】",
            task_rule,
            "",
            "【唯一允许使用的输入】",
            json.dumps(expansion_request, ensure_ascii=False, indent=2),
            "",
            "【唯一输出协议】",
            json.dumps(protocol, ensure_ascii=False, indent=2),
            "",
            "【硬约束】",
            root_field_rule,
            *introduction_rules,
            "2. section_id只能来自sections_to_expand；不得新增一级章节，不得输出或修改章节标题。",
            (
                "3. 工作总结每个expanded_section必须输出sentences数组；每个sentence必须且只能包含sentence_type、evidence_id、text，sentence_type必须为ANALYSIS且evidence_id必须为空字符串。"
                if work_summary
                else (
                    "3. 每个paragraph必须且只能包含subsection_id、"
                    "evidence_id、fact_text、expansion_text、"
                    "transition_text、summary_text；subsection_id只能复制"
                    "同一section的writing_subsections既有ID，不得新增或跨section。"
                    if subsection_expansion
                    else
                    "3. 每个paragraph必须且只能包含evidence_id、fact_text、expansion_text、transition_text、summary_text。"
                )
            ),
            (
                "4. 新增内容只能作为可选ANALYSIS；不得补FACT，不得因长度或段落形态补写。"
                if work_summary
                else "4. 新增内容只能作为ANALYSIS；evidence_id和fact_text必须为空字符串。"
            ),
            "5. 不得创建、修改、补全或复用任何evidence_id，不得删除或改写已有正文。",
            "6. 不得新增数字、次数、日期、文件、会议、机构、职责、项目、制度名称、现实成果或完成态结论。",
            "7. 不得把计划措施写成已经完成的事实，不得把分析判断写成现实成果。",
            functional_rule,
            length_rule,
            "10. 禁止重复已有句子、连续同义改写、重复标题、机械堆叠“进一步、持续、不断、切实”等词或复制Fact Anchor凑字。",
            "11. fact_anchors只能帮助理解已有事实边界；不得扩写FACT，不得由锚点推断新的现实细节。",
            "12. analysis_support和reference_structure_requirements只用于安全分析与组织功能，不得作为现实事实来源。",
            "13. 所有缺口章节必须在本次同一个响应中返回；不得请求重试或建议再次调用。",
        ]
    )


def build_structured_execution_repair_prompt(
    repair_input: dict[str, Any],
) -> str:
    """Build the isolated one-section Writer execution repair prompt."""

    section_id = str(repair_input.get("section_id") or "")
    output_protocol = {
        "section": {
            "section_id": section_id,
            "heading": "保留或修正当前章节标题",
            "sentences": [
                {
                    "sentence_type": "FACT或ANALYSIS",
                    "group_id": "FACT复制合法Evidence Group；ANALYSIS为空",
                    "fact_unit_ids": [
                        "FACT只能复制同组合法fact_unit_id；ANALYSIS为空数组"
                    ],
                    "plan_step": "只能复制执行合同allowlist值",
                    "writing_focus_role": "只能复制执行合同allowlist值",
                    "expansion_stage": "只能复制执行合同allowlist值",
                    "content_function": "只能复制执行合同allowlist值",
                    "work_chain_role": "只能复制执行合同allowlist值",
                    "text": "仅调整本章节既有事实与分析的组织，不新增事实",
                }
            ],
        }
    }
    return "\n".join(
        [
            "【任务】",
            "只修复一个失败章节的 Structured / Writer Execution 合同问题。",
            "不得重写全文，不得返回或推断其他章节。",
            "",
            "【唯一允许使用的输入】",
            json.dumps(repair_input, ensure_ascii=False, indent=2),
            "",
            "【唯一输出协议】",
            json.dumps(output_protocol, ensure_ascii=False, indent=2),
            "",
            "【硬约束】",
            "1. 只输出一个JSON对象，根字段必须且只能是section。",
            f"2. section.section_id必须保持为{section_id}，不得新增、删除或返回其他章节。",
            "3. 只能解决validation_issues列出的问题；不得重新规划、改变章节目标或改写无关内容。",
            "4. FACT只能使用legal_evidence_groups中本章节既有group_id和fact_unit_id；不得输出evidence_id或evidence_ids。",
            "5. 不得新增、补全、拼接或推断事实；不得新增时间、数字、机构、文件、人员、会议、项目、职责、动作、成果或因果结论。",
            "6. ANALYSIS的group_id必须为空字符串、fact_unit_ids必须为空数组，且不得引入新的具体事实或完成态成果。",
            "7. plan_step、writing_focus_role、expansion_stage、content_function、work_chain_role只能复制writer_execution_contract_section.execution_contract中的合法值。",
            "8. 可为打断FACT stacking调整本章节既有句序，或利用既有ANALYSIS句承担衔接；不得为此制造新的事实或新分析结论。",
            "9. 必须满足原合同的必需项覆盖、顺序、工作链角色和最多连续FACT数量要求；复检不会放宽任何规则。",
            "10. 本次响应是唯一一次修复机会，不得请求重试。",
        ]
    )
