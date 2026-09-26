"""Minimal report observations for offline architecture proof, not acceptance."""
import json

from app.models.full_article import bind_report
from app.services.generation.full_article_quality import (
    full_article_quality_context, quality_check_receipt,
)


def evaluate_report_offline_quality(candidate, sidecar, fact_report):
    from .registry import resolve_genre

    observed = full_article_quality_context(candidate, sidecar, fact_report)
    adapter = resolve_genre(observed["task"].get("document_type", ""), purpose="offline")
    contract = adapter.structure_contract
    blocks = observed["blocks"]
    headings = [candidate.content[block.text_start:block.end].strip() for block in blocks if block.level]
    duplicates = len(headings) - len(set(headings))
    checks = []

    def add(check_id, result, evidence, evaluator, *, required=True, migration=""):
        checks.append(quality_check_receipt(candidate, fact_report, check_id, result,
            evidence, evaluator, required=required, migration=migration))

    add("raw_structure_and_locations", "PASS", [block.model_dump() for block in blocks], "article_blocks")
    add("heading_repetition_and_style", "REVIEW_REQUIRED" if duplicates else "PASS",
        {"duplicate_heading_count": duplicates, "structure_contract_id": contract.contract_id},
        "report_offline_heading_observation")
    add("content_quality_observations", "MANUAL_REVIEW_REQUIRED",
        {"applicability": "OFFLINE_ARCHITECTURE_PROOF", "semantic_maturity_evaluated": False},
        "report_offline_content_applicability")
    add("whole_article_budget", observed["length_status"], observed["length"], "derive_final_delivery_length_gate",
        migration="全文采用现行长度规则；超限只记录，不删改正文；没有旧 Writer budget usage。")
    add("fact_safety", observed["facts"]["status"], {"report_hash": fact_report.result_sha256,
        "counts": observed["facts"]["validation_counts"]}, "evaluate_full_article_facts")
    add("legacy_plan_intent_budget_fulfilment", "UNAVAILABLE_FOR_FULL_ARTICLE",
        {"missing": ["Frozen Plan", "ParagraphTask", "paragraph_intent_id", "M2/M3 trace", "budget usage"]},
        "applicability", required=False, migration="无旧过程输入；不适用，不计作通过。")
    add("semantic_task_and_evidence_coverage", "MANUAL_REVIEW_REQUIRED",
        {"task_requirement": observed["task"], "structure_contract_id": contract.contract_id,
         "section_roles": list(contract.section_roles), "article_structure": [block.model_dump() for block in blocks],
         "fact_report_hash": fact_report.result_sha256}, "report_offline_structure_contract",
        migration=contract.maturity_review_instruction)
    add("baseline_genre_maturity_revision_cost", "MANUAL_REVIEW_REQUIRED",
        {"quality_contract": adapter.quality_contract_id,
         "manual_items": ["report_structure_maturity", "audience_fit", "user_editing_cost"],
         "production_ready": False}, "report_offline_quality_contract",
        migration="只证明适配器链路；汇报材料成熟度和商业质量未经真实验收。")
    return bind_report("QUALITY", candidate, sidecar, {
        **observed["identity"], "status": "REVIEW_REQUIRED", "fact_report_sha256": fact_report.result_sha256,
        "checks": checks, "required_check_ids": [check["id"] for check in checks if check["required"]],
        "required_checks_complete": False, "quality_pass": False, "article_qualified": False,
        "quality_contract_id": adapter.quality_contract_id, "experimental_m4": "NOT_REQUIRED_NOT_RUN",
        "heading_observations": {"duplicate_heading_count": duplicates}, "signals": [],
        "paragraph_lengths": [{"block_id": block.block_id, "characters": block.end-block.text_start}
                              for block in blocks if not block.level],
        "review_input": {"task": observed["task"], "paragraphs": [block.model_dump() for block in blocks],
                         "candidate_digest": candidate.digest, "article_hash": candidate.content_sha256},
        "automatic_rewrite": False, "offline_architecture_proof": True, "production_ready": False,
    })
