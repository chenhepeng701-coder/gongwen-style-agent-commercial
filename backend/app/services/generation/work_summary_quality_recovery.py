"""Bounded, section-level repair for the work-summary commercial quality gate."""

from __future__ import annotations

import copy
import json
import re
from typing import Any, Mapping, Sequence

from app.models.flow4 import PARAGRAPH_FIELDS, preserve_paragraph_identity


MAX_QUALITY_REPAIR_ROUNDS = 2
QUALITY_REPAIR_SCHEMA_VERSION = "work-summary-quality-repair-1"


def _parse_response(raw_response: str) -> dict[str, Any]:
    value = str(raw_response or "").strip()
    fenced = re.fullmatch(
        r"```(?:json)?\s*\n([\s\S]*?)\n```",
        value,
        flags=re.IGNORECASE,
    )
    if fenced:
        value = fenced.group(1).strip()
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("quality repair response root must be one JSON object")
    return parsed


def _target_section_ids(quality_result: Mapping[str, Any]) -> list[str]:
    ordered: list[str] = []
    for issue in quality_result.get("issues") or []:
        if not isinstance(issue, Mapping):
            continue
        section_id = str(issue.get("section_id") or "")
        if (
            section_id
            and str(issue.get("severity") or "") == "BLOCKING"
            and bool(issue.get("repairable"))
            and section_id not in ordered
        ):
            ordered.append(section_id)
    return ordered


def _evidence_field(section: Mapping[str, Any]) -> str:
    if any(
        isinstance(sentence, Mapping) and "evidence_ids" in sentence
        for sentence in section.get("sentences") or []
    ):
        return "evidence_ids"
    return "evidence_id"


def _fact_binding_signature(sentence: Mapping[str, Any]) -> str:
    payload = {
        str(key): copy.deepcopy(value)
        for key, value in sentence.items()
        if str(key) != "text"
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def fact_binding_snapshot(
    draft: Mapping[str, Any],
    section_ids: Sequence[str] | None = None,
) -> dict[str, list[str]]:
    """Return immutable FACT identity/type/binding order for escape detection."""

    allowed = set(section_ids or [])
    return {
        str(section.get("section_id") or ""): [
            _fact_binding_signature(sentence)
            for sentence in section.get("sentences") or []
            if isinstance(sentence, Mapping)
            and str(sentence.get("sentence_type") or "") == "FACT"
        ]
        for section in draft.get("sections") or []
        if isinstance(section, Mapping)
        and (not allowed or str(section.get("section_id") or "") in allowed)
    }


def _frozen_section_projection(
    frozen_article_plan: Any,
    content_plan: Mapping[str, Any],
    target_section_ids: Sequence[str],
) -> dict[str, Any]:
    frozen_payload: Mapping[str, Any] = {}
    if hasattr(frozen_article_plan, "to_dict"):
        candidate = frozen_article_plan.to_dict()
        if isinstance(candidate, Mapping):
            frozen_payload = candidate
    elif isinstance(frozen_article_plan, Mapping):
        frozen_payload = frozen_article_plan
    frozen_content_plan = frozen_payload.get("content_plan")
    source_plan = (
        frozen_content_plan
        if isinstance(frozen_content_plan, Mapping)
        else content_plan
    )
    targets = set(target_section_ids)
    return {
        "plan_id": str(frozen_payload.get("plan_id") or ""),
        "frozen_plan_sha256": str(
            frozen_payload.get("frozen_plan_sha256")
            or getattr(frozen_article_plan, "frozen_plan_sha256", "")
            or ""
        ),
        "sections": [
            copy.deepcopy(dict(section))
            for section in source_plan.get("sections") or []
            if isinstance(section, Mapping)
            and str(section.get("section_id") or "") in targets
        ],
    }


def _section_evidence_groups(
    evidence_group_pack: Mapping[str, Any],
    target_section_ids: Sequence[str],
) -> dict[str, Any]:
    targets = set(target_section_ids)
    groups = [
        copy.deepcopy(dict(group))
        for group in evidence_group_pack.get("groups") or []
        if isinstance(group, Mapping)
        and str(group.get("section_id") or "") in targets
    ]
    return {
        "schema_version": str(evidence_group_pack.get("schema_version") or ""),
        "groups": groups,
        "group_ids_by_section": {
            section_id: [
                str(group.get("group_id") or "")
                for group in groups
                if str(group.get("section_id") or "") == section_id
            ]
            for section_id in target_section_ids
        },
    }


def _context_heading_relationships(
    draft: Mapping[str, Any],
    target_section_ids: Sequence[str],
) -> list[dict[str, str]]:
    sections = [
        section for section in draft.get("sections") or []
        if isinstance(section, Mapping)
    ]
    target_indexes = {
        index
        for index, section in enumerate(sections)
        if str(section.get("section_id") or "") in set(target_section_ids)
    }
    included = {
        neighbor
        for index in target_indexes
        for neighbor in (index - 1, index, index + 1)
        if 0 <= neighbor < len(sections)
    }
    return [
        {
            "section_id": str(sections[index].get("section_id") or ""),
            "heading": str(sections[index].get("heading") or ""),
            "relationship": "TARGET" if index in target_indexes else "ADJACENT_CONTEXT",
        }
        for index in sorted(included)
    ]


def build_work_summary_quality_repair_prompt(
    *,
    draft: Mapping[str, Any],
    quality_result: Mapping[str, Any],
    frozen_article_plan: Any,
    content_plan: Mapping[str, Any],
    evidence_group_pack: Mapping[str, Any],
    repair_round: int,
) -> tuple[str, list[str]]:
    """Build one closed-world batch prompt for all failed sections in a round."""

    target_section_ids = _target_section_ids(quality_result)
    targets = set(target_section_ids)
    target_sections = [
        copy.deepcopy(dict(section))
        for section in draft.get("sections") or []
        if isinstance(section, Mapping)
        and str(section.get("section_id") or "") in targets
    ]
    if [str(section.get("section_id") or "") for section in target_sections] != target_section_ids:
        raise ValueError("quality repair target sections are missing or out of order")
    target_issues = [
        copy.deepcopy(dict(issue))
        for issue in quality_result.get("issues") or []
        if isinstance(issue, Mapping)
        and str(issue.get("section_id") or "") in targets
        and str(issue.get("severity") or "") == "BLOCKING"
        and bool(issue.get("repairable"))
    ]
    payload = {
        "repair_round": repair_round,
        "target_sections": target_sections,
        "frozen_article_plan_projection": _frozen_section_projection(
            frozen_article_plan,
            content_plan,
            target_section_ids,
        ),
        "legal_evidence_groups": _section_evidence_groups(
            evidence_group_pack,
            target_section_ids,
        ),
        "quality_issues": target_issues,
        "heading_context": _context_heading_relationships(
            draft,
            target_section_ids,
        ),
    }
    protocol = {"sections": target_sections}
    prompt = "\n".join([
        "【任务】按质量问题对给定工作总结失败章节做一次批量、定点修复。",
        "【硬边界】",
        "1. 只能输出一个 JSON 对象，根字段只能是 sections，不得输出解释或 Markdown。",
        "2. 只返回目标 section，section_id、数量和顺序必须原样复制；不得返回或改写其他章节。",
        "3. 可以重写目标标题、FACT 文本和 ANALYSIS 文本，也可以调整或增加安全 ANALYSIS；不得新增、删除、复制、重排 FACT。",
        "4. 每个 FACT 的 sentence_type、evidence_id/evidence_ids 及其他非 text 字段必须逐项原样复制；不得 FACT→ANALYSIS 逃逸，不得制造或跨节引用 Evidence ID。",
        "5. FACT 只能在本节 legal_evidence_groups 支持范围内改写，不得增加数字、日期、人数、金额、机构、项目、成果、完成状态、因果、计划事项或现实判断。",
        "6. ANALYSIS 的 evidence_id 必须为空或 evidence_ids 必须为空数组；不得引入新的现实事实、完成态成果或无证据因果。",
        "7. 修复要直接回应每条 repair_instruction；不得只插入一句空泛 ANALYSIS 来规避 FACT_STACKING。",
        "8. 不得重新规划、重新选择证据、扩大证据范围或默认全文重写。",
        "【本轮唯一允许输入】",
        json.dumps(payload, ensure_ascii=False, indent=2),
        "【输出协议（结构与 FACT 绑定以此为准，文本按问题修复）】",
        json.dumps(protocol, ensure_ascii=False, indent=2),
    ])
    return prompt, target_section_ids


def _validate_and_merge(
    *,
    draft: Mapping[str, Any],
    parsed: Mapping[str, Any],
    target_section_ids: Sequence[str],
) -> dict[str, Any]:
    if set(parsed) != {"sections"} or not isinstance(parsed.get("sections"), list):
        raise ValueError("quality repair response root fields changed")
    returned = parsed["sections"]
    if not all(isinstance(section, Mapping) for section in returned):
        raise ValueError("quality repair sections must be objects")
    returned_ids = [str(section.get("section_id") or "") for section in returned]
    if returned_ids != list(target_section_ids):
        raise ValueError("quality repair section identity or order changed")
    before_by_id = {
        str(section.get("section_id") or ""): section
        for section in draft.get("sections") or []
        if isinstance(section, Mapping)
    }
    # FLOW4 ownership preservation only; quality and evidence rules stay unchanged.
    returned = copy.deepcopy(returned)
    preserve_paragraph_identity([before_by_id[section_id] for section_id in returned_ids], returned)
    for section in returned:
        if set(section) != {"section_id", "heading", "sentences"}:
            raise ValueError("quality repair section fields changed")
        section_id = str(section.get("section_id") or "")
        if not str(section.get("heading") or "").strip():
            raise ValueError("quality repair heading is empty")
        sentences = section.get("sentences")
        if not isinstance(sentences, list) or not sentences:
            raise ValueError("quality repair sentences must be a non-empty array")
        original = before_by_id[section_id]
        evidence_field = _evidence_field(original)
        original_fact_bindings = fact_binding_snapshot(
            {"sections": [original]}, [section_id],
        )[section_id]
        candidate_fact_bindings: list[str] = []
        subsection_required = any(
            isinstance(sentence, Mapping) and "subsection_id" in sentence
            for sentence in original.get("sentences") or []
        )
        for sentence in sentences:
            if not isinstance(sentence, Mapping):
                raise ValueError("quality repair sentence must be an object")
            sentence_type = str(sentence.get("sentence_type") or "")
            if sentence_type not in {"FACT", "ANALYSIS"}:
                raise ValueError("quality repair sentence_type changed")
            if not str(sentence.get("text") or "").strip():
                raise ValueError("quality repair sentence text is empty")
            if sentence_type == "FACT":
                candidate_fact_bindings.append(_fact_binding_signature(sentence))
                continue
            allowed_fields = {"sentence_type", "text", evidence_field}
            allowed_fields.update(field for field in PARAGRAPH_FIELDS if field in sentence)
            if subsection_required:
                allowed_fields.add("subsection_id")
            if set(sentence) != allowed_fields:
                raise ValueError("quality repair ANALYSIS fields changed")
            if evidence_field == "evidence_ids":
                if sentence.get("evidence_ids") != []:
                    raise ValueError("quality repair ANALYSIS evidence_ids must be empty")
            elif str(sentence.get("evidence_id") or ""):
                raise ValueError("quality repair ANALYSIS evidence_id must be empty")
            if subsection_required and not str(sentence.get("subsection_id") or ""):
                raise ValueError("quality repair ANALYSIS subsection_id is empty")
        if candidate_fact_bindings != original_fact_bindings:
            raise ValueError("FACT identity/type/evidence binding sequence changed")

    merged = copy.deepcopy(dict(draft))
    returned_by_id = {
        str(section.get("section_id") or ""): section for section in returned
    }
    for section in merged.get("sections") or []:
        section_id = str(section.get("section_id") or "")
        if section_id not in returned_by_id:
            continue
        replacement = returned_by_id[section_id]
        section["heading"] = str(replacement["heading"]).strip()
        section["sentences"] = copy.deepcopy(replacement["sentences"])
    if fact_binding_snapshot(merged, target_section_ids) != fact_binding_snapshot(
        draft, target_section_ids,
    ):
        raise ValueError("FACT binding snapshot changed after merge")
    return merged


def execute_work_summary_quality_repair(
    *,
    draft: Mapping[str, Any],
    quality_result: Mapping[str, Any],
    frozen_article_plan: Any,
    content_plan: Mapping[str, Any],
    evidence_group_pack: Mapping[str, Any],
    provider: Any,
    temperature: float,
    repair_round: int,
) -> dict[str, Any]:
    """Call the existing Writer once and merge only protocol-safe sections."""

    base_report: dict[str, Any] = {
        "schema_version": QUALITY_REPAIR_SCHEMA_VERSION,
        "repair_round": repair_round,
        "max_repair_rounds": MAX_QUALITY_REPAIR_ROUNDS,
        "attempted": False,
        "model_call_count": 0,
        "batch_call": True,
        "status": "NOT_REQUIRED",
        "target_section_ids": [],
    }
    try:
        prompt, target_section_ids = build_work_summary_quality_repair_prompt(
            draft=draft,
            quality_result=quality_result,
            frozen_article_plan=frozen_article_plan,
            content_plan=content_plan,
            evidence_group_pack=evidence_group_pack,
            repair_round=repair_round,
        )
    except (TypeError, ValueError) as exc:
        return {
            "structured_draft": copy.deepcopy(dict(draft)),
            "raw_response": "",
            "report": {
                **base_report,
                "status": "REJECTED_INPUT_CONTRACT",
                "error_type": type(exc).__name__,
            },
        }
    if not target_section_ids:
        return {
            "structured_draft": copy.deepcopy(dict(draft)),
            "raw_response": "",
            "report": base_report,
        }
    report = {
        **base_report,
        "attempted": True,
        "model_call_count": 1,
        "target_section_ids": list(target_section_ids),
        "fact_bindings_before": fact_binding_snapshot(draft, target_section_ids),
    }
    try:
        raw_response = provider.generate_text(
            prompt=prompt,
            system_prompt="只输出严格符合闭世界批量章节修复协议的一个 JSON 对象。",
            temperature=temperature,
        )
    except Exception as exc:  # Provider failures become an explicit return state.
        return {
            "structured_draft": copy.deepcopy(dict(draft)),
            "raw_response": "",
            "report": {
                **report,
                "status": "MODEL_CALL_FAILED",
                "error_type": type(exc).__name__,
            },
        }
    try:
        parsed = _parse_response(str(raw_response))
        merged = _validate_and_merge(
            draft=draft,
            parsed=parsed,
            target_section_ids=target_section_ids,
        )
    except (json.JSONDecodeError, TypeError, ValueError, KeyError) as exc:
        return {
            "structured_draft": copy.deepcopy(dict(draft)),
            "raw_response": str(raw_response),
            "report": {
                **report,
                "status": "REJECTED_PROTOCOL",
                **({"contract_error": "PARAGRAPH_IDENTITY_LOST"}
                   if "PARAGRAPH_IDENTITY_LOST" in str(exc) else {}),
                "error_type": type(exc).__name__,
                "fact_bindings_after": fact_binding_snapshot(draft, target_section_ids),
            },
        }
    return {
        "structured_draft": merged,
        "raw_response": str(raw_response),
        "report": {
            **report,
            "status": "MERGED_PENDING_FULL_REVALIDATION",
            "fact_bindings_after": fact_binding_snapshot(merged, target_section_ids),
            "fact_bindings_unchanged": True,
        },
    }
