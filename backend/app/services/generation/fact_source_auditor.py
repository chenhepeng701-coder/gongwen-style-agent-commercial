"""Development-only fact source audit for official_draft output.

The auditor is intentionally lightweight: it uses deterministic string
heuristics, never calls an LLM, and never changes the generated article.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any


SNIPPET_WINDOW_CHARS = 80
MAX_CLAIM_CHARS = 140

SOURCE_BASED_KEYWORDS = (
    "巡察反馈",
    "日常监督",
    "群众反映",
    "走访发现",
    "调研发现",
    "检查发现",
    "督查反馈",
    "据群众反映",
    "通报",
    "查处",
    "近年来通报的典型案例",
    "近年通报案例显示",
    "从通报案例看",
    "从近年来查处情况看",
    "从近年来监督检查情况看",
    "从近年巡察反馈看",
    "从近年日常监督看",
    "从群众反映情况看",
    "有关案例",
    "近年巡察",
    "近年来巡察",
)

IMPLICIT_SOURCE_KEYWORDS = (
    "近年来通报的典型案例",
    "近年通报案例显示",
    "从通报案例看",
    "从近年来查处情况看",
    "从近年来监督检查情况看",
    "从近年巡察反馈看",
    "从近年日常监督看",
    "从群众反映情况看",
    "从巡察反馈看",
    "从群众反映看",
    "近年来通报",
)

LOCAL_OCCURRED_KEYWORDS = (
    "有的村出现过",
    "有的村",
    "个别干部虚报冒领",
    "项目验收时有的",
    "我们镇上这几年",
    "我们镇这几年",
    "具体到我们镇",
    "我们镇",
    "本镇",
    "镇党委",
    "镇党委会",
    "镇纪委",
    "相关部门",
    "班子成员",
    "某村",
    "资金被套取",
)

LOCAL_CONTEXT_KEYWORDS = (
    "村干部",
    "项目验收",
    "工程招投标",
    "资金发放",
)

POLICY_OR_MECHANISM_KEYWORDS = (
    "四议两公开",
    "村务监督委员会",
    "容错纠错机制",
    "小微权力清单",
)

DOMAIN_GENERAL_KEYWORDS = (
    "低保评议",
    "低保评定",
    "惠农补贴发放",
    "惠农补贴",
    "惠农资金",
    "征地拆迁",
    "小型工程",
    "三资管理",
    "集体资产",
    "救灾款物",
    "救济款物",
    "危房改造",
    "项目验收",
    "小金库",
    "坐收坐支",
)

TIME_SOURCE_KEYWORDS = (
    "近年来",
    "近年",
    "去年",
    "前几天",
)

OCCURRED_MARKERS = (
    "已经",
    "出现过",
    "发生过",
    "发现",
    "反馈",
    "通报",
    "查处",
    "群众反映",
    "走访发现",
    "调研发现",
    "检查发现",
    "督查反馈",
    "巡察反馈",
    "我们镇上这几年",
    "反映",
    "出了",
    "出台了",
    "建立了",
    "完善了",
    "落实了",
    "虚报冒领",
    "套取",
    "睁一只眼闭一只眼",
)

OCCURRED_FACT_SIGNAL_KEYWORDS = (
    "近年来查处",
    "近年查处",
    "去年查处",
    "已经查处",
    "查处了",
    "查处过",
    "通报查处",
    "公开通报",
    "巡察反馈",
    "督查反馈",
    "群众反映",
    "检查发现",
    "走访发现",
    "调研发现",
    "镇纪委发现",
    "镇纪委查处了",
    "已经",
    "出现过",
    "发生过",
    "发现",
    "反馈",
    "通报",
    "反映",
    "出了",
    "出台了",
    "建立了",
    "完善了",
    "落实了",
    "虚报冒领",
    "套取",
    "睁一只眼闭一只眼",
)

OCCURRED_FACT_SIGNAL_PATTERN = re.compile(
    r"(查处\s*\d+\s*起|处分\s*\d+\s*人|问责\s*\d+\s*人)"
)

NORMATIVE_REQUIREMENT_KEYWORDS = (
    "要加强",
    "要定期",
    "要及时",
    "要坚决",
    "要严格",
    "要推动",
    "要落实",
    "要执行",
    "要带头",
    "必须严格",
    "必须落实",
    "应当加强",
    "应当及时",
    "需要加强",
    "需要防范",
)

NORMATIVE_ENFORCEMENT_KEYWORDS = (
    "要严肃查处",
    "要依规依纪处理",
    "要依法依规处理",
    "严肃处理",
    "严肃查处",
    "依规依纪处理",
    "依法依规处理",
    "该提醒的提醒",
    "该问责的问责",
    "该查处的查处",
    "及时提醒",
    "咬耳扯袖",
    "对踩红线、越底线的行为",
    "对违反纪律规矩的行为",
    "要紧盯",
)

RISK_EXPRESSION_KEYWORDS = (
    "如果",
    "一旦",
    "可能出现",
    "容易出现",
    "需要防范",
    "要防止",
    "群众关注度高",
    "重点环节",
    "薄弱环节",
    "一般表现",
    "廉政风险",
    "风险较集中",
    "公开透明",
    "不能停留在纸面上",
)

GENERALIZED_WHITELIST = (
    "从基层廉政风险看",
    "从实际工作中容易出现的情况看",
    "从群众身边不正之风的典型表现看",
    "从乡镇工作容易出现的薄弱环节看",
    "一些地方",
    "一些环节",
    "在实际工作中",
    "从日常工作看",
    "制度建设不能停留在纸面上",
)

NUMERIC_PATTERN = re.compile(
    r"(\d{4}年|\d+月\d+日|\d+月|\d+日|\d+(?:\.\d+)?\s*(?:万元|亿元|元|%|％|个|项|人|次|批|方面))"
)

SENTENCE_PATTERN = re.compile(r"[^。！？!?；;\n]+[。！？!?；;]?")
TOKEN_PATTERN = re.compile(r"[\u4e00-\u9fff]{2,}|\d+(?:\.\d+)?")


def _safe_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _normalize_source_texts(content_references: Any) -> list[dict[str, str]]:
    if content_references is None:
        return []
    if isinstance(content_references, (str, dict)):
        raw_items = [content_references]
    else:
        try:
            raw_items = list(content_references)
        except TypeError:
            raw_items = [content_references]

    normalized: list[dict[str, str]] = []
    for index, item in enumerate(raw_items, start=1):
        if isinstance(item, dict):
            text = _safe_text(item.get("text") or item.get("chunk_text") or item.get("content"))
            source = _safe_text(item.get("source") or item.get("source_title") or item.get("title"))
        else:
            text = _safe_text(item)
            source = ""
        if text:
            normalized.append({"source": source or f"content_reference_{index}", "text": text})
    return normalized


def _keyword_hits(text: str, keywords: tuple[str, ...]) -> list[str]:
    return [keyword for keyword in keywords if keyword in text]


def _numeric_hits(text: str) -> list[str]:
    return [match.group(0) for match in NUMERIC_PATTERN.finditer(text)]


def _risk_word_hits(text: str) -> list[str]:
    hits: list[str] = []
    for group in (
        SOURCE_BASED_KEYWORDS,
        LOCAL_OCCURRED_KEYWORDS,
        LOCAL_CONTEXT_KEYWORDS,
        POLICY_OR_MECHANISM_KEYWORDS,
        DOMAIN_GENERAL_KEYWORDS,
        TIME_SOURCE_KEYWORDS,
        RISK_EXPRESSION_KEYWORDS,
        NORMATIVE_ENFORCEMENT_KEYWORDS,
    ):
        hits.extend(_keyword_hits(text, group))
    hits.extend(_numeric_hits(text))
    return _dedupe_preserve(hits)


def _normative_requirement_hits(text: str) -> list[str]:
    return _dedupe_preserve(
        _keyword_hits(text, NORMATIVE_REQUIREMENT_KEYWORDS)
        + _keyword_hits(text, NORMATIVE_ENFORCEMENT_KEYWORDS)
    )


def _has_occurred_marker(text: str) -> bool:
    return bool(_keyword_hits(text, OCCURRED_MARKERS))


def _has_occurred_fact_signal(text: str) -> bool:
    return bool(
        _keyword_hits(text, TIME_SOURCE_KEYWORDS)
        or _keyword_hits(text, OCCURRED_FACT_SIGNAL_KEYWORDS)
        or OCCURRED_FACT_SIGNAL_PATTERN.search(text)
    )


def _has_local_or_supervision_signal(text: str) -> bool:
    return bool(
        _keyword_hits(text, LOCAL_OCCURRED_KEYWORDS)
        or _keyword_hits(text, SOURCE_BASED_KEYWORDS)
        or _keyword_hits(text, LOCAL_CONTEXT_KEYWORDS)
        or _keyword_hits(text, POLICY_OR_MECHANISM_KEYWORDS)
        or _keyword_hits(text, DOMAIN_GENERAL_KEYWORDS)
    )


def _is_risk_expression(text: str) -> bool:
    return bool(_keyword_hits(text, RISK_EXPRESSION_KEYWORDS))


def _is_conditional_risk_expression(text: str) -> bool:
    if _keyword_hits(text, ("如果", "一旦", "可能出现", "容易出现", "要防止", "需要防范")):
        source_or_local_hits = _keyword_hits(text, SOURCE_BASED_KEYWORDS) + _keyword_hits(text, LOCAL_OCCURRED_KEYWORDS)
        return not any(hit in text for hit in source_or_local_hits if hit not in {"个别干部虚报冒领"})
    return False


def _has_strong_specific_signal(text: str) -> bool:
    return bool(
        _keyword_hits(text, SOURCE_BASED_KEYWORDS)
        or _keyword_hits(text, LOCAL_OCCURRED_KEYWORDS)
        or NUMERIC_PATTERN.search(text)
    )


def _is_generalized_expression(text: str) -> bool:
    if _has_strong_specific_signal(text):
        return False
    if _keyword_hits(text, DOMAIN_GENERAL_KEYWORDS):
        return False
    return any(phrase in text for phrase in GENERALIZED_WHITELIST) or (
        _is_risk_expression(text) and not _has_occurred_marker(text)
    )


def _claim_type_for(text: str) -> str:
    if NUMERIC_PATTERN.search(text):
        return "hard_numeric_fact"
    if _normative_requirement_hits(text) and not _has_occurred_fact_signal(text):
        return "normative_requirement"
    if _is_conditional_risk_expression(text):
        return "conditional_risk_expression"
    if _keyword_hits(text, IMPLICIT_SOURCE_KEYWORDS):
        return "implicit_source_claim"
    if _keyword_hits(text, SOURCE_BASED_KEYWORDS) or _keyword_hits(text, TIME_SOURCE_KEYWORDS):
        return "implicit_source_claim"
    if _keyword_hits(text, LOCAL_OCCURRED_KEYWORDS):
        return "occurred_local_fact"
    if _keyword_hits(text, LOCAL_CONTEXT_KEYWORDS) and _has_occurred_marker(text):
        return "occurred_local_fact"
    if _keyword_hits(text, POLICY_OR_MECHANISM_KEYWORDS):
        return "generalized_expression"
    if _is_generalized_expression(text):
        return "generalized_expression"
    if _keyword_hits(text, DOMAIN_GENERAL_KEYWORDS):
        return "generalized_expression"
    return "fact_like_claim"


def _rewrite_hint_for(claim_text: str, claim_type: str, risk_level: str) -> str:
    if risk_level == "LOW":
        return "已命中来源或属于稳妥泛化表达，原则上不改。"
    if claim_type == "occurred_local_fact":
        return "将已发生、本地化事实改为重点工作、风险防范或规范执行表达；不要保留“当前、我们镇、去年、有的村出现过”等无来源事实口吻。"
    if claim_type == "implicit_source_claim":
        return "去掉暗含来源口吻，改为“一些基层案例”“基层廉政风险的一般表现”等稳妥泛化表达。"
    if claim_type == "hard_numeric_fact":
        return "数字、时间、金额、百分比等硬事实必须来自用户输入或内容参考；无来源时删除具体数字或改为定性表达。"
    if claim_type == "conditional_risk_expression":
        return "保留条件风险表达，必要时仅调整为更规范的风险防范句式。"
    if claim_type == "normative_requirement":
        return "保留规范性工作要求，不写成已经发生的事实。"
    if claim_type == "generalized_expression":
        return "保留泛化机关表达，避免补入具体事实。"
    return "建议人工复核，必要时改为风险表达、规范性要求或泛化表达。"


def _dedupe_preserve(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _split_sentences(text: Any) -> list[str]:
    content = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    sentences: list[str] = []
    for line in content.split("\n"):
        clean_line = _safe_text(line)
        if not clean_line:
            continue
        for match in SENTENCE_PATTERN.finditer(clean_line):
            sentence = _safe_text(match.group(0)).strip(" ，,、")
            if sentence:
                sentences.append(sentence)
    return sentences


def _trim_claim(sentence: str) -> str:
    sentence = _safe_text(sentence).strip(" ，,、")
    if len(sentence) <= MAX_CLAIM_CHARS:
        return sentence
    anchors = _risk_word_hits(sentence)
    if not anchors:
        return sentence[:MAX_CLAIM_CHARS].rstrip(" ，,、")
    positions = [sentence.find(anchor) for anchor in anchors if sentence.find(anchor) >= 0]
    start = max(0, min(positions) - 30)
    end = min(len(sentence), max(positions) + len(anchors[-1]) + 70)
    return sentence[start:end].strip(" ，,、")


def _tokens(text: str) -> set[str]:
    tokens = {token for token in TOKEN_PATTERN.findall(text) if len(token) >= 2}
    known_phrases = (
        SOURCE_BASED_KEYWORDS
        + LOCAL_OCCURRED_KEYWORDS
        + LOCAL_CONTEXT_KEYWORDS
        + POLICY_OR_MECHANISM_KEYWORDS
        + DOMAIN_GENERAL_KEYWORDS
        + TIME_SOURCE_KEYWORDS
    )
    tokens.update(phrase for phrase in known_phrases if phrase in text)
    stop_words = {"我们", "主要", "方面", "工作", "情况", "问题", "干部", "乡镇", "廉政党课"}
    return {token for token in tokens if token not in stop_words}


def _similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    if left in right or right in left:
        return 1.0
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _claim_quality(text: str) -> tuple[int, int, int]:
    has_end = int(text.endswith(("。", "！", "？", "；", "!", "?", ";")))
    return (len(_risk_word_hits(text)), has_end, len(text))


def _dedupe_claims(claims: list[dict[str, str]]) -> list[dict[str, str]]:
    deduped: list[dict[str, str]] = []
    for claim in claims:
        current_text = claim["claim_text"]
        duplicate_index: int | None = None
        for index, existing in enumerate(deduped):
            existing_text = existing["claim_text"]
            if _similarity(current_text, existing_text) >= 0.72:
                duplicate_index = index
                break
        if duplicate_index is None:
            deduped.append(claim)
            continue
        if _claim_quality(current_text) > _claim_quality(deduped[duplicate_index]["claim_text"]):
            deduped[duplicate_index] = claim
    return deduped


def extract_fact_like_claims(text: Any) -> list[dict[str, str]]:
    """Extract fact-like snippets that may need source support."""
    if text is None:
        return []

    claims: list[dict[str, str]] = []
    seen: set[str] = set()
    for sentence in _split_sentences(text):
        if not _risk_word_hits(sentence):
            continue
        claim_text = _trim_claim(sentence)
        if not claim_text or claim_text in seen:
            continue
        seen.add(claim_text)
        claims.append({"claim_text": claim_text, "claim_type": _claim_type_for(claim_text)})

    return _dedupe_claims(claims)


def _source_snippet(source_text: str, claim_text: str) -> str:
    direct_pos = source_text.find(claim_text)
    if direct_pos >= 0:
        start = max(0, direct_pos - 20)
        end = min(len(source_text), direct_pos + len(claim_text) + 60)
        return source_text[start:end].strip()

    claim_tokens = _tokens(claim_text)
    positions = [source_text.find(token) for token in claim_tokens if source_text.find(token) >= 0]
    if positions:
        start = max(0, min(positions) - 20)
        end = min(len(source_text), start + SNIPPET_WINDOW_CHARS)
        return source_text[start:end].strip()
    return source_text[:SNIPPET_WINDOW_CHARS].strip()


def _match_in_sources(claim: str, sources: list[dict[str, str]], source_type: str) -> dict[str, str] | None:
    claim_text = _safe_text(claim)
    if not claim_text:
        return None

    for source in sources:
        text = source["text"]
        if claim_text and claim_text in text:
            return {
                "source_status": "来自用户输入" if source_type == "user_input" else "命中内容参考",
                "matched_source_type": source_type,
                "matched_source_name": source["source"],
                "matched_source_snippet": _source_snippet(text, claim_text),
                "match_method": "exact",
            }

    claim_tokens = _tokens(claim_text)
    if not claim_tokens:
        return None

    best: tuple[int, dict[str, str]] | None = None
    for source in sources:
        text = source["text"]
        source_tokens = _tokens(text)
        overlap = claim_tokens & source_tokens
        strong_phrase_hit = any(token in text for token in claim_tokens if len(token) >= 4)
        enough_overlap = len(overlap) >= 2 or (len(overlap) >= 1 and strong_phrase_hit)
        if not enough_overlap:
            continue
        score = len(overlap) + (1 if strong_phrase_hit else 0)
        record = {
            "source_status": "来自用户输入" if source_type == "user_input" else "命中内容参考",
            "matched_source_type": source_type,
            "matched_source_name": source["source"],
            "matched_source_snippet": _source_snippet(text, claim_text),
            "match_method": "keyword_overlap",
        }
        if best is None or score > best[0]:
            best = (score, record)

    return best[1] if best else None


def match_claim_to_sources(claim: Any, user_input_texts: Any, content_references: Any) -> dict[str, str]:
    """Match one claim against user input first, then content references."""
    claim_text = claim.get("claim_text", "") if isinstance(claim, dict) else _safe_text(claim)

    if isinstance(user_input_texts, (str, dict)) or user_input_texts is None:
        user_items = [user_input_texts] if user_input_texts is not None else []
    else:
        try:
            user_items = list(user_input_texts)
        except TypeError:
            user_items = [user_input_texts]
    user_sources = [
        {"source": f"user_input_{index}", "text": _safe_text(item)}
        for index, item in enumerate(user_items, start=1)
        if _safe_text(item)
    ]

    user_match = _match_in_sources(claim_text, user_sources, "user_input")
    if user_match:
        return user_match

    content_match = _match_in_sources(claim_text, _normalize_source_texts(content_references), "content_reference")
    if content_match:
        return content_match

    if _is_generalized_expression(claim_text):
        return {
            "source_status": "泛化表达，无需强事实来源",
            "matched_source_type": "none",
            "matched_source_name": "",
            "matched_source_snippet": "",
            "match_method": "generalized_whitelist",
        }

    return {
        "source_status": "未找到来源",
        "matched_source_type": "none",
        "matched_source_name": "",
        "matched_source_snippet": "",
        "match_method": "none",
    }


def _format_hits(hits: list[str]) -> str:
    return "、".join(hits) if hits else "无"


def classify_claim_risk(claim: Any, source_status: Any) -> dict[str, Any]:
    """Classify source risk for one claim."""
    claim_text = claim.get("claim_text", "") if isinstance(claim, dict) else _safe_text(claim)
    claim_type = claim.get("claim_type", _claim_type_for(claim_text)) if isinstance(claim, dict) else _claim_type_for(claim_text)
    status_text = source_status.get("source_status", "") if isinstance(source_status, dict) else _safe_text(source_status)
    risk_hits = _risk_word_hits(claim_text)
    normative_hits = _normative_requirement_hits(claim_text)
    hit_text = _format_hits(risk_hits)
    normative_hit_text = _format_hits(normative_hits)

    if status_text in {"来自用户输入", "命中内容参考"}:
        return {
            "risk_level": "LOW",
            "reason": f"{status_text}；命中风险词：{hit_text}。",
            "risk_words": risk_hits,
            "normative_words": normative_hits,
        }

    if normative_hits and not _has_occurred_fact_signal(claim_text):
        if _has_local_or_supervision_signal(claim_text):
            return {
                "risk_level": "MEDIUM",
                "reason": (
                    f"命中风险词：{hit_text}。命中规范性词：{normative_hit_text}。"
                    "包含本地机构或监督事项，但句子为规范性工作要求，未写成已发生事实，因此不按 HIGH 处理。"
                ),
                "risk_words": risk_hits,
                "normative_words": normative_hits,
            }
        return {
            "risk_level": "LOW",
            "reason": (
                f"命中风险词：{hit_text}。命中规范性词：{normative_hit_text}。"
                "属于泛化工作要求，未出现已发生事实信号，因此不按 HIGH 处理。"
            ),
            "risk_words": risk_hits,
            "normative_words": normative_hits,
        }

    if status_text == "泛化表达，无需强事实来源":
        return {
            "risk_level": "LOW",
            "reason": f"{status_text}；命中风险词：{hit_text}。",
            "risk_words": risk_hits,
            "normative_words": normative_hits,
        }

    if claim_type == "hard_numeric_fact":
        return {
            "risk_level": "HIGH",
            "reason": f"命中风险词：{hit_text}。包含数字、日期、金额或百分比等硬事实，但未在用户输入或内容参考中命中。",
            "risk_words": risk_hits,
            "normative_words": normative_hits,
        }
    if claim_type == "implicit_source_claim":
        return {
            "risk_level": "HIGH",
            "reason": f"命中风险词：{hit_text}。包含暗含来源表述，但未命中来源。",
            "risk_words": risk_hits,
            "normative_words": normative_hits,
        }
    if claim_type == "occurred_local_fact":
        return {
            "risk_level": "HIGH",
            "reason": f"命中风险词：{hit_text}。包含本地具体或已发生事实表述，但未命中来源。",
            "risk_words": risk_hits,
            "normative_words": normative_hits,
        }
    if claim_type == "conditional_risk_expression":
        return {
            "risk_level": "MEDIUM",
            "reason": f"命中风险词：{hit_text}。属于条件风险表达，未直接写成已发生事实，因此不按 HIGH 处理。",
            "risk_words": risk_hits,
            "normative_words": normative_hits,
        }
    if claim_type == "normative_requirement":
        return {
            "risk_level": "MEDIUM",
            "reason": f"命中风险词：{hit_text}。属于规范性工作要求，未直接写成已发生事实，因此不按 HIGH 处理。",
            "risk_words": risk_hits,
            "normative_words": normative_hits,
        }
    if claim_type == "generalized_expression":
        return {
            "risk_level": "LOW",
            "reason": f"命中风险词：{hit_text}。属于稳妥泛化表达。",
            "risk_words": risk_hits,
            "normative_words": normative_hits,
        }
    return {
        "risk_level": "MEDIUM",
        "reason": f"命中风险词：{hit_text}。事实相似片段未命中来源，建议人工复核。",
        "risk_words": risk_hits,
        "normative_words": normative_hits,
    }


def _task_user_inputs(task: Any) -> list[str]:
    fields = (
        "title",
        "topic",
        "document_type",
        "level",
        "org_type",
        "business_domain",
        "writing_purpose",
        "reference_scope",
    )
    return [_safe_text(getattr(task, field, "")) for field in fields if _safe_text(getattr(task, field, ""))]


def build_fact_source_audit(final_content: Any, task: Any, content_references: Any) -> dict[str, Any]:
    """Build a structured development audit result."""
    claims = extract_fact_like_claims(final_content)
    user_inputs = _task_user_inputs(task)
    items: list[dict[str, Any]] = []

    for claim in claims:
        match = match_claim_to_sources(claim, user_inputs, content_references)
        risk = classify_claim_risk(claim, match)
        items.append(
            {
                "fact_text": claim["claim_text"],
                "claim_text": claim["claim_text"],
                "claim_type": claim["claim_type"],
                "source_status": match["source_status"],
                "risk_level": risk["risk_level"],
                "matched_source": match["matched_source_name"],
                "matched_excerpt": match["matched_source_snippet"],
                "matched_source_type": match["matched_source_type"],
                "matched_source_name": match["matched_source_name"],
                "matched_source_snippet": match["matched_source_snippet"],
                "risk_words": risk["risk_words"],
                "normative_words": risk.get("normative_words", []),
                "rewrite_hint": _rewrite_hint_for(
                    claim["claim_text"],
                    claim["claim_type"],
                    risk["risk_level"],
                ),
                "reason": risk["reason"],
            }
        )

    counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for item in items:
        counts[item["risk_level"]] += 1

    return {
        "document_type": _safe_text(getattr(task, "document_type", "")),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "content_reference_count": len(_normalize_source_texts(content_references)),
        "claims_total": len(items),
        "high_risk_count": counts["HIGH"],
        "medium_risk_count": counts["MEDIUM"],
        "low_risk_count": counts["LOW"],
        "items": items,
    }


def _render_items(title: str, items: list[dict[str, Any]]) -> list[str]:
    lines = [f"## {title}"]
    if not items:
        lines.append("无。")
        return lines
    for index, item in enumerate(items, start=1):
        source_name = item.get("matched_source_name") or "无"
        snippet = item.get("matched_source_snippet") or "无"
        risk_words = _format_hits(list(item.get("risk_words") or []))
        normative_words = _format_hits(list(item.get("normative_words") or []))
        lines.extend(
            [
                f"### {index}. {item['claim_text']}",
                f"- 事实片段：{item['claim_text']}",
                f"- 片段类型：{item['claim_type']}",
                f"- 风险等级：{item['risk_level']}",
                f"- 来源状态：{item['source_status']}",
                f"- 命中来源：{source_name}",
                f"- 命中片段：{snippet}",
                f"- 命中风险词：{risk_words}",
                f"- 命中规范性词：{normative_words}",
                f"- 改写提示：{item.get('rewrite_hint') or '无'}",
                f"- 判断理由：{item['reason']}",
            ]
        )
    return lines


def render_fact_source_audit_markdown(audit_result: Any) -> str:
    """Render a Markdown sidecar report for development review."""
    result = audit_result if isinstance(audit_result, dict) else {}
    items = list(result.get("items", []) or [])
    high_items = [item for item in items if item.get("risk_level") == "HIGH"]
    medium_items = [item for item in items if item.get("risk_level") == "MEDIUM"]
    low_items = [item for item in items if item.get("risk_level") == "LOW"]

    lines = [
        "# official_draft 事实来源开发期审计报告",
        "",
        f"- 文种：{result.get('document_type') or '未指定'}",
        f"- 生成时间：{result.get('generated_at') or ''}",
        f"- 内容参考数量：{result.get('content_reference_count', 0)}",
        f"- 审计片段总数：{result.get('claims_total', 0)}",
        f"- 高风险数量：{result.get('high_risk_count', 0)}",
        f"- 中风险数量：{result.get('medium_risk_count', 0)}",
        f"- 低风险数量：{result.get('low_risk_count', 0)}",
        "",
    ]
    lines.extend(_render_items("高风险片段列表", high_items))
    lines.append("")
    lines.extend(_render_items("中风险片段列表", medium_items))
    lines.append("")
    lines.extend(_render_items("低风险片段列表", low_items))
    lines.append("")
    return "\n".join(lines)
