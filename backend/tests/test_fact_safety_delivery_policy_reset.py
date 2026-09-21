from __future__ import annotations

import pytest

from app.services.generation.evidence_safety import run_evidence_safety
from app.services.generation.generator import validate_final_delivery_quality


def _reports(*, fact_risks: int = 0, analysis_risks: int = 0, hard_blocks: int = 0):
    return {
        "structured": {
            "passed": True,
            "invalid_evidence_reference_count": 0,
        },
        "fact": {
            "passed": fact_risks == 0 and hard_blocks == 0,
            "unsupported_count": fact_risks,
            "out_of_plan_count": 0,
            "hard_block_count": hard_blocks,
            "evidence_contract_hard_block_count": hard_blocks,
            "delivery_blocked": hard_blocks > 0,
        },
        "analysis": {
            "passed": analysis_risks == 0,
            "analysis_fact_leak_count": analysis_risks,
        },
    }


def _validate(reports, *, flow4_risk_status="NO_RECORDED_RISK"):
    return validate_final_delivery_quality(
        final_text="正文" * 50,
        target_words=100,
        content_plan={"sections": []},
        structured_draft={"sections": []},
        validation_reports=reports,
        flow4_risk_status=flow4_risk_status,
    )


def test_no_risk_is_normal_delivery():
    result = _validate(_reports())

    assert result["status"] == "PASS"
    assert result["delivery_status"] == "SUCCESS"
    assert result["review_required"] is False


@pytest.mark.parametrize(
    ("fact_risks", "analysis_risks", "flow4_risk_status"),
    [
        (1, 0, "REVIEW_REQUIRED"),
        (4, 0, "REVIEW_REQUIRED"),
        (0, 1, "REVIEW_REQUIRED"),
    ],
)
def test_reviewable_fact_or_analysis_risks_do_not_block_complete_draft(
    fact_risks: int,
    analysis_risks: int,
    flow4_risk_status: str,
):
    result = _validate(
        _reports(fact_risks=fact_risks, analysis_risks=analysis_risks),
        flow4_risk_status=flow4_risk_status,
    )

    assert result["status"] == "REVIEW_REQUIRED_DRAFT"
    assert result["delivery_status"] == "SUCCESS_WITH_REVIEW"
    assert result["review_required"] is True


def test_true_hard_block_remains_unsafe_but_complete_body_is_deliverable():
    result = _validate(_reports(fact_risks=1, hard_blocks=1))

    assert result["status"] == "REVIEW_REQUIRED_DRAFT"
    assert result["delivery_status"] == "SUCCESS_WITH_REVIEW"
    assert result["review_required"] is True


def test_invalid_or_unknown_evidence_reference_remains_blocked():
    reports = _reports()
    reports["structured"].update(
        passed=False,
        invalid_evidence_reference_count=1,
    )

    with pytest.raises(ValueError, match="FAIL_STRUCTURED_OUTPUT"):
        _validate(reports, flow4_risk_status="REVIEW_REQUIRED")


def test_frozen_plan_integrity_failure_stops_before_draft_delivery():
    def fail_frozen_integrity() -> None:
        raise ValueError("FrozenArticlePlan integrity mismatch")

    with pytest.raises(ValueError, match="FrozenArticlePlan integrity mismatch"):
        run_evidence_safety(
            {"sections": []},
            fact_validator=lambda *_args: {"passed": True},
            full_revalidator=lambda _draft: {"passed": True},
            frozen_plan_integrity_check=fail_frozen_integrity,
        )
