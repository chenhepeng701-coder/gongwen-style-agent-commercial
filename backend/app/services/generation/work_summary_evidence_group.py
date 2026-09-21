"""Writer-facing evidence groups for work-summary generation.

Atomic Evidence and Fact Anchors remain the verification truth.  This module
owns only the compact, cleaned projection consumed by the Writer.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections import defaultdict
from typing import Any, Mapping


EVIDENCE_GROUP_SCHEMA_VERSION = "work-summary-evidence-group-2"
WRITER_BINDING_SCHEMA_VERSION = "work-summary-writer-unit-binding-1"
_PLACEHOLDER_RE = re.compile(
    r"(?:[×✕]{1,}|\bX{2,}\b|待补(?:充)?|待确认|未知(?:数字|时间|次数|结果)?|TBD)",
    re.IGNORECASE,
)
_PLACEHOLDER_COUNT_RE = re.compile(
    r"(?:[一二三四五六七八九十百千万两\d]*[×✕X]+(?:轮|次|场|项|个|名|人|家|条|起|件)?)",
    re.IGNORECASE,
)
_TRAILING_FRAGMENT_RE = re.compile(
    r"(?:围绕|通过|针对|并|及|与|和|对|向|为|在|其中|同时|以及)$"
)
_DANGLING_ATOM_OBJECT_RE = re.compile(
    r"(?:达到|实现|完成|形成|提升|增强|增长|下降|提高|改善|转变|转型)$"
)
_GENERIC_SUBJECTS = {"相关责任主体", "有关方面", "各方", "相关单位", ""}
_GENERIC_ACTIONS = {"反映", "呈现", ""}


def writable_fact_diagnostic(text: str) -> dict[str, Any]:
    """Projection diagnostic only: never repair an Atomic subject or state.

    Source proximity alone does not authorize a missing referent. In particular,
    a longer source sentence containing placeholders cannot be promoted to a
    complete fact by deleting the unknown quantities or guessing their object.
    """
    value = _compact(text)
    if re.match(r"^(?:(?:主要|重点|全部|专门)用于|用于|其中用于)", value):
        reason = "missing_usage_subject"
    elif (re.match(r"^自.+以来$", value)
          or re.match(r"^(?:在|按照).+(?:方面|模式)$", value)
          or re.match(r"^尽管[^，。；]+$", value)
          or re.match(r"^但?发现?在以下方面", value)):
        reason = "context_clause_without_independent_assertion"
    else:
        return {"status": "WRITABLE", "usage": "INDEPENDENT_OR_SUPPORTING"}
    return {"status": "INCOMPLETE_WRITABLE_FACT", "usage": "SUPPORT_ONLY",
            "reason": reason, "may_infer_missing_subject_or_object": False}


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip("，,；;。:：")


def _clean_visible_value(value: Any) -> str:
    text = _PLACEHOLDER_COUNT_RE.sub("", str(value or ""))
    text = _PLACEHOLDER_RE.sub("", text)
    return _compact(text)


def _clean_exact_values(values: Mapping[str, Any] | None) -> dict[str, list[str]]:
    cleaned: dict[str, list[str]] = {}
    for category, raw_values in (values or {}).items():
        retained = [
            value
            for value in (_clean_visible_value(item) for item in (raw_values or []))
            if value and not _TRAILING_FRAGMENT_RE.search(value)
        ]
        if retained:
            cleaned[str(category)] = list(dict.fromkeys(retained))
    return cleaned


def _fact_hint(anchor: Mapping[str, Any]) -> tuple[str, str]:
    subject = _clean_visible_value(anchor.get("canonical_subject"))
    action = _clean_visible_value(anchor.get("canonical_action"))
    obj = _clean_visible_value(anchor.get("canonical_object"))
    if (
        action in _GENERIC_ACTIONS
        or not obj
        or _TRAILING_FRAGMENT_RE.search(obj)
        or _DANGLING_ATOM_OBJECT_RE.search(obj)
    ):
        return "", "incomplete_or_non_writable_anchor"

    # A placeholder count is discarded, while the confirmed occurrence is
    # retained.  No quantity is inferred or formatted.
    if not subject or subject in _GENERIC_SUBJECTS:
        hint = f"{action}{obj}"
    else:
        hint = f"{subject}{action}{obj}"
    hint = _clean_visible_value(hint)
    if len(re.findall(r"[\u3400-\u9fff]", hint)) < 4:
        return "", "visible_fact_too_short"
    return hint, ""


def _complete_source_fact(
    anchor: Mapping[str, Any],
    support: Mapping[str, Any] | None,
) -> tuple[str, str, str]:
    """Select a complete source-owned fact before falling back to Atomic fields."""

    support = support or {}
    if support.get("source_span_binding_valid") is False:
        return "", "", "invalid_source_binding"
    candidates = (
        ("source_span", support.get("source_span")),
        ("source_sentence", support.get("source_sentence")),
    )
    for source_kind, raw_value in candidates:
        raw_text = str(raw_value or "").strip()
        if not raw_text or _PLACEHOLDER_RE.search(raw_text):
            continue
        fact = _clean_visible_value(raw_text)
        if (
            len(re.findall(r"[\u3400-\u9fff]", fact)) >= 4
            and not _TRAILING_FRAGMENT_RE.search(fact)
        ):
            return fact, source_kind, ""

    hint, reason = _fact_hint(anchor)
    if reason:
        return "", "", reason
    return hint, "verified_atomic_projection", ""


def _fingerprint(anchor: Mapping[str, Any], hint: str, exact: Mapping[str, Any]) -> str:
    payload = {
        "hint": hint,
        "state": str(anchor.get("semantic_state") or "UNKNOWN"),
        "exact": exact,
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _chain_members(section: Mapping[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for chain in section.get("work_chain_summaries") or []:
        chain_id = str(chain.get("chain_id") or "")
        for evidence_id in chain.get("member_evidence_ids") or []:
            if chain_id and evidence_id:
                result[str(evidence_id)] = chain_id
    return result


def build_work_summary_evidence_groups(
    content_plan: Mapping[str, Any],
    fact_anchor_pack: Mapping[str, Any],
    fact_source_support: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build dynamic, traceable material groups without prescribing sentences."""

    sections = {
        str(section.get("section_id") or ""): section
        for section in content_plan.get("sections") or []
        if isinstance(section, Mapping)
    }
    chain_by_id = {
        section_id: _chain_members(section)
        for section_id, section in sections.items()
    }
    paragraph_by_id = {
        sid: {eid: task["task_id"] for task in section.get("paragraph_tasks", [])
              for eid in task["supporting_evidence_ids"]}
        for sid, section in sections.items()
    }
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    rejected: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for anchor in fact_anchor_pack.get("fact_anchors") or []:
        if not isinstance(anchor, Mapping):
            continue
        evidence_id = str(anchor.get("evidence_id") or "")
        section_id = str(anchor.get("section_id") or "")
        if not evidence_id or section_id not in sections:
            rejected.append({"evidence_id": evidence_id, "reason": "missing_or_unknown_section"})
            continue
        source_support = (fact_source_support or {}).get(evidence_id) or {}
        hint, source_kind, reason = _complete_source_fact(anchor, source_support)
        if reason:
            rejected.append({"evidence_id": evidence_id, "reason": reason})
            continue
        exact_values = _clean_exact_values(anchor.get("exact_values") or {})
        fingerprint = _fingerprint(anchor, hint, exact_values)
        duplicate_key = (section_id, fingerprint)
        if duplicate_key in seen:
            rejected.append({"evidence_id": evidence_id, "reason": "duplicate_fact"})
            continue
        seen.add(duplicate_key)

        state = str(anchor.get("semantic_state") or "UNKNOWN")
        chain_id = chain_by_id.get(section_id, {}).get(evidence_id, "")
        semantic_bucket = paragraph_by_id.get(section_id, {}).get(evidence_id) or chain_id or state
        grouped[(section_id, semantic_bucket, state)].append({
            "evidence_id": evidence_id,
            "fact": hint,
            "source_kind": source_kind,
            "exact_values": exact_values,
            "semantic_state": state,
            "subject": copy.deepcopy(anchor.get("canonical_subject", "")),
            "object": copy.deepcopy(anchor.get("canonical_object", "")),
            "semantic_constraints": {key: copy.deepcopy(anchor[key]) for key in
                                     ("state_qualifier", "negation_markers", "modality_markers", "times")
                                     if key in anchor},
            "writability": writable_fact_diagnostic(hint),
        })

    groups: list[dict[str, Any]] = []
    ids_by_section: dict[str, list[str]] = defaultdict(list)
    for index, ((section_id, semantic_bucket, state), facts) in enumerate(grouped.items(), 1):
        section = sections[section_id]
        group_id = f"EG-{index:03d}"
        evidence_ids = [str(item["evidence_id"]) for item in facts]
        facts_with_units = [
            {
                **item,
                "fact_unit_id": f"{group_id}-U{unit_index:02d}",
            }
            for unit_index, item in enumerate(facts, 1)
        ]
        ids_by_section[section_id].append(group_id)
        groups.append({
            "group_id": group_id,
            "section_id": section_id,
            "topic": str(section.get("business_block") or section.get("section_purpose") or "本节工作"),
            "semantic_state": state,
            "evidence_ids": evidence_ids,
            "facts": facts_with_units,
            "writing_instruction": "综合组织为完整句群或成熟段落，不逐条照抄，不补充组外事实。",
        })

    return {
        "schema_version": EVIDENCE_GROUP_SCHEMA_VERSION,
        "groups": groups,
        "group_ids_by_section": dict(ids_by_section),
        "accepted_evidence_count": sum(len(group["evidence_ids"]) for group in groups),
        "rejected_evidence": rejected,
        "rejected_evidence_count": len(rejected),
        "external_model_calls": 0,
    }


def writer_visible_evidence_groups(
    evidence_group_pack: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Remove internal evidence IDs from the Writer-visible material contract."""

    visible: list[dict[str, Any]] = []
    for group in evidence_group_pack.get("groups") or []:
        if not isinstance(group, Mapping):
            continue
        visible.append({
            "group_id": str(group.get("group_id") or ""),
            "section_id": str(group.get("section_id") or ""),
            "topic": str(group.get("topic") or ""),
            "semantic_state": str(group.get("semantic_state") or "UNKNOWN"),
            "facts": [
                {
                    "fact_unit_id": str(fact.get("fact_unit_id") or ""),
                    "fact": str(fact.get("fact") or ""),
                    "source_kind": str(fact.get("source_kind") or ""),
                    "exact_values": copy.deepcopy(fact.get("exact_values") or {}),
                    "semantic_state": str(fact.get("semantic_state") or "UNKNOWN"),
                }
                for fact in group.get("facts") or []
                if isinstance(fact, Mapping)
            ],
            "writing_instruction": str(group.get("writing_instruction") or ""),
        })
    return visible


def bind_work_summary_writer_units(
    draft: Mapping[str, Any],
    evidence_group_pack: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Bind Writer unit identifiers to evidence IDs without inspecting prose."""

    bound = copy.deepcopy(dict(draft))
    groups = {
        str(group.get("group_id") or ""): group
        for group in evidence_group_pack.get("groups") or []
        if isinstance(group, Mapping) and group.get("group_id")
    }
    units: dict[str, tuple[str, str, str]] = {}
    for group_id, group in groups.items():
        section_id = str(group.get("section_id") or "")
        for fact in group.get("facts") or []:
            if not isinstance(fact, Mapping):
                continue
            unit_id = str(fact.get("fact_unit_id") or "")
            evidence_id = str(fact.get("evidence_id") or "")
            if unit_id and evidence_id:
                units[unit_id] = (group_id, section_id, evidence_id)

    issues: list[dict[str, Any]] = []
    ignored_model_evidence_field_count = 0
    bound_fact_count = 0
    bound_evidence_count = 0
    for section in bound.get("sections") or []:
        if not isinstance(section, dict):
            continue
        section_id = str(section.get("section_id") or "")
        for index, sentence in enumerate(section.get("sentences") or []):
            if not isinstance(sentence, dict):
                continue
            location = {"section_id": section_id, "index": index}
            if "evidence_id" in sentence or "evidence_ids" in sentence:
                ignored_model_evidence_field_count += 1
            sentence_type = str(sentence.get("sentence_type") or "")
            group_id = str(sentence.get("group_id") or "")
            text = str(sentence.get("text") or "")
            raw_unit_ids = sentence.get("fact_unit_ids")
            unit_ids = (
                list(dict.fromkeys(
                    str(value).strip()
                    for value in raw_unit_ids
                    if str(value).strip()
                ))
                if isinstance(raw_unit_ids, list)
                else []
            )
            metadata = (
                {"subsection_id": sentence["subsection_id"]}
                if "subsection_id" in sentence else {}
            )
            if sentence_type == "ANALYSIS":
                if group_id or unit_ids:
                    issues.append({"rule": "analysis_writer_unit_not_empty", **location})
                sentence.clear()
                sentence.update({
                    "sentence_type": "ANALYSIS",
                    "evidence_ids": [],
                    "text": text,
                    **metadata,
                })
                continue
            if sentence_type != "FACT" or not group_id or not unit_ids:
                issues.append({"rule": "fact_writer_unit_binding_missing", **location})
                sentence.clear()
                sentence.update({
                    "sentence_type": sentence_type,
                    "evidence_ids": [],
                    "text": text,
                    **metadata,
                })
                continue
            group = groups.get(group_id)
            if group is None:
                issues.append({"rule": "fact_group_id_unknown", "group_id": group_id, **location})
            elif str(group.get("section_id") or "") != section_id:
                issues.append({"rule": "fact_group_cross_section", "group_id": group_id, **location})
            evidence_ids: list[str] = []
            for unit_id in unit_ids:
                binding = units.get(unit_id)
                if binding is None:
                    issues.append({"rule": "fact_unit_id_unknown", "fact_unit_id": unit_id, **location})
                    continue
                unit_group_id, unit_section_id, evidence_id = binding
                if unit_group_id != group_id or unit_section_id != section_id:
                    issues.append({"rule": "fact_unit_binding_mismatch", "fact_unit_id": unit_id, **location})
                    continue
                evidence_ids.append(evidence_id)
            sentence.clear()
            sentence.update({
                "sentence_type": "FACT",
                "evidence_ids": list(dict.fromkeys(evidence_ids)),
                "text": text,
                **metadata,
            })
            if evidence_ids:
                bound_fact_count += 1
                bound_evidence_count += len(set(evidence_ids))

    return bound, {
        "schema_version": WRITER_BINDING_SCHEMA_VERSION,
        "passed": not issues,
        "issues": issues,
        "issue_count": len(issues),
        "ignored_model_evidence_field_count": ignored_model_evidence_field_count,
        "bound_fact_count": bound_fact_count,
        "bound_evidence_count": bound_evidence_count,
        "external_model_calls": 0,
    }
