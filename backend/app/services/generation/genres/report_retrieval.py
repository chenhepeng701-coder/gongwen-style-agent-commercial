"""Synthetic/offline report reading policy; no commercial genre approval."""
import re
from typing import Any, Mapping

from app.services.generation.genres.retrieval import RetrievalProfile


def classify_coverage(candidate: Mapping[str, Any]) -> str:
    metadata = candidate.get("metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    content_role = str(candidate.get("content_role") or metadata.get("content_role") or "").lower()
    heading = str(candidate.get("section_heading") or metadata.get("section_heading") or "")
    section_path = " ".join(str(value) for value in (metadata.get("section_path") or []))
    context = f"{section_path} {heading}"
    if re.search(r"建议|请予|请求|拟办|下一步", context):
        return "recommendations"
    if content_role == "problem" or re.search(r"问题|困难|不足", context):
        return "issues"
    return "situation"


def chunk_preference(candidate: Mapping[str, Any]) -> float:
    # An offline report favors situation and issue reading without declaring
    # any factual writability. Shared evidence admission still runs afterwards.
    return {"situation": 0.04, "issues": 0.08, "recommendations": 0.0}[classify_coverage(candidate)]


REPORT_RETRIEVAL_PROFILE = RetrievalProfile(
    profile_id="report_offline_coverage_v1",
    coverage_roles=("situation", "issues", "recommendations"),
    classify_coverage=classify_coverage,
    chunk_preference=chunk_preference,
    chunk_preference_source_roles=(("fact_source", "fact_source"),
                                   ("mixed", "fact_source"), ("mixed", "mixed")),
    primary_min_coverage_chunks=6,
    primary_max_coverage_chunks=18,
)
