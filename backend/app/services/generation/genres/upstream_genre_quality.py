"""Genre-specific lexical observations within the existing QUALITY boundary.

This does not call upstream executables or supply any Fact Safety verdict.
Missing cues are review leads, never proof that a function is semantically
absent. Conversely, matched words cannot certify mature article quality.
"""
from app.models.full_article import bind_report
from app.services.generation.full_article_quality import (
    full_article_quality_context, quality_check_receipt,
)
from .upstream_genre_contracts import genre_spec


def _locations(content, terms, *, start=0):
    matches = []
    for term in terms:
        offset = content.find(term, start)
        if offset >= 0:
            matches.append({"cue": term, "start": offset, "end": offset + len(term)})
    return sorted(matches, key=lambda row: (row["start"], row["end"]))


def observe_genre_content(upstream_id: str, content: str) -> dict:
    """Inspect the genre contract only; never infer evidence support or safety."""
    spec = genre_spec(upstream_id)
    first_line = next((line for line in content.splitlines() if line.strip()), "")
    title = first_line.strip().lstrip("#").strip()
    body_start = content.find(first_line) + len(first_line) if first_line else 0
    roles = []
    for role in spec.roles:
        locations = _locations(content, role.cues, start=body_start)
        roles.append({"role": role.role_id, "label": role.label, "required": role.required,
                      "observed": bool(locations), "locations": locations})
    missing = [row["role"] for row in roles if row["required"] and not row["observed"]]
    positions = {row["role"]: row["locations"][0]["start"] for row in roles if row["locations"]}
    order_reviews = [{"before": before, "after": after,
                      "before_position": positions[before], "after_position": positions[after]}
                     for before, after in spec.order_pairs
                     if before in positions and after in positions and positions[before] > positions[after]]
    unresolved_pairs = [[before, after] for before, after in spec.order_pairs
                        if before not in positions or after not in positions]
    contamination = _locations(content, spec.inappropriate_phrases, start=body_start)
    # Closing cues are observed near the end, allowing ordinary signature/date
    # lines. Their exact legal/application appropriateness stays manual.
    tail_start = max(body_start, len(content) - 240)
    closings = _locations(content, spec.closing_terms, start=tail_start)
    identity_present = any(term in title for term in spec.identity_terms)
    identity_status = ("PASS" if identity_present else "REVIEW_REQUIRED"
                       if spec.title_identity_required else "NOT_REQUIRED")
    closing_status = ("PASS" if closings else "REVIEW_REQUIRED"
                      if spec.closing_required else "NOT_REQUIRED")
    return {
        "upstream_genre_id": upstream_id, "source_path": spec.source_path,
        "inspection_basis": "LEXICAL_CUES_ONLY_NOT_SEMANTIC_ACCEPTANCE",
        "required_elements": {"status": "REVIEW_REQUIRED" if missing else "PASS",
                              "missing_roles": missing, "roles": roles},
        "document_identity": {"status": identity_status, "title": title,
                              "expected_terms": list(spec.identity_terms),
                              "title_identity_required": spec.title_identity_required},
        "closing_convention": {"status": closing_status, "required": spec.closing_required,
                               "common_terms": list(spec.closing_terms), "locations": closings,
                               "absence_policy": "REVIEW_ONLY_NO_BODY_REWRITE"},
        "inappropriate_mixing": {"status": "REVIEW_REQUIRED" if contamination else "PASS",
                                 "locations": contamination},
        "ordering": {"status": "REVIEW_REQUIRED" if order_reviews or unresolved_pairs else "PASS",
                     "expected_pairs": [list(pair) for pair in spec.order_pairs],
                     "reversed_pairs": order_reviews, "unobserved_pairs": unresolved_pairs},
        "review_instruction": spec.review_instruction,
        "manual_elements": {"status": "MANUAL_REVIEW_REQUIRED" if spec.manual_elements else "NOT_REQUIRED",
                            "items": list(spec.manual_elements),
                            "absence_policy": "DO_NOT_INVENT_OR_COMPLETE_FACTS"},
        "fact_authority": "SHARED_CANONICAL_FACT_ONLY",
        "semantic_maturity_evaluated": False,
    }


def quality_evaluator(upstream_id: str):
    # Resolve at construction, so a malformed identity cannot defer failure
    # until an article exists. The closure owns only its frozen genre semantics.
    spec = genre_spec(upstream_id)

    def evaluate(candidate, sidecar, fact_report):
        from .registry import resolve_genre

        observed = full_article_quality_context(candidate, sidecar, fact_report)
        adapter = resolve_genre(observed["task"].get("document_type", ""), purpose="inspection")
        contract = adapter.structure_contract
        blocks = observed["blocks"]
        headings = [candidate.content[block.text_start:block.end].strip() for block in blocks if block.level]
        duplicates = len(headings) - len(set(headings))
        genre_observations = observe_genre_content(spec.upstream_id, candidate.content)
        checks = []

        def add(check_id, result, evidence, evaluator, *, required=True, migration=""):
            checks.append(quality_check_receipt(candidate, fact_report, check_id, result,
                evidence, evaluator, required=required, migration=migration))

        # These seven required receipts are the existing FLOW4 contract. Genre
        # diagnostics are evidence within that contract, not a new handoff.
        add("raw_structure_and_locations", "PASS", [block.model_dump() for block in blocks], "article_blocks")
        add("heading_repetition_and_style", "REVIEW_REQUIRED" if duplicates else "PASS",
            {"duplicate_heading_count": duplicates, "structure_contract_id": contract.contract_id},
            f"upstream_{upstream_id}_heading_observation")
        add("content_quality_observations", "MANUAL_REVIEW_REQUIRED", genre_observations,
            f"upstream_{upstream_id}_genre_observations",
            migration="词面命中仅支持结构复核；不等于内容充分、成熟稿或商业质量验收。")
        add("whole_article_budget", observed["length_status"], observed["length"], "derive_final_delivery_length_gate",
            migration="全文采用现行长度规则；超限只记录，不删改正文；没有旧 Writer budget usage。")
        add("fact_safety", observed["facts"]["status"], {"report_hash": fact_report.result_sha256,
            "counts": observed["facts"]["validation_counts"]}, "evaluate_full_article_facts")
        add("legacy_plan_intent_budget_fulfilment", "UNAVAILABLE_FOR_FULL_ARTICLE",
            {"missing": ["Frozen Plan", "ParagraphTask", "paragraph_intent_id", "M2/M3 trace", "budget usage"]},
            "applicability", required=False, migration="无旧过程输入；不适用，不计作通过。")
        add("semantic_task_and_evidence_coverage", "MANUAL_REVIEW_REQUIRED",
            {"task_requirement": observed["task"], "structure_contract_id": contract.contract_id,
             "section_roles": list(contract.section_roles), "genre_observations": genre_observations,
             "article_structure": [block.model_dump() for block in blocks],
             "fact_report_hash": fact_report.result_sha256}, f"upstream_{upstream_id}_structure_contract",
            migration=contract.maturity_review_instruction)
        add("baseline_genre_maturity_revision_cost", "MANUAL_REVIEW_REQUIRED",
            {"quality_contract": adapter.quality_contract_id, "upstream_genre_id": upstream_id,
             "manual_items": ["genre_structure_maturity", "audience_fit", "user_editing_cost"],
             "production_ready": False}, f"upstream_{upstream_id}_quality_contract",
            migration="已接入共享商业基础设施；真实 Writer 成稿、黄金样本和用户质量验收尚未进行。")
        return bind_report("QUALITY", candidate, sidecar, {
            **observed["identity"], "status": "REVIEW_REQUIRED", "fact_report_sha256": fact_report.result_sha256,
            "checks": checks, "required_check_ids": [check["id"] for check in checks if check["required"]],
            "required_checks_complete": False, "quality_pass": False, "article_qualified": False,
            "quality_contract_id": adapter.quality_contract_id, "experimental_m4": "NOT_REQUIRED_NOT_RUN",
            "heading_observations": {"duplicate_heading_count": duplicates}, "signals": [],
            "genre_observations": genre_observations,
            "paragraph_lengths": [{"block_id": block.block_id, "characters": block.end-block.text_start}
                                  for block in blocks if not block.level],
            "review_input": {"task": observed["task"], "paragraphs": [block.model_dump() for block in blocks],
                             "candidate_digest": candidate.digest, "article_hash": candidate.content_sha256},
            "automatic_rewrite": False, "offline_architecture_proof": True, "production_ready": False,
        })

    evaluate.__name__ = f"evaluate_upstream_{upstream_id.replace('-', '_')}_quality"
    return evaluate
