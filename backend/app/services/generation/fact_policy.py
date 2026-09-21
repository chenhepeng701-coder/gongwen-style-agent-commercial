"""Executable terminology map for the frozen V3 fact-policy alignment.

This module does not inspect or rewrite prose.  Fact Safety and the production
orchestrator retain their existing ownership; M2 only freezes their mapping.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


FACT_POLICY_ALIGNMENT_VERSION = "v3-fact-policy-alignment-1"
FACT_POLICY_DOCUMENT = "docs/architecture/v3_fact_policy_alignment_v1.0.md"


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FactClassPolicy(_StrictFrozenModel):
    architecture_fact_class: Literal["F0", "F1", "F2"]
    current_code_rules: tuple[str, ...] = Field(min_length=1)
    prompt_rules: tuple[str, ...] = Field(min_length=1)
    allowed_delivery_states: tuple[
        Literal["SUCCESS", "SUCCESS_WITH_REPAIR", "SUCCESS_WITH_REVIEW", "FAILED"], ...
    ] = Field(min_length=1)
    official_success_allowed: bool
    user_confirmation_required_when_unresolved: bool
    literal_placeholder_in_article_body_allowed: bool = False


class MustDeliverPolicy(_StrictFrozenModel):
    delivery_status: Literal["SUCCESS", "SUCCESS_WITH_REPAIR", "SUCCESS_WITH_REVIEW", "FAILED"]
    article_body_required: bool
    article_qualified: bool
    official_success: bool
    user_confirmation_required: bool

    @model_validator(mode="after")
    def validate_invariants(self) -> "MustDeliverPolicy":
        if self.delivery_status == "SUCCESS_WITH_REVIEW":
            if not self.article_body_required or self.article_qualified or self.official_success:
                raise ValueError("invalid SUCCESS_WITH_REVIEW mapping")
            if not self.user_confirmation_required:
                raise ValueError("review delivery requires user confirmation")
        if self.delivery_status in {"SUCCESS", "SUCCESS_WITH_REPAIR"}:
            if not self.article_body_required or not self.article_qualified or not self.official_success:
                raise ValueError("invalid official-success mapping")
            if self.user_confirmation_required:
                raise ValueError("official success cannot require confirmation")
        if self.delivery_status == "FAILED" and self.official_success:
            raise ValueError("FAILED cannot be official success")
        return self


class FactPolicyAlignment(_StrictFrozenModel):
    version: Literal["v3-fact-policy-alignment-1"] = FACT_POLICY_ALIGNMENT_VERSION
    governance_document: Literal[
        "docs/architecture/v3_fact_policy_alignment_v1.0.md"
    ] = FACT_POLICY_DOCUMENT
    placeholder_confirmation_decision: Literal[
        "NO_LITERAL_PLACEHOLDER_IN_ARTICLE_BODY_REVIEW_METADATA_CONFIRMATION"
    ]
    fact_classes: tuple[FactClassPolicy, FactClassPolicy, FactClassPolicy]
    must_deliver: tuple[MustDeliverPolicy, MustDeliverPolicy, MustDeliverPolicy, MustDeliverPolicy]

    @model_validator(mode="after")
    def validate_unique_mapping(self) -> "FactPolicyAlignment":
        if {value.architecture_fact_class for value in self.fact_classes} != {"F0", "F1", "F2"}:
            raise ValueError("F0/F1/F2 mapping must be complete and unique")
        if {value.delivery_status for value in self.must_deliver} != {
            "SUCCESS",
            "SUCCESS_WITH_REPAIR",
            "SUCCESS_WITH_REVIEW",
            "FAILED",
        }:
            raise ValueError("Must-Deliver mapping must be complete and unique")
        f0 = next(value for value in self.fact_classes if value.architecture_fact_class == "F0")
        if f0.official_success_allowed or any(
            state in {"SUCCESS", "SUCCESS_WITH_REPAIR"}
            for state in f0.allowed_delivery_states
        ):
            raise ValueError("F0 cannot become official success")
        return self


FACT_POLICY_ALIGNMENT = FactPolicyAlignment(
    placeholder_confirmation_decision=(
        "NO_LITERAL_PLACEHOLDER_IN_ARTICLE_BODY_REVIEW_METADATA_CONFIRMATION"
    ),
    fact_classes=(
        FactClassPolicy(
            architecture_fact_class="F0",
            current_code_rules=(
                "A_HARD_BLOCK_OR_HARD_CONTRACT_FAILURE",
                "INVALID_EVIDENCE_ID_OR_SOURCE_IDENTITY",
                "SEMANTIC_STATE_ESCALATION",
                "UNSUPPORTED_MAJOR_FACT_OR_CAUSALITY",
            ),
            prompt_rules=(
                "FACT_REQUIRES_LEGAL_SAME_SECTION_EVIDENCE_ID",
                "PLAN_OR_PENDING_MUST_NOT_BECOME_COMPLETED",
                "NO_INVENTED_SUBJECT_PROJECT_RESULT_OR_CAUSALITY",
            ),
            allowed_delivery_states=("SUCCESS_WITH_REVIEW", "FAILED"),
            official_success_allowed=False,
            user_confirmation_required_when_unresolved=True,
        ),
        FactClassPolicy(
            architecture_fact_class="F1",
            current_code_rules=("B_SAFE_REPAIR", "D_REVIEW_REQUIRED"),
            prompt_rules=(
                "NO_INVENTED_SPECIFIC_VALUE",
                "USE_FACT_FREE_GENERALIZATION_OR_REVIEW_METADATA",
            ),
            allowed_delivery_states=("SUCCESS_WITH_REPAIR", "SUCCESS_WITH_REVIEW"),
            official_success_allowed=True,
            user_confirmation_required_when_unresolved=True,
        ),
        FactClassPolicy(
            architecture_fact_class="F2",
            current_code_rules=("C_PASS_WITH_EQUIVALENCE",),
            prompt_rules=(
                "ANALYSIS_EVIDENCE_ID_EMPTY",
                "GENERAL_SYNTHESIS_MUST_NOT_ADD_CONCRETE_FACTS",
            ),
            allowed_delivery_states=("SUCCESS", "SUCCESS_WITH_REPAIR"),
            official_success_allowed=True,
            user_confirmation_required_when_unresolved=False,
        ),
    ),
    must_deliver=(
        MustDeliverPolicy(
            delivery_status="SUCCESS",
            article_body_required=True,
            article_qualified=True,
            official_success=True,
            user_confirmation_required=False,
        ),
        MustDeliverPolicy(
            delivery_status="SUCCESS_WITH_REPAIR",
            article_body_required=True,
            article_qualified=True,
            official_success=True,
            user_confirmation_required=False,
        ),
        MustDeliverPolicy(
            delivery_status="SUCCESS_WITH_REVIEW",
            article_body_required=True,
            article_qualified=False,
            official_success=False,
            user_confirmation_required=True,
        ),
        MustDeliverPolicy(
            delivery_status="FAILED",
            article_body_required=False,
            article_qualified=False,
            official_success=False,
            user_confirmation_required=False,
        ),
    ),
)
