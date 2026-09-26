"""Shared full-article quality boundary; genre evaluators never own facts.

The registry selects content/structure observations. Canonical Fact Safety,
immutable article identity and the common length surface stay in the host.
"""
import json

from app.models.full_article import (
    FullArticleContractError, article_blocks, article_check_identity, article_units,
    canonical_json, text_sha,
)


def full_article_quality_context(candidate, sidecar, fact_report):
    """Authenticate shared safety and expose only read-only observations."""
    from app.services.generation.flow3_v3_trace import require_full_article_fact_report
    from app.services.generation.generator import count_official_draft_words, derive_final_delivery_length_gate

    require_full_article_fact_report(candidate, sidecar, fact_report)
    raw_task = json.loads(candidate.task_json)
    task = raw_task.get("user_task", raw_task)
    measured = count_official_draft_words(candidate.content)
    target = task.get("target_words")
    length = {"target_words": target, "measured_article_characters": measured,
              "count_basis": "count_official_draft_words(canonical_raw_content)",
              "legacy_writer_hard_max": raw_task.get("hard_max"),
              "legacy_budget_usage": "UNAVAILABLE_FOR_FULL_ARTICLE"}
    length_status = "MANUAL_REVIEW_REQUIRED"
    if type(target) is int and target > 0:
        limits = derive_final_delivery_length_gate(target)
        length.update(limits)
        length_status = ("FAIL_OVER_LENGTH" if measured > limits["max_words"] else
                         "MANUAL_REVIEW_REQUIRED" if measured < limits["min_words"] else "PASS")
        length["under_length_policy"] = "REVIEW_WITHOUT_LEGACY_CAPACITY_MODE"
        length["upper_limit_severity"] = "BLOCKING_QUALIFICATION_NOT_BODY_DELIVERY"
        length["target_delta"] = measured - target
    return {"identity": article_check_identity(candidate), "task": task,
            "facts": json.loads(fact_report.result_json), "blocks": article_blocks(candidate.content),
            "units": article_units(candidate.content), "length": length, "length_status": length_status}


def quality_check_receipt(candidate, fact_report, check_id, result, evidence, evaluator,
                          *, required=True, migration=""):
    """Common integrity vocabulary consumed by FLOW4, without a quality verdict."""
    row = {"id": check_id, "check_result": result, "status": result,
           "required": required, "evidence": evidence,
           "input": {**article_check_identity(candidate), "fact_report_sha256": fact_report.result_sha256},
           "evaluator": evaluator, "article_version": candidate.version, "migration": migration}
    row["report_identity"] = text_sha(canonical_json(row))
    return row


def evaluate_full_article_quality(candidate, sidecar, fact_report):
    """Single registry dispatch after the canonical shared safety prerequisite."""
    from app.services.generation.genres.registry import resolve_genre

    observed = full_article_quality_context(candidate, sidecar, fact_report)
    task = observed["task"]
    adapter = resolve_genre(task.get("document_type", ""), purpose="inspection")
    before = (candidate.digest, sidecar.digest, fact_report.result_sha256)
    report = adapter.quality_evaluator(candidate, sidecar, fact_report)
    if before != (candidate.digest, sidecar.digest, fact_report.result_sha256):
        raise FullArticleContractError("GENRE_QUALITY_INPUT_MUTATED")
    report.require_current(candidate, sidecar)
    result = json.loads(report.result_json)
    if (report.owner != "QUALITY" or result.get("quality_contract_id") != adapter.quality_contract_id
            or result.get("fact_report_sha256") != fact_report.result_sha256
            or any(result.get(key) != value for key, value in article_check_identity(candidate).items())):
        raise FullArticleContractError("GENRE_QUALITY_REPORT_IDENTITY_INVALID")
    if (json.loads(fact_report.result_json)["status"] != "PASS"
            and (result.get("quality_pass") is True or result.get("article_qualified") is True)):
        raise FullArticleContractError("GENRE_QUALITY_CANNOT_OVERRIDE_FACT_SAFETY")
    # Genre content observations cannot replace shared structure/length/fact
    # receipts. Reproduce these from authenticated inputs, not adapter claims.
    common = (
        quality_check_receipt(candidate, fact_report, "raw_structure_and_locations", "PASS",
            [block.model_dump() for block in observed["blocks"]], "article_blocks"),
        quality_check_receipt(candidate, fact_report, "whole_article_budget", observed["length_status"],
            observed["length"], "derive_final_delivery_length_gate",
            migration="全文采用现行长度规则；超限只记录，不删改正文；没有旧 Writer budget usage。"),
        quality_check_receipt(candidate, fact_report, "fact_safety", observed["facts"]["status"],
            {"report_hash": fact_report.result_sha256, "counts": observed["facts"]["validation_counts"]},
            "evaluate_full_article_facts"),
    )
    for expected in common:
        actual = [check for check in result.get("checks", []) if check.get("id") == expected["id"]]
        if actual != [expected]:
            raise FullArticleContractError("GENRE_QUALITY_SHARED_CHECK_INVALID")
    return report
