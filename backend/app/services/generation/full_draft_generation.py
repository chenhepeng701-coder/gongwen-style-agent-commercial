"""Registered whole-article route using shared Evidence, Provider and delivery.

No Planner/SectionWriter/fallback and no public client-supplied registry. The
normal entry obtains current RAG inputs. Frozen packets are service-level test
inputs only, never a second public API or material library.
"""
import json
import logging
from collections import Counter
from collections.abc import Mapping
from contextvars import ContextVar
from contextlib import nullcontext
from pathlib import Path
import time
import uuid

from app.models.full_article import FullArticleContractError, canonical_json, text_sha
from app.models.generation import ArticleGenBatchResult, ArticleGenRequest
from app.services.generation.task_assembly import assemble_task_from_assets
from app.services.library.material_ingestion import read_selected_material_assets
from app.services.generation.writing_reference_pack import load_writing_reference_pack, use_writing_reference_pack
from app.services.generation.official_document_drafting import build_full_draft_prompt
from app.services.generation.full_article_candidate_adapter import import_full_article, propose_exact_citations
from app.services.generation.flow3_v3_trace import evaluate_full_article_facts
from app.services.generation.full_article_quality import evaluate_full_article_quality
from app.services.generation.commercial_genre_runtime import CommercialGenreRuntime
from app.services.generation.delivery_decision import full_article_review_result, full_article_unchecked_result
from app.services.flow4.handoff import build_full_article_handoff
from app.services.flow4.renderer import render_full_article
from app.services.flow4.artifact_delivery import deliver_full_article_artifacts
from app.services.llm.factory import get_llm_provider
from app.services.llm.openai_compatible_provider import OpenAICompatibleProvider
from app.services.llm.writer_observability import TRACE_FILENAME, observe_writer_call
from app.services.retrieval.document_retriever import build_retrieval_trace, retrieve_documents
from app.utils.runtime_paths import get_output_dir


logger = logging.getLogger(__name__)
RAG_TRACE_FILE = "RAG_RETRIEVAL_TRACE.json"
ATOMIC_TRACE_FILE = "ATOMIC_EVIDENCE_TRACE.json"
# Task-local observation channel, never part of the packet or Writer contract.
_material_traces = ContextVar("full_draft_material_traces", default=None)


def _build_full_draft_retrieval_trace(payload):
    """Full-draft-only projection of this retrieval result, without re-querying.

    Extend a fresh, source-free view; leave the shared retrieval sidecar and the
    input object untouched. Raw candidates and filtered samples carry source
    prose/metadata, so only explicit diagnostic fields may cross this boundary.
    """
    trace = build_retrieval_trace(payload)
    raw_candidates = [{
        "document_id": str(item.get("document_id") or item.get("doc_id") or "").strip(),
        **{key: item[key] for key in (
            "chunk_id", "chunk_index", "raw_rank", "score", "raw_similarity", "score_type",
        ) if key in item},
    } for item in payload.get("raw_chunk_candidates") or []]
    source_diagnostics = payload.get("retrieval_diagnostics") or {}
    diagnostics = {
        **{key: source_diagnostics[key] for key in (
            "raw_results_count", "rebuilt_doc_ids", "retrieval_mode",
        ) if key in source_diagnostics},
        "filtered_samples": [{key: item[key] for key in (
            "doc_id", "chunk_id", "chunk_index", "score", "filtered", "filter_reason",
        ) if key in item} for item in source_diagnostics.get("filtered_samples") or []],
    }
    for source, row in zip(
        (item for item in payload.get("ranked_documents") or [] if isinstance(item, Mapping)),
        trace["ranked_documents"],
    ):
        row.update({
            "score_components": {key: value for key, value in
                (source.get("score_components") or {}).items()
                if key in {
                    "max_chunk_similarity", "top3_mean_similarity", "semantic_aggregate",
                    "title_topic_match", "content_topic_match", "document_type_match",
                    "business_domain_match", "source_role_compatibility", "auxiliary_match",
                    "negative_title_match",
                }},
            "passed_primary_gate": source.get("passed_primary_gate"),
            "gate_reasons": list(source.get("gate_reasons") or []),
        })
    trace.update({
        "trace_schema_version": "full-draft-retrieval-trace-redacted-1",
        "raw_chunk_candidates": raw_candidates,
        "raw_candidate_count": len(raw_candidates),
        "retrieval_mode": diagnostics.get("retrieval_mode"),
        "retrieval_diagnostics": diagnostics,
        "insufficient_reason": str(payload.get("insufficient_reason") or "").strip(),
    })
    return trace


def _build_atomic_trace(assembly):
    """Project existing post-validation decisions; never rebuild or validate.

    Accepted/discarded rows retain identity, state and hashes, not source prose.
    The existing material_packet.json remains the exact Writer-input artifact.
    """
    anchors = json.loads(assembly.anchor_input_json)
    pack = anchors["atomic_evidence"]
    row_fields = ("evidence_id", "document_id", "chunk_id", "source_span_sha256",
                  "document_role", "evidence_type", "fact_status", "semantic_state",
                  "semantic_state_contract_version", "reason", "status")

    def rows(values):
        return [{key: item[key] for key in row_fields if key in item} for item in values]

    accepted = rows(pack.get("evidence") or [])
    discarded = rows(pack.get("discarded_evidence") or [])
    unavailable = rows(anchors.get("unavailable") or [])
    source_report = anchors["atomic_report"]
    report = {key: source_report[key] for key in (
        "flow3_v3_status", "passed", "hard_issue_count", "warning_count", "evidence_count",
        "discarded_evidence_count", "contains_complete_document", "contains_complete_chunk",
        "contains_source_title_or_heading", "contains_copied_20_han",
        "contains_oversized_source_copy", "web_tail_clean", "isolated_count",
        "isolated_evidence_ids", "isolation_reason", "remaining_atom_count",
        "local_isolation_limit", "local_isolation_status", "local_isolation_candidate_count",
    ) if key in source_report}
    report["evidence_type_distribution"] = {key: value for key, value in
        source_report.get("evidence_type_distribution", {}).items()
        if key in {"context", "risk", "action", "mechanism", "scene", "result", "expression"}}
    for field in ("hard_issues", "warnings"):
        # Free-text detail (including nested objects) is intentionally omitted;
        # upstream producers do not define this diagnostic privacy boundary.
        report[field] = [{key: item[key] for key in (
            "rule", "evidence_id", "field", "length",
        ) if key in item} for item in source_report.get(field) or []]
    return {
        "trace_schema_version": "atomic-evidence-trace-redacted-1",
        "redacted": True, "contains_source_text": False,
        "accepted_evidence": accepted, "accepted_count": len(accepted),
        "discarded_evidence": discarded, "discarded_count": len(discarded),
        "discard_reason_counts": dict(Counter(item.get("reason", "") for item in discarded)),
        "registry_count": len(anchors["registry"]),
        "registry_evidence_ids": list(anchors["registry"]),
        "registry_sha256": anchors["registry_sha256"],
        "unavailable": unavailable, "unavailable_count": len(unavailable),
        "unavailable_reason_counts": dict(Counter(item.get("reason", "") for item in unavailable)),
        "atomic_report": report, "packet_digest": assembly.packet.digest,
        "writer_input_artifact": "material_packet.json",
    }


def _capture_material_traces(retrieval, assembly):
    traces = _material_traces.get()
    if traces is None:
        return
    for name, build in (
        (RAG_TRACE_FILE, lambda: _build_full_draft_retrieval_trace(retrieval)),
        (ATOMIC_TRACE_FILE, lambda: _build_atomic_trace(assembly)),
    ):
        try:
            traces[name] = build()
        except Exception as exc:
            logger.warning("MATERIAL_TRACE_BUILD_FAILED file=%s error_type=%s", name, type(exc).__name__)


def _persist_material_traces(root, traces):
    for name, data in traces.items():
        try:
            _save_json(root, name, data)
        except Exception as exc:
            logger.warning("MATERIAL_TRACE_PERSIST_FAILED file=%s error_type=%s", name, type(exc).__name__)


def prepare_full_draft_material(request):
    # Only the existing task normalization is reused from generator. In
    # particular prepare_flow3_v33_structured_generation is never entered.
    from app.services.generation.generator import build_official_draft_task
    runtime = CommercialGenreRuntime.admit(request.document_type, request.export_format)
    task = build_official_draft_task(request)
    retrieval = retrieve_documents(task, persist_trace=False,
                                   retrieval_profile=runtime.adapter.retrieval_profile)
    assets = read_selected_material_assets(retrieval)
    assembly = assemble_task_from_assets(request, retrieval, assets)
    runtime.validate_packet(assembly.packet)
    _capture_material_traces(retrieval, assembly)
    return assembly.packet


def _save_json(root, name, data):
    (root / name).write_bytes((canonical_json(data) + "\n").encode("utf-8"))


async def generate_full_draft(request):
    request = ArticleGenRequest.model_validate(request.model_dump())
    if request.generation_route != "full_article_drafting":
        raise FullArticleContractError("FULL_DRAFT_EXPLICIT_ROUTE_REQUIRED")
    started = time.perf_counter()
    runtime = CommercialGenreRuntime.admit(request.document_type, request.export_format)
    genre = runtime.adapter
    reference_pack = (load_writing_reference_pack()
                      if genre.drafting_rules.reference_policy == "case32" else None)
    reference_hash = reference_pack.sha256 if reference_pack else ""
    traces = {}
    token = _material_traces.set(traces)
    try:
        packet = prepare_full_draft_material(request)
    finally:
        _material_traces.reset(token)
    runtime.validate_packet(packet)
    provider = get_llm_provider()
    if not isinstance(provider, OpenAICompatibleProvider) or not provider.config.model.lower().startswith("qwen"):
        raise FullArticleContractError("FULL_DRAFT_CURRENT_QWEN_PROVIDER_REQUIRED")
    root = Path(get_output_dir()) / "full-draft" / uuid.uuid4().hex
    root.mkdir(parents=True, exist_ok=False)
    _persist_material_traces(root, traces)
    _save_json(root, "material_packet.json", packet.__dict__)
    accounting = {"route": "full_article_drafting", "provider": "OpenAICompatibleProvider",
                  "model": provider.config.model, "writer_calls": 0, "material_brief_calls": 0,
                  "http_attempts": 0, "brief_http_attempts": 0, "writer_http_attempts": 0,
                  "retry_limit": 0, "fallback_used": False, "status": "PREPARED",
                  "material_preparation": "deterministic_task_assembly",
                  "run_id": root.name, "writer_trace": TRACE_FILENAME,
                  "REFERENCE_PACK_SHA256": reference_hash,
                  "commercial_runtime": runtime.diagnostics(),
                  "canonical_fact_status": "NOT_RUN"}
    _save_json(root, "call_accounting.json", accounting)

    def attempted(attempt):
        accounting.update(writer_http_attempts=attempt,
                          http_attempts=accounting["brief_http_attempts"] + attempt, status="WRITER_RUNNING")
        _save_json(root, "call_accounting.json", accounting)

    try:
        # brief_json is a deterministic compatibility view, never an LLM response.
        _save_json(root, "TASK_ASSEMBLY.json", {
            "kind": "deterministic_task_assembly", "input_sha256": packet.brief_input_sha256,
            "reading_view": json.loads(packet.brief_json),
            "fact_authority": "UNCHANGED_FROZEN_REGISTRY"})
        _save_json(root, "DETERMINISTIC_FACT_REVIEW_OVERLAY.json", packet.overlay())
        _save_json(root, "material_packet.json", packet.__dict__)
        with use_writing_reference_pack(reference_pack) if reference_pack else nullcontext():
            prompt = build_full_draft_prompt(packet)
        _save_json(root, "prompt_manifest.json", {
            **{k: v for k, v in prompt.items() if k not in {"system", "user"}},
            "REFERENCE_PACK_SHA256": reference_hash})
        call_options = runtime.claim_writer_call()
        accounting["writer_calls"] = 1
        # Only the verified exact Flash model opts in; other Qwen calls stay unchanged.
        writer_options = {"reasoning_effort": "low"} if provider.config.model == "qwen3.8-flash" else {}
        with observe_writer_call(root):
            raw = provider.generate_text(prompt["user"], system_prompt=prompt["system"],
                temperature=request.temperature, **call_options,
                on_http_request_attempt=attempted, **writer_options)
        # First operation on a returned article: persist exact UTF-8 bytes,
        # before validation, Markdown indexing, citations, checks or export.
        (root / "RAW_WRITER_ARTICLE.md").write_bytes(raw.encode("utf-8"))
        provenance = {"kind": "LIVE_FULL_ARTICLE_WRITER", "generation_route": request.generation_route,
                      "ruleset_id": prompt["ruleset_id"], "ruleset_hash": prompt["ruleset_hash"],
                      "host_boundary_hash": prompt["host_boundary_hash"], "raw_article_hash": text_sha(raw),
                      "material_packet_hash": packet.digest, "provider": accounting["provider"],
                      "model": accounting["model"], "run_id": root.name, "normalization": "NONE",
                      "REFERENCE_PACK_SHA256": reference_hash}
        candidate = import_full_article(raw, packet.candidate_input(), provenance)
        _save_json(root, "candidate.json", candidate.model_dump())
        identity = {"raw_article_hash": text_sha(raw), "candidate_article_hash": candidate.content_sha256,
                    "normalization": "NONE", "body_identical": raw == candidate.content}
        _save_json(root, "article_identity.json", identity)
        warnings = []
        try:
            citations = propose_exact_citations(candidate)
            facts = evaluate_full_article_facts(candidate, citations)
            accounting["canonical_fact_status"] = json.loads(facts.result_json)["status"]
            quality = evaluate_full_article_quality(candidate, citations, facts)
            runtime.require_no_recovery()
            _save_json(root, "citations.json", citations.model_dump())
            _save_json(root, "fact_report.json", facts.model_dump())
            _save_json(root, "quality_report.json", quality.model_dump())
            rendered = render_full_article(build_full_article_handoff(candidate, citations, facts, quality))
            # A non-body export failure never throws away an already checked draft.
            manifest = None
            try:
                manifest = deliver_full_article_artifacts(rendered, root)
            except Exception as exc:
                warnings.append("全文附件生成失败，请使用页面正文并重试导出：" + type(exc).__name__)
            result = full_article_review_result(rendered, manifest)
        except Exception as exc:
            _save_json(root, "checks_unavailable.json", {"error_type": type(exc).__name__, "reports_trusted": False})
            result = full_article_unchecked_result(candidate, error_type=type(exc).__name__)
        result.generation_route = request.generation_route
        result.generation_provenance = json.loads(candidate.provenance_json)
        result.selected_candidate_stage = "FULL_ARTICLE_WRITER"
        result.target_word_count = request.target_words
        result.export_format = request.export_format
        result.generation_time = round(time.perf_counter() - started, 3)
        result.delivery_warnings.extend(warnings)
        if result.delivery_decision:
            result.delivery_decision.delivery_warnings.extend(warnings)
        runtime.require_delivery_body(raw, result.article_markdown)
        (root / "FINAL_DELIVERED_ARTICLE.md").write_bytes(result.article_markdown.encode("utf-8"))
        _save_json(root, "api_result.json", result.model_dump())
        accounting["status"] = result.delivery_status
        return ArticleGenBatchResult(articles=[result], output_dir=str(get_output_dir()))
    except Exception as exc:
        accounting.update(status="FAILED", error_type=type(exc).__name__)
        raise  # Existing public error mapping; never invoke the old Writer.
    finally:
        _save_json(root, "call_accounting.json", accounting)
