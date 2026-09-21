"""Deterministic, fact-free structure extraction for reference documents.

The extractor reads only the already retrieved primary-document chunks.  It
never exposes source prose or headings to the generation model.  All emitted
semantic values come from controlled vocabularies, so the result can guide
organization without becoming a second fact channel.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

from app.services.generation.work_summary_planner import infer_reference_scenario


LEGACY_SCHEMA_VERSION = "reference-structure-1"
SCHEMA_VERSION = "reference-structure-2"
SOURCE_ROLE = "REFERENCE_STRUCTURE_ONLY_NOT_FACT_SOURCE"
EMPTY_STATUS = "EMPTY_NO_REFERENCE_STRUCTURE"
EXTRACTED_STATUS = "EXTRACTED"

_TOP_HEADING_RE = re.compile(r"^\s*[一二三四五六七八九十百]+[、.．]\s*(?P<body>[^\n]{1,80})")
_SECONDARY_HEADING_RE = re.compile(
    r"^\s*(?:[（(][一二三四五六七八九十百]+[）)]|[一二三四五六七八九十百]+是)\s*(?P<body>[^\n]{1,80})"
)

_DOCUMENT_TYPES = (
    ("调研报告", "调研报告"),
    ("情况报告", "情况报告"),
    ("工作报告", "工作报告"),
    ("工作总结", "工作总结"),
    ("汇报材料", "汇报材料"),
    ("请示", "请示"),
    ("通知", "通知"),
    ("方案", "工作方案"),
    ("讲话", "讲话稿"),
    ("纪要", "会议纪要"),
    ("函", "函"),
)

_SECTION_ROLE_LABELS = {
    "background_introduction": "开头背景",
    "work_progress": "工作开展情况",
    "work_deployment": "工作部署",
    "achievement_summary": "成效总结",
    "problem_analysis": "存在问题",
    "next_step_arrangement": "下一步安排",
}

_PARAGRAPH_FUNCTION_LABELS = {
    "background_explanation": "背景说明",
    "work_deployment": "工作部署",
    "measure_development": "措施展开",
    "achievement_summary": "成效总结",
    "problem_analysis": "问题分析",
    "next_step_arrangement": "下一步安排",
}

_FOCUS_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("问题", "不足", "短板", "差距"), "问题分析"),
    (("下一步", "计划", "打算", "安排"), "下一步安排"),
    (("责任", "政治站位", "组织领导"), "责任体系建设"),
    (("理论", "学习", "思想", "共识"), "理论武装"),
    (("阵地", "舆情", "信息发布"), "阵地管理"),
    (("宣传", "舆论", "引导"), "宣传引导"),
    (("队伍", "人才", "干部培训"), "队伍建设"),
    (("制度", "机制", "督查", "考核", "清单"), "制度安排与督查落实"),
    (("风险", "隐患", "防范"), "风险防控"),
    (("协同", "联动", "统筹"), "协同推进"),
    (("服务", "保障"), "服务保障"),
    (("成效", "成果", "成绩"), "成效归纳"),
)

_ALLOWED_DOCUMENT_TYPES = {value for _, value in _DOCUMENT_TYPES} | {"未识别"}
_ALLOWED_SECTION_ROLES = set(_SECTION_ROLE_LABELS)
_ALLOWED_FOCUSES = {label for _, label in _FOCUS_RULES} | {
    "工作推进",
    "问题分析",
    "下一步安排",
    "背景说明",
    "成效归纳",
}
_ALLOWED_PARAGRAPH_FUNCTIONS = set(_PARAGRAPH_FUNCTION_LABELS)
_FACT_BOUNDARY = [
    "不提供事实锚点或evidence_id",
    "不提供时间、数字、机构、事件、职责或成果",
    "不复制范文标题、章节标题或正文原句",
]

_LEGACY_REFERENCE_KEYS = {
    "schema_version",
    "status",
    "source_role",
    "document_type",
    "primary_structure",
    "overall_progression",
    "paragraph_function_sequence",
    "fact_boundary",
}
_REFERENCE_KEYS = _LEGACY_REFERENCE_KEYS | {
    "scenario_profile",
    "reference_use",
}
_SECTION_KEYS = {
    "section_role",
    "organization_focus",
    "development_logic",
    "paragraph_functions",
    "subsections",
}
_LEGACY_SECTION_KEYS = _SECTION_KEYS - {"subsections"}
_SUBSECTION_KEYS = {
    "subsection_id",
    "topic",
    "function",
    "order",
    "paragraph_functions",
}
_SUBSECTION_ID_RE = re.compile(r"^SEC-\d{2}-SUB-\d{2}$")
_SUBSECTION_LEADING_RE = re.compile(
    r"^(?:持续|严格|全面|深入|扎实|切实|着力|聚焦|围绕|坚持|从严|"
    r"强化|细化|健全|完善|加强|提升|深化|丰富|开展|抓好|推进|落实)+"
)
_SUBSECTION_TAIL_RE = re.compile(
    r"(?:仍需|仍有|存在|有待|需要).*$"
)
_SUBSECTION_FACT_VALUE_RE = re.compile(
    r"(?:\d|20[0-9]{2}年|[一二三四五六七八九十百千万]+(?:次|项|个|人|万元)|"
    r"《|》|会议|文件|项目|工程|成果|成效|办公室|委员会|人民政府|党组)"
)


def empty_reference_structure() -> dict[str, Any]:
    """Return the stable no-reference shape used for graceful degradation."""
    return {
        "schema_version": SCHEMA_VERSION,
        "status": EMPTY_STATUS,
        "source_role": SOURCE_ROLE,
        "document_type": "未识别",
        "scenario_profile": {
            "scenario_id": "unknown",
            "confidence": "FALLBACK",
            "matched_by": "no_reference",
        },
        "reference_use": "STRUCTURE_CANDIDATE",
        "primary_structure": [],
        "overall_progression": [],
        "paragraph_function_sequence": [],
        "fact_boundary": list(_FACT_BOUNDARY),
    }


def _value(item: Mapping[str, Any], name: str) -> Any:
    metadata = item.get("metadata")
    if isinstance(metadata, Mapping) and metadata.get(name) is not None:
        return metadata.get(name)
    return item.get(name)


def _chunk_identity(item: Mapping[str, Any]) -> tuple[int, str]:
    try:
        index = int(item.get("chunk_index", 0) or 0)
    except (TypeError, ValueError):
        index = 0
    return index, str(item.get("chunk_id") or "")


def _primary_chunks(retrieval_payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    primary_id = str(retrieval_payload.get("primary_document_id") or "")
    if not primary_id:
        return []

    candidates: list[Mapping[str, Any]] = []
    raw = retrieval_payload.get("raw_chunk_candidates")
    if isinstance(raw, list):
        candidates.extend(item for item in raw if isinstance(item, Mapping))
    selected = retrieval_payload.get("selected_chunks")
    if isinstance(selected, list):
        candidates.extend(item for item in selected if isinstance(item, Mapping))

    deduped: dict[tuple[int, str], dict[str, Any]] = {}
    for item in candidates:
        document_id = str(item.get("document_id") or item.get("doc_id") or "")
        if document_id != primary_id:
            continue
        text = str(item.get("chunk_text") or item.get("text") or "").strip()
        if not text:
            continue
        normalized = dict(item)
        normalized["chunk_text"] = text
        identity = _chunk_identity(normalized)
        existing = deduped.get(identity)
        if existing is None or len(text) > len(str(existing.get("chunk_text") or "")):
            deduped[identity] = normalized
    return [deduped[key] for key in sorted(deduped)]


def _source_title(retrieval_payload: Mapping[str, Any], chunks: list[dict[str, Any]]) -> str:
    primary_id = str(retrieval_payload.get("primary_document_id") or "")
    for item in retrieval_payload.get("ranked_documents") or []:
        if isinstance(item, Mapping) and str(item.get("document_id") or "") == primary_id:
            title = str(item.get("source_title") or "").strip()
            if title:
                return title
    for chunk in chunks:
        title = str(chunk.get("source_title") or chunk.get("title") or "").strip()
        if title:
            return title
    return ""


def _infer_document_type(title: str, chunks: list[dict[str, Any]]) -> str:
    sample = title
    if chunks:
        sample = f"{sample}\n{str(chunks[0].get('chunk_text') or '')[:160]}"
    if "落实情况报告" in sample or "情况报告" in sample:
        return "情况报告"
    for marker, label in _DOCUMENT_TYPES:
        if marker in sample:
            return label
    return "未识别"


def _heading_candidates(chunk: Mapping[str, Any]) -> Iterable[str]:
    seen: set[str] = set()
    for value in (
        chunk.get("section_heading"),
        _value(chunk, "section_heading"),
        _value(chunk, "section_title"),
        _value(chunk, "heading"),
    ):
        heading = str(value or "").strip()
        if heading and heading not in seen:
            seen.add(heading)
            yield heading
    text = str(chunk.get("chunk_text") or "")
    for line in text.splitlines()[:3]:
        heading = line.strip()
        if heading and heading not in seen:
            seen.add(heading)
            yield heading


def _section_role(heading: str) -> str:
    if any(marker in heading for marker in ("问题", "不足", "短板", "差距")):
        return "problem_analysis"
    if any(marker in heading for marker in ("下一步", "计划", "打算", "安排")):
        return "next_step_arrangement"
    if any(marker in heading for marker in ("成效", "成果", "成绩")):
        return "achievement_summary"
    if any(marker in heading for marker in ("部署", "要求")):
        return "work_deployment"
    return "work_progress"


def _safe_focus(value: str, fallback_role: str) -> str:
    for markers, label in _FOCUS_RULES:
        if any(marker in value for marker in markers):
            return label
    return {
        "background_introduction": "背景说明",
        "problem_analysis": "问题分析",
        "next_step_arrangement": "下一步安排",
        "achievement_summary": "成效归纳",
    }.get(fallback_role, "工作推进")


def _paragraph_function(text: str, section_role: str) -> str:
    if section_role == "next_step_arrangement":
        return "next_step_arrangement"
    if section_role == "problem_analysis":
        return "problem_analysis"
    if any(
        marker in text for marker in ("存在", "不足", "短板", "差距", "不够")
    ):
        return "problem_analysis"
    if any(
        marker in text for marker in ("下一步", "将继续", "计划", "打算")
    ):
        return "next_step_arrangement"
    if any(marker in text for marker in ("取得", "成效", "成果", "实现")):
        return "achievement_summary"
    if any(marker in text for marker in ("部署", "要求", "安排")):
        return "work_deployment"
    if any(marker in text for marker in ("制定", "建立", "开展", "组织", "健全", "严格", "加强")):
        return "measure_development"
    return "background_explanation"


def _dedupe(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _abstract_subsection_topic(heading: str, section_role: str) -> str:
    """Reduce a source heading to a short, fact-free writing topic."""
    value = re.split(r"[，。；：:、]", str(heading or ""), maxsplit=1)[0].strip()
    value = _SUBSECTION_LEADING_RE.sub("", value)
    value = _SUBSECTION_TAIL_RE.sub("", value)
    value = re.sub(r"(?:工作|制度|水平|能力|力度|要求)$", "", value).strip()
    value = re.sub(r"[^\u3400-\u9fffA-Za-z]", "", value)
    if (
        len(value) < 2
        or len(value) > 16
        or _SUBSECTION_FACT_VALUE_RE.search(value)
    ):
        return _safe_focus(heading, section_role)
    return value


def extract_reference_structure(
    task: Any,
    retrieval_payload: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Extract a controlled-vocabulary structure layer from primary chunks."""
    del task  # The genre here belongs to the reference, not to the new task.
    if not retrieval_payload:
        return empty_reference_structure()
    chunks = _primary_chunks(retrieval_payload)
    if not chunks:
        return empty_reference_structure()

    title = _source_title(retrieval_payload, chunks)
    document_type = _infer_document_type(title, chunks)
    reference_scenario = infer_reference_scenario(
        title,
        document_type,
        str(chunks[0].get("chunk_text") or "") if chunks else "",
    )
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    preface_functions: list[str] = []

    for chunk in chunks:
        text = str(chunk.get("chunk_text") or "")
        top_heading = ""
        secondary_headings: list[str] = []
        for candidate in _heading_candidates(chunk):
            top_match = _TOP_HEADING_RE.match(candidate)
            if top_match and not top_heading:
                top_heading = top_match.group("body").strip()
                continue
            secondary_match = _SECONDARY_HEADING_RE.match(candidate)
            if secondary_match:
                secondary_headings.append(
                    secondary_match.group("body").strip().rstrip("。；;")
                )

        if top_heading:
            role = _section_role(top_heading)
            current = {
                "section_role": role,
                "organization_focus": _safe_focus(top_heading, role),
                "development_logic": [],
                "paragraph_functions": [],
                "subsections": [],
            }
            sections.append(current)
        if current is None:
            preface_functions.append(_paragraph_function(text, "background_introduction"))
            continue

        if not secondary_headings:
            for candidate in _heading_candidates(chunk):
                match = _SECONDARY_HEADING_RE.match(candidate)
                if match:
                    secondary_headings.append(
                        match.group("body").strip().rstrip("。；;")
                    )
        secondary_headings = _dedupe(secondary_headings)
        current["development_logic"].extend(
            _safe_focus(heading, str(current["section_role"]))
            for heading in secondary_headings
        )
        for heading in secondary_headings:
            topic = _abstract_subsection_topic(
                heading,
                str(current["section_role"]),
            )
            if topic in {
                str(item.get("topic") or "")
                for item in current["subsections"]
            }:
                continue
            order = len(current["subsections"]) + 1
            function = _paragraph_function(
                heading,
                str(current["section_role"]),
            )
            current["subsections"].append(
                {
                    "subsection_id": (
                        f"SEC-{len(sections):02d}-SUB-{order:02d}"
                    ),
                    "topic": topic,
                    "function": function,
                    "order": order,
                    "paragraph_functions": _dedupe(
                        [
                            function,
                            _paragraph_function(
                                text,
                                str(current["section_role"]),
                            ),
                        ]
                    ),
                }
            )
        current["paragraph_functions"].append(
            _paragraph_function(text, str(current["section_role"]))
        )

    if not sections:
        return empty_reference_structure()

    for section in sections:
        development = _dedupe(section["development_logic"])
        if not development:
            development = [str(section["organization_focus"])]
        section["development_logic"] = development
        section["paragraph_functions"] = _dedupe(section["paragraph_functions"]) or [
            "background_explanation"
        ]

    paragraph_sequence = _dedupe([
        *preface_functions,
        *(
            function
            for section in sections
            for function in section["paragraph_functions"]
        ),
    ])
    if preface_functions and paragraph_sequence[0] != "background_explanation":
        paragraph_sequence.insert(0, "background_explanation")

    return {
        "schema_version": SCHEMA_VERSION,
        "status": EXTRACTED_STATUS,
        "source_role": SOURCE_ROLE,
        "document_type": document_type,
        "scenario_profile": {
            "scenario_id": str(reference_scenario.get("scenario_id") or "unknown"),
            "confidence": str(reference_scenario.get("confidence") or "FALLBACK"),
            "matched_by": str(reference_scenario.get("matched_by") or "reference_text"),
        },
        "reference_use": (
            "STRUCTURE_CANDIDATE"
            if document_type == "工作总结"
            else "WRITING_STYLE_ONLY_OR_INCOMPATIBLE"
        ),
        "primary_structure": sections,
        "overall_progression": [section["section_role"] for section in sections],
        "paragraph_function_sequence": paragraph_sequence,
        "fact_boundary": list(_FACT_BOUNDARY),
    }


def validate_reference_structure(reference: Mapping[str, Any]) -> dict[str, Any]:
    """Validate stable shape and prove that only controlled semantics escaped."""
    issues: list[dict[str, Any]] = []
    schema_version = reference.get("schema_version")
    allowed_reference_key_sets = (
        (_REFERENCE_KEYS, _LEGACY_REFERENCE_KEYS)
        if schema_version == SCHEMA_VERSION
        else (_LEGACY_REFERENCE_KEYS,)
    )
    if set(reference) not in allowed_reference_key_sets:
        expected_reference_keys = _REFERENCE_KEYS if schema_version == SCHEMA_VERSION else _LEGACY_REFERENCE_KEYS
        issues.append({"rule": "reference_fields", "detail": sorted(set(reference) ^ expected_reference_keys)})
    if schema_version not in {LEGACY_SCHEMA_VERSION, SCHEMA_VERSION}:
        issues.append({"rule": "schema_version"})
    if reference.get("status") not in {EMPTY_STATUS, EXTRACTED_STATUS}:
        issues.append({"rule": "status"})
    if reference.get("source_role") != SOURCE_ROLE:
        issues.append({"rule": "source_role"})
    if reference.get("document_type") not in _ALLOWED_DOCUMENT_TYPES:
        issues.append({"rule": "document_type"})
    if schema_version == SCHEMA_VERSION and (
        "scenario_profile" in reference or "reference_use" in reference
    ):
        scenario_profile = reference.get("scenario_profile")
        allowed_scenarios = {
            "unknown",
            "not_work_summary",
            "annual_summary",
            "half_year_summary",
            "stage_summary",
            "special_summary",
            "long_period_summary",
            "department_comprehensive_summary",
            "party_building_summary",
            "learning_education_summary",
            "township_comprehensive_summary",
        }
        if (
            not isinstance(scenario_profile, Mapping)
            or set(scenario_profile) != {"scenario_id", "confidence", "matched_by"}
            or scenario_profile.get("scenario_id") not in allowed_scenarios
            or scenario_profile.get("confidence") not in {"HIGH", "MEDIUM", "FALLBACK"}
        ):
            issues.append({"rule": "scenario_profile"})
        if reference.get("reference_use") not in {
            "STRUCTURE_CANDIDATE",
            "WRITING_STYLE_ONLY_OR_INCOMPATIBLE",
        }:
            issues.append({"rule": "reference_use"})
    if reference.get("fact_boundary") != _FACT_BOUNDARY:
        issues.append({"rule": "fact_boundary"})

    sections = reference.get("primary_structure")
    if not isinstance(sections, list):
        issues.append({"rule": "primary_structure_shape"})
        sections = []
    for section in sections:
        expected_section_keys = (
            _LEGACY_SECTION_KEYS
            if schema_version == LEGACY_SCHEMA_VERSION
            else _SECTION_KEYS
        )
        if (
            not isinstance(section, Mapping)
            or set(section) != expected_section_keys
        ):
            issues.append({"rule": "section_fields"})
            continue
        if section.get("section_role") not in _ALLOWED_SECTION_ROLES:
            issues.append({"rule": "section_role"})
        if section.get("organization_focus") not in _ALLOWED_FOCUSES:
            issues.append({"rule": "organization_focus"})
        if not isinstance(section.get("development_logic"), list) or any(
            value not in _ALLOWED_FOCUSES for value in section.get("development_logic") or []
        ):
            issues.append({"rule": "development_logic"})
        if not isinstance(section.get("paragraph_functions"), list) or any(
            value not in _ALLOWED_PARAGRAPH_FUNCTIONS
            for value in section.get("paragraph_functions") or []
        ):
            issues.append({"rule": "paragraph_functions"})
        if schema_version == SCHEMA_VERSION:
            subsections = section.get("subsections")
            if not isinstance(subsections, list):
                issues.append({"rule": "subsections_shape"})
                subsections = []
            for expected_order, subsection in enumerate(
                subsections,
                start=1,
            ):
                if (
                    not isinstance(subsection, Mapping)
                    or set(subsection) != _SUBSECTION_KEYS
                ):
                    issues.append({"rule": "subsection_fields"})
                    continue
                subsection_id = str(
                    subsection.get("subsection_id") or ""
                )
                if (
                    not _SUBSECTION_ID_RE.fullmatch(subsection_id)
                    or int(subsection.get("order") or 0) != expected_order
                ):
                    issues.append({"rule": "subsection_identity_order"})
                topic = str(subsection.get("topic") or "")
                if (
                    not topic
                    or len(topic) > 16
                    or _SUBSECTION_FACT_VALUE_RE.search(topic)
                ):
                    issues.append({"rule": "subsection_topic"})
                if (
                    subsection.get("function")
                    not in _ALLOWED_PARAGRAPH_FUNCTIONS
                ):
                    issues.append({"rule": "subsection_function"})
                subsection_functions = subsection.get(
                    "paragraph_functions"
                )
                if (
                    not isinstance(subsection_functions, list)
                    or any(
                        value not in _ALLOWED_PARAGRAPH_FUNCTIONS
                        for value in subsection_functions or []
                    )
                ):
                    issues.append(
                        {"rule": "subsection_paragraph_functions"}
                    )

    progression = reference.get("overall_progression")
    if not isinstance(progression, list) or any(value not in _ALLOWED_SECTION_ROLES for value in progression):
        issues.append({"rule": "overall_progression"})
    functions = reference.get("paragraph_function_sequence")
    if not isinstance(functions, list) or any(value not in _ALLOWED_PARAGRAPH_FUNCTIONS for value in functions):
        issues.append({"rule": "paragraph_function_sequence"})
    if reference.get("status") == EMPTY_STATUS and any((sections, progression, functions)):
        issues.append({"rule": "empty_reference_not_empty"})
    if reference.get("status") == EXTRACTED_STATUS and not sections:
        issues.append({"rule": "extracted_reference_empty"})

    return {
        "passed": not issues,
        "status": "PASS" if not issues else "BLOCKED_INVALID_REFERENCE_STRUCTURE",
        "hard_issue_count": len(issues),
        "hard_issues": issues,
        "fact_channel_present": False,
        "source_text_exposed": False,
    }
