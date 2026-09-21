"""Fact-free writing references from the existing local RAG index.

No index/import, embedding request, raw prose in model guidance, or fact binding.
The selected sources and observations stay local; only closed-vocabulary
features cross the Planner/Writer boundary.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from statistics import median
from typing import Any, Mapping

from app.services.retrieval.document_retriever import _DISABLED_REFERENCE_SCOPES
from app.services.retrieval.retriever import search_detailed

FEATURES = {
    "TOPIC_THEN_DETAIL": "先用简洁主题句组织本段，再展开不同的具体信息；结尾不重复主题句。",
    "ACTION_PROGRESS": "将同主题事实按对象、动作、进度或结果组织；只呈现证据中已有的环节，不补造闭环。",
    "PROBLEM_GROUPS": "问题按实际约束归组，先说明问题所在，再交代不同表现；不推断材料没有的原因和后果。",
    "RESPONSE_GROUPS": "下一步按对应问题或未完成事项组织动作，保持计划状态；无对应依据时不得声称已回应。",
    "QUALIFIED_PROGRESS": "把时间、范围、数量语境与相应动作放在一起，保留部分完成和未完成等限定。",
    "DIRECT_TRANSITION": "围绕主题自然转接下一项新信息，不加万能意义总结句。",
}


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def select_writing_references(task: Any, *, search_fn: Any = None,
                              excluded_document_ids: tuple[str, ...] = ()) -> dict[str, Any]:
    """Use the existing BM25 channel: no extra external call or second RAG."""
    scope = str(getattr(task, "reference_scope", "") or "").strip()
    result: dict[str, Any] = {
        "version": "writing-reference-1", "source_role": "WRITING_REFERENCE",
        "status": "EMPTY", "candidate_document_count": 0, "selected": [],
        "external_calls": 0, "retrieval_channel": "existing_bm25_only",
        "reference_scope": scope,
    }
    if scope.casefold() in {v.casefold() for v in _DISABLED_REFERENCE_SCOPES} or scope in {"仅内容参考", "内容参考"}:
        result["status"] = "DISABLED"
        return result
    if getattr(task, "document_type", "") != "工作总结":
        return result
    query = " ".join(str(getattr(task, k, "") or "") for k in
                     ("title", "document_type", "business_domain", "writing_purpose"))
    retrieved = (search_fn or search_detailed)(query, top_k=200, retrieval_mode="bm25_only")
    documents: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in retrieved.get("results", []):
        meta = row.get("metadata") or {}
        doc_id = str(row.get("doc_id") or meta.get("document_id") or "")
        if not doc_id or doc_id in excluded_document_ids:
            continue
        if meta.get("document_type", row.get("document_type")) != "工作总结":
            continue
        if meta.get("content_reference_type") not in {"style", "both"}:
            continue
        if not str(row.get("chunk_text") or "").strip():
            continue
        documents[doc_id].append(row)
    result["candidate_document_count"] = len(documents)
    ranked = sorted(documents, key=lambda did: (-max(float(r.get("score") or 0) for r in documents[did]), did))
    for did in ranked[:3]:
        # Preserve document order after ranking documents; no full-document prompt.
        chunks = sorted(documents[did], key=lambda r: int(r.get("chunk_index") or 0))[:12]
        lines = [line.strip() for r in chunks for line in r["chunk_text"].splitlines() if line.strip()]
        text = "\n".join(lines)
        observations: dict[str, list[int]] = {}
        patterns = {
            "TOPIC_THEN_DETAIL": r"^(?:[（(][一二三四五六七八九十]+[）)]|[一二三四五六七八九十]+是)",
            "ACTION_PROGRESS": r"(?:开展|推进|组织|落实|建设).*(?:完成|累计|截至|达到|建成)",
            "PROBLEM_GROUPS": r"(?:问题|不足|短板|差距|有待)",
            "RESPONSE_GROUPS": r"(?:下一步|下半年|工作思路|计划|拟|将继续)",
            "QUALIFIED_PROGRESS": r"(?:截至|部分|尚未|累计|每年|年底|\d{4}年)",
            "DIRECT_TRANSITION": r"(?:同时|围绕|针对|在此基础上)",
        }
        for code, pattern in patterns.items():
            hits = [i for i, line in enumerate(lines) if re.search(pattern, line)]
            if hits:
                observations[code] = hits
        if not observations:
            continue
        prose_lengths = [len(v) for v in lines if len(v) > 40]
        result["selected"].append({
            "reference_id": "WR-" + did, "document_id": did,
            "title": chunks[0].get("title", ""),
            "source_file": (chunks[0].get("metadata") or {}).get("source_file", ""),
            "source_digest": _digest([{"id": r.get("chunk_id"), "text": r["chunk_text"]} for r in chunks]),
            "chunk_ids": [r.get("chunk_id") for r in chunks],
            "metadata_uses": sorted({(r.get("metadata") or {})["content_reference_type"] for r in chunks}),
            "feature_codes": list(observations), "observation_line_indices": observations,
            "paragraph_chars_median": median(prose_lengths) if prose_lengths else 0,
            "sentence_chars_median": median([len(v) for v in re.split(r"[。！？]", text) if len(v) > 10] or [0]),
        })
    result["status"] = "SELECTED" if result["selected"] else "EMPTY"
    result["selection_digest"] = _digest(result["selected"])
    return result


def reference_guidance(pack: Mapping[str, Any], role: str) -> list[dict[str, Any]]:
    """Rebuild wording from code-owned constants; never trust source prose."""
    role_codes = {"PROBLEM_GROUPS"} if role in {"PROBLEM", "problem_analysis"} else (
        {"RESPONSE_GROUPS"} if role in {"NEXT_STEP", "next_step_arrangement"} else {"ACTION_PROGRESS"})
    relevant = role_codes | {"TOPIC_THEN_DETAIL", "QUALIFIED_PROGRESS", "DIRECT_TRANSITION"}
    result: dict[str, dict[str, Any]] = {}
    for source in pack.get("selected", []):
        for code in source.get("feature_codes", []):
            if code in relevant and code in FEATURES:
                row = result.setdefault(code, {"reference_ids": [], "feature_code": code,
                                               "instruction": FEATURES[code]})
                row["reference_ids"].append(source["reference_id"])
    return list(result.values())
