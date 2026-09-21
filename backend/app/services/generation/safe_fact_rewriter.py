"""Deterministic local fact-risk reduction for official_draft output."""

from __future__ import annotations

import re
from typing import Any


LOCAL_REWRITABLE_TYPES = {"occurred_local_fact", "implicit_source_claim", "normative_requirement"}
LOCAL_UNSUPPORTED_FACT_REPLACEMENT = "相关情况需结合实际进一步核实。"

OCCURRED_ENFORCEMENT_FACT_PATTERN = re.compile(
    r"(近年来查处|近年查处|去年查处|已经查处|查处了|查处过|通报查处|公开通报|巡察反馈|督查反馈|群众反映|检查发现|走访发现|调研发现|镇纪委发现|镇纪委查处了|查处\s*\d+\s*起|处分\s*\d+\s*人|问责\s*\d+\s*人)"
)
DATE_RE = re.compile(r"\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日|\d{1,2}\s*月\s*\d{1,2}\s*日")
MONEY_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:亿元|万元|万|元)")
PERSON_NAME_RE = re.compile(r"(?:由|责任人为|负责人为)\s*[\u4e00-\u9fff]{1,3}某|[\u4e00-\u9fff]{1,3}某\s*负责")
PLAN_PRECISE_TIME_RE = re.compile(
    r"(?:\d+\s*小时|[一二两三四五六七八九十]+小时|每\s*(?:日|天|周|月|季度|半年|年)|按\s*(?:日|天|周|月|季度|半年|年)|"
    r"每[日天周月季度半年年]|第一时间|在[^，。；;]{0,8}小时内)"
)

NORMATIVE_ENFORCEMENT_MARKERS = (
    "镇纪委要紧盯",
    "镇纪委要加强监督",
    "镇纪委要加强日常监督",
    "苗头性",
    "倾向性",
    "及时提醒",
    "咬耳扯袖",
    "踩红线",
    "越底线",
    "严肃查处",
    "依规依纪处理",
    "违反纪律规矩",
)


def _safe_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _numbers(text: str) -> set[str]:
    return set(re.findall(r"\d+(?:\.\d+)?", text or ""))


def _anchor_terms(anchor: Any) -> tuple[str, ...]:
    return tuple(str(term) for term in getattr(anchor, "subject_terms", ()) or () if str(term))


def _anchor_quantity_matches(text: str, anchor: Any) -> bool:
    quantity = _safe_text(getattr(anchor, "quantity_text", ""))
    normalized = _safe_text(getattr(anchor, "quantity_normalized", ""))
    unit = _safe_text(getattr(anchor, "unit", ""))
    if quantity and quantity in text:
        return True
    return bool(normalized and unit and normalized in _numbers(text) and unit in text)


def _anchor_supported_in_text(text: str, anchor: Any) -> bool:
    terms = _anchor_terms(anchor)
    return bool(terms and _anchor_quantity_matches(text, anchor) and any(term in text for term in terms))


def _anchor_ids_in_text(text: str, user_fact_anchors: Any) -> set[str]:
    ids: set[str] = set()
    for anchor in list(user_fact_anchors or []):
        if _anchor_supported_in_text(text, anchor):
            anchor_id = _safe_text(getattr(anchor, "anchor_id", ""))
            if anchor_id:
                ids.add(anchor_id)
    return ids


def _rewrite_preserves_anchors(original: str, rewritten: str, user_fact_anchors: Any) -> bool:
    original_anchor_ids = _anchor_ids_in_text(original, user_fact_anchors)
    if not original_anchor_ids:
        return True
    return original_anchor_ids <= _anchor_ids_in_text(rewritten, user_fact_anchors)


def _has_unanchored_specific_value(text: str, user_fact_anchors: Any) -> bool:
    anchored_numbers = {
        _safe_text(getattr(anchor, "quantity_normalized", ""))
        for anchor in list(user_fact_anchors or [])
        if _anchor_supported_in_text(text, anchor)
    }
    anchored_numbers.discard("")
    numbers = _numbers(text)
    return bool(
        (numbers - anchored_numbers)
        or DATE_RE.search(text or "")
        or MONEY_RE.search(text or "")
        or PERSON_NAME_RE.search(text or "")
        or PLAN_PRECISE_TIME_RE.search(text or "")
    )


def _strip_unsupported_specifics(text: str) -> str:
    result = text or ""
    result = DATE_RE.sub("", result)
    result = MONEY_RE.sub("相关资金", result)
    result = PERSON_NAME_RE.sub("明确专人负责", result)
    result = re.sub(r"\d+(?:\.\d+)?\s*(?:次|项|人|个|场|件|万元|亿元|元|户)?", "", result)
    result = re.sub(r"(?:已|已经|截至目前|截至|截止|今年以来|今年|目前|共|累计|正式)", "", result)
    result = result.replace("已投入", "统筹保障")
    result = re.sub(r"\s+", "", result)
    result = re.sub(r"([，,；;、]){2,}", r"\1", result)
    result = result.strip("，,；;、：: ")
    return result


def _split_rewrite_units(sentence: str) -> list[str]:
    units = re.findall(r"[^，；。：、\n\r,;:]+[，；。：、,;:]?", sentence or "")
    return [unit.strip() for unit in units if unit.strip()] or ([sentence.strip()] if sentence and sentence.strip() else [])


def _try_preserve_anchor_units_and_weaken_rest(original: str, user_fact_anchors: Any) -> str:
    if not _anchor_ids_in_text(original, user_fact_anchors):
        return ""
    units: list[str] = []
    changed = False
    for unit in _split_rewrite_units(original):
        if _anchor_ids_in_text(unit, user_fact_anchors):
            units.append(unit)
            continue
        if _has_unanchored_specific_value(unit, user_fact_anchors):
            if units and not units[-1].endswith(("，", ",", "；", ";", "。", "、")):
                units[-1] += "，"
            units.append(LOCAL_UNSUPPORTED_FACT_REPLACEMENT)
            changed = True
            continue
        units.append(unit)
    rewritten = "".join(units).strip()
    if not changed or rewritten == original:
        return ""
    if not _rewrite_preserves_anchors(original, rewritten, user_fact_anchors):
        return ""
    return rewritten


def _is_heading(line: str) -> bool:
    return bool(re.match(r"^\s*(?:[一二三四五六七八九十]+、|第[一二三四五六七八九十]+[，,、])", line or ""))


def _is_content_reference_hit(item: dict[str, Any]) -> bool:
    return item.get("source_status") == "命中内容参考" or item.get("matched_source_type") == "content_reference"


def _has_occurred_enforcement_fact(sentence: str) -> bool:
    return bool(OCCURRED_ENFORCEMENT_FACT_PATTERN.search(sentence or ""))


def _is_normative_enforcement_requirement(sentence: str) -> bool:
    text = sentence or ""
    if _has_occurred_enforcement_fact(text):
        return False
    return any(marker in text for marker in NORMATIVE_ENFORCEMENT_MARKERS)


def _rewrite_normative_enforcement_requirement(sentence: str) -> str:
    text = sentence
    if (
        "紧盯这些重点环节" in text
        and ("踩红线" in text or "越底线" in text or "严肃查处" in text)
    ):
        suffix = "不能让制度成为“纸老虎”，真正把铁规矩立起来、严起来。" if "纸老虎" in text else "真正把铁规矩立起来、严起来。"
        return (
            "要紧盯低保评定、项目建设、资金拨付等重点环节，"
            "对苗头性、倾向性问题及时提醒，"
            f"对违反纪律规矩的行为依规依纪处理，{suffix}"
        )
    if "镇纪委要加强" in text and ("苗头性" in text or "监督" in text):
        return "要加强日常监督，对苗头性问题及时提醒，对违规行为依规依纪纠正，推动监督要求落到实处。"
    if "踩红线" in text or "越底线" in text:
        return "对违反纪律规矩的行为，要依规依纪处理，形成制度约束。"
    if "违反纪律规矩" in text and "依规依纪处理" in text:
        return text.replace("镇纪委要", "要").replace("镇纪委", "").strip(" ，,、")
    return text


def _rewrite_occured_local_fact(sentence: str) -> str:
    text = sentence
    if _is_normative_enforcement_requirement(text):
        return _rewrite_normative_enforcement_requirement(text)
    if "当前，我们镇的" in text and "任务很重" in text:
        inner = text.split("当前，我们镇的", 1)[1].split("等任务很重", 1)[0]
        return f"围绕{inner}等重点工作，越是任务繁重，越要把程序走规范、把责任扛起来。"
    if "我们镇上这几年" in text and "出台了不少制度" in text:
        return "制度建设不能停留在纸面上，关键要看执行是否到位、程序是否闭环、监督是否有力。"
    if "我们镇这几年" in text and "出台了不少制度" in text:
        return "制度建设不能停留在纸面上，关键要看执行是否到位、程序是否闭环、监督是否有力。"
    if "有的村出现过优亲厚友" in text:
        return "在低保评议等环节，要防止优亲厚友、审核不严、程序不规范等风险。"

    replacements = [
        ("当前，我们镇的", "围绕"),
        ("当前，我们镇", "围绕乡镇重点工作"),
        ("我们镇上这几年也出台了不少制度", "制度建设不能停留在纸面上"),
        ("我们镇上这几年", "制度建设和基层治理中"),
        ("我们镇这几年", "制度建设和基层治理中"),
        ("具体到我们镇", "具体到乡镇工作"),
        ("我们镇", "乡镇工作"),
        ("本镇", "基层一线"),
        ("去年", "在相关工作中"),
        ("近年来", "从基层治理实践看"),
        ("有的村出现过", "在村级事务中要防止"),
        ("有的村", "部分村级事务环节"),
        ("镇纪委发现", "监督工作中需要关注"),
        ("镇党委", "党委"),
        ("镇纪委", "纪检组织"),
    ]
    for old, new in replacements:
        text = text.replace(old, new)
    return text


def _rewrite_implicit_source_claim(sentence: str) -> str:
    text = sentence
    if _is_normative_enforcement_requirement(text):
        return _rewrite_normative_enforcement_requirement(text)
    replacements = [
        ("但大家看看近年来通报的典型案例", "从一些基层违纪违法案例看"),
        ("近年来通报的典型案例", "一些基层违纪违法案例"),
        ("近年通报案例显示", "从一些基层案例看"),
        ("从通报案例看", "从一些基层案例看"),
        ("从近年来查处情况看", "从基层廉政风险的一般表现看"),
        ("从近年来监督检查情况看", "从基层廉政风险的一般表现看"),
        ("从近年巡察反馈看", "从基层治理薄弱环节看"),
        ("从群众反映情况看", "从群众身边不正之风的一般表现看"),
        ("近年来通报", "一些基层案例"),
        ("近年来", "从基层工作实践看"),
    ]
    for old, new in replacements:
        text = text.replace(old, new)
    return text


def _rewrite_sentence(sentence: str, claim_type: str) -> tuple[str, str]:
    if _is_normative_enforcement_requirement(sentence):
        return _rewrite_normative_enforcement_requirement(sentence), "normative_enforcement_to_safe_requirement"
    if _has_unanchored_specific_value(sentence, []):
        return _strip_unsupported_specifics(sentence), "unsupported_specifics_to_generalized_statement"
    if claim_type == "occurred_local_fact":
        return _rewrite_occured_local_fact(sentence), "occurred_local_fact_to_generalized_risk"
    if claim_type == "implicit_source_claim":
        return _rewrite_implicit_source_claim(sentence), "implicit_source_to_generalized_case"
    return sentence, "not_rewritten"


def rewrite_high_risk_facts_locally(
    original_text: str,
    audit_items: Any,
    document_type: str = "",
    user_input_summary: str | None = None,
    content_reference_summary: str | None = None,
    user_fact_anchors: Any = None,
) -> dict[str, Any]:
    """Rewrite only clearly safe HIGH snippets without calling an LLM."""
    text = str(original_text or "")
    items = audit_items.get("items", []) if isinstance(audit_items, dict) else list(audit_items or [])
    report_items: list[dict[str, Any]] = []

    for item in items:
        original = _safe_text(item.get("fact_text") or item.get("claim_text"))
        claim_type = str(item.get("claim_type") or "")
        risk_level = str(item.get("risk_level") or "")
        report = {
            "original": original,
            "rewritten": original,
            "claim_type": claim_type,
            "risk_level_before": risk_level,
            "rewrite_method": "not_attempted",
            "success": False,
            "failure_reason": "",
        }

        if risk_level != "HIGH":
            report["failure_reason"] = "not_high_risk"
            report_items.append(report)
            continue
        if claim_type not in LOCAL_REWRITABLE_TYPES:
            report["failure_reason"] = "claim_type_not_local_rewritable"
            report_items.append(report)
            continue
        if _is_content_reference_hit(item):
            report["failure_reason"] = "matched_content_reference"
            report_items.append(report)
            continue
        if not original or len(original) > 180:
            report["failure_reason"] = "empty_or_complex_long_fragment"
            report_items.append(report)
            continue
        if _is_heading(original):
            report["failure_reason"] = "heading_protected"
            report_items.append(report)
            continue
        if _has_occurred_enforcement_fact(original):
            report["failure_reason"] = "occurred_fact_protected"
            report_items.append(report)
            continue

        anchor_safe_partial = _try_preserve_anchor_units_and_weaken_rest(original, user_fact_anchors)
        if anchor_safe_partial:
            rewritten, method = anchor_safe_partial, "anchor_preserving_partial_rewrite"
        else:
            rewritten, method = _rewrite_sentence(original, claim_type)
        if rewritten == original:
            report["failure_reason"] = "no_safe_rule_matched"
            report_items.append(report)
            continue
        if not _rewrite_preserves_anchors(original, rewritten, user_fact_anchors):
            report["failure_reason"] = "user_fact_anchor_protected"
            report["rewritten"] = rewritten
            report["rewrite_method"] = method
            report_items.append(report)
            continue
        if original not in text:
            report["failure_reason"] = "fragment_not_found"
            report["rewritten"] = rewritten
            report["rewrite_method"] = method
            report_items.append(report)
            continue

        text = text.replace(original, rewritten, 1)
        report.update(
            {
                "rewritten": rewritten,
                "rewrite_method": method,
                "success": True,
                "failure_reason": "",
            }
        )
        report_items.append(report)

    return {
        "rewritten_text": text,
        "rewrite_report": {
            "document_type": document_type,
            "attempted_count": sum(1 for item in report_items if item["rewrite_method"] != "not_attempted"),
            "success_count": sum(1 for item in report_items if item["success"]),
            "items": report_items,
        },
    }


def render_fact_rewrite_report_markdown(rewrite_report: Any) -> str:
    report = rewrite_report if isinstance(rewrite_report, dict) else {}
    lines = [
        "# official_draft 本地事实降险报告",
        "",
        f"- 文种：{report.get('document_type') or '未指定'}",
        f"- 尝试改写数量：{report.get('attempted_count', 0)}",
        f"- 成功改写数量：{report.get('success_count', 0)}",
        "",
    ]
    for index, item in enumerate(report.get("items", []) or [], start=1):
        lines.extend(
            [
                f"## {index}. {item.get('claim_type') or 'unknown'}",
                f"- 原句：{item.get('original') or ''}",
                f"- 改后句：{item.get('rewritten') or ''}",
                f"- 改写前风险：{item.get('risk_level_before') or ''}",
                f"- 改写方法：{item.get('rewrite_method') or ''}",
                f"- 是否成功：{item.get('success')}",
                f"- 失败原因：{item.get('failure_reason') or '无'}",
                "",
            ]
        )
    return "\n".join(lines)
