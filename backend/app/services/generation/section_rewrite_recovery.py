"""One-shot, closed-world recovery for one Evidence Safety failed section."""

from __future__ import annotations

import copy
import json
import re
from typing import Any


SECTION_REWRITE_STATUSES = {
    "BLOCKED_SECTION_REWRITE_REQUIRED",
    "REVIEW_REQUIRED_DRAFT",
}
SECTION_REWRITE_CLASSIFICATION = "SECTION_REWRITE_REQUIRED"
MAX_SECTION_REWRITE_ATTEMPTS = 1


def _fact_evidence_ids(section: dict[str, Any]) -> list[str]:
    return [
        str(sentence.get("evidence_id") or "")
        for sentence in section.get("sentences") or []
        if sentence.get("sentence_type") == "FACT"
    ]


def _target_section_id(evidence_safety_report: dict[str, Any]) -> str:
    if str(evidence_safety_report.get("status") or "") not in SECTION_REWRITE_STATUSES:
        return ""
    if evidence_safety_report.get("draft_rewrite_required"):
        return ""
    if int(evidence_safety_report.get("hard_block_count") or 0):
        return ""
    failures = [
        item
        for item in evidence_safety_report.get("failure_classifications") or []
        if str(item.get("classification") or "")
        == SECTION_REWRITE_CLASSIFICATION
    ]
    section_ids = {
        str(item.get("section_id") or "") for item in failures
        if item.get("section_id")
    }
    if len(section_ids) != 1:
        return ""
    return next(iter(section_ids))


def build_section_rewrite_prompt(
    *,
    section: dict[str, Any],
    allowed_fact_anchors: list[dict[str, Any]],
    allowed_source_support: dict[str, dict[str, Any]],
) -> str:
    """Build the minimal closed-world prompt for one failed section."""

    payload = {
        "section": section,
        "allowed_fact_anchors": allowed_fact_anchors,
        "allowed_source_support": allowed_source_support,
    }
    protocol = {
        "section_id": str(section.get("section_id") or ""),
        "sentences": [
            {
                "sentence_type": str(item.get("sentence_type") or ""),
                "evidence_id": str(item.get("evidence_id") or ""),
                "text": "重写后的句子",
            }
            for item in section.get("sentences") or []
        ],
    }
    return "\n".join([
        "【任务】只重写给定的一个小节，使其中事实严格受允许证据支持。",
        "【硬约束】",
        "1. 只能输出一个 JSON 对象，不得输出解释或 Markdown。",
        "2. section_id、句子数量、句子顺序、sentence_type 和 evidence_id 必须逐项原样复制。",
        "3. 不得新增、删除、复制、替换或重绑 evidence_id。",
        "4. FACT 只能改写允许 Fact Anchor 已明确支持的内容；不得补充任何新事实或推断。",
        "5. ANALYSIS 的 evidence_id 必须为空，且不得引入数字、时间、机构、职责、措施、成果或完成态等新事实。",
        "6. 不得返回其他小节，不得修改标题、计划或证据。",
        "【唯一允许使用的输入】",
        json.dumps(payload, ensure_ascii=False, indent=2),
        "【唯一输出协议】",
        json.dumps(protocol, ensure_ascii=False, indent=2),
    ])


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
        raise ValueError("section rewrite response root must be one JSON object")
    return parsed


def execute_one_shot_section_rewrite(
    *,
    draft: dict[str, Any],
    evidence_safety_report: dict[str, Any],
    fact_anchor_pack: dict[str, Any],
    source_support: dict[str, dict[str, Any]],
    provider: Any,
    temperature: float,
) -> dict[str, Any]:
    """Call the existing writer once and merge text only if bindings are identical."""

    target_section_id = _target_section_id(evidence_safety_report)
    base_report: dict[str, Any] = {
        "attempted": False,
        "attempt_count": 0,
        "max_attempts": MAX_SECTION_REWRITE_ATTEMPTS,
        "section_id": target_section_id,
        "status": "NOT_ELIGIBLE",
        "model_call_count": 0,
    }
    if not target_section_id:
        return {"structured_draft": draft, "raw_response": "", "report": base_report}

    target_sections = [
        item for item in draft.get("sections") or []
        if str(item.get("section_id") or "") == target_section_id
    ]
    if len(target_sections) != 1:
        return {
            "structured_draft": draft,
            "raw_response": "",
            "report": {**base_report, "status": "TARGET_SECTION_INVALID"},
        }
    target_section = target_sections[0]
    original_evidence_ids = _fact_evidence_ids(target_section)
    allowed_id_set = set(original_evidence_ids)
    allowed_anchors = [
        copy.deepcopy(item)
        for item in fact_anchor_pack.get("fact_anchors") or []
        if str(item.get("section_id") or "") == target_section_id
        and str(item.get("evidence_id") or "") in allowed_id_set
    ]
    allowed_support = {
        evidence_id: copy.deepcopy(source_support[evidence_id])
        for evidence_id in allowed_id_set
        if evidence_id in source_support
    }
    prompt = build_section_rewrite_prompt(
        section=copy.deepcopy(target_section),
        allowed_fact_anchors=allowed_anchors,
        allowed_source_support=allowed_support,
    )
    raw_response = provider.generate_text(
        prompt=prompt,
        system_prompt="只输出严格符合协议的一个 JSON 对象。",
        temperature=temperature,
    )
    report = {
        **base_report,
        "attempted": True,
        "attempt_count": 1,
        "model_call_count": 1,
        "evidence_ids_before": original_evidence_ids,
    }
    try:
        parsed = _parse_response(str(raw_response))
        if set(parsed) != {"section_id", "sentences"}:
            raise ValueError("section rewrite response fields changed")
        if str(parsed.get("section_id") or "") != target_section_id:
            raise ValueError("section_id changed")
        rewritten_sentences = parsed.get("sentences")
        original_sentences = target_section.get("sentences") or []
        if not isinstance(rewritten_sentences, list):
            raise ValueError("sentences must be an array")
        if len(rewritten_sentences) != len(original_sentences):
            raise ValueError("sentence count changed")
        for before, after in zip(original_sentences, rewritten_sentences):
            if not isinstance(after, dict) or set(after) != {
                "sentence_type", "evidence_id", "text",
            }:
                raise ValueError("sentence fields changed")
            if str(after.get("sentence_type") or "") != str(
                before.get("sentence_type") or ""
            ):
                raise ValueError("sentence_type changed")
            if str(after.get("evidence_id") or "") != str(
                before.get("evidence_id") or ""
            ):
                raise ValueError("evidence_id binding changed")
            if not str(after.get("text") or "").strip():
                raise ValueError("sentence text is empty")

        merged = copy.deepcopy(draft)
        merged_target = next(
            item for item in merged.get("sections") or []
            if str(item.get("section_id") or "") == target_section_id
        )
        for before, after in zip(
            merged_target.get("sentences") or [], rewritten_sentences
        ):
            before["text"] = str(after["text"]).strip()
        evidence_ids_after = _fact_evidence_ids(merged_target)
        if evidence_ids_after != original_evidence_ids:
            raise ValueError("evidence_id sequence changed")
    except (json.JSONDecodeError, ValueError, StopIteration) as exc:
        return {
            "structured_draft": draft,
            "raw_response": str(raw_response),
            "report": {
                **report,
                "status": "REJECTED_PROTOCOL",
                "error_type": type(exc).__name__,
                "evidence_ids_after": original_evidence_ids,
            },
        }
    return {
        "structured_draft": merged,
        "raw_response": str(raw_response),
        "report": {
            **report,
            "status": "MERGED_PENDING_FULL_REVALIDATION",
            "evidence_ids_after": evidence_ids_after,
            "evidence_ids_unchanged": evidence_ids_after == original_evidence_ids,
        },
    }
