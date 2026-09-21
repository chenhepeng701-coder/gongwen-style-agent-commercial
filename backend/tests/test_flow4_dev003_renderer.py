from __future__ import annotations

import ast
import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.models.flow4 import Flow4Input
from app.services.flow4.handoff import build_flow4_input, create_flow3_output_envelope
from app.services.flow4.integrity_gate import assert_flow4_boundary_isolated
from app.services.flow4.renderer import render_flow4_input


def _flow4_input(*, include_title: bool = True) -> Flow4Input:
    envelope = create_flow3_output_envelope(
        parsed_structured_draft={
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
                },
                {
                    "section_id": "SEC-02",
                    "heading": "二、健全协同工作机制",
                    "sentences": [
                        {
                            "sentence_type": "FACT",
                            "evidence_id": "EV-002",
                            "text": "县委召开多部门联席会议。",
                        }
                    ],
                },
            ],
        },
        content_plan={
            "schema_version": "flow3-v3.3-content-plan-1",
            "plan_id": "PLAN-DEV003",
            "sections": [
                {"section_id": "SEC-01"},
                {"section_id": "SEC-02"},
            ],
            "target_total": 800,
        },
        fact_anchor_pack={
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
                    "forbidden_detail_types": [],
                },
                {
                    "evidence_id": "EV-002",
                    "section_id": "SEC-02",
                    "role": "supporting",
                    "canonical_subject": "县委",
                    "canonical_action": "召开",
                    "canonical_object": "多部门联席会议",
                    "exact_values": {},
                    "forbidden_detail_types": [],
                },
            ],
            "analysis_support_pack": [],
            "source_span_dedup_report": {
                "passed": True,
                "source_span_cross_section_conflict_count": 0,
            },
        },
        validation_reports={
            "structured": {
                "passed": True,
                "invalid_evidence_reference_count": 0,
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
        },
        task={
            "title": "大型活动安保经验材料",
            "document_type": "经验交流材料",
            "target_words": 800,
            "export_format": "txt",
        },
    )
    return build_flow4_input(
        envelope,
        run_id="FLOW3-RUN-DEV003",
        flow3_status="FAIL_ANALYSIS_FACT_LEAK",
        handoff_id="HANDOFF-DEV003",
        created_at=datetime(2026, 7, 27, 8, 0, tzinfo=timezone.utc),
        render_options={"include_title_in_plain_text": include_title},
    )


def test_normal_render_uses_frozen_plain_text_layout():
    result = render_flow4_input(_flow4_input())

    assert result.final_text == (
        "大型活动安保经验材料\n\n"
        "一、强化风险前置研判\n"
        "县委专题听取安保工作汇报。\n"
        "风险前置研判有助于增强工作主动性。\n\n"
        "二、健全协同工作机制\n"
        "县委召开多部门联席会议。"
    )


def test_sentence_text_and_section_sentence_order_are_preserved_exactly():
    source = _flow4_input(include_title=False)
    result = render_flow4_input(source)
    expected_sentences = [
        sentence.text
        for section in source.parsed_structured_draft.sections
        for sentence in section.sentences
    ]
    rendered_lines = result.final_text.splitlines()

    assert rendered_lines == [
        "一、强化风险前置研判",
        *expected_sentences[:2],
        "",
        "二、健全协同工作机制",
        expected_sentences[2],
    ]
    assert [item.section_id for item in result.render_trace] == [
        "SEC-01",
        "SEC-01",
        "SEC-02",
    ]
    assert [item.sentence_index for item in result.render_trace] == [0, 1, 0]


def test_render_trace_preserves_type_evidence_and_exact_text_digest():
    source = _flow4_input()
    result = render_flow4_input(source)
    source_sentences = [
        sentence
        for section in source.parsed_structured_draft.sections
        for sentence in section.sentences
    ]

    assert [
        (item.sentence_type, item.evidence_ids)
        for item in result.render_trace
    ] == [
        ("FACT", ("EV-001",)),
        ("ANALYSIS", ()),
        ("FACT", ("EV-002",)),
    ]
    assert [item.rendered_text_sha256 for item in result.render_trace] == [
        hashlib.sha256(sentence.text.encode("utf-8")).hexdigest()
        for sentence in source_sentences
    ]


def test_review_required_and_authoritative_reports_are_preserved():
    source = _flow4_input()
    result = render_flow4_input(source)

    assert result.validation_summary.fact == source.validation_reports.fact
    assert result.validation_summary.analysis == source.validation_reports.analysis
    assert result.validation_summary.analysis["analysis_fact_leak_count"] == 2
    assert result.validation_summary.analysis["passed"] is False
    assert result.validation_summary.flow3_status == "FAIL_ANALYSIS_FACT_LEAK"
    assert result.validation_summary.risk_status == "REVIEW_REQUIRED"


def test_renderer_rejects_non_flow4_input_without_coercion():
    with pytest.raises(TypeError, match="gate-approved Flow4Input"):
        render_flow4_input(_flow4_input().model_dump(mode="python"))  # type: ignore[arg-type]


def test_renderer_rejects_core_payload_changed_after_integrity_gate():
    source = _flow4_input()
    source.parsed_structured_draft.sections[0].sentences[0].text = "被篡改的句子。"

    with pytest.raises(ValidationError, match="payload_sha256 does not match"):
        render_flow4_input(source)


def test_renderer_has_no_generation_provider_rag_or_rewrite_boundary():
    renderer_path = Path(__file__).parents[1] / "app" / "services" / "flow4" / "renderer.py"
    assert_flow4_boundary_isolated([renderer_path])

    tree = ast.parse(renderer_path.read_text(encoding="utf-8"))
    imported_modules = {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert all(not module.startswith("app.services.generation") for module in imported_modules)
    assert all(not module.startswith("app.services.library") for module in imported_modules)
