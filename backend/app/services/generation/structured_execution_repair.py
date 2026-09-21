"""Bounded repair loop for V3 structured Writer execution failures.

This module does not own delivery or HTTP decisions.  It classifies the
validator report, applies at most one repair round, and re-runs the unchanged
parse/binding/validation path supplied by FLOW3.
"""

from __future__ import annotations

import copy
import hashlib
import itertools
import json
from typing import Any, Callable

from app.models.flow4 import PARAGRAPH_FIELDS, preserve_paragraph_identity

from app.services.generation.flow3_v3_trace import (
    WRITER_EXECUTION_FIELDS,
    WRITER_NO_CHAIN_ROLE,
    normalize_paragraph_structured_draft,
    parse_and_validate_structured_draft,
    parse_structured_draft,
)
from app.services.generation.work_summary_evidence_group import (
    writer_visible_evidence_groups,
)


MAX_STRUCTURED_REPAIR_ROUNDS = 1
STRUCTURED_REPAIR_RESULT_SCHEMA_VERSION = "structured-execution-repair-result-1"
REPAIR_STATUSES = {
    "NOT_NEEDED",
    "REPAIRED",
    "REPAIR_FAILED",
    "UNREPAIRABLE",
}

ModelRepairer = Callable[[dict[str, Any]], str]


def _stable_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _article_exists(draft: dict[str, Any] | None) -> bool:
    if not isinstance(draft, dict):
        return False
    if str(draft.get("introduction") or "").strip():
        return True
    return any(
        str(sentence.get("text") or "").strip()
        for section in draft.get("sections") or []
        if isinstance(section, dict)
        for sentence in section.get("sentences") or []
        if isinstance(sentence, dict)
    )


def _hard_issues(report: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        copy.deepcopy(issue)
        for issue in report.get("hard_issues") or []
        if isinstance(issue, dict)
    ]


def _only_writer_execution_issues(issues: list[dict[str, Any]]) -> bool:
    """Allow the strict schema echo caused only by missing execution fields."""

    if not issues:
        return False
    missing_by_location = {
        (str(issue.get("section_id") or ""), int(issue.get("index") or 0)): {
            str(field) for field in issue.get("fields") or []
        }
        for issue in issues
        if issue.get("rule") == "writer_execution_fields_missing"
    }
    allowed_sentence_fields = {
        "sentence_type",
        "text",
        "evidence_id",
        "evidence_ids",
        "subsection_id",
        *PARAGRAPH_FIELDS,
        *WRITER_EXECUTION_FIELDS,
    }
    for issue in issues:
        rule = str(issue.get("rule") or "")
        if rule.startswith("writer_execution_"):
            continue
        if rule != "sentence_fields":
            return False
        location = (
            str(issue.get("section_id") or ""),
            int(issue.get("index") or 0),
        )
        actual = {str(field) for field in issue.get("actual") or []}
        missing = missing_by_location.get(location) or set()
        if (
            not missing
            or not actual.issubset(allowed_sentence_fields)
            or not missing.issubset(set(WRITER_EXECUTION_FIELDS))
            or not missing.isdisjoint(actual)
        ):
            return False
    return True


def classify_structured_repair(
    draft: dict[str, Any] | None,
    structured_report: dict[str, Any],
) -> dict[str, Any]:
    """Return a decision input without mutating the draft or choosing delivery."""

    issues = _hard_issues(structured_report)
    exists = _article_exists(draft)
    if bool(structured_report.get("passed")):
        validation_status = "PASS"
        recommended_action = "CONTINUE"
        reason = "structured_and_writer_execution_checks_passed"
    elif (
        exists
        and int(structured_report.get("invalid_evidence_reference_count") or 0)
        == 0
        and _only_writer_execution_issues(issues)
    ):
        validation_status = "FAIL_REPAIRABLE"
        recommended_action = "REPAIR"
        reason = "writer_execution_contract_failure_with_article"
    else:
        validation_status = "FAIL_UNREPAIRABLE"
        recommended_action = "RETURN_UNREPAIRED_STATE"
        reason = "failure_is_not_limited_to_writer_execution_contract"
    return {
        "phase": "CHECK",
        "validation_status": validation_status,
        "recommended_action": recommended_action,
        "reason": reason,
        "issue_count": len(issues),
        "issues": issues,
        "article_exists": exists,
        "article_recoverable": validation_status == "FAIL_REPAIRABLE",
        "max_repair_rounds": MAX_STRUCTURED_REPAIR_ROUNDS,
    }


def _ordered_values(rows: Any, value_field: str) -> list[str]:
    values: list[tuple[int, int, str]] = []
    for index, row in enumerate(rows or [], 1):
        if not isinstance(row, dict):
            continue
        value = str(row.get(value_field) or "").strip()
        if value:
            values.append((int(row.get("order") or index), index, value))
    values.sort()
    return list(dict.fromkeys(value for _order, _index, value in values))


def _repair_unique_metadata_field(
    sentences: list[dict[str, Any]],
    *,
    field: str,
    allowed_values: list[str],
    ordered: bool,
) -> bool:
    """Fill only values for which the unchanged contract has one solution."""

    allowed = list(dict.fromkeys(value for value in allowed_values if value))
    if not sentences or not allowed:
        return False
    invalid_positions = [
        index
        for index, sentence in enumerate(sentences)
        if str(sentence.get(field) or "").strip() not in allowed
    ]
    if not invalid_positions:
        return False

    current = [str(sentence.get(field) or "").strip() for sentence in sentences]
    current_valid = {value for value in current if value in allowed}
    missing = [value for value in allowed if value not in current_valid]
    assignments: list[tuple[str, ...]] = []
    if len(allowed) == 1:
        assignments = [tuple(allowed[0] for _ in invalid_positions)]
    elif not ordered:
        if len(invalid_positions) == 1 and len(missing) == 1:
            assignments = [(missing[0],)]
    else:
        combination_count = len(allowed) ** len(invalid_positions)
        if combination_count <= 4096:
            order_index = {value: index for index, value in enumerate(allowed)}
            for candidate in itertools.product(allowed, repeat=len(invalid_positions)):
                proposed = list(current)
                for position, value in zip(invalid_positions, candidate):
                    proposed[position] = value
                if set(proposed) != set(allowed):
                    continue
                proposed_order = [order_index[value] for value in proposed]
                if proposed_order != sorted(proposed_order):
                    continue
                assignments.append(candidate)
                if len(assignments) > 1:
                    break
    if len(assignments) != 1:
        return False
    changed = False
    for position, value in zip(invalid_positions, assignments[0]):
        if sentences[position].get(field) != value:
            sentences[position][field] = value
            changed = True
    return changed


def _unit_to_chain(control_section: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for chain in control_section.get("work_chain_organization") or []:
        if not isinstance(chain, dict):
            continue
        chain_id = str(chain.get("chain_id") or "")
        for unit in chain.get("member_writer_units") or []:
            if not isinstance(unit, dict):
                continue
            unit_id = str(unit.get("fact_unit_id") or "")
            if unit_id and chain_id:
                result[unit_id] = chain_id
    return result


def _repair_deterministic_metadata(
    source_draft: dict[str, Any],
    writer_plan_control: dict[str, Any] | None,
) -> list[str]:
    if not isinstance(writer_plan_control, dict):
        return []
    controls = {
        str(section.get("section_id") or ""): section
        for section in writer_plan_control.get("sections") or []
        if isinstance(section, dict)
    }
    repaired_sections: list[str] = []
    for section in source_draft.get("sections") or []:
        if not isinstance(section, dict):
            continue
        section_id = str(section.get("section_id") or "")
        control = controls.get(section_id)
        if not isinstance(control, dict):
            continue
        contract = control.get("execution_contract") or {}
        sentences = [
            sentence
            for sentence in section.get("sentences") or []
            if isinstance(sentence, dict)
        ]
        changed = False
        field_specs = (
            (
                "plan_step",
                _ordered_values(contract.get("plan_steps"), "plan_step"),
                True,
            ),
            (
                "expansion_stage",
                _ordered_values(
                    contract.get("expansion_stages"), "expansion_stage"
                ),
                True,
            ),
            (
                "writing_focus_role",
                _ordered_values(
                    contract.get("writing_focus_roles"),
                    "writing_focus_role",
                ),
                False,
            ),
            (
                "content_function",
                [
                    str(value).strip()
                    for value in contract.get("allowed_content_functions") or []
                    if str(value).strip()
                ],
                False,
            ),
        )
        for field, values, ordered in field_specs:
            changed = (
                _repair_unique_metadata_field(
                    sentences,
                    field=field,
                    allowed_values=values,
                    ordered=ordered,
                )
                or changed
            )

        allowed_chain_roles = {
            str(value)
            for value in contract.get("allowed_work_chain_roles") or []
            if str(value)
        }
        unit_to_chain = _unit_to_chain(control)
        for sentence in sentences:
            expected_role = ""
            if str(sentence.get("sentence_type") or "") == "ANALYSIS":
                expected_role = WRITER_NO_CHAIN_ROLE
            elif str(sentence.get("sentence_type") or "") == "FACT":
                roles = {
                    unit_to_chain[str(unit_id)]
                    for unit_id in sentence.get("fact_unit_ids") or []
                    if str(unit_id) in unit_to_chain
                }
                if len(roles) == 1:
                    expected_role = next(iter(roles))
            if (
                expected_role
                and expected_role in allowed_chain_roles
                and sentence.get("work_chain_role") != expected_role
            ):
                sentence["work_chain_role"] = expected_role
                changed = True
        if changed:
            repaired_sections.append(section_id)
    return repaired_sections


def _section_issue_map(issues: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for issue in issues:
        section_id = str(issue.get("section_id") or "")
        if section_id:
            result.setdefault(section_id, []).append(copy.deepcopy(issue))
    return result


def _without_internal_evidence_ids(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_internal_evidence_ids(item)
            for key, item in value.items()
            if "evidence_id" not in str(key).lower()
        }
    if isinstance(value, list):
        return [_without_internal_evidence_ids(item) for item in value]
    return copy.deepcopy(value)


def _frozen_plan_payload(frozen_article_plan: Any) -> dict[str, Any]:
    if isinstance(frozen_article_plan, dict):
        return copy.deepcopy(frozen_article_plan)
    to_dict = getattr(frozen_article_plan, "to_dict", None)
    if callable(to_dict):
        value = to_dict()
        return copy.deepcopy(value) if isinstance(value, dict) else {}
    return {}


def _matching_section(sections: Any, section_id: str) -> dict[str, Any]:
    return copy.deepcopy(next(
        (
            section
            for section in sections or []
            if isinstance(section, dict)
            and str(section.get("section_id") or "") == section_id
        ),
        {},
    ))


def _model_repair_payload(
    *,
    failed_section: dict[str, Any],
    frozen_article_plan: Any,
    writer_plan_control: dict[str, Any] | None,
    evidence_group_pack: dict[str, Any] | None,
    validation_issues: list[dict[str, Any]],
) -> dict[str, Any]:
    section_id = str(failed_section.get("section_id") or "")
    frozen_payload = _frozen_plan_payload(frozen_article_plan)
    frozen_section = _matching_section(
        (frozen_payload.get("content_plan") or {}).get("sections") or [],
        section_id,
    )
    control_section = _matching_section(
        (writer_plan_control or {}).get("sections") or [],
        section_id,
    )
    legal_groups = [
        group
        for group in writer_visible_evidence_groups(evidence_group_pack or {})
        if str(group.get("section_id") or "") == section_id
    ]
    return {
        "schema_version": "structured-execution-model-repair-input-1",
        "repair_round": 1,
        "max_repair_rounds": MAX_STRUCTURED_REPAIR_ROUNDS,
        "section_id": section_id,
        "failed_section": copy.deepcopy(failed_section),
        "frozen_article_plan_section": _without_internal_evidence_ids(
            frozen_section
        ),
        "writer_execution_contract_section": copy.deepcopy(control_section),
        "legal_evidence_groups": legal_groups,
        "validation_issues": copy.deepcopy(validation_issues),
    }


def _validate_model_section_units(
    section: dict[str, Any],
    evidence_group_pack: dict[str, Any] | None,
) -> None:
    section_id = str(section.get("section_id") or "")
    legal_units: dict[str, str] = {}
    for group in (evidence_group_pack or {}).get("groups") or []:
        if (
            not isinstance(group, dict)
            or str(group.get("section_id") or "") != section_id
        ):
            continue
        group_id = str(group.get("group_id") or "")
        for fact in group.get("facts") or []:
            if isinstance(fact, dict) and fact.get("fact_unit_id"):
                legal_units[str(fact["fact_unit_id"])] = group_id
    for index, sentence in enumerate(section.get("sentences") or []):
        if not isinstance(sentence, dict):
            raise ValueError(f"model repair sentence is not an object: {index}")
        if "evidence_id" in sentence or "evidence_ids" in sentence:
            raise ValueError("model repair must not emit internal evidence IDs")
        if str(sentence.get("sentence_type") or "") != "FACT":
            continue
        group_id = str(sentence.get("group_id") or "")
        unit_ids = [
            str(value)
            for value in sentence.get("fact_unit_ids") or []
            if str(value)
        ]
        if not group_id or not unit_ids:
            raise ValueError("model repair FACT must retain legal Writer units")
        if any(legal_units.get(unit_id) != group_id for unit_id in unit_ids):
            raise ValueError("model repair introduced an illegal Writer unit")


def _merge_model_repaired_section(
    source_draft: dict[str, Any],
    *,
    section_id: str,
    raw_response: str,
    evidence_group_pack: dict[str, Any] | None,
) -> dict[str, Any]:
    parsed = parse_structured_draft(raw_response)
    if set(parsed) != {"section"} or not isinstance(parsed.get("section"), dict):
        raise ValueError("model repair output must be exactly one section wrapper")
    repaired_section = parsed["section"]
    if str(repaired_section.get("section_id") or "") != section_id:
        raise ValueError("model repair changed section identity")
    _validate_model_section_units(repaired_section, evidence_group_pack)
    merged = copy.deepcopy(source_draft)
    matches = [
        index
        for index, section in enumerate(merged.get("sections") or [])
        if isinstance(section, dict)
        and str(section.get("section_id") or "") == section_id
    ]
    if len(matches) != 1:
        raise ValueError("model repair target section is not unique")
    preserve_paragraph_identity([merged["sections"][matches[0]]], [repaired_section])
    merged["sections"][matches[0]] = copy.deepcopy(repaired_section)
    return merged


def _recheck(
    source_draft: dict[str, Any],
    *,
    content_plan: dict[str, Any],
    fact_anchor_pack: dict[str, Any],
    introduction_contract: dict[str, Any] | None,
    writing_structure_overlay: dict[str, Any] | None,
    evidence_group_pack: dict[str, Any] | None,
    writer_plan_control: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    return parse_and_validate_structured_draft(
        json.dumps(source_draft, ensure_ascii=False),
        content_plan,
        fact_anchor_pack,
        introduction_contract,
        writing_structure_overlay,
        evidence_group_pack,
        writer_plan_control,
    )


def _repair_result(
    *,
    status: str,
    repair_round: int,
    repair_type: str,
    original_issues: list[dict[str, Any]],
    repaired_sections: list[str],
    remaining_issues: list[dict[str, Any]],
    draft: dict[str, Any] | None,
    model_call_count: int,
    fact_anchor_unchanged: bool,
    evidence_group_unchanged: bool,
    model_errors: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    if status not in REPAIR_STATUSES:
        raise ValueError(f"unsupported structured repair status: {status}")
    article_exists = _article_exists(draft)
    article_recoverable = bool(
        article_exists
        and (
            not remaining_issues
            or _only_writer_execution_issues(remaining_issues)
        )
    )
    return {
        "schema_version": STRUCTURED_REPAIR_RESULT_SCHEMA_VERSION,
        "repair_status": status,
        "repair_round": repair_round,
        "max_repair_rounds": MAX_STRUCTURED_REPAIR_ROUNDS,
        "repair_type": repair_type,
        "original_issues": original_issues,
        "repaired_sections": list(dict.fromkeys(repaired_sections)),
        "remaining_issues": remaining_issues,
        "article_exists": article_exists,
        "article_recoverable": article_recoverable,
        "next_step": (
            "CONTINUE_EVIDENCE_BINDING_AND_FACT_SAFETY"
            if status in {"NOT_NEEDED", "REPAIRED"}
            else "RETURN_UNREPAIRED_STATE"
        ),
        "model_call_count": model_call_count,
        "fact_anchor_unchanged": fact_anchor_unchanged,
        "evidence_group_unchanged": evidence_group_unchanged,
        "model_errors": list(model_errors or []),
        "http_status": None,
    }


def run_structured_execution_repair(
    *,
    raw_response: str,
    initial_draft: dict[str, Any] | None,
    initial_report: dict[str, Any],
    content_plan: dict[str, Any],
    fact_anchor_pack: dict[str, Any],
    introduction_contract: dict[str, Any] | None = None,
    writing_structure_overlay: dict[str, Any] | None = None,
    evidence_group_pack: dict[str, Any] | None = None,
    writer_plan_control: dict[str, Any] | None = None,
    frozen_article_plan: Any = None,
    model_repairer: ModelRepairer | None = None,
) -> dict[str, Any]:
    """Apply one deterministic-first repair round and the unchanged recheck."""

    classification = classify_structured_repair(initial_draft, initial_report)
    original_issues = _hard_issues(initial_report)
    anchor_hash_before = _stable_sha256(fact_anchor_pack)
    group_hash_before = _stable_sha256(evidence_group_pack or {})
    if classification["validation_status"] == "PASS":
        result = _repair_result(
            status="NOT_NEEDED",
            repair_round=0,
            repair_type="NONE",
            original_issues=[],
            repaired_sections=[],
            remaining_issues=[],
            draft=initial_draft,
            model_call_count=0,
            fact_anchor_unchanged=True,
            evidence_group_unchanged=True,
        )
        result.update({
            "original_candidate_digest": _stable_sha256(initial_draft),
            "repair_candidate_digest": _stable_sha256(initial_draft),
            "returned_candidate_digest": _stable_sha256(initial_draft),
            "original_preserved": True,
            "model_call_budget": 1,
            "model_call_budget_exhausted": False,
        })
        return {
            "structured_draft": initial_draft,
            "structured_report": initial_report,
            "repair_result": result,
        }
    if classification["validation_status"] != "FAIL_REPAIRABLE":
        result = _repair_result(
            status="UNREPAIRABLE",
            repair_round=0,
            repair_type="NONE",
            original_issues=original_issues,
            repaired_sections=[],
            remaining_issues=original_issues,
            draft=initial_draft,
            model_call_count=0,
            fact_anchor_unchanged=(anchor_hash_before == _stable_sha256(fact_anchor_pack)),
            evidence_group_unchanged=(group_hash_before == _stable_sha256(evidence_group_pack or {})),
        )
        result.update({
            "original_candidate_digest": _stable_sha256(initial_draft),
            "repair_candidate_digest": _stable_sha256(initial_draft),
            "returned_candidate_digest": _stable_sha256(initial_draft),
            "original_preserved": True,
            "model_call_budget": 1,
            "model_call_budget_exhausted": False,
        })
        return {
            "structured_draft": initial_draft,
            "structured_report": initial_report,
            "repair_result": result,
        }

    try:
        source_draft = parse_structured_draft(raw_response)
        source_draft, _paragraph_issues = normalize_paragraph_structured_draft(
            source_draft,
            writing_structure_overlay,
        )
        preserve_paragraph_identity(
            (initial_draft or {}).get("sections") or [],
            source_draft.get("sections") or [],
            positional_text_identity=True,
        )
    except (json.JSONDecodeError, ValueError) as exc:
        remaining = original_issues + [{
            "rule": ("PARAGRAPH_IDENTITY_LOST" if "PARAGRAPH_IDENTITY_LOST" in str(exc)
                     else "structured_repair_source_parse_failed"),
            "error_type": type(exc).__name__,
        }]
        result = _repair_result(
            status="REPAIR_FAILED",
            repair_round=0,
            repair_type="NONE",
            original_issues=original_issues,
            repaired_sections=[],
            remaining_issues=remaining,
            draft=initial_draft,
            model_call_count=0,
            fact_anchor_unchanged=True,
            evidence_group_unchanged=True,
        )
        result.update({
            "original_candidate_digest": _stable_sha256(initial_draft),
            "repair_candidate_digest": _stable_sha256(initial_draft),
            "returned_candidate_digest": _stable_sha256(initial_draft),
            "original_preserved": True,
            "model_call_budget": 1,
            "model_call_budget_exhausted": False,
        })
        return {
            "structured_draft": initial_draft,
            "structured_report": initial_report,
            "repair_result": result,
        }

    repaired_sections = _repair_deterministic_metadata(
        source_draft,
        writer_plan_control,
    )
    deterministic_used = bool(repaired_sections)
    current_draft, current_report = _recheck(
        source_draft,
        content_plan=content_plan,
        fact_anchor_pack=fact_anchor_pack,
        introduction_contract=introduction_contract,
        writing_structure_overlay=writing_structure_overlay,
        evidence_group_pack=evidence_group_pack,
        writer_plan_control=writer_plan_control,
    )
    current_issues = _hard_issues(current_report)
    model_call_count = 0
    model_errors: list[dict[str, str]] = []
    model_used = False

    if not current_report.get("passed") and _only_writer_execution_issues(
        current_issues
    ) and model_repairer is not None:
        issues_by_section = _section_issue_map(current_issues)
        for section in list(source_draft.get("sections") or []):
            if not isinstance(section, dict):
                continue
            section_id = str(section.get("section_id") or "")
            if section_id not in issues_by_section:
                continue
            if model_call_count >= 1:
                model_errors.append({
                    "section_id": section_id,
                    "error_type": "STRUCTURED_MODEL_REPAIR_BUDGET_EXHAUSTED",
                    "message": "existing structured repair allowance is one request per article",
                })
                continue
            payload = _model_repair_payload(
                failed_section=section,
                frozen_article_plan=frozen_article_plan,
                writer_plan_control=writer_plan_control,
                evidence_group_pack=evidence_group_pack,
                validation_issues=issues_by_section[section_id],
            )
            try:
                model_call_count += 1
                raw_repair = model_repairer(payload)
                source_draft = _merge_model_repaired_section(
                    source_draft,
                    section_id=section_id,
                    raw_response=str(raw_repair),
                    evidence_group_pack=evidence_group_pack,
                )
                repaired_sections.append(section_id)
                model_used = True
            except Exception as exc:  # Provider/model output failure is reportable state.
                model_errors.append({
                    "section_id": section_id,
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                })
        current_draft, current_report = _recheck(
            source_draft,
            content_plan=content_plan,
            fact_anchor_pack=fact_anchor_pack,
            introduction_contract=introduction_contract,
            writing_structure_overlay=writing_structure_overlay,
            evidence_group_pack=evidence_group_pack,
            writer_plan_control=writer_plan_control,
        )
        current_issues = _hard_issues(current_report)

    passed = bool(current_report.get("passed"))
    if passed:
        status = "REPAIRED"
    elif not _only_writer_execution_issues(current_issues):
        status = "UNREPAIRABLE"
    else:
        status = "REPAIR_FAILED"
    repair_type = (
        "DETERMINISTIC_AND_MODEL"
        if deterministic_used and model_used
        else "DETERMINISTIC"
        if deterministic_used
        else "MODEL"
        if model_used or model_call_count
        else "NONE"
    )
    result = _repair_result(
        status=status,
        repair_round=1 if deterministic_used or model_call_count else 0,
        repair_type=repair_type,
        original_issues=original_issues,
        repaired_sections=repaired_sections,
        remaining_issues=current_issues,
        draft=current_draft,
        model_call_count=model_call_count,
        fact_anchor_unchanged=(anchor_hash_before == _stable_sha256(fact_anchor_pack)),
        evidence_group_unchanged=(group_hash_before == _stable_sha256(evidence_group_pack or {})),
        model_errors=model_errors,
    )
    # A failed/partial repair remains an auditable Candidate but never replaces
    # the byte-equivalent Original returned to the orchestrator.
    repair_candidate = copy.deepcopy(current_draft)
    returned_draft = current_draft if status == "REPAIRED" else initial_draft
    returned_report = current_report if status == "REPAIRED" else initial_report
    result.update({
        "original_candidate_digest": _stable_sha256(initial_draft),
        "repair_candidate_digest": _stable_sha256(repair_candidate),
        "returned_candidate_digest": _stable_sha256(returned_draft),
        "original_preserved": status != "REPAIRED",
        "model_call_budget": 1,
        "model_call_budget_exhausted": bool(
            any(
                item.get("error_type") == "STRUCTURED_MODEL_REPAIR_BUDGET_EXHAUSTED"
                for item in model_errors
            )
        ),
    })
    return {
        "structured_draft": returned_draft,
        "structured_report": returned_report,
        "repair_candidate": repair_candidate,
        "repair_result": result,
    }
