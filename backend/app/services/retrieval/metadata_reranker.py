"""Bounded metadata reranking for already-retrieved RAG candidates."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


UNKNOWN_VALUES = {"", "未知", "unknown", "legacy", "未指定"}


@dataclass(frozen=True)
class MetadataRerankMatch:
    business_domain_match: bool
    document_type_match: bool
    max_forward_move: int


def _normalize_metadata_value(value: Any) -> str:
    text = str(value or "").replace("\u3000", " ").strip()
    text = re.sub(r"\s+", " ", text)
    return "" if text in UNKNOWN_VALUES else text


def _candidate_metadata(candidate: dict[str, Any]) -> dict[str, Any]:
    metadata = candidate.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    return {
        "business_domain": metadata.get("business_domain", candidate.get("business_domain")),
        "document_type": metadata.get("document_type", candidate.get("document_type")),
    }


def metadata_match_for_candidate(
    candidate: dict[str, Any],
    *,
    business_domain: str | None = None,
    document_type: str | None = None,
) -> MetadataRerankMatch:
    """Return exact metadata match flags and bounded forward movement."""
    metadata = _candidate_metadata(candidate)
    requested_business_domain = _normalize_metadata_value(business_domain)
    requested_document_type = _normalize_metadata_value(document_type)
    candidate_business_domain = _normalize_metadata_value(metadata.get("business_domain"))
    candidate_document_type = _normalize_metadata_value(metadata.get("document_type"))

    business_match = bool(
        requested_business_domain
        and candidate_business_domain
        and requested_business_domain == candidate_business_domain
    )
    document_match = bool(
        requested_document_type
        and candidate_document_type
        and requested_document_type == candidate_document_type
    )
    max_forward_move = min((2 if business_match else 0) + (1 if document_match else 0), 3)
    return MetadataRerankMatch(
        business_domain_match=business_match,
        document_type_match=document_match,
        max_forward_move=max_forward_move,
    )


def soft_rerank_by_request_metadata(
    candidates: list[dict[str, Any]] | None,
    *,
    business_domain: str | None = None,
    document_type: str | None = None,
) -> list[dict[str, Any]]:
    """Rerank candidates with bounded adjacent moves without filtering.

    The input order is treated as the baseline relevance order.  Metadata can
    only move a candidate forward across unmatched candidates, and never beyond
    its configured movement cap.  Candidates with any metadata match keep their
    original relative order.
    """
    items = list(candidates or [])
    if not items:
        return []

    decorated = []
    for index, item in enumerate(items):
        match = metadata_match_for_candidate(
            item,
            business_domain=business_domain,
            document_type=document_type,
        )
        decorated.append(
            {
                "item": item,
                "original_index": index,
                "max_forward_move": match.max_forward_move,
            }
        )

    if not any(entry["max_forward_move"] for entry in decorated):
        return items

    ordered = list(decorated)
    for current_position in range(len(ordered)):
        position = current_position
        entry = ordered[position]
        max_forward_move = int(entry["max_forward_move"])
        if max_forward_move <= 0:
            continue

        earliest_original_index = int(entry["original_index"]) - max_forward_move
        while position > 0:
            previous = ordered[position - 1]
            if int(previous["max_forward_move"]) > 0:
                break
            if int(previous["original_index"]) < earliest_original_index:
                break
            ordered[position - 1], ordered[position] = ordered[position], ordered[position - 1]
            position -= 1

    return [entry["item"] for entry in ordered]
