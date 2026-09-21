from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.models.flow4 import Flow4Input, canonical_payload_sha256
from app.services.flow4.handoff import build_flow4_input, create_flow3_output_envelope
from app.services.flow4.integrity_gate import (
    HandoffIntegrityError,
    assert_flow4_boundary_isolated,
    run_handoff_integrity_gate,
)


def _draft() -> dict:
    return {
        "title": "大型活动安保经验材料",
        "sections": [
            {
                "section_id": "SEC-01",
                "heading": "一、强化风险前置研判",
                "sentences": [
                    {
                        "sentence_type": "FACT",
                        "evidence_id": "EV-001",
                        "text": "县委专题听取安保工作汇报。",
                    },
                    {
                        "sentence_type": "ANALYSIS",
                        "evidence_id": "",
                        "text": "风险前置研判有助于增强工作主动性。",
                    },
                ],
            }
        ],
    }


def _content_plan() -> dict:
    return {
        "schema_version": "flow3-v3.3-content-plan-1",
        "plan_id": "PLAN-001",
        "sections": [
            {
                "section_id": "SEC-01",
                "section_goal": "说明风险研判",
                "selected_evidence_ids": ["EV-001"],
            }
        ],
        "target_total": 800,
    }


def _anchor_pack() -> dict:
    return {
        "schema_version": "flow3-v3.3-fact-anchor-1",
        "fact_anchors": [
            {
                "evidence_id": "EV-001",
                "section_id": "SEC-01",
                "role": "core",
                "canonical_subject": "县委",
                "canonical_action": "专题听取",
                "canonical_object": "安保工作汇报",
                "exact_values": {},
                "forbidden_detail_types": ["time", "quantity"],
            }
        ],
        "analysis_support_pack": [],
        "source_span_dedup_report": {
            "passed": True,
            "source_span_cross_section_conflict_count": 0,
        },
    }


def _reports() -> dict:
    return {
        "structured": {
            "passed": True,
            "invalid_evidence_reference_count": 0,
            "hard_issues": [],
        },
        "fact": {
            "passed": True,
            "unsupported_count": 0,
            "out_of_plan_count": 0,
            "claims": [{"evidence_id": "EV-001", "passed": True}],
        },
        "analysis": {
            "passed": False,
            "analysis_fact_leak_count": 2,
            "leaks": [{"sentence_index": 0}, {"sentence_index": 1}],
            "checker_modified_response": False,
        },
    }


def _task() -> dict:
    return {
        "title": "大型活动安保经验材料",
        "document_type": "经验交流材料",
        "target_words": 800,
        "export_format": "docx",
    }


def _envelope():
    return create_flow3_output_envelope(
        parsed_structured_draft=_draft(),
        content_plan=_content_plan(),
        fact_anchor_pack=_anchor_pack(),
        validation_reports=_reports(),
        task=_task(),
    )


def _flow4_input():
    return build_flow4_input(
        _envelope(),
        run_id="FLOW3-RUN-DEV002",
        flow3_status="FAIL_ANALYSIS_FACT_LEAK",
        handoff_id="HANDOFF-DEV002",
        created_at=datetime(2026, 7, 27, 8, 0, tzinfo=timezone.utc),
    )


def _rehash_core_payload(payload: dict) -> None:
    core_fields = (
        "parsed_structured_draft",
        "content_plan",
        "fact_anchor_pack",
        "validation_reports",
        "task",
    )
    payload["source"]["payload_sha256"] = canonical_payload_sha256(
        {field: payload[field] for field in core_fields}
    )


def test_normal_handoff_passes_with_review_required_and_sentence_trace():
    envelope = _envelope()
    flow4_input = _flow4_input()

    report = run_handoff_integrity_gate(envelope, flow4_input)

    assert report.passed is True
    assert report.risk_status == "REVIEW_REQUIRED"
    assert report.flow3_status == "FAIL_ANALYSIS_FACT_LEAK"
    assert report.sentence_count == 2
    assert report.fact_trace_mapped_count == 1
    assert [
        (
            item.section_id,
            item.sentence_index,
            item.sentence_type,
            item.evidence_ids,
            item.anchor_roles,
            item.trace_status,
        )
        for item in report.evidence_trace
    ] == [
        ("SEC-01", 0, "FACT", ("EV-001",), ("core",), "MAPPED"),
        ("SEC-01", 1, "ANALYSIS", (), (), "ANALYSIS_UNANCHORED"),
    ]


def test_missing_required_core_field_is_rejected_without_defaulting():
    payload = _envelope().model_dump(mode="python")
    del payload["task"]

    with pytest.raises(ValidationError, match="task"):
        build_flow4_input(
            payload,
            run_id="FLOW3-RUN-MISSING",
            flow3_status="FAIL_ANALYSIS_FACT_LEAK",
        )


def test_missing_fact_evidence_id_is_rejected_without_inference():
    payload = _envelope().model_dump(mode="python")
    payload["parsed_structured_draft"]["sections"][0]["sentences"][0]["evidence_ids"] = []

    with pytest.raises(ValidationError, match="FACT sentence evidence_ids"):
        build_flow4_input(
            payload,
            run_id="FLOW3-RUN-NO-EVIDENCE",
            flow3_status="FAIL_ANALYSIS_FACT_LEAK",
        )


def test_missing_sentence_type_is_rejected_without_repair():
    payload = _envelope().model_dump(mode="python")
    del payload["parsed_structured_draft"]["sections"][0]["sentences"][0]["sentence_type"]

    with pytest.raises(ValidationError, match="sentence_type"):
        build_flow4_input(
            payload,
            run_id="FLOW3-RUN-NO-TYPE",
            flow3_status="FAIL_ANALYSIS_FACT_LEAK",
        )


def test_analysis_warning_loss_is_rejected_even_with_recomputed_digest():
    envelope = _envelope()
    payload = deepcopy(_flow4_input().model_dump(mode="python"))
    del payload["validation_reports"]["analysis"]["leaks"]
    _rehash_core_payload(payload)
    polluted = Flow4Input.model_validate(payload)

    with pytest.raises(HandoffIntegrityError, match="VALIDATION_STATE_NOT_PRESERVED"):
        run_handoff_integrity_gate(envelope, polluted)


def test_analysis_warning_cannot_be_converted_to_pass():
    envelope = _envelope()
    payload = deepcopy(_flow4_input().model_dump(mode="python"))
    payload["validation_reports"]["analysis"]["passed"] = True
    payload["source"]["flow3_status"] = "PASS_VALIDATION"
    _rehash_core_payload(payload)
    polluted = Flow4Input.model_validate(payload)

    with pytest.raises(HandoffIntegrityError) as exc_info:
        run_handoff_integrity_gate(envelope, polluted)

    codes = {issue.code for issue in exc_info.value.issues}
    assert "VALIDATION_STATE_NOT_PRESERVED" in codes
    assert "VALIDATION_RISK_DOWNGRADED" in codes
    assert "FLOW3_STATUS_DOWNGRADED" in codes


def test_sentence_order_change_is_rejected_even_with_recomputed_digest():
    envelope = _envelope()
    payload = deepcopy(_flow4_input().model_dump(mode="python"))
    payload["parsed_structured_draft"]["sections"][0]["sentences"].reverse()
    _rehash_core_payload(payload)
    reordered = Flow4Input.model_validate(payload)

    with pytest.raises(HandoffIntegrityError, match="STRUCTURE_NOT_PRESERVED"):
        run_handoff_integrity_gate(envelope, reordered)


def test_evidence_id_reassignment_is_rejected_even_when_internally_consistent():
    envelope = _envelope()
    payload = deepcopy(_flow4_input().model_dump(mode="python"))
    payload["parsed_structured_draft"]["sections"][0]["sentences"][0]["evidence_ids"] = ["EV-NEW"]
    payload["fact_anchor_pack"]["fact_anchors"][0]["evidence_id"] = "EV-NEW"
    _rehash_core_payload(payload)
    reassigned = Flow4Input.model_validate(payload)

    with pytest.raises(HandoffIntegrityError) as exc_info:
        run_handoff_integrity_gate(envelope, reassigned)

    codes = {issue.code for issue in exc_info.value.issues}
    assert "STRUCTURE_NOT_PRESERVED" in codes
    assert "EVIDENCE_CHAIN_NOT_PRESERVED" in codes


@pytest.mark.parametrize(
    "body",
    [
        "return generator._run_hybrid_fact_safe_gate('flattened text')",
        "return fallback_repair('flattened text')",
        "return provider.generate('regenerate')",
        "return rag_reference_split('raw rag context')",
    ],
)
def test_legacy_chain_call_in_flow4_boundary_is_rejected(tmp_path, body):
    polluted_module = tmp_path / "polluted_flow4_entry.py"
    polluted_module.write_text(
        "def enter(generator):\n"
        f"    {body}\n",
        encoding="utf-8",
    )

    with pytest.raises(HandoffIntegrityError, match="LEGACY_BOUNDARY_CALL"):
        assert_flow4_boundary_isolated([polluted_module])


@pytest.mark.parametrize(
    "extensions",
    [
        {"provider": "openai-compatible"},
        {"fallback_repair": True},
        {"raw_rag_context": ["重新注入"]},
    ],
)
def test_legacy_controls_cannot_be_smuggled_through_extensions(extensions):
    with pytest.raises(HandoffIntegrityError, match="BOUNDARY_PAYLOAD_POLLUTION"):
        build_flow4_input(
            _envelope(),
            run_id="FLOW3-RUN-POLLUTED",
            flow3_status="FAIL_ANALYSIS_FACT_LEAK",
            extensions=extensions,
        )
