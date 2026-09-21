"""Experimental service route, isolated from Legacy and all model repair paths.

This route consumes prepared M2 inputs. It never invokes preflight/RAG, and does
not implement Commercial Quality Repair or choose a public API delivery state.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from app.models.flow4 import preserve_paragraph_identity, render_section_lines
from app.services.flow4.handoff import build_flow4_input, create_flow3_output_envelope
from app.services.flow4.renderer import render_flow4_input
from app.services.generation.dynamic_budget import record_actual_usage
from app.services.generation.delivery_decision import (
    CandidateRole, IssueRoute, LastKnownGoodArticle, route_issue,
)
from app.services.generation.flow3_v3_trace import (
    apply_fact_safety_gate, parse_and_validate_structured_draft, validate_analysis_sentences,
)
from app.services.generation.paragraph_compatibility import ParagraphCompatibilityAdapter
from app.services.generation.section_writer import (
    SECTION_WRITER_EXPERIMENTAL, SectionWriter, SectionWriterContractError,
    WriterContextLimits, build_writer_input, build_batch_plan,
    evaluate_writer_completion, measure_writer_usage,
)
from app.utils.storage import atomic_write_text


def _json_file(root: Path, name: str, value: Any) -> str:
    path = root / name
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path.resolve())


def _complete_candidate_text(draft: Any) -> str:
    """Render all candidate title/heading/paragraph text without quality edits."""
    payload = draft.model_dump(mode="json") if hasattr(draft, "model_dump") else dict(draft)
    values = [str(payload.get("title") or "").strip()]
    for section in payload.get("sections") or []:
        values.append(str(section.get("heading") or "").strip())
        paragraphs = section.get("paragraphs")
        if isinstance(paragraphs, list):
            sentences = [sentence for paragraph in paragraphs for sentence in paragraph.get("sentences") or []]
        else:
            sentences = section.get("sentences") or []
        values.extend(str(sentence.get("text") or "").strip() for sentence in sentences)
    return "\n".join(value for value in values if value)


def generate_section_writer_experimental(
    *, prepared: dict[str, Any], provider: Any, artifact_root: Path,
    temperature: float = 0.7, external_authorized: bool = False,
    local_fixture: bool = False, limits: WriterContextLimits | None = None,
    save_raw_responses: bool = False,
) -> dict[str, Any]:
    started = time.perf_counter()
    root = Path(artifact_root)
    required = ("article_outline", "dynamic_budget", "frozen_article_plan", "evidence_group_pack")
    if any(prepared.get(k) is None for k in required):
        raise SectionWriterContractError("FROZEN_SECTION_WRITER_INPUT_REQUIRED")
    inputs = build_writer_input(prepared["article_outline"], prepared["dynamic_budget"],
                                prepared["frozen_article_plan"], prepared["evidence_group_pack"],
                                prepared.get("fact_anchor_pack"), task=prepared.get("task"))
    if not external_authorized and not local_fixture:
        raise SectionWriterContractError("M3_SECTION_WRITER_REAL_RUN_AUTHORIZATION_REQUIRED")
    batch_plan = build_batch_plan(inputs, limits)
    root.mkdir(parents=True, exist_ok=True)
    paths = {}
    candidates = LastKnownGoodArticle()
    candidate_lock = threading.Lock()

    def save_candidate(candidate: Any, label: str) -> None:
        paths[f"candidate_{label}"] = _json_file(
            root,
            f"candidate_{label}.json",
            asdict(candidate),
        )
        paths["candidate_lineage"] = _json_file(
            root,
            "section_writer_candidate_versions.json",
            candidates.lineage_summary(),
        )

    def recoverable_handoff(exc: Exception) -> dict[str, Any] | None:
        if not candidates.versions:
            return None
        selected = candidates.candidate or candidates.versions[0]
        issue_type = (
            exc.code if isinstance(exc, SectionWriterContractError)
            else type(exc).__name__
        )
        veto = issue_type in {
            "CROSS_PARAGRAPH_EVIDENCE", "UNKNOWN_EVIDENCE_ALIAS",
            "WRITER_GLOBAL_EVIDENCE_ID_FORBIDDEN", "FACT_EVIDENCE_REQUIRED",
        }
        route = route_issue(
            issue_type,
            candidate_exists=True,
            veto_fact=veto,
        )
        return {
            "writer_mode": SECTION_WRITER_EXPERIMENTAL,
            "flow3_v33_status": "U2_RECOVERABLE_CANDIDATE",
            "issue_route": route.value,
            "next_owner": (
                "M4" if route == IssueRoute.QUALITY_REPAIR_REQUIRED else "U2_RECHECK"
            ),
            "candidate_versions": candidates.lineage_summary(),
            "candidate": asdict(selected),
            "structured_draft": selected.structured_draft,
            "rendered_draft": selected.content,
            "model_call_count": len(call_records),
            "commercial_quality_verdict": "NOT_EVALUATED_M4_OWNER",
            "paths": paths,
        }
    paths["writer_batch_plan"] = _json_file(root, "writer_batch_plan.json", batch_plan.summary())
    paths["prompt_summary"] = _json_file(root, "section_writer_prompt_summary.json", {
        "batches": [b.summary() for b in batch_plan.batches],
        "prompt_full_text_saved": False, "temperature": temperature,
    })
    paths["normalization_trace"] = _json_file(root, "writer_input_normalization_trace.json",
                                               json.loads(inputs.normalization_trace_json))
    call_records = {}
    def record_call(batch, raw, metrics):
        call_records[batch.batch_id] = metrics
        _json_file(root, f"writer_call_{batch.batch_id}.json", metrics)
        with candidate_lock:
            candidate = candidates.preserve_raw(
                raw,
                stage=f"SECTION_WRITER_{batch.batch_id}_RAW",
            )
            if candidate is not None:
                save_candidate(candidate, batch.batch_id.lower())
        if save_raw_responses:
            atomic_write_text(root / f"section_writer_raw_response_{batch.batch_id}.txt", raw, encoding="utf-8")
    try:
        execution = SectionWriter(limits).write(inputs, provider=provider, temperature=temperature,
                                                 external_authorized=external_authorized, local_fixture=local_fixture,
                                                 on_call_completed=record_call)
    except Exception as exc:
        _json_file(root, "performance_report.json", {
            "status": "WRITER_FAILED", "error_type": type(exc).__name__,
            "contract_code": exc.code if isinstance(exc, SectionWriterContractError) else None,
            "calls": [call_records[k] for k in sorted(call_records)],
            "batch_count": len(call_records), "total_m3_path_ms": (time.perf_counter()-started)*1000,
            "retry_count": 0, "structured_model_repair_calls": 0, "quality_rewrite_calls": 0,
        })
        handoff = recoverable_handoff(exc)
        if handoff is not None:
            handoff["performance"] = {
                "status": "WRITER_FAILED_WITH_RECOVERABLE_CANDIDATE",
                "batch_count": len(call_records),
                "external_model_calls_added_by_u2": 0,
            }
            return handoff
        raise
    paths["section_writer_draft"] = _json_file(root, "section_writer_draft.json", execution.draft.model_dump(mode="json"))
    parsed_candidate_text = _complete_candidate_text(execution.draft)
    parent_id = candidates.versions[-1].candidate_id if candidates.versions else ""
    candidates.update(
        title=str(execution.draft.title or ""),
        content=parsed_candidate_text,
        structured_draft=execution.draft.model_dump(mode="json"),
        stage="SECTION_WRITER_PARSED_DRAFT",
        safety_boundary="WRITER_UNVERIFIED",
        role=CandidateRole.PARSED.value,
        parent_candidate_id=parent_id,
    )
    parsed_candidate = candidates.versions[-1]
    save_candidate(parsed_candidate, "parsed")
    measured = measure_writer_usage(execution.draft, inputs)
    completion = evaluate_writer_completion(execution.draft, inputs)
    paths["writer_completion"] = _json_file(
        root, "section_writer_completion_report.json", completion.model_dump(mode="json"),
    )
    completed_intents = list(completion.completed_paragraph_intent_ids)
    writer_node_usage = {n["outline_node_id"]: n["actual_words"] for n in measured["node_usage"]}
    writer_usage = record_actual_usage(inputs.budget, actual_words_by_node=writer_node_usage,
                                       completed_paragraph_intent_ids=completed_intents)
    paths["writer_budget_usage"] = _json_file(root, "writer_dynamic_budget_usage.json", {
        **writer_usage.model_dump(mode="json"), "paragraph_usage": measured["paragraph_usage"],
        "node_budget_breakdown": measured["node_usage"],
        "count_basis": measured["count_basis"], "budget_unit": measured["budget_unit"],
    })
    if completion.status == "FAIL":
        performance = {
            "status": "WRITER_GOAL_INCOMPLETE_WITH_RECOVERABLE_CANDIDATE",
            "batch_count": len(execution.calls), "calls": execution.calls,
            "writer_budget_status": writer_usage.usage_status,
            "writer_actual_words": writer_usage.total_actual_words,
            "total_writer_call_latency_ms": sum(c["latency_ms"] for c in execution.calls),
            "writer_wall_ms": execution.writer_wall_ms,
            "total_m3_path_ms": (time.perf_counter() - started) * 1000,
            "structured_model_repair_calls": 0, "quality_rewrite_calls": 0,
            "independent_title_calls": 0, "retry_count": 0,
            "local_fixture": local_fixture,
        }
        paths["performance"] = _json_file(root, "performance_report.json", performance)
        exc = SectionWriterContractError(
            "FALSE_PARAGRAPH_COMPLETION", report=completion.model_dump(mode="json"),
        )
        recoverable = recoverable_handoff(exc)
        if recoverable is None:
            raise exc
        recoverable.update({
            "flow3_v33_status": "M3_FALSE_PARAGRAPH_COMPLETION",
            "issue_route": IssueRoute.INTERNAL_REPAIR_REQUIRED.value,
            "next_owner": "M3_SECTION_WRITER",
            "commercial_quality_verdict": "M3_FALSE_PARAGRAPH_COMPLETION",
            "candidate": asdict(parsed_candidate),
            "structured_draft": parsed_candidate.structured_draft,
            "rendered_draft": parsed_candidate.content,
            "completion_report": completion.model_dump(mode="json"),
            "dynamic_budget_usage": writer_usage,
            "performance": performance,
        })
        return recoverable
    structured, compatibility = ParagraphCompatibilityAdapter().convert(execution.draft, inputs)
    compatibility["paragraph_completion"] = completion.model_dump(mode="json")
    parsed, report = parse_and_validate_structured_draft(
        json.dumps(structured, ensure_ascii=False), inputs.content_plan, inputs.fact_anchor_pack,
    )
    if not report["passed"] or parsed is None:
        _json_file(root, "paragraph_compatibility_report.json", {**compatibility, "flow3_report": report})
        exc = SectionWriterContractError("FLOW3_STRUCTURED_HANDOFF_FAILURE", report=report)
        handoff = recoverable_handoff(exc)
        if handoff is not None:
            candidates.bind_report(parsed_candidate.candidate_id, "STRUCTURE", report)
            return handoff
        raise exc
    # Existing Fact Safety retains authority. No quality/structured model repair.
    safe_draft, fact_report, safe_repair = apply_fact_safety_gate(
        parsed, inputs.fact_anchor_pack, prepared.get("fact_source_support") or {},
        content_plan=inputs.content_plan, frozen_plan_sha256=inputs.outline.source_plan_sha,
    )
    isolation_trace = (
        safe_repair.get("isolations") or ()
        if safe_repair.get("status") == "PASS_WITH_LOCAL_ISOLATION"
        else ()
    )
    preserve_paragraph_identity(
        parsed["sections"],
        safe_draft["sections"],
        fact_safety_isolation_trace=isolation_trace,
    )
    analysis_report = validate_analysis_sentences(safe_draft)
    safe_draft, report = parse_and_validate_structured_draft(
        json.dumps(safe_draft, ensure_ascii=False), inputs.content_plan, inputs.fact_anchor_pack,
    )
    if safe_draft is None or not report["passed"]:
        exc = SectionWriterContractError(
            "POST_SAFETY_PARAGRAPH_CONTRACT_FAILURE", report=report,
        )
        handoff = recoverable_handoff(exc)
        if handoff is not None:
            candidates.bind_report(parsed_candidate.candidate_id, "POST_SAFETY_STRUCTURE", report)
            return handoff
        raise exc
    validation_reports = {"structured": report, "fact": fact_report, "analysis": analysis_report}
    candidates.update(
        title=str(safe_draft.get("title") or execution.draft.title or ""),
        content=_complete_candidate_text(safe_draft),
        structured_draft=safe_draft,
        stage="SECTION_WRITER_FACT_RECHECK_DRAFT",
        safety_boundary=("FACT_SAFE" if fact_report["passed"] and analysis_report["passed"] else "REVIEW_REQUIRED"),
        role=(CandidateRole.REPAIRED.value if safe_repair.get("safe_repair_count") else CandidateRole.PARSED.value),
        parent_candidate_id=parsed_candidate.candidate_id,
    )
    safety_candidate = candidates.versions[-1]
    candidates.bind_report(safety_candidate.candidate_id, "STRUCTURE", report)
    candidates.bind_report(safety_candidate.candidate_id, "FACT_SAFETY", fact_report)
    candidates.bind_report(safety_candidate.candidate_id, "ANALYSIS", analysis_report)
    save_candidate(safety_candidate, "safety")
    flow3_status = "PASS" if fact_report["passed"] and analysis_report["passed"] else "REVIEW_REQUIRED_DRAFT"
    # Model quality remains unvalidated in M3; this is an internal safety status.
    flow4_started = time.perf_counter()
    envelope = create_flow3_output_envelope(
        parsed_structured_draft=safe_draft, content_plan=inputs.content_plan,
        fact_anchor_pack=inputs.fact_anchor_pack, validation_reports=validation_reports,
        task={"title": safe_draft["title"], "document_type": inputs.outline.document_type,
              "target_words": inputs.budget.total_target_words, "export_format": "txt"},
    )
    handoff = build_flow4_input(envelope, run_id=root.name, flow3_status=flow3_status,
                               render_options={"paragraph_mode": "paragraph_aware"})
    render = render_flow4_input(handoff)
    flow4_ms = (time.perf_counter() - flow4_started) * 1000
    expected = [(p["section_id"], p["paragraph_id"], p["paragraph_order"])
                for p in compatibility["paragraphs"]]
    actual = [(s.section_id, p.paragraph_id, p.paragraph_order) for s in render.sections for p in s.paragraphs]
    if expected != actual:
        exc = SectionWriterContractError("FLOW4_PARAGRAPH_ROUND_TRIP_LOST")
        recoverable = recoverable_handoff(exc)
        if recoverable is not None:
            return recoverable
        raise exc
    compatibility["flow4_round_trip"] = "ROUND_TRIP_PRESERVED"
    # Count actual final characters, including headings/title/separators, exactly
    # as existing delivery length checks do. Attribute global overhead to node 1.
    actual_by_node = {n["outline_node_id"]: len("\n".join(render_section_lines(s)))
                      for n, s in zip(inputs.nodes, render.sections)}
    overhead = len(render.final_text) - sum(actual_by_node.values())
    actual_by_node[inputs.nodes[0]["outline_node_id"]] += overhead
    usage = record_actual_usage(inputs.budget, actual_words_by_node=actual_by_node,
                                completed_paragraph_intent_ids=completed_intents)
    paragraph_usage = [{"paragraph_id": p.paragraph_id, "actual_words": len(p.text)}
                       for s in render.sections for p in s.paragraphs]
    paths["budget_usage"] = _json_file(root, "dynamic_budget_usage.json", {
        **usage.model_dump(mode="json"), "paragraph_usage": paragraph_usage,
        "count_basis": "final_text_characters_including_headings_title_and_separators",
        "global_overhead_charged_to_first_node": overhead,
    })
    paths["compatibility"] = _json_file(root, "paragraph_compatibility_report.json", compatibility)
    paths["flow4_result"] = _json_file(root, "flow4_v3_result.json", render.model_dump(mode="json"))
    paths["flow4_input"] = _json_file(root, "flow4_v3_input.json", handoff.model_dump(mode="json"))
    paths["safety"] = _json_file(root, "fact_safety_result.json", {"reports": validation_reports, "local_repair": safe_repair})
    article_path = root / "readable_article.txt"
    atomic_write_text(article_path, render.final_text, encoding="utf-8")
    paths["article"] = str(article_path.resolve())
    report_bindings = {}
    for report_type in ("STRUCTURE", "FACT_SAFETY", "ANALYSIS"):
        binding = candidates.report_for(safety_candidate, report_type)
        if binding is None:
            raise SectionWriterContractError(
                "CANDIDATE_REPORT_BINDING_MISSING", location=report_type,
            )
        report_bindings[report_type] = {
            "candidate_id": binding.candidate_id,
            "candidate_version": binding.candidate_version,
            "candidate_digest": binding.candidate_digest,
            "report_digest": binding.report_digest,
        }
    candidate_checkpoint = {
        "status": "M3_CANDIDATE_READY_FOR_M4",
        "candidate_id": safety_candidate.candidate_id,
        "candidate_version": safety_candidate.version,
        "candidate_digest": safety_candidate.digest,
        "candidate_stage": safety_candidate.stage,
        "safety_boundary": safety_candidate.safety_boundary,
        "complete_body": bool(safety_candidate.content.strip()),
        "paragraph_count": len(compatibility["paragraphs"]),
        "paragraph_identity_order": [
            {
                "section_id": item["section_id"],
                "paragraph_id": item["paragraph_id"],
                "paragraph_order": item["paragraph_order"],
            }
            for item in compatibility["paragraphs"]
        ],
        "evidence_trace_path": paths["compatibility"],
        "report_bindings": report_bindings,
        "flow4_contract_version": handoff.contract_version,
        "flow4_handoff_id": handoff.handoff_id,
        "flow4_round_trip": compatibility["flow4_round_trip"],
        "paragraph_completion_status": completion.status,
        "delivery_decision": "PENDING_M4",
        "commercial_quality_verdict": "NOT_EVALUATED_M4_OWNER",
        "next_owner": "M4",
    }
    paths["candidate_checkpoint"] = _json_file(
        root, "m3_candidate_checkpoint.json", candidate_checkpoint,
    )
    performance = {
        "batch_count": len(execution.calls), "calls": execution.calls,
        "writer_budget_status": writer_usage.usage_status,
        "writer_actual_words": writer_usage.total_actual_words,
        "delivery_budget_status": usage.usage_status,
        "delivery_actual_words": usage.total_actual_words,
        "budget_unit": measured["budget_unit"],
        "total_writer_call_latency_ms": sum(c["latency_ms"] for c in execution.calls),
        "writer_wall_ms": execution.writer_wall_ms, "flow4_local_ms": flow4_ms,
        "total_m3_path_ms": (time.perf_counter() - started) * 1000,
        "structured_model_repair_calls": 0, "quality_rewrite_calls": 0,
        "independent_title_calls": 0, "retry_count": 0,
        "local_fixture": local_fixture,
        "calibration_target_ms": 70000,
        "performance_architecture_verdict": "NOT_REAL_VALIDATED" if local_fixture else "PENDING_CROSS_CASE_REVIEW",
    }
    paths["performance"] = _json_file(root, "performance_report.json", performance)
    if usage.conflict_reasons or writer_usage.conflict_reasons:
        # Persist the intact draft for review; never truncate to meet the limit.
        exc = SectionWriterContractError("BUDGET_CONTRACT_CONFLICT", report={
            "usage": usage.model_dump(mode="json"), "writer_usage": writer_usage.model_dump(mode="json"), "paths": paths})
        recoverable = recoverable_handoff(exc)
        if recoverable is not None:
            recoverable["dynamic_budget_usage"] = usage
            recoverable["candidate_checkpoint"] = candidate_checkpoint
            recoverable["flow4_input"] = handoff
            recoverable["issue_route"] = IssueRoute.QUALITY_REPAIR_REQUIRED.value
            recoverable["next_owner"] = "M4"
            return recoverable
        raise exc
    return {"writer_mode": SECTION_WRITER_EXPERIMENTAL, "flow3_v33_status": flow3_status,
            "model_call_count": len(execution.calls), "structured_draft": safe_draft,
            "structured_report": report, "validation_reports": validation_reports,
            "rendered_draft": render.final_text, "flow4_result": render, "flow4_input": handoff,
            "dynamic_budget_usage": usage, "paragraph_compatibility_report": compatibility,
            "performance": performance, "paths": paths,
            "candidate_versions": candidates.lineage_summary(),
            "candidate": asdict(safety_candidate),
            "candidate_checkpoint": candidate_checkpoint, "next_owner": "M4",
            "completion_report": completion.model_dump(mode="json"),
            "semantic_coverage_status": "NOT_EVALUATED_M4_OWNER",
            "commercial_quality_verdict": "NOT_EVALUATED_M4_OWNER"}
