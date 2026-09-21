"""Lightweight structure checks for official_draft output."""

from __future__ import annotations

import re
from typing import Any


HEADING_PATTERN = re.compile(r"^\s*((?:[一二三四五六七八九十]+、.+)|(?:第[一二三四五六七八九十]+[，,、].+))\s*$")


def _paragraphs(text: Any) -> list[str]:
    return [line.strip() for line in str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n") if line.strip()]


def _has_salutation(paragraphs: list[str]) -> bool:
    return any(p.endswith(("：", ":")) and len(p) <= 12 for p in paragraphs[:5]) or any(
        p in {"同志们：", "同志们:", "各位领导、同志们：", "各位同志："} for p in paragraphs[:5]
    )


def _heading_list(paragraphs: list[str]) -> list[str]:
    return [p for p in paragraphs if HEADING_PATTERN.match(p) and len(p) <= 80]


def _has_conclusion(paragraphs: list[str]) -> bool:
    if not paragraphs:
        return False
    tail = "\n".join(paragraphs[-3:])
    markers = ("最后", "同志们", "总之", "下一步", "让我们", "希望大家", "以上")
    return any(marker in tail for marker in markers) or len(paragraphs[-1]) >= 30


def _long_paragraph_ratio(paragraphs: list[str]) -> float:
    body = [p for p in paragraphs if not HEADING_PATTERN.match(p)]
    if not body:
        return 1.0
    return sum(1 for p in body if len(p) > 280) / len(body)


def _score_from_warnings(warnings: list[str]) -> int:
    return max(0, 100 - len(warnings) * 18)


def validate_structure(text: str, document_type: str = "", title: str | None = None) -> dict[str, Any]:
    paragraphs = _paragraphs(text)
    headings = _heading_list(paragraphs)
    has_salutation = _has_salutation(paragraphs)
    has_conclusion = _has_conclusion(paragraphs)
    heading_count = len(headings)
    doc_type = (document_type or "").strip()
    warnings: list[str] = []

    if len(paragraphs) < 4:
        warnings.append("正文段落过少，可能不是完整公文初稿。")
    if _long_paragraph_ratio(paragraphs) >= 0.65 and len(paragraphs) <= 8:
        warnings.append("连续大段落比例较高，存在散文化或层次不足风险。")

    if doc_type == "党课讲稿":
        if not has_salutation:
            warnings.append("党课讲稿缺少称呼，如“同志们：”。")
        if heading_count < 2:
            warnings.append("党课讲稿一级标题数量不足，存在结构退化风险。")
        if heading_count > 5:
            warnings.append("党课讲稿一级标题过多，层次可能发散。")
        if not has_conclusion:
            warnings.append("党课讲稿缺少收束性结尾段。")
    elif doc_type == "领导讲话":
        if not has_salutation and paragraphs and not any(word in paragraphs[0] for word in ("今天", "这次", "刚才")):
            warnings.append("领导讲话缺少称呼或开场。")
        if heading_count < 2:
            warnings.append("领导讲话分层结构不足。")
        if heading_count > 4:
            warnings.append("领导讲话一级层次偏多。")
        if not has_conclusion:
            warnings.append("领导讲话缺少收束性结尾。")
    elif doc_type == "工作汇报":
        layer_words = ("工作进展", "主要做法", "问题", "不足", "下一步", "打算", "措施")
        if not any(word in text for word in layer_words) and heading_count < 2:
            warnings.append("工作汇报缺少进展、问题、下一步等基本层次。")
    elif doc_type == "工作总结":
        layer_words = ("成效", "主要做法", "问题", "不足", "下步", "下一步", "方向")
        if not any(word in text for word in layer_words) and heading_count < 2:
            warnings.append("工作总结缺少成效、做法、问题、下步方向等层次。")
    elif doc_type == "调研报告":
        layer_words = ("基本情况", "问题", "原因", "对策", "建议")
        if sum(1 for word in layer_words if word in text) < 2 and heading_count < 2:
            warnings.append("调研报告缺少基本情况、问题分析、原因分析、对策建议等逻辑层次。")

    score = _score_from_warnings(warnings)
    is_valid = score >= 70 and not any("一级标题数量不足" in warning or "分层结构不足" in warning for warning in warnings)
    report = {
        "document_type": doc_type,
        "title": title or "",
        "has_salutation": has_salutation,
        "heading_count": heading_count,
        "heading_list": headings,
        "paragraph_count": len(paragraphs),
        "has_conclusion": has_conclusion,
        "warnings": warnings,
        "is_valid": is_valid,
        "score": score,
    }
    return {
        "is_valid": is_valid,
        "score": score,
        "warnings": warnings,
        "structure_report": report,
    }


def render_structure_report_markdown(structure_result: Any) -> str:
    result = structure_result if isinstance(structure_result, dict) else {}
    report = result.get("structure_report", result) if isinstance(result, dict) else {}
    lines = [
        "# official_draft 结构校验报告",
        "",
        f"- 文种：{report.get('document_type') or '未指定'}",
        f"- 是否通过：{report.get('is_valid')}",
        f"- 得分：{report.get('score', 0)}",
        f"- 是否有称呼：{report.get('has_salutation')}",
        f"- 一级标题数量：{report.get('heading_count', 0)}",
        f"- 段落数量：{report.get('paragraph_count', 0)}",
        f"- 是否有结尾段：{report.get('has_conclusion')}",
        "",
        "## 一级标题",
    ]
    headings = report.get("heading_list") or []
    lines.extend([f"- {heading}" for heading in headings] or ["无。"])
    lines.extend(["", "## 警告"])
    lines.extend([f"- {warning}" for warning in report.get("warnings", [])] or ["无。"])
    lines.append("")
    return "\n".join(lines)
