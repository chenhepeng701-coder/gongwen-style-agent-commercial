"""Document-level RAG retrieval built on the project's stored vector index."""

from __future__ import annotations

import json
import logging
import re
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean
from typing import Any, Mapping

from app.services.retrieval.official_document_classifier import classify_document_role
from app.services.retrieval.retriever import search_detailed
from app.utils.runtime_paths import get_logs_dir
from app.utils.storage import atomic_write_text


logger = logging.getLogger(__name__)


RELEVANCE_THRESHOLD = 0.50
RAW_CANDIDATE_LIMIT = 200
# Work-summary fact sources need document coverage, not a fixed semantic Top-K.
# Six remains only a relevance seed; the final primary budget is derived from
# the document's clean structural coverage and size.
PRIMARY_RELEVANCE_SEED = 6
PRIMARY_MIN_COVERAGE_CHUNKS = 8
PRIMARY_MAX_COVERAGE_CHUNKS = 24
AUXILIARY_CHUNK_BUDGET = 2
PRIMARY_TEXT_LIMIT = 800
AUXILIARY_TEXT_LIMIT = 300
MAX_AUXILIARY_DOCUMENTS = 2
MAX_AUXILIARY_SEMANTIC_GAP = 0.15

_NEGATION_MARKERS = (
    "不得参考", "避免参考", "不包括", "不要", "排除", "无需", "勿用",
)
_RETRIEVAL_PREFIX_RE = re.compile(r"^(?:请)?(?:查找|检索|寻找|搜索|参考)\s*")
_DISABLED_REFERENCE_SCOPES = {
    "none", "disabled", "off", "不使用资料库", "不参考资料", "无需参考资料",
}
_UNKNOWN_VALUES = {"", "未知", "unknown", "legacy", "未指定", "其他"}
_GENERIC_TOPIC_TERMS = (
    "2026年", "2026 年", "关于", "围绕", "年度", "当前", "重点", "开展",
    "工作", "会议", "讲话", "报告", "材料", "全市", "全县", "全区",
    "全镇", "单位", "部门", "相关", "进行", "推进",
)
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
_RESULT_KEYS = (
    "doc_id", "title", "file_path", "chunk_index", "chunk_text", "score",
    "score_type", "vector_score", "chunk_id", "source_file", "metadata",
    "document_type", "business_domain", "org_type", "level",
    "writing_purpose", "section_index", "section_heading",
)


def _value(source: Any, name: str, default: Any = "") -> Any:
    if isinstance(source, Mapping):
        return source.get(name, default)
    return getattr(source, name, default)


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _normalized(value: Any) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", _clean(value).lower())


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = _normalized(value)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(value)
    return result


def _split_positive_and_negative(title: str) -> tuple[str, list[str]]:
    marker_positions = [
        (title.find(marker), marker)
        for marker in _NEGATION_MARKERS
        if title.find(marker) >= 0
    ]
    if not marker_positions:
        positive = title
        negative_text = ""
    else:
        index, marker = min(marker_positions, key=lambda item: item[0])
        positive = title[:index]
        negative_text = title[index + len(marker):]
    positive = _RETRIEVAL_PREFIX_RE.sub("", positive).strip(" ，,。；;：:")
    negatives = [
        part.strip(" ，,。；;：:")
        for part in re.split(r"[，,。；;、]", negative_text)
        if part.strip(" ，,。；;：:")
    ]
    return positive or title.strip(), negatives


def _field_payload(task: Any) -> dict[str, Any]:
    return {
        "title": _clean(_value(task, "title")),
        "document_type": _clean(_value(task, "document_type")),
        "level": _clean(_value(task, "level")),
        "org_type": _clean(_value(task, "org_type")),
        "business_domain": _clean(_value(task, "business_domain")),
        "writing_purpose": _clean(_value(task, "writing_purpose")),
        "target_words": _value(task, "target_words", None),
        "reference_scope": _clean(_value(task, "reference_scope")),
    }


def prepare_retrieval_request(task: Any) -> dict[str, Any]:
    fields = _field_payload(task)
    positive_title, negative_concepts = _split_positive_and_negative(fields["title"])
    primary_limits = _unique([
        fields["document_type"], fields["business_domain"],
    ])
    auxiliary_limits = []
    positive_normalized = _normalized(positive_title)
    for value in (fields["org_type"], fields["level"], fields["writing_purpose"]):
        normalized = _normalized(value)
        if normalized and normalized not in positive_normalized:
            auxiliary_limits.append(value)
    lines = [f"主题：{positive_title}"]
    if primary_limits:
        lines.append(f"检索限定：{'；'.join(primary_limits)}")
    auxiliary_limits = _unique(auxiliary_limits)
    if auxiliary_limits:
        lines.append(f"辅助限定：{'；'.join(auxiliary_limits)}")
    return {
        "query_text": "\n".join(lines),
        "query_fields": fields,
        "positive_title": positive_title,
        "negative_concepts": negative_concepts,
        "semantic_fields": [
            name for name in ("title", "document_type", "business_domain", "org_type", "level", "writing_purpose")
            if fields.get(name)
        ],
        "excluded_semantic_fields": ["target_words", "reference_scope", "scene", "style"],
    }


def _meaningful_ngrams(value: str) -> set[str]:
    normalized = _normalized(value)
    for term in _GENERIC_TOPIC_TERMS:
        normalized = normalized.replace(_normalized(term), "")
    if len(normalized) < 2:
        normalized = _normalized(value)
    return {
        normalized[index:index + 2]
        for index in range(max(0, len(normalized) - 1))
        if len(normalized[index:index + 2]) == 2
    }


def _coverage(query: str, candidate: str) -> float:
    query_terms = _meaningful_ngrams(query)
    if not query_terms:
        return 0.0
    return len(query_terms & _meaningful_ngrams(candidate)) / len(query_terms)


def _field_match(requested: Any, candidate: Any) -> float:
    requested_value = _normalized(requested)
    candidate_value = _normalized(candidate)
    if requested_value in _UNKNOWN_VALUES or candidate_value in _UNKNOWN_VALUES:
        return 0.0
    return float(
        requested_value == candidate_value
        or requested_value in candidate_value
        or candidate_value in requested_value
    )


def _metadata(candidate: Mapping[str, Any]) -> dict[str, Any]:
    metadata = candidate.get("metadata")
    return dict(metadata) if isinstance(metadata, Mapping) else {}


def _metadata_value(candidate: Mapping[str, Any], name: str) -> Any:
    metadata = _metadata(candidate)
    return metadata.get(name, candidate.get(name))


def _source_role(
    *,
    document_type: Any = "",
    writing_purpose: Any = "",
    candidate: Mapping[str, Any] | None = None,
) -> str:
    if candidate is not None:
        stored = _clean(_metadata_value(candidate, "document_role"))
        if stored in {"fact_source", "expression_source", "mixed"}:
            return stored
        document_type = _metadata_value(candidate, "document_type")
        writing_purpose = _metadata_value(candidate, "writing_purpose")
    return classify_document_role(
        document_type=_clean(document_type),
        writing_purpose=_clean(writing_purpose),
    )


def _role_compatibility(requested_role: str, candidate_role: str) -> float:
    if requested_role == "mixed" or candidate_role == "mixed":
        return 0.0
    return 1.0 if requested_role == candidate_role else -1.0


def _similarity(candidate: Mapping[str, Any]) -> float:
    raw = candidate.get("vector_score")
    if raw is None:
        raw = candidate.get("score")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def _raw_candidate(candidate: Mapping[str, Any], rank: int) -> dict[str, Any]:
    result = {key: candidate.get(key) for key in _RESULT_KEYS if key in candidate}
    result.update({
        "raw_rank": rank,
        "raw_similarity": round(_similarity(candidate), 6),
    })
    return result


def _aggregate_documents(
    candidates: list[dict[str, Any]],
    prepared: dict[str, Any],
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        doc_id = _clean(candidate.get("doc_id"))
        if doc_id:
            grouped[doc_id].append(candidate)

    fields = prepared["query_fields"]
    positive_title = prepared["positive_title"]
    negative_concepts = prepared["negative_concepts"]
    requested_source_role = _source_role(
        document_type=fields["document_type"],
        writing_purpose=fields["writing_purpose"],
    )
    documents: list[dict[str, Any]] = []
    for doc_id, chunks in grouped.items():
        relevance_order = sorted(
            chunks,
            key=lambda item: (-_similarity(item), int(item.get("chunk_index", 0) or 0)),
        )
        similarities = [_similarity(item) for item in relevance_order]
        max_similarity = similarities[0]
        top3_mean = fmean(similarities[:3])
        semantic_aggregate = 0.65 * max_similarity + 0.35 * top3_mean
        representative = relevance_order[0]
        title = _clean(representative.get("title"))
        top_content = "\n".join(_clean(item.get("chunk_text")) for item in relevance_order[:3])
        title_topic_match = _coverage(positive_title, title)
        content_topic_match = _coverage(positive_title, f"{title}\n{top_content}")
        document_type_match = _field_match(
            fields["document_type"], _metadata_value(representative, "document_type")
        )
        business_domain_match = _field_match(
            fields["business_domain"], _metadata_value(representative, "business_domain")
        )
        candidate_source_role = _source_role(candidate=representative)
        source_role_compatibility = _role_compatibility(requested_source_role, candidate_source_role)
        auxiliary_haystack = " ".join([
            title,
            _clean(_metadata_value(representative, "org_type")),
            _clean(_metadata_value(representative, "level")),
            _clean(_metadata_value(representative, "writing_purpose")),
        ])
        auxiliary_values = [
            fields[name]
            for name in ("org_type", "level", "writing_purpose")
            if _normalized(fields[name])
        ]
        auxiliary_match = (
            fmean(_field_match(value, auxiliary_haystack) for value in auxiliary_values)
            if auxiliary_values else 0.0
        )
        negative_matches = [
            _coverage(concept, title)
            for concept in negative_concepts
            if _meaningful_ngrams(concept)
        ]
        negative_title_match = max(negative_matches, default=0.0)
        negative_terms = set().union(*(
            _meaningful_ngrams(concept) for concept in negative_concepts
        )) if negative_concepts else set()
        negative_title_conflict = bool(negative_terms & _meaningful_ngrams(title))
        # Topic relevance leads: preserve the four existing topic weights.
        # Role/type/auxiliary preferences span at most 0.05 in total, so they
        # can resolve close matches but cannot overturn a larger topic gap.
        # Negative-title exclusion remains a separate, unchanged gate.
        document_score = (
            0.625 * semantic_aggregate
            + 0.180 * title_topic_match
            + 0.060 * content_topic_match
            + 0.015 * business_domain_match
            + 0.020 * source_role_compatibility
            + 0.005 * document_type_match
            + 0.005 * auxiliary_match
            - 0.350 * negative_title_match
        )
        positive_support = (
            title_topic_match >= 0.08
            or content_topic_match >= 0.10
            or semantic_aggregate >= 0.58
        )
        gate_reasons: list[str] = []
        if semantic_aggregate < RELEVANCE_THRESHOLD:
            gate_reasons.append("semantic_below_threshold")
        if negative_title_conflict:
            gate_reasons.append("negative_title_conflict")
        if not positive_support:
            gate_reasons.append("insufficient_topic_support")
        documents.append({
            "document_id": doc_id,
            "source_title": title,
            "source_file": _clean(representative.get("source_file")),
            "document_score": round(document_score, 6),
            "score_components": {
                "max_chunk_similarity": round(max_similarity, 6),
                "top3_mean_similarity": round(top3_mean, 6),
                "semantic_aggregate": round(semantic_aggregate, 6),
                "title_topic_match": round(title_topic_match, 6),
                "content_topic_match": round(content_topic_match, 6),
                "document_type_match": round(document_type_match, 6),
                "business_domain_match": round(business_domain_match, 6),
                "source_role_compatibility": round(source_role_compatibility, 6),
                "auxiliary_match": round(auxiliary_match, 6),
                "negative_title_match": round(negative_title_match, 6),
            },
            "negative_title_conflict": negative_title_conflict,
            "passed_primary_gate": not gate_reasons,
            "gate_reasons": gate_reasons,
            "candidate_chunk_count": len(chunks),
            "_requested_source_role": requested_source_role,
            "_candidate_source_role": candidate_source_role,
            "_chunks": relevance_order,
        })
    documents.sort(key=lambda item: (
        -item["document_score"],
        -item["score_components"]["semantic_aggregate"],
        -item["score_components"]["max_chunk_similarity"],
        item["source_title"],
        item["document_id"],
    ))
    for rank, document in enumerate(documents, 1):
        document["rank"] = rank
    return documents


def _select_documents(documents: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    primary = next((document for document in documents if document["passed_primary_gate"]), None)
    if primary is None:
        return None, []
    auxiliaries: list[dict[str, Any]] = []
    primary_semantic = primary["score_components"]["semantic_aggregate"]
    for document in documents:
        if document is primary or not document["passed_primary_gate"]:
            continue
        if document["score_components"]["semantic_aggregate"] < primary_semantic - MAX_AUXILIARY_SEMANTIC_GAP:
            document["gate_reasons"].append("outside_auxiliary_margin")
            continue
        components = document["score_components"]
        auxiliary_support = (
            components["title_topic_match"] >= 0.10
            or components["content_topic_match"] >= 0.12
            or components["document_type_match"] > 0
            or components["business_domain_match"] > 0
        )
        if not auxiliary_support:
            document["gate_reasons"].append("insufficient_auxiliary_support")
            continue
        if len(auxiliaries) >= MAX_AUXILIARY_DOCUMENTS:
            document["gate_reasons"].append("auxiliary_limit_reached")
            continue
        auxiliaries.append(document)
    return primary, auxiliaries


def _selected_chunk(
    candidate: Mapping[str, Any],
    document: Mapping[str, Any],
    role: str,
    raw_rank: int,
    *,
    coverage_role: str = "relevance",
    selection_reason: str = "semantic_relevance",
) -> dict[str, Any]:
    metadata = _metadata(candidate)
    if candidate.get("file_path") and "file_path" not in metadata:
        metadata["file_path"] = candidate.get("file_path")
    full_text = _clean(candidate.get("chunk_text"))
    text_limit = PRIMARY_TEXT_LIMIT if role == "primary" else AUXILIARY_TEXT_LIMIT
    source_title = _clean(candidate.get("title")) or _clean(document.get("source_title"))
    return {
        "document_id": _clean(candidate.get("doc_id")),
        "doc_id": _clean(candidate.get("doc_id")),
        "document_role": role,
        "document_rank": document["rank"],
        "document_score": document["document_score"],
        "chunk_id": _clean(candidate.get("chunk_id")) or f"{candidate.get('doc_id', '')}:{candidate.get('chunk_index', 0)}",
        "chunk_index": int(candidate.get("chunk_index", 0) or 0),
        "section_index": int(candidate.get("section_index", metadata.get("section_index", 0)) or 0),
        "section_heading": _clean(candidate.get("section_heading", metadata.get("section_heading", ""))),
        "source_file": _clean(candidate.get("source_file", metadata.get("source_file", ""))),
        "source_title": source_title,
        "source": source_title,
        "raw_rank": raw_rank,
        "raw_similarity": round(_similarity(candidate), 6),
        "score": round(_similarity(candidate), 6),
        "score_type": "vector_similarity",
        "coverage_role": coverage_role,
        "selection_reason": selection_reason,
        "metadata": metadata,
        "chunk_text": full_text,
        "text": full_text[:text_limit],
    }


def _chunk_coverage_role(candidate: Mapping[str, Any]) -> str:
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


def _primary_coverage_selection(document: Mapping[str, Any]) -> list[tuple[dict[str, Any], str, str]]:
    ranked = _rank_chunks_for_document(document)
    # Retrieval preserves document coverage.  Placeholder-bearing propositions
    # are removed at Atomic Evidence granularity so other legal facts in the
    # same chunk are not discarded with them.
    clean_ranked = ranked
    if len(clean_ranked) <= PRIMARY_MIN_COVERAGE_CHUNKS:
        return [
            (item, _chunk_coverage_role(item), "all_relevant_primary_chunks")
            for item in clean_ranked
        ]

    selected: list[tuple[dict[str, Any], str, str]] = []
    selected_keys: set[tuple[str, int]] = set()

    def add(item: dict[str, Any], coverage_role: str, reason: str) -> None:
        key = (_clean(item.get("chunk_id")), int(item.get("chunk_index", 0) or 0))
        if key in selected_keys:
            return
        selected_keys.add(key)
        selected.append((item, coverage_role, reason))

    # First secure the content functions required by a work summary.  This is
    # still relevance-bounded because every candidate belongs to the already
    # gated primary document.
    for role in ("achievement_status", "major_work", "problem", "next_step"):
        role_items = [item for item in clean_ranked if _chunk_coverage_role(item) == role]
        for item in role_items[:2]:
            add(item, role, f"structure_coverage:{role}")

    for item in clean_ranked[:PRIMARY_RELEVANCE_SEED]:
        add(item, _chunk_coverage_role(item), "semantic_relevance_seed")

    # Expand with distinct source sections before adding more same-theme hits.
    seen_sections = {
        (
            int(item.get("section_index", _metadata(item).get("section_index", 0)) or 0),
            _clean(item.get("section_heading") or _metadata(item).get("section_heading")),
        )
        for item, _role, _reason in selected
    }
    for item in clean_ranked:
        section_key = (
            int(item.get("section_index", _metadata(item).get("section_index", 0)) or 0),
            _clean(item.get("section_heading") or _metadata(item).get("section_heading")),
        )
        if section_key in seen_sections:
            continue
        add(item, _chunk_coverage_role(item), "distinct_primary_section")
        seen_sections.add(section_key)
        if len(selected) >= PRIMARY_MAX_COVERAGE_CHUNKS:
            break

    dynamic_budget = min(
        PRIMARY_MAX_COVERAGE_CHUNKS,
        max(PRIMARY_MIN_COVERAGE_CHUNKS, len(clean_ranked) - len(clean_ranked) // 3),
    )
    for item in clean_ranked:
        if len(selected) >= dynamic_budget:
            break
        add(item, _chunk_coverage_role(item), "primary_document_coverage_fill")
    return selected


def _select_chunks(
    primary: dict[str, Any],
    auxiliaries: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    *,
    work_summary: bool,
) -> list[dict[str, Any]]:
    raw_ranks = {
        (_clean(candidate.get("doc_id")), _clean(candidate.get("chunk_id")), int(candidate.get("chunk_index", 0) or 0)): rank
        for rank, candidate in enumerate(candidates, 1)
    }
    selected: list[dict[str, Any]] = []
    document_selections: list[tuple[dict[str, Any], str, list[tuple[dict[str, Any], str, str]]]] = [
        (
            primary,
            "primary",
            _primary_coverage_selection(primary)
            if work_summary
            else [
                (item, _chunk_coverage_role(item), "semantic_relevance")
                for item in _rank_chunks_for_document(primary)[:PRIMARY_RELEVANCE_SEED]
            ],
        ),
        *(
            (
                document,
                "auxiliary",
                [
                    (item, _chunk_coverage_role(item), "qualified_auxiliary_relevance")
                    for item in _rank_chunks_for_document(document)[:AUXILIARY_CHUNK_BUDGET]
                ],
            )
            for document in auxiliaries
        ),
    ]
    for document, role, chosen in document_selections:
        original_order = sorted(chosen, key=lambda entry: (
            int(entry[0].get("chunk_index", 0) or 0),
            int(entry[0].get("section_index", _metadata(entry[0]).get("section_index", 0)) or 0),
            _clean(entry[0].get("chunk_id")),
        ))
        for candidate, coverage_role, selection_reason in original_order:
            identity = (
                _clean(candidate.get("doc_id")),
                _clean(candidate.get("chunk_id")),
                int(candidate.get("chunk_index", 0) or 0),
            )
            selected.append(_selected_chunk(
                candidate,
                document,
                role,
                raw_ranks.get(identity, 0),
                coverage_role=coverage_role,
                selection_reason=selection_reason,
            ))
    return selected


def _fact_chunk_bonus(candidate: Mapping[str, Any]) -> float:
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


def _rank_chunks_for_document(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    chunks = list(document.get("_chunks") or [])
    if not (
        document.get("_requested_source_role") == "fact_source"
        and document.get("_candidate_source_role") == "fact_source"
    ):
        return chunks
    return sorted(
        chunks,
        key=lambda item: (
            -(_similarity(item) + _fact_chunk_bonus(item)),
            -_similarity(item),
            int(item.get("chunk_index", 0) or 0),
            _clean(item.get("chunk_id")),
        ),
    )


def _public_document(document: Mapping[str, Any], status: str, reason: str) -> dict[str, Any]:
    return {
        key: value
        for key, value in {
            **{name: document[name] for name in (
                "rank", "document_id", "source_title", "source_file", "document_score",
                "score_components", "negative_title_conflict", "passed_primary_gate",
                "candidate_chunk_count",
            )},
            "selection_status": status,
            "selection_reason": reason,
            "gate_reasons": list(document.get("gate_reasons", [])),
        }.items()
    }


def build_retrieval_trace(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return a source-text-free trace suitable for durable diagnostics."""

    selected = []
    for item in payload.get("selected_chunks") or []:
        if not isinstance(item, Mapping):
            continue
        selected.append({
            "chunk_id": _clean(item.get("chunk_id")),
            "chunk_index": int(item.get("chunk_index", 0) or 0),
            "section_index": int(item.get("section_index", 0) or 0),
            "source_document_id": _clean(item.get("document_id") or item.get("doc_id")),
            "document_role": _clean(item.get("document_role")),
            "raw_rank": int(item.get("raw_rank", 0) or 0),
            "score": round(_similarity(item), 6),
            "coverage_role": _clean(item.get("coverage_role")),
            "selection_reason": _clean(item.get("selection_reason")),
        })
    return {
        "trace_schema_version": "retrieval-trace-redacted-1",
        "redacted": True,
        "contains_source_text": False,
        "retrieval_status": _clean(payload.get("retrieval_status")),
        "request_time": _clean(payload.get("request_time")),
        "primary_document_id": _clean(payload.get("primary_document_id")),
        "auxiliary_document_ids": [
            _clean(value) for value in payload.get("auxiliary_document_ids") or []
        ],
        "selected_chunks": selected,
        "selected_chunk_count": len(selected),
        "ranked_documents": [
            {
                "document_id": _clean(item.get("document_id")),
                "rank": int(item.get("rank", 0) or 0),
                "document_score": float(item.get("document_score", 0.0) or 0.0),
                "selection_status": _clean(item.get("selection_status")),
                "selection_reason": _clean(item.get("selection_reason")),
            }
            for item in payload.get("ranked_documents") or []
            if isinstance(item, Mapping)
        ],
    }


def _persist_trace(payload: dict[str, Any]) -> str:
    serialized = json.dumps(build_retrieval_trace(payload), ensure_ascii=False, indent=2)
    try:
        trace_dir = get_logs_dir() / "retrieval_traces"
        trace_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        path = trace_dir / f"{stamp}_{uuid.uuid4().hex[:8]}.json"
        atomic_write_text(path, serialized, encoding="utf-8")
        return str(path)
    except OSError as exc:
        logger.warning("RAG_RETRIEVAL_TRACE_PERSIST_FAILED error_type=%s", type(exc).__name__)
        logger.info("RAG_RETRIEVAL_TRACE=%s", serialized)
        return ""


def _empty_result(prepared: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "retrieval_status": "INSUFFICIENT",
        "query_text": prepared["query_text"],
        "query_fields": prepared["query_fields"],
        "ranked_documents": [],
        "primary_document_id": None,
        "auxiliary_document_ids": [],
        "selected_chunks": [],
        "rejected_documents": [],
        "relevance_threshold": RELEVANCE_THRESHOLD,
        "insufficient_reason": reason,
        "request_time": datetime.now(timezone.utc).isoformat(),
        "positive_title": prepared["positive_title"],
        "negative_concepts": prepared["negative_concepts"],
        "raw_chunk_candidates": [],
        "gate_result": "INSUFFICIENT",
    }


def retrieve_documents(
    task: Any,
    *,
    persist_trace: bool = True,
    search_fn: Any = None,
) -> dict[str, Any]:
    """Return the locked Flow 2 document-level retrieval contract."""
    prepared = prepare_retrieval_request(task)
    reference_scope = _normalized(prepared["query_fields"].get("reference_scope"))
    disabled_scopes = {_normalized(value) for value in _DISABLED_REFERENCE_SCOPES}
    if reference_scope in disabled_scopes:
        result = _empty_result(prepared, "reference_scope_disabled")
        if persist_trace:
            result["trace_path"] = _persist_trace(result)
        return result

    request_time = datetime.now(timezone.utc).isoformat()
    vector_search = search_fn or search_detailed
    if vector_search is search_detailed:
        detailed = vector_search(
            prepared["query_text"],
            top_k=RAW_CANDIDATE_LIMIT,
            retrieval_mode="vector_only",
        )
    else:
        # Test/diagnostic injection preserves legacy two-argument fakes while
        # production remains explicitly vector_only above.
        detailed = vector_search(prepared["query_text"], top_k=RAW_CANDIDATE_LIMIT)
    candidates = list(detailed.get("results", []) or [])
    raw_candidates = [_raw_candidate(candidate, rank) for rank, candidate in enumerate(candidates, 1)]
    documents = _aggregate_documents(candidates, prepared)
    primary, auxiliaries = _select_documents(documents)

    selected_ids = set()
    if primary:
        selected_ids.add(primary["document_id"])
    selected_ids.update(document["document_id"] for document in auxiliaries)
    ranked_documents = []
    rejected_documents = []
    for document in documents:
        if primary is document:
            public = _public_document(document, "PRIMARY", "highest_ranked_document_passing_gate")
        elif document in auxiliaries:
            public = _public_document(document, "AUXILIARY", "qualified_supplemental_document")
        else:
            reasons = list(document.get("gate_reasons", []))
            if not reasons and document["document_id"] not in selected_ids:
                reasons.append("not_selected")
            public = _public_document(document, "REJECTED", ";".join(reasons))
            rejected_documents.append(public)
        ranked_documents.append(public)

    if primary is None:
        result = _empty_result(prepared, "no_document_passed_relevance_gate")
        result.update({
            "request_time": request_time,
            "ranked_documents": ranked_documents,
            "rejected_documents": rejected_documents,
            "raw_chunk_candidates": raw_candidates,
            "retrieval_diagnostics": {
                "raw_results_count": detailed.get("raw_results_count", 0),
                "filtered_samples": detailed.get("filtered_samples", []),
                "rebuilt_doc_ids": detailed.get("rebuilt_doc_ids", []),
                "retrieval_mode": detailed.get("retrieval_mode", "vector_only"),
            },
        })
    else:
        result = {
            "retrieval_status": "SUFFICIENT",
            "query_text": prepared["query_text"],
            "query_fields": prepared["query_fields"],
            "ranked_documents": ranked_documents,
            "primary_document_id": primary["document_id"],
            "auxiliary_document_ids": [document["document_id"] for document in auxiliaries],
            "selected_chunks": _select_chunks(
                primary,
                auxiliaries,
                candidates,
                work_summary=str(getattr(task, "document_type", "") or "").strip()
                == "工作总结",
            ),
            "rejected_documents": rejected_documents,
            "relevance_threshold": RELEVANCE_THRESHOLD,
            "insufficient_reason": "",
            "request_time": request_time,
            "positive_title": prepared["positive_title"],
            "negative_concepts": prepared["negative_concepts"],
            "raw_chunk_candidates": raw_candidates,
            "gate_result": "SUFFICIENT",
            "retrieval_diagnostics": {
                "raw_results_count": detailed.get("raw_results_count", 0),
                "filtered_samples": detailed.get("filtered_samples", []),
                "rebuilt_doc_ids": detailed.get("rebuilt_doc_ids", []),
                "retrieval_mode": detailed.get("retrieval_mode", "vector_only"),
            },
        }
    if persist_trace:
        result["trace_path"] = _persist_trace(result)
    return result
