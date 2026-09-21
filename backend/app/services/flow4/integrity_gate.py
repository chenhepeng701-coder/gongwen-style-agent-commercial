"""Fail-closed integrity gate for the FLOW3-V3.3 to FLOW4 handoff."""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path
from typing import Iterable, Literal, Sequence

from pydantic import BaseModel, ConfigDict, StrictInt, StrictStr

from app.models.flow4 import (
    Flow3OutputEnvelope,
    Flow4Input,
    canonical_payload_sha256,
)


class _FrozenGateModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class HandoffIntegrityIssue(_FrozenGateModel):
    code: StrictStr
    path: StrictStr
    message: StrictStr


class SentenceEvidenceTrace(_FrozenGateModel):
    """One immutable audit record for one FLOW3 sentence atom."""

    section_id: StrictStr
    sentence_index: StrictInt
    sentence_type: Literal["FACT", "ANALYSIS"]
    evidence_ids: tuple[StrictStr, ...]
    text_sha256: StrictStr
    anchor_roles: tuple[Literal["core", "supporting"], ...]
    trace_status: Literal["MAPPED", "ANALYSIS_UNANCHORED"]


class HandoffIntegrityReport(_FrozenGateModel):
    """Successful gate result; failures raise before FLOW4 can consume input."""

    passed: Literal[True] = True
    handoff_id: StrictStr
    flow3_status: StrictStr
    risk_status: Literal["NO_RECORDED_RISK", "REVIEW_REQUIRED"]
    payload_sha256_matched: Literal[True] = True
    section_identity_order_passed: Literal[True] = True
    validation_reports_preserved: Literal[True] = True
    boundary_isolation_passed: Literal[True] = True
    sentence_count: StrictInt
    fact_sentence_count: StrictInt
    analysis_sentence_count: StrictInt
    fact_trace_mapped_count: StrictInt
    evidence_trace: tuple[SentenceEvidenceTrace, ...]


class HandoffIntegrityError(ValueError):
    """Stable, inspectable failure raised by the FLOW4 handoff gate."""

    def __init__(self, issues: Sequence[HandoffIntegrityIssue]):
        self.issues = tuple(issues)
        summary = "; ".join(f"{issue.code}@{issue.path}: {issue.message}" for issue in self.issues)
        super().__init__(summary or "FLOW4 handoff integrity check failed")


_CORE_FIELDS = (
    "parsed_structured_draft",
    "content_plan",
    "fact_anchor_pack",
    "validation_reports",
    "task",
)

_PROHIBITED_IMPORT_PREFIXES = (
    "app.services.generation",
    "app.services.library",
    "app.services.retrieval",
    "app.services.rag",
)

_PROHIBITED_CALL_NAMES = frozenset(
    {
        "_run_hybrid_fact_safe_gate",
        "build_fact_source_audit",
        "clean_official_draft_text",
        "clean_word_export_text",
        "generate_official_draft",
        "inspect_and_rewrite",
        "rewrite_high_risk_facts_locally",
        "targeted_quality_repair",
    }
)

_PROHIBITED_CALL_FRAGMENTS = (
    "fallback",
    "provider",
    "rag_reference_split",
    "reinject",
    "retrieval",
    "retrieve",
)

_PROHIBITED_EXTENSION_KEY_FRAGMENTS = (
    "fallback",
    "final_content",
    "model",
    "prompt",
    "provider",
    "rag",
    "repair",
    "retrieval",
    "rewrite",
    "temperature",
)


def validate_full_article_handoff(handoff):
    """Verify locations and version-bound reports; never repeat Fact Safety."""
    import json
    from app.models.full_article import (FullArticleHandoff, FullArticleContractError,
        article_units, article_check_identity, canonical_json, text_sha)
    value = FullArticleHandoff.model_validate(handoff.model_dump())
    candidate, citations = value.candidate, value.citations
    for report, owner in ((value.fact_report, "FACT"), (value.quality_report, "QUALITY")):
        report.require_current(candidate, citations)
        if report.owner != owner:
            raise FullArticleContractError("REPORT_OWNER_MISMATCH")
    facts = json.loads(value.fact_report.result_json)
    quality = json.loads(value.quality_report.result_json)
    units = article_units(candidate.content)
    rows = facts.get("units", [])
    proposals = {p.unit_id: p for p in citations.proposals}
    if len(proposals) != len(citations.proposals) or set(proposals) - {u.unit_id for u in units}:
        raise FullArticleContractError("CITATION_LOCATION_INVALID")
    if len(rows) != len(units):
        raise FullArticleContractError("FACT_COVERAGE_INCOMPLETE")
    for unit, row in zip(units, rows):
        if (any(row.get(k) != v for k, v in unit.model_dump().items())
                or row.get("observed_text") != candidate.content[unit.start:unit.end]):
            raise FullArticleContractError("FACT_LOCATION_INVALID")
        proposal = proposals.get(unit.unit_id)
        if (row.get("evidence_ids") != (list(proposal.evidence_ids) if proposal else [])
                or row.get("scope_id") != (proposal.scope_id if proposal else None)
                or (row.get("status") == "SUPPORTED") != (row.get("result", {}).get("passed") is True)
                or (not proposal and row.get("status") == "SUPPORTED")):
            raise FullArticleContractError("FACT_CITATION_REPORT_INCONSISTENT")
    unresolved = sum(row.get("status") not in {"SUPPORTED", "NON_FACTUAL"} for row in rows)
    if (facts.get("unresolved_count") != unresolved
            or (unresolved and facts.get("status") == "PASS")):
        raise FullArticleContractError("FACT_STATUS_INCONSISTENT")
    if quality.get("fact_report_sha256") != value.fact_report.result_sha256:
        raise FullArticleContractError("QUALITY_FACT_BINDING_INVALID")
    # This offline contract cannot transport semantic M4 acceptance yet.
    if (quality.get("status") != "REVIEW_REQUIRED" or quality.get("quality_pass") is not False
            or quality.get("required_checks_complete") is not False):
        raise FullArticleContractError("FULL_ARTICLE_QUALITY_ACCEPTANCE_UNAVAILABLE")
    if not quality.get("checks") or not quality.get("review_input"):
        raise FullArticleContractError("QUALITY_REPORT_INCOMPLETE")
    identity = article_check_identity(candidate)
    for report in (facts, quality):
        if any(report.get(k) != v for k, v in identity.items()):
            raise FullArticleContractError("ARTICLE_REPORT_IDENTITY_INVALID")
    required_ids = {"raw_structure_and_locations", "heading_repetition_and_style",
        "content_quality_observations", "whole_article_budget", "fact_safety",
        "semantic_task_and_evidence_coverage", "baseline_genre_maturity_revision_cost"}
    checks = quality["checks"]
    ids = [c.get("id") for c in checks]
    if (len(set(ids)) != len(ids) or set(quality.get("required_check_ids", [])) != required_ids
            or {c.get("id") for c in checks if c.get("required")} != required_ids):
        raise FullArticleContractError("QUALITY_REQUIRED_CHECKS_MISSING")
    for check in checks:
        payload = {k: v for k, v in check.items() if k != "report_identity"}
        if (check.get("report_identity") != text_sha(canonical_json(payload))
                or check.get("input") != {**identity, "fact_report_sha256": value.fact_report.result_sha256}
                or check.get("article_version") != candidate.version
                or check.get("status") != check.get("check_result")
                or not check.get("evaluator") or "evidence" not in check):
            raise FullArticleContractError("QUALITY_CHECK_RECEIPT_INVALID")
    complete = all(c["check_result"] not in {"MANUAL_REVIEW_REQUIRED", "UNAVAILABLE_FOR_FULL_ARTICLE"}
                   for c in checks if c["required"])
    passed = complete and all(c["check_result"] == "PASS" for c in checks if c["required"])
    if (quality["required_checks_complete"] != complete or quality["quality_pass"] != passed
            or quality.get("article_qualified") != (passed and facts["status"] == "PASS")):
        raise FullArticleContractError("QUALITY_CHECK_AGGREGATE_INVALID")
    return value


def _issue(code: str, path: str, message: str) -> HandoffIntegrityIssue:
    return HandoffIntegrityIssue(code=code, path=path, message=message)


def _core_payload(value: Flow3OutputEnvelope | Flow4Input) -> dict:
    payload = value.model_dump(mode="json")
    return {field: payload[field] for field in _CORE_FIELDS}


def _call_name(node: ast.Call) -> str:
    parts: list[str] = []
    current: ast.expr = node.func
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return ".".join(reversed(parts))


def _boundary_issues(module_paths: Iterable[Path]) -> list[HandoffIntegrityIssue]:
    issues: list[HandoffIntegrityIssue] = []
    for path in sorted((Path(item) for item in module_paths), key=lambda item: str(item)):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError) as exc:
            issues.append(
                _issue(
                    "BOUNDARY_MODULE_UNINSPECTABLE",
                    str(path),
                    f"FLOW4 boundary module cannot be inspected: {exc}",
                )
            )
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                imported = [node.module or ""]
            else:
                imported = []
            for module_name in imported:
                if module_name.startswith(_PROHIBITED_IMPORT_PREFIXES):
                    issues.append(
                        _issue(
                            "LEGACY_BOUNDARY_IMPORT",
                            f"{path}:{node.lineno}",
                            f"prohibited FLOW4 import: {module_name}",
                        )
                    )

            if not isinstance(node, ast.Call):
                continue
            call_name = _call_name(node)
            lowered = call_name.lower()
            tail = lowered.rsplit(".", 1)[-1]
            if tail in _PROHIBITED_CALL_NAMES or any(
                fragment in lowered for fragment in _PROHIBITED_CALL_FRAGMENTS
            ):
                issues.append(
                    _issue(
                        "LEGACY_BOUNDARY_CALL",
                        f"{path}:{node.lineno}",
                        f"prohibited FLOW4 call: {call_name or '<dynamic call>'}",
                    )
                )
    return issues


def assert_flow4_boundary_isolated(
    module_paths: Iterable[str | Path] | None = None,
) -> tuple[Path, ...]:
    """Reject FLOW4 modules that import or call legacy generation/RAG paths."""

    paths = (
        tuple(Path(item) for item in module_paths)
        if module_paths is not None
        else tuple(Path(__file__).parent.glob("*.py"))
    )
    issues = _boundary_issues(paths)
    if issues:
        raise HandoffIntegrityError(issues)
    return paths


def _validate_report_value_types(flow4_input: Flow4Input) -> list[HandoffIntegrityIssue]:
    issues: list[HandoffIntegrityIssue] = []
    reports = flow4_input.validation_reports
    specifications = (
        ("structured", reports.structured, ("invalid_evidence_reference_count",)),
        ("fact", reports.fact, ("unsupported_count", "out_of_plan_count")),
        ("analysis", reports.analysis, ("analysis_fact_leak_count",)),
    )
    for report_name, report, count_fields in specifications:
        if type(report["passed"]) is not bool:
            issues.append(
                _issue(
                    "INVALID_VALIDATION_STATE",
                    f"validation_reports.{report_name}.passed",
                    "authoritative passed state must be a boolean",
                )
            )
        for field in count_fields:
            value = report[field]
            if type(value) is not int or value < 0:
                issues.append(
                    _issue(
                        "INVALID_VALIDATION_STATE",
                        f"validation_reports.{report_name}.{field}",
                        "authoritative risk count must be a non-negative integer",
                    )
                )
    return issues


def _extension_pollution_issues(flow4_input: Flow4Input) -> list[HandoffIntegrityIssue]:
    issues: list[HandoffIntegrityIssue] = []

    def inspect(value: object, path: str) -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                lowered = str(key).lower()
                if any(fragment in lowered for fragment in _PROHIBITED_EXTENSION_KEY_FRAGMENTS):
                    issues.append(
                        _issue(
                            "BOUNDARY_PAYLOAD_POLLUTION",
                            f"{path}.{key}",
                            "FLOW4 extensions cannot carry generation, repair, fallback, or RAG controls",
                        )
                    )
                inspect(nested, f"{path}.{key}")
        elif isinstance(value, (list, tuple)):
            for index, nested in enumerate(value):
                inspect(nested, f"{path}[{index}]")

    if flow4_input.extensions is not None:
        inspect(flow4_input.extensions, "extensions")
    return issues


def determine_flow4_risk_status(
    flow4_input: Flow4Input,
) -> Literal["NO_RECORDED_RISK", "REVIEW_REQUIRED"]:
    """Derive the frozen risk state without changing an authoritative report."""

    reports = flow4_input.validation_reports
    has_recorded_risk = (
        reports.structured["passed"] is not True
        or reports.fact["passed"] is not True
        or reports.analysis["passed"] is not True
        or reports.structured["invalid_evidence_reference_count"] > 0
        or reports.fact["unsupported_count"] > 0
        or reports.fact["out_of_plan_count"] > 0
        or reports.analysis["analysis_fact_leak_count"] > 0
    )
    return "REVIEW_REQUIRED" if has_recorded_risk else "NO_RECORDED_RISK"


def _has_recorded_risk(flow4_input: Flow4Input) -> bool:
    return determine_flow4_risk_status(flow4_input) == "REVIEW_REQUIRED"


def _risk_consistency_issues(flow4_input: Flow4Input) -> list[HandoffIntegrityIssue]:
    reports = flow4_input.validation_reports
    issues: list[HandoffIntegrityIssue] = []
    count_and_passed = (
        (
            "validation_reports.structured",
            reports.structured["invalid_evidence_reference_count"],
            reports.structured["passed"],
        ),
        (
            "validation_reports.fact",
            reports.fact["unsupported_count"] + reports.fact["out_of_plan_count"],
            reports.fact["passed"],
        ),
        (
            "validation_reports.analysis",
            reports.analysis["analysis_fact_leak_count"],
            reports.analysis["passed"],
        ),
    )
    for path, risk_count, passed in count_and_passed:
        if risk_count > 0 and passed is True:
            issues.append(
                _issue(
                    "VALIDATION_RISK_DOWNGRADED",
                    path,
                    "positive authoritative risk count cannot be carried as passed",
                )
            )

    if _has_recorded_risk(flow4_input) and flow4_input.source.flow3_status.upper() in {
        "PASS",
        "PASSED",
        "PASS_VALIDATION",
        "READY",
        "COMPLETED",
    }:
        issues.append(
            _issue(
                "FLOW3_STATUS_DOWNGRADED",
                "source.flow3_status",
                "FLOW3 risk reports cannot be carried with a pass/ready source status",
            )
        )
    return issues


def _preservation_issues(
    envelope: Flow3OutputEnvelope,
    flow4_input: Flow4Input,
) -> list[HandoffIntegrityIssue]:
    issues: list[HandoffIntegrityIssue] = []
    source_payload = _core_payload(envelope)
    target_payload = _core_payload(flow4_input)
    field_codes = {
        "parsed_structured_draft": "STRUCTURE_NOT_PRESERVED",
        "content_plan": "CONTENT_PLAN_NOT_PRESERVED",
        "fact_anchor_pack": "EVIDENCE_CHAIN_NOT_PRESERVED",
        "validation_reports": "VALIDATION_STATE_NOT_PRESERVED",
        "task": "TASK_SNAPSHOT_NOT_PRESERVED",
    }
    for field in _CORE_FIELDS:
        if source_payload[field] != target_payload[field]:
            issues.append(
                _issue(
                    field_codes[field],
                    field,
                    f"{field} differs from the FLOW3 output envelope",
                )
            )

    expected_digest = canonical_payload_sha256(source_payload)
    if flow4_input.source.payload_sha256 != expected_digest:
        issues.append(
            _issue(
                "PAYLOAD_DIGEST_NOT_PRESERVED",
                "source.payload_sha256",
                "payload digest differs from the FLOW3 output envelope",
            )
        )
    return issues


def _build_evidence_trace(flow4_input: Flow4Input) -> tuple[SentenceEvidenceTrace, ...]:
    anchors = {
        anchor.evidence_id: anchor
        for anchor in flow4_input.fact_anchor_pack.fact_anchors
    }
    trace: list[SentenceEvidenceTrace] = []
    for section in flow4_input.parsed_structured_draft.sections:
        for sentence_index, sentence in enumerate(section.sentences):
            if sentence.sentence_type == "FACT":
                evidence_ids = tuple(sentence.evidence_ids)
                anchor_roles = tuple(
                    anchors[evidence_id].role
                    for evidence_id in evidence_ids
                )
                trace_status = "MAPPED"
            else:
                evidence_ids = ()
                anchor_roles = ()
                trace_status = "ANALYSIS_UNANCHORED"
            trace.append(
                SentenceEvidenceTrace(
                    section_id=section.section_id,
                    sentence_index=sentence_index,
                    sentence_type=sentence.sentence_type,
                    evidence_ids=evidence_ids,
                    text_sha256=hashlib.sha256(sentence.text.encode("utf-8")).hexdigest(),
                    anchor_roles=anchor_roles,
                    trace_status=trace_status,
                )
            )
    return tuple(trace)


def run_handoff_integrity_gate(
    envelope: Flow3OutputEnvelope,
    flow4_input: Flow4Input,
    *,
    boundary_module_paths: Iterable[str | Path] | None = None,
) -> HandoffIntegrityReport:
    """Verify exact preservation and return sentence-level evidence trace."""

    issues = _preservation_issues(envelope, flow4_input)
    issues.extend(_validate_report_value_types(flow4_input))
    issues.extend(_extension_pollution_issues(flow4_input))
    if not any(issue.code == "INVALID_VALIDATION_STATE" for issue in issues):
        issues.extend(_risk_consistency_issues(flow4_input))

    try:
        assert_flow4_boundary_isolated(boundary_module_paths)
    except HandoffIntegrityError as exc:
        issues.extend(exc.issues)

    if issues:
        raise HandoffIntegrityError(issues)

    evidence_trace = _build_evidence_trace(flow4_input)
    fact_count = sum(item.sentence_type == "FACT" for item in evidence_trace)
    analysis_count = len(evidence_trace) - fact_count
    return HandoffIntegrityReport(
        handoff_id=flow4_input.handoff_id,
        flow3_status=flow4_input.source.flow3_status,
        risk_status=determine_flow4_risk_status(flow4_input),
        sentence_count=len(evidence_trace),
        fact_sentence_count=fact_count,
        analysis_sentence_count=analysis_count,
        fact_trace_mapped_count=sum(item.trace_status == "MAPPED" for item in evidence_trace),
        evidence_trace=evidence_trace,
    )
