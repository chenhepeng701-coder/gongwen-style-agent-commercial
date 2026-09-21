"""Non-blocking density observations for structured Writer output.

The metrics in this module are engineering observations only.  They do not
define production thresholds, change FACT / ANALYSIS validation, or decide
article quality.
"""

from __future__ import annotations

from typing import Any, Mapping


def _ordered_chain_ids(section: Mapping[str, Any]) -> list[list[str]]:
    chains: list[list[str]] = []
    for chain in section.get("work_chain_summaries") or []:
        if not isinstance(chain, Mapping):
            continue
        if str(chain.get("order_status") or "") != "ORDER_AVAILABLE":
            continue
        values = [
            str(value)
            for value in chain.get("ordered_evidence_ids") or []
            if str(value)
        ]
        if values:
            chains.append(values)
    direct = [
        str(value)
        for value in section.get("ordered_chain_evidence_ids") or []
        if str(value)
    ]
    if direct and direct not in chains:
        chains.append(direct)
    return chains


def observe_writer_density(
    draft: Mapping[str, Any],
    content_plan: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return descriptive FACT/ANALYSIS density without a pass/fail verdict."""

    plan_by_id = {
        str(section.get("section_id") or ""): section
        for section in (content_plan or {}).get("sections") or []
        if isinstance(section, Mapping)
    }
    fact_sentence_count = 0
    analysis_sentence_count = 0
    fact_chars = 0
    analysis_chars = 0
    max_consecutive_analysis = 0
    consecutive_analysis = 0
    fact_followed_by_analysis_count = 0
    work_chain_adjacent_fact_count = 0

    for section in draft.get("sections") or []:
        if not isinstance(section, Mapping):
            continue
        section_id = str(section.get("section_id") or "")
        sentences = [
            sentence
            for sentence in section.get("sentences") or []
            if isinstance(sentence, Mapping)
        ]
        for index, sentence in enumerate(sentences):
            sentence_type = str(sentence.get("sentence_type") or "")
            text = str(sentence.get("text") or "").strip()
            if sentence_type == "FACT":
                fact_sentence_count += 1
                fact_chars += len(text)
                consecutive_analysis = 0
                if (
                    index + 1 < len(sentences)
                    and str(sentences[index + 1].get("sentence_type") or "")
                    == "ANALYSIS"
                ):
                    fact_followed_by_analysis_count += 1
            elif sentence_type == "ANALYSIS":
                analysis_sentence_count += 1
                analysis_chars += len(text)
                consecutive_analysis += 1
                max_consecutive_analysis = max(
                    max_consecutive_analysis,
                    consecutive_analysis,
                )
            else:
                consecutive_analysis = 0

        positions: dict[str, list[int]] = {}
        for index, sentence in enumerate(sentences):
            if str(sentence.get("sentence_type") or "") != "FACT":
                continue
            evidence_ids = sentence.get("evidence_ids")
            if not isinstance(evidence_ids, list):
                evidence_ids = [sentence.get("evidence_id")]
            for evidence_id in evidence_ids:
                evidence_id = str(evidence_id or "")
                if evidence_id:
                    positions.setdefault(evidence_id, []).append(index)
        counted_pairs: set[tuple[str, str, str]] = set()
        for chain_ids in _ordered_chain_ids(plan_by_id.get(section_id) or {}):
            for left, right in zip(chain_ids, chain_ids[1:]):
                pair = (section_id, left, right)
                if pair in counted_pairs:
                    continue
                counted_pairs.add(pair)
                if any(
                    right_index in {left_index, left_index + 1}
                    for left_index in positions.get(left, [])
                    for right_index in positions.get(right, [])
                ):
                    work_chain_adjacent_fact_count += 1

    total_chars = fact_chars + analysis_chars
    return {
        "schema_version": "writer-density-observation-1",
        "observation_only": True,
        "hard_gate": False,
        "thresholds": None,
        "fact_sentence_count": fact_sentence_count,
        "analysis_sentence_count": analysis_sentence_count,
        "fact_chars": fact_chars,
        "analysis_chars": analysis_chars,
        "fact_char_ratio": round(fact_chars / total_chars, 6) if total_chars else 0.0,
        "analysis_char_ratio": (
            round(analysis_chars / total_chars, 6) if total_chars else 0.0
        ),
        "max_consecutive_analysis": max_consecutive_analysis,
        "max_consecutive_analysis_scope": "document_order_across_sections",
        "fact_followed_by_analysis_count": fact_followed_by_analysis_count,
        "fact_followed_by_analysis_scope": "immediate_next_sentence_same_section",
        "work_chain_adjacent_fact_count": work_chain_adjacent_fact_count,
    }
