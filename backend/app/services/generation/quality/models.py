"""Pure data models for versioned document quality contracts.

This module intentionally contains no generation, retrieval, validation, or provider
integration.  Quality contracts describe product expectations; they are neither a
fact source nor a runtime quality gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator, model_serializer


class AutomationLevel(str, Enum):
    """How far a quality judgement can be automated without inventing certainty."""

    AUTOMATIC = "AUTOMATIC"
    ASSISTED = "ASSISTED"
    MANUAL = "MANUAL"


class CurrentSystemCoverage(str, Enum):
    """Whether the current production safety chain already enforces an item."""

    CURRENT = "CURRENT"
    PARTIAL = "PARTIAL"
    FUTURE_QUALITY_GATE = "FUTURE_QUALITY_GATE"
    REVIEW_ONLY = "REVIEW_ONLY"


class SampleTier(str, Enum):
    CORE = "CORE"
    BOUNDARY = "BOUNDARY"
    NEGATIVE = "NEGATIVE"


class ContentFunction(str, Enum):
    """Semantic jobs in an article; values do not prescribe section count/order."""

    PERIOD_TASK_PROGRESS = "period_task_progress"
    INDICATOR_STATUS = "indicator_status"
    MAJOR_WORK = "major_work"
    CONCRETE_OBJECT_PROGRESS = "concrete_object_progress"
    VERIFIABLE_RESULT = "verifiable_result"
    DISTINCTIVE_PRACTICE = "distinctive_practice"
    PROBLEM_ANALYSIS = "problem_analysis"
    UNFINISHED_TASK = "unfinished_task"
    NEXT_STEP_ARRANGEMENT = "next_step_arrangement"
    LONG_PERIOD_CHANGE = "long_period_change"
    GOVERNANCE_CHAIN = "governance_chain"
    REFORM_INNOVATION = "reform_innovation"


@dataclass(frozen=True)
class SourceReference:
    document_path: str
    section: str
    line_range: str


@dataclass(frozen=True)
class UniversalRule:
    rule_id: str
    name: str
    description: str
    automation_level: AutomationLevel
    source_reference: SourceReference


@dataclass(frozen=True)
class HardGate:
    gate_id: str
    name: str
    description: str
    severity: str
    automation_level: AutomationLevel
    evidence_requirement: str
    current_system_coverage: CurrentSystemCoverage
    source_reference: SourceReference


@dataclass(frozen=True)
class QualityDimension:
    dimension_id: str
    name: str
    weight: int
    description: str
    automation_level: AutomationLevel
    observable_signals: tuple[str, ...]
    source_reference: SourceReference


@dataclass(frozen=True)
class CommercialQualityLevel:
    level_id: str
    score_range: str
    positioning: str
    commercial_target: str
    source_reference: SourceReference


@dataclass(frozen=True)
class ObservableIndicator:
    indicator_id: str
    name: str
    description: str
    automation_level: AutomationLevel
    source_reference: SourceReference


@dataclass(frozen=True)
class ScenarioProfile:
    scenario_id: str
    display_name: str
    purpose: str
    preferred_content_functions: tuple[ContentFunction, ...]
    optional_content_functions: tuple[ContentFunction, ...]
    structural_tendencies: tuple[str, ...]
    problem_policy: str
    next_step_policy: str
    evidence_expectation: str
    prohibited_template_assumptions: tuple[str, ...]
    source_reference: tuple[SourceReference, ...]


@dataclass(frozen=True)
class WorkChainDefinition:
    definition: str
    standard_elements: tuple[str, ...]
    governance_elements: tuple[str, ...]
    semantic_constraints: tuple[str, ...]
    source_reference: SourceReference


@dataclass(frozen=True)
class SparseMaterialPolicy:
    trigger: str
    allowed_responses: tuple[str, ...]
    prohibited_responses: tuple[str, ...]
    source_reference: tuple[SourceReference, ...]


@dataclass(frozen=True)
class ProblemPolicyCase:
    condition: str
    allowed_treatment: str


@dataclass(frozen=True)
class ProblemPolicy:
    universal_section_required: bool
    cases: tuple[ProblemPolicyCase, ...]
    high_quality_components: tuple[str, ...]
    prohibited_default_templates: tuple[str, ...]
    source_reference: tuple[SourceReference, ...]


@dataclass(frozen=True)
class NextStepPolicy:
    universal_section_required: bool
    preferred_sources: tuple[str, ...]
    allowed_placements: tuple[str, ...]
    prohibited_default_templates: tuple[str, ...]
    source_reference: tuple[SourceReference, ...]


@dataclass(frozen=True)
class HeadingPolicy:
    desirable_properties: tuple[str, ...]
    prohibited_assumptions: tuple[str, ...]
    source_reference: SourceReference


@dataclass(frozen=True)
class AISmell:
    smell_id: str
    name: str
    definition: str
    automation_level: AutomationLevel
    observable_signals: tuple[str, ...]
    source_reference: SourceReference


@dataclass(frozen=True)
class GoldenSample:
    sample_id: str
    title: str
    tier: SampleTier
    scenario: str | None
    structure_value: int | None
    material_value: int | None
    expression_value: int | None
    intended_regression_use: str | None
    source_reference: SourceReference


@dataclass(frozen=True)
class GoldenRegistryMetadata:
    source_stated_regression_sizes: tuple[tuple[SampleTier, int], ...]
    membership_status: tuple[tuple[SampleTier, str], ...]
    note: str
    source_reference: SourceReference


@dataclass(frozen=True)
class AutomationBoundary:
    level: AutomationLevel
    definition: str
    examples: tuple[str, ...]
    source_reference: tuple[SourceReference, ...]


@dataclass(frozen=True)
class WorkSummaryQualityContract:
    contract_id: str
    contract_version: str
    document_type: str
    source_documents: tuple[str, ...]
    universal_rules: tuple[UniversalRule, ...]
    hard_gates: tuple[HardGate, ...]
    quality_dimensions: tuple[QualityDimension, ...]
    commercial_quality_levels: tuple[CommercialQualityLevel, ...]
    observable_indicators: tuple[ObservableIndicator, ...]
    scenario_profiles: tuple[ScenarioProfile, ...]
    content_functions: tuple[ContentFunction, ...]
    work_chain_definition: WorkChainDefinition
    sparse_material_policy: SparseMaterialPolicy
    problem_policy: ProblemPolicy
    next_step_policy: NextStepPolicy
    heading_policy: HeadingPolicy
    ai_smells: tuple[AISmell, ...]
    golden_sample_registry: tuple[GoldenSample, ...]
    golden_registry_metadata: GoldenRegistryMetadata
    automation_boundaries: tuple[AutomationBoundary, ...]


def validate_contract(contract: WorkSummaryQualityContract) -> tuple[str, ...]:
    """Return deterministic schema/consistency errors without scoring any draft."""

    errors: list[str] = []

    def require_unique(label: str, values: tuple[str, ...]) -> None:
        if len(values) != len(set(values)):
            errors.append(f"duplicate_{label}")

    require_unique("universal_rule_id", tuple(x.rule_id for x in contract.universal_rules))
    require_unique("hard_gate_id", tuple(x.gate_id for x in contract.hard_gates))
    require_unique("dimension_id", tuple(x.dimension_id for x in contract.quality_dimensions))
    require_unique("scenario_id", tuple(x.scenario_id for x in contract.scenario_profiles))
    require_unique("indicator_id", tuple(x.indicator_id for x in contract.observable_indicators))
    require_unique("ai_smell_id", tuple(x.smell_id for x in contract.ai_smells))
    require_unique("golden_sample_id", tuple(x.sample_id for x in contract.golden_sample_registry))
    require_unique("golden_sample_title", tuple(x.title for x in contract.golden_sample_registry))

    if sum(item.weight for item in contract.quality_dimensions) != 100:
        errors.append("quality_dimension_weight_total_not_100")
    if {gate.gate_id for gate in contract.hard_gates} != {f"G{i}" for i in range(1, 8)}:
        errors.append("hard_gate_set_must_be_G1_to_G7")
    if {boundary.level for boundary in contract.automation_boundaries} != set(AutomationLevel):
        errors.append("automation_boundary_levels_incomplete")
    if not contract.source_documents:
        errors.append("source_documents_required")

    return tuple(errors)


# M4 runtime contracts share the existing quality contract module. These data
# models do not evaluate prose, alter evidence, or choose public delivery states.
CommercialDimension = Literal[
    "GOAL_COVERAGE", "CONTENT_HIERARCHY", "REPETITION", "SENTENCE_QUALITY",
    "COHERENCE", "ROLE_FIT", "OFFICIAL_STYLE",
]
COMMERCIAL_DIMENSIONS = (
    "GOAL_COVERAGE", "CONTENT_HIERARCHY", "REPETITION", "SENTENCE_QUALITY",
    "COHERENCE", "ROLE_FIT", "OFFICIAL_STYLE",
)
RevisionCost = Literal["LOW", "MEDIUM", "HIGH"]


class _CommercialContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class CommercialFinding(_CommercialContract):
    """M4 finding：status 是唯一改写标志，不另设 rewrite_required 字段。

    诊断说明始终写入 issue_summary；改写正文只在响应的段落修订列表中返回。
    """

    # Pydantic's after-validator is not exported automatically. Expose the
    # same conditional rule in both Original and Revised response schemas.
    model_config = ConfigDict(json_schema_extra={
        "if": {"properties": {"status": {"const": "REWRITE_REQUIRED"}}, "required": ["status"]},
        "then": {"required": ["rewrite_reason"], "properties": {"rewrite_reason": {"pattern": r"\S"}}},
        "else": {"properties": {"rewrite_reason": {"const": ""}}},
    })

    paragraph_id: str = Field(min_length=1)
    dimension: CommercialDimension
    status: Literal["SATISFIED", "MINOR", "REWRITE_REQUIRED"] = Field(
        description="SATISFIED/MINOR 不要求改写；只有 REWRITE_REQUIRED 要求改写。")
    issue_summary: str = Field(min_length=1)
    # Verbatim evidence locates the judgement in this Candidate, not a sample.
    observed_text: str = Field(min_length=1, description=(
        "从被评估版本的对应 paragraph_id 段落逐字复制非空、连续原文，须可确定定位。"
        "quality_report 引用原稿 context.paragraphs[].text；revised_quality_assessment 引用合并后修订稿对应段落。"
        "禁止自行添加省略号拼接、多片段合并、改写后再引用或摘要式定位。"
        "引用有歧义时扩大连续片段或复制完整段落；段落级改写可引用完整段落，不得伪造首句……末句。"))
    rewrite_reason: str = Field(default="", description=(
        "status=SATISFIED/MINOR 时只能省略或填写空字符串，不接受 null、空白字符或非空理由。"
        "status=REWRITE_REQUIRED 时必须填写含非空白字符的具体改写理由。"
        "finding 内不得添加 rewrite_target/revised_text 等 schema 外字段。"))

    @model_validator(mode="after")
    def require_reason(self):
        if self.status == "REWRITE_REQUIRED" and not self.rewrite_reason.strip():
            raise ValueError("rewrite reason required")
        if self.status != "REWRITE_REQUIRED" and self.rewrite_reason:
            raise ValueError("non-rewrite finding cannot request repair")
        return self


class CommercialAssessment(_CommercialContract):
    candidate_version: int = Field(ge=1)
    candidate_digest: str = Field(min_length=64, max_length=64)
    assessment_input_digest: str = Field(min_length=64, max_length=64)
    revision_cost: RevisionCost
    revision_cost_reason: str = Field(min_length=1)
    findings: list[CommercialFinding] = Field(min_length=1)


class CommercialUpstreamBlocker(_CommercialContract):
    """M4 input projection of an existing Owner decision; never a new safety verdict."""

    paragraph_id: str = Field(min_length=1)
    issue_type: Literal["FACT_SAFETY_HARD_BLOCK", "STRUCTURE_RECHECK_REQUIRED", "ANALYSIS_RECHECK_REQUIRED"]
    source: Literal["FACT_SAFETY", "STRUCTURE", "ANALYSIS"]
    severity: Literal["HARD_BLOCK", "RECHECK_REQUIRED"]
    blocking: Literal[True] = True


class CommercialRewriteTarget(_CommercialContract):
    """段落级改写目标：用 paragraph_id 定位整段；原文引用在对应 finding.observed_text。

    不添加 original_text/quoted_text/source_span；不伪造跨片段引用。
    """

    paragraph_id: str = Field(min_length=1)
    issue_type: str = Field(min_length=1)
    issue_summary: str = Field(min_length=1)
    rewrite_reason: str = Field(min_length=1)
    quality_issues: list[CommercialDimension] = Field(description=(
        "QUALITY_ISSUES：七维质量问题，与 REWRITE_REQUIRED findings 对应；不得放入上游 blocker 标签。"))
    upstream_blockers: list[CommercialUpstreamBlocker] = Field(description=(
        "UPSTREAM_BLOCKERS：从 context.upstream_blocker_map 逐项原样复制本段条目；"
        "无则为空。不得新增、删除、降级、更改来源或段落。issue_type 是两类来源标签的中文分号聚合。"))


class CommercialParagraphRevision(_CommercialContract):
    paragraph_id: str = Field(min_length=1)
    rewrite_reason: str = Field(min_length=1)
    revised_body: str = Field(min_length=1)
    # Text-only slots retain the original FACT/ANALYSIS and evidence bindings.
    # A slot may contain several natural sentences (e.g. splitting a long one).
    sentence_texts: list[str] = Field(min_length=1)


class CommercialFactRetention(_CommercialContract):
    paragraph_id: str = Field(min_length=1)
    sentence_index: int = Field(ge=0)
    status: Literal["RETAINED", "LOST", "UNCERTAIN"]
    reason: str = Field(min_length=1)


class CommercialRevisionAssessment(_CommercialContract):
    """Same-response review of the WHOLE merged revision, not Original findings.

    The owner binds this to the locally computed version/digest after verifying
    the persisted response receipt and exact patches; the model never hashes.
    """
    revision_cost: RevisionCost
    revision_cost_reason: str = Field(min_length=1)
    findings: list[CommercialFinding] = Field(min_length=1)
    fact_retention: list[CommercialFactRetention]


class CommercialQualityResponse(_CommercialContract):
    quality_report: CommercialAssessment
    overall_verdict: Literal["QUALITY_ACCEPT", "QUALITY_REWRITE_REQUIRED"]
    rewrite_targets: list[CommercialRewriteTarget] = Field(description=(
        "QUALITY_ACCEPT 时为空；需改写时每个目标 paragraph_id 只列一次，覆盖原稿 REWRITE_REQUIRED findings "
        "及 context.upstream_blocker_map 的全部段落；quality_issues 与 upstream_blockers 分开保留来源。"))
    revised_paragraphs: list[CommercialParagraphRevision] = Field(description=(
        "与 rewrite_targets 按 paragraph_id 一一对应，不得重复、缺失或增加非目标段；"
        "对应 rewrite_reason 必须一致。QUALITY_ACCEPT 时为空。"))
    revised_quality_assessment: CommercialRevisionAssessment | None = None

    @model_serializer(mode="wrap")
    def preserve_existing_response_digest(self, handler):
        data = handler(self)
        if self.revised_quality_assessment is None:
            data.pop("revised_quality_assessment", None)
        return data


class CommercialQualityReport(_CommercialContract):
    schema_version: Literal["m4-commercial-quality-1"] = "m4-commercial-quality-1"
    owner: Literal["M4"] = "M4"
    # Local accounting identity, supplied by the authorized caller, never the model.
    m4_execution_id: str = ""
    commercial_target: Literal["COMMERCIAL_USABLE_DRAFT"] = "COMMERCIAL_USABLE_DRAFT"
    candidate_id: str
    candidate_version: int
    candidate_digest: str
    assessment_input_digest: str
    overall_verdict: Literal["QUALITY_ACCEPT", "QUALITY_REWRITE_REQUIRED"]
    revision_cost: RevisionCost
    revision_cost_reason: str
    findings: list[CommercialFinding]
    rewrite_targets: list[CommercialRewriteTarget]
    acceptance_blockers: list[str]
    upstream_blocker_map: list[CommercialUpstreamBlocker]
    assessment: CommercialAssessment
    quality_call_count: Literal[1] = 1
    rewrite_call_count: Literal[0] = 0
    single_call_response: CommercialQualityResponse | None = None


class CommercialIndependentQualityRecheck(_CommercialContract):
    """Read-only assessment of one frozen Candidate; digest lives in its binding.

    No revised text or rewrite request is accepted. The caller copies identity
    and input hashes from the Owner's admission, never from an older assessment.
    """
    schema_version: Literal["m4-independent-quality-recheck-1"] = "m4-independent-quality-recheck-1"
    owner: Literal["M4"] = "M4"
    read_only: Literal[True] = True
    recheck_index: Literal[1] = 1
    report_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    candidate_version: int = Field(ge=1)
    candidate_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    parent_candidate_id: str
    candidate_lineage_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    assessment_input_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    assessment: CommercialRevisionAssessment
    core_goal_retention: Literal["PASS", "FAIL", "UNCERTAIN"]
    core_goal_retention_reason: str = Field(min_length=1)
    fact_detail_preservation: Literal["PASS", "FAIL", "UNCERTAIN"]
    fact_detail_preservation_reason: str = Field(min_length=1)
    final_quality_verdict: Literal["PASS", "FAIL"]
    final_quality_reason: str = Field(min_length=1)
