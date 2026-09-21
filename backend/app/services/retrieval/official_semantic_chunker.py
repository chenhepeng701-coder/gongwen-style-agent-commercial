"""Deterministic, structure-aware chunking for Chinese official documents."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.services.retrieval.official_chunk_metadata import (
    SCHEMA_VERSION,
    build_chunk_metadata,
    document_level_metadata,
    make_chunk_id,
    make_document_id,
)


TARGET_CHUNK_CHARS = 500
MIN_CHUNK_CHARS = 120
MAX_CHUNK_CHARS = 850
OVERLAP_CHARS = 0

# Kept as an explicit rollback point in embedder.py; new semantic chunks do not use overlap.
LEGACY_CHUNK_SIZE = 500
LEGACY_CHUNK_OVERLAP = 50

_CN_NUM = "一二三四五六七八九十百"
_ORDINAL_HEADING_PATTERN = re.compile(rf"^第[{_CN_NUM}]+[，,、.．]\s*.+")
_INLINE_ENUM_HEADING_PATTERN = re.compile(rf"^[{_CN_NUM}]{{1,3}}是[，,：:]?.+")
_SEQUENCE_HEADING_PATTERN = re.compile(r"^(?:首先|其次|再次|最后)[，,：:]?.+")
_ARABIC_HEADING_PATTERN = re.compile(r"^\d{1,3}[.．、]\s*(?P<title>.+)$")
_HEADING_PATTERNS: tuple[tuple[int, re.Pattern[str]], ...] = (
    (1, re.compile(rf"^[{_CN_NUM}]+[、.．]\s*.+")),
    (2, re.compile(rf"^[（(][{_CN_NUM}]+[）)]\s*.+")),
    (4, re.compile(r"^[（(]\d{1,3}[）)]\s*.+")),
)
_ATTACHMENT_PATTERN = re.compile(r"^(?:附件|附录)(?:\s*\d+)?(?:[：:、.]|\s|$)")
_QUOTED_SCOPE_PATTERN = re.compile(
    r"(?P<quoted>[“\"][^”\"]{1,20}[”\"])\s*"
    r"(?P<suffix>[\u4e00-\u9fff]{0,6}(?:目标|要求|任务|举措|措施|原则|方面|重点))"
)
_SALUTATION_PATTERN = re.compile(r"^(?:各位领导、?同志们|同志们|各位领导|各位同志|同志们、朋友们)[:：]\s*$")
_PUNCTUATION_ONLY = re.compile(r"^[\s\-—_=+·•，。！？；：、,.!?;:（）()【】\[\]<>《》]+$")
_SENTENCE_BOUNDARY = re.compile(r"(?<=[。！？；.!?;])")
_CLAUSE_BOUNDARY = re.compile(r"(?<=[，、：,:])")
_TITLE_SUFFIXES = (
    "情况", "问题", "不足", "措施", "要求", "成效", "进展", "机制", "安排", "建议",
    "目标", "原则", "范围", "任务", "重点", "总体情况", "工作回顾", "下一步工作", "结语",
)
_DOCUMENT_TYPE_WORDS = (
    "讲话", "讲稿", "报告", "总结", "汇报", "通知", "方案", "意见", "决定", "通报",
    "请示", "批复", "函", "纪要", "计划", "述职", "党课",
)


def normalize_paragraphs(text: str) -> list[str]:
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    return [line.strip() for line in normalized.split("\n") if _is_meaningful(line)]


def _is_meaningful(text: str) -> bool:
    value = (text or "").strip()
    return bool(value and not _PUNCTUATION_ONLY.fullmatch(value))


def _heading_info(paragraph: str, current_depth: int = 0) -> tuple[int, str] | None:
    text = (paragraph or "").strip()
    if not text:
        return None
    if text.count("\t") >= 2:
        return None  # tabular row, not a numbered section heading
    if _ATTACHMENT_PATTERN.match(text):
        return 1, _heading_title(text)
    if _ORDINAL_HEADING_PATTERN.match(text):
        return min(max(current_depth + 1, 1), 4), _heading_title(text)
    if _INLINE_ENUM_HEADING_PATTERN.match(text) or _SEQUENCE_HEADING_PATTERN.match(text):
        return min(max(current_depth + 1, 1), 4), _heading_title(text)
    arabic_match = _ARABIC_HEADING_PATTERN.match(text)
    if arabic_match:
        title_text = arabic_match.group("title").strip()
        if _is_concise_numbered_heading(title_text):
            return 3, _heading_title(text)
        return None
    for level, pattern in _HEADING_PATTERNS:
        if pattern.match(text):
            return level, _heading_title(text)
    colon_title = text.rstrip("：:").strip()
    if (
        text.endswith(("：", ":"))
        and not _SALUTATION_PATTERN.match(text)
        and (len(colon_title) <= 12 or (len(colon_title) <= 20 and colon_title.endswith(_TITLE_SUFFIXES)))
    ):
        return min(max(current_depth + 1, 1), 4), colon_title
    if len(text) <= 24 and text.endswith(_TITLE_SUFFIXES) and not text.endswith(("。", "！", "？")):
        return min(max(current_depth + 1, 1), 4), text
    return None


def _is_concise_numbered_heading(title_text: str) -> bool:
    """Distinguish a short numbered heading from a complete numbered statement."""
    value = (title_text or "").strip()
    if not value or len(value) > 32:
        return False
    if value.endswith(("。", "！", "？", "；", ".", "!", "?", ";")):
        return False
    if re.search(r"[。！？；.!?;]", value):
        return False
    return True


def _heading_family(text: str) -> str:
    value = (text or "").strip()
    if _ATTACHMENT_PATTERN.match(value):
        return "attachment"
    if _ORDINAL_HEADING_PATTERN.match(value):
        return "ordinal"
    if _INLINE_ENUM_HEADING_PATTERN.match(value) or _SEQUENCE_HEADING_PATTERN.match(value):
        return "inline_enum"
    return "fixed"


def _lead_in_scope_title(text: str, next_text: str) -> str:
    """Extract a strict quoted scope label when a colon introduces a numbered series."""
    value = (text or "").strip()
    if not value.endswith(("：", ":")) or _heading_family(next_text) not in {"ordinal", "inline_enum"}:
        return ""
    matches = list(_QUOTED_SCOPE_PATTERN.finditer(value))
    if not matches:
        return ""
    match = matches[-1]
    return f"{match.group('quoted')}{match.group('suffix')}"


def _heading_title(text: str) -> str:
    value = (text or "").strip()
    # Inline enumerations such as “一是……。具体……” use only their first clause as path label.
    for delimiter in ("。", "；", "：", ":"):
        pos = value.find(delimiter)
        if 0 < pos <= 60:
            return value[:pos].strip()
    return value[:80].strip()


def is_section_heading(paragraph: str) -> bool:
    return _heading_info(paragraph) is not None


def _looks_like_document_title(text: str) -> bool:
    value = (text or "").strip()
    if not value or len(value) > 100 or _SALUTATION_PATTERN.match(value) or _ATTACHMENT_PATTERN.match(value):
        return False
    if _heading_info(value):
        return False
    if value.endswith(("。", "！", "？", ".", "!", "?")):
        return False
    return any(word in value for word in _DOCUMENT_TYPE_WORDS) or len(value) <= 50


def classify_paragraph_type(paragraph: str, *, index: int = 0, section_heading: str = "") -> str:
    """Compatibility alias retained for existing consumers and tests."""
    text = (paragraph or "").strip()
    if not text:
        return "unknown"
    if index == 0 and _looks_like_document_title(text):
        return "title"
    if _SALUTATION_PATTERN.match(text):
        return "salutation"
    heading_context = re.sub(r"\s+", "", section_heading or "")
    compact = re.sub(r"\s+", "", text)
    combined = heading_context + compact
    if any(word in heading_context for word in ("部署", "推进", "开展", "建立", "完善", "落实", "聚焦", "整治", "强化", "深化", "统筹", "查摆", "促改")):
        return "measure"
    if any(word in heading_context for word in ("成效", "进展", "现状", "情况")):
        return "current_status"
    if any(word in combined for word in ("存在问题", "存在不足", "主要不足", "短板弱项", "差距不足", "问题表现", "主要原因")):
        return "problem"
    if any(word in combined for word in ("措施", "做法", "部署", "推进", "落实", "聚焦", "整治", "统筹", "深化", "查摆", "促改", "抓好", "强化", "开展", "建立", "完善")):
        return "measure"
    if any(word in combined for word in ("要求", "必须", "要把", "要以", "要坚持", "纪律", "责任")):
        return "requirement"
    if any(word in combined for word in ("背景", "根据", "按照", "围绕", "为深入", "为贯彻", "形势")):
        return "background"
    if any(word in combined for word in ("目前", "当前", "近年来", "今年以来", "现状", "基础", "成效")):
        return "current_status"
    if any(word in compact for word in ("最后", "总之", "让我们", "希望大家")):
        return "conclusion"
    if is_section_heading(text):
        return "transition"
    return "unknown"


def _infer_content_role(
    text: str,
    *,
    section_title: str = "",
    section_path: list[str] | None = None,
    is_title: bool = False,
    is_attachment: bool = False,
) -> str:
    if is_title:
        return "title"

    path = list(section_path or ([] if not section_title else [section_title]))
    compact_path = [re.sub(r"\s+", "", item) for item in path if item]
    current_heading = compact_path[-1] if compact_path else re.sub(r"\s+", "", section_title or "")

    # Explicit section semantics outrank incidental words in the body.
    if any(any(word in heading for word in ("存在的问题", "存在问题", "问题与不足", "问题和不足", "短板", "风险隐患")) for heading in compact_path):
        return "problem"
    if any(any(word in heading for word in ("责任", "职责")) for heading in compact_path):
        return "responsibility"
    if any(any(word in heading for word in ("结语", "结束语")) for heading in compact_path):
        return "conclusion"
    for heading in reversed(compact_path):
        if any(word in heading for word in ("工作要求", "部署要求")):
            return "requirement"
        if any(word in heading for word in ("工作成效", "取得成效", "主要成绩")):
            return "result"
        if any(word in heading for word in ("下一步工作", "工作打算", "主要任务", "具体举措", "工作措施")):
            return "measure"
    if re.match(rf"^(?:第[{_CN_NUM}]+[，,、.．]|[{_CN_NUM}]{{1,3}}是)", current_heading):
        if re.search(r"(?:要|应|须|必须|不得|严禁)", current_heading):
            return "requirement"
        if re.search(r"(?:强化|加强|完善|推进|落实|提升|深化|严守|严格|精益求精)", current_heading):
            return "measure"

    compact = re.sub(r"\s+", "", text or "")
    signals: list[str] = []
    rules = (
        ("problem", ("存在问题", "存在不足", "短板弱项", "问题与不足", "问题和不足", "风险隐患")),
        ("responsibility", ("责任分工", "主体责任", "第一责任人", "责任到人", "牵头单位", "履行职责")),
        ("mechanism", ("长效机制", "制度机制", "闭环管理", "常态长效", "工作机制")),
        ("result", ("取得成效", "实现了", "完成率", "同比增长", "同比下降", "提升了")),
        ("progress", ("进展情况", "目前", "当前", "截至", "已完成", "正在推进")),
        ("measure", ("主要措施", "工作举措", "采取", "推进", "落实", "开展", "完善", "强化", "建立")),
        ("requirement", ("工作要求", "必须", "应当", "要坚持", "要切实", "严禁", "不得")),
        ("background", ("根据", "按照", "为贯彻", "背景", "形势")),
        ("summary", ("总体情况", "工作总结", "总体来看", "综上")),
        ("conclusion", ("最后", "总之", "让我们", "希望大家")),
    )
    for role, keywords in rules:
        if any(keyword in compact for keyword in keywords):
            signals.append(role)
    if re.search(r"\d", compact) and any(unit in compact for unit in ("%", "％", "万元", "亿元", "人次", "项", "个", "年", "月")):
        signals.append("data")
    if re.match(r"^(?:例如|比如|典型案例|以案为鉴)", compact):
        signals.append("example")
    if not any(role == "requirement" for role in signals) and re.search(r"(?:^|[。；])(?:要|应|须)[^。；]{2,}", compact):
        signals.insert(0, "requirement")
    unique = list(dict.fromkeys(signals))
    if not unique:
        return "attachment" if is_attachment else "unknown"
    if len(unique) == 1:
        return unique[0]
    # A numeric result/measure remains more useful as mixed than as an overconfident single role.
    return "mixed"


def _infer_reference_type(text: str, *, role: str, has_heading: bool) -> str:
    if role == "title":
        return "style"
    concrete = bool(
        re.search(r"\d", text)
        or any(word in text for word in ("截至", "万元", "亿元", "责任单位", "政策", "条例", "办法", "方案"))
    )
    if concrete and has_heading:
        return "both"
    if concrete or role in {"data", "result", "example", "progress"}:
        return "content"
    if has_heading or role in {"problem", "measure", "requirement", "responsibility", "mechanism", "summary", "conclusion"}:
        return "both"
    return "unknown"


def _source_records(text: str, paragraphs: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if paragraphs is not None:
        records = []
        for fallback_index, item in enumerate(paragraphs):
            value = str(item.get("text") or "").strip()
            if not _is_meaningful(value):
                continue
            records.append(
                {
                    "text": value,
                    "paragraph_index": int(item.get("paragraph_index", fallback_index)),
                    "kind": str(item.get("kind") or "paragraph"),
                }
            )
        return records
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    return [
        {"text": line.strip(), "paragraph_index": index, "kind": "paragraph"}
        for index, line in enumerate(normalized.split("\n"))
        if _is_meaningful(line)
    ]


def _annotate_records(records: list[dict[str, Any]], title_text: str) -> list[dict[str, Any]]:
    stack: list[tuple[int, str, str]] = []
    section_index = 0
    attachment_mode = False
    attachment_title = ""
    annotated: list[dict[str, Any]] = []
    title_used = False
    for order, source in enumerate(records):
        text = source["text"]
        is_doc_title = not title_used and text == title_text and _looks_like_document_title(text)
        if is_doc_title:
            title_used = True
        is_attachment_heading = bool(not is_doc_title and _ATTACHMENT_PATTERN.match(text))
        info = None if is_doc_title or source.get("kind") == "table" else _heading_info(text, len(stack))
        family = _heading_family(text) if info else ""
        lead_in_title = ""
        if not is_doc_title and source.get("kind") != "table" and order + 1 < len(records):
            lead_in_title = _lead_in_scope_title(text, records[order + 1]["text"])
            if lead_in_title:
                info = (min((stack[-1][0] if stack else 0) + 1, 4), lead_in_title)
                family = "lead_in"
        heading_has_body = bool(
            info
            and (
                lead_in_title
                or re.match(rf"^(?:[{_CN_NUM}]{{1,3}}是|首先|其次|再次|最后)", text)
                or any(mark in text for mark in ("。", "！", "？", ".", "!", "?"))
            )
        )
        if info:
            level, heading_title = info
            if is_attachment_heading:
                attachment_mode = True
                attachment_title = heading_title
                stack.clear()
            else:
                if family in {"ordinal", "inline_enum"}:
                    previous_sibling = next((item for item in reversed(stack) if item[2] == family), None)
                    level = previous_sibling[0] if previous_sibling else min((stack[-1][0] if stack else 0) + 1, 4)
                while stack and stack[-1][0] >= level:
                    stack.pop()
                stack.append((level, heading_title, family))
            section_index += 1
        section_path = ([attachment_title] if attachment_mode and attachment_title else []) + [item[1] for item in stack]
        level = len(section_path)
        section_title = section_path[-1] if section_path else ""
        paragraph_type = classify_paragraph_type(
            text,
            index=0 if is_doc_title else order,
            section_heading=section_title,
        )
        role = _infer_content_role(
            text,
            section_title=section_title,
            section_path=section_path,
            is_title=is_doc_title,
            is_attachment=attachment_mode or source.get("kind") == "table" and attachment_mode,
        )
        annotated.append(
            {
                **source,
                "is_heading": bool(info),
                "heading_has_body": heading_has_body,
                "is_doc_title": is_doc_title,
                "is_attachment_heading": is_attachment_heading,
                "is_attachment": attachment_mode,
                "section_path": section_path,
                "section_level": level,
                "section_index": section_index,
                "section_title": section_title,
                "paragraph_type": paragraph_type,
                "content_role": role,
            }
        )
    return annotated


def _split_at_boundaries(text: str, limit: int) -> list[str]:
    value = text.strip()
    if len(value) <= limit or limit < 80:
        return [value]
    for pattern in (_SENTENCE_BOUNDARY, _CLAUSE_BOUNDARY):
        units = [item for item in pattern.split(value) if item]
        if len(units) <= 1:
            continue
        pieces: list[str] = []
        current = ""
        for unit in units:
            if current and len(current) + len(unit) > limit:
                pieces.append(current.strip())
                current = unit
            else:
                current += unit
        if current.strip():
            pieces.append(current.strip())
        if pieces and all(len(piece) <= limit for piece in pieces):
            return pieces
    # Whitespace is a safe final boundary for Latin/numeric-heavy paragraphs.
    words = value.split()
    if len(words) > 1:
        pieces, current = [], ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if current and len(candidate) > limit:
                pieces.append(current)
                current = word
            else:
                current = candidate
        if current:
            pieces.append(current)
        if all(len(piece) <= limit for piece in pieces):
            return pieces
    # Do not guess Chinese word boundaries for an unpunctuated pathological string.
    return [value]


def _atomic_records(records: list[dict[str, Any]], max_chars: int) -> list[dict[str, Any]]:
    atoms: list[dict[str, Any]] = []
    for record in records:
        pieces = _split_at_boundaries(record["text"], max_chars)
        for part_index, piece in enumerate(pieces):
            atoms.append(
                {
                    **record,
                    "text": piece,
                    "is_heading": record["is_heading"] and part_index == 0,
                    "secondary_split": len(pieces) > 1,
                    "split_part": part_index,
                    "oversized_unsplittable": len(piece) > max_chars,
                }
            )
    return atoms


def _same_merge_scope(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return (
        left.get("section_path") == right.get("section_path")
        and bool(left.get("is_attachment")) == bool(right.get("is_attachment"))
    )


def _common_section_path(atoms: list[dict[str, Any]]) -> list[str]:
    paths = [list(item.get("section_path") or []) for item in atoms]
    if not paths:
        return []
    longest = max(paths, key=len)
    if all(longest[: len(path)] == path for path in paths):
        return list(longest)
    common = list(paths[0])
    for path in paths[1:]:
        prefix: list[str] = []
        for left, right in zip(common, path):
            if left != right:
                break
            prefix.append(left)
        common = prefix
    return common


def _make_group(atoms: list[dict[str, Any]], *, short_merged: bool = False) -> dict[str, Any]:
    text = "\n".join(item["text"] for item in atoms).strip()
    short_merged = short_merged or (
        len(atoms) > 1 and any(len(item["text"]) < MIN_CHUNK_CHARS for item in atoms)
    )
    has_heading = any(item["is_heading"] or item["is_doc_title"] for item in atoms)
    flags: list[str] = []
    if any(item["secondary_split"] for item in atoms):
        flags.append("secondary_split")
    if any(item["oversized_unsplittable"] for item in atoms):
        flags.append("oversized_unsplittable")
    if short_merged:
        flags.append("short_chunk_merged")
    common_path = _common_section_path(atoms)
    is_attachment = any(item["is_attachment"] for item in atoms)
    role = _infer_content_role(
        text,
        section_title=common_path[-1] if common_path else "",
        section_path=common_path,
        is_title=all(item["is_doc_title"] for item in atoms),
        is_attachment=is_attachment,
    )
    reference_type = _infer_reference_type(text, role=role, has_heading=has_heading)
    return {
        "atoms": atoms,
        "chunk_text": text,
        "paragraph_start": min(item["paragraph_index"] for item in atoms),
        "paragraph_end": max(item["paragraph_index"] for item in atoms),
        "section_path": common_path,
        "section_level": len(common_path),
        "section_index": int(atoms[-1].get("section_index") or 0),
        "section_title": common_path[-1] if common_path else "",
        "paragraph_type": next((item["paragraph_type"] for item in atoms if item["paragraph_type"] not in {"transition", "unknown"}), atoms[0]["paragraph_type"]),
        "content_role": role,
        "content_reference_type": reference_type,
        "is_title_chunk": any(item["is_doc_title"] for item in atoms),
        "is_attachment": is_attachment,
        "secondary_split": any(item["secondary_split"] for item in atoms),
        "short_merged": short_merged,
        "quality_flags": flags,
    }


def _initial_groups(atoms: list[dict[str, Any]], target_chars: int, max_chars: int) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []

    def flush() -> None:
        nonlocal current
        if current:
            groups.append(_make_group(current))
            current = []

    for atom in atoms:
        if atom["is_heading"] and current:
            flush()
        if current and not _same_merge_scope(current[-1], atom):
            flush()
        prospective = len("\n".join(item["text"] for item in current + [atom]))
        if current and prospective > max_chars:
            # Never strand a heading: give the following text the remaining safe capacity.
            if len(current) == 1 and (current[0]["is_heading"] or current[0]["is_doc_title"]):
                capacity = max_chars - len(current[0]["text"]) - 1
                pieces = _split_at_boundaries(atom["text"], capacity)
                if len(pieces) > 1:
                    first = {**atom, "text": pieces[0], "secondary_split": True}
                    current.append(first)
                    flush()
                    for position, piece in enumerate(pieces[1:]):
                        part = {**atom, "text": piece, "secondary_split": True, "split_part": atom["split_part"] + position + 1}
                        if current and len("\n".join(item["text"] for item in current + [part])) > max_chars:
                            flush()
                        current.append(part)
                    continue
            flush()
        elif current and len("\n".join(item["text"] for item in current)) >= target_chars:
            flush()
        current.append(atom)
    flush()
    return groups


def _merge_short_groups(groups: list[dict[str, Any]], min_chars: int, max_chars: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    index = 0
    while index < len(groups):
        group = groups[index]
        if len(group["chunk_text"]) >= min_chars:
            result.append(group)
            index += 1
            continue
        structural_only = all(
            item["is_doc_title"] or (item["is_heading"] and not item.get("heading_has_body"))
            for item in group["atoms"]
        )
        next_is_descendant = (
            index + 1 < len(groups)
            and groups[index + 1].get("section_path", [])[: len(group.get("section_path", []))]
            == group.get("section_path", [])
        )
        may_merge_forward = index + 1 < len(groups) and (
            _same_merge_scope(group, groups[index + 1])
            or (
                structural_only
                and not all(item["is_doc_title"] for item in group["atoms"])
                and next_is_descendant
            )
            or (
                bool(group.get("is_attachment"))
                and len(group.get("section_path", [])) == 1
                and any(item.get("is_attachment_heading") for item in group["atoms"])
                and next_is_descendant
            )
        )
        if may_merge_forward:
            combined_atoms = group["atoms"] + groups[index + 1]["atoms"]
            combined_text = "\n".join(item["text"] for item in combined_atoms)
            if len(combined_text) <= max_chars:
                result.append(_make_group(combined_atoms, short_merged=True))
                index += 2
                continue
        if result and _same_merge_scope(result[-1], group):
            combined_atoms = result[-1]["atoms"] + group["atoms"]
            combined_text = "\n".join(item["text"] for item in combined_atoms)
            if len(combined_text) <= max_chars:
                result[-1] = _make_group(combined_atoms, short_merged=True)
                index += 1
                continue
        result.append(group)
        index += 1
    return result


def semantic_chunk_document(
    text: str,
    *,
    document_id: str = "",
    source_file: str = "",
    source_path: str = "",
    title: str = "",
    paragraphs: list[dict[str, Any]] | None = None,
    target_chunk_size: int = TARGET_CHUNK_CHARS,
    min_chunk_size: int = MIN_CHUNK_CHARS,
    max_chunk_size: int = MAX_CHUNK_CHARS,
    min_structure_markers: int = 1,
) -> list[dict[str, Any]]:
    del min_structure_markers  # Kept for source compatibility; all inputs now use paragraph semantics.
    records = _source_records(text, paragraphs)
    if not records:
        return []

    resolved_source_file = source_file or (Path(source_path).name if source_path else "")
    first_text = records[0]["text"]
    title_candidate = title.strip() if _looks_like_document_title(title) else ""
    if not title_candidate and _looks_like_document_title(first_text):
        title_candidate = first_text
    resolved_title = title_candidate or Path(resolved_source_file).stem or "未识别标题"
    title_source = "provided" if title_candidate and title.strip() == title_candidate else (
        "detected" if title_candidate else "filename"
    )
    full_text = "\n".join(item["text"] for item in records)
    resolved_doc_id = document_id or make_document_id(
        source_path=source_path,
        source_file=resolved_source_file,
        title=resolved_title,
        text=full_text,
    )
    doc_meta = document_level_metadata(
        title=resolved_title,
        source_file=resolved_source_file,
        source_path=source_path,
        text=full_text,
    )

    annotated = _annotate_records(records, title_candidate)
    atoms = _atomic_records(annotated, max_chunk_size)
    groups = _initial_groups(atoms, target_chunk_size, max_chunk_size)
    groups = _merge_short_groups(groups, min_chunk_size, max_chunk_size)

    chunks: list[dict[str, Any]] = []
    for group in groups:
        chunk_text = group["chunk_text"].strip()
        if not _is_meaningful(chunk_text):
            continue
        structural_only = all(
            item["is_doc_title"] or (item["is_heading"] and not item.get("heading_has_body"))
            for item in group["atoms"]
        )
        title_metadata_chunk = all(item["is_doc_title"] for item in group["atoms"]) and len(groups) > 1
        if structural_only and not title_metadata_chunk:
            # A document/section title with no body is represented by document_title/section_path,
            # not emitted as a retrieval chunk with no usable reference content.
            continue
        chunk_index = len(chunks)
        chunk_id = make_chunk_id(
            resolved_doc_id,
            chunk_index,
            content=chunk_text,
            paragraph_start=group["paragraph_start"],
            paragraph_end=group["paragraph_end"],
            section_path=group["section_path"],
        )
        metadata = build_chunk_metadata(
            document_id=resolved_doc_id,
            chunk_id=chunk_id,
            source_file=resolved_source_file,
            source_path=source_path,
            title=resolved_title,
            chunk_text=chunk_text,
            chunk_index=chunk_index,
            paragraph_type=group["paragraph_type"],
            section_heading=group["section_title"],
            section_index=group["section_index"],
            chunk_strategy="official_semantic_v0.4R-7.6",
            document_metadata=doc_meta,
            document_title_source=title_source,
            section_path=group["section_path"],
            section_level=group["section_level"],
            paragraph_start=group["paragraph_start"],
            paragraph_end=group["paragraph_end"],
            content_role=group["content_role"],
            content_reference_type=group["content_reference_type"],
            is_title_chunk=group["is_title_chunk"],
            is_attachment=group["is_attachment"],
            quality_flags=group["quality_flags"],
        )
        chunks.append(
            {
                "chunk_id": chunk_id,
                "document_id": resolved_doc_id,
                "source_file": resolved_source_file,
                "title": resolved_title,
                "document_title": resolved_title,
                "document_type": metadata["document_type"],
                "level": metadata["level"],
                "org_type": metadata["org_type"],
                "business_domain": metadata["business_domain"],
                "writing_purpose": metadata["writing_purpose"],
                "section_title": metadata["section_title"],
                "section_heading": metadata["section_heading"],
                "section_path": metadata["section_path"],
                "section_level": metadata["section_level"],
                "section_index": metadata["section_index"],
                "chunk_index": chunk_index,
                "paragraph_start": metadata["paragraph_start"],
                "paragraph_end": metadata["paragraph_end"],
                "paragraph_type": metadata["paragraph_type"],
                "content_role": metadata["content_role"],
                "content_reference_type": metadata["content_reference_type"],
                "is_title_chunk": metadata["is_title_chunk"],
                "is_attachment": metadata["is_attachment"],
                "chunk_text": chunk_text,
                "content": chunk_text,
                "char_count": len(chunk_text),
                "chunk_length": len(chunk_text),
                "chunk_strategy": metadata["chunk_strategy"],
                "secondary_split": group["secondary_split"],
                "short_merged": group["short_merged"],
                "quality_flags": metadata["quality_flags"],
                "schema_version": SCHEMA_VERSION,
                "metadata": metadata,
            }
        )
    return chunks
