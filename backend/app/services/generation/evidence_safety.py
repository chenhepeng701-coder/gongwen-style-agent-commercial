"""Deterministic post-Writer FACT safety coordination.

This module owns LD-04 failure disposition and the single local-repair pass.  It
does not classify source facts, build anchors, plan sections, or implement a
second validator.  Callers inject the existing FACT and structured validators.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from difflib import SequenceMatcher
from enum import Enum
from typing import Any, Callable


EVIDENCE_SAFETY_VERSION = "evidence-safety-local-risk-isolation-4"
MAX_LOCAL_REPAIR_ROUNDS = 1
MAX_LOCAL_ISOLATION_COUNT = 1


class FactSafetyRiskClass(str, Enum):
    A_HARD_BLOCK = "A_HARD_BLOCK"
    B_SAFE_REPAIR = "B_SAFE_REPAIR"
    C_PASS_WITH_EQUIVALENCE = "C_PASS_WITH_EQUIVALENCE"
    D_REVIEW_REQUIRED = "D_REVIEW_REQUIRED"


class FactSafetyARiskSubclass(str, Enum):
    A1_SAFE_REPAIR = "A1_SAFE_REPAIR"
    A2_HARD_BLOCK = "A2_HARD_BLOCK"


class FactFailureClassification(str, Enum):
    LOCAL_FIXABLE = "LOCAL_FIXABLE"
    SECTION_REWRITE_REQUIRED = "SECTION_REWRITE_REQUIRED"
    DRAFT_REWRITE_REQUIRED = "DRAFT_REWRITE_REQUIRED"
    HARD_BLOCK = "HARD_BLOCK"


class DraftSafetyStatus(str, Enum):
    PASS = "PASS"
    PASS_WITH_LOCAL_REPAIR = "PASS_WITH_LOCAL_REPAIR"
    REVIEW_REQUIRED_DRAFT = "REVIEW_REQUIRED_DRAFT"
    BLOCKED_SECTION_REWRITE_REQUIRED = "BLOCKED_SECTION_REWRITE_REQUIRED"
    BLOCKED_DRAFT_REWRITE_REQUIRED = "BLOCKED_DRAFT_REWRITE_REQUIRED"
    BLOCKED_HARD_SAFETY = "BLOCKED_HARD_SAFETY"


_CLAUSE_RE = re.compile(r"([^，,；;。！？!?]+)([，,；;。！？!?]*)")
_HAN_RE = re.compile(r"[\u3400-\u9fff]")
_LEADING_RESIDUE_RE = re.compile(
    r"^(?:并且|并|且|以及|同时|此外|进而|从而|因而|所以|因此|针对该问题|对此)\s*$"
)
_TRAILING_RESIDUE_RE = re.compile(
    r"(?:并且|并|且|以及|同时|此外|进而|从而|因而|所以|因此|针对|围绕|通过|对|向|为|与|和|及)$"
)
_TARGETED_REPAIR_INPUT_FIELDS = {
    "risk_sentence",
    "risk_type",
    "evidence_ids",
    "original_fact_status",
    "suggested_downgrade_action",
}
_TARGETED_STRENGTH_DOWNGRADES = {
    "DROP_COMPLETION_INTENSIFIER": (
        ("全面完成", "完成"),
        ("全部完成", "完成"),
        ("圆满完成", "完成"),
    ),
    "DROP_EFFECT_INTENSIFIER": (
        ("明显提升", "提升"),
        ("显著提升", "提升"),
    ),
    "DOWNGRADE_DURABLE_MECHANISM": (
        ("形成长效机制", "建立机制"),
    ),
}
_RETAIN_CLAUSE_ACTION_RE = re.compile(r"RETAIN_ORIGINAL_CLAUSE_(\d+)")
_LOCALLY_ISOLATABLE_A2_REASONS = {
    "unsupported_hard_fact",
    "semantic_state_mismatch",
}


def _sha256_text(value: Any) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _normalized(value: Any) -> str:
    return re.sub(r"[^\u3400-\u9fffA-Za-z0-9]", "", str(value or ""))


def _char_ngrams(value: Any, size: int = 2) -> set[str]:
    text = _normalized(value)
    if not text:
        return set()
    if len(text) < size:
        return {text}
    return {text[index:index + size] for index in range(len(text) - size + 1)}


def _overlap_score(left: Any, right: Any) -> float:
    left_grams = _char_ngrams(left)
    right_grams = _char_ngrams(right)
    if not left_grams or not right_grams:
        return 0.0
    return len(left_grams & right_grams) / max(1, len(left_grams))


def _split_clauses(text: str) -> list[str]:
    return [
        match.group(1).strip()
        for match in _CLAUSE_RE.finditer(str(text or ""))
        if match.group(1).strip()
    ]


def local_repair_structural_sanity(text: str) -> bool:
    """Reject empty, punctuation-only, connector-only, or visibly incomplete text."""

    cleaned = str(text or "").strip(" \t\r\n，,；;。！？!?：:")
    if len(_HAN_RE.findall(cleaned)) < 4:
        return False
    if _LEADING_RESIDUE_RE.fullmatch(cleaned):
        return False
    if _TRAILING_RESIDUE_RE.search(cleaned):
        return False
    return True


def select_local_deletion_candidate(
    original_text: str,
    *,
    source_text: str,
    anchor_terms: list[str] | tuple[str, ...] = (),
    risk_fragments: list[str] | tuple[str, ...] = (),
) -> dict[str, Any] | None:
    """Propose deletion of one isolated unsupported proposition, not its peers.

    Exact bound-source coverage locates the *other* propositions conservatively.
    Multiple uncertain clauses or an attached qualifier require human review.
    This is only a proposal: subject, state, binding and full validation still
    have to pass before it can be applied. No prose is added or regenerated.
    """

    matches = list(_CLAUSE_RE.finditer(str(original_text or "")))
    source = _normalized(source_text)
    if len(matches) < 2 or not source:
        return None
    supported = [bool(_normalized(m.group(1)) and _normalized(m.group(1)) in source
                      and local_repair_structural_sanity(m.group(1))) for m in matches]
    # A source span may itself contain a second fact outside its Anchor. Reuse
    # the validator's located component payload rather than treating that fact
    # as authorized just because it occurs in the surrounding source text.
    for i, match in enumerate(matches):
        if any(_normalized(v) and _normalized(v) in _normalized(match.group(1)) for v in risk_fragments):
            supported[i] = False
    uncertain = [i for i, proved in enumerate(supported) if not proved]
    if len(uncertain) != 1:
        return None
    index = uncertain[0]
    removed = matches[index].group(1)
    # A clause can govern its neighbours. Never turn a qualified/negative plan
    # into an unconditional assertion by dropping that scope-bearing clause.
    if re.search(r"如果|若|只有|除非|^在.+(?:前|后|时|条件下)|(?:前|后|时)$|截至|未|不|不得|尚|计划|拟|将|力争|^\s*(?:在|到|于)?\d{4}年", removed):
        return None
    retained = "".join(m.group(0) for i, m in enumerate(matches) if i != index)
    repaired = f"{retained.rstrip('，,；;。！？!?')}。"
    if _normalized(repaired) == _normalized(original_text):
        return None
    return {
        "text": repaired,
        "repair_strategy": "candidate_safe_clause_retention",
        "removed_clause_count": 1,
        "removed_spans": [{"start": matches[index].start(), "end": matches[index].end(),
                           "original_text": matches[index].group(0), "replacement": ""}],
    }


def suggested_strength_downgrade_action(text: str) -> str:
    """Return one closed-list downgrade action; never infer new wording."""

    for action, replacements in _TARGETED_STRENGTH_DOWNGRADES.items():
        if any(before in str(text or "") for before, _after in replacements):
            return action
    return ""


def build_targeted_safe_repair_input(
    *,
    risk_sentence: str,
    risk_type: str,
    evidence_ids: list[str] | tuple[str, ...],
    original_fact_status: str,
    suggested_downgrade_action: str,
) -> dict[str, Any]:
    """Build the only five-field input accepted by targeted FACT repair."""

    return {
        "risk_sentence": str(risk_sentence or ""),
        "risk_type": str(risk_type or ""),
        "evidence_ids": [str(value) for value in evidence_ids if str(value)],
        "original_fact_status": str(original_fact_status or ""),
        "suggested_downgrade_action": str(suggested_downgrade_action or ""),
    }


def targeted_safe_repair_candidate(
    repair_input: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Apply one closed-list sentence downgrade without consulting a Writer."""

    if not isinstance(repair_input, dict):
        return None
    if set(repair_input) != _TARGETED_REPAIR_INPUT_FIELDS:
        return None
    original = str(repair_input.get("risk_sentence") or "")
    risk_type = str(repair_input.get("risk_type") or "")
    fact_status = str(repair_input.get("original_fact_status") or "")
    evidence_ids = repair_input.get("evidence_ids")
    action = str(repair_input.get("suggested_downgrade_action") or "")
    if (
        not original.strip()
        or risk_type not in {"FACT_STRENGTH_UPGRADE", "SEMANTIC_STATE_UPGRADE"}
        or not fact_status
        or not isinstance(evidence_ids, list)
        or not evidence_ids
        or any(not isinstance(value, str) or not value for value in evidence_ids)
        or len(evidence_ids) != len(set(evidence_ids))
    ):
        return None

    repaired = original
    strategy = ""
    removed_clause_count = 0
    replacements = _TARGETED_STRENGTH_DOWNGRADES.get(action)
    if replacements and risk_type == "FACT_STRENGTH_UPGRADE":
        for before, after in replacements:
            if before in repaired:
                repaired = repaired.replace(before, after, 1)
                strategy = f"targeted_strength_downgrade:{action}"
                break
    else:
        match = _RETAIN_CLAUSE_ACTION_RE.fullmatch(action)
        clauses = _split_clauses(original)
        if match and risk_type == "SEMANTIC_STATE_UPGRADE":
            index = int(match.group(1)) - 1
            if 0 <= index < len(clauses):
                repaired = f"{clauses[index].rstrip('，,；;。！？!?')}。"
                strategy = "targeted_unsupported_clause_deletion"
                removed_clause_count = len(clauses) - 1

    if (
        not strategy
        or _normalized(repaired) == _normalized(original)
        or not local_repair_structural_sanity(repaired)
    ):
        return None
    return {
        "text": repaired,
        "repair_strategy": strategy,
        "removed_clause_count": removed_clause_count,
        "suggested_downgrade_action": action,
    }


def _protected_draft_snapshot(draft: dict[str, Any]) -> dict[str, Any]:
    snapshot = copy.deepcopy(draft)
    for section in snapshot.get("sections") or []:
        for sentence in section.get("sentences") or []:
            if sentence.get("sentence_type") == "FACT":
                sentence["text"] = "<FACT_TEXT_MUTABLE_BY_EVIDENCE_SAFETY>"
    return snapshot


def _sentence_evidence_ids(sentence: dict[str, Any]) -> tuple[str, ...]:
    values = sentence.get("evidence_ids")
    if isinstance(values, list):
        return tuple(str(value) for value in values if str(value))
    value = str(sentence.get("evidence_id") or "")
    return (value,) if value else ()


def _set_sentence_evidence_ids(
    sentence: dict[str, Any], evidence_ids: list[str] | tuple[str, ...]
) -> None:
    """Retain the original binding shape while only removing declarations."""

    retained = [str(value) for value in evidence_ids if str(value)]
    if "evidence_ids" in sentence:
        sentence["evidence_ids"] = retained
    else:
        sentence["evidence_id"] = retained[0] if len(retained) == 1 else ""


def _sentence_at(
    draft: dict[str, Any], section_id: str, sentence_index: int
) -> dict[str, Any] | None:
    for section in draft.get("sections") or []:
        if str(section.get("section_id") or "") != section_id:
            continue
        sentences = section.get("sentences") or []
        if 0 <= sentence_index < len(sentences):
            return sentences[sentence_index]
    return None


def _draft_has_effective_body(draft: dict[str, Any]) -> bool:
    return any(
        local_repair_structural_sanity(str(sentence.get("text") or ""))
        for section in draft.get("sections") or []
        for sentence in section.get("sentences") or []
    )


def _all_fact_bindings_valid(draft: dict[str, Any]) -> bool:
    facts = [
        sentence
        for section in draft.get("sections") or []
        for sentence in section.get("sentences") or []
        if sentence.get("sentence_type") == "FACT"
    ]
    return bool(facts) and all(_sentence_evidence_ids(sentence) for sentence in facts)


def _fact_bindings(draft: dict[str, Any]) -> list[tuple[str, int, str, tuple[str, ...]]]:
    bindings: list[tuple[str, int, str, tuple[str, ...]]] = []
    for section in draft.get("sections") or []:
        section_id = str(section.get("section_id") or "")
        for index, sentence in enumerate(section.get("sentences") or []):
            if sentence.get("sentence_type") == "FACT":
                bindings.append((
                    section_id,
                    index,
                    str(sentence.get("sentence_type") or ""),
                    _sentence_evidence_ids(sentence),
                ))
    return bindings


def _structure_preserved(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    allowed_pruned_bindings: set[tuple[str, int]] | None = None,
) -> bool:
    allowed = allowed_pruned_bindings or set()
    before_snapshot = _protected_draft_snapshot(before)
    after_snapshot = _protected_draft_snapshot(after)
    before_bindings = {
        (section_id, index): evidence_ids
        for section_id, index, _sentence_type, evidence_ids in _fact_bindings(before)
    }
    after_bindings = {
        (section_id, index): evidence_ids
        for section_id, index, _sentence_type, evidence_ids in _fact_bindings(after)
    }
    if set(before_bindings) != set(after_bindings):
        return False
    for position, original_ids in before_bindings.items():
        retained_ids = after_bindings[position]
        if retained_ids == original_ids:
            continue
        if (
            position not in allowed
            or not retained_ids
            or not set(retained_ids).issubset(original_ids)
        ):
            return False
        section_id, sentence_index = position
        snapshot_sentence = _sentence_at(
            after_snapshot, section_id, sentence_index
        )
        original_sentence = _sentence_at(
            before_snapshot, section_id, sentence_index
        )
        if snapshot_sentence is None or original_sentence is None:
            return False
        if "evidence_ids" in original_sentence:
            snapshot_sentence["evidence_ids"] = copy.deepcopy(
                original_sentence["evidence_ids"]
            )
        else:
            snapshot_sentence["evidence_id"] = original_sentence.get(
                "evidence_id"
            )
    return before_snapshot == after_snapshot


def _conservative_repair_candidates(
    sentence: dict[str, Any], failure: dict[str, Any]
) -> list[dict[str, Any]]:
    """Reuse the existing A1/B candidate policy after a binding is narrowed."""

    risk_class = _failure_risk_class(failure)
    if risk_class == FactSafetyRiskClass.D_REVIEW_REQUIRED:
        return []
    candidates: list[dict[str, Any]] = []
    if risk_class == FactSafetyRiskClass.A_HARD_BLOCK:
        targeted_candidate = targeted_safe_repair_candidate(
            failure.get("safe_repair_input")
        )
        if targeted_candidate is not None:
            repair_input = failure["safe_repair_input"]
            if (
                repair_input["risk_sentence"]
                == str(sentence.get("text") or "")
                and tuple(repair_input["evidence_ids"])
                == _sentence_evidence_ids(sentence)
            ):
                candidates.append(targeted_candidate)
    else:
        retained_candidate = select_local_deletion_candidate(
            str(sentence.get("text") or ""),
            source_text=str(failure.get("source_text_for_repair") or ""),
            anchor_terms=list(failure.get("anchor_terms") or []),
            risk_fragments=_located_risk_fragments(failure),
        )
        if retained_candidate is not None:
            candidates.append(retained_candidate)
        # Keep the existing one-proposition normalization, including an existing
        # discourse prefix. It must never project a multi-proposition FACT onto
        # a single source atom. The caller still validates the complete result.
        prefix = str(failure.get("source_projection_prefix") or "")
        scope = str(sentence.get("text") or "")[len(prefix):]
        # A bound topic frame is not a second proposition. Count the remaining
        # clause only when the frame names the exact source/Anchor subject;
        # the projected sentence still goes through the full FACT validator.
        topic = re.match(r"^在([^，,；;。！？!?]{1,40}方面)[，,]", scope)
        terms = failure.get("anchor_terms") or []
        source = str(failure.get("source_text_for_repair") or "")
        if (topic and terms and _normalized(topic[1]) == _normalized(terms[0])
                and re.match(re.escape(topic[1]) + r"[：:,，]", source)):
            scope = scope[topic.end():]
        if failure.get("source_projection_candidate") and len(_split_clauses(scope)) == 1:
            projected = prefix + str(failure.get("source_projection_candidate") or "")
            if local_repair_structural_sanity(projected):
                projection_candidate = {
                    "text": projected,
                    "repair_strategy": "existing_deterministic_source_projection",
                    "removed_clause_count": 0,
                }
                if not any(
                    _normalized(item["text"]) == _normalized(projected)
                    for item in candidates
                ):
                    candidates.append(projection_candidate)
    return candidates


def _located_risk_fragments(failure: dict[str, Any]) -> list[str]:
    fragments = []
    for detail in [*failure.get("hard_details", []), *failure.get("component_failures", [])]:
        value = detail.get("value")
        if isinstance(value, str):
            fragments.append(value)
        elif detail.get("component") == "single_core_fact" and isinstance(value, list):
            fragments.extend(v for v in value if isinstance(v, str))
    return fragments


def _probe_local_isolation_candidate(
    working_draft: dict[str, Any],
    *,
    section_id: str,
    sentence_index: int,
    candidate: dict[str, Any],
    isolation_validator: FactValidator,
    allow_conservative_repair: bool = False,
) -> dict[str, Any] | None:
    """Realign a provisional text/binding pair, then prove it with FACT safety.

    Membership comes from the complete materialization inventory, independently
    of which compatibility/body error the validator happens to report first.
    Conservative body repair is bounded to one pass; its text is realigned again.
    """

    original_sentence = _sentence_at(working_draft, section_id, sentence_index)
    if original_sentence is None:
        return None
    original_ids = list(_sentence_evidence_ids(original_sentence))
    if not original_ids:
        return None
    retained_ids = list(original_ids)
    while retained_ids:
        probe_draft = copy.deepcopy(working_draft)
        probe_sentence = _sentence_at(probe_draft, section_id, sentence_index)
        if probe_sentence is None:
            return None
        probe_sentence["text"] = candidate["text"]
        _set_sentence_evidence_ids(probe_sentence, retained_ids)
        validation = {
            **isolation_validator(probe_draft, section_id, sentence_index),
            "section_id": section_id,
            "sentence_index": sentence_index,
        }
        alignment = validation.get("evidence_alignment")
        if alignment is not None:
            if [item["evidence_id"] for item in alignment] != retained_ids:
                return None
            materialized_ids = [item["evidence_id"] for item in alignment
                                if item["materialized"]]
            # One remaining declaration is proved by the normal single-FACT
            # validator, which also supports safe partial source projections.
            if len(retained_ids) > 1 and materialized_ids != retained_ids:
                retained_ids = materialized_ids
                continue
        if validation.get("passed"):
            return {
                **candidate,
                "validation_result": validation,
                "retained_evidence_ids": retained_ids,
                "removed_evidence_ids": [
                    value for value in original_ids if value not in retained_ids
                ],
            }
        # Both unchanged and narrowed bindings use the same candidate policy.
        # Subject/state/component errors are diagnostics, not repair dispatch.
        if allow_conservative_repair:
            for proposed in _conservative_repair_candidates(probe_sentence, validation):
                original_characters = iter(_normalized(candidate["text"]))
                if not all(char in original_characters for char in _normalized(proposed["text"])):
                    continue
                repaired = _probe_local_isolation_candidate(
                    probe_draft, section_id=section_id, sentence_index=sentence_index,
                    candidate=proposed, isolation_validator=isolation_validator,
                )
                if repaired is not None:
                    return {
                        **repaired,
                        "repair_strategy": "evidence_realignment+" + repaired["repair_strategy"],
                        "removed_evidence_ids": [
                            value for value in original_ids
                            if value not in repaired["retained_evidence_ids"]
                        ],
                    }
            return None
        return None
    return None


def _failure_position(failure: dict[str, Any]) -> tuple[str, int] | None:
    section_id = failure.get("section_id")
    sentence_index = failure.get("sentence_index")
    if not section_id or type(sentence_index) is not int or sentence_index < 0:
        return None
    return str(section_id), sentence_index


def _blocked_disposition(failures: list[dict[str, Any]]) -> DraftSafetyStatus:
    if any(
        item.get("risk_class") == FactSafetyRiskClass.A_HARD_BLOCK.value
        or bool(item.get("hard_contract_failure"))
        for item in failures
    ):
        return DraftSafetyStatus.BLOCKED_HARD_SAFETY
    return DraftSafetyStatus.REVIEW_REQUIRED_DRAFT


def _failure_risk_class(failure: dict[str, Any]) -> FactSafetyRiskClass:
    declared = str(failure.get("risk_class") or "")
    if declared:
        return FactSafetyRiskClass(declared)
    if failure.get("hard_contract_failure"):
        return FactSafetyRiskClass.A_HARD_BLOCK
    return FactSafetyRiskClass.B_SAFE_REPAIR


FactValidator = Callable[[dict[str, Any], str, int], dict[str, Any]]
FullRevalidator = Callable[[dict[str, Any]], dict[str, Any]]


def run_evidence_safety(
    draft: dict[str, Any],
    *,
    fact_validator: FactValidator,
    full_revalidator: FullRevalidator,
    isolation_validator: FactValidator | None = None,
    frozen_plan_integrity_check: Callable[[], None] | None = None,
    frozen_plan_sha256: str = "",
    allow_evidence_subset_narrowing: bool = False,
) -> dict[str, Any]:
    """Classify, apply a proved conservative repair, then revalidate.

    Subset narrowing is opt-in for callers with a frozen section contract. Any remaining
    body risk uses the existing conservative repair and normal FACT validator;
    only the proved text/binding pair is applied.
    """

    original_draft = copy.deepcopy(draft)
    working_draft = copy.deepcopy(draft)
    validate_isolation = isolation_validator or fact_validator
    if frozen_plan_integrity_check is not None:
        frozen_plan_integrity_check()
    frozen_before = str(frozen_plan_sha256 or "")

    checked_fact_count = 0
    initial_failures: list[dict[str, Any]] = []
    passed_claims: list[dict[str, Any]] = []
    repair_records: list[dict[str, Any]] = []
    isolation_records: list[dict[str, Any]] = []
    local_fixable_count = 0
    local_repair_attempt_count = 0
    local_repair_success_count = 0
    local_isolation_attempt_count = 0
    local_isolation_success_count = 0
    allowed_pruned_bindings: set[tuple[str, int]] = set()

    for section in working_draft.get("sections") or []:
        section_id = str(section.get("section_id") or "")
        for sentence_index, sentence in enumerate(section.get("sentences") or []):
            if sentence.get("sentence_type") != "FACT":
                continue
            checked_fact_count += 1
            result = fact_validator(working_draft, section_id, sentence_index)
            result.update(section_id=section_id, sentence_index=sentence_index)
            if result.get("passed"):
                result.setdefault(
                    "risk_class",
                    FactSafetyRiskClass.C_PASS_WITH_EQUIVALENCE.value,
                )
                passed_claims.append(result)
                continue
            failure = {
                **result,
                "section_id": section_id,
                "sentence_index": sentence_index,
            }
            risk_class = _failure_risk_class(failure)
            failure["initial_risk_class"] = risk_class.value
            failure["risk_class"] = risk_class.value
            initial_failures.append(failure)
            if allow_evidence_subset_narrowing:
                # Prove narrowing alone first; if body risk remains, compose
                # the existing conservative repair on the provisional subset.
                original_ids = list(_sentence_evidence_ids(sentence))
                candidate = _probe_local_isolation_candidate(
                    working_draft, section_id=section_id, sentence_index=sentence_index,
                    candidate={"text": str(sentence.get("text") or ""),
                               "repair_strategy": "evidence_subset_narrowing",
                               "removed_clause_count": 0},
                    isolation_validator=fact_validator,
                    allow_conservative_repair=True,
                )
                if candidate is not None:
                    original_text = str(sentence.get("text") or "")
                    sentence["text"] = candidate["text"]
                    _set_sentence_evidence_ids(sentence, candidate["retained_evidence_ids"])
                    if candidate["removed_evidence_ids"]:
                        allowed_pruned_bindings.add((section_id, sentence_index))
                    repair_records.append({
                        "section_id": section_id, "sentence_index": sentence_index,
                        "paragraph_id": sentence.get("paragraph_id"),
                        "paragraph_order": sentence.get("paragraph_order"),
                        "initial_risk_class": risk_class.value,
                        "original_failure_category": failure["reason"],
                        "reason": failure["reason"],
                        "classification": FactFailureClassification.LOCAL_FIXABLE.value,
                        "repair_strategy": candidate["repair_strategy"],
                        "original_text_sha256": _sha256_text(original_text),
                        "repaired_text_sha256": _sha256_text(candidate["text"]),
                        "repaired_text": candidate["text"],
                        "original_evidence_ids": original_ids,
                        "retained_evidence_ids": candidate["retained_evidence_ids"],
                        "removed_evidence_ids": candidate["removed_evidence_ids"],
                        "removed_clause_count": candidate["removed_clause_count"],
                        "validation_result": candidate["validation_result"],
                        "post_repair_validation_status": "PASS",
                    })
                    failure.update(resolved=True, hard_contract_failure=False, veto_fact=False,
                                   classification=FactFailureClassification.LOCAL_FIXABLE.value,
                                   risk_class=FactSafetyRiskClass.B_SAFE_REPAIR.value)
                    local_fixable_count += 1
                    local_repair_attempt_count += 1
                    local_repair_success_count += 1
                    continue
                if any(not item["materialized"] for item in failure.get("evidence_alignment", [])):
                    # A failed provisional subset cannot be retried through the
                    # broader isolation disposition with its original bindings.
                    failure["classification"] = (
                        FactFailureClassification.HARD_BLOCK.value
                        if failure.get("hard_contract_failure")
                        else FactFailureClassification.SECTION_REWRITE_REQUIRED.value)
                    continue
            if risk_class == FactSafetyRiskClass.D_REVIEW_REQUIRED:
                failure["classification"] = (
                    FactFailureClassification.SECTION_REWRITE_REQUIRED.value
                )
                continue

            candidates = _conservative_repair_candidates(sentence, failure)
            if risk_class == FactSafetyRiskClass.A_HARD_BLOCK and not candidates:
                failure["a_risk_subclass"] = FactSafetyARiskSubclass.A2_HARD_BLOCK.value
                failure["hard_contract_failure"] = True
                failure["classification"] = FactFailureClassification.HARD_BLOCK.value
                continue

            # Eligibility is proved on a temporary draft.  Only the first
            # candidate that passes the existing full FACT validator is ever
            # applied to the working draft, so the FACT has one repair attempt.
            candidate: dict[str, Any] | None = None
            for proposed in candidates:
                probe_draft = copy.deepcopy(working_draft)
                probe_sentence = next(
                    item
                    for item in probe_draft.get("sections") or []
                    if str(item.get("section_id") or "") == section_id
                )["sentences"][sentence_index]
                probe_sentence["text"] = proposed["text"]
                if fact_validator(
                    probe_draft, section_id, sentence_index
                ).get("passed"):
                    candidate = proposed
                    break
            if candidate is None:
                fallback_risk = str(
                    failure.get("repair_failure_risk_class")
                    or (
                        FactSafetyRiskClass.A_HARD_BLOCK.value
                        if risk_class == FactSafetyRiskClass.A_HARD_BLOCK
                        else FactSafetyRiskClass.D_REVIEW_REQUIRED.value
                    )
                )
                failure["risk_class"] = fallback_risk
                if fallback_risk == FactSafetyRiskClass.A_HARD_BLOCK.value:
                    failure["a_risk_subclass"] = (
                        FactSafetyARiskSubclass.A2_HARD_BLOCK.value
                    )
                    failure["hard_contract_failure"] = True
                    failure["classification"] = FactFailureClassification.HARD_BLOCK.value
                else:
                    failure["classification"] = (
                        FactFailureClassification.SECTION_REWRITE_REQUIRED.value
                    )
                continue

            if risk_class == FactSafetyRiskClass.A_HARD_BLOCK:
                failure["a_risk_subclass"] = (
                    FactSafetyARiskSubclass.A1_SAFE_REPAIR.value
                )
                failure["risk_class"] = FactSafetyRiskClass.B_SAFE_REPAIR.value
                failure["hard_contract_failure"] = False

            local_repair_attempt_count += 1
            original_text = str(sentence.get("text") or "")
            sentence["text"] = candidate["text"]
            post_repair = fact_validator(working_draft, section_id, sentence_index)
            record = {
                "section_id": section_id,
                "sentence_index": sentence_index,
                "paragraph_id": sentence.get("paragraph_id"),
                "paragraph_order": sentence.get("paragraph_order"),
                "original_evidence_ids": list(_sentence_evidence_ids(sentence)),
                "retained_evidence_ids": list(_sentence_evidence_ids(sentence)),
                "removed_evidence_ids": [],
                "validation_result": post_repair,
                "evidence_id": str(sentence.get("evidence_id") or ""),
                "evidence_ids": list(_sentence_evidence_ids(sentence)),
                "initial_risk_class": str(failure.get("initial_risk_class") or ""),
                "a_risk_subclass": str(failure.get("a_risk_subclass") or ""),
                "risk_type": str(
                    (failure.get("safe_repair_input") or {}).get("risk_type") or ""
                ),
                "original_fact_status": str(
                    (failure.get("safe_repair_input") or {}).get(
                        "original_fact_status"
                    ) or ""
                ),
                "suggested_downgrade_action": str(
                    candidate.get("suggested_downgrade_action") or ""
                ),
                "original_failure_category": str(failure.get("reason") or ""),
                "classification": (
                    FactFailureClassification.LOCAL_FIXABLE.value
                    if post_repair.get("passed")
                    else FactFailureClassification.SECTION_REWRITE_REQUIRED.value
                ),
                "repair_strategy": candidate["repair_strategy"],
                "original_text_sha256": _sha256_text(original_text),
                "repaired_text_sha256": _sha256_text(candidate["text"]),
                "repaired_text": candidate["text"],
                "removed_clause_count": int(candidate["removed_clause_count"]),
                "post_repair_validation_status": (
                    "PASS" if post_repair.get("passed") else "FAIL"
                ),
            }
            repair_records.append(record)
            if post_repair.get("passed"):
                failure["classification"] = FactFailureClassification.LOCAL_FIXABLE.value
                failure["risk_class"] = FactSafetyRiskClass.B_SAFE_REPAIR.value
                failure["resolved"] = True
                local_fixable_count += 1
                local_repair_success_count += 1
            else:
                sentence["text"] = original_text
                if risk_class == FactSafetyRiskClass.A_HARD_BLOCK:
                    failure["a_risk_subclass"] = (
                        FactSafetyARiskSubclass.A2_HARD_BLOCK.value
                    )
                    failure["classification"] = FactFailureClassification.HARD_BLOCK.value
                    failure["risk_class"] = FactSafetyRiskClass.A_HARD_BLOCK.value
                    failure["hard_contract_failure"] = True
                else:
                    failure["classification"] = (
                        FactFailureClassification.SECTION_REWRITE_REQUIRED.value
                    )
                    failure["risk_class"] = FactSafetyRiskClass.D_REVIEW_REQUIRED.value
                failure["post_repair_failure"] = post_repair

    isolatable_a2_failures = [
        item
        for item in initial_failures
        if (
            not item.get("resolved")
            and item.get("a_risk_subclass")
            == FactSafetyARiskSubclass.A2_HARD_BLOCK.value
            and (allow_evidence_subset_narrowing
                 or item.get("reason") in _LOCALLY_ISOLATABLE_A2_REASONS)
            and item.get("source_text_for_repair")
            and (item.get("evidence_ids") or item.get("evidence_id"))
        )
    ]
    if len(isolatable_a2_failures) == MAX_LOCAL_ISOLATION_COUNT:
        failure = isolatable_a2_failures[0]
        section_id = str(failure.get("section_id") or "")
        sentence_index = failure["sentence_index"]
        sentence = _sentence_at(working_draft, section_id, sentence_index)
        isolation_candidate = None
        if sentence is not None:
            proposed = select_local_deletion_candidate(
                str(sentence.get("text") or ""),
                source_text=str(failure.get("source_text_for_repair") or ""),
                anchor_terms=list(failure.get("anchor_terms") or []),
                risk_fragments=_located_risk_fragments(failure),
            )
            if proposed is not None:
                local_isolation_attempt_count += 1
                isolation_candidate = _probe_local_isolation_candidate(
                    working_draft,
                    section_id=section_id,
                    sentence_index=sentence_index,
                    candidate=proposed,
                    isolation_validator=validate_isolation,
                )
        if isolation_candidate is not None and sentence is not None:
            original_text = str(sentence.get("text") or "")
            original_ids = list(_sentence_evidence_ids(sentence))
            sentence["text"] = isolation_candidate["text"]
            _set_sentence_evidence_ids(
                sentence, isolation_candidate["retained_evidence_ids"]
            )
            post_isolation = validate_isolation(
                working_draft, section_id, sentence_index
            )
            isolation_safe = bool(
                post_isolation.get("passed")
                and _draft_has_effective_body(working_draft)
                and _all_fact_bindings_valid(working_draft)
            )
            record = {
                "section_id": section_id,
                "sentence_index": sentence_index,
                "paragraph_id": sentence.get("paragraph_id"),
                "paragraph_order": sentence.get("paragraph_order"),
                "validation_result": post_isolation,
                "initial_risk_class": str(
                    failure.get("initial_risk_class") or ""
                ),
                "a_risk_subclass": FactSafetyARiskSubclass.A2_HARD_BLOCK.value,
                "original_failure_category": str(failure.get("reason") or ""),
                "isolation_strategy": str(
                    isolation_candidate.get("repair_strategy") or ""
                ),
                "original_text_sha256": _sha256_text(original_text),
                "isolated_text_sha256": _sha256_text(
                    isolation_candidate["text"]
                ),
                "repaired_text": isolation_candidate["text"],
                "removed_clause_count": int(
                    isolation_candidate.get("removed_clause_count") or 0
                ),
                "original_evidence_ids": original_ids,
                "retained_evidence_ids": list(
                    isolation_candidate["retained_evidence_ids"]
                ),
                "removed_evidence_ids": list(
                    isolation_candidate["removed_evidence_ids"]
                ),
                "post_isolation_validation_status": (
                    "PASS" if isolation_safe else "FAIL"
                ),
            }
            isolation_records.append(record)
            if isolation_safe:
                failure["risk_class"] = FactSafetyRiskClass.B_SAFE_REPAIR.value
                failure["hard_contract_failure"] = False
                failure["classification"] = (
                    FactFailureClassification.LOCAL_FIXABLE.value
                )
                failure["local_isolation_status"] = "ISOLATED_AND_REVALIDATED"
                failure["resolved"] = True
                local_isolation_success_count = 1
                if isolation_candidate["removed_evidence_ids"]:
                    allowed_pruned_bindings.add((section_id, sentence_index))
            else:
                sentence["text"] = original_text
                _set_sentence_evidence_ids(sentence, original_ids)
                failure["local_isolation_status"] = "ISOLATION_REJECTED"
        if not failure.get("resolved"):
            # A failed isolation/recheck cannot demote an original veto fact.
            # Only a proved-safe retained clause may leave the hard-block lane.
            if failure.get("veto_fact"):
                failure["risk_class"] = FactSafetyRiskClass.A_HARD_BLOCK.value
                failure["a_risk_subclass"] = (
                    FactSafetyARiskSubclass.A2_HARD_BLOCK.value
                )
                failure["classification"] = (
                    FactFailureClassification.HARD_BLOCK.value
                )
                failure["local_isolation_status"] = "ISOLATION_REJECTED_HARD_BLOCK"
            else:
                failure["risk_class"] = FactSafetyRiskClass.D_REVIEW_REQUIRED.value
                failure["hard_contract_failure"] = False
                failure["classification"] = (
                    FactFailureClassification.SECTION_REWRITE_REQUIRED.value
                )
                failure["local_isolation_status"] = "REVIEW_REQUIRED"

    remaining_failures = [
        item for item in initial_failures if not item.get("resolved")
    ]
    full_revalidation = {
        "passed": not remaining_failures,
        "status": "NOT_RUN",
        "failures": remaining_failures,
    }
    if local_repair_success_count or local_isolation_success_count:
        full_revalidation = full_revalidator(working_draft)
        isolated_positions = {
            (
                str(item.get("section_id") or ""),
                item["sentence_index"],
            )
            for item in initial_failures
            if item.get("local_isolation_status")
            == "ISOLATED_AND_REVALIDATED"
        }
        if isolated_positions and not full_revalidation.get("passed"):
            unconfirmed_failures: list[dict[str, Any]] = []
            for item in list(full_revalidation.get("failures") or []):
                position = _failure_position(item)
                if (
                    position in isolated_positions
                    and validate_isolation(
                        working_draft, position[0], position[1]
                    ).get("passed")
                ):
                    continue
                unconfirmed_failures.append(item)
            full_revalidation["failures"] = unconfirmed_failures
            if not unconfirmed_failures:
                full_revalidation["passed"] = True
                full_revalidation["status"] = "PASS"
                full_revalidation["source_fact_validation_status"] = "PASS"
        repaired_positions = {
            (
                str(item.get("section_id") or ""),
                item["sentence_index"],
            )
            for item in initial_failures
            if item.get("resolved")
        }
        recheck_failures = list(full_revalidation.get("failures") or [])
        # A global or unlocated failure cannot certify any repair, but must
        # never be attributed to sentence zero. Located failures affect only
        # their own repair version.
        unknown_failure = any(_failure_position(item) is None for item in recheck_failures)
        failed_positions = {
            _failure_position(item) for item in recheck_failures
            if _failure_position(item) in repaired_positions
        }
        if unknown_failure or (not full_revalidation.get("passed") and not recheck_failures):
            failed_positions = repaired_positions
        repair_revalidation_failures = [
            item for item in recheck_failures
            if _failure_position(item) is None or _failure_position(item) in failed_positions
        ]
        full_revalidation["repaired_claims_passed"] = not (
            failed_positions
        )
        full_revalidation["repaired_claim_failure_count"] = len(
            repair_revalidation_failures
        )
        if failed_positions:
            failed_repaired_facts = [
                item for item in initial_failures
                if item.get("resolved") and _failure_position(item) in failed_positions
            ]
            for item in failed_repaired_facts:
                item.pop("resolved", None)
                if (
                    item.get("initial_risk_class")
                    == FactSafetyRiskClass.A_HARD_BLOCK.value
                ):
                    item["a_risk_subclass"] = (
                        FactSafetyARiskSubclass.A2_HARD_BLOCK.value
                    )
                    item["classification"] = FactFailureClassification.HARD_BLOCK.value
                    item["risk_class"] = FactSafetyRiskClass.A_HARD_BLOCK.value
                    item["hard_contract_failure"] = True
                else:
                    item["classification"] = (
                        FactFailureClassification.SECTION_REWRITE_REQUIRED.value
                    )
                    item["risk_class"] = FactSafetyRiskClass.D_REVIEW_REQUIRED.value
                item["post_repair_full_revalidation_failed"] = True
            remaining_failures = [
                *[item for item in initial_failures if not item.get("resolved")],
                *recheck_failures,
            ]
            for record in repair_records:
                if (record["post_repair_validation_status"] == "PASS"
                        and _failure_position(record) in failed_positions):
                    record["post_repair_validation_status"] = "FAIL_FULL_REVALIDATION"
                    record["classification"] = (
                        FactFailureClassification.HARD_BLOCK.value
                        if record.get("initial_risk_class")
                        == FactSafetyRiskClass.A_HARD_BLOCK.value
                        else FactFailureClassification.SECTION_REWRITE_REQUIRED.value
                    )
            for record in isolation_records:
                if (record["post_isolation_validation_status"] == "PASS"
                        and _failure_position(record) in failed_positions):
                    record["post_isolation_validation_status"] = (
                        "FAIL_FULL_REVALIDATION"
                    )
            for section_id, sentence_index in failed_positions:
                sentence = _sentence_at(working_draft, section_id, sentence_index)
                original_sentence = _sentence_at(original_draft, section_id, sentence_index)
                if sentence is not None and original_sentence is not None:
                    sentence.clear()
                    sentence.update(copy.deepcopy(original_sentence))
            allowed_pruned_bindings.difference_update(failed_positions)
            local_repair_success_count = sum(
                record["post_repair_validation_status"] == "PASS" for record in repair_records
            )
            local_isolation_success_count = sum(
                record["post_isolation_validation_status"] == "PASS" for record in isolation_records
            )
            local_fixable_count = local_repair_success_count
        elif recheck_failures:
            remaining_failures = [*remaining_failures, *recheck_failures]

    structure_preserved = _structure_preserved(
        original_draft,
        working_draft,
        allowed_pruned_bindings=allowed_pruned_bindings,
    )
    if not structure_preserved:
        remaining_failures.append({
            "reason": "repair_structure_changed",
            "hard_contract_failure": True,
            "risk_class": FactSafetyRiskClass.A_HARD_BLOCK.value,
            "classification": FactFailureClassification.HARD_BLOCK.value,
        })

    if frozen_plan_integrity_check is not None:
        frozen_plan_integrity_check()
    frozen_after = str(frozen_plan_sha256 or "")
    frozen_integrity = (
        "PASS" if frozen_before == frozen_after else "FAIL"
    )
    if frozen_integrity != "PASS":
        remaining_failures.append({
            "reason": "frozen_plan_integrity_mismatch",
            "hard_contract_failure": True,
            "risk_class": FactSafetyRiskClass.A_HARD_BLOCK.value,
            "classification": FactFailureClassification.HARD_BLOCK.value,
        })

    hard_block_count = sum(
        item.get("risk_class") == FactSafetyRiskClass.A_HARD_BLOCK.value
        or bool(item.get("hard_contract_failure"))
        for item in remaining_failures
    )
    if remaining_failures:
        status = _blocked_disposition(remaining_failures)
    elif local_repair_success_count or local_isolation_success_count:
        status = DraftSafetyStatus.PASS_WITH_LOCAL_REPAIR
    else:
        status = DraftSafetyStatus.PASS

    section_rewrite_sections = {
        str(item.get("section_id") or "")
        for item in remaining_failures
        if item.get("section_id") and not item.get("hard_contract_failure")
    }
    draft_rewrite_required = len(section_rewrite_sections) >= 2
    equivalence_count = sum(
        item.get("risk_class")
        == FactSafetyRiskClass.C_PASS_WITH_EQUIVALENCE.value
        for item in passed_claims
    )
    review_required_count = sum(
        item.get("risk_class") == FactSafetyRiskClass.D_REVIEW_REQUIRED.value
        for item in remaining_failures
    )
    if hard_block_count:
        fact_safety_result = FactSafetyRiskClass.A_HARD_BLOCK.value
    elif review_required_count:
        fact_safety_result = FactSafetyRiskClass.D_REVIEW_REQUIRED.value
    elif local_repair_success_count or local_isolation_success_count:
        fact_safety_result = FactSafetyRiskClass.B_SAFE_REPAIR.value
    else:
        fact_safety_result = FactSafetyRiskClass.C_PASS_WITH_EQUIVALENCE.value
    # Record precise edits on both successful and rolled-back attempts. These
    # fields are sealed together with evidence and validation in report digests.
    for record in [*repair_records, *isolation_records]:
        position = _failure_position(record)
        if position is None:
            continue
        before = _sentence_at(original_draft, *position)
        after = _sentence_at(working_draft, *position)
        original_text = str((before or {}).get("text") or "")
        final_text = str((after or {}).get("text") or "")
        record["original_text"] = original_text
        record["final_text"] = final_text
        record["content_changes"] = [
            {"start": i, "end": j, "original_fragment": original_text[i:j],
             "replacement_fragment": final_text[a:b],
             "reason": record.get("original_failure_category"),
             "evidence_ids": list(_sentence_evidence_ids(after or {})),
             "revalidation_status": record.get("post_repair_validation_status")
                 or record.get("post_isolation_validation_status")}
            for tag, i, j, a, b in SequenceMatcher(None, original_text, final_text, autojunk=False).get_opcodes()
            if tag != "equal"
        ]
    realignment_versions = []
    if allow_evidence_subset_narrowing:
        # Seal audit data only after scoped revalidation and positional rollback.
        # Candidate consumers bind this entire version, not independently cached
        # text, evidence, or state. No sentence/paragraph identity is changed.
        for record in [*repair_records, *isolation_records]:
            binding = record.get("validation_result", {}).get("evidence_alignment", [])
            record["subject_state_bindings"] = [
                {key: item[key] for key in ("evidence_id", "subject", "semantic_state")}
                for item in binding
            ]
            record["repair_version_sha256"] = report_sha256(record)
        for section in working_draft.get("sections") or []:
            sid = str(section.get("section_id") or "")
            for index, sentence in enumerate(section.get("sentences") or []):
                if sentence.get("sentence_type") != "FACT":
                    continue
                original_sentence = _sentence_at(original_draft, sid, index)
                original_ids = list(_sentence_evidence_ids(original_sentence))
                retained_ids = list(_sentence_evidence_ids(sentence))
                records = [copy.deepcopy(record) for record in [*repair_records, *isolation_records]
                           if _failure_position(record) == (sid, index)]
                validator = (validate_isolation if any(
                    record.get("post_isolation_validation_status") == "PASS" for record in records
                ) else fact_validator)
                validation = validator(working_draft, sid, index)
                version = {
                    "section_id": sid, "sentence_index": index,
                    "paragraph_id": sentence.get("paragraph_id"),
                    "paragraph_order": sentence.get("paragraph_order"),
                    "repaired_text": str(sentence.get("text") or ""),
                    "original_evidence_ids": original_ids,
                    "retained_evidence_ids": retained_ids,
                    "removed_evidence_ids": [eid for eid in original_ids if eid not in retained_ids],
                    "subject_state_bindings": [
                        {key: item[key] for key in ("evidence_id", "subject", "semantic_state")}
                        for item in validation.get("evidence_alignment", [])
                    ],
                    "validation_result": {**validation, "section_id": sid, "sentence_index": index},
                    "repair_records": records,
                    "scoped_revalidation_passed": not remaining_failures,
                }
                version["digest"] = report_sha256(version)
                realignment_versions.append(version)
    report = {
        "evidence_safety_version": EVIDENCE_SAFETY_VERSION,
        "fact_safety_result": fact_safety_result,
        "status": status.value,
        "checked_fact_count": checked_fact_count,
        "initial_failure_count": len(initial_failures),
        "local_fixable_count": local_fixable_count,
        "local_repair_rounds": 1 if local_repair_attempt_count else 0,
        "local_repair_attempt_count": local_repair_attempt_count,
        "local_repair_success_count": local_repair_success_count,
        "local_isolation_attempt_count": local_isolation_attempt_count,
        "local_isolation_success_count": local_isolation_success_count,
        "local_isolation_limit": MAX_LOCAL_ISOLATION_COUNT,
        "section_rewrite_required_count": len(section_rewrite_sections),
        "draft_rewrite_required": draft_rewrite_required,
        "hard_block_count": hard_block_count,
        "a1_safe_repair_count": sum(
            item.get("a_risk_subclass")
            == FactSafetyARiskSubclass.A1_SAFE_REPAIR.value
            and bool(item.get("resolved"))
            for item in initial_failures
        ),
        "a2_hard_block_count": sum(
            item.get("a_risk_subclass")
            == FactSafetyARiskSubclass.A2_HARD_BLOCK.value
            and item.get("risk_class") == FactSafetyRiskClass.A_HARD_BLOCK.value
            for item in remaining_failures
        ),
        "a2_review_required_count": sum(
            item.get("a_risk_subclass")
            == FactSafetyARiskSubclass.A2_HARD_BLOCK.value
            and item.get("risk_class")
            == FactSafetyRiskClass.D_REVIEW_REQUIRED.value
            for item in remaining_failures
        ),
        "safe_equivalence_count": equivalence_count,
        "review_required_count": review_required_count,
        "risk_class_counts": {
            risk.value: sum(
                item.get("risk_class") == risk.value
                for item in [*passed_claims, *initial_failures]
            )
            for risk in FactSafetyRiskClass
        },
        "delivery_blocked": bool(hard_block_count),
        "review_required": bool(remaining_failures) and not hard_block_count,
        "remaining_failure_count": len(remaining_failures),
        "revalidation_passed": bool(
            not remaining_failures
            and (
                not (local_repair_success_count or local_isolation_success_count)
                or full_revalidation.get("passed")
            )
        ),
        "repair_revalidation_passed": bool(
            (repair_records or isolation_records)
            and full_revalidation.get("repaired_claims_passed")
        ),
        "full_revalidation": {
            key: value
            for key, value in full_revalidation.items()
            if key != "failures"
        },
        "frozen_plan_integrity": {
            "status": frozen_integrity,
            "sha256_before": frozen_before,
            "sha256_after": frozen_after,
        },
        "structure_preserved": structure_preserved,
        "repair_records": repair_records,
        "local_isolation_records": isolation_records,
        "failure_classifications": [
            {
                "section_id": str(item.get("section_id") or ""),
                "evidence_id": str(item.get("evidence_id") or ""),
                "evidence_ids": list(item.get("evidence_ids") or []),
                "sentence_index": item["sentence_index"],
                "reason": str(item.get("reason") or ""),
                "initial_risk_class": str(
                    item.get("initial_risk_class") or ""
                ),
                "risk_class": str(item.get("risk_class") or ""),
                "a_risk_subclass": str(item.get("a_risk_subclass") or ""),
                "classification": str(item.get("classification") or ""),
            }
            for item in initial_failures
        ],
        "external_model_calls": 0,
    }
    if allow_evidence_subset_narrowing:
        report["realignment_versions"] = realignment_versions
    return {
        "structured_draft": working_draft,
        "initial_failures": initial_failures,
        "remaining_failures": remaining_failures,
        "passed_claims": passed_claims,
        "report": report,
    }


def report_sha256(report: dict[str, Any]) -> str:
    payload = json.dumps(
        report,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return _sha256_text(payload)
