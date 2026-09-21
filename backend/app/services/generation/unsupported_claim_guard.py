"""Deterministic guard for unsupported local facts and hard policy rules."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


LOCAL_COMPLETED_ACTION = "LOCAL_COMPLETED_ACTION"
LOCAL_EXISTING_PROBLEM = "LOCAL_EXISTING_PROBLEM"
LOCAL_EXISTING_SYSTEM = "LOCAL_EXISTING_SYSTEM"
MANDATORY_POLICY_RULE = "MANDATORY_POLICY_RULE"

UNSUPPORTED_CLAIM_TYPES = (
    LOCAL_COMPLETED_ACTION,
    LOCAL_EXISTING_PROBLEM,
    LOCAL_EXISTING_SYSTEM,
    MANDATORY_POLICY_RULE,
)

LOCAL_FACT_PLACEHOLDER = "【待补充：请提供本单位相关事实、问题或制度依据】"
RULE_PLACEHOLDER = "【待确认：具体频次、时限、责任部门和上报要求需依据现行制度核实】"
GENERAL_RULE_SUGGESTION = "可结合实际进一步明确相关频次、时限、责任分工和上报要求。"
ALLOWED_FACT_SAFETY_PLACEHOLDERS = (LOCAL_FACT_PLACEHOLDER, RULE_PLACEHOLDER)
PUBLIC_LOCAL_FACT_REPLACEMENT = "相关情况可结合本单位实际进一步细化，避免使用缺少依据的具体事实表述。"
PUBLIC_RULE_REPLACEMENT = "相关频次、时限、责任分工和上报要求可结合现行制度进一步明确。"

GENERIC_SUBJECT_NAMES = {
    "某局",
    "某镇",
    "某县",
    "某市",
    "某单位",
    "有关单位",
    "相关单位",
    "本单位",
    "我单位",
    "我局",
    "我镇",
}

LOCAL_SUBJECT_MARKERS = (
    "我局",
    "本局",
    "我镇",
    "本镇",
    "我单位",
    "本单位",
    "我县",
    "本县",
    "我市",
    "本市",
    "局内",
    "全局",
    "全镇",
    "各股室",
    "各科室",
    "村级",
    "本辖区",
)

LOCAL_NUMERIC_ACTION_MARKERS = (
    "开展",
    "组织",
    "召开",
    "举办",
    "完成",
    "整改",
    "解决",
    "走访",
    "座谈",
    "调研",
    "培训",
    "学习",
    "研讨",
)

COMPLETED_MARKERS = (
    "今年以来",
    "近年来",
    "近年以来",
    "已完成",
    "累计",
    "已处置",
    "已办理",
    "已开展",
    "开展了",
    "举办了",
    "进行了",
    "完成了",
    "发现并处置",
    "审批备案",
    "提醒谈话",
    "取得明显成效",
)

PROBLEM_MARKERS = (
    "存在",
    "仍有",
    "主要问题",
    "突出问题",
    "执行不到位",
    "落实不到位",
    "协同不畅",
    "管理存在盲区",
    "存在漏洞",
    "服务不足",
    "不够严格",
    "不够通畅",
    "客观存在",
)

SYSTEM_MARKERS = (
    "已建立",
    "建立了",
    "已实行",
    "实行",
    "已形成",
    "形成了",
    "纳入考核",
    "纳入",
    "已落实",
    "落实定期",
    "信息员队伍",
    "研判制度",
    "述职评议",
    "机制",
    "制度",
)

RULE_SPECIFIC_MARKERS = (
    "24小时",
    "48小时",
    "二十四小时",
    "四十八小时",
    "每日",
    "每天",
    "每月",
    "按月",
    "每季度",
    "季度",
    "每半年",
    "半年",
    "每两年",
    "两年",
    "每年",
    "定期",
    "至少",
    "必须",
    "严格执行",
    "同步上报",
    "上报",
    "责任人",
    "责任部门",
    "蓝色",
    "黄色",
    "红色",
    "蓝黄红",
    "预警",
    "办公室",
    "预算股",
    "国库股",
    "采购股",
    "资产管理股",
    "债务管理股",
    "监督股",
    "宣传部",
    "网信办",
    "公安",
    "股室",
    "科室",
    "局班子成员",
    "分管科室负责人",
    "双随机一公开",
    "财政开放日",
    "媒体见面会",
    "青年理论学习小组",
)

RULE_VALUE_RE = re.compile(
    r"(?:\d+\s*小时|\d+\s*日|\d+\s*天|\d+\s*个工作日|每\s*[日月季度半年年]|按\s*[日月季度半年年]|"
    r"至少|必须|蓝色|黄色|红色|蓝黄红|办公室|预算股|国库股|采购股|资产管理股|债务管理股|监督股|"
    r"宣传部|网信办|公安|股室|科室|上报|责任人|责任部门|双随机一公开|财政开放日|媒体见面会)"
)
NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
DATE_RE = re.compile(r"\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日|\d{1,2}\s*月\s*\d{1,2}\s*日")
MONEY_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:亿元|万元|万|元)")
PERSON_NAME_RE = re.compile(r"(?:由|责任人为|负责人为)\s*[\u4e00-\u9fff]{1,3}某|[\u4e00-\u9fff]{1,3}某\s*负责")
PLAN_PRECISE_TIME_RE = re.compile(
    r"(?:\d+\s*小时|[一二两三四五六七八九十]+小时|每\s*(?:日|天|周|月|季度|半年|年)|按\s*(?:日|天|周|月|季度|半年|年)|"
    r"每[日天周月季度半年年]|第一时间|在[^，。；;]{0,8}小时内)"
)
CHINESE_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]{2,}")
SENTENCE_RE = re.compile(r"[^。！？!?；;\n]+[。！？!?；;]?")
CLAUSE_SPLIT_RE = re.compile(r"([^，；。：、\n\r,;:]+[，；。：、,;:]?)")

TOKEN_STOPWORDS = {
    "今年以来",
    "近年来",
    "当前",
    "工作",
    "问题",
    "情况",
    "方面",
    "相关",
    "进行",
    "开展",
    "建立",
    "制度",
    "机制",
    "必须",
    "至少",
    "本单位",
    "我单位",
    "我局",
    "我镇",
}


@dataclass(frozen=True)
class UnsupportedClaim:
    sentence: str
    claim_type: str
    has_direct_support: bool
    support_source: str
    source_category: str
    action: str
    replacement: str


def _safe_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _compact(text: str) -> str:
    return re.sub(r"[\s，,。；;：:、（）()\[\]【】“”\"'《》<>]+", "", text or "")


def _numbers(text: str) -> set[str]:
    return set(NUMBER_RE.findall(text or ""))


def _tokens(text: str) -> set[str]:
    result: set[str] = set()
    for raw in CHINESE_TOKEN_RE.findall(text or ""):
        if raw in TOKEN_STOPWORDS:
            continue
        if len(raw) > 8:
            result.add(raw[:8])
            result.add(raw[-8:])
        else:
            result.add(raw)
    return {token for token in result if token not in TOKEN_STOPWORDS}


def _has_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def _is_plan_document(document_type: str) -> bool:
    return "预案" in _safe_text(document_type)


def _split_sentences(text: str) -> list[str]:
    sentences: list[str] = []
    for line in str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        for match in SENTENCE_RE.finditer(line):
            sentence = match.group(0).strip()
            if sentence:
                sentences.append(sentence)
    return sentences


def _split_claim_units(sentence: str) -> list[str]:
    units: list[str] = []
    for match in CLAUSE_SPLIT_RE.finditer(sentence or ""):
        unit = match.group(0).strip()
        if unit:
            units.append(unit)
    return units or ([sentence.strip()] if sentence and sentence.strip() else [])


def _looks_like_heading(sentence: str) -> bool:
    return bool(re.match(r"^\s*(?:[一二三四五六七八九十]+[、.．]|（[一二三四五六七八九十]+）)", sentence or ""))


def _is_generic_subject(subject: str) -> bool:
    value = _safe_text(subject)
    if not value or value in GENERIC_SUBJECT_NAMES:
        return True
    if "某" in value or "XX" in value.upper():
        return True
    return False


def derive_current_subject(task: Any) -> str:
    """Best-effort current subject name from task fields; empty means unknown."""
    candidates: list[str] = []
    for field in ("title", "writing_purpose", "org_type", "topic"):
        value = _safe_text(getattr(task, field, ""))
        if not value:
            continue
        candidates.extend(
            re.findall(
                r"([\u4e00-\u9fff]{2,20}(?:局|镇|乡|街道|党委|政府|委|办|中心|公司|集团|学校|医院|法院|检察院))",
                value,
            )
        )
    for candidate in candidates:
        cleaned = candidate.strip("关于的")
        if cleaned and not _is_generic_subject(cleaned):
            return cleaned
    return ""


def _normalize_references(retrieval_top5: Any) -> list[dict[str, str]]:
    if retrieval_top5 is None:
        raw_items: list[Any] = []
    elif isinstance(retrieval_top5, (str, dict)):
        raw_items = [retrieval_top5]
    else:
        try:
            raw_items = list(retrieval_top5)
        except TypeError:
            raw_items = [retrieval_top5]

    references: list[dict[str, str]] = []
    for index, item in enumerate(raw_items[:5], start=1):
        if isinstance(item, dict):
            source = _safe_text(item.get("source") or item.get("title") or item.get("source_title")) or f"retrieval_{index}"
            text = _safe_text(item.get("text") or item.get("chunk_text") or item.get("content"))
        else:
            source = f"retrieval_{index}"
            text = _safe_text(item)
        if text or source:
            references.append({"source": source, "text": text})
    return references


def _has_plan_completed_fact(text: str) -> bool:
    if _has_any(text, COMPLETED_MARKERS):
        return True
    return bool(
        re.search(
            r"(?:已|已经|截至|截止|目前|共|累计|正式实施|投入|整改|完成)[^。；;，,]{0,24}"
            r"(?:完成|整改|开展|召开|建立|实行|实施|投入|排查|处置|取得|落实|\d)",
            text or "",
        )
    )


def _has_plan_conditional_risk(text: str) -> bool:
    return bool(re.search(r"(?:是否存在|可能|需防范|如发现|若发现|发现[^。；;]{0,16}应|防止|严防|避免)", text or ""))


def _has_plan_future_or_requirement(text: str) -> bool:
    return bool(
        re.search(
            r"(?:应|应当|要|需|需要|可|可以|建立|建立健全|完善|加强|规范|落实|健全|明确|做好|压实|统筹|"
            r"组织|开展|推进|制定|细化|强化|坚持|防范|处置|报告|管理)",
            text or "",
        )
    )


def _has_unsupported_specific_value(text: str) -> bool:
    return bool(
        NUMBER_RE.search(text or "")
        or DATE_RE.search(text or "")
        or MONEY_RE.search(text or "")
        or PERSON_NAME_RE.search(text or "")
        or PLAN_PRECISE_TIME_RE.search(text or "")
    )


def _classify_plan_claim(sentence: str) -> str:
    text = sentence or ""
    if _looks_like_heading(text):
        return ""
    if _has_plan_completed_fact(text):
        return LOCAL_COMPLETED_ACTION
    if _has_plan_conditional_risk(text) and not _has_unsupported_specific_value(text):
        return ""
    if _has_plan_future_or_requirement(text):
        return MANDATORY_POLICY_RULE if _has_unsupported_specific_value(text) else ""
    if _has_unsupported_specific_value(text):
        return MANDATORY_POLICY_RULE
    return ""


def _classify_claim(sentence: str, *, document_type: str = "") -> str:
    text = sentence or ""
    if _is_plan_document(document_type):
        return _classify_plan_claim(text)
    if _looks_like_heading(text):
        return ""
    if NUMBER_RE.search(text) and any(marker in text for marker in LOCAL_NUMERIC_ACTION_MARKERS):
        return LOCAL_COMPLETED_ACTION
    if NUMBER_RE.search(text) and any(marker in text for marker in ("培训", "参训", "人")):
        return LOCAL_COMPLETED_ACTION
    if _has_any(text, RULE_SPECIFIC_MARKERS) or RULE_VALUE_RE.search(text):
        return MANDATORY_POLICY_RULE
    if _has_any(text, SYSTEM_MARKERS) and (_has_any(text, LOCAL_SUBJECT_MARKERS) or _has_any(text, ("已", "建立", "实行", "纳入"))):
        return LOCAL_EXISTING_SYSTEM
    if _has_any(text, PROBLEM_MARKERS) and (
        _has_any(text, LOCAL_SUBJECT_MARKERS)
        or "上述问题" in text
        or "当前" in text
        or not re.search(r"^(?:要|需|需要|应|应当|可|可以|防止|避免|严防)", text)
    ):
        return LOCAL_EXISTING_PROBLEM
    if _numbers(text) and "培训" in text and ("参训" in text or "人" in text):
        return LOCAL_COMPLETED_ACTION
    if _has_any(text, COMPLETED_MARKERS):
        return LOCAL_COMPLETED_ACTION
    return ""


def _source_supports_claim(claim: str, source_text: str, *, strict_specific: bool) -> bool:
    claim_compact = _compact(claim)
    source_compact = _compact(source_text)
    if claim_compact and claim_compact in source_compact:
        return True

    claim_numbers = _numbers(claim)
    if claim_numbers and not claim_numbers <= _numbers(source_text):
        return False

    claim_tokens = _tokens(claim)
    source_tokens = _tokens(source_text)
    overlap = claim_tokens & source_tokens
    if strict_specific and claim_numbers:
        return len(overlap) >= 1
    if strict_specific:
        return len(overlap) >= 2
    return bool(overlap)


def _user_direct_support(claim: str, user_direct_facts: Any) -> str:
    if isinstance(user_direct_facts, (str, dict)) or user_direct_facts is None:
        items = [user_direct_facts] if user_direct_facts is not None else []
    else:
        try:
            items = list(user_direct_facts)
        except TypeError:
            items = [user_direct_facts]
    for index, item in enumerate(items, start=1):
        text = _safe_text(item)
        if not text:
            continue
        if _source_supports_claim(claim, text, strict_specific=True):
            return f"user_direct_{index}"
    return ""


def _user_anchor_support(claim: str, user_fact_anchors: Any) -> str:
    claim_numbers = _numbers(claim)
    for anchor in list(user_fact_anchors or []):
        quantity = _safe_text(getattr(anchor, "quantity_text", ""))
        normalized = _safe_text(getattr(anchor, "quantity_normalized", ""))
        unit = _safe_text(getattr(anchor, "unit", ""))
        terms = tuple(getattr(anchor, "subject_terms", ()) or ())
        if not terms:
            continue
        quantity_matched = bool(quantity and quantity in claim)
        if not quantity_matched and normalized:
            quantity_matched = normalized in claim_numbers and bool(unit and unit in claim)
        if quantity_matched and any(term and term in claim for term in terms):
            return f"user_anchor_{getattr(anchor, 'anchor_id', '') or 'fact'}"
    return ""


def _user_anchor_numbers_in_claim(claim: str, user_fact_anchors: Any) -> set[str]:
    claim_numbers = _numbers(claim)
    anchored: set[str] = set()
    for anchor in list(user_fact_anchors or []):
        normalized = _safe_text(getattr(anchor, "quantity_normalized", ""))
        quantity = _safe_text(getattr(anchor, "quantity_text", ""))
        unit = _safe_text(getattr(anchor, "unit", ""))
        terms = tuple(getattr(anchor, "subject_terms", ()) or ())
        if not normalized or not terms:
            continue
        quantity_matched = bool(quantity and quantity in claim)
        if not quantity_matched:
            quantity_matched = normalized in claim_numbers and bool(unit and unit in claim)
        if quantity_matched and any(term and term in claim for term in terms):
            anchored.add(normalized)
    return anchored


def _has_unanchored_number(claim: str, user_fact_anchors: Any) -> bool:
    claim_numbers = _numbers(claim)
    if not claim_numbers:
        return False
    return bool(claim_numbers - _user_anchor_numbers_in_claim(claim, user_fact_anchors))


def _preserve_user_anchor_units(claim: str, user_fact_anchors: Any) -> str:
    preserved: list[str] = []
    for unit in _split_claim_units(claim):
        if _user_anchor_support(unit, user_fact_anchors):
            preserved.append(unit)
    return "".join(preserved).strip()


def _user_anchor_ids(claim: str, user_fact_anchors: Any) -> list[str]:
    support = _user_anchor_support(claim, user_fact_anchors)
    if not support.startswith("user_anchor_"):
        return []
    anchor_id = support.removeprefix("user_anchor_")
    return [anchor_id] if anchor_id else []


def _current_subject_support(claim: str, references: list[dict[str, str]], target_subject: str) -> str:
    subject = _safe_text(target_subject)
    if _is_generic_subject(subject):
        return ""
    for index, ref in enumerate(references, start=1):
        source = ref["source"]
        text = ref["text"]
        combined = f"{source} {text}"
        if "某" in combined or "XX" in combined.upper():
            continue
        if subject not in combined:
            continue
        if _source_supports_claim(claim, combined, strict_specific=True):
            return source or f"retrieval_{index}"
    return ""


def _strip_unsupported_specifics(text: str) -> str:
    result = text or ""
    result = DATE_RE.sub("", result)
    result = MONEY_RE.sub("相关资金", result)
    result = PERSON_NAME_RE.sub("明确专人负责", result)
    result = re.sub(r"\d+(?:\.\d+)?\s*(?:次|项|人|个|场|件|万元|亿元|元)?", "", result)
    result = re.sub(r"[一二两三四五六七八九十]+小时内", "规定时间内", result)
    result = re.sub(r"在规定时间内上报", "按规定及时报告", result)
    result = re.sub(r"第一时间", "及时", result)
    result = re.sub(r"每\s*(?:日|天|周|月|季度|半年|年)", "定期", result)
    result = re.sub(r"每[日天周月季度半年年]", "定期", result)
    result = re.sub(r"按\s*(?:日|天|周|月|季度|半年|年)", "定期", result)
    result = result.replace("一次", "")
    result = re.sub(r"(?:已|已经|截至目前|截至|截止|今年以来|今年|目前|共|累计|正式)", "", result)
    result = result.replace("全面完成所有", "推进")
    result = result.replace("完成所有", "推进")
    result = result.replace("完成全部", "推进")
    result = result.replace("已建立", "建立健全")
    result = result.replace("建立了", "建立健全")
    result = result.replace("已实行", "实行")
    result = result.replace("已开展", "开展")
    result = result.replace("已投入", "统筹保障")
    result = re.sub(r"\s+", "", result)
    result = re.sub(r"([，,；;、]){2,}", r"\1", result)
    result = result.strip("，,；;、：: ")
    return result


def _soften_plan_claim(sentence: str, claim_type: str) -> str:
    text = (sentence or "").strip()
    if not text:
        return ""
    suffix = "。" if text.endswith("。") else ""

    if re.search(r"截至|截止|目前|共|累计", text) and re.search(r"整改|完成|排查", text):
        return "持续推进问题排查整改。"
    if MONEY_RE.search(text) or PERSON_NAME_RE.search(text):
        return "明确责任分工，按规定统筹相关保障。"
    if DATE_RE.search(text) and re.search(r"已|实施|正式", text):
        return "按规定完善相关制度并组织实施。"
    if re.search(r"已建立|已经建立|建立了", text):
        softened = re.sub(r"已建立|已经建立|建立了", "建立健全", text)
        return softened
    if "风险研判会议" in text and PLAN_PRECISE_TIME_RE.search(text):
        return "定期开展风险研判。"
    if re.search(r"发现问题后.*(?:上报|报告)", text) and PLAN_PRECISE_TIME_RE.search(text):
        return "发现问题后应按规定及时报告。"

    softened = _strip_unsupported_specifics(text)
    if claim_type == LOCAL_COMPLETED_ACTION:
        softened = re.sub(r"^(?:推进)?(所有|全部)", "", softened)
        if "整改" in text and "整改" not in softened:
            softened = "持续推进问题整改"
        elif "专题教育" in text and "专题教育" not in softened:
            softened = "开展专题教育"
    if not softened:
        return ""
    if suffix and not softened.endswith(("。", "；", ";", "：", ":")):
        softened += suffix
    return softened


def _replacement_for(claim_type: str, claim: str = "", document_type: str = "") -> str:
    if _is_plan_document(document_type):
        return _soften_plan_claim(claim, claim_type)
    if claim_type == MANDATORY_POLICY_RULE:
        return PUBLIC_RULE_REPLACEMENT
    return PUBLIC_LOCAL_FACT_REPLACEMENT


class UnsupportedClaimGuard:
    """Find and rewrite unsupported high-risk local assertions.

    The guard is intentionally mechanical. It only treats user-provided facts
    and same-subject evidence as direct support; topical similarity remains
    reference-only.
    """

    def inspect_and_rewrite(
        self,
        candidate_text: str,
        *,
        user_direct_facts: Any = None,
        retrieval_top5: Any = None,
        target_subject: str = "",
        document_type: str = "",
        user_fact_anchors: Any = None,
    ) -> dict[str, Any]:
        text = str(candidate_text or "")
        references = _normalize_references(retrieval_top5)
        claims: list[UnsupportedClaim] = []
        rewritten = text

        anchor_facts = [
            getattr(anchor, "source_snippet", "")
            for anchor in list(user_fact_anchors or [])
            if getattr(anchor, "source_snippet", "")
        ]
        if isinstance(user_direct_facts, list):
            user_facts = list(user_direct_facts) + anchor_facts
        else:
            user_facts = ([user_direct_facts] if user_direct_facts else []) + anchor_facts

        seen_units: set[str] = set()
        protected_anchor_ids_set: set[str] = set()
        for sentence in _split_sentences(text):
            sentence_has_user_anchor = bool(_user_anchor_support(sentence, user_fact_anchors))
            sentence_units = _split_claim_units(sentence) if sentence_has_user_anchor else [sentence]
            for sentence_unit in sentence_units:
                if sentence_unit in seen_units:
                    continue
                seen_units.add(sentence_unit)
                protected_anchor_ids_set.update(_user_anchor_ids(sentence_unit, user_fact_anchors))
                claim_type = _classify_claim(sentence_unit, document_type=document_type)
                if claim_type not in UNSUPPORTED_CLAIM_TYPES:
                    continue

                support_source = _user_anchor_support(sentence_unit, user_fact_anchors)
                has_unanchored_number = _has_unanchored_number(sentence_unit, user_fact_anchors)
                if support_source and has_unanchored_number:
                    support_source = ""
                if not support_source:
                    support_source = _user_direct_support(sentence_unit, user_facts)
                source_category = "USER_DIRECT" if support_source else "REFERENCE_ONLY"
                if not support_source:
                    support_source = _current_subject_support(sentence_unit, references, target_subject)
                    source_category = "CURRENT_SUBJECT_EVIDENCE" if support_source else "REFERENCE_ONLY"

                has_direct_support = bool(support_source)
                replacement = ""
                if not has_direct_support:
                    replacement = _replacement_for(claim_type, sentence_unit, document_type)
                    preserved_anchor_units = _preserve_user_anchor_units(sentence_unit, user_fact_anchors)
                    if preserved_anchor_units:
                        replacement = preserved_anchor_units
                action = "kept_direct_support" if has_direct_support else "replaced_with_placeholder"
                if not has_direct_support and sentence_unit in rewritten:
                    rewritten = rewritten.replace(sentence_unit, replacement, 1)

                claims.append(
                    UnsupportedClaim(
                        sentence=sentence_unit,
                        claim_type=claim_type,
                        has_direct_support=has_direct_support,
                        support_source=support_source,
                        source_category=source_category,
                        action=action,
                        replacement=replacement,
                    )
                )

        unsupported = [claim for claim in claims if not claim.has_direct_support]
        claim_types = sorted({claim.claim_type for claim in unsupported})
        protected_anchor_ids = sorted(protected_anchor_ids_set)
        return {
            "processed_text": rewritten,
            "rewritten_text": rewritten,
            "document_type": document_type,
            "target_subject": target_subject,
            "claims": [claim.__dict__ for claim in claims],
            "protected_user_anchor_ids": protected_anchor_ids,
            "unsupported_claim_count": len(unsupported),
            "unsupported_claim_types": claim_types,
            "rewrite_used": rewritten != text,
            "user_confirmation_required": bool(unsupported),
        }


def render_unsupported_claim_guard_markdown(result: Any) -> str:
    report = result if isinstance(result, dict) else {}
    lines = [
        "# official_draft G3C Unsupported Claim Guard",
        "",
        f"- document_type: {report.get('document_type') or ''}",
        f"- target_subject: {report.get('target_subject') or ''}",
        f"- rewrite_used: {report.get('rewrite_used')}",
        f"- unsupported_claim_count: {report.get('unsupported_claim_count', 0)}",
        f"- unsupported_claim_types: {', '.join(report.get('unsupported_claim_types') or []) or 'none'}",
        "",
    ]
    for index, claim in enumerate(report.get("claims", []) or [], start=1):
        lines.extend(
            [
                f"## {index}. {claim.get('claim_type')}",
                f"- sentence: {claim.get('sentence')}",
                f"- has_direct_support: {claim.get('has_direct_support')}",
                f"- source_category: {claim.get('source_category')}",
                f"- support_source: {claim.get('support_source') or 'none'}",
                f"- action: {claim.get('action')}",
                f"- replacement: {claim.get('replacement') or 'none'}",
                "",
            ]
        )
    return "\n".join(lines)
