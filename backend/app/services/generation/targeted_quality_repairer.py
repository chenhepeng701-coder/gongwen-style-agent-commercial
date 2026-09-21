"""One-shot targeted quality repair for the official_draft hybrid gate."""

from __future__ import annotations

from typing import Any


def _risk_count(audit: Any, level: str) -> int:
    if not isinstance(audit, dict):
        return 0
    key = f"{level.lower()}_risk_count"
    if key in audit:
        return int(audit.get(key) or 0)
    return sum(1 for item in audit.get("items", []) or [] if item.get("risk_level") == level)


def _failed_local_rewrites(local_result: Any) -> list[dict[str, Any]]:
    if not isinstance(local_result, dict):
        return []
    report = local_result.get("rewrite_report") or {}
    return [
        item
        for item in report.get("items", []) or []
        if item.get("risk_level_before") == "HIGH"
        and not item.get("success")
        and item.get("failure_reason") not in {"not_high_risk", "matched_content_reference"}
    ]


def should_call_targeted_repair(
    audit_after_local: Any,
    structure_after_local: Any,
    local_result: Any | None = None,
    max_calls: int = 1,
    calls_used: int = 0,
) -> tuple[bool, list[str]]:
    """Return whether the one-shot LLM repair is justified."""
    reasons: list[str] = []
    if calls_used >= max_calls:
        return False, ["已达到每篇最大 DeepSeek 定向修复调用次数。"]

    high_count = _risk_count(audit_after_local, "HIGH")
    if high_count > 1:
        reasons.append(f"本地降险后 HIGH={high_count}，超过 1。")

    structure_valid = True
    if isinstance(structure_after_local, dict):
        structure_valid = bool(structure_after_local.get("is_valid", True))
        warnings = structure_after_local.get("warnings", []) or []
        if not structure_valid:
            reasons.append("结构校验失败：" + "；".join(str(w) for w in warnings[:3]))

    failed_rewrites = _failed_local_rewrites(local_result)
    if failed_rewrites and high_count > 0:
        reasons.append("存在本地规则无法安全替换的复杂 HIGH 片段。")

    return bool(reasons), reasons


def build_targeted_repair_prompt(
    original_text: str,
    locally_rewritten_text: str,
    audit_items_high: list[dict[str, Any]],
    structure_report: dict[str, Any],
    document_type: str,
    task_model: Any = None,
    content_references_summary: str = "",
    style_references_summary: str = "",
) -> str:
    task_lines = []
    for field in ("title", "topic", "level", "org_type", "business_domain", "writing_purpose"):
        value = getattr(task_model, field, "") if task_model is not None else ""
        if value:
            task_lines.append(f"- {field}: {value}")

    high_lines = []
    for index, item in enumerate(audit_items_high, start=1):
        high_lines.append(
            "\n".join(
                [
                    f"{index}. {item.get('fact_text') or item.get('claim_text')}",
                    f"   - 类型：{item.get('claim_type')}",
                    f"   - 原因：{item.get('reason')}",
                    f"   - 改写提示：{item.get('rewrite_hint')}",
                ]
            )
        )

    structure_warnings = structure_report.get("warnings", []) if isinstance(structure_report, dict) else []
    return f"""你不是重新写文章。请只对列出的问题片段做定向修复。

硬性要求：
1. 保留原文主体结构，保留已有一级标题。
2. 不全篇重写，不改变文种，不删除主要内容。
3. 如果一级标题缺失，只补 3 个左右动态一级标题并按原段落归类。
4. 不新增未在用户输入或内容参考中出现的具体事实、数字、时间、金额、百分比。
5. 无来源事实改为风险表达、规范性要求或泛化表达。
6. 不使用“近年来通报”“群众反映强烈”“我们镇当前”等无来源具体表述。
7. 不把文章改成空话，不输出 Markdown 说明，只输出修复后的正文。
8. 通用材料、模板材料、匿名材料和其他单位材料只可作为写法参考，不得改写为当前单位已完成、已存在、已建立或必须执行的确定性陈述。
9. 无依据的本地已完成工作、现存问题、已建制度必须改为泛化风险表达或规范性要求；无依据的频次、时限、责任部门、责任人、上报链路和等级划分必须去除具体值并改为可结合现行制度明确的自然表述。
10. 不得输出“待补充”“待确认”“TODO”“需要核实”“请提供资料”等内部提示或占位标记。

【文种】
{document_type or "未指定"}

【任务信息】
{chr(10).join(task_lines) or "无"}

【需要修复的 HIGH 片段】
{chr(10).join(high_lines) or "无"}

【结构问题】
{chr(10).join("- " + str(w) for w in structure_warnings) or "无"}

【内容参考摘要】
{content_references_summary or "无"}

【写法参考摘要】
{style_references_summary or "无"}

【本地降险后的正文】
{locally_rewritten_text or original_text}
"""


def targeted_quality_repair(
    original_text: str,
    locally_rewritten_text: str,
    audit_items_high: list[dict[str, Any]],
    structure_report: dict[str, Any],
    document_type: str,
    task_model: Any = None,
    content_references_summary: str = "",
    style_references_summary: str = "",
    provider: Any = None,
    call_llm: bool = True,
) -> dict[str, Any]:
    prompt = build_targeted_repair_prompt(
        original_text=original_text,
        locally_rewritten_text=locally_rewritten_text,
        audit_items_high=audit_items_high,
        structure_report=structure_report,
        document_type=document_type,
        task_model=task_model,
        content_references_summary=content_references_summary,
        style_references_summary=style_references_summary,
    )
    report = {
        "called_llm": False,
        "call_reason": [],
        "input_high_count": len(audit_items_high),
        "structure_issues": list(structure_report.get("warnings", []) if isinstance(structure_report, dict) else []),
        "success": False,
        "error": "",
        "call_count": 0,
        "token_cost": None,
        "prompt": prompt,
    }

    if not call_llm or provider is None:
        report["error"] = "llm_not_called_function_level"
        return {"repaired_text": locally_rewritten_text or original_text, "repair_report": report}

    try:
        repaired_text = provider.generate_text(prompt=prompt, system_prompt="", temperature=0.2)
        repaired_text = str(repaired_text or "").strip()
        report.update({"called_llm": True, "success": bool(repaired_text), "call_count": 1})
        if not repaired_text:
            report["error"] = "empty_llm_response"
            repaired_text = locally_rewritten_text or original_text
        return {"repaired_text": repaired_text, "repair_report": report}
    except Exception as exc:
        report.update({"called_llm": True, "success": False, "error": str(exc), "call_count": 1})
        return {"repaired_text": locally_rewritten_text or original_text, "repair_report": report}


def render_targeted_repair_report_markdown(repair_report: Any) -> str:
    report = repair_report if isinstance(repair_report, dict) else {}
    lines = [
        "# official_draft DeepSeek 定向修复报告",
        "",
        f"- 是否调用 DeepSeek：{report.get('called_llm')}",
        f"- 调用原因：{'；'.join(report.get('call_reason') or []) or '无'}",
        f"- 输入 HIGH 数量：{report.get('input_high_count', 0)}",
        f"- 是否成功：{report.get('success')}",
        f"- 错误信息：{report.get('error') or '无'}",
        f"- 调用次数：{report.get('call_count', 0)}",
        f"- token 成本字段：{report.get('token_cost')}",
        "",
        "## 结构问题",
    ]
    lines.extend([f"- {item}" for item in report.get("structure_issues", [])] or ["无。"])
    lines.append("")
    return "\n".join(lines)
