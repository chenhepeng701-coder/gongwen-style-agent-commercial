"""The existing work-summary reading/coverage policy, extracted without changes.

This is retrieval preference only. Atomic Evidence owns whether any proposition
can become authorized factual evidence, including unresolved placeholders.
"""
from dataclasses import replace
import re
from typing import Any, Mapping

from app.services.generation.genres.retrieval import RetrievalProfile


_FACT_COMPLETION_RE = re.compile(
    r"(?:截至|累计|已经|已完成|完成了|建立了|制定了|开展了|组织开展|落实了|推进了|"
    r"实现了|达到了|通过了|整改销号|排查发现|治理完成|建成|形成了)"
)
_FACT_RESULT_RE = re.compile(r"(?:成效|进展|结果|完成率|覆盖率|同比|提升|下降|销号率|持证上岗率)")
_UNRESOLVED_PLACEHOLDER_RE = re.compile(r"(?:[×Xx]{1,}|_{2,}|＿{2,})")
_PROBLEM_SECTION_RE = re.compile(r"(?:问题|不足|短板|差距|制约|瓶颈|未完成|尚未)")
_FUTURE_SECTION_RE = re.compile(
    r"(?:下一步|下阶段|下半年|工作打算|工作计划|工作措施|工作安排|部署要求|后续)"
)
_ACHIEVEMENT_SECTION_RE = re.compile(r"(?:总体|成绩|成效|成果|完成情况|进展|结果|态势)")
_FACT_ROLE_BONUS = {
    "result": 0.20,
    "progress": 0.18,
    "data": 0.18,
    "mixed": 0.10,
    "measure": 0.04,
    "problem": 0.12,
    "responsibility": 0.02,
}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _metadata(candidate: Mapping[str, Any]) -> dict[str, Any]:
    metadata = candidate.get("metadata")
    return dict(metadata) if isinstance(metadata, Mapping) else {}


def classify_coverage(candidate: Mapping[str, Any]) -> str:
    metadata = _metadata(candidate)
    content_role = _clean(candidate.get("content_role") or metadata.get("content_role")).lower()
    heading = _clean(candidate.get("section_heading") or metadata.get("section_heading"))
    section_path = " ".join(str(value) for value in (metadata.get("section_path") or []))
    context = f"{section_path} {heading}".strip()
    text = _clean(candidate.get("chunk_text"))
    if _FUTURE_SECTION_RE.search(context):
        return "next_step"
    if content_role == "problem" or _PROBLEM_SECTION_RE.search(context):
        return "problem"
    if content_role in {"result", "progress", "data", "mixed"} or _ACHIEVEMENT_SECTION_RE.search(context):
        return "achievement_status"
    if content_role in {"measure", "responsibility"} or _FACT_COMPLETION_RE.search(text):
        return "major_work"
    return "context"


def chunk_preference(candidate: Mapping[str, Any]) -> float:
    metadata = _metadata(candidate)
    content_role = _clean(candidate.get("content_role") or metadata.get("content_role")).lower()
    text = _clean(candidate.get("chunk_text"))
    heading = _clean(candidate.get("section_heading") or metadata.get("section_heading"))
    section_path = " ".join(str(value) for value in (metadata.get("section_path") or []))
    section_context = f"{section_path} {heading}".strip()
    bonus = _FACT_ROLE_BONUS.get(content_role, 0.0)
    if content_role == "problem" and int(metadata.get("section_level") or 0) >= 2:
        bonus += 0.08
    if content_role == "background":
        bonus -= 0.06
    if _FACT_COMPLETION_RE.search(text):
        bonus += 0.12
    if _FACT_RESULT_RE.search(text):
        bonus += 0.05
    if _FUTURE_SECTION_RE.search(section_context):
        bonus -= 0.30
    # A chunk carrying unresolved user placeholders can still provide style or
    # structure, but it must not displace a clean factual chunk in the fixed
    # primary evidence budget.
    if _UNRESOLVED_PLACEHOLDER_RE.search(text):
        bonus -= 0.40
    return bonus


WORK_SUMMARY_RETRIEVAL_PROFILE = RetrievalProfile(
    profile_id="work_summary_coverage_v1",
    coverage_roles=("achievement_status", "major_work", "problem", "next_step"),
    classify_coverage=classify_coverage,
    chunk_preference=chunk_preference,
)

# Other pre-existing low-level retrieval callers used the same chunk reading
# preferences with a relevance-only primary selection. This compatibility
# profile is NOT a registered genre and grants no formal drafting capability.
LEGACY_RETRIEVAL_PROFILE = replace(
    WORK_SUMMARY_RETRIEVAL_PROFILE,
    profile_id="legacy_relevance_only",
    primary_coverage=False,
)
