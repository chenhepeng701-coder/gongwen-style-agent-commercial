"""Lightweight content/style split for official_draft RAG references."""

from __future__ import annotations

import re
from typing import Any


MAX_REFERENCE_ITEM_CHARS = 700
MAX_REFERENCE_BLOCK_CHARS = 5000
MAX_REFERENCE_ITEMS = 6

CONTENT_REFERENCE_KEYWORDS = (
    "项目",
    "资金",
    "名单",
    "问题",
    "整改",
    "反馈",
    "群众",
    "走访",
    "调研",
    "检查",
    "巡察",
    "督查",
    "台账",
    "清单",
    "机制",
    "措施",
    "成效",
    "案例",
    "风险点",
    "工作进展",
    "制度",
    "落实",
)

STYLE_REFERENCE_KEYWORDS = (
    "一、",
    "二、",
    "三、",
    "首先",
    "其次",
    "再次",
    "最后",
    "要提高站位",
    "提高站位",
    "要压实责任",
    "压实责任",
    "要狠抓落实",
    "狠抓落实",
    "讲话",
    "汇报",
    "总结",
    "标题",
    "结尾",
    "段落",
)

FACT_PATTERN = re.compile(
    r"(\d+|[一二三四五六七八九十]+[年月日]|[0-9]{4}年|万元|亿元|%|％|第[一二三四五六七八九十\d]+)"
)


def truncate_reference_text(text: Any, max_chars: int = MAX_REFERENCE_ITEM_CHARS) -> str:
    """Normalize and truncate one reference chunk safely."""
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    if max_chars <= 0 or len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 1].rstrip() + "…"


def _as_reference_data(text: str) -> str:
    """Wrap retrieved text as inert reference data before prompt assembly."""
    value = truncate_reference_text(text)
    if not value:
        return ""
    value = value.replace("```", "` ` `")
    return f"<reference_data>{value}</reference_data>"


def _extract_text(item: Any) -> str:
    if isinstance(item, dict):
        return truncate_reference_text(item.get("text") or item.get("chunk_text") or item.get("content") or "")
    return truncate_reference_text(item)


def _extract_source(item: Any, index: int) -> str:
    if not isinstance(item, dict):
        return f"资料{index}"
    source = str(item.get("source") or item.get("source_title") or item.get("title") or "").strip()
    return source or f"资料{index}"


def classify_rag_chunk(text: Any) -> dict[str, bool]:
    """Classify one RAG chunk with simple heuristics.

    Unknown chunks are treated as style-only, so they cannot become strong fact sources.
    """
    value = truncate_reference_text(text, max_chars=2000)
    if not value:
        return {"content": False, "style": False}

    has_content_signal = bool(FACT_PATTERN.search(value)) or any(
        keyword in value for keyword in CONTENT_REFERENCE_KEYWORDS
    )
    has_style_signal = any(keyword in value for keyword in STYLE_REFERENCE_KEYWORDS)

    return {
        "content": has_content_signal,
        "style": has_style_signal or not has_content_signal,
    }


def split_rag_references(rag_items: Any) -> dict[str, list[dict[str, str]]]:
    """Split current RAG results into content and style references without changing retrieval."""
    if rag_items is None:
        items: list[Any] = []
    elif isinstance(rag_items, str):
        items = [rag_items]
    elif isinstance(rag_items, dict):
        items = [rag_items]
    else:
        try:
            items = list(rag_items)
        except TypeError:
            items = [rag_items]

    content_references: list[dict[str, str]] = []
    style_references: list[dict[str, str]] = []

    for index, item in enumerate(items, start=1):
        text = _as_reference_data(_extract_text(item))
        if not text:
            continue

        source = _extract_source(item, index)
        record = {"source": source, "text": text}
        classification = classify_rag_chunk(text)

        if classification["content"] and len(content_references) < MAX_REFERENCE_ITEMS:
            content_references.append(record)
        if classification["style"] and len(style_references) < MAX_REFERENCE_ITEMS:
            style_references.append(record)

    return {
        "content_references": content_references,
        "style_references": style_references,
    }


def _format_reference_items(items: list[dict[str, str]], empty_text: str) -> str:
    if not items:
        return empty_text
    lines: list[str] = []
    for index, item in enumerate(items, start=1):
        lines.append(f"{index}. 来源：{item['source']}\n   摘要：{item['text']}")
    return "\n".join(lines)


def build_rag_reference_block(
    content_references: list[dict[str, str]] | None,
    style_references: list[dict[str, str]] | None,
) -> str:
    """Build the prompt block used by official_draft."""
    content_items = list(content_references or [])[:MAX_REFERENCE_ITEMS]
    style_items = list(style_references or [])[:MAX_REFERENCE_ITEMS]

    block = (
        "【RAG 资料轻量分离】\n"
        "【资料使用规则】\n"
        "- 事实只能来自用户输入或内容参考。\n"
        "- 正文中的具体事实、数据、日期、会议、走访、巡察反馈、群众反映、项目、资金、单位名称，只能来自用户输入或内容参考。\n"
        "- 写法参考不能作为事实来源，不能把写法参考里的事实当成本次事实。\n"
        "- 写法参考不能改变 document_type；本次最终文种必须服从 document_type。\n"
        "- 如果写法参考体裁与本次 document_type 不一致，只能借表达、节奏和标题方式，不得照搬其结构。\n"
        "- 不得虚构走访、巡察反馈、群众反映、项目、资金、日期、数字，不得虚构会议部署、项目验收、资金套取、某村某单位等具体情节。\n"
        "- 资料不足不得编造事实；只能使用稳妥概括表述，如从基层廉政风险看、从日常监督情况看、从一些典型问题看、从实际工作中容易出现的情况看。\n"
        "- 不得输出【待补】，不得编造占位事实，不得写根据资料显示、根据参考资料等无来源表述。\n"
        "- 最终正文不得暴露 RAG、检索、资料库、内容参考、写法参考等系统词。\n\n"
        "【内容参考】\n"
        f"{_format_reference_items(content_items, '无。没有内容参考支撑时，不得写确定的具体事实。')}\n\n"
        "【写法参考】\n"
        f"{_format_reference_items(style_items, '无。没有写法参考时，按 document_type 和通用公文规则生成。')}"
    )
    return truncate_reference_text(block, MAX_REFERENCE_BLOCK_CHARS)
