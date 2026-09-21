"""Explicit whole-summary route using existing Evidence, Provider and delivery.

No Planner/SectionWriter/fallback and no public client-supplied registry. The
normal entry obtains current RAG inputs. Frozen packets are service-level test
inputs only, never a second public API or material library.
"""
from dataclasses import asdict
import json
from pathlib import Path
import time
import uuid

from app.models.full_article import FullArticleContractError, canonical_json, text_sha
from app.models.generation import ArticleGenBatchResult, ArticleGenRequest
from app.services.generation.atomic_evidence import build_full_draft_anchor_input
from app.services.generation.full_draft_material import FullDraftMaterialPacket
from app.services.generation.writing_reference_pack import load_writing_reference_pack, use_writing_reference_pack
from app.services.generation.official_document_drafting import build_full_draft_prompt
from app.services.generation.full_article_candidate_adapter import import_full_article, propose_exact_citations
from app.services.generation.flow3_v3_trace import evaluate_full_article_facts
from app.services.generation.work_summary_quality_gate import evaluate_full_article_quality
from app.services.generation.delivery_decision import full_article_review_result, full_article_unchecked_result
from app.services.flow4.handoff import build_full_article_handoff
from app.services.flow4.renderer import render_full_article
from app.services.flow4.artifact_delivery import deliver_full_article_artifacts
from app.services.llm.factory import get_llm_provider
from app.services.llm.openai_compatible_provider import OpenAICompatibleProvider
from app.services.retrieval.document_retriever import retrieve_documents
from app.utils.runtime_paths import get_output_dir


def prepare_full_draft_material(request):
    # Only the existing task normalization is reused from generator. In
    # particular prepare_flow3_v33_structured_generation is never entered.
    from app.services.generation.generator import build_official_draft_task, derive_final_delivery_length_gate
    task = build_official_draft_task(request)
    retrieval = retrieve_documents(task, persist_trace=False)
    inputs = build_full_draft_anchor_input(task, retrieval)
    task_fields = {**asdict(task), "style": request.style, "scene": request.scene,
                   "hard_max": derive_final_delivery_length_gate(task.target_words)["max_words"]}
    return FullDraftMaterialPacket.from_authorized_input(task_fields, inputs)


def _save_json(root, name, data):
    (root / name).write_bytes((canonical_json(data) + "\n").encode("utf-8"))


async def generate_full_draft(request):
    request = ArticleGenRequest.model_validate(request.model_dump())
    if request.generation_route != "full_article_drafting":
        raise FullArticleContractError("FULL_DRAFT_EXPLICIT_ROUTE_REQUIRED")
    started = time.perf_counter()
    reference_pack = load_writing_reference_pack()
    packet = prepare_full_draft_material(request)
    provider = get_llm_provider()
    if not isinstance(provider, OpenAICompatibleProvider) or not provider.config.model.lower().startswith("qwen"):
        raise FullArticleContractError("FULL_DRAFT_CURRENT_QWEN_PROVIDER_REQUIRED")
    root = Path(get_output_dir()) / "full-draft" / uuid.uuid4().hex
    root.mkdir(parents=True, exist_ok=False)
    _save_json(root, "material_packet.json", packet.__dict__)
    accounting = {"route": "full_article_drafting", "provider": "OpenAICompatibleProvider",
                  "model": provider.config.model, "writer_calls": 0, "material_brief_calls": 0,
                  "http_attempts": 0, "brief_http_attempts": 0, "writer_http_attempts": 0,
                  "retry_limit": 0, "fallback_used": False, "status": "PREPARED",
                  "REFERENCE_PACK_SHA256": reference_pack.sha256}
    _save_json(root, "call_accounting.json", accounting)

    def attempted(attempt):
        accounting.update(writer_http_attempts=attempt,
                          http_attempts=accounting["brief_http_attempts"] + attempt, status="WRITER_RUNNING")
        _save_json(root, "call_accounting.json", accounting)

    try:
        # Exactly the CASE32 material role. It makes no writing decisions and
        # cannot create/replace source records or invoke a Planner.
        if not packet.brief_json:
            brief_prompt = packet.brief_prompt()
            _save_json(root, "BRIEF_REQUEST.json", {"model": provider.config.model,
                "messages": [{"role": "system", "content": brief_prompt["system"]},
                             {"role": "user", "content": brief_prompt["user"]}],
                "temperature": request.temperature, "retry": 0, "reasoning_effort": "none"})

            def brief_attempted(attempt):
                accounting.update(brief_http_attempts=attempt, http_attempts=attempt, status="BRIEF_RUNNING")
                _save_json(root, "call_accounting.json", accounting)

            accounting["material_brief_calls"] = 1
            brief_raw = provider.generate_text(brief_prompt["user"], system_prompt=brief_prompt["system"],
                temperature=request.temperature, retry_limit=0, preserve_whitespace=True, reasoning_effort="none",
                on_http_request_attempt=brief_attempted)
            (root / "BRIEF_RAW_RESPONSE.txt").write_bytes(brief_raw.encode("utf-8"))
            try:
                brief_value = json.loads(brief_raw)
            except ValueError as exc:
                raise FullArticleContractError("MATERIAL_BRIEF_RESPONSE_INVALID") from exc
            packet = packet.with_brief(brief_value)
        _save_json(root, "BRIEF_RESPONSE.json", json.loads(packet.brief_json))
        _save_json(root, "MATERIAL_BRIEF_BOUND.json", packet.bound_brief())
        _save_json(root, "DETERMINISTIC_FACT_REVIEW_OVERLAY.json", packet.overlay())
        _save_json(root, "material_packet.json", packet.__dict__)
        with use_writing_reference_pack(reference_pack):
            prompt = build_full_draft_prompt(packet)
        _save_json(root, "prompt_manifest.json", {
            **{k: v for k, v in prompt.items() if k not in {"system", "user"}},
            "REFERENCE_PACK_SHA256": reference_pack.sha256})
        accounting["writer_calls"] = 1
        raw = provider.generate_text(prompt["user"], system_prompt=prompt["system"],
            temperature=request.temperature, retry_limit=0, preserve_whitespace=True,
            on_http_request_attempt=attempted)
        # First operation on a returned article: persist exact UTF-8 bytes,
        # before validation, Markdown indexing, citations, checks or export.
        (root / "RAW_WRITER_ARTICLE.md").write_bytes(raw.encode("utf-8"))
        provenance = {"kind": "LIVE_FULL_ARTICLE_WRITER", "generation_route": request.generation_route,
                      "ruleset_id": prompt["ruleset_id"], "ruleset_hash": prompt["ruleset_hash"],
                      "host_boundary_hash": prompt["host_boundary_hash"], "raw_article_hash": text_sha(raw),
                      "material_packet_hash": packet.digest, "provider": accounting["provider"],
                      "model": accounting["model"], "run_id": root.name, "normalization": "NONE",
                      "REFERENCE_PACK_SHA256": reference_pack.sha256}
        candidate = import_full_article(raw, packet.candidate_input(), provenance)
        _save_json(root, "candidate.json", candidate.model_dump())
        identity = {"raw_article_hash": text_sha(raw), "candidate_article_hash": candidate.content_sha256,
                    "normalization": "NONE", "body_identical": raw == candidate.content}
        _save_json(root, "article_identity.json", identity)
        warnings = []
        try:
            citations = propose_exact_citations(candidate)
            facts = evaluate_full_article_facts(candidate, citations)
            quality = evaluate_full_article_quality(candidate, citations, facts)
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
        if result.article_markdown != raw:
            raise FullArticleContractError("FULL_DRAFT_BODY_CHANGED_DURING_DELIVERY")
        (root / "FINAL_DELIVERED_ARTICLE.md").write_bytes(result.article_markdown.encode("utf-8"))
        _save_json(root, "api_result.json", result.model_dump())
        accounting["status"] = result.delivery_status
        return ArticleGenBatchResult(articles=[result], output_dir=str(get_output_dir()))
    except Exception as exc:
        accounting.update(status="FAILED", error_type=type(exc).__name__)
        raise  # Existing public error mapping; never invoke the old Writer.
    finally:
        _save_json(root, "call_accounting.json", accounting)
