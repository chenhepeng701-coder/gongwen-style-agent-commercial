"""Single planning owner and immutable handoff for commercial work summaries.

The engine orchestrates existing deterministic planning components.  It does
not classify Semantic State, create facts, call a model, retrieve material, or
own Writer/Fact Safety behavior.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import time
from types import MappingProxyType
from typing import Any, Mapping

from app.services.generation.atomic_evidence import (
    build_content_plan,
    build_fact_anchor_pack,
    isolate_fact_section_mismatches,
    repair_content_plan,
    select_evidence_for_content_plan,
    semantic_state_payload,
    validate_content_plan,
    validate_evidence_selection,
    validate_fact_anchor_pack,
)
from app.services.generation.writing_style_library import (
    apply_writing_style_guidance_to_content_plan,
    load_writing_style_guidance_pack,
)
from app.services.generation.work_summary_capacity_budget import (
    build_evidence_capacity_budget,
    project_capacity_budget_onto_plan,
)
from app.services.generation.work_summary_evidence_chain import (
    build_final_work_chain_pack,
    project_work_chain_pack_onto_plan,
)
from app.services.generation.work_summary_execution_policy import (
    TARGET_ORIENTED,
    build_work_summary_execution_policy,
)
from app.services.generation.work_summary_planner import (
    EVIDENCE_CONSTRAINED,
    degrade_work_summary_plan_for_sparse_evidence,
    is_work_summary_task,
)
from app.utils.storage import atomic_write_text
from app.services.generation.paragraph_task import plan_paragraph_tasks, validate_paragraph_tasks
from app.services.generation.flow3_v3_trace import build_fact_source_support
from app.services.generation.work_summary_evidence_group import build_work_summary_evidence_groups


PLANNING_ENGINE_VERSION = "planning-engine-1"
FROZEN_ARTICLE_PLAN_SCHEMA_VERSION = "frozen-article-plan-1"
PLANNING_OWNER = "planning_engine_v1"
MAX_REPLAN_ROUNDS = 1

INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
NO_SAFE_REPLAN = "NO_SAFE_REPLAN"
INVALID_FINAL_BINDING = "INVALID_FINAL_BINDING"
STALE_DERIVED_STATE = "STALE_DERIVED_STATE"
FREEZE_VALIDATION_FAILED = "FREEZE_VALIDATION_FAILED"

_SPARSE_SELECTION_DENSITY_RULES = {"section_core_fact_count"}
_SPARSE_ANCHOR_DENSITY_RULES = {"section_anchor_count", "section_core_anchor_count"}
_SPARSE_PLAN_RULES = {"required_assignment", "recommended_words_sum"}


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _jsonable(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return copy.deepcopy(value)


def _redact_source_text(value: Any) -> Any:
    prohibited = {
        "text",
        "source_text",
        "source_sentence",
        "source_span",
        "chunk_text",
        "raw_text",
    }
    if isinstance(value, Mapping):
        return {
            str(key): _redact_source_text(item)
            for key, item in value.items()
            if str(key) not in prohibited
        }
    if isinstance(value, (list, tuple)):
        return [_redact_source_text(item) for item in value]
    return copy.deepcopy(value)


class PlanningFailure(RuntimeError):
    """Auditable, local Planning Engine failure."""

    def __init__(self, code: str, report: Mapping[str, Any]):
        self.code = str(code)
        self.report = copy.deepcopy(dict(report))
        super().__init__(f"{self.code}: {self.report.get('reason') or 'planning failed'}")


@dataclass(frozen=True)
class FrozenArticlePlan:
    """Recursively immutable canonical planning state consumed by Writer/Safety."""

    plan_id: str
    document_type: str
    frozen_plan_sha256: str
    _payload: Mapping[str, Any] = field(repr=False)

    @classmethod
    def create(
        cls,
        *,
        document_type: str,
        content_plan: Mapping[str, Any],
        selection: Mapping[str, Any],
        fact_anchor_pack: Mapping[str, Any],
        work_chain_pack: Mapping[str, Any],
        capacity_budget: Mapping[str, Any],
        prohibited_inference: list[Any] | tuple[Any, ...] | None = None,
    ) -> "FrozenArticlePlan":
        selection_state = copy.deepcopy(dict(selection))
        selection_state.pop("content_plan", None)
        selection_state.pop("work_chain_pack", None)
        selection_state.pop("capacity_budget", None)
        payload = {
            "plan_schema_version": FROZEN_ARTICLE_PLAN_SCHEMA_VERSION,
            "planning_engine_version": PLANNING_ENGINE_VERSION,
            "planning_owner": PLANNING_OWNER,
            "plan_id": str(content_plan.get("plan_id") or ""),
            "document_type": str(document_type or ""),
            "content_plan": copy.deepcopy(dict(content_plan)),
            "selection": selection_state,
            "fact_anchor_pack": copy.deepcopy(dict(fact_anchor_pack)),
            "work_chain_pack": copy.deepcopy(dict(work_chain_pack)),
            "capacity_budget": copy.deepcopy(dict(capacity_budget)),
            "prohibited_inference": copy.deepcopy(list(prohibited_inference or [])),
        }
        digest = _sha256(payload)
        instance = cls(
            plan_id=str(payload["plan_id"]),
            document_type=str(payload["document_type"]),
            frozen_plan_sha256=digest,
            _payload=_freeze(payload),
        )
        instance.assert_integrity()
        return instance

    @property
    def payload(self) -> Mapping[str, Any]:
        return self._payload

    @property
    def sections(self) -> tuple[Any, ...]:
        content_plan = self._payload.get("content_plan") or {}
        return tuple(content_plan.get("sections") or ())

    def assert_integrity(self) -> None:
        actual = _sha256(_thaw(self._payload))
        if actual != self.frozen_plan_sha256:
            raise PlanningFailure(
                FREEZE_VALIDATION_FAILED,
                {
                    "reason": "frozen plan hash mismatch",
                    "expected_sha256": self.frozen_plan_sha256,
                    "actual_sha256": actual,
                },
            )

    def to_dict(self) -> dict[str, Any]:
        self.assert_integrity()
        return {
            **_thaw(self._payload),
            "frozen_plan_sha256": self.frozen_plan_sha256,
        }

    def to_artifact_dict(self) -> dict[str, Any]:
        artifact = _redact_source_text(self.to_dict())
        artifact["source_text_redacted"] = True
        return artifact

    def legacy_projection(self) -> dict[str, Any]:
        """Create independent mutable copies for unchanged downstream APIs."""

        self.assert_integrity()
        payload = _thaw(self._payload)
        content_plan = payload["content_plan"]
        work_chain_pack = payload["work_chain_pack"]
        capacity_budget = payload["capacity_budget"]
        selection = payload["selection"]
        selection["content_plan"] = copy.deepcopy(content_plan)
        selection["work_chain_pack"] = copy.deepcopy(work_chain_pack)
        selection["capacity_budget"] = copy.deepcopy(capacity_budget)
        return {
            "content_plan": content_plan,
            "selection": selection,
            "fact_anchor_pack": payload["fact_anchor_pack"],
            "work_chain_pack": work_chain_pack,
            "capacity_budget": capacity_budget,
        }

    def assert_projection_integrity(
        self,
        *,
        content_plan: Mapping[str, Any],
        selection: Mapping[str, Any],
        fact_anchor_pack: Mapping[str, Any],
    ) -> None:
        """Block a downstream handoff if a compatibility copy has changed."""

        self.assert_integrity()
        expected = self.legacy_projection()
        actual_selection = copy.deepcopy(dict(selection))
        comparisons = {
            "content_plan": (content_plan, expected["content_plan"]),
            "selection": (actual_selection, expected["selection"]),
            "fact_anchor_pack": (fact_anchor_pack, expected["fact_anchor_pack"]),
        }
        changed = [
            name
            for name, (actual, expected_value) in comparisons.items()
            if _canonical_json(actual) != _canonical_json(expected_value)
        ]
        if changed:
            raise PlanningFailure(
                STALE_DERIVED_STATE,
                {
                    "reason": "downstream planning projection changed after freeze",
                    "changed_projection_fields": changed,
                    "frozen_plan_sha256": self.frozen_plan_sha256,
                },
            )


@dataclass(frozen=True)
class PlanningEngineResult:
    frozen_article_plan: FrozenArticlePlan
    initial_content_plan: dict[str, Any]
    content_plan_report: dict[str, Any]
    selection_report: dict[str, Any]
    fact_anchor_report: dict[str, Any]
    writing_style_guidance_pack: dict[str, Any] | None
    writing_style_guidance_report: dict[str, Any]
    writing_style_plan_overlay_report: dict[str, Any]
    planning_engine_summary: dict[str, Any]
    artifact_paths: dict[str, str]

    def legacy_projection(self) -> dict[str, Any]:
        return self.frozen_article_plan.legacy_projection()


def _binding_snapshot(content_plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "section_id": str(section.get("section_id") or ""),
            "selected_evidence_ids": list(section.get("selected_evidence_ids") or []),
            "core_evidence_ids": list(section.get("core_evidence_ids") or []),
            "supporting_evidence_ids": list(section.get("supporting_evidence_ids") or []),
        }
        for section in content_plan.get("sections") or []
    ]


def _semantic_state_integrity(
    evidence_pack: Mapping[str, Any],
    selection: Mapping[str, Any],
    fact_anchor_pack: Mapping[str, Any],
) -> dict[str, Any]:
    source_by_id = {
        str(item.get("evidence_id") or ""): item
        for item in evidence_pack.get("evidence") or []
        if isinstance(item, Mapping) and item.get("evidence_id")
    }
    layers = {
        "selection_local": selection.get("selected_evidence_local") or [],
        "selection_outbound": selection.get("selected_evidence_outbound") or [],
        "fact_anchor": fact_anchor_pack.get("fact_anchors") or [],
        "analysis_support": fact_anchor_pack.get("analysis_support_pack") or [],
    }
    mismatches: list[dict[str, str]] = []
    layer_hashes: dict[str, str] = {}
    for layer, records in layers.items():
        rows: list[dict[str, Any]] = []
        for record in records:
            evidence_id = str(record.get("evidence_id") or "")
            source = source_by_id.get(evidence_id)
            if source is None:
                mismatches.append({"layer": layer, "evidence_id": evidence_id})
                continue
            source_state = semantic_state_payload(source)
            propagated_state = semantic_state_payload(record)
            rows.append({"evidence_id": evidence_id, **propagated_state})
            if propagated_state != source_state:
                mismatches.append({"layer": layer, "evidence_id": evidence_id})
        layer_hashes[layer] = _sha256(sorted(rows, key=lambda item: item["evidence_id"]))
    selected_ids = [str(value) for value in selection.get("selected_evidence_ids") or []]
    source_rows = [
        {"evidence_id": evidence_id, **semantic_state_payload(source_by_id[evidence_id])}
        for evidence_id in selected_ids
        if evidence_id in source_by_id
    ]
    return {
        "passed": not mismatches,
        "semantic_state_owner": "Atomic Evidence",
        "reclassified_by_planning_engine": False,
        "selected_source_state_sha256": _sha256(
            sorted(source_rows, key=lambda item: item["evidence_id"])
        ),
        "layer_state_sha256": layer_hashes,
        "mismatches": mismatches,
    }


def _hard_issue_rules(report: Mapping[str, Any]) -> set[str]:
    return {
        str(issue.get("rule") or "")
        for issue in report.get("hard_issues") or []
        if isinstance(issue, Mapping)
    }


def _sparse_degrade_is_safe(
    *,
    selection_report: Mapping[str, Any],
    anchor_report: Mapping[str, Any],
    semantic_integrity: Mapping[str, Any],
    replan_metadata: Mapping[str, Any],
) -> bool:
    selection_rules = _hard_issue_rules(selection_report)
    anchor_rules = _hard_issue_rules(anchor_report)
    normal_failed_on_density = bool(selection_rules or anchor_rules)
    replan_finished = bool(replan_metadata.get("replan_applied")) or str(
        replan_metadata.get("replan_reason") or ""
    ) in {"NO_SAFE_MERGE", "PRESERVATION_FAILED"}
    return bool(
        normal_failed_on_density
        and replan_finished
        and selection_rules <= _SPARSE_SELECTION_DENSITY_RULES
        and anchor_rules <= _SPARSE_ANCHOR_DENSITY_RULES
        and int(anchor_report.get("fact_anchorable_count") or 0) > 0
        and int(anchor_report.get("incomplete_anchor_count") or 0) == 0
        and int(anchor_report.get("section_goal_mismatch_count") or 0) == 0
        and int(anchor_report.get("analysis_support_mismatch_count") or 0) == 0
        and int(anchor_report.get("source_span_cross_section_conflict_count") or 0) == 0
        and int(anchor_report.get("source_span_multi_core_conflict_count") or 0) == 0
        and bool(anchor_report.get("outbound_matches_local"))
        and int(selection_report.get("section_goal_mismatch_count") or 0) == 0
        and int(selection_report.get("analysis_support_mismatch_count") or 0) == 0
        and int(selection_report.get("source_span_cross_section_conflict_count") or 0) == 0
        and bool(semantic_integrity.get("passed"))
    )


def _apply_sparse_density_contract(
    report: Mapping[str, Any], allowed_rules: set[str]
) -> dict[str, Any]:
    normalized = copy.deepcopy(dict(report))
    original_issues = list(normalized.get("hard_issues") or [])
    waived = [
        issue for issue in original_issues if str(issue.get("rule") or "") in allowed_rules
    ]
    remaining = [issue for issue in original_issues if issue not in waived]
    normalized["normal_strict_passed"] = not original_issues
    normalized["normal_strict_hard_issues"] = original_issues
    normalized["sparse_density_waivers"] = waived
    normalized["hard_issues"] = remaining
    normalized["hard_issue_count"] = len(remaining)
    normalized["passed"] = not remaining
    if "flow3_v32_status" in normalized:
        normalized["flow3_v32_status"] = (
            "PASS" if not remaining else "BLOCKED_INVALID_EVIDENCE_SELECTION"
        )
    if "flow3_v33_status" in normalized:
        normalized["flow3_v33_status"] = (
            "PASS" if not remaining else "BLOCKED_INVALID_FACT_ANCHORS"
        )
    if "flow3_v3_status" in normalized:
        normalized["flow3_v3_status"] = (
            "PASS" if not remaining else "BLOCKED_INVALID_CONTENT_PLAN"
        )
    if any(issue.get("rule") == "recommended_words_sum" for issue in waived):
        normalized["recommended_length_can_be_below_target"] = True
    return normalized


def _project_sparse_selection(
    selection: Mapping[str, Any], content_plan: Mapping[str, Any]
) -> dict[str, Any]:
    projected = copy.deepcopy(dict(selection))
    sections = list(content_plan.get("sections") or [])
    selected_ids = [
        str(evidence_id)
        for section in sections
        for evidence_id in section.get("selected_evidence_ids") or []
    ]
    retained = set(selected_ids)
    fact_section_count = sum(
        str(section.get("evidence_policy") or "fact_required") == "fact_required"
        for section in sections
    )
    projected["selected_evidence_ids"] = selected_ids
    projected["selected_evidence_count"] = len(selected_ids)
    projected["selected_evidence_local"] = [
        item
        for item in projected.get("selected_evidence_local") or []
        if str(item.get("evidence_id") or "") in retained
    ]
    projected["selected_evidence_outbound"] = [
        item
        for item in projected.get("selected_evidence_outbound") or []
        if str(item.get("evidence_id") or "") in retained
    ]
    projected["minimum_total"] = fact_section_count
    projected["maximum_total"] = max(fact_section_count, len(selected_ids))
    projected["section_count"] = len(sections)
    projected["content_plan"] = copy.deepcopy(dict(content_plan))
    projected["planning_mode"] = EVIDENCE_CONSTRAINED
    return projected


def _project_sparse_anchor_pack(
    fact_anchor_pack: Mapping[str, Any], selection: Mapping[str, Any]
) -> dict[str, Any]:
    projected = copy.deepcopy(dict(fact_anchor_pack))
    sections = list((selection.get("content_plan") or {}).get("sections") or [])
    retained_sections = {str(section.get("section_id") or "") for section in sections}
    retained_evidence = set(selection.get("selected_evidence_ids") or [])

    def retained(record: Mapping[str, Any]) -> bool:
        return (
            str(record.get("section_id") or "") in retained_sections
            and str(record.get("evidence_id") or "") in retained_evidence
        )

    projected["fact_anchors"] = [
        item for item in projected.get("fact_anchors") or [] if retained(item)
    ]
    projected["analysis_support_pack"] = [
        item for item in projected.get("analysis_support_pack") or [] if retained(item)
    ]
    dedup = copy.deepcopy(projected.get("source_span_dedup_report") or {})
    if dedup:
        dedup["entries"] = [
            entry
            for entry in dedup.get("entries") or []
            if retained_evidence.intersection(entry.get("original_candidate_evidence_ids") or [])
            and retained_sections.intersection(entry.get("section_id") or [])
        ]
        projected["source_span_dedup_report"] = dedup
    return projected


def _planning_invariants(
    *,
    content_plan: Mapping[str, Any],
    selection: Mapping[str, Any],
    fact_anchor_pack: Mapping[str, Any],
    work_chain_pack: Mapping[str, Any],
    capacity_budget: Mapping[str, Any],
    binding_sha256: str,
    anchor_sha256: str,
    chain_sha256: str,
) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    selected_by_section = {
        str(section.get("section_id") or ""): set(section.get("selected_evidence_ids") or [])
        for section in content_plan.get("sections") or []
    }
    anchor_ids = {
        str(anchor.get("evidence_id") or "")
        for anchor in fact_anchor_pack.get("fact_anchors") or []
    }
    for anchor in fact_anchor_pack.get("fact_anchors") or []:
        section_id = str(anchor.get("section_id") or "")
        if str(anchor.get("evidence_id") or "") not in selected_by_section.get(section_id, set()):
            issues.append({"invariant": "PLAN-INVARIANT-01", "section_id": section_id})
    for chain in work_chain_pack.get("chains") or []:
        if not set(chain.get("member_evidence_ids") or []) <= anchor_ids:
            issues.append({"invariant": "PLAN-INVARIANT-02", "chain_id": chain.get("chain_id")})
    section_ids = set(selected_by_section)
    capacity_section_ids = {
        str(row.get("section_id") or "")
        for row in capacity_budget.get("section_capacity") or []
    }
    if capacity_section_ids != section_ids:
        issues.append({"invariant": "PLAN-INVARIANT-03", "detail": "capacity section mismatch"})
    chain_source = work_chain_pack.get("planning_derivation") or {}
    capacity_source = capacity_budget.get("planning_derivation") or {}
    if (
        chain_source.get("final_binding_sha256") != binding_sha256
        or chain_source.get("final_anchor_sha256") != anchor_sha256
        or capacity_source.get("final_binding_sha256") != binding_sha256
        or capacity_source.get("final_anchor_sha256") != anchor_sha256
        or capacity_source.get("final_chain_sha256") != chain_sha256
    ):
        issues.append({"invariant": "PLAN-INVARIANT-04", "detail": "derived-state provenance mismatch"})
    if _canonical_json(selection.get("content_plan") or {}) != _canonical_json(content_plan):
        issues.append({"invariant": "PLAN-INVARIANT-04", "detail": "selection carries another plan"})
    return {
        "passed": not issues,
        "invariants": {
            "PLAN-INVARIANT-01": "final anchors derive from final selection",
            "PLAN-INVARIANT-02": "final chains derive from final plan and anchors",
            "PLAN-INVARIANT-03": "final capacity derives from final planning state",
            "PLAN-INVARIANT-04": "pre-replan derived objects are not production state",
            "PLAN-INVARIANT-05": "no replan is allowed after freeze",
        },
        "issues": issues,
    }


def _write_json(path: Path, payload: Mapping[str, Any]) -> str:
    atomic_write_text(
        path,
        json.dumps(_jsonable(payload), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return str(path.resolve())


class PlanningEngine:
    """Orchestrate the sole production planning path for work summaries."""

    def __init__(self, *, max_replan_rounds: int = MAX_REPLAN_ROUNDS):
        if max_replan_rounds < 0 or max_replan_rounds > 2:
            raise ValueError("max_replan_rounds must be between 0 and 2")
        self.max_replan_rounds = int(max_replan_rounds)

    def _fail(
        self,
        code: str,
        *,
        reason: str,
        artifact_root: Path | None,
        started: float,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        summary = {
            "planning_engine_version": PLANNING_ENGINE_VERSION,
            "planning_owner": PLANNING_OWNER,
            "planning_status": "BLOCKED",
            "failure_code": code,
            "reason": reason,
            "planning_engine_ms": round((time.perf_counter() - started) * 1000, 3),
            **copy.deepcopy(dict(details or {})),
        }
        if artifact_root is not None:
            artifact_root.mkdir(parents=True, exist_ok=True)
            summary["planning_engine_summary_path"] = _write_json(
                artifact_root / "planning_engine_summary.json", summary
            )
        raise PlanningFailure(code, summary)

    def plan(
        self,
        task: Any,
        evidence_pack: dict[str, Any],
        *,
        reference_structure: dict[str, Any] | None = None,
        retrieval: dict[str, Any] | None = None,
        target_words_provided: bool | None = None,
        artifact_root: Path | None = None,
        writing_reference: Mapping[str, Any] | None = None,
    ) -> PlanningEngineResult:
        """Run planning and normalize every component failure to PlanningFailure."""

        started = time.perf_counter()
        try:
            return self._plan_unchecked(
                task,
                evidence_pack,
                reference_structure=reference_structure,
                retrieval=retrieval,
                target_words_provided=target_words_provided,
                artifact_root=artifact_root,
                writing_reference=writing_reference,
            )
        except PlanningFailure:
            raise
        except Exception as exc:
            self._fail(
                INVALID_FINAL_BINDING,
                reason="planning component raised an unexpected local error",
                artifact_root=(
                    Path(artifact_root) if artifact_root is not None else None
                ),
                started=started,
                details={"exception_type": type(exc).__name__},
            )
            raise AssertionError("unreachable") from exc

    def _plan_unchecked(
        self,
        task: Any,
        evidence_pack: dict[str, Any],
        *,
        reference_structure: dict[str, Any] | None = None,
        retrieval: dict[str, Any] | None = None,
        target_words_provided: bool | None = None,
        artifact_root: Path | None = None,
        writing_reference: Mapping[str, Any] | None = None,
    ) -> PlanningEngineResult:
        started = time.perf_counter()
        root = Path(artifact_root) if artifact_root is not None else None
        if not is_work_summary_task(task):
            self._fail(
                INVALID_FINAL_BINDING,
                reason="work-summary adapter received a non-work-summary task",
                artifact_root=root,
                started=started,
            )

        initial_plan = build_content_plan(
            task,
            evidence_pack,
            reference_structure=reference_structure,
        )
        selection = select_evidence_for_content_plan(task, evidence_pack, initial_plan)
        replan_metadata = copy.deepcopy(selection.get("replan_metadata") or {})
        replan_rounds = 1 if replan_metadata.get("replan_applied") else 0
        if replan_rounds > self.max_replan_rounds:
            self._fail(
                NO_SAFE_REPLAN,
                reason="deterministic replan limit exceeded",
                artifact_root=root,
                started=started,
                details={"replan_rounds": replan_rounds},
            )

        fact_anchor_pack = build_fact_anchor_pack(selection, evidence_pack)
        repaired_plan = repair_content_plan(
            task,
            selection["content_plan"],
            fact_anchor_pack,
            target_words_provided=target_words_provided,
        )
        style_result = load_writing_style_guidance_pack(task)
        style_pack = style_result.get("pack")
        style_report = copy.deepcopy(style_result.get("report") or {})
        repaired_plan, style_overlay_report = apply_writing_style_guidance_to_content_plan(
            repaired_plan,
            style_pack,
            task=task,
        )

        # Evaluate the unchanged strict contract first.  Sparse recovery is
        # permitted only when every failure is a density rule and all binding,
        # source-span, writability, and Semantic State checks are already safe.
        selection["content_plan"] = copy.deepcopy(repaired_plan)
        initial_selection_report = validate_evidence_selection(selection, evidence_pack)
        selection, normal_selection_report = isolate_fact_section_mismatches(
            selection,
            evidence_pack,
            initial_selection_report,
        )
        selection_recovery = copy.deepcopy(
            selection.get("local_selection_recovery") or {
                "attempted": False,
                "applied": False,
                "recovery_round_count": 0,
                "max_recovery_rounds": 1,
                "isolated_evidence_ids": [],
                "reassigned_evidence_ids": [],
            }
        )
        if selection_recovery.get("applied"):
            repaired_plan = copy.deepcopy(selection["content_plan"])
            fact_anchor_pack = build_fact_anchor_pack(selection, evidence_pack)
        normal_plan_report = validate_content_plan(repaired_plan, evidence_pack, retrieval)
        normal_anchor_report = validate_fact_anchor_pack(
            fact_anchor_pack,
            selection,
            evidence_pack,
            list(fact_anchor_pack.get("fact_anchors") or []),
        )
        normal_semantic_integrity = _semantic_state_integrity(
            evidence_pack, selection, fact_anchor_pack
        )
        if not normal_plan_report.get("passed"):
            self._fail(
                INVALID_FINAL_BINDING,
                reason="normal content plan validation failed before sparse recovery",
                artifact_root=root,
                started=started,
                details={"content_plan_report": normal_plan_report},
            )

        degrade_metadata: dict[str, Any] = {
            "degraded_plan_applied": False,
            "degrade_reason": "NOT_REQUIRED",
            "planning_mode": "normal",
            "pre_degrade_section_count": len(repaired_plan.get("sections") or []),
            "post_degrade_section_count": len(repaired_plan.get("sections") or []),
            "retained_section_ids": [
                str(section.get("section_id") or "")
                for section in repaired_plan.get("sections") or []
            ],
            "removed_or_downgraded_section_ids": [],
        }
        if _sparse_degrade_is_safe(
            selection_report=normal_selection_report,
            anchor_report=normal_anchor_report,
            semantic_integrity=normal_semantic_integrity,
            replan_metadata=replan_metadata,
        ):
            degraded_plan, degrade_metadata = degrade_work_summary_plan_for_sparse_evidence(
                repaired_plan,
                section_anchor_counts=normal_anchor_report.get("section_anchor_counts") or {},
            )
            if degrade_metadata.get("degraded_plan_applied"):
                repaired_plan = degraded_plan
                selection = _project_sparse_selection(selection, repaired_plan)
                fact_anchor_pack = _project_sparse_anchor_pack(fact_anchor_pack, selection)
                degrade_metadata.update({
                    "normal_plan_failed": True,
                    "normal_replan_failed": True,
                    "normal_selection_report": normal_selection_report,
                    "normal_fact_anchor_report": normal_anchor_report,
                })
                repaired_plan["work_summary_sparse_evidence"] = copy.deepcopy(
                    degrade_metadata
                )

        binding_sha256 = _sha256(_binding_snapshot(repaired_plan))
        anchor_sha256 = _sha256(fact_anchor_pack)

        final_chain_pack = build_final_work_chain_pack(
            list(evidence_pack.get("evidence") or []),
            repaired_plan,
            fact_anchor_pack,
        )
        final_chain_pack["planning_owner"] = PLANNING_OWNER
        final_chain_pack["planning_derivation"] = {
            "source_plan_id": str(repaired_plan.get("plan_id") or ""),
            "final_binding_sha256": binding_sha256,
            "final_anchor_sha256": anchor_sha256,
            "built_after_final_anchors": True,
        }
        chain_sha256 = _sha256(final_chain_pack)
        repaired_plan = project_work_chain_pack_onto_plan(
            repaired_plan, final_chain_pack
        )

        # Editorial grouping follows final fact binding, before budget/freeze.
        # Reuse the same source-support and writable-material owners as M3.
        source_support = build_fact_source_support(
            fact_anchor_pack, selection["selected_evidence_local"], retrieval or {},
        )
        material_groups = build_work_summary_evidence_groups(repaired_plan, fact_anchor_pack, source_support)
        repaired_plan = plan_paragraph_tasks(repaired_plan, material_groups, writing_reference or {})
        validate_paragraph_tasks(repaired_plan, material_groups)

        final_capacity = build_evidence_capacity_budget(
            repaired_plan,
            list(evidence_pack.get("evidence") or []),
            final_chain_pack,
            requested_target_words=int(getattr(task, "target_words", 0) or 0),
            fact_anchor_pack=fact_anchor_pack,
        )
        final_capacity["planning_owner"] = PLANNING_OWNER
        final_capacity["planning_derivation"] = {
            "source_plan_id": str(repaired_plan.get("plan_id") or ""),
            "final_binding_sha256": binding_sha256,
            "final_anchor_sha256": anchor_sha256,
            "final_chain_sha256": chain_sha256,
            "built_after_final_chain": True,
        }
        repaired_plan = project_capacity_budget_onto_plan(
            repaired_plan, final_capacity
        )
        execution_policy = build_work_summary_execution_policy(repaired_plan)
        if degrade_metadata.get("degraded_plan_applied"):
            target_length_mode = str(execution_policy.get("length_mode") or "")
            degrade_metadata.update({
                "fact_anchor_count": len(fact_anchor_pack.get("fact_anchors") or []),
                "analysis_support_count": len(
                    fact_anchor_pack.get("analysis_support_pack") or []
                ),
                "requested_target": int(getattr(task, "target_words", 0) or 0),
                "target_length_mode": target_length_mode,
                "under_target_due_to_evidence": target_length_mode != TARGET_ORIENTED,
            })
            repaired_plan["planning_mode"] = EVIDENCE_CONSTRAINED
            repaired_plan["work_summary_sparse_evidence"] = copy.deepcopy(
                degrade_metadata
            )
        repaired_plan["planning_owner"] = PLANNING_OWNER
        repaired_plan["planning_lifecycle"] = "FROZEN"
        repaired_plan["planning_derivation"] = {
            "final_binding_sha256": binding_sha256,
            "final_anchor_sha256": anchor_sha256,
            "final_chain_sha256": chain_sha256,
            "capacity_sha256": _sha256(final_capacity),
        }

        selection["content_plan"] = copy.deepcopy(repaired_plan)
        selection["work_chain_pack"] = copy.deepcopy(final_chain_pack)
        selection["capacity_budget"] = copy.deepcopy(final_capacity)
        selection["planning_owner"] = PLANNING_OWNER
        selection["planning_lifecycle"] = "FROZEN"

        plan_report = validate_content_plan(repaired_plan, evidence_pack, retrieval)
        selection_report = validate_evidence_selection(selection, evidence_pack)
        anchor_report = validate_fact_anchor_pack(
            fact_anchor_pack,
            selection,
            evidence_pack,
            list(fact_anchor_pack.get("fact_anchors") or []),
        )
        semantic_integrity = _semantic_state_integrity(
            evidence_pack, selection, fact_anchor_pack
        )
        if degrade_metadata.get("degraded_plan_applied"):
            plan_report = _apply_sparse_density_contract(
                plan_report, _SPARSE_PLAN_RULES
            )
            selection_report = _apply_sparse_density_contract(
                selection_report, _SPARSE_SELECTION_DENSITY_RULES
            )
            anchor_report = _apply_sparse_density_contract(
                anchor_report, _SPARSE_ANCHOR_DENSITY_RULES
            )
        invariants = _planning_invariants(
            content_plan=repaired_plan,
            selection=selection,
            fact_anchor_pack=fact_anchor_pack,
            work_chain_pack=final_chain_pack,
            capacity_budget=final_capacity,
            binding_sha256=binding_sha256,
            anchor_sha256=anchor_sha256,
            chain_sha256=chain_sha256,
        )

        fact_section_count = sum(
            str(section.get("evidence_policy") or "fact_required") == "fact_required"
            for section in repaired_plan.get("sections") or []
        )
        anchor_count = len(fact_anchor_pack.get("fact_anchors") or [])
        if not plan_report.get("passed"):
            self._fail(
                INVALID_FINAL_BINDING,
                reason="final content plan validation failed",
                artifact_root=root,
                started=started,
                details={"content_plan_report": plan_report},
            )
        if not semantic_integrity["passed"]:
            self._fail(
                INVALID_FINAL_BINDING,
                reason="Semantic State was not propagated read-only",
                artifact_root=root,
                started=started,
                details={"semantic_state_integrity": semantic_integrity},
            )
        if not selection_report.get("passed") or not anchor_report.get("passed"):
            selection_rules = _hard_issue_rules(selection_report)
            anchor_rules = _hard_issue_rules(anchor_report)
            density_only = bool(selection_rules or anchor_rules) and (
                selection_rules <= _SPARSE_SELECTION_DENSITY_RULES
                and anchor_rules <= _SPARSE_ANCHOR_DENSITY_RULES
            )
            if density_only and anchor_count < fact_section_count * 2:
                failure_code = INSUFFICIENT_EVIDENCE
            elif density_only and replan_metadata.get("replan_reason") in {
                "NO_SAFE_MERGE", "PRESERVATION_FAILED"
            }:
                failure_code = NO_SAFE_REPLAN
            else:
                failure_code = INVALID_FINAL_BINDING
            self._fail(
                failure_code,
                reason="final evidence or anchor binding validation failed",
                artifact_root=root,
                started=started,
                details={
                    "selection_report": selection_report,
                    "fact_anchor_report": anchor_report,
                    "replan_metadata": replan_metadata,
                },
            )
        if not invariants["passed"]:
            self._fail(
                STALE_DERIVED_STATE,
                reason="final derived-state invariant failed",
                artifact_root=root,
                started=started,
                details={"planning_invariants": invariants},
            )

        try:
            frozen = FrozenArticlePlan.create(
                document_type=str(getattr(task, "document_type", "") or ""),
                content_plan=repaired_plan,
                selection=selection,
                fact_anchor_pack=fact_anchor_pack,
                work_chain_pack=final_chain_pack,
                capacity_budget=final_capacity,
                prohibited_inference=list(evidence_pack.get("prohibited_inference") or []),
            )
        except PlanningFailure:
            raise
        except Exception as exc:
            self._fail(
                FREEZE_VALIDATION_FAILED,
                reason=f"FrozenArticlePlan construction failed: {type(exc).__name__}",
                artifact_root=root,
                started=started,
            )
            raise AssertionError("unreachable") from exc

        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        summary = {
            "planning_engine_version": PLANNING_ENGINE_VERSION,
            "planning_owner": PLANNING_OWNER,
            "initial_section_count": len(initial_plan.get("sections") or []),
            "final_section_count": len(repaired_plan.get("sections") or []),
            "replan_applied": bool(replan_metadata.get("replan_applied")),
            "replan_rounds": replan_rounds,
            "max_replan_rounds": self.max_replan_rounds,
            "local_selection_recovery": selection_recovery,
            "final_selected_evidence_count": int(selection.get("selected_evidence_count") or 0),
            "final_anchor_count": anchor_count,
            "final_chain_count": len(final_chain_pack.get("chains") or []),
            "capacity_status": str(final_capacity.get("capacity_gap") or ""),
            "planning_mode": (
                EVIDENCE_CONSTRAINED
                if degrade_metadata.get("degraded_plan_applied")
                else "normal"
            ),
            "normal_plan_failed": bool(degrade_metadata.get("normal_plan_failed")),
            "normal_replan_failed": bool(degrade_metadata.get("normal_replan_failed")),
            "degraded_plan_applied": bool(degrade_metadata.get("degraded_plan_applied")),
            "degrade_reason": str(degrade_metadata.get("degrade_reason") or "NOT_REQUIRED"),
            "pre_degrade_section_count": int(
                degrade_metadata.get("pre_degrade_section_count")
                or len(repaired_plan.get("sections") or [])
            ),
            "post_degrade_section_count": int(
                degrade_metadata.get("post_degrade_section_count")
                or len(repaired_plan.get("sections") or [])
            ),
            "retained_section_ids": list(
                degrade_metadata.get("retained_section_ids") or []
            ),
            "removed_or_downgraded_section_ids": list(
                degrade_metadata.get("removed_or_downgraded_section_ids") or []
            ),
            "fact_anchor_count": anchor_count,
            "analysis_support_count": len(
                fact_anchor_pack.get("analysis_support_pack") or []
            ),
            "requested_target": int(getattr(task, "target_words", 0) or 0),
            "target_length_mode": str(execution_policy.get("length_mode") or ""),
            "under_target_due_to_evidence": bool(
                str(execution_policy.get("length_mode") or "") != TARGET_ORIENTED
            ),
            "pre_plan_capacity_exists": True,
            "pre_plan_capacity_is_production_state": False,
            "final_capacity_is_production_state": True,
            "semantic_state_integrity": semantic_integrity,
            "planning_invariants": invariants,
            "frozen_plan_sha256": frozen.frozen_plan_sha256,
            "planning_status": "PASS",
            "planning_engine_ms": elapsed_ms,
        }
        paths: dict[str, str] = {}
        if root is not None:
            root.mkdir(parents=True, exist_ok=True)
            paths["planning_engine_summary"] = _write_json(
                root / "planning_engine_summary.json", summary
            )
            paths["frozen_article_plan"] = _write_json(
                root / "frozen_article_plan.json", frozen.to_artifact_dict()
            )

        return PlanningEngineResult(
            frozen_article_plan=frozen,
            initial_content_plan=copy.deepcopy(initial_plan),
            content_plan_report=copy.deepcopy(plan_report),
            selection_report=copy.deepcopy(selection_report),
            fact_anchor_report=copy.deepcopy(anchor_report),
            writing_style_guidance_pack=copy.deepcopy(style_pack),
            writing_style_guidance_report=style_report,
            writing_style_plan_overlay_report=copy.deepcopy(style_overlay_report),
            planning_engine_summary=summary,
            artifact_paths=paths,
        )
