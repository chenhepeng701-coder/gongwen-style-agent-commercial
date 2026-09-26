"""Must-deliver state mapping owned by the production orchestrator.

Gates report their result here; this module never changes a quality or safety
standard and never decides HTTP status.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Iterable

from app.models.generation import DeliveryDecision


PUBLIC_SUCCESS_STATES = frozenset({
    "SUCCESS",
    "SUCCESS_WITH_REPAIR",
    "SUCCESS_WITH_REVIEW",
})

SAFETY_BOUNDARY_RANK = {
    "WRITER_UNVERIFIED": 0,
    "REVIEW_REQUIRED": 1,
    "FACT_SAFE": 2,
}


class CandidateRole(str, Enum):
    """Internal lineage roles; DELIVERABLE remains an M5 decision."""

    RAW = "RAW"
    PARSED = "PARSED"
    REPAIRED = "REPAIRED"
    QUALITY = "QUALITY"
    ACCEPTED = "ACCEPTED"


class IssueRoute(str, Enum):
    """Internal continuation labels, never public delivery states."""

    SYSTEM_ERROR = "SYSTEM_ERROR"
    INTERNAL_REPAIR_REQUIRED = "INTERNAL_REPAIR_REQUIRED"
    VETO_FACT_REPAIR_REQUIRED = "VETO_FACT_REPAIR_REQUIRED"
    QUALITY_REPAIR_REQUIRED = "QUALITY_REPAIR_REQUIRED"
    INPUT_REQUIRED_PRE_WRITER = "INPUT_REQUIRED_PRE_WRITER"


_RAW_BODY_FIELD = re.compile(
    r'"(?:title|introduction|heading|text)"\s*:\s*("(?:\\.|[^"\\])*")',
    re.S,
)


def _stable_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def candidate_digest(
    *, title: str, content: str, structured_draft: dict[str, Any] | None,
) -> str:
    """Canonical, mapping-order-independent digest for one article version."""

    return _stable_digest({
        "title": str(title or "").strip(),
        "content": str(content or "").strip(),
        "structured_draft": copy.deepcopy(structured_draft or {}),
    })


def recover_article_from_raw(raw: str) -> tuple[str, str, dict[str, Any]] | None:
    """Recover only explicit article fields or an unmistakable plain article.

    This is a preservation helper, not a permissive parser: recovered text is
    never contract-qualified and an error message/empty wrapper is not a
    Candidate.
    """

    text = str(raw or "").strip()
    if not text:
        return None
    unwrapped = text
    if unwrapped.startswith("```json") and unwrapped.endswith("```"):
        unwrapped = unwrapped[7:-3].strip()
    elif unwrapped.startswith("```") and unwrapped.endswith("```"):
        unwrapped = unwrapped[3:-3].strip()
    parsed: dict[str, Any] = {}
    try:
        value = json.loads(unwrapped)
        if isinstance(value, dict):
            parsed = value
    except (json.JSONDecodeError, TypeError):
        parsed = {}

    fields: list[str] = []
    title = str(parsed.get("title") or "").strip()
    introduction = str(parsed.get("introduction") or "").strip()
    if title:
        fields.append(title)
    if introduction:
        fields.append(introduction)
    for section in parsed.get("sections") or []:
        if not isinstance(section, dict):
            continue
        heading = str(section.get("heading") or "").strip()
        if heading:
            fields.append(heading)
        for sentence in section.get("sentences") or []:
            if isinstance(sentence, dict) and str(sentence.get("text") or "").strip():
                fields.append(str(sentence["text"]).strip())
        for paragraph in section.get("paragraphs") or []:
            if not isinstance(paragraph, dict):
                continue
            for sentence in paragraph.get("sentences") or []:
                if isinstance(sentence, dict) and str(sentence.get("text") or "").strip():
                    fields.append(str(sentence["text"]).strip())

    if not fields:
        for match in _RAW_BODY_FIELD.finditer(unwrapped):
            try:
                value = json.loads(match.group(1))
            except json.JSONDecodeError:
                continue
            if str(value or "").strip():
                fields.append(str(value).strip())
        title = fields[0] if fields else ""

    content = "\n".join(dict.fromkeys(fields)).strip()
    han_count = len(re.findall(r"[\u4e00-\u9fff]", content))
    if not content and han_count == 0 and len(unwrapped) >= 120 and "\n" in unwrapped:
        content = unwrapped
        han_count = len(re.findall(r"[\u4e00-\u9fff]", content))
    if not content or (han_count < 12 and len(content) < 40):
        return None
    return title, content, parsed


def route_issue(
    issue_type: str,
    *,
    candidate_exists: bool,
    pre_writer_input_required: bool = False,
    veto_fact: bool = False,
) -> IssueRoute:
    """Route an existing issue without creating a second Fact Policy."""

    if pre_writer_input_required:
        return IssueRoute.INPUT_REQUIRED_PRE_WRITER
    if veto_fact:
        return IssueRoute.VETO_FACT_REPAIR_REQUIRED
    normalized = str(issue_type or "").upper()
    if not candidate_exists:
        return IssueRoute.SYSTEM_ERROR
    if normalized in {
        "FAIL_SOURCE_PARAPHRASE", "SOURCEPARAPHRASEERROR",
        "BUDGET_CONTRACT_CONFLICT", "LENGTH_TOO_SHORT", "LENGTH_TOO_LONG",
        "REQUIRED_EVIDENCE_MISSING",
    }:
        return IssueRoute.QUALITY_REPAIR_REQUIRED
    if normalized in {
        "A_HARD_BLOCK", "FAIL_UNSUPPORTED_FACT", "SEMANTIC_STATE_UPGRADE",
        "ILLEGAL_EVIDENCE_ID", "EVIDENCE_SCOPE_VIOLATION",
    }:
        return IssueRoute.VETO_FACT_REPAIR_REQUIRED
    return IssueRoute.INTERNAL_REPAIR_REQUIRED


@dataclass(frozen=True)
class ArticleCandidate:
    """One recoverable article version retained by the orchestrator."""

    title: str
    content: str
    structured_draft: dict[str, Any]
    stage: str
    safety_boundary: str
    role: str = CandidateRole.PARSED.value
    candidate_id: str = ""
    version: int = 0
    digest: str = ""
    parent_candidate_id: str = ""

    def __post_init__(self) -> None:
        digest = self.digest or candidate_digest(
            title=self.title,
            content=self.content,
            structured_draft=self.structured_draft,
        )
        object.__setattr__(self, "digest", digest)
        if not self.candidate_id:
            object.__setattr__(self, "candidate_id", f"CANDIDATE-{digest[:16]}")


@dataclass(frozen=True)
class CandidateReportBinding:
    candidate_id: str
    candidate_version: int
    candidate_digest: str
    report_type: str
    report_digest: str
    report: dict[str, Any]


class LastKnownGoodArticle:
    """Immutable Candidate lineage plus explicit selected/accepted pointers."""

    def __init__(self) -> None:
        self._candidate: ArticleCandidate | None = None
        self._safety_rank = -1
        self._versions: list[ArticleCandidate] = []
        self._bindings: dict[tuple[str, str], CandidateReportBinding] = {}
        self._original_candidate_id = ""
        self._accepted_candidate_id = ""

    @property
    def candidate(self) -> ArticleCandidate | None:
        return self._candidate

    @property
    def versions(self) -> tuple[ArticleCandidate, ...]:
        return tuple(self._versions)

    @property
    def original(self) -> ArticleCandidate | None:
        return self.get(self._original_candidate_id)

    @property
    def accepted(self) -> ArticleCandidate | None:
        return self.get(self._accepted_candidate_id)

    def get(self, candidate_id: str) -> ArticleCandidate | None:
        return next(
            (item for item in self._versions if item.candidate_id == candidate_id),
            None,
        )

    @property
    def article_exists(self) -> bool:
        return bool(self._candidate and self._candidate.content.strip())

    @property
    def article_recoverable(self) -> bool:
        return self.article_exists

    def update(
        self,
        *,
        title: str,
        content: str,
        structured_draft: dict[str, Any] | None,
        stage: str,
        safety_boundary: str,
        role: str = CandidateRole.PARSED.value,
        parent_candidate_id: str = "",
    ) -> bool:
        normalized_content = str(content or "").strip()
        rank = SAFETY_BOUNDARY_RANK.get(safety_boundary, -1)
        if not normalized_content or rank < 0:
            return False
        version = len(self._versions) + 1
        digest = candidate_digest(
            title=title,
            content=normalized_content,
            structured_draft=structured_draft,
        )
        candidate = ArticleCandidate(
            title=str(title or "").strip(),
            content=normalized_content,
            structured_draft=copy.deepcopy(structured_draft or {}),
            stage=str(stage or ""),
            safety_boundary=safety_boundary,
            role=str(role or CandidateRole.PARSED.value),
            candidate_id=f"CANDIDATE-{version:04d}-{digest[:12]}",
            version=version,
            digest=digest,
            parent_candidate_id=str(parent_candidate_id or ""),
        )
        self._versions.append(candidate)
        if not self._original_candidate_id and candidate.role != CandidateRole.RAW.value:
            self._original_candidate_id = candidate.candidate_id
        # A same-rank later version is retained but cannot silently replace the
        # selected Original. Explicit accept() is reserved for the later owner.
        if self._candidate is None or rank > self._safety_rank:
            self._candidate = candidate
            self._safety_rank = rank
            return True
        return False

    def preserve_raw(
        self,
        raw: str,
        *,
        stage: str,
        safety_boundary: str = "WRITER_UNVERIFIED",
        parent_candidate_id: str = "",
    ) -> ArticleCandidate | None:
        recovered = recover_article_from_raw(raw)
        if recovered is None:
            return None
        title, content, structured = recovered
        self.update(
            title=title,
            content=content,
            structured_draft=structured,
            stage=stage,
            safety_boundary=safety_boundary,
            role=CandidateRole.RAW.value,
            parent_candidate_id=parent_candidate_id,
        )
        return self._versions[-1]

    def accept(self, candidate_id: str) -> ArticleCandidate:
        candidate = self.get(candidate_id)
        if candidate is None:
            raise ValueError("UNKNOWN_CANDIDATE_VERSION")
        self._accepted_candidate_id = candidate_id
        self._candidate = candidate
        self._safety_rank = SAFETY_BOUNDARY_RANK.get(candidate.safety_boundary, -1)
        return candidate

    def bind_report(
        self,
        candidate_id: str,
        report_type: str,
        report: dict[str, Any],
    ) -> CandidateReportBinding:
        candidate = self.get(candidate_id)
        if candidate is None:
            raise ValueError("UNKNOWN_CANDIDATE_VERSION")
        binding = CandidateReportBinding(
            candidate_id=candidate.candidate_id,
            candidate_version=candidate.version,
            candidate_digest=candidate.digest,
            report_type=str(report_type or ""),
            report_digest=_stable_digest(report),
            report=copy.deepcopy(report),
        )
        self._bindings[(candidate.candidate_id, binding.report_type)] = binding
        return binding

    def report_for(
        self,
        candidate: ArticleCandidate,
        report_type: str,
    ) -> CandidateReportBinding | None:
        binding = self._bindings.get((candidate.candidate_id, report_type))
        if binding is None:
            return None
        if (
            binding.candidate_version != candidate.version
            or binding.candidate_digest != candidate.digest
        ):
            return None
        return binding

    def lineage_summary(self) -> list[dict[str, Any]]:
        return [
            {
                "candidate_id": item.candidate_id,
                "version": item.version,
                "digest": item.digest,
                "parent_candidate_id": item.parent_candidate_id,
                "role": item.role,
                "stage": item.stage,
                "safety_boundary": item.safety_boundary,
                "selected": bool(
                    self._candidate
                    and self._candidate.candidate_id == item.candidate_id
                ),
                "accepted": self._accepted_candidate_id == item.candidate_id,
            }
            for item in self._versions
        ]


def full_article_review_result(render_result, manifest=None):
    """Project the same canonical version and owner reports to the public model."""
    import json
    from app.models.generation import ArticleGenResult
    from app.services.generation.generator import count_official_draft_words
    from app.services.flow4.renderer import render_full_article
    from app.services.generation.work_summary_quality_gate import evaluate_full_article_quality
    rendered = render_full_article(render_result.handoff)
    if rendered != render_result:
        raise ValueError("FULL_ARTICLE_RENDER_CHANGED")
    handoff = rendered.handoff
    # Reproduce evaluator receipts at the service boundary. FLOW4 only checks
    # transport integrity and must not import or implement a safety engine.
    if handoff.quality_report != evaluate_full_article_quality(
            handoff.candidate, handoff.citations, handoff.fact_report):
        raise ValueError("QUALITY_REPORT_EVALUATION_MISMATCH")
    facts = json.loads(handoff.fact_report.result_json)
    quality = json.loads(handoff.quality_report.result_json)
    issues = []
    for row in facts["units"]:
        if row["status"] in {"SUPPORTED", "NON_FACTUAL"}:
            continue
        issues.append({**issue_item(stage="FACT", issue_type=row["result"]["reason"],
            description="该原文片段尚未获得充分事实支持，请人工复核。", section_id=row["block_id"]),
            "start": row["start"], "end": row["end"], "line": row["line"],
            "observed_text": row["observed_text"], "unit_id": row["unit_id"],
            "risk_codes": row.get("risk_codes", []),
            "boundary_findings": row["result"].get("boundary_findings", []),
            "review_suggestion": row["review_suggestion"]})
    for check in quality["checks"]:
        if check["required"] and check["check_result"] != "PASS":
            issues.append(issue_item(stage="QUALITY", issue_type=check["id"],
                description=check.get("migration", "必需质量检查尚未完成。")))
    decision = review_decision(issues=issues, failure_stage="FULL_ARTICLE_CHECKS",
        reason="原稿完整保留；事实待核及必需质量检查未完成，不能认定正式合格稿。")
    paths = {}
    if manifest is not None:
        if (manifest["candidate_digest"] != handoff.candidate.digest
                or manifest["fact_report_sha256"] != handoff.fact_report.result_sha256
                or manifest["quality_report_sha256"] != handoff.quality_report.result_sha256):
            raise ValueError("ARTIFACT_REPORT_BINDING_INVALID")
        paths = {item["path"].rsplit(".", 1)[-1]: item["path"] for item in manifest["artifacts"]}
    return ArticleGenResult(title=rendered.title, content=rendered.final_text,
        word_count=count_official_draft_words(rendered.final_text),
        content_projection=rendered.projection, full_article_check_status={
            "candidate_digest": handoff.candidate.digest,
            "evidence_sha256": handoff.candidate.evidence_sha256,
            "fact": facts["status"], "quality": quality["status"],
            "required_checks_complete": quality["required_checks_complete"],
            "article_version": handoff.candidate.version,
            "article_hash": handoff.candidate.content_sha256,
            "fact_report_sha256": handoff.fact_report.result_sha256,
            "quality_report_sha256": handoff.quality_report.result_sha256,
            "fact_report": handoff.fact_report.model_dump(),
            "quality_report": handoff.quality_report.model_dump(),
            "checks": quality["checks"]},
        delivery_status=decision.delivery_status, official_success=False,
        delivery_decision=decision, risk_items=issues, user_confirmation_required=True,
        fact_risk_count=facts["unresolved_count"], unverified_claim_count=facts["unresolved_count"],
        final_pipeline_stage_completed=False, selected_candidate_stage="FULL_ARTICLE_OFFLINE",
        length_gate_status="REVIEW_REQUIRED", structure_gate_status="REVIEW_REQUIRED",
        save_path=paths.get("txt", ""), docx_path=paths.get("docx", ""),
        reason=decision.reason, next_step=decision.next_step, review_suggestion=decision.next_step)


def full_article_unchecked_result(candidate, *, error_type: str):
    """Deliver an existing valid body when checks are unavailable, never PASS.

    This is delivery policy only, with no fabricated evaluator report/receipt.
    """
    from app.models.full_article import FullArticleCandidate, FullArticleProjection, article_blocks
    from app.models.generation import ArticleGenResult
    candidate = FullArticleCandidate.model_validate(candidate.model_dump())
    issue = issue_item(stage="FULL_ARTICLE_CHECKS", issue_type="CHECKS_UNAVAILABLE",
        description="文章已生成，但自动核验未完成，全部正文须人工复核。", reason=error_type)
    decision = review_decision(issues=[issue], failure_stage="FULL_ARTICLE_CHECKS",
        reason="自动核验不可用；完整保留模型原稿，不能作为安全通过正式稿。")
    return ArticleGenResult(title=candidate.title, content=candidate.content,
        content_projection=FullArticleProjection(content_sha256=candidate.content_sha256,
            candidate_digest=candidate.digest, blocks=article_blocks(candidate.content)),
        full_article_check_status={"fact": "UNAVAILABLE", "quality": "UNAVAILABLE",
            "required_checks_complete": False, "reason": "CHECKS_UNAVAILABLE"},
        delivery_status="SUCCESS_WITH_REVIEW", delivery_decision=decision, official_success=False,
        article_qualified=False, user_confirmation_required=True, risk_items=[issue],
        final_pipeline_stage_completed=False, length_gate_status="UNAVAILABLE", structure_gate_status="UNAVAILABLE",
        reason=decision.reason, next_step=decision.next_step, review_suggestion=decision.next_step)


def issue_item(
    *,
    stage: str,
    issue_type: str,
    description: str,
    section_id: str = "",
    paragraph_index: int = -1,
    field_name: str = "content",
    reason: str = "",
) -> dict[str, Any]:
    """Build the stable location-bearing issue shape used for review delivery."""

    return {
        "stage": stage,
        "section_id": section_id,
        "section_heading": "",
        "paragraph_index": paragraph_index,
        "field_name": field_name,
        "risk_type": issue_type,
        "internal_error_type": issue_type,
        "risk_description": description,
        "reason": reason or description,
    }


def review_decision(
    *,
    issues: Iterable[dict[str, Any]],
    failure_stage: str,
    reason: str,
    delivery_warnings: Iterable[str] = (),
    next_step: str = "请对照原始资料和标注位置人工复核后再作为正式稿使用。",
) -> DeliveryDecision:
    """Map a recoverable content failure to public review delivery."""

    return DeliveryDecision(
        delivery_status="SUCCESS_WITH_REVIEW",
        article_exists=True,
        article_recoverable=True,
        article_qualified=False,
        official_success=False,
        user_confirmation_required=True,
        issues=list(issues),
        delivery_warnings=list(delivery_warnings),
        failure_class="RECOVERABLE_CONTENT_FAILURE",
        failure_stage=failure_stage,
        reason=reason,
        next_step=next_step,
    )


def qualified_decision(
    *,
    repaired: bool,
    delivery_warnings: Iterable[str] = (),
) -> DeliveryDecision:
    """Map a gate-qualified body to SUCCESS or SUCCESS_WITH_REPAIR."""

    warnings = list(delivery_warnings)
    return DeliveryDecision(
        delivery_status="SUCCESS_WITH_REPAIR" if repaired else "SUCCESS",
        article_exists=True,
        article_recoverable=True,
        article_qualified=True,
        official_success=True,
        user_confirmation_required=False,
        issues=[],
        delivery_warnings=warnings,
        failure_class=("RECOVERABLE_POSTPROCESS_FAILURE" if warnings else "NONE"),
        failure_stage="ARTIFACT_DELIVERY" if warnings else "",
        reason=("部分交付附件未生成，API 正文仍可交付。" if warnings else ""),
        next_step=("可直接使用 API 正文，并按警告重试所需附件。" if warnings else "可交付正式稿。"),
    )


def deliver_artifacts_resilient(
    render_result: Any,
    output_dir: Any,
    *,
    formats: Iterable[str],
    output_name: str,
    deliverer: Callable[..., Any],
) -> tuple[list[Any], list[str]]:
    """Attempt every requested non-body artifact without losing API text."""

    artifacts: list[Any] = []
    warnings: list[str] = []
    for file_type in tuple(dict.fromkeys(str(value).lower() for value in formats)):
        try:
            delivery = deliverer(
                render_result,
                output_dir,
                formats=(file_type,),
                output_name=output_name,
            )
            artifacts.extend(list(delivery.artifacts))
        except Exception as exc:  # artifact stage is explicitly non-body delivery
            warnings.append(
                f"{file_type.upper()} 产物生成失败：{type(exc).__name__}"
            )
    return artifacts, warnings
