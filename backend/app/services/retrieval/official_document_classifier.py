"""Rule-based official document classifier for retrieval chunk metadata."""

from __future__ import annotations

import re
from typing import Iterable


UNKNOWN = "未知"

DOCUMENT_TYPES = (
    "党课讲稿", "领导讲话", "工作汇报", "工作总结", "工作报告",
    "情况报告", "整改报告", "评估报告", "调研报告", "通报", "其他",
)
DOCUMENT_ROLES = ("fact_source", "expression_source", "mixed")
FACT_SOURCE_DOCUMENT_TYPES = frozenset({
    "工作汇报", "工作总结", "工作报告", "情况报告", "整改报告", "评估报告",
    "调研报告", "通报",
})
EXPRESSION_SOURCE_DOCUMENT_TYPES = frozenset({"党课讲稿", "领导讲话"})
LEVELS = ("乡镇 / 街道", "县区", "市级", "省级", "部门", "村 / 社区", UNKNOWN)
ORG_TYPES = ("党政机关", "事业单位", "国企", "学校", "医院", "村社", UNKNOWN)
BUSINESS_DOMAINS = (
    "纪检 / 廉政",
    "党建",
    "巡察整改",
    "安全生产",
    "乡村振兴",
    "基层治理",
    "营商环境",
    "生态环保",
    "组织人事",
    "综合政务",
    UNKNOWN,
)
WRITING_PURPOSES = (
    "学习研讨",
    "会议讲话",
    "工作部署",
    "汇报总结",
    "调研分析",
    "整改落实",
    "表态发言",
    UNKNOWN,
)


def _contains_any(text: str, keywords: Iterable[str]) -> bool:
    return any(keyword in text for keyword in keywords)


def _first_match(text: str, rules: list[tuple[str, tuple[str, ...]]], default: str = UNKNOWN) -> str:
    for value, keywords in rules:
        if _contains_any(text, keywords):
            return value
    return default


def normalize_classification_context(
    *,
    title: str = "",
    source_file: str = "",
    text: str = "",
    max_text_chars: int = 3000,
) -> str:
    """Build a compact rule-matching context from stable document signals."""
    parts = [source_file or "", title or "", (text or "")[:max_text_chars]]
    return "\n".join(part for part in parts if part).strip()


def classify_document_type(*, title: str = "", source_file: str = "", text: str = "") -> str:
    title_context = normalize_classification_context(title=title, source_file=source_file)
    title_compact = re.sub(r"\s+", "", title_context)
    compact = re.sub(
        r"\s+", "", normalize_classification_context(title=title, source_file=source_file, text=text)
    )

    title_type = _classify_document_type_compact(title_compact)
    if title_type != "其他":
        return title_type
    return _classify_document_type_compact(compact)


def _classify_document_type_compact(compact: str) -> str:
    if _contains_any(compact, ("党课讲稿", "廉政党课", "专题党课", "党课")):
        return "党课讲稿"
    if "同志们" in compact and "党课" in compact:
        return "党课讲稿"
    if _contains_any(compact, ("讲话稿", "动员讲话", "部署讲话", "致辞")):
        return "领导讲话"
    if re.search(r"在.+会议上?的讲话", compact) or "讲话" in compact:
        return "领导讲话"
    if "整改报告" in compact:
        return "整改报告"
    if _contains_any(compact, ("评估报告", "评价报告")):
        return "评估报告"
    if _contains_any(compact, ("情况报告", "落实情况报告", "推进情况报告")):
        return "情况报告"
    if _contains_any(compact, ("工作总结", "年度总结", "半年总结", "阶段性总结", "总结")):
        return "工作总结"
    if _contains_any(compact, ("工作汇报", "情况汇报", "专题汇报", "汇报")):
        return "工作汇报"
    if _contains_any(compact, ("调研报告", "关于", "调研", "问题分析", "对策建议")) and _contains_any(
        compact,
        ("调研报告", "调研", "问题分析", "对策建议"),
    ):
        return "调研报告"
    if "工作报告" in compact:
        return "工作报告"
    if _contains_any(compact, ("情况通报", "工作通报")) or compact.endswith("通报"):
        return "通报"
    return "其他"


def classify_level(*, title: str = "", source_file: str = "", text: str = "") -> str:
    context = normalize_classification_context(title=title, source_file=source_file, text=text)
    compact = re.sub(r"\s+", "", context)

    if _contains_any(compact, ("村委", "村党支部", "社区党委", "社区党支部", "社区居委会")):
        return "村 / 社区"
    if _contains_any(compact, ("乡镇", "镇党委", "镇政府", "街道", "街道办", "全镇")):
        return "乡镇 / 街道"
    if _contains_any(compact, ("县委", "区委", "县政府", "区政府", "全县", "全区")):
        return "县区"
    if _contains_any(compact, ("市委", "市政府", "全市", "市级")):
        return "市级"
    if _contains_any(compact, ("省委", "省政府", "全省", "省级")):
        return "省级"
    if _contains_any(compact, ("局", "委", "办", "厅", "处", "中心")):
        return "部门"
    return UNKNOWN


def classify_org_type(*, title: str = "", source_file: str = "", text: str = "") -> str:
    context = normalize_classification_context(title=title, source_file=source_file, text=text)
    compact = re.sub(r"\s+", "", context)
    return _first_match(
        compact,
        [
            ("学校", ("学校", "高校", "学院", "中学", "小学")),
            ("医院", ("医院", "卫健", "医疗")),
            ("国企", ("国企", "集团公司", "有限公司", "公司党委")),
            ("村社", ("村委", "社区", "村党支部", "村级")),
            ("事业单位", ("事业单位", "中心", "院", "所")),
            ("党政机关", ("党委", "政府", "纪委", "组织部", "宣传部", "局", "办")),
        ],
        UNKNOWN,
    )


def classify_business_domain(*, title: str = "", source_file: str = "", text: str = "") -> str:
    rules = [
        ("纪检 / 廉政", ("廉政", "党风廉政", "纪检", "纪委", "纪律", "作风建设", "微腐败", "警示教育")),
        ("巡察整改", ("巡察", "整改", "反馈问题", "问题清单", "回头看")),
        ("安全生产", ("安全生产", "隐患排查", "应急", "消防", "防汛")),
        ("乡村振兴", ("乡村振兴", "人居环境", "集体经济", "农村", "村级")),
        ("基层治理", ("基层治理", "网格", "矛盾纠纷", "便民服务", "群众诉求")),
        ("营商环境", ("营商环境", "企业服务", "招商", "项目服务")),
        ("生态环保", ("生态环保", "污染防治", "环保", "河长制", "生态环境")),
        ("组织人事", ("组织人事", "干部", "换届", "人才", "选拔任用")),
        ("党建", ("党建", "党支部", "党员", "主题党日", "基层党组织")),
        ("综合政务", ("办公室", "综合协调", "政务", "督查督办")),
    ]
    # Title/file-name signals are document-level intent.  Body words such as
    # “整改” or “纪律” are common inside safety material and must not steal the
    # document's primary business domain from an explicit title.
    title_compact = re.sub(
        r"\s+", "", normalize_classification_context(title=title, source_file=source_file)
    )
    title_match = _first_match(title_compact, rules, UNKNOWN)
    if title_match != UNKNOWN:
        return title_match
    compact = re.sub(
        r"\s+", "", normalize_classification_context(title=title, source_file=source_file, text=text)
    )
    return _first_match(compact, rules, UNKNOWN)


def classify_writing_purpose(*, title: str = "", source_file: str = "", text: str = "") -> str:
    # Stable title signals take precedence over words that merely occur in the
    # body.  In particular, a summary often discusses learning and education;
    # those words must not turn the whole document into a study/seminar item.
    title_context = "\n".join(part for part in (source_file, title) if part)
    title_compact = re.sub(r"\s+", "", title_context)
    if _contains_any(
        title_compact,
        (
            "工作总结", "年度总结", "半年总结", "阶段性总结", "工作报告",
            "情况报告", "评估报告", "评价报告", "情况通报", "工作通报",
        ),
    ):
        return "汇报总结"
    if "整改报告" in title_compact:
        return "整改落实"
    if _contains_any(title_compact, ("学习研讨", "研讨发言", "学习体会", "理论学习中心组", "心得体会", "党课讲稿", "专题党课", "党课")):
        return "学习研讨"

    context = normalize_classification_context(title=title, source_file=source_file, text=text)
    compact = re.sub(r"\s+", "", context)
    return _first_match(
        compact,
        [
            ("会议讲话", ("讲话", "致辞", "会议上")),
            ("工作部署", ("部署", "安排", "推进会", "动员")),
            ("汇报总结", ("汇报", "总结", "述职")),
            ("学习研讨", ("学习研讨", "研讨发言", "学习体会", "理论学习中心组", "心得体会", "党课")),
            ("调研分析", ("调研", "分析", "对策建议")),
            ("整改落实", ("整改", "落实", "反馈问题")),
            ("表态发言", ("表态", "发言")),
        ],
        UNKNOWN,
    )


def classify_document_role(
    *,
    document_type: str = "",
    writing_purpose: str = "",
    title: str = "",
    source_file: str = "",
    text: str = "",
) -> str:
    """Classify whether a document is a factual source or an expression source."""
    resolved_type = (document_type or "").strip() or classify_document_type(
        title=title, source_file=source_file, text=text
    )
    resolved_purpose = (writing_purpose or "").strip() or classify_writing_purpose(
        title=title, source_file=source_file, text=text
    )
    if resolved_type in FACT_SOURCE_DOCUMENT_TYPES:
        return "fact_source"
    if resolved_type in EXPRESSION_SOURCE_DOCUMENT_TYPES:
        return "expression_source"
    if resolved_purpose in {"汇报总结", "调研分析", "整改落实"}:
        return "fact_source"
    if resolved_purpose in {"会议讲话", "学习研讨", "表态发言"}:
        return "expression_source"
    return "mixed"


def classify_official_document(*, title: str = "", source_file: str = "", text: str = "") -> dict[str, str]:
    """Classify document-level metadata without LLM calls."""
    document_type = classify_document_type(title=title, source_file=source_file, text=text)
    writing_purpose = classify_writing_purpose(title=title, source_file=source_file, text=text)
    return {
        "document_type": document_type,
        "level": classify_level(title=title, source_file=source_file, text=text),
        "org_type": classify_org_type(title=title, source_file=source_file, text=text),
        "business_domain": classify_business_domain(title=title, source_file=source_file, text=text),
        "writing_purpose": writing_purpose,
        "document_role": classify_document_role(
            document_type=document_type,
            writing_purpose=writing_purpose,
        ),
    }
