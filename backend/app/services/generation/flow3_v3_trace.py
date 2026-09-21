"""Persistence and non-generative checks for Flow 3 v3."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import time
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable

from app.models.flow4 import (
    PARAGRAPH_FIELDS,
    paragraph_contract_issues,
    preserve_paragraph_identity,
    project_section_body_lines,
)
from app.services.generation.atomic_evidence import (
    ACTION_ALIAS_GROUPS,
    DEPARTMENT_ALIAS_GROUPS,
    EQUIPMENT_VALUE_RE,
    FREQUENCY_RE,
    MECHANISM_VALUE_RE,
    PLACE_VALUE_RE,
    PRECISE_NUMBER_RE,
    _source_fragments,
    classify_semantic_state,
    check_semantic_state_consistency,
    clean_source_residue,
    normalize_mechanism_value,
)
from app.services.generation.evidence_safety import (
    DraftSafetyStatus,
    FactSafetyRiskClass,
    build_targeted_safe_repair_input,
    run_evidence_safety,
    select_local_deletion_candidate,
    suggested_strength_downgrade_action,
    targeted_safe_repair_candidate,
)
from app.services.generation.work_summary_evidence_group import (
    EVIDENCE_GROUP_SCHEMA_VERSION,
    bind_work_summary_writer_units,
)
from app.utils.storage import atomic_write_text


FIXED_EXPRESSION_ALLOWLIST = (
    "坚持以习近平新时代中国特色社会主义思想为指导",
    "深入学习贯彻党的二十大和二十届三中全会精神",
)
WRITER_EXECUTION_REPORT_SCHEMA_VERSION = "writer-execution-report-1"
WRITER_EXECUTION_FIELDS = (
    "plan_step",
    "writing_focus_role",
    "expansion_stage",
    "content_function",
    "work_chain_role",
)
WRITER_NO_CHAIN_ROLE = "NO_CHAIN"
SENTENCE_RE = re.compile(r"[^。！？!?；;\n]+[。！？!?；;]?")
NUMBER_RE = re.compile(r"(?:\d+(?:\.\d+)?|[一二三四五六七八九十百千万亿两]+)(?:年|月|日|次|名|人|项|个|分钟|小时|天|场|套|台|条|起|件|%|％)")
ORGANIZATION_RE = re.compile(r"[\u3400-\u9fffA-Za-z]{2,16}(?:委员会|指挥部|工作专班|公安局|消防队|医院|部门|机构|岗位)")
ORGANIZATION_TERMS = ("公安", "消防", "文旅", "应急", "城管", "交通", "卫健", "政法", "交警", "公交", "客运")
EQUIPMENT_TERMS = (
    "X光机", "金属探测器", "无人机", "视频监控", "人流监测", "无线对讲",
    "急救设备", "广播系统", "闸机", "警务终端", "智能系统",
)
EQUIPMENT_RE = re.compile(r"[\u3400-\u9fffA-Za-z0-9]{2,16}(?:设备|系统|平台|终端|探测器|对讲机|X光机)")
PLACE_RE = re.compile(r"[\u3400-\u9fff]{2,12}(?:核心区|观众区|安检入口|疏散通道|舞台区域|活动区域|功能区|服务点|救助点|备勤点)")
RESULT_TERMS = ("实现", "完成", "发现", "处置", "未发生", "零事故", "零案件", "达到", "取得", "圆满")
ABSOLUTE_TERMS = ("全部", "所有", "全天候", "全覆盖", "全流程", "零事故", "零案件", "百分之百")
TIME_LIMIT_RE = re.compile(r"(?:\d+|[一二三四五六七八九十]+)(?:秒|分钟|小时|天)内")
HEADING_RE = re.compile(r"^\s*(?:[一二三四五六七八九十]+、|（[一二三四五六七八九十]+）|第[一二三四五六七八九十]+[章节])\s*(.{2,40})$")

_LOW_QUALITY_FUNCTION_WORDS = frozenset(
    "在未把被与和及并而对从由向为于将其该这各但或则且又仍已就所的地得"
)
_DISCOURSE_PREFIX_RE = re.compile(
    r"^(?:总体来看|总的来看|从总体上看|从实际情况看|实践表明|需要看到|应当看到|同时|此外|其中|目前|当前)"
)
_HARD_NUMBER_RE = re.compile(
    r"(?:\d+(?:\.\d+)?|[一二三四五六七八九十百千万亿两]+)"
    r"(?:个百分点|万元|亿元|公里|平方米|人次|年|个月|月|日|次|名|人|项|个|处|户|家|分钟|秒|小时|天|场|套|台|条|起|件|%|％)"
)
_NAMED_ORGANIZATION_RE = re.compile(
    r"(?:工作专班|领导小组|[\u3400-\u9fffA-Za-z]{2,24}(?:党委|党组|政府|委员会|办公室|管理局|公安局|消防队|公司|集团|中心|医院|学校|研究院|协会|指挥部))"
)
_PERSON_OR_ROLE_RE = re.compile(
    r"[\u3400-\u9fff]{0,12}(?:书记|局长|主任|处长|科长|经理|董事长|领导|负责人)"
)
_POLICY_PROJECT_RE = re.compile(
    r"[\u3400-\u9fffA-Za-z0-9]{2,28}(?:专项行动|行动方案|实施方案|项目|工程|计划|规划|政策|条例|办法)"
)
_NAMED_PLACE_RE = re.compile(
    r"(?<![全各本我该辖])[\u3400-\u9fff]{2,12}(?:省|市|县|区|州|镇|乡|街道|社区|园区|场馆|广场)"
)
_COMPLETION_OR_OUTCOME_RE = re.compile(
    r"(?:全面|全部|圆满|顺利|有序|显著|明显|有效)?(?:完成|实现|建成|形成|达到|提升|提高|增强|改善|下降|增长|推动|落实)"
)
_NEGATIVE_POLARITY_MARKERS = (
    "未", "没有", "尚未", "不够", "不足", "不完善", "不成熟", "较低", "差距",
    "困难", "问题", "迟缓", "不畅", "不到位", "未能", "难以", "薄弱",
)
_POSITIVE_POLARITY_MARKERS = (
    "已经", "已", "得到", "实现", "完成", "建成", "有效", "显著", "明显提升",
    "有序", "顺利", "形成", "达到", "提高", "增强", "改善",
)


class UnsupportedFactError(RuntimeError):
    def __init__(self, report: dict[str, Any], trace_dir: str):
        super().__init__("flow3_v3_status=FAIL_UNSUPPORTED_FACT")
        self.report = report
        self.trace_dir = trace_dir
        self.flow3_v3_status = "FAIL_UNSUPPORTED_FACT"


class OutOfPlanFactError(RuntimeError):
    def __init__(self, report: dict[str, Any], trace_dir: str):
        super().__init__("flow3_v32_status=FAIL_OUT_OF_PLAN_FACT")
        self.report = report
        self.trace_dir = trace_dir
        self.flow3_v3_status = "FAIL_OUT_OF_PLAN_FACT"


class SourceParaphraseError(RuntimeError):
    def __init__(self, report: dict[str, Any], trace_dir: str):
        super().__init__("flow3_v3_status=FAIL_SOURCE_PARAPHRASE")
        self.report = report
        self.trace_dir = trace_dir
        self.flow3_v3_status = "FAIL_SOURCE_PARAPHRASE"


class SourceParaphraseHighRiskError(RuntimeError):
    def __init__(self, report: dict[str, Any], trace_dir: str):
        super().__init__("flow3_v3_status=FAIL_SOURCE_PARAPHRASE;review_required=true")
        self.report = report
        self.trace_dir = trace_dir
        self.flow3_v3_status = "FAIL_SOURCE_PARAPHRASE"


class StructuredOutputError(RuntimeError):
    def __init__(self, report: dict[str, Any], trace_dir: str):
        super().__init__("flow3_v33_status=FAIL_STRUCTURED_OUTPUT")
        self.report = report
        self.trace_dir = trace_dir
        self.flow3_v3_status = "FAIL_STRUCTURED_OUTPUT"


class AnalysisFactLeakError(RuntimeError):
    def __init__(self, report: dict[str, Any], trace_dir: str):
        super().__init__("flow3_v33_status=FAIL_ANALYSIS_FACT_LEAK")
        self.report = report
        self.trace_dir = trace_dir
        self.flow3_v3_status = "FAIL_ANALYSIS_FACT_LEAK"


def _write_json(path: Path, value: Any) -> str:
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path.resolve())


def initialize_trace(
    artifact_root: Path,
    *,
    retrieval_result: dict[str, Any],
    evidence_pack: dict[str, Any],
    quality_report: dict[str, Any],
    content_plan: dict[str, Any],
    content_plan_quality_report: dict[str, Any],
    evidence_selection_report: dict[str, Any],
    selected_evidence_local: list[dict[str, Any]],
    selected_evidence_outbound: list[dict[str, Any]],
    prompt: str,
) -> dict[str, str]:
    root = Path(artifact_root)
    root.mkdir(parents=True, exist_ok=True)
    return {
        "trace_dir": str(root.resolve()),
        "retrieval_result": _write_json(root / "retrieval_result.json", retrieval_result),
        "atomic_evidence_pack": _write_json(root / "atomic_evidence_pack.json", evidence_pack),
        "evidence_quality_report": _write_json(root / "evidence_quality_report.json", quality_report),
        "content_plan": _write_json(root / "content_plan.json", content_plan),
        "content_plan_quality_report": _write_json(root / "content_plan_quality_report.json", content_plan_quality_report),
        "evidence_selection_report": _write_json(root / "evidence_selection_report.json", evidence_selection_report),
        "selected_evidence_local": _write_json(root / "selected_evidence_local.json", selected_evidence_local),
        "selected_evidence_outbound": _write_json(root / "selected_evidence_outbound.json", selected_evidence_outbound),
        "prompt": _write_text(root / "prompt.txt", prompt),
    }


def _write_text(path: Path, value: str) -> str:
    atomic_write_text(path, value, encoding="utf-8")
    return str(path.resolve())


def save_raw_response(artifact_root: Path, raw_response: str) -> str:
    """Persist the untouched provider response before any checker executes."""
    root = Path(artifact_root)
    path = _write_text(root / "raw_structured_response.txt", raw_response)
    _write_text(root / "raw_response.txt", raw_response)  # V3/V3.2 maintenance artifact alias.
    return path


def save_check_reports(
    artifact_root: Path,
    *,
    unsupported_report: dict[str, Any],
    paraphrase_report: dict[str, Any],
) -> dict[str, str]:
    root = Path(artifact_root)
    return {
        "unsupported_fact_report": _write_json(root / "unsupported_fact_report.json", unsupported_report),
        "source_paraphrase_risk": _write_json(root / "source_paraphrase_risk.json", paraphrase_report),
    }


def save_v33_validation_reports(
    artifact_root: Path, *, structured_report: dict[str, Any],
    fact_report: dict[str, Any], analysis_report: dict[str, Any],
    paraphrase_report: dict[str, Any] | None = None,
) -> dict[str, str]:
    root = Path(artifact_root)
    paths = {
        "structured_validation_report": _write_json(root / "structured_validation_report.json", structured_report),
        "fact_validation_report": _write_json(root / "fact_validation_report.json", fact_report),
        "analysis_validation_report": _write_json(root / "analysis_validation_report.json", analysis_report),
    }
    if paraphrase_report is not None:
        paths["source_paraphrase_risk"] = _write_json(root / "source_paraphrase_risk.json", paraphrase_report)
    return paths


def save_runtime_summary(artifact_root: Path, *, manifest: dict[str, Any], summary: dict[str, Any]) -> dict[str, str]:
    root = Path(artifact_root)
    return {
        "runtime_manifest": _write_json(root / "runtime_manifest.json", manifest),
        "run_summary": _write_json(root / "run_summary.json", summary),
    }


def parse_structured_draft(raw_response: str) -> dict[str, Any]:
    """Parse exactly one JSON object; only BOM and one whole-object fence are tolerated."""
    value = str(raw_response or "")
    if value.startswith("\ufeff"):
        value = value[1:]
    stripped = value.strip()
    fence = re.fullmatch(r"```(?:json)?\s*\n([\s\S]*?)\n```", stripped, flags=re.IGNORECASE)
    if fence:
        stripped = fence.group(1).strip()
    parsed = json.loads(stripped)
    if not isinstance(parsed, dict):
        raise ValueError("structured response root must be one JSON object")
    return parsed


def _paragraph_text(paragraph: dict[str, Any]) -> str:
    parts: list[str] = []
    for field in ("fact_text", "expansion_text", "transition_text", "summary_text"):
        value = paragraph.get(field)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    return "".join(parts)


def _paragraph_analysis_text(paragraph: dict[str, Any]) -> str:
    parts: list[str] = []
    for field in ("expansion_text", "transition_text", "summary_text"):
        value = paragraph.get(field)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    return "".join(parts)


def _overlay_subsections_by_section(
    writing_structure_overlay: dict[str, Any] | None,
) -> dict[str, list[str]]:
    return {
        str(section.get("section_id") or ""): [
            str(subsection.get("subsection_id") or "")
            for subsection in section.get("subsections") or []
            if isinstance(subsection, dict)
        ]
        for section in (
            (writing_structure_overlay or {}).get("sections") or []
        )
        if isinstance(section, dict)
    }


def validate_subsection_structure(
    draft: dict[str, Any],
    writing_structure_overlay: dict[str, Any] | None,
) -> dict[str, Any]:
    """Compare model subsection identities with the fact-free overlay."""
    expected = _overlay_subsections_by_section(
        writing_structure_overlay
    )
    if not expected:
        return {
            "overlay_applied": False,
            "section_count": 0,
            "subsection_count_by_section": {},
            "expected_subsection_ids": {},
            "generated_subsection_ids": {},
            "missing_subsection_ids": [],
            "unknown_subsection_ids": [],
            "reordered_subsection_ids": [],
            "duplicated_subsection_ids": [],
            "structure_status": "NOT_APPLIED",
        }
    generated: dict[str, list[str]] = {}
    unknown: list[str] = []
    reordered: list[str] = []
    duplicated: list[str] = []
    for section in draft.get("sections") or []:
        if not isinstance(section, dict):
            continue
        section_id = str(section.get("section_id") or "")
        raw_subsections = section.get("subsections")
        if not isinstance(raw_subsections, list):
            generated[section_id] = []
            continue
        ids = [
            str(item.get("subsection_id") or "")
            for item in raw_subsections
            if isinstance(item, dict)
        ]
        generated[section_id] = ids
        seen: set[str] = set()
        for subsection_id in ids:
            if subsection_id in seen:
                duplicated.append(subsection_id)
            seen.add(subsection_id)
            if subsection_id not in expected.get(section_id, []):
                unknown.append(subsection_id)
        known_in_order = [
            value for value in ids
            if value in expected.get(section_id, [])
        ]
        expected_present = [
            value for value in expected.get(section_id, [])
            if value in known_in_order
        ]
        if known_in_order != expected_present:
            reordered.extend(known_in_order)
    missing = [
        subsection_id
        for section_id, expected_ids in expected.items()
        for subsection_id in expected_ids
        if subsection_id not in generated.get(section_id, [])
    ]
    invalid = bool(unknown or reordered or duplicated)
    status = (
        "INVALID"
        if invalid
        else (
            "SUBSECTION_STRUCTURE_INCOMPLETE"
            if missing
            else "PASS"
        )
    )
    return {
        "overlay_applied": True,
        "section_count": len(expected),
        "subsection_count_by_section": {
            section_id: len(ids)
            for section_id, ids in expected.items()
        },
        "expected_subsection_ids": expected,
        "generated_subsection_ids": generated,
        "missing_subsection_ids": missing,
        "unknown_subsection_ids": unknown,
        "reordered_subsection_ids": reordered,
        "duplicated_subsection_ids": duplicated,
        "structure_status": status,
    }


def _normalize_paragraphs(
    paragraphs: Any,
    *,
    section_id: str,
    issues: list[dict[str, Any]],
    subsection_id: str = "",
) -> list[dict[str, Any]]:
    if not isinstance(paragraphs, list):
        issues.append(
            {
                "rule": "paragraphs_array",
                "section_id": section_id,
                **(
                    {"subsection_id": subsection_id}
                    if subsection_id
                    else {}
                ),
            }
        )
        return []
    sentences: list[dict[str, Any]] = []
    for paragraph_index, paragraph in enumerate(paragraphs):
        location = {
            "section_id": section_id,
            "paragraph_index": paragraph_index,
        }
        if subsection_id:
            location["subsection_id"] = subsection_id
        if not isinstance(paragraph, dict):
            issues.append({"rule": "paragraph_object", **location})
            continue
        expected = {
            "evidence_id",
            "fact_text",
            "expansion_text",
            "transition_text",
            "summary_text",
        }
        expected.update(field for field in PARAGRAPH_FIELDS if field in paragraph)
        if set(paragraph) != expected:
            issues.append(
                {
                    "rule": "paragraph_fields",
                    **location,
                    "actual": sorted(paragraph),
                }
            )
        evidence_id = paragraph.get("evidence_id")
        fact_text = paragraph.get("fact_text")
        text = _paragraph_text(paragraph)
        evidence_id_valid = isinstance(evidence_id, str)
        fact_text_valid = isinstance(fact_text, str)
        has_evidence_id = (
            evidence_id_valid and bool(evidence_id.strip())
        )
        has_fact_text = fact_text_valid and bool(fact_text.strip())
        if not evidence_id_valid:
            issues.append(
                {"rule": "paragraph_evidence_id_type", **location}
            )
        if not fact_text_valid:
            issues.append(
                {"rule": "paragraph_fact_text_type", **location}
            )
        if has_evidence_id != has_fact_text:
            issues.append(
                {"rule": "paragraph_fact_binding_incomplete", **location}
            )
        if not text:
            issues.append(
                {"rule": "paragraph_text_blank", **location}
            )
        sentence_metadata = (
            {"subsection_id": subsection_id}
            if subsection_id
            else {}
        )
        sentence_metadata.update({field: paragraph[field] for field in PARAGRAPH_FIELDS if field in paragraph})
        if has_evidence_id and has_fact_text:
            sentences.append(
                {
                    "sentence_type": "FACT",
                    "evidence_id": evidence_id.strip(),
                    "text": fact_text.strip(),
                    **sentence_metadata,
                }
            )
        analysis_text = _paragraph_analysis_text(paragraph)
        if analysis_text:
            sentences.append(
                {
                    "sentence_type": "ANALYSIS",
                    "evidence_id": "",
                    "text": analysis_text,
                    **sentence_metadata,
                }
            )
    return sentences


def normalize_paragraph_structured_draft(
    draft: dict[str, Any],
    writing_structure_overlay: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Pass sentence atoms through and flatten legacy paragraph projections.

    The work-summary Writer emits sentence atoms directly.  Older callers may
    still supply paragraph-shaped output; only that compatibility input is
    converted into one FACT atom plus an ANALYSIS atom when analysis text was
    actually present.  No ANALYSIS is manufactured for a FACT-only input.
    """
    if not isinstance(draft, dict):
        return draft, [{"rule": "root_object"}]
    sections = draft.get("sections")
    if not isinstance(sections, list):
        return draft, []

    normalized_sections: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    changed = False
    expected_subsections = _overlay_subsections_by_section(
        writing_structure_overlay
    )
    for section in sections:
        if not isinstance(section, dict):
            normalized_sections.append(section)
            continue
        if "sentences" in section and "paragraphs" not in section:
            normalized_sections.append(section)
            continue
        if "paragraphs" not in section and "subsections" not in section:
            normalized_sections.append(section)
            continue

        changed = True
        section_id = str(section.get("section_id") or "")
        if "subsections" in section:
            allowed = {"section_id", "heading", "subsections"}
            if set(section) != allowed:
                issues.append(
                    {
                        "rule": "subsection_section_fields",
                        "section_id": section_id,
                        "actual": sorted(section),
                    }
                )
            raw_subsections = section.get("subsections")
            if not isinstance(raw_subsections, list):
                issues.append(
                    {
                        "rule": "subsections_array",
                        "section_id": section_id,
                    }
                )
                raw_subsections = []
            sentences: list[dict[str, Any]] = []
            subsection_metadata: list[dict[str, Any]] = []
            accepted_ids: set[str] = set()
            allowed_ids = expected_subsections.get(section_id, [])
            for subsection in raw_subsections:
                if not isinstance(subsection, dict):
                    issues.append(
                        {
                            "rule": "subsection_object",
                            "section_id": section_id,
                        }
                    )
                    continue
                subsection_id = str(
                    subsection.get("subsection_id") or ""
                )
                if (
                    subsection_id not in allowed_ids
                    or subsection_id in accepted_ids
                ):
                    continue
                accepted_ids.add(subsection_id)
                if set(subsection) != {
                    "subsection_id",
                    "lead",
                    "paragraphs",
                }:
                    issues.append(
                        {
                            "rule": "subsection_fields",
                            "section_id": section_id,
                            "subsection_id": subsection_id,
                            "actual": sorted(subsection),
                        }
                    )
                lead = subsection.get("lead")
                if not isinstance(lead, str):
                    issues.append(
                        {
                            "rule": "subsection_lead",
                            "section_id": section_id,
                            "subsection_id": subsection_id,
                        }
                    )
                    lead = ""
                subsection_metadata.append(
                    {
                        "subsection_id": subsection_id,
                        "lead": str(lead).strip(),
                        "order": (
                            allowed_ids.index(subsection_id) + 1
                        ),
                    }
                )
                sentences.extend(
                    _normalize_paragraphs(
                        subsection.get("paragraphs"),
                        section_id=section_id,
                        issues=issues,
                        subsection_id=subsection_id,
                    )
                )
            normalized_sections.append(
                {
                    "section_id": section.get("section_id"),
                    "heading": section.get("heading"),
                    "subsections": subsection_metadata,
                    "sentences": sentences,
                }
            )
            continue

        allowed = {"section_id", "heading", "paragraphs"}
        if set(section) != allowed:
            issues.append({
                "rule": "paragraph_section_fields",
                "section_id": section_id,
                "actual": sorted(section),
            })
        sentences = _normalize_paragraphs(
            section.get("paragraphs"),
            section_id=section_id,
            issues=issues,
        )

        normalized_sections.append({
            "section_id": section.get("section_id"),
            "heading": section.get("heading"),
            "sentences": sentences,
        })

    if not changed:
        return draft, issues
    normalized = {
        "title": draft.get("title"),
        "sections": normalized_sections,
    }
    if "introduction" in draft:
        normalized["introduction"] = draft.get("introduction")
    return normalized, issues


def _declared_evidence_ids(sentence: dict[str, Any]) -> list[str]:
    values = sentence.get("evidence_ids")
    if isinstance(values, list):
        return list(dict.fromkeys(
            str(value).strip() for value in values if str(value).strip()
        ))
    value = str(sentence.get("evidence_id") or "").strip()
    return [value] if value else []


def _normalized_heading_value(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(
        r"^(?:[一二三四五六七八九十百]+、|（[一二三四五六七八九十百]+）|"
        r"\([一二三四五六七八九十百]+\)|\d+[.、])\s*",
        "",
        text,
    )
    return re.sub(r"[\s，。；：、,.!?！？:;（）()《》“”‘’\-—_]", "", text)


def _writer_execution_metadata(
    draft: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    """Capture model-declared plan metadata before deterministic binding."""
    captured: dict[str, list[dict[str, Any]]] = {}
    for section in draft.get("sections") or []:
        if not isinstance(section, dict):
            continue
        section_id = str(section.get("section_id") or "")
        captured[section_id] = [
            (
                {
                    field: copy.deepcopy(sentence.get(field))
                    for field in WRITER_EXECUTION_FIELDS
                    if field in sentence
                }
                if isinstance(sentence, dict) else {}
            )
            for sentence in section.get("sentences") or []
        ]
    return captured


def _restore_writer_execution_metadata(
    draft: dict[str, Any],
    captured: dict[str, list[dict[str, Any]]],
) -> None:
    """Reattach plan metadata removed by the unchanged Evidence Binder."""
    for section in draft.get("sections") or []:
        if not isinstance(section, dict):
            continue
        metadata = captured.get(str(section.get("section_id") or "")) or []
        for index, sentence in enumerate(section.get("sentences") or []):
            if isinstance(sentence, dict) and index < len(metadata):
                sentence.update(copy.deepcopy(metadata[index]))


def _execution_unit_chain_map(
    control_section: dict[str, Any],
) -> tuple[dict[str, str], dict[str, tuple[int, int]]]:
    unit_to_chain: dict[str, str] = {}
    ordered_positions: dict[str, tuple[int, int]] = {}
    for chain_index, chain in enumerate(
        control_section.get("work_chain_organization") or []
    ):
        if not isinstance(chain, dict):
            continue
        chain_id = str(chain.get("chain_id") or "")
        for unit in chain.get("member_writer_units") or []:
            unit_id = str((unit or {}).get("fact_unit_id") or "")
            if unit_id and chain_id:
                unit_to_chain[unit_id] = chain_id
        for unit_index, unit in enumerate(chain.get("ordered_writer_units") or []):
            unit_id = str((unit or {}).get("fact_unit_id") or "")
            if unit_id:
                ordered_positions[unit_id] = (chain_index, unit_index)
    return unit_to_chain, ordered_positions


def _execution_evidence_chain_map(
    plan_section: dict[str, Any],
) -> tuple[dict[str, str], dict[str, tuple[int, int]]]:
    evidence_to_chain: dict[str, str] = {}
    ordered_positions: dict[str, tuple[int, int]] = {}
    for chain_index, chain in enumerate(plan_section.get("work_chain_summaries") or []):
        if not isinstance(chain, dict):
            continue
        chain_id = str(chain.get("chain_id") or "")
        for evidence_id in chain.get("member_evidence_ids") or []:
            if str(evidence_id or "") and chain_id:
                evidence_to_chain[str(evidence_id)] = chain_id
        for evidence_index, evidence_id in enumerate(
            chain.get("ordered_evidence_ids") or []
        ):
            if str(evidence_id or ""):
                ordered_positions[str(evidence_id)] = (
                    chain_index,
                    evidence_index,
                )
    return evidence_to_chain, ordered_positions


def validate_writer_execution_contract(
    draft: dict[str, Any],
    content_plan: dict[str, Any],
    writer_plan_control: dict[str, Any] | None,
) -> dict[str, Any]:
    """Validate model-declared execution against FrozenArticlePlan allowlists."""
    if not isinstance(writer_plan_control, dict) or not writer_plan_control.get("sections"):
        return {
            "schema_version": WRITER_EXECUTION_REPORT_SCHEMA_VERSION,
            "status": "NOT_APPLICABLE",
            "passed": True,
            "executed_sections": [],
            "executed_plan_steps": [],
            "executed_writing_focus": [],
            "executed_expansion_path": [],
            "unexecuted_items": [],
            "risk_items": [],
            "section_reports": [],
            "hard_issues": [],
            "hard_issue_count": 0,
        }

    issues: list[dict[str, Any]] = []
    risks: list[dict[str, Any]] = []
    unexecuted: list[dict[str, Any]] = []
    section_reports: list[dict[str, Any]] = []
    executed_sections: list[str] = []
    executed_plan_steps: list[dict[str, str]] = []
    executed_focus: list[dict[str, str]] = []
    executed_path: list[dict[str, str]] = []
    draft_by_id = {
        str(section.get("section_id") or ""): section
        for section in draft.get("sections") or []
        if isinstance(section, dict)
    }
    plan_by_id = {
        str(section.get("section_id") or ""): section
        for section in content_plan.get("sections") or []
        if isinstance(section, dict)
    }

    for control_section in writer_plan_control.get("sections") or []:
        if not isinstance(control_section, dict):
            continue
        section_id = str(control_section.get("section_id") or "")
        contract = control_section.get("execution_contract") or {}
        draft_section = draft_by_id.get(section_id) or {}
        plan_section = plan_by_id.get(section_id) or {}
        section_issues: list[dict[str, Any]] = []
        heading = str(draft_section.get("heading") or "")
        forbidden_headings = list(
            (contract.get("heading_contract") or {}).get("forbidden_exact_copy") or []
        )
        normalized_heading = _normalized_heading_value(heading)
        if normalized_heading and any(
            normalized_heading == _normalized_heading_value(value)
            for value in forbidden_headings
            if _normalized_heading_value(value)
        ):
            issue = {
                "rule": "writer_execution_heading_copies_business_block",
                "section_id": section_id,
                "heading": heading,
            }
            section_issues.append(issue)
            risks.append(issue)

        step_rows = [row for row in contract.get("plan_steps") or [] if isinstance(row, dict)]
        step_to_instruction = {
            str(row.get("plan_step") or ""): str(row.get("instruction") or "")
            for row in step_rows
        }
        step_order = {
            str(row.get("plan_step") or ""): int(row.get("order") or index)
            for index, row in enumerate(step_rows, 1)
        }
        expansion_rows = [
            row for row in contract.get("expansion_stages") or []
            if isinstance(row, dict)
        ]
        expansion_order = {
            str(row.get("expansion_stage") or ""): int(row.get("order") or index)
            for index, row in enumerate(expansion_rows, 1)
        }
        focus_rows = [
            row for row in contract.get("writing_focus_roles") or []
            if isinstance(row, dict)
        ]
        focus_by_id = {
            str(row.get("writing_focus_role") or ""): str(row.get("focus") or "")
            for row in focus_rows
        }
        allowed_functions = {
            str(value) for value in contract.get("allowed_content_functions") or []
            if str(value)
        }
        allowed_chain_roles = {
            str(value) for value in contract.get("allowed_work_chain_roles") or []
            if str(value)
        }
        unit_to_chain, unit_order = _execution_unit_chain_map(control_section)
        evidence_to_chain, evidence_order = _execution_evidence_chain_map(plan_section)
        used_steps: list[str] = []
        used_expansion_stages: list[str] = []
        used_focus: set[str] = set()
        used_functions: set[str] = set()
        observed_chain_positions: list[tuple[int, int]] = []
        consecutive_fact_count = 0
        max_fact_run = 0

        sentences = draft_section.get("sentences") or []
        if not isinstance(sentences, list) or not sentences:
            section_issues.append({
                "rule": "writer_execution_section_empty",
                "section_id": section_id,
            })
            sentences = []
        for index, sentence in enumerate(sentences):
            if not isinstance(sentence, dict):
                continue
            location = {"section_id": section_id, "index": index}
            missing_fields = [
                field for field in WRITER_EXECUTION_FIELDS
                if not isinstance(sentence.get(field), str)
                or not str(sentence.get(field) or "").strip()
            ]
            if missing_fields:
                section_issues.append({
                    "rule": "writer_execution_fields_missing",
                    **location,
                    "fields": missing_fields,
                })
            plan_step = str(sentence.get("plan_step") or "")
            expansion_stage = str(sentence.get("expansion_stage") or "")
            focus_role = str(sentence.get("writing_focus_role") or "")
            content_function = str(sentence.get("content_function") or "")
            work_chain_role = str(sentence.get("work_chain_role") or "")
            if plan_step not in step_to_instruction:
                section_issues.append({
                    "rule": "writer_execution_plan_step_unknown",
                    **location,
                    "plan_step": plan_step,
                })
            else:
                used_steps.append(plan_step)
            if expansion_stage not in expansion_order:
                section_issues.append({
                    "rule": "writer_execution_expansion_stage_unknown",
                    **location,
                    "expansion_stage": expansion_stage,
                })
            else:
                used_expansion_stages.append(expansion_stage)
            if focus_role not in focus_by_id:
                section_issues.append({
                    "rule": "writer_execution_writing_focus_unknown",
                    **location,
                    "writing_focus_role": focus_role,
                })
            else:
                used_focus.add(focus_role)
            if content_function not in allowed_functions:
                section_issues.append({
                    "rule": "writer_execution_content_function_unknown",
                    **location,
                    "content_function": content_function,
                })
            else:
                used_functions.add(content_function)
            if work_chain_role not in allowed_chain_roles:
                section_issues.append({
                    "rule": "writer_execution_work_chain_role_unknown",
                    **location,
                    "work_chain_role": work_chain_role,
                })

            sentence_type = str(sentence.get("sentence_type") or "")
            if sentence_type == "FACT":
                consecutive_fact_count += 1
                max_fact_run = max(max_fact_run, consecutive_fact_count)
            else:
                consecutive_fact_count = 0
            declared_unit_ids = [
                str(value) for value in sentence.get("fact_unit_ids") or []
                if str(value)
            ]
            declared_evidence_ids = _declared_evidence_ids(sentence)
            expected_roles = {
                unit_to_chain[value] for value in declared_unit_ids
                if value in unit_to_chain
            } or {
                evidence_to_chain[value] for value in declared_evidence_ids
                if value in evidence_to_chain
            }
            expected_role = (
                next(iter(expected_roles))
                if len(expected_roles) == 1 else WRITER_NO_CHAIN_ROLE
            )
            if len(expected_roles) > 1:
                section_issues.append({
                    "rule": "writer_execution_work_chain_roles_mixed",
                    **location,
                    "roles": sorted(expected_roles),
                })
            if work_chain_role and work_chain_role != expected_role:
                section_issues.append({
                    "rule": "writer_execution_work_chain_role_mismatch",
                    **location,
                    "expected": expected_role,
                    "actual": work_chain_role,
                })
            for value in declared_unit_ids:
                if value in unit_order:
                    observed_chain_positions.append(unit_order[value])
            for value in declared_evidence_ids:
                if value in evidence_order:
                    observed_chain_positions.append(evidence_order[value])

        distinct_steps = list(dict.fromkeys(used_steps))
        distinct_expansion_stages = list(dict.fromkeys(used_expansion_stages))
        observed_step_orders = [
            step_order[value] for value in used_steps if value in step_order
        ]
        if observed_step_orders != sorted(
            observed_step_orders
        ):
            section_issues.append({
                "rule": "writer_execution_plan_step_order",
                "section_id": section_id,
                "actual": distinct_steps,
            })
        observed_expansion_orders = [
            expansion_order[value]
            for value in used_expansion_stages
            if value in expansion_order
        ]
        if observed_expansion_orders != sorted(observed_expansion_orders):
            section_issues.append({
                "rule": "writer_execution_expansion_path_order",
                "section_id": section_id,
                "actual": distinct_expansion_stages,
            })
        if observed_chain_positions != sorted(observed_chain_positions):
            section_issues.append({
                "rule": "writer_execution_work_chain_order",
                "section_id": section_id,
                "actual_positions": observed_chain_positions,
            })
        max_allowed = int(contract.get("max_consecutive_fact_units") or 2)
        if max_fact_run > max_allowed:
            issue = {
                "rule": "writer_execution_fact_stacking",
                "section_id": section_id,
                "maximum_allowed": max_allowed,
                "actual": max_fact_run,
            }
            section_issues.append(issue)
            risks.append(issue)

        coverage_checks = (
            ("plan_step", set(step_to_instruction), set(used_steps)),
            (
                "expansion_stage",
                set(expansion_order),
                set(used_expansion_stages),
            ),
            ("writing_focus_role", set(focus_by_id), used_focus),
            ("content_function", allowed_functions, used_functions),
        )
        for item_type, expected, actual in coverage_checks:
            for missing in sorted(expected - actual):
                item = {
                    "rule": "writer_execution_required_item_missing",
                    "section_id": section_id,
                    "item_type": item_type,
                    "item": missing,
                }
                section_issues.append(item)
                unexecuted.append(item)

        if not section_issues:
            executed_sections.append(section_id)
        for focus_role in sorted(used_focus & set(focus_by_id)):
            executed_focus.append({
                "section_id": section_id,
                "writing_focus_role": focus_role,
                "focus": focus_by_id[focus_role],
            })
        for plan_step in distinct_steps:
            if plan_step in step_to_instruction:
                executed_plan_steps.append({
                    "section_id": section_id,
                    "plan_step": plan_step,
                    "instruction": step_to_instruction[plan_step],
                })
        for expansion_stage in distinct_expansion_stages:
            if expansion_stage in expansion_order:
                executed_path.append({
                    "section_id": section_id,
                    "expansion_stage": expansion_stage,
                })
        issues.extend(section_issues)
        section_reports.append({
            "section_id": section_id,
            "section_goal": str(control_section.get("section_goal") or ""),
            "goal_covered": not section_issues,
            "heading": heading,
            "executed_plan_steps": distinct_steps,
            "executed_expansion_stages": distinct_expansion_stages,
            "executed_writing_focus_roles": sorted(used_focus),
            "executed_content_functions": sorted(used_functions),
            "maximum_consecutive_fact_units": max_fact_run,
            "issue_count": len(section_issues),
            "issues": section_issues,
        })

    return {
        "schema_version": WRITER_EXECUTION_REPORT_SCHEMA_VERSION,
        "status": "PASS" if not issues else "FAIL_WRITER_EXECUTION",
        "passed": not issues,
        "executed_sections": executed_sections,
        "executed_plan_steps": executed_plan_steps,
        "executed_writing_focus": executed_focus,
        "executed_expansion_path": executed_path,
        "unexecuted_items": unexecuted,
        "risk_items": risks,
        "section_reports": section_reports,
        "hard_issues": issues,
        "hard_issue_count": len(issues),
    }


@dataclass(frozen=True)
class ParagraphValidationContext:
    """Caller-owned identity and EvidenceOwnership, never inferred from a patch."""

    section_id: str
    paragraph_id: str
    paragraph_order: int
    allowed_evidence_ids: tuple[str, ...]


def _paragraph_validation_scope(scope, paragraph_context, content_plan) -> bool:
    if scope not in {"FULL_ARTICLE", "PARAGRAPH_LOCAL"}:
        raise ValueError("FACT_SAFETY_VALIDATION_SCOPE_INVALID")
    if scope == "FULL_ARTICLE":
        if paragraph_context is not None:
            raise ValueError("FULL_ARTICLE_PARAGRAPH_CONTEXT_NOT_ALLOWED")
        return False
    if (not isinstance(paragraph_context, ParagraphValidationContext)
            or not isinstance(content_plan, dict)
            or not isinstance(paragraph_context.section_id, str)
            or not paragraph_context.section_id.strip()
            or not isinstance(paragraph_context.paragraph_id, str)
            or not paragraph_context.paragraph_id.strip()
            or type(paragraph_context.paragraph_order) is not int
            or paragraph_context.paragraph_order < 0
            or not isinstance(paragraph_context.allowed_evidence_ids, tuple)
            or any(not isinstance(eid, str) or not eid.strip()
                   for eid in paragraph_context.allowed_evidence_ids)
            or len(set(paragraph_context.allowed_evidence_ids))
            != len(paragraph_context.allowed_evidence_ids)):
        raise ValueError("PARAGRAPH_LOCAL_CONTEXT_REQUIRED")
    return True


def validate_structured_draft(
    draft: dict[str, Any],
    content_plan: dict[str, Any],
    fact_anchor_pack: dict[str, Any],
    introduction_contract: dict[str, Any] | None = None,
    writer_execution_contract: dict[str, Any] | None = None,
    *,
    validation_scope: str = "FULL_ARTICLE",
    paragraph_context: ParagraphValidationContext | None = None,
) -> dict[str, Any]:
    local_scope = _paragraph_validation_scope(validation_scope, paragraph_context, content_plan)
    issues: list[dict[str, Any]] = []
    multi_evidence_contract = str(
        content_plan.get("planner_owner") or ""
    ).startswith("work_summary_planner")
    planning_mode = str(content_plan.get("planning_mode") or "normal")
    fact_required_minimum = (
        1 if planning_mode == "evidence_constrained" or multi_evidence_contract else 2
    )
    expected_root_fields = {"title", "sections"}
    if local_scope and "title" not in draft:
        expected_root_fields.remove("title")
    if (introduction_contract or {}).get("required"):
        expected_root_fields.add("introduction")
    actual_root_fields = set(draft)
    missing_non_introduction_fields = (
        expected_root_fields - {"introduction"} - actual_root_fields
    )
    extra_root_fields = actual_root_fields - expected_root_fields
    if missing_non_introduction_fields or extra_root_fields:
        issues.append({"rule": "root_fields", "actual": sorted(draft)})
    if not local_scope and (not isinstance(draft.get("title"), str) or not draft.get("title", "").strip()):
        issues.append({"rule": "title"})
    draft_sections = draft.get("sections") if isinstance(draft.get("sections"), list) else []
    issues.extend(paragraph_contract_issues(draft_sections))
    plan_sections = list(content_plan.get("sections") or [])
    expected_ids = [str(item.get("section_id") or "") for item in plan_sections]
    if local_scope:
        if expected_ids.count(paragraph_context.section_id) != 1:
            issues.append({"rule": "paragraph_section_not_in_plan"})
        expected_ids = [paragraph_context.section_id]
    policy_by_section = {
        str(item.get("section_id") or ""): str(item.get("evidence_policy") or "fact_required")
        for item in plan_sections
    }
    actual_ids = [str(item.get("section_id") or "") for item in draft_sections if isinstance(item, dict)]
    if actual_ids != expected_ids or len(draft_sections) != len(expected_ids):
        issues.append({"rule": "section_identity_order", "expected": expected_ids, "actual": actual_ids})

    anchors = list(fact_anchor_pack.get("fact_anchors") or [])
    anchor_by_id = {str(item.get("evidence_id") or ""): item for item in anchors}
    use_counts: Counter[str] = Counter()
    fact_counts: Counter[str] = Counter()
    for section in draft_sections:
        if not isinstance(section, dict):
            issues.append({"rule": "section_object"})
            continue
        section_id = str(section.get("section_id") or "")
        allowed_section_fields = {"section_id", "heading", "sentences"}
        if "subsections" in section:
            allowed_section_fields.add("subsections")
        if set(section) != allowed_section_fields:
            issues.append({"rule": "section_fields", "section_id": section_id, "actual": sorted(section)})
        if not isinstance(section.get("heading"), str) or not section.get("heading", "").strip():
            issues.append({"rule": "heading", "section_id": section_id})
        sentences = section.get("sentences") if isinstance(section.get("sentences"), list) else []
        if not isinstance(section.get("sentences"), list):
            issues.append({"rule": "sentences_array", "section_id": section_id})
        if local_scope and not sentences:
            issues.append({"rule": "paragraph_body_empty", "section_id": section_id})
        for index, sentence in enumerate(sentences):
            if not isinstance(sentence, dict):
                issues.append({"rule": "sentence_object", "section_id": section_id, "index": index})
                continue
            allowed_sentence_fields = {"sentence_type", "text"}
            allowed_sentence_fields.add(
                "evidence_ids" if multi_evidence_contract else "evidence_id"
            )
            if writer_execution_contract:
                allowed_sentence_fields.update(WRITER_EXECUTION_FIELDS)
            if "subsection_id" in sentence:
                allowed_sentence_fields.add("subsection_id")
            allowed_sentence_fields.update(field for field in PARAGRAPH_FIELDS if field in sentence)
            if set(sentence) != allowed_sentence_fields:
                issues.append({"rule": "sentence_fields", "section_id": section_id, "index": index, "actual": sorted(sentence)})
            sentence_type = sentence.get("sentence_type")
            evidence_ids = _declared_evidence_ids(sentence)
            if local_scope:
                if (sentence.get("paragraph_id") != paragraph_context.paragraph_id
                        or type(sentence.get("paragraph_order")) is not int
                        or sentence.get("paragraph_order") != paragraph_context.paragraph_order):
                    issues.append({"rule": "paragraph_identity_order", "section_id": section_id, "index": index})
                references = set(evidence_ids)
                if sentence.get("evidence_id"):
                    references.add(sentence["evidence_id"])
                if not references <= set(paragraph_context.allowed_evidence_ids):
                    issues.append({"rule": "paragraph_evidence_scope", "section_id": section_id, "index": index})
            if sentence_type not in {"FACT", "ANALYSIS"}:
                issues.append({"rule": "sentence_type", "section_id": section_id, "index": index})
            if not isinstance(sentence.get("text"), str) or not sentence.get("text", "").strip():
                issues.append({"rule": "sentence_text", "section_id": section_id, "index": index})
            if sentence_type == "FACT":
                fact_counts[section_id] += 1
                if not evidence_ids:
                    issues.append({"rule": "fact_evidence_id_blank", "section_id": section_id, "index": index})
                    continue
                for evidence_id in evidence_ids:
                    anchor = anchor_by_id.get(evidence_id)
                    if not anchor:
                        issues.append({"rule": "fact_evidence_id_unknown", "section_id": section_id, "evidence_id": evidence_id})
                    elif anchor.get("section_id") != section_id:
                        issues.append({"rule": "fact_evidence_id_cross_section", "section_id": section_id, "evidence_id": evidence_id})
                    else:
                        use_counts[evidence_id] += 1
            elif sentence_type == "ANALYSIS" and evidence_ids:
                issues.append({"rule": "analysis_evidence_id_not_empty", "section_id": section_id, "index": index})
    for section_id in expected_ids:
        evidence_policy = policy_by_section.get(section_id, "fact_required")
        if (
            evidence_policy == "fact_required"
            and not local_scope
            and fact_counts[section_id] < fact_required_minimum
        ):
            issues.append({
                "rule": "section_fact_count",
                "section_id": section_id,
                "actual": fact_counts[section_id],
                "minimum": fact_required_minimum,
                "planning_mode": planning_mode,
            })
        if evidence_policy == "analysis_only" and fact_counts[section_id] != 0:
            issues.append({
                "rule": "analysis_only_fact_present",
                "section_id": section_id,
                "actual": fact_counts[section_id],
            })
    if not multi_evidence_contract:
        for anchor in anchors:
            evidence_id = str(anchor.get("evidence_id") or "")
            count = use_counts[evidence_id]
            if anchor.get("role") == "core" and (count > 1 or (not local_scope and count != 1)):
                issues.append({"rule": "core_anchor_use_count", "evidence_id": evidence_id, "actual": count})
            if anchor.get("role") == "supporting" and count > 1:
                issues.append({"rule": "supporting_anchor_use_count", "evidence_id": evidence_id, "actual": count})
    return {
        "flow3_v33_status": "PASS" if not issues else "FAIL_STRUCTURED_OUTPUT",
        "passed": not issues,
        "hard_issue_count": len(issues),
        "hard_issues": issues,
        "planning_mode": planning_mode,
        "fact_required_minimum": fact_required_minimum,
        "multi_evidence_contract": multi_evidence_contract,
        "core_anchor_use_counts": {
            str(item.get("evidence_id") or ""): use_counts[str(item.get("evidence_id") or "")]
            for item in anchors if item.get("role") == "core"
        },
        "fact_sentence_counts": dict(fact_counts),
        "invalid_evidence_reference_count": sum(
            issue["rule"] in {"fact_evidence_id_blank", "fact_evidence_id_unknown", "fact_evidence_id_cross_section"}
            for issue in issues
        ),
    }


def parse_and_validate_structured_draft(
    raw_response: str,
    content_plan: dict[str, Any],
    fact_anchor_pack: dict[str, Any],
    introduction_contract: dict[str, Any] | None = None,
    writing_structure_overlay: dict[str, Any] | None = None,
    evidence_group_pack: dict[str, Any] | None = None,
    writer_plan_control: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    try:
        draft = parse_structured_draft(raw_response)
    except (json.JSONDecodeError, ValueError) as exc:
        return None, {
            "flow3_v33_status": "FAIL_STRUCTURED_OUTPUT", "passed": False,
            "hard_issue_count": 1,
            "hard_issues": [{"rule": "json_parse", "error_type": type(exc).__name__, "message": str(exc)}],
            "invalid_evidence_reference_count": 0,
        }
    subsection_report = validate_subsection_structure(
        draft,
        writing_structure_overlay,
    )
    subsection_issues = [
        {
            "rule": "unknown_subsection_id",
            "subsection_id": subsection_id,
        }
        for subsection_id in subsection_report[
            "unknown_subsection_ids"
        ]
    ]
    subsection_issues.extend(
        {
            "rule": "reordered_subsection_id",
            "subsection_id": subsection_id,
        }
        for subsection_id in subsection_report[
            "reordered_subsection_ids"
        ]
    )
    subsection_issues.extend(
        {
            "rule": "duplicated_subsection_id",
            "subsection_id": subsection_id,
        }
        for subsection_id in subsection_report[
            "duplicated_subsection_ids"
        ]
    )
    draft, paragraph_issues = normalize_paragraph_structured_draft(
        draft,
        writing_structure_overlay,
    )
    writer_execution_report = validate_writer_execution_contract(
        draft,
        content_plan,
        writer_plan_control,
    )
    execution_metadata = _writer_execution_metadata(draft)
    writer_binding_report = {
        "schema_version": "not-applicable",
        "passed": True,
        "issues": [],
        "issue_count": 0,
        "ignored_model_evidence_field_count": 0,
        "bound_fact_count": 0,
        "bound_evidence_count": 0,
        "external_model_calls": 0,
    }
    if (
        isinstance(evidence_group_pack, dict)
        and evidence_group_pack.get("schema_version")
        == EVIDENCE_GROUP_SCHEMA_VERSION
    ):
        paragraph_source = draft  # The existing binder returns a detached projection.
        draft, writer_binding_report = bind_work_summary_writer_units(
            draft,
            evidence_group_pack,
        )
        _restore_writer_execution_metadata(draft, execution_metadata)
        try:
            preserve_paragraph_identity(
                paragraph_source.get("sections") or [], draft.get("sections") or [],
                positional_text_identity=True,
            )
        except ValueError as exc:
            paragraph_issues.append({"rule": "PARAGRAPH_IDENTITY_LOST", "message": str(exc)})
    report = validate_structured_draft(
        draft,
        content_plan,
        fact_anchor_pack,
        introduction_contract,
        writer_plan_control,
    )
    combined_protocol_issues = (
        paragraph_issues
        + subsection_issues
        + list(writer_binding_report.get("issues") or [])
        + list(writer_execution_report.get("hard_issues") or [])
    )
    if combined_protocol_issues:
        hard_issues = (
            list(report["hard_issues"])
            + combined_protocol_issues
        )
        report = {
            **report,
            "flow3_v33_status": "FAIL_STRUCTURED_OUTPUT",
            "passed": False,
            "hard_issue_count": len(hard_issues),
            "hard_issues": hard_issues,
        }
    report["subsection_structure_report"] = subsection_report
    report["writer_binding_report"] = writer_binding_report
    report["writer_execution_report"] = writer_execution_report
    return draft, report


def save_parsed_structured_draft(artifact_root: Path, draft: dict[str, Any]) -> str:
    return _write_json(Path(artifact_root) / "parsed_structured_draft.json", draft)


def _anchor_values(anchor: dict[str, Any], category: str) -> list[str]:
    values = (anchor.get("exact_values") or {}).get(category) or []
    return [str(value) for value in values]


def _fact_detail_values(sentence: str) -> dict[str, list[str]]:
    specifics = _specifics(sentence)
    # "提前" is a standalone temporal modifier. Never absorb its following object.
    times = list(specifics["time_or_frequency"])
    if "提前" in sentence and "提前" not in times:
        times.append("提前")
    for value in re.findall(r"(?:活动)?筹备初期|活动初期|筹备期间|活动期间|活动当天", sentence):
        if value not in times:
            times.append(value)
    times = ["提前" if value.startswith("提前") else value for value in times]
    frequencies = [value for value in times if re.search(r"每日|每天|每周|每月|季度|多次|多轮", value)]
    times = [value for value in times if value not in frequencies]
    roles = []
    for proposition in _base_propositions(sentence)[1:]:
        if proposition.get("action") in {"负责", "牵头成立", "下设"}:
            roles.append(_norm(proposition.get("fact_item")))
    return {
        "time": list(dict.fromkeys(times)),
        "frequency": list(dict.fromkeys(frequencies)),
        "quantity": specifics["quantity_or_date"],
        "organization": specifics["organization_or_post"],
        "organization_role": list(dict.fromkeys(roles)),
        "equipment": specifics["equipment_or_system"],
        "mechanism": specifics["mechanism_or_workgroup"],
        "place": specifics["place_or_functional_area"],
        "result": specifics["result_statement"],
        "absolute_statement": specifics["absolute_statement"],
    }


def _object_value_matches(value: str, candidate: str) -> bool:
    left = re.sub(r"(?:有关|相关|工作)", "", _norm(value))
    right = re.sub(r"(?:有关|相关|工作)", "", _norm(candidate))
    return _value_matches(left, right)


def _anchor_component_values(anchor: dict[str, Any], component: str) -> list[str]:
    """Return only values already bound to the declared Fact Anchor."""
    values: list[str] = []
    canonical = str(anchor.get(f"canonical_{component}") or "").strip()
    if canonical:
        values.append(canonical)
    exact_values = anchor.get("exact_values") or {}
    if component == "object":
        values.extend(str(value) for value in exact_values.get("result") or [] if str(value).strip())

    declared_aliases = anchor.get("allowed_aliases") or {}
    if isinstance(declared_aliases, dict):
        values.extend(
            str(value)
            for value in declared_aliases.get(component) or []
            if str(value).strip()
        )

    alias_groups = DEPARTMENT_ALIAS_GROUPS if component == "subject" else ACTION_ALIAS_GROUPS if component == "action" else {}
    for group_canonical, aliases in alias_groups.items():
        if canonical and any(_norm(canonical) == _norm(value) for value in (group_canonical, *aliases)):
            values.extend(aliases)
            break
    return list(dict.fromkeys(values))


def _anchor_component_position(text: str, values: Iterable[str]) -> int:
    compact_text = _norm(text)
    positions = [
        compact_text.find(_norm(value))
        for value in values
        if _norm(value) and _norm(value) in compact_text
    ]
    return min(positions) if positions else -1


def _anchor_object_position(text: str, values: Iterable[str]) -> int:
    """Find a bound object, including coordinated surface variants, without inference."""
    exact_position = _anchor_component_position(text, values)
    if exact_position >= 0:
        return exact_position
    compact_text = _norm(text)
    for value in values:
        parts = [
            _norm(part)
            for part in re.split(r"[、和及与]", str(value or ""))
            if _norm(part)
        ]
        if len(parts) < 2:
            continue
        positions = [compact_text.find(part) for part in parts]
        if all(position >= 0 for position in positions) and positions == sorted(positions):
            return positions[0]
    return -1


def _anchor_state_action_position(text: str, anchor: dict[str, Any]) -> int:
    """Resolve Atomic Evidence's non-literal PROBLEM action from its own state marker."""
    if _norm(anchor.get("canonical_action")) != _norm("存在"):
        return -1
    if str(anchor.get("semantic_state") or "") not in {"PROBLEM", "GAP"}:
        return -1
    state_values = [
        str(anchor.get("state_qualifier") or ""),
        *(str(value) for value in anchor.get("state_evidence") or []),
    ]
    return _anchor_component_position(text, state_values)


def _anchor_sentence_mapping(text: str, anchor: dict[str, Any]) -> dict[str, Any]:
    """Map a Chinese sentence to one already-declared anchor without inference."""
    subject_position = _anchor_component_position(text, _anchor_component_values(anchor, "subject"))
    action_position = _anchor_component_position(text, _anchor_component_values(anchor, "action"))
    action_from_state = False
    if action_position < 0:
        action_position = _anchor_state_action_position(text, anchor)
        action_from_state = action_position >= 0
    object_position = _anchor_object_position(text, _anchor_component_values(anchor, "object"))
    subject_present = subject_position >= 0 and (action_position < 0 or subject_position <= action_position)
    action_present = action_position >= 0
    # Chinese official prose permits fronted/passive objects. Ownership is
    # established by the declared anchor value itself, not by source-surface
    # similarity or by a fixed subject-action-object word order.
    object_present = object_position >= 0
    return {
        "subject": subject_present,
        "action": action_present,
        "object": object_present,
        "matched": subject_present and action_present and object_present,
        "subject_position": subject_position,
        "action_position": action_position,
        "object_position": object_position,
        "action_from_state": action_from_state,
    }


def _implicit_anchor_subject_supported(
    text: str,
    anchor: dict[str, Any],
    *,
    action_supported: bool,
    object_supported: bool,
    source_surface_supported: bool,
) -> bool:
    """Allow a grammatical subject ellipsis only after predicate ownership is proven."""
    if not (action_supported and object_supported and source_surface_supported):
        return False
    if _anchor_component_position(text, _anchor_component_values(anchor, "subject")) >= 0:
        return False
    if _subject_from_text(text):
        return False
    compact_text = _support_text(text)
    action_position = _anchor_component_position(text, _anchor_component_values(anchor, "action"))
    object_position = _anchor_object_position(text, _anchor_component_values(anchor, "object"))
    return (
        action_position == 0
        or object_position == 0
        or bool(re.match(r"^(?:已经|正在|已|要|应|需|可|将|继续|进一步|持续|同步)", compact_text))
    )


_ANCHOR_PAYLOAD_SEGMENT_RE = re.compile(
    r"(?:并且|以及|同时|并|且)|[，,；;。！？!?\n]+"
)
_ANCHOR_GRAMMAR_ONLY_RE = re.compile(
    r"(?:(?:由|被|把|将|对|围绕|了|的|其|该|有关|相关|和|及|与))*"
)


def _anchor_owned_values(anchor: dict[str, Any]) -> list[str]:
    """Return surface values explicitly owned by this evidence_id."""
    flattened = [
        value
        for component in ("subject", "action", "object")
        for value in _anchor_component_values(anchor, component)
    ]
    exact_values = anchor.get("exact_values") or {}
    for category_values in exact_values.values():
        if isinstance(category_values, list):
            flattened.extend(
                str(value) for value in category_values
                if str(value).strip() and "*" not in str(value)
            )
    flattened.extend(
        str(value)
        for value in (
            anchor.get("state_qualifier"),
            *(anchor.get("state_evidence") or []),
            anchor.get("result"),
            anchor.get("value"),
        )
        if str(value or "").strip()
    )
    return list(dict.fromkeys(flattened))


def _anchor_object_surface_owned(segment: str, anchor: dict[str, Any]) -> bool:
    """Accept only an object surface that is traceable to this anchor."""
    if _anchor_object_position(segment, _anchor_component_values(anchor, "object")) >= 0:
        return True
    compact_segment = _norm(segment)
    for value in _anchor_component_values(anchor, "object"):
        compact_value = _norm(value)
        if len(compact_value) < 4:
            continue
        match = SequenceMatcher(None, compact_segment, compact_value).find_longest_match()
        if match.size >= 4:
            return True
    return False


def _anchor_payload_segment_owned(segment: str, anchor: dict[str, Any]) -> bool:
    """Require each factual segment to carry an anchor-owned predicate/object."""
    return (
        _anchor_component_position(
            segment, _anchor_component_values(anchor, "action")
        ) >= 0
        or _anchor_state_action_position(segment, anchor) >= 0
        or _anchor_object_surface_owned(segment, anchor)
    )


def _anchor_boundary_residual(segment: str, anchor: dict[str, Any]) -> list[str]:
    """Find substantive text outside a directly mapped anchor surface."""
    compact = _norm(segment)
    spans: list[tuple[int, int]] = []
    for value in _anchor_owned_values(anchor):
        normalized = _norm(value)
        if not normalized:
            continue
        for match in re.finditer(re.escape(normalized), compact):
            spans.append((match.start(), match.end()))
    if not spans:
        return []
    action_owned = (
        _anchor_component_position(
            segment, _anchor_component_values(anchor, "action")
        ) >= 0
        or _anchor_state_action_position(segment, anchor) >= 0
    )
    object_owned = (
        _anchor_object_position(
            segment, _anchor_component_values(anchor, "object")
        ) >= 0
    )
    if not (action_owned and object_owned):
        return []
    start = min(item[0] for item in spans)
    end = max(item[1] for item in spans)
    residuals: list[str] = []
    for side, residual in (("prefix", compact[:start]), ("suffix", compact[end:])):
        if not residual or _ANCHOR_GRAMMAR_ONLY_RE.fullmatch(residual):
            continue
        # Preserve the existing deterministic allowance for a short modifier
        # before the anchor or a two-character object noun extension.
        if len(residual) <= 2 and side in {"prefix", "suffix"}:
            continue
        residuals.append(residual)
    return residuals


def _unbound_anchor_payload(text: str, anchor: dict[str, Any]) -> list[str]:
    """Return factual surface that this Fact Anchor cannot explain."""
    unbound: list[str] = []
    for raw_segment in _ANCHOR_PAYLOAD_SEGMENT_RE.split(text):
        segment = raw_segment.strip()
        if not _norm(segment):
            continue
        if not _anchor_payload_segment_owned(segment, anchor):
            unbound.append(_norm(segment))
            continue
        unbound.extend(_anchor_boundary_residual(segment, anchor))
    return list(dict.fromkeys(value for value in unbound if value))


def _organization_matches_anchor(value: str, anchor: dict[str, Any]) -> bool:
    if _value_matches(value, anchor.get("canonical_subject", "")):
        return True
    object_value = _norm(anchor.get("canonical_object", ""))
    for canonical, aliases in DEPARTMENT_ALIAS_GROUPS.items():
        if not _value_matches(value, canonical) and not any(_value_matches(value, alias) for alias in aliases):
            continue
        return any(_norm(alias) and _norm(alias) in object_value for alias in aliases)
    return False


def _proposition_for_declared_anchor(sentence: str, anchor: dict[str, Any]) -> tuple[dict[str, str], int]:
    """Extract the declared triple even when its action is outside the legacy verb lexicon."""
    parsed = _base_propositions(sentence)
    canonical_action = str(anchor.get("canonical_action") or "")
    for proposition in parsed:
        if _value_matches(proposition.get("action", ""), canonical_action):
            scoped_object = _scoped_work_report_object(sentence, canonical_action)
            if scoped_object and re.fullmatch(r"(?:有关|相关)?工作?汇报", proposition.get("object", "")):
                proposition = {**proposition, "object": scoped_object}
            return proposition, len(parsed)
    action_values = [canonical_action]
    for canonical, aliases in ACTION_ALIAS_GROUPS.items():
        if canonical == canonical_action or canonical_action in aliases:
            action_values.extend(aliases)
    matches = [
        (match.start(), value, match.end())
        for value in dict.fromkeys(value for value in action_values if value)
        for match in [re.search(re.escape(value), sentence)] if match
    ]
    if not matches:
        return {"subject": "", "action": "", "object": "", "fact_item": ""}, len(parsed)
    start, action, end = sorted(matches, key=lambda item: (item[0], -len(item[1])))[0]
    before = sentence[:start]
    after = re.split(r"[，,；;。！？!?]", sentence[end:], maxsplit=1)[0]
    scoped_object = _scoped_work_report_object(sentence, action)
    proposition = {
        "subject": _norm(_subject_from_text(before)),
        "action": action,
        "object": _norm(scoped_object) or re.sub(r"^(?:了|对|围绕|有关|相关)", "", _norm(after)),
        "fact_item": _norm(sentence),
    }
    # A legacy verb hit can be a nominal modifier (for example “负责人” or
    # “处置能力”), not a second proposition.  Count only actual parsed
    # propositions so the anchor-aware mapping can judge the sentence itself.
    return proposition, max(1, len(parsed))


def _scoped_work_report_object(sentence: str, action: str) -> str:
    match = re.search(
        rf"(?:围绕|对)([^，,；;。！？!?]{{2,24}}?)(?:工作|有关事项|事项)?[，,；;]\s*{re.escape(action)}(?:有关|相关)?工作汇报",
        sentence,
    )
    return _norm(f"{match.group(1)}汇报") if match else ""


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _raw_source_units(text: str) -> list[str]:
    cleaned = clean_source_residue(text)
    units: list[str] = []
    for value in re.split(r"[。！？!?；;\n]+", cleaned):
        value = re.sub(
            r"^\s*(?:[一二三四五六七八九十]+、|（[一二三四五六七八九十]+）|第[一二三四五六七八九十]+[章节])\s*",
            "",
            value,
        ).strip(" ，,：:")
        if value and not HEADING_RE.match(value):
            units.append(value)
    return units


def _support_text(value: Any) -> str:
    text = _DISCOURSE_PREFIX_RE.sub("", str(value or "").strip())
    return _norm(text)


def _char_ngrams(value: Any, size: int = 2) -> set[str]:
    text = _support_text(value)
    if not text:
        return set()
    if len(text) < size:
        return {text}
    return {text[index:index + size] for index in range(len(text) - size + 1)}


def _overlap_score(left: Any, right: Any) -> float:
    left_grams = _char_ngrams(left)
    right_grams = _char_ngrams(right)
    if not left_grams or not right_grams:
        return 0.0
    return len(left_grams & right_grams) / max(1, min(len(left_grams), len(right_grams)))


def _low_quality_anchor_fields(anchor: dict[str, Any], source_span: str) -> list[str]:
    issues: list[str] = []
    normalized_source = _norm(source_span)
    for field in ("subject", "action", "object"):
        value = str(anchor.get(f"canonical_{field}") or "").strip()
        normalized = _norm(value)
        if not normalized:
            issues.append(field)
            continue
        if len(normalized) == 1 and normalized in _LOW_QUALITY_FUNCTION_WORDS:
            issues.append(field)
            continue
        if normalized_source and field in {"subject", "object"} and normalized not in normalized_source:
            issues.append(field)
    return issues


def build_fact_source_support(
    fact_anchor_pack: dict[str, Any],
    selected_evidence_local: Iterable[dict[str, Any]],
    retrieval_result: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Bind selected evidence to its original local source span without changing anchors."""
    anchors = {
        str(item.get("evidence_id") or ""): item
        for item in fact_anchor_pack.get("fact_anchors") or []
    }
    selected = {
        str(item.get("evidence_id") or ""): item
        for item in selected_evidence_local
        if item.get("evidence_id")
    }
    chunks: dict[tuple[str, str], str] = {}
    chunks_by_id: dict[str, str] = {}
    for chunk in retrieval_result.get("selected_chunks") or []:
        metadata = chunk.get("metadata") or {}
        document_id = str(
            chunk.get("document_id") or chunk.get("doc_id")
            or metadata.get("document_id") or ""
        )
        chunk_id = str(chunk.get("chunk_id") or metadata.get("chunk_id") or "")
        text = str(chunk.get("text") or chunk.get("content") or "")
        if text and chunk_id:
            chunks[(document_id, chunk_id)] = text
            chunks_by_id[chunk_id] = text

    support: dict[str, dict[str, Any]] = {}
    for evidence_id, anchor in anchors.items():
        item = selected.get(evidence_id, {})
        document_id = str(item.get("document_id") or "")
        chunk_id = str(item.get("chunk_id") or "")
        chunk_text = chunks.get((document_id, chunk_id)) or chunks_by_id.get(chunk_id, "")
        expected_hash = str(item.get("source_span_sha256") or "")
        fragments = list(_source_fragments(chunk_text)) if chunk_text else []
        exact_fragment = next(
            (fragment for fragment in fragments if _sha256_text(fragment) == expected_hash),
            "",
        )
        # A declared production binding must resolve exactly.  Older in-memory
        # callers that never declared a source hash remain distinguishable
        # from an invalid binding and continue through the legacy anchor-only
        # compatibility path below.
        source_span_binding_valid = (
            bool(exact_fragment) if expected_hash else None
        )
        if not exact_fragment and fragments:
            anchor_text = "".join(
                str(anchor.get(name) or "")
                for name in ("canonical_subject", "canonical_action", "canonical_object")
            )
            exact_fragment = max(fragments, key=lambda value: _overlap_score(value, anchor_text))
        source_sentence = exact_fragment
        if exact_fragment:
            containing_units = [
                unit for unit in _raw_source_units(chunk_text)
                if _support_text(exact_fragment) in _support_text(unit)
            ]
            if containing_units:
                source_sentence = min(containing_units, key=len)
        support[evidence_id] = {
            "evidence_id": evidence_id,
            "section_id": str(anchor.get("section_id") or ""),
            "document_id": document_id,
            "chunk_id": chunk_id,
            "source_span_sha256": expected_hash,
            "source_span_actual_sha256": (
                _sha256_text(exact_fragment) if exact_fragment else ""
            ),
            "source_span_binding_valid": source_span_binding_valid,
            "source_span": exact_fragment,
            "source_sentence": source_sentence,
            "source_available": bool(exact_fragment),
            "low_quality_anchor_fields": _low_quality_anchor_fields(anchor, exact_fragment),
        }
    return support


def _dedupe_matches(pattern: re.Pattern[str], text: str) -> list[str]:
    return list(dict.fromkeys(match.group(0) for match in pattern.finditer(text)))


def _verified_classification_counts(text: str) -> set[tuple[int, int]]:
    """Exempt only a locally counted enumeration's own numeric token.

    Labels must be explicit, distinct, non-numeric phrases. Business numbers
    elsewhere (even in the same sentence) still follow the normal evidence rule.
    """
    numeral = r"[2-9二三四五六七八九两]"
    label = r"[\u3400-\u9fff]{2,16}?"
    labels = rf"{label}(?:[、，,和与及]{label})+"
    patterns = (
        re.compile(rf"(?:围绕|从|包括|分为)(?P<labels>{labels})(?P<count>{numeral}个)(?:方面|维度)"),
        re.compile(rf"(?:从|包括|分为)(?P<count>{numeral}个)(?:方面|维度)[^：:。；;]{{0,8}}[：:](?P<labels>{labels})(?=[。；;！？!?]|$)"),
    )
    verified = set()
    for pattern in patterns:
        for match in pattern.finditer(text):
            items = re.split(r"[、，,和与及]", match['labels'])
            number = match['count'][:-1]
            count = int(number) if number.isdigit() else {"二": 2, "两": 2, "三": 3, "四": 4,
                "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}[number]
            if len(items) == len(set(items)) == count:
                verified.add(match.span('count'))
    return verified


def _responsible_subject(value: str) -> str:
    """Comparison-only normalization; frozen anchors are never rewritten."""
    value = _norm(value)
    if re.fullmatch(r"(?:但|当前|目前|进一步|加快|逐项|定期)+", value):
        return ""
    return value


def _bound_state_check(anchor: dict[str, Any], text: str, source: str) -> dict[str, Any]:
    # Noun phrases are not completion predicates. This changes only the probe,
    # not Candidate/Evidence text or the upstream semantic-state contract.
    probe = re.sub(r"完成(?:时限|期限|时间|要求|目标)", "任务期限", text)
    result = check_semantic_state_consistency(anchor, probe)
    without_connector = lambda s: re.sub(r"^(?:但|当前|目前)+", "", _norm(s))
    if source and without_connector(text) == without_connector(source):
        result.update(passed=True, candidate_state=result['source_state'], completion_upgrade=False)
    qualifier = re.match(r"^((?:如果|若|只有|除非)[^，,。；;]+)[，,]", source)
    if qualifier and _norm(qualifier.group(1)) not in _norm(text):
        result.update(passed=False, lost_source_qualifier=qualifier.group(1))
    return result


def _hard_fact_values(text: str) -> dict[str, list[str]]:
    details = _fact_detail_values(text)
    outcome_values = []
    for match in _COMPLETION_OR_OUTCOME_RE.finditer(text):
        value = match.group(0)
        if value == "完成" and re.match(r"时限|期限|时间|要求|目标", text[match.end():]):
            continue
        if (
            value.startswith(("全面", "全部", "圆满", "显著", "明显"))
            or value.endswith(("完成", "实现", "建成", "达到"))
        ):
            outcome_values.append(value)
    if "形成长效机制" in text:
        outcome_values.append("形成长效机制")
    values = {
        "number_or_time": list(dict.fromkeys(m.group(0) for m in _HARD_NUMBER_RE.finditer(text)
            if m.span() not in _verified_classification_counts(text))),
        "organization": _dedupe_matches(_NAMED_ORGANIZATION_RE, text),
        "person_or_role": _dedupe_matches(_PERSON_OR_ROLE_RE, text),
        "policy_or_project": _dedupe_matches(_POLICY_PROJECT_RE, text),
        "equipment": list(details["equipment"]),
        "place": list(dict.fromkeys([*details["place"], *_dedupe_matches(_NAMED_PLACE_RE, text)])),
        "absolute_statement": list(details["absolute_statement"]),
        "strong_outcome": list(dict.fromkeys(outcome_values)),
    }
    return {name: value for name, value in values.items() if value}


def _hard_value_supported(value: str, category: str, source_text: str) -> bool:
    value_norm = _norm(value)
    source_norm = _norm(source_text)
    if value_norm and value_norm in source_norm:
        return True
    source_values = _hard_fact_values(source_text).get(category) or []
    if (
        category == "strong_outcome"
        and value.startswith(("全面", "全部", "圆满", "显著", "明显"))
    ):
        return False
    if category == "person_or_role":
        candidate_roles = set(re.findall(
            r"书记|局长|主任|处长|科长|经理|董事长|领导|负责人",
            value,
        ))
        source_roles = set(re.findall(
            r"书记|局长|主任|处长|科长|经理|董事长|领导|负责人",
            source_text,
        ))
        if candidate_roles and candidate_roles <= source_roles:
            return True
    if category in {"organization", "person_or_role"}:
        return any(
            min(len(value_norm), len(_norm(candidate))) >= 4
            and (value_norm in _norm(candidate) or _norm(candidate) in value_norm)
            for candidate in source_values
        )
    return any(_value_matches(value, candidate) for candidate in source_values)


_RESULT_SIGNATURE_PATTERNS = {
    "completed_state": re.compile(
        r"(?:已经|已|全面|全部|圆满|顺利)[^，。；]{0,12}"
        r"(?:完成|实现|建成|形成|达到|落实)|"
        r"(?:完成|实现|建成|形成|达到|落实)了"
    ),
    "measured_effect": re.compile(
        r"(?:得到|获得)(?:明显|显著|有效|持续|大幅|进一步)?"
        r"(?:提升|提高|增强|改善|下降|增长)|"
        r"(?:明显|显著|有效|持续|大幅|进一步)"
        r"(?:提升|提高|增强|改善|下降|增长)|"
        r"保持(?:较高|稳定|良好)(?:水平|状态|态势)?"
    ),
    "directional_transition": re.compile(
        r"由[^，。；]{1,24}(?:向|转向|转为)[^，。；]{1,24}|"
        r"(?:实现|完成|推动)[^，。；]{0,24}(?:转变|转型)"
    ),
    "achievement": re.compile(
        r"取得(?:明显|显著|积极|阶段性|初步|良好)?"
        r"(?:成效|成果|效果|进展)"
    ),
    "causal_outcome": re.compile(
        r"(?:从而|进而|因此|因而|使得?|促使|带动)[^，。；]{2,36}"
    ),
}
_RESULT_SIGNATURE_NOISE_RE = re.compile(
    r"已经|已|全面|全部|圆满|顺利|得到|获得|实现|完成|建成|形成|达到|落实|"
    r"明显|显著|有效|持续|大幅|进一步|提升|提高|增强|改善|下降|增长|保持|较高|稳定|良好|水平|状态|态势|"
    r"转变|转型|取得|成效|成果|效果|进展|从而|进而|因此|因而|使得?|促使|带动|"
    r"推动|由|向|转向|转为|了|的|地|得"
)


def _result_claim_signatures(text: str) -> list[dict[str, str]]:
    signatures: list[dict[str, str]] = []
    for clause in re.split(r"[，,；;。！？!?]+", str(text or "")):
        compact_clause = _norm(clause)
        if not compact_clause:
            continue
        for kind, pattern in _RESULT_SIGNATURE_PATTERNS.items():
            if not pattern.search(compact_clause):
                continue
            target = _RESULT_SIGNATURE_NOISE_RE.sub("", compact_clause)
            signatures.append({
                "kind": kind,
                "claim": compact_clause,
                "target": target,
            })
    return signatures


def _result_signature_supported(
    candidate: dict[str, str],
    source_signatures: list[dict[str, str]],
) -> bool:
    compatible_kinds = {
        "measured_effect": {"measured_effect", "achievement"},
        "achievement": {"achievement", "measured_effect"},
    }.get(candidate["kind"], {candidate["kind"]})
    for source in source_signatures:
        if source["kind"] not in compatible_kinds:
            continue
        candidate_target = candidate["target"]
        source_target = source["target"]
        if not candidate_target or not source_target:
            return True
        if (
            candidate_target in source_target
            or source_target in candidate_target
            or _overlap_score(candidate_target, source_target) >= 0.55
        ):
            return True
    return False


_EQUIVALENCE_OMISSION_RE = re.compile(
    r"与(?:方案|规划|既定)?目标要求相比|较为|明显|仍然|仍|的|地|得"
)


def _equivalence_normalized(value: str) -> str:
    normalized = _support_text(value)
    normalized = normalized.replace("尚存", "存在").replace("和", "与")
    return _EQUIVALENCE_OMISSION_RE.sub("", normalized)


def _safe_surface_equivalent(candidate: str, source: str) -> bool:
    """Accept conservative compression without adding a factual predicate."""

    candidate_surface = _support_text(candidate)
    source_surface = _support_text(source)
    if not candidate_surface or not source_surface:
        return False
    if candidate_surface == source_surface:
        return True
    candidate_equivalent = _equivalence_normalized(candidate_surface)
    source_equivalent = _equivalence_normalized(source_surface)
    if candidate_equivalent == source_equivalent:
        return True
    shorter, longer = sorted(
        (candidate_equivalent, source_equivalent), key=len,
    )
    return bool(
        len(shorter) >= 8
        and shorter in longer
        and len(shorter) / max(1, len(longer)) >= 0.72
    )


def _unsupported_hard_details(text: str, source_text: str) -> list[dict[str, str]]:
    unsupported: list[dict[str, str]] = []
    plan_to_plan = (
        classify_semantic_state(text)["semantic_state"] == "PLAN"
        and classify_semantic_state(source_text)["semantic_state"] == "PLAN"
    )
    for category, values in _hard_fact_values(text).items():
        for value in values:
            if category == "strong_outcome" and plan_to_plan:
                continue
            if not _hard_value_supported(value, category, source_text):
                unsupported.append({"category": category, "value": value})
    source_result_signatures = _result_claim_signatures(source_text)
    for signature in _result_claim_signatures(text):
        if not _result_signature_supported(signature, source_result_signatures):
            unsupported.append({
                "category": "result_state_or_causality",
                "value": signature["claim"],
            })
    return unsupported


def _hard_detail_supported_by_source(
    detail: dict[str, str],
    source_text: str,
) -> bool:
    """Check one already-extracted hard detail against one source only."""

    category = str(detail.get("category") or "")
    value = str(detail.get("value") or "")
    if category == "result_state_or_causality":
        signatures = _result_claim_signatures(value)
        source_signatures = _result_claim_signatures(source_text)
        return bool(signatures) and all(
            _result_signature_supported(item, source_signatures)
            for item in signatures
        )
    return _hard_value_supported(value, category, source_text)


def _polarity(text: str) -> str:
    normalized = _norm(text)
    negative = any(marker in normalized for marker in _NEGATIVE_POLARITY_MARKERS)
    positive = any(marker in normalized for marker in _POSITIVE_POLARITY_MARKERS)
    if negative and not positive:
        return "negative"
    if positive and not negative:
        return "positive"
    return "neutral"


def _trusted_anchor_match_count(text: str, anchor: dict[str, Any], low_quality: set[str]) -> int:
    count = 0
    normalized_text = _norm(text)
    for field in ("subject", "action", "object"):
        if field in low_quality:
            continue
        value = str(anchor.get(f"canonical_{field}") or "")
        if value and (
            _norm(value) in normalized_text
            or _value_matches(value, text)
        ):
            count += 1
    return count


def _source_related(text: str, source_text: str, anchor: dict[str, Any], low_quality: set[str]) -> bool:
    if not source_text:
        return _trusted_anchor_match_count(text, anchor, low_quality) >= 2
    if _overlap_score(text, source_text) >= 0.28:
        return True
    return _trusted_anchor_match_count(text, anchor, low_quality) >= 2


def _declared_member_materialized(
    text: str,
    source_text: str,
    all_source_texts: list[str],
    anchor: dict[str, Any],
    low_quality: set[str],
    *,
    strict: bool = False,
) -> bool:
    """Prove one declared group member is present; never infer another member."""

    if _safe_surface_equivalent(text, source_text):
        return True
    if not strict and _trusted_anchor_match_count(text, anchor, low_quality) >= 2:
        return True
    if not _source_related(text, source_text, anchor, low_quality):
        return False
    own_grams = _char_ngrams(source_text)
    if strict and own_grams:
        source_coverage = len(own_grams & _char_ngrams(text)) / len(own_grams)
        if source_coverage < 0.70:
            return False
    other_grams: set[str] = set()
    skipped = False
    for candidate in all_source_texts:
        if not skipped and candidate == source_text:
            skipped = True
            continue
        other_grams.update(_char_ngrams(candidate))
    unique_grams = own_grams - other_grams
    if not unique_grams:
        return True
    matched = unique_grams & _char_ngrams(text)
    required = max(1, min(3, (len(unique_grams) + 7) // 8))
    return len(matched) >= required


def _member_claim_text(
    text: str,
    anchor: dict[str, Any],
    source_text: str,
    low_quality: set[str],
) -> str:
    """Select the clause that actually materializes one bound Anchor."""

    clauses = [
        value.strip()
        for value in re.split(r"[，,；;。！？!?]+", str(text or ""))
        if value.strip()
    ]
    if not clauses:
        return str(text or "")
    ranked: list[tuple[float, int, str]] = []
    for index, clause in enumerate(clauses):
        trusted = _trusted_anchor_match_count(clause, anchor, low_quality)
        overlap = _overlap_score(clause, source_text)
        # Bound source-span similarity outranks possibly low-quality canonical
        # fields; the latter only breaks ties. This prevents a shared/wrongly
        # projected action from assigning one Anchor to another Anchor's clause.
        ranked.append((overlap * 5.0 + trusted, -index, clause))
    return max(ranked)[2]


def _source_entails_surface_form(text: str, source_text: str) -> bool:
    candidate = _support_text(text)
    source = _support_text(source_text)
    if not candidate or not source:
        return False
    if _polarity(text) != "neutral" and _polarity(source_text) != "neutral" and _polarity(text) != _polarity(source_text):
        return False
    if candidate in source:
        return True
    candidate_grams = _char_ngrams(candidate)
    source_grams = _char_ngrams(source)
    return bool(candidate_grams) and len(candidate_grams & source_grams) / len(candidate_grams) >= 0.78


def _has_isolated_supported_clause(text: str, source_text: str) -> bool:
    clauses = [
        value.strip()
        for value in re.split(r"[，,；;。！？!?]+", str(text or ""))
        if value.strip()
    ]
    if len(clauses) < 2:
        return False
    return any(
        not _unsupported_hard_details(clause, source_text)
        and (
            _safe_surface_equivalent(clause, source_text)
            or _source_entails_surface_form(clause, source_text)
        )
        for clause in clauses
    )


def _hard_details_require_a2(
    hard_details: list[dict[str, str]],
    text: str,
) -> bool:
    """Keep newly asserted concrete facts outside the deletion-repair lane."""

    always_blocked = {
        "number_or_time",
        "organization",
        "person_or_role",
        "equipment",
        "place",
        "absolute_statement",
        "strong_outcome",
        "result_state_or_causality",
    }
    if any(item.get("category") in always_blocked for item in hard_details):
        return True
    if any(item.get("category") == "policy_or_project" for item in hard_details):
        return bool(
            re.search(
                r"《[^》]{2,80}》|(?:文件|方案|办法|规定|通知|意见|条例|细则)",
                text,
            )
        )
    return False


def _deterministic_safe_fact(source: dict[str, Any]) -> str:
    span = str(source.get("source_span") or "").strip(" ，,。；;：:")
    sentence = str(source.get("source_sentence") or "").strip(" ，,。；;：:")
    dangling = bool(re.search(r"(?:方面|过程中|情况下|基础上|前提下|同时)$", span))
    replacement = sentence if dangling and sentence else span or sentence
    return f"{replacement}。" if replacement else ""


def _fact_probe(
    *, section_id: str, evidence_id: str, text: str,
) -> dict[str, Any]:
    return {
        "sections": [{
            "section_id": section_id,
            "sentences": [{
                "sentence_type": "FACT",
                "evidence_id": evidence_id,
                "text": text,
            }],
        }],
    }


def _fact_at(
    draft: dict[str, Any], section_id: str, sentence_index: int,
) -> dict[str, Any]:
    section = next(
        item for item in draft.get("sections") or []
        if str(item.get("section_id") or "") == section_id
    )
    return section["sentences"][sentence_index]


def _a1_safe_repair_input(
    *,
    text: str,
    evidence_ids: list[str],
    source_text: str,
    anchor_terms: list[str],
    risk_type: str,
) -> dict[str, Any] | None:
    """Propose one bounded downgrade; Evidence Safety remains the executor."""

    action = ""
    if risk_type == "FACT_STRENGTH_UPGRADE":
        action = suggested_strength_downgrade_action(text)
    elif risk_type == "SEMANTIC_STATE_UPGRADE":
        retained = select_local_deletion_candidate(
            text,
            source_text=source_text,
            anchor_terms=anchor_terms,
        )
        if retained is not None:
            retained_norm = _norm(retained["text"])
            clauses = [
                value.strip()
                for value in re.split(r"[，,；;。！？!?]+", text)
                if value.strip()
            ]
            for index, clause in enumerate(clauses, 1):
                if _norm(clause) == retained_norm:
                    action = f"RETAIN_ORIGINAL_CLAUSE_{index}"
                    break
    if not action:
        return None

    repair_input = build_targeted_safe_repair_input(
        risk_sentence=text,
        risk_type=risk_type,
        evidence_ids=evidence_ids,
        original_fact_status=str(
            classify_semantic_state(text).get("semantic_state") or "UNKNOWN"
        ),
        suggested_downgrade_action=action,
    )
    candidate = targeted_safe_repair_candidate(repair_input)
    if candidate is None:
        return None
    if _unsupported_hard_details(candidate["text"], source_text):
        return None
    return repair_input


def evaluate_full_article_facts(candidate, sidecar):
    """Read-only full-text adapter into the existing FACT owner.

    A verification container contains exactly one original review unit. Its
    scope is explicit and still checked by the canonical gate, never globalized.
    Unknown/unclassified text (including headings) remains review-required.
    This does not call the auto-repair pipeline or rewrite the canonical body.
    """
    from app.models.full_article import (
        FullArticleCandidate, CitationSidecar, FullArticleContractError,
        article_units, article_blocks, bind_report, article_check_identity,
    )
    candidate = FullArticleCandidate.model_validate(candidate.model_dump())
    sidecar = CitationSidecar.model_validate(sidecar.model_dump())
    if (sidecar.candidate_digest != candidate.digest
            or sidecar.evidence_sha256 != candidate.evidence_sha256):
        raise FullArticleContractError("STALE_CITATION_SIDECAR")
    registry = json.loads(candidate.evidence_json)
    anchors = {eid: row["anchor"] for eid, row in registry.items()}
    sources = {eid: {**row["source_scope"], "source_available": True,
        "source_sentence": row["source_scope"]["source_span"],
        "source_span_binding_valid": True} for eid, row in registry.items()}
    units = article_units(candidate.content)
    by_unit = {}
    for proposal in sidecar.proposals:
        if proposal.unit_id in by_unit or proposal.unit_id not in {u.unit_id for u in units}:
            raise FullArticleContractError("CITATION_LOCATION_INVALID")
        by_unit[proposal.unit_id] = proposal
    blocks = {b.block_id: b for b in article_blocks(candidate.content)}
    identity = article_check_identity(candidate)
    article_sections = {}
    heading_path = []
    for block in blocks.values():
        if block.level:
            heading_path = [h for h in heading_path if h[0] < block.level]
            heading_path.append((block.level, block.block_id))
        article_sections[block.block_id] = [h[1] for h in heading_path]
    rows = []
    for unit in units:
        text = candidate.content[unit.start:unit.end]
        proposal = by_unit.get(unit.unit_id)
        checked = {"passed": False, "reason": "UNBOUND_OR_UNCLASSIFIED",
                   "risk_class": "D_REVIEW_REQUIRED"}
        non_factual = False
        # Only plain advisory clauses can enter existing ANALYSIS validation.
        # Headings, signatures, unknown prose and factual qualifiers stay under
        # review. Absence of a binding alone never proves NON_FACTUAL.
        if (not proposal and not blocks[unit.block_id].level
                and classify_fact_sentence(text) == "general_analysis"
                and not any(_specifics(text).values())
                and not re.search(r"已|正在|目前|实际|取得|成效|结果|导致|证明|表明", text)
                and not any(marker in text for marker in (*_LOCAL_FACT_ANCHORS, *_OCCURRED_ACTIONS))
                and all(any(clause.strip().startswith(marker) for marker in _GENERAL_MARKERS)
                        for clause in re.split(r"[，,；;]", text) if clause.strip())):
            analysis = validate_analysis_sentences({"sections": [{"section_id": unit.block_id,
                "sentences": [{"sentence_type": "ANALYSIS", "text": text, "evidence_ids": []}]}]})
            non_factual = analysis["passed"]
            if non_factual:
                checked = {"passed": False, "reason": "NON_FACTUAL", "analysis_validation": analysis,
                           "risk_class": None}
        if proposal:
            draft = {"sections": [{"section_id": proposal.scope_id,
                "sentences": [{"sentence_type": "FACT", "text": text,
                               "evidence_ids": list(proposal.evidence_ids)}]}]}
            checked = _evaluate_evidence_safety_fact(draft, proposal.scope_id, 0,
                anchors=anchors, source_support=sources, strict_multi_evidence_binding=True)
            restricted = [eid for eid in proposal.evidence_ids if eid in registry and (
                registry[eid]["fact"].get("writability", {}).get("status") != "WRITABLE"
                or registry[eid]["fact"].get("writability", {}).get("usage")
                not in {"INDEPENDENT_OR_SUPPORTING"})]
            if restricted:
                checked = {**checked, "passed": False, "reason": "EVIDENCE_USAGE_REQUIRES_REVIEW",
                           "risk_class": (FactSafetyRiskClass.A_HARD_BLOCK.value
                               if checked.get("risk_class") == FactSafetyRiskClass.A_HARD_BLOCK.value
                               else FactSafetyRiskClass.D_REVIEW_REQUIRED.value),
                           "restricted_evidence_ids": restricted}
            # Uninterpreted limitations never become implicit permission. All
            # original fields remain in the immutable registry and bindings.
            limitations = [eid for eid in proposal.evidence_ids if eid in registry and (
                any(registry[eid].get(k) for k in ("limitation", "limitations"))
                or any(registry[eid][part].get(k) for part in ("fact", "anchor", "source_scope")
                       for k in ("limitation", "limitations"))
                or registry[eid]["fact"].get("semantic_state") != registry[eid]["anchor"].get("semantic_state"))]
            if limitations:
                checked = {**checked, "passed": False, "reason": "EVIDENCE_LIMITATION_REQUIRES_REVIEW",
                           "risk_class": (FactSafetyRiskClass.A_HARD_BLOCK.value
                               if checked.get("risk_class") == FactSafetyRiskClass.A_HARD_BLOCK.value
                               else FactSafetyRiskClass.D_REVIEW_REQUIRED.value),
                           "restricted_evidence_ids": limitations}
        validation_result = ("NON_FACTUAL" if non_factual else "SUPPORTED" if checked["passed"] else
            "HARD_BLOCK" if checked.get("risk_class") == FactSafetyRiskClass.A_HARD_BLOCK.value
            else "REVIEW_REQUIRED")
        rows.append({**unit.model_dump(), "line": blocks[unit.block_id].line,
            "article_version": candidate.version, "article_hash": candidate.content_sha256,
            "article_section": article_sections[unit.block_id],
            "observed_text": text, "scope_id": proposal.scope_id if proposal else None,
            "evidence_ids": list(proposal.evidence_ids) if proposal else [],
            "candidate_evidence_ids": list(proposal.evidence_ids) if proposal else [],
            "binding_origin": proposal.origin if proposal else "NO_BINDING",
            "bindings": [{"evidence_id": eid, "scope": registry[eid]["section_id"],
                "source": registry[eid]["source_scope"], "anchor": registry[eid]["anchor"],
                "fact": registry[eid]["fact"]} for eid in (proposal.evidence_ids if proposal else ())
                if eid in registry],
            "validation_result": validation_result,
            "classification": "ANALYSIS" if non_factual else "CANDIDATE_FACT" if proposal else "UNCLASSIFIED",
            "result": checked, "status": "NON_FACTUAL" if non_factual else "SUPPORTED" if checked["passed"] else "REVIEW_REQUIRED",
            "review_suggestion": "结合相邻原文、原始资料核对主体、动作、对象、时间和状态；引用存在不等于支持。"})
    unresolved = [row for row in rows if row["status"] not in {"SUPPORTED", "NON_FACTUAL"}]
    return bind_report("FACT", candidate, sidecar, {
        **identity, "evaluator": "flow3_v3_trace._evaluate_evidence_safety_fact",
        "validation_counts": {status: sum(r["validation_result"] == status for r in rows)
            for status in ("SUPPORTED", "REVIEW_REQUIRED", "HARD_BLOCK", "NON_FACTUAL")},
        "status": "PASS" if rows and not unresolved else "REVIEW_REQUIRED",
        "units": rows, "unresolved_count": len(unresolved),
        "coverage": "EVERY_NONBLANK_UNIT_INCLUDING_HEADINGS",
        "source_verification": "FROZEN_SPAN_HASH_ONLY_NOT_ORIGINAL_DOCUMENT_RETRIEVAL",
        "automatic_rewrite": False,
    })


def require_full_article_fact_report(candidate, sidecar, report):
    """An ID/digest alone is not an evaluator receipt. Reproduce local results."""
    from app.models.full_article import FullArticleContractError
    report.require_current(candidate, sidecar)
    if report.owner != "FACT" or report != evaluate_full_article_facts(candidate, sidecar):
        raise FullArticleContractError("FACT_REPORT_EVALUATION_MISMATCH")


def _evaluate_evidence_safety_fact(
    draft: dict[str, Any],
    section_id: str,
    sentence_index: int,
    *,
    anchors: dict[str, dict[str, Any]],
    source_support: dict[str, dict[str, Any]],
    strict_multi_evidence_binding: bool = False,
) -> dict[str, Any]:
    """Use existing source, Semantic State, and FACT validators for one atom."""

    sentence = _fact_at(draft, section_id, sentence_index)
    text = str(sentence.get("text") or "")
    evidence_ids = _declared_evidence_ids(sentence)
    evidence_id = evidence_ids[0] if len(evidence_ids) == 1 else ""
    declared_anchors = [anchors.get(value) for value in evidence_ids]
    declared_sources = [source_support.get(value) or {} for value in evidence_ids]
    anchor = declared_anchors[0] if len(declared_anchors) == 1 else None
    source = declared_sources[0] if len(declared_sources) == 1 else {}
    source_span = str(source.get("source_span") or "")
    source_sentence = str(source.get("source_sentence") or source_span)
    # Local safety decisions must stay inside the bound atomic span.  The
    # surrounding source sentence can contain adjacent, unbound claims (and
    # placeholders), so it is only a fallback when no span is available.
    source_text = source_span or source_sentence
    result = {
        "passed": False,
        "section_id": section_id,
        "evidence_id": evidence_id,
        "evidence_ids": evidence_ids,
        "reason": "",
        "risk_class": FactSafetyRiskClass.D_REVIEW_REQUIRED.value,
        "hard_contract_failure": False,
        "veto_fact": False,
        "hard_details": [],
        "source_span_sha256": str(source.get("source_span_sha256") or ""),
        "source_text_for_repair": source_text,
        "anchor_terms": [],
        "source_projection_candidate": "",
    }
    if not evidence_ids or any(item is None for item in declared_anchors):
        result.update(
            reason="invalid_evidence_id",
            risk_class=FactSafetyRiskClass.A_HARD_BLOCK.value,
            hard_contract_failure=True,
            veto_fact=True,
        )
        return result
    if any(str(item.get("section_id") or "") != section_id for item in declared_anchors if item):
        result.update(
            reason="section_mismatch",
            risk_class=FactSafetyRiskClass.A_HARD_BLOCK.value,
            hard_contract_failure=True,
            veto_fact=True,
        )
        return result
    if any(item.get("source_span_binding_valid") is False for item in declared_sources):
        result.update(
            reason="source_span_binding_invalid",
            risk_class=FactSafetyRiskClass.A_HARD_BLOCK.value,
            hard_contract_failure=True,
            veto_fact=True,
        )
        return result

    # A literal chapter-topic lead-in is organizational context, not another
    # completed action. It is ignored only for the support probe, never deleted.
    topic = re.match(r"围绕([^，,。；;]{2,60})[，,]", text)
    heading = next((str(s.get('heading') or '') for s in draft.get('sections', [])
                    if s.get('section_id') == section_id), '')
    if (topic and topic[1] in heading and not _hard_fact_values(topic[1])
            and not re.search(r"已|完成|建成|取得|实现|未|不", topic[1])):
        probe = copy.deepcopy(draft)
        _fact_at(probe, section_id, sentence_index)['text'] = text[topic.end():]
        checked = _evaluate_evidence_safety_fact(probe, section_id, sentence_index,
            anchors=anchors, source_support=source_support,
            strict_multi_evidence_binding=strict_multi_evidence_binding)
        if checked['passed']:
            return {**checked, 'retained_topic_context': topic[0]}
    discourse = _DISCOURSE_PREFIX_RE.match(text)
    if discourse and discourse.group(0) != "实践表明":
        prefix_end = discourse.end()
        while prefix_end < len(text) and text[prefix_end] in "，, ":
            prefix_end += 1
        probe = copy.deepcopy(draft)
        _fact_at(probe, section_id, sentence_index)['text'] = text[prefix_end:]
        checked = _evaluate_evidence_safety_fact(probe, section_id, sentence_index,
            anchors=anchors, source_support=source_support,
            strict_multi_evidence_binding=strict_multi_evidence_binding)
        if checked['passed']:
            return {**checked, 'retained_discourse_context': text[:prefix_end]}

    # Describe every declaration before the validator's first-failure return.
    # Repair consumes this inventory, never the order of diagnostic errors.
    materialization_sources = [
        str(item.get("source_span") or item.get("source_sentence") or "")
        for item in declared_sources
    ]
    result["evidence_alignment"] = [
        {
            "evidence_id": value,
            "materialized": _declared_member_materialized(
                text, support, materialization_sources, bound_anchor,
                set(bound_source.get("low_quality_anchor_fields") or []),
                strict=strict_multi_evidence_binding and len(evidence_ids) > 1,
            ),
            "subject": str(bound_anchor.get("canonical_subject") or ""),
            "semantic_state": str(bound_anchor.get("semantic_state") or ""),
        }
        for value, bound_anchor, bound_source, support in zip(
            evidence_ids, declared_anchors, declared_sources, materialization_sources
        )
    ]

    if len(evidence_ids) > 1:
        source_texts = [
            str(item.get("source_sentence") or item.get("source_span") or "")
            for item in declared_sources
        ]
        materialization_texts = [
            str(item.get("source_span") or item.get("source_sentence") or "")
            for item in declared_sources
        ]
        combined_source = "。".join(value for value in source_texts if value)
        result["source_text_for_repair"] = combined_source
        result["evidence_support_texts"] = {
            evidence_id: materialization_text
            for evidence_id, materialization_text in zip(
                evidence_ids, materialization_texts
            )
        }
        result["anchor_terms"] = [
            str(item.get(name) or "")
            for item in declared_anchors if item
            for name in ("canonical_subject", "canonical_action", "canonical_object")
            if item.get(name)
        ]
        hard_details = _unsupported_hard_details(text, combined_source)
        result["hard_details"] = hard_details
        for declared_evidence_id, declared_anchor, declared_source in zip(
            evidence_ids, declared_anchors, declared_sources,
        ):
            assert declared_anchor is not None
            declared_source_text = str(
                declared_source.get("source_span")
                or declared_source.get("source_sentence")
                or ""
            )
            low_quality = set(declared_source.get("low_quality_anchor_fields") or [])
            member_materialized = _declared_member_materialized(
                text,
                declared_source_text,
                materialization_texts,
                declared_anchor,
                low_quality,
                strict=strict_multi_evidence_binding,
            )
            if not member_materialized:
                result.update(
                    reason="declared_evidence_not_materialized",
                    risk_class=(
                        FactSafetyRiskClass.A_HARD_BLOCK.value
                        if strict_multi_evidence_binding
                        else FactSafetyRiskClass.D_REVIEW_REQUIRED.value
                    ),
                    hard_contract_failure=strict_multi_evidence_binding,
                    veto_fact=strict_multi_evidence_binding,
                    unmaterialized_evidence_id=declared_evidence_id,
                )
                return result
            member_claim = _member_claim_text(
                text,
                declared_anchor,
                declared_source_text,
                low_quality,
            )
            state_check = _bound_state_check(
                declared_anchor,
                member_claim,
                declared_source_text,
            )
            # A verbatim source-span materialization inherits the frozen
            # Anchor state.  Reclassifying an isolated source phrase can lose
            # its surrounding plan qualifier and create a false completion
            # upgrade even though the Writer copied no stronger assertion.
            if _support_text(member_claim) == _support_text(declared_source_text):
                state_check = {
                    **state_check,
                    "passed": True,
                    "candidate_state": state_check["source_state"],
                    "completion_upgrade": False,
                }
            member_mapping = validate_fact_sentences(
                _fact_probe(
                    section_id=section_id,
                    evidence_id=declared_evidence_id,
                    text=member_claim,
                ),
                {"fact_anchors": [declared_anchor]},
                {declared_evidence_id: declared_source},
            )
            source_anchor_mapping = validate_fact_sentences(
                _fact_probe(
                    section_id=section_id,
                    evidence_id=declared_evidence_id,
                    text=declared_source_text,
                ),
                {"fact_anchors": [declared_anchor]},
                {declared_evidence_id: declared_source},
            )
            source_supported_components = {
                str(item.get("component") or "")
                for item in source_anchor_mapping.get("claims") or []
                if item.get("passed")
            }
            member_core_failures = [
                item
                for item in member_mapping.get("unsupported_claims") or []
                if str(item.get("component") or "")
                in {"subject", "action", "object", "semantic_state"}
                and (
                    str(item.get("component") or "") == "semantic_state"
                    or str(item.get("component") or "")
                    in source_supported_components
                )
            ]
            member_hard_details = _unsupported_hard_details(
                member_claim,
                declared_source_text,
            )
            member_claim_norm = _norm(member_claim)
            own_subject = _responsible_subject(declared_anchor.get("canonical_subject") or "")
            own_object = _norm(declared_anchor.get("canonical_object") or "")
            own_fact_materialized = bool(
                member_materialized
                and (not own_subject or own_subject in member_claim_norm)
            )
            attributed_member_hard_details: list[dict[str, str]] = []
            for detail in member_hard_details:
                generic_completed_equivalence = bool(
                    str(detail.get("category") or "") == "strong_outcome"
                    and _norm(detail.get("value") or "")
                    in {"完成", "实现", "建成", "形成", "达到", "落实"}
                    and state_check["source_state"] == "COMPLETED"
                    and state_check["candidate_state"] == "COMPLETED"
                    and _source_entails_surface_form(
                        member_claim,
                        declared_source_text,
                    )
                )
                if generic_completed_equivalence:
                    continue
                coordinated_foreign_fact = False
                if own_fact_materialized:
                    for foreign_anchor, foreign_source in zip(
                        declared_anchors,
                        declared_sources,
                    ):
                        if foreign_anchor is None or foreign_anchor is declared_anchor:
                            continue
                        foreign_subject = _norm(
                            foreign_anchor.get("canonical_subject") or ""
                        )
                        foreign_source_text = str(
                            foreign_source.get("source_span")
                            or foreign_source.get("source_sentence")
                            or ""
                        )
                        foreign_materialized = _declared_member_materialized(
                            member_claim,
                            foreign_source_text,
                            materialization_texts,
                            foreign_anchor,
                            set(
                                foreign_source.get("low_quality_anchor_fields")
                                or []
                            ),
                            strict=False,
                        )
                        if (
                            foreign_materialized
                            and (
                                foreign_subject == own_subject
                                or (
                                    foreign_subject
                                    and foreign_subject in member_claim_norm
                                )
                            )
                            and _hard_detail_supported_by_source(
                                detail,
                                foreign_source_text,
                            )
                        ):
                            coordinated_foreign_fact = True
                            break
                if not coordinated_foreign_fact:
                    attributed_member_hard_details.append(detail)
            member_boundary_failures: list[dict[str, Any]] = []
            for detail in attributed_member_hard_details:
                category = str(detail.get("category") or "")
                if category == "number_or_time":
                    component = "number"
                elif category == "policy_or_project":
                    component = "project"
                elif category == "strong_outcome":
                    component = "result"
                elif category == "result_state_or_causality":
                    component = (
                        "causal"
                        if re.search(
                            r"(?:由于|因为|因|从而|进而|因此|因而|使得?|促使|带动)",
                            str(detail.get("value") or member_claim),
                        )
                        else "result"
                    )
                else:
                    continue
                member_boundary_failures.append({
                    "section_id": section_id,
                    "evidence_id": declared_evidence_id,
                    "sentence": member_claim,
                    "component": component,
                    "source_category": category,
                    "value": str(detail.get("value") or ""),
                    "mapping_status": "UNSUPPORTED",
                    "passed": False,
                })
            # These failures have already been attributed to this member by
            # _member_claim_text and checked only against this Anchor's own
            # source span. They must not be washed clean by combined_source.
            member_core_failures.extend(member_boundary_failures)
            foreign_subjects = {
                _responsible_subject(item.get("canonical_subject") or "")
                for item in declared_anchors
                if (
                    item is not None
                    and item is not declared_anchor
                    and _norm(item.get("canonical_object") or "") != own_object
                )
            } - {"", own_subject}
            foreign_objects = {
                _norm(item.get("canonical_object") or "")
                for item in declared_anchors
                if (
                    item is not None
                    and item is not declared_anchor
                    and _norm(item.get("canonical_subject") or "") != own_subject
                )
            } - {"", own_object}
            explicit_component_conflict = bool(
                (
                    own_subject
                    and own_subject in member_claim_norm
                    and own_object
                    and own_object not in member_claim_norm
                    and any(value in member_claim_norm for value in foreign_objects)
                )
                or (
                    own_object
                    and own_object in member_claim_norm
                    and own_subject
                    and own_subject not in member_claim_norm
                    and any(value in member_claim_norm for value in foreign_subjects)
                )
            )
            member_check = {
                "evidence_id": declared_evidence_id,
                "member_claim": member_claim,
                "semantic_state": {
                    "source": state_check["source_state"],
                    "candidate": state_check["candidate_state"],
                    "passed": state_check["passed"],
                },
                "core_component_failures": member_core_failures,
                "member_hard_details": member_hard_details,
                "attributed_member_hard_details": attributed_member_hard_details,
                "materialized": member_materialized,
                "explicit_component_conflict": explicit_component_conflict,
            }
            result.setdefault("member_rechecks", []).append(member_check)
            state_upgrade = bool(
                not state_check["passed"]
                and (state_check["candidate_state"] == "COMPLETED"
                     or state_check.get("lost_source_qualifier"))
                and state_check["source_state"] != "COMPLETED"
            )
            laundered_boundary_failures = [
                item
                for item in member_boundary_failures
                if not any(
                    str(combined_detail.get("category") or "")
                    == str(item.get("source_category") or "")
                    and _norm(combined_detail.get("value") or "")
                    == _norm(item.get("value") or "")
                    for combined_detail in hard_details
                )
            ]
            if member_boundary_failures:
                result.setdefault("per_anchor_boundary_failures", []).append({
                    "evidence_id": declared_evidence_id,
                    "component_failures": member_boundary_failures,
                })
            if state_upgrade or laundered_boundary_failures or explicit_component_conflict:
                result.update(
                    reason=(
                        "semantic_state_mismatch"
                        if state_upgrade
                        else (
                            "multi_evidence_anchor_fact_boundary_conflict"
                            if laundered_boundary_failures
                            else "multi_evidence_anchor_component_conflict"
                        )
                    ),
                    risk_class=FactSafetyRiskClass.A_HARD_BLOCK.value,
                    hard_contract_failure=True,
                    veto_fact=True,
                    failed_evidence_id=declared_evidence_id,
                    evidence_semantic_state=state_check["source_state"],
                    candidate_semantic_state=state_check["candidate_state"],
                    component_failures=(
                        laundered_boundary_failures or member_core_failures
                    ),
                )
                return result
        if hard_details:
            repair_input = _a1_safe_repair_input(
                text=text,
                evidence_ids=evidence_ids,
                source_text=combined_source,
                anchor_terms=list(result["anchor_terms"]),
                risk_type="FACT_STRENGTH_UPGRADE",
            )
            result.update(
                reason="unsupported_hard_fact",
                risk_class=FactSafetyRiskClass.A_HARD_BLOCK.value,
                hard_contract_failure=True,
                veto_fact=True,
            )
            per_anchor_failures = list(
                result.get("per_anchor_boundary_failures") or []
            )
            if per_anchor_failures:
                result["failed_evidence_id"] = str(
                    per_anchor_failures[0].get("evidence_id") or ""
                )
                result["component_failures"] = list(
                    per_anchor_failures[0].get("component_failures") or []
                )
            if repair_input is not None:
                result["safe_repair_input"] = repair_input
            return result
        result.update(
            passed=True,
            reason="multi_evidence_source_support",
            risk_class=FactSafetyRiskClass.C_PASS_WITH_EQUIVALENCE.value,
            equivalence_applied=True,
        )
        return result

    assert anchor is not None
    result["anchor_terms"] = [
        str(anchor.get(name) or "")
        for name in (
            "canonical_subject", "canonical_action", "canonical_object",
        )
        if anchor.get(name)
    ]
    if str(anchor.get("section_id") or "") != section_id:
        result.update(
            reason="section_mismatch",
            risk_class=FactSafetyRiskClass.A_HARD_BLOCK.value,
            hard_contract_failure=True,
        )
        return result
    if source.get("source_span_binding_valid") is False:
        result.update(
            reason="source_span_binding_invalid",
            risk_class=FactSafetyRiskClass.A_HARD_BLOCK.value,
            hard_contract_failure=True,
        )
        return result

    if not source_span:
        legacy_report = validate_fact_sentences(
            _fact_probe(
                section_id=section_id,
                evidence_id=evidence_id,
                text=text,
            ),
            {"fact_anchors": [anchor]},
        )
        if legacy_report["passed"]:
            result.update(
                passed=True,
                reason="anchor_only_legacy_support",
                risk_class=FactSafetyRiskClass.C_PASS_WITH_EQUIVALENCE.value,
            )
        else:
            result.update(
                reason="source_span_unavailable",
                risk_class=FactSafetyRiskClass.A_HARD_BLOCK.value,
                hard_contract_failure=True,
            )
        return result

    hard_details = _unsupported_hard_details(text, source_text)
    result["hard_details"] = hard_details
    if hard_details:
        hard_state_check = _bound_state_check(anchor, text, source_text)
        completion_state_upgrade = bool(
            not hard_state_check["passed"]
            and hard_state_check["candidate_state"] == "COMPLETED"
            and hard_state_check["source_state"] != "COMPLETED"
        )
        if completion_state_upgrade:
            result.update({
                "evidence_semantic_state": hard_state_check["source_state"],
                "candidate_semantic_state": hard_state_check[
                    "candidate_state"
                ],
                "state_evidence": hard_state_check["state_evidence"],
            })
        safe_clause_repair = (
            not _hard_details_require_a2(hard_details, text)
            and _has_isolated_supported_clause(text, source_text)
        )
        repair_input = (
            None
            if safe_clause_repair
            else _a1_safe_repair_input(
                text=text,
                evidence_ids=evidence_ids,
                source_text=source_text,
                anchor_terms=list(result["anchor_terms"]),
                risk_type="FACT_STRENGTH_UPGRADE",
            )
        )
        result.update(
            reason=(
                "semantic_state_mismatch"
                if completion_state_upgrade
                else "unsupported_hard_fact"
            ),
            risk_class=(
                FactSafetyRiskClass.B_SAFE_REPAIR.value
                if safe_clause_repair
                else FactSafetyRiskClass.A_HARD_BLOCK.value
            ),
            repair_failure_risk_class=FactSafetyRiskClass.A_HARD_BLOCK.value,
            hard_contract_failure=not safe_clause_repair,
            veto_fact=bool(
                completion_state_upgrade or strict_multi_evidence_binding
            ),
        )
        if repair_input is not None:
            result["safe_repair_input"] = repair_input
        return result

    mapping_report = validate_fact_sentences(
        _fact_probe(
            section_id=section_id,
            evidence_id=evidence_id,
            text=text,
        ),
        {"fact_anchors": [anchor]},
        {evidence_id: source},
    )
    failed_components = {
        str(item.get("component") or "")
        for item in mapping_report.get("unsupported_claims") or []
    }
    exact_surface = _support_text(text) == _support_text(source_text)
    equivalence_mapping_safe = (
        not exact_surface
        or mapping_report["passed"]
        or failed_components <= {"semantic_state"}
    )
    state_check = _bound_state_check(anchor, text, source_text)
    if (
        (state_check["passed"] or (
            state_check["source_state"] not in {"PLAN", "NOT_IMPLEMENTED"}
            and not state_check.get("completion_upgrade")
            and not state_check.get("lost_source_qualifier")
        ))
        and equivalence_mapping_safe
        and _safe_surface_equivalent(text, source_text)
    ):
        result.update(
            passed=True,
            reason="safe_surface_equivalence",
            risk_class=FactSafetyRiskClass.C_PASS_WITH_EQUIVALENCE.value,
            equivalence_applied=True,
        )
        return result

    result.update({
        "evidence_semantic_state": state_check["source_state"],
        "candidate_semantic_state": state_check["candidate_state"],
        "state_evidence": state_check["state_evidence"],
    })
    if not state_check["passed"]:
        status_upgrade = (
            state_check["candidate_state"] == "COMPLETED"
            and state_check["source_state"] != "COMPLETED"
        )
        repair_input = (
            _a1_safe_repair_input(
                text=text,
                evidence_ids=evidence_ids,
                source_text=source_text,
                anchor_terms=list(result["anchor_terms"]),
                risk_type="SEMANTIC_STATE_UPGRADE",
            )
            if status_upgrade
            else None
        )
        result.update(
            reason="semantic_state_mismatch",
            risk_class=(
                FactSafetyRiskClass.A_HARD_BLOCK.value
                if status_upgrade
                else FactSafetyRiskClass.D_REVIEW_REQUIRED.value
            ),
            hard_contract_failure=status_upgrade,
        )
        if repair_input is not None:
            result["safe_repair_input"] = repair_input
        return result
    low_quality = set(source.get("low_quality_anchor_fields") or [])
    if not _source_related(text, source_text, anchor, low_quality):
        result.update(
            reason="evidence_unrelated",
            risk_class=FactSafetyRiskClass.D_REVIEW_REQUIRED.value,
        )
        return result
    if not _source_entails_surface_form(text, source_span):
        result.update(
            reason="source_surface_not_entailed",
            risk_class=FactSafetyRiskClass.B_SAFE_REPAIR.value,
            repair_failure_risk_class=FactSafetyRiskClass.D_REVIEW_REQUIRED.value,
            source_projection_candidate=_deterministic_safe_fact(source),
            source_projection_prefix=(text[:prefix_end] if discourse and discourse.group(0) != "实践表明" else ""),
        )
        return result

    if not mapping_report["passed"]:
        result.update(
            reason="fact_component_unsupported",
            risk_class=FactSafetyRiskClass.B_SAFE_REPAIR.value,
            repair_failure_risk_class=FactSafetyRiskClass.D_REVIEW_REQUIRED.value,
            component_failures=list(mapping_report.get("unsupported_claims") or []),
        )
        return result
    result.update(
        passed=True,
        reason="source_span_support",
        risk_class=FactSafetyRiskClass.C_PASS_WITH_EQUIVALENCE.value,
    )
    return result


def apply_fact_safety_gate(
    draft: dict[str, Any],
    fact_anchor_pack: dict[str, Any],
    source_support: dict[str, dict[str, Any]],
    *,
    content_plan: dict[str, Any] | None = None,
    frozen_plan_integrity_check: Any | None = None,
    frozen_plan_sha256: str = "",
    validation_scope: str = "FULL_ARTICLE",
    paragraph_context: ParagraphValidationContext | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Run canonical Evidence Safety; default FULL_ARTICLE behavior is unchanged.

    PARAGRAPH_LOCAL requires frozen caller-owned identity/evidence scope. Only
    article-level structural obligations are excluded; classification, severity,
    semantic state, multi-evidence support and repair remain the same owner path.
    Only PARAGRAPH_LOCAL enables subset repair, composed with conservative
    body repair when needed and proved by the unchanged normal FACT validator
    before the existing scoped revalidation.
    Local acceptance is never a full article safety/structure verdict.
    """

    started = time.perf_counter()
    local_scope = _paragraph_validation_scope(validation_scope, paragraph_context, content_plan)
    if local_scope:
        local_structure = validate_structured_draft(
            draft, content_plan, fact_anchor_pack,
            validation_scope=validation_scope, paragraph_context=paragraph_context)
        local_analysis = validate_analysis_sentences(draft)
        if not local_structure["passed"] or not local_analysis["passed"]:
            raise ValueError("PARAGRAPH_LOCAL_VALIDATION_FAILED:" + json.dumps(
                {"structured": local_structure, "analysis": local_analysis}, ensure_ascii=False))
    anchors = {
        str(item.get("evidence_id") or ""): item
        for item in fact_anchor_pack.get("fact_anchors") or []
    }
    strict_multi_evidence_binding = bool(
        content_plan is not None
        and str(content_plan.get("planner_owner") or "").startswith(
            "work_summary_planner"
        )
    )

    def validate_one(
        candidate_draft: dict[str, Any],
        section_id: str,
        sentence_index: int,
    ) -> dict[str, Any]:
        result = _evaluate_evidence_safety_fact(
            candidate_draft,
            section_id,
            sentence_index,
            anchors=anchors,
            source_support=source_support,
            strict_multi_evidence_binding=strict_multi_evidence_binding,
        )
        return {**result, "section_id": section_id, "sentence_index": sentence_index}

    def validate_local_isolation(
        candidate_draft: dict[str, Any],
        section_id: str,
        sentence_index: int,
    ) -> dict[str, Any]:
        """Validate retained propositions against their declared source spans."""

        normal_result = validate_one(
            candidate_draft, section_id, sentence_index
        )
        if (normal_result.get("veto_fact") or (
            not normal_result.get("passed")
            and "evidence_semantic_state" in normal_result
            and normal_result.get("evidence_semantic_state") != normal_result.get("candidate_semantic_state")
        )):
            return normal_result
        candidate_section = next(
            (
                item
                for item in candidate_draft.get("sections") or []
                if str(item.get("section_id") or "") == section_id
            ),
            None,
        )
        sentences = (
            candidate_section.get("sentences") or []
            if candidate_section is not None
            else []
        )
        sentence = (
            sentences[sentence_index]
            if 0 <= sentence_index < len(sentences)
            else None
        )
        if sentence is None:
            return normal_result
        text = str(sentence.get("text") or "")
        evidence_ids = _declared_evidence_ids(sentence)
        declared_anchors = [anchors.get(value) for value in evidence_ids]
        declared_sources = [source_support.get(value) or {} for value in evidence_ids]
        if (
            not evidence_ids
            or any(item is None for item in declared_anchors)
            or any(
                str(item.get("section_id") or "") != section_id
                for item in declared_anchors
                if item is not None
            )
            or any(
                item.get("source_span_binding_valid") is False
                for item in declared_sources
            )
        ):
            return normal_result
        source_texts = [
            str(item.get("source_span") or item.get("source_sentence") or "")
            for item in declared_sources
        ]
        if any(not value for value in source_texts):
            return normal_result
        if len(evidence_ids) == 1 and normal_result.get("passed"):
            return normal_result
        combined_source = "。".join(source_texts)
        if _unsupported_hard_details(text, combined_source):
            return normal_result
        if len(evidence_ids) == 1:
            anchor = declared_anchors[0]
            assert anchor is not None
            low_quality = set(
                declared_sources[0].get("low_quality_anchor_fields") or []
            )
            supported = bool(
                _trusted_anchor_match_count(text, anchor, low_quality) >= 2
                and (
                    _safe_surface_equivalent(text, source_texts[0])
                    or _source_entails_surface_form(text, source_texts[0])
                )
            )
        else:
            isolation_alignment = [
                {"evidence_id": value,
                 "materialized": _declared_member_materialized(
                     text, support, source_texts, bound_anchor or {},
                     set(bound_source.get("low_quality_anchor_fields") or []), strict=True),
                 "subject": str((bound_anchor or {}).get("canonical_subject") or ""),
                 "semantic_state": str((bound_anchor or {}).get("semantic_state") or "")}
                for value, support, bound_anchor, bound_source in zip(
                    evidence_ids, source_texts, declared_anchors, declared_sources)
            ]
            unsupported_evidence_id = next(
                (
                    evidence_id
                    for evidence_id, source_text, anchor, source in zip(
                        evidence_ids,
                        source_texts,
                        declared_anchors,
                        declared_sources,
                    )
                    if not _declared_member_materialized(
                        text,
                        source_text,
                        source_texts,
                        anchor or {},
                        set(source.get("low_quality_anchor_fields") or []),
                        strict=True,
                    )
                ),
                "",
            )
            if unsupported_evidence_id:
                return {
                    "passed": False,
                    "section_id": section_id,
                    "sentence_index": sentence_index,
                    "reason": "declared_evidence_not_materialized",
                    "risk_class": FactSafetyRiskClass.D_REVIEW_REQUIRED.value,
                    "hard_contract_failure": False,
                    "unmaterialized_evidence_id": unsupported_evidence_id,
                    "evidence_alignment": isolation_alignment,
                }
            supported = True
        if not supported:
            return normal_result
        return {
            "passed": True,
            "section_id": section_id,
            "sentence_index": sentence_index,
            "evidence_ids": evidence_ids,
            "reason": "local_isolation_bound_span_support",
            "risk_class": FactSafetyRiskClass.C_PASS_WITH_EQUIVALENCE.value,
            "hard_contract_failure": False,
            "local_isolation_validation": True,
            "evidence_alignment": normal_result.get("evidence_alignment", []),
        }

    def revalidate_all(candidate_draft: dict[str, Any]) -> dict[str, Any]:
        failures: list[dict[str, Any]] = []
        review_failures: list[dict[str, Any]] = []
        structured_report = (
            validate_structured_draft(
                candidate_draft,
                content_plan,
                fact_anchor_pack,
                validation_scope=validation_scope,
                paragraph_context=paragraph_context,
            )
            if content_plan is not None
            else {
                "passed": True,
                "flow3_v33_status": "NOT_RUN_CONTENT_PLAN_NOT_PROVIDED",
                "hard_issue_count": 0,
            }
        )
        if not structured_report["passed"]:
            failures.append({
                "reason": "post_repair_structured_validation_failed",
                "hard_contract_failure": True,
                "structured_issues": list(
                    structured_report.get("hard_issues") or []
                ),
            })
        fact_mapping_report = validate_fact_sentences(
            candidate_draft,
            fact_anchor_pack,
            source_support,
        )
        for section in candidate_draft.get("sections") or []:
            section_id = str(section.get("section_id") or "")
            for sentence_index, sentence in enumerate(
                section.get("sentences") or []
            ):
                if sentence.get("sentence_type") != "FACT":
                    continue
                result = validate_one(
                    candidate_draft, section_id, sentence_index
                )
                if not result["passed"]:
                    if (
                        result.get("risk_class")
                        == FactSafetyRiskClass.D_REVIEW_REQUIRED.value
                        and not result.get("hard_contract_failure")
                    ):
                        review_failures.append(result)
                    else:
                        failures.append(result)
        return {
            "passed": not failures,
            "status": (
                "PASS"
                if not failures and not review_failures
                else "PASS_WITH_REVIEW_REQUIRED"
                if not failures
                else "FAIL"
            ),
            "structured_validation_status": str(
                structured_report.get("flow3_v33_status") or ""
            ),
            "fact_mapping_validation_status": str(
                fact_mapping_report.get("flow3_v33_status") or ""
            ),
            "source_fact_validation_status": (
                "PASS" if not failures else "FAIL"
            ),
            "review_required_count": len(review_failures),
            "failures": failures,
        }

    safety = run_evidence_safety(
        draft,
        fact_validator=validate_one,
        full_revalidator=revalidate_all,
        isolation_validator=validate_local_isolation,
        # The same declared text/evidence pair must survive both paragraph and
        # whole-article checks. Full structural/section obligations still run.
        allow_evidence_subset_narrowing=local_scope or strict_multi_evidence_binding,
        frozen_plan_integrity_check=frozen_plan_integrity_check,
        frozen_plan_sha256=frozen_plan_sha256,
    )
    evidence_safety_report = safety["report"]
    blocked_claims = list(safety["remaining_failures"])
    resolved_ids = {
        (str(item.get("section_id") or ""), item["sentence_index"])
        for item in safety["initial_failures"]
        if item.get("resolved")
    }
    claims: list[dict[str, Any]] = list(safety["passed_claims"])
    for item in safety["initial_failures"]:
        key = (
            str(item.get("section_id") or ""),
            item["sentence_index"],
        )
        claims.append({
            **item,
            "decision": (
                (
                    "PASS_WITH_LOCAL_ISOLATION"
                    if item.get("local_isolation_status")
                    == "ISOLATED_AND_REVALIDATED"
                    else "PASS_WITH_SAFE_REPAIR"
                )
                if key in resolved_ids
                else (
                    "HARD_BLOCK"
                    if item.get("risk_class")
                    == FactSafetyRiskClass.A_HARD_BLOCK.value
                    else "REVIEW_REQUIRED_DRAFT"
                )
            ),
        })

    validation_passed = evidence_safety_report["status"] in {
        DraftSafetyStatus.PASS.value,
        DraftSafetyStatus.PASS_WITH_LOCAL_REPAIR.value,
    }
    fact_safety_blocked = bool(evidence_safety_report["hard_block_count"])
    review_required = bool(blocked_claims)
    repair_count = int(
        evidence_safety_report["local_repair_success_count"]
    )
    isolation_count = int(
        evidence_safety_report.get("local_isolation_success_count") or 0
    )
    modification_count = repair_count + isolation_count
    revalidation_passed = bool(
        evidence_safety_report.get("revalidation_passed")
    )
    structure_preserved = bool(
        evidence_safety_report.get("structure_preserved")
    )
    fact_safety_status = (
        "FAIL_UNSUPPORTED_FACT" if fact_safety_blocked
        else "REVIEW_REQUIRED_DRAFT" if review_required
        else "PASS_WITH_LOCAL_ISOLATION" if isolation_count
        else "PASS_WITH_SAFE_REPAIR" if modification_count
        else "PASS"
    )
    delivery_status = (
        "SUCCESS_WITH_REVIEW" if review_required
        else "SUCCESS_WITH_REPAIR" if modification_count
        else "SUCCESS"
    )
    elapsed_ms = (time.perf_counter() - started) * 1000
    report = {
        # A semantic conflict in an existing body requires review delivery.
        # Keep the independent safety verdict and all A-class findings intact.
        "flow3_v33_status": (
            "REVIEW_REQUIRED_DRAFT"
            if blocked_claims and all(
                item.get("reason") == "semantic_state_mismatch" for item in blocked_claims
            ) else fact_safety_status
        ),
        "fact_safety_status": fact_safety_status,
        "delivery_status": delivery_status,
        "fact_safety_result": evidence_safety_report["fact_safety_result"],
        "evidence_safety_status": evidence_safety_report["status"],
        "safety_disposition": evidence_safety_report["status"],
        "decision": (
            "HARD_BLOCK" if fact_safety_blocked else fact_safety_status
        ),
        "fact_safety_decision": (
            "HARD_BLOCK" if fact_safety_blocked else fact_safety_status
        ),
        "passed": validation_passed,
        "delivery_allowed": True,
        "delivery_blocked": False,
        "fact_safety_blocked": fact_safety_blocked,
        "revalidation_passed": revalidation_passed,
        "structure_preserved": structure_preserved,
        "review_required": review_required,
        "supported_selected_count": (
            int(evidence_safety_report["checked_fact_count"])
            - len(blocked_claims)
        ),
        "unsupported_count": len(blocked_claims),
        "hard_block_count": int(evidence_safety_report["hard_block_count"]),
        "evidence_contract_hard_block_count": int(
            evidence_safety_report["hard_block_count"]
        ),
        "safe_repair_count": repair_count,
        "local_repair_count": repair_count,
        "local_isolation_count": isolation_count,
        "out_of_plan_count": 0,
        "claims": claims,
        "unsupported_claims": blocked_claims,
        "checker_modified_response": bool(modification_count),
        "checked_components": [
            "evidence_id", "section_id", "source_span", "source_span_binding",
            "core_support", "subject", "action", "object", "number_or_time",
            "organization", "organization_role", "person_or_role",
            "policy_or_project", "equipment", "mechanism", "place", "result",
            "absolute_statement", "semantic_state", "completion_state",
        ],
    }
    repair_report = {
        "status": (
            "PASS_WITH_LOCAL_ISOLATION" if isolation_count
            else "PASS_WITH_SAFE_REPAIR" if modification_count
            else "NOT_NEEDED"
        ),
        "fact_safety_result": evidence_safety_report["fact_safety_result"],
        "canonical_safety_result": "evidence_safety_report.json",
        "safe_repair_count": repair_count,
        "local_isolation_count": isolation_count,
        "local_repair_rounds": int(
            evidence_safety_report["local_repair_rounds"]
        ),
        "hard_block_count": int(evidence_safety_report["hard_block_count"]),
        "repairs": list(evidence_safety_report["repair_records"]),
        "isolations": list(
            evidence_safety_report.get("local_isolation_records") or []
        ),
        "fact_validation_ms": round(elapsed_ms, 3),
        "safe_repair_ms": round(elapsed_ms if modification_count else 0.0, 3),
        "model_call_count": 0,
        "evidence_safety_report": evidence_safety_report,
    }
    return safety["structured_draft"], report, repair_report


def validate_fact_sentences(
    draft: dict[str, Any], fact_anchor_pack: dict[str, Any],
    source_support: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    anchors = {
        str(item.get("evidence_id") or ""): item
        for item in fact_anchor_pack.get("fact_anchors") or []
    }
    claims: list[dict[str, Any]] = []
    for section in draft.get("sections") or []:
        for sentence in section.get("sentences") or []:
            if sentence.get("sentence_type") != "FACT":
                continue
            text = str(sentence.get("text") or "")
            evidence_ids = _declared_evidence_ids(sentence)
            evidence_id = evidence_ids[0] if len(evidence_ids) == 1 else ""
            if len(evidence_ids) > 1:
                member_source_texts = [
                    str((source_support or {}).get(value, {}).get("source_sentence")
                        or (source_support or {}).get(value, {}).get("source_span") or "")
                    for value in evidence_ids
                ]
                member_materialization_texts = [
                    str((source_support or {}).get(value, {}).get("source_span")
                        or (source_support or {}).get(value, {}).get("source_sentence") or "")
                    for value in evidence_ids
                ]
                combined_source = "。".join(member_source_texts)
                hard_details = _unsupported_hard_details(text, combined_source)
                claims.append({
                    "section_id": section.get("section_id"),
                    "evidence_ids": evidence_ids,
                    "sentence": text,
                    "component": "multi_evidence_hard_facts",
                    "value": hard_details,
                    "mapping_status": "SUPPORTED_SELECTED" if not hard_details else "UNSUPPORTED",
                    "passed": not hard_details,
                })
                for value, member_text in zip(
                    evidence_ids, member_materialization_texts,
                ):
                    declared_anchor = anchors.get(value, {})
                    declared_source = (source_support or {}).get(value) or {}
                    related = _declared_member_materialized(
                        text,
                        member_text,
                        member_materialization_texts,
                        declared_anchor,
                        set(declared_source.get("low_quality_anchor_fields") or []),
                    )
                    claims.append({
                        "section_id": section.get("section_id"),
                        "evidence_id": value,
                        "sentence": text,
                        "component": "multi_evidence_trace",
                        "value": value,
                        "mapping_status": "SUPPORTED_SELECTED" if related else "UNSUPPORTED",
                        "passed": bool(related),
                    })
                continue
            anchor = anchors.get(evidence_id, {})
            source = (source_support or {}).get(evidence_id) or {}
            source_text = str(
                source.get("source_span")
                or source.get("source_sentence")
                or ""
            )
            source_binding_valid = source.get("source_span_binding_valid") is not False
            source_surface_supported = bool(
                source_text
                and source_binding_valid
                and _source_entails_surface_form(text, source_text)
            )
            proposition, proposition_count = _proposition_for_declared_anchor(text, anchor)
            anchor_mapping = _anchor_sentence_mapping(text, anchor)
            action_supported = (
                _value_matches(proposition.get("action", ""), anchor.get("canonical_action", ""))
                or anchor_mapping["action"]
            )
            object_supported = (
                _object_value_matches(proposition.get("object", ""), anchor.get("canonical_object", ""))
                or anchor_mapping["object"]
            )
            subject_supported = (
                _value_matches(proposition.get("subject", ""), anchor.get("canonical_subject", ""))
                or anchor_mapping["subject"]
                or _implicit_anchor_subject_supported(
                    text,
                    anchor,
                    action_supported=action_supported,
                    object_supported=object_supported,
                    source_surface_supported=source_surface_supported,
                )
            )
            if (not _responsible_subject(anchor.get('canonical_subject') or '')
                    and source_surface_supported and action_supported and object_supported):
                subject_supported = True
            core_checks = {
                "subject": subject_supported,
                "action": action_supported,
                "object": object_supported,
            }
            unbound_anchor_payload = _unbound_anchor_payload(text, anchor)
            if (
                (
                    proposition_count != 1
                    and not (proposition_count == 0 and anchor_mapping["matched"])
                )
                or unbound_anchor_payload
            ):
                core_checks["single_core_fact"] = False
            component_values = {
                "subject": proposition.get("subject", "") or anchor.get("canonical_subject", ""),
                "action": proposition.get("action", "") or anchor.get("canonical_action", ""),
                "object": proposition.get("object", "") or anchor.get("canonical_object", ""),
                "single_core_fact": unbound_anchor_payload or proposition_count,
            }
            for category, passed in core_checks.items():
                claims.append({
                    "section_id": section.get("section_id"), "evidence_id": evidence_id,
                    "sentence": text, "component": category,
                    "value": component_values.get(category, proposition_count),
                    "mapping_status": "SUPPORTED_SELECTED" if passed else "UNSUPPORTED",
                    "passed": passed,
                })
            state_check = _bound_state_check(anchor, text, source_text)
            if state_check["contract_applies"]:
                claims.append({
                    "section_id": section.get("section_id"),
                    "evidence_id": evidence_id,
                    "sentence": text,
                    "component": "semantic_state",
                    "value": {
                        "evidence": state_check["source_state"],
                        "candidate": state_check["candidate_state"],
                    },
                    "mapping_status": (
                        "SUPPORTED_SELECTED" if state_check["passed"] else "UNSUPPORTED"
                    ),
                    "passed": state_check["passed"],
                })
            details = _fact_detail_values(text)
            for result_value in _anchor_values(anchor, "result"):
                if _anchor_component_position(text, [result_value]) >= 0 and result_value not in details["result"]:
                    details["result"].append(result_value)
            for category, values in details.items():
                allowed = _anchor_values(anchor, category)
                if category == "absolute_statement":
                    allowed = _anchor_values(anchor, "result")
                for value in values:
                    passed = (
                        any(_value_matches(value, candidate) for candidate in allowed)
                        # Source-first validation has already checked the
                        # complete surface, hard details, polarity, and
                        # Semantic State.  A parser-only detail fragment must
                        # not turn that supported surface into a false block.
                        or source_surface_supported
                    )
                    # Merely naming the anchor subject is not a new organization claim.
                    if category == "organization" and _organization_matches_anchor(value, anchor):
                        passed = True
                    claims.append({
                        "section_id": section.get("section_id"), "evidence_id": evidence_id,
                        "sentence": text, "component": category, "value": value,
                        "mapping_status": "SUPPORTED_SELECTED" if passed else "UNSUPPORTED",
                        "passed": passed,
                    })
    unsupported = [item for item in claims if not item["passed"]]
    supported = [item for item in claims if item["passed"]]
    return {
        "flow3_v33_status": "PASS" if not unsupported else "FAIL_UNSUPPORTED_FACT",
        "passed": not unsupported,
        "supported_selected_count": len(supported),
        "unsupported_count": len(unsupported),
        "out_of_plan_count": 0,
        "claims": claims,
        "unsupported_claims": unsupported,
        "checker_modified_response": False,
        "checked_components": [
            "subject", "action", "object", "time", "frequency", "quantity", "organization",
            "organization_role", "equipment", "mechanism", "place", "result", "absolute_statement",
            "semantic_state", "source_support",
        ],
    }


_ANALYSIS_LOCAL_FACT_PATTERNS = (
    r"形成(?:了)?风险评估报告", r"形成(?:了)?分级预案", r"责任落实到具体单位和岗位",
    r"(?:多部门|各部门)[^。；]{0,20}联合行动", r"调整(?:了)?方案",
    r"盘点(?:了)?(?:人员|装备|物资)", r"三快", r"日调度", r"视频监控", r"未发生(?:事故|案件)",
    r"(?:建立|召开|开展|设置|配备|实现|确保|避免|取得)(?:了|过|完成|到)",
)


def validate_analysis_sentences(draft: dict[str, Any]) -> dict[str, Any]:
    leaks: list[dict[str, Any]] = []
    checked = 0
    sections = list(draft.get("sections") or [])
    introduction = draft.get("introduction")
    if isinstance(introduction, str) and introduction.strip():
        sections.insert(
            0,
            {
                "section_id": "INTRO",
                "sentences": [{
                    "sentence_type": "ANALYSIS",
                    "evidence_id": "",
                    "text": introduction,
                }],
            },
        )
    for section in sections:
        for sentence in section.get("sentences") or []:
            if sentence.get("sentence_type") != "ANALYSIS":
                continue
            checked += 1
            text = str(sentence.get("text") or "")
            clauses = _sentences(text) or [text]
            for clause in clauses:
                advisory = any(marker in clause for marker in _GENERAL_MARKERS)
                explicit = bool(re.search(r"(?:已|已经|正在|本次|活动期间|活动当天|形成了|建立了|召开了|开展了|设置了|配备了|实现了|取得了|未发生)", clause))
                current_context = "目前" in clause
                local_subject = any(marker in clause for marker in _LOCAL_FACT_ANCHORS)
                occurred = any(action in clause for action in _OCCURRED_ACTIONS)
                details = _fact_detail_values(clause)
                concrete_details = any(details[key] for key in (
                    "time", "frequency", "quantity", "organization_role", "equipment", "mechanism", "place", "absolute_statement",
                ))
                completed_result = bool(details["result"]) and bool(re.search(
                    r"(?:已|已经|圆满|取得|实现了|完成了|形成了|未发生)",
                    clause,
                ))
                pattern = next((value for value in _ANALYSIS_LOCAL_FACT_PATTERNS if re.search(value, clause)), "")
                leaked = (
                    (explicit and (occurred or concrete_details or local_subject) and not advisory)
                    or (current_context and occurred and (concrete_details or local_subject) and not advisory)
                    or completed_result
                    or (local_subject and occurred and not advisory)
                    or (bool(pattern) and not advisory)
                )
                if leaked:
                    leaks.append({
                        "section_id": section.get("section_id"),
                        "sentence": text,
                        "matched_clause": clause,
                        "rule": "local_fact_leak",
                        "matched_pattern": pattern,
                    })
                    break
    return {
        "flow3_v33_status": "PASS" if not leaks else "FAIL_ANALYSIS_FACT_LEAK",
        "passed": not leaks,
        "analysis_sentence_count": checked,
        "analysis_fact_leak_count": len(leaks),
        "leaks": leaks,
        "checker_modified_response": False,
    }


def sanitize_introduction_analysis(draft: dict[str, Any]) -> dict[str, Any]:
    """Remove only unsafe introduction sentences using the existing ANALYSIS gate."""

    introduction = draft.get("introduction")
    if not isinstance(introduction, str) or not introduction.strip():
        return {
            "text": "",
            "safety_issue_count": 0,
            "removed_sentences": [],
        }
    retained: list[str] = []
    removed: list[str] = []
    for match in SENTENCE_RE.finditer(introduction):
        sentence = match.group(0).strip()
        if not sentence:
            continue
        probe = {
            "sections": [{
                "section_id": "INTRO",
                "sentences": [{
                    "sentence_type": "ANALYSIS",
                    "evidence_id": "",
                    "text": sentence,
                }],
            }],
        }
        if validate_analysis_sentences(probe).get("passed"):
            retained.append(sentence)
        else:
            removed.append(sentence)
    return {
        "text": "".join(retained),
        "safety_issue_count": len(removed),
        "removed_sentences": removed,
    }


def _render_fact_first_sentence_runs(
    sentences: list[dict[str, Any]],
) -> list[str]:
    """Join adjacent FACT atoms into one work paragraph without changing text."""

    lines: list[str] = []
    fact_run: list[str] = []
    for sentence in sentences:
        text = str(sentence.get("text") or "")
        if str(sentence.get("sentence_type") or "") == "FACT":
            if text:
                fact_run.append(text)
            continue
        if fact_run:
            lines.append("".join(fact_run))
            fact_run = []
        if text:
            lines.append(text)
    if fact_run:
        lines.append("".join(fact_run))
    return lines


def render_structured_draft(draft: dict[str, Any], content_plan: dict[str, Any]) -> str:
    """Remove protocol markers only; preserve every model text byte within lines."""
    by_id = {str(item.get("section_id") or ""): item for item in draft.get("sections") or []}
    blocks = [str(draft.get("title") or "")]
    introduction = draft.get("introduction")
    if isinstance(introduction, str) and introduction.strip():
        blocks.append(introduction.strip())
    for planned in content_plan.get("sections") or []:
        section = by_id[str(planned.get("section_id") or "")]
        lines = [str(section.get("heading") or "")]
        sentences = [
            item
            for item in section.get("sentences") or []
            if isinstance(item, dict)
        ]
        subsections = [
            item
            for item in section.get("subsections") or []
            if isinstance(item, dict)
        ]
        lines.extend(project_section_body_lines(
            sentences,
            subsections,
            join_adjacent_facts=(
                str(content_plan.get("planner_owner") or "")
                == "work_summary_planner_v1"
            ),
        ))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def render_source_paraphrase_audit_text(
    draft: dict[str, Any],
    content_plan: dict[str, Any],
) -> tuple[str, int]:
    """Render the prose governed by the paraphrase gate, excluding validated FACT text.

    FACT sentences remain governed by the FACT safety gate and may need to retain an
    original source span during deterministic safe repair.  Headings, introductions,
    and every ANALYSIS sentence remain in the paraphrase audit scope.
    """

    audit_draft = copy.deepcopy(draft)
    exempt_fact_sentence_count = 0
    for section in audit_draft.get("sections") or []:
        retained_sentences: list[dict[str, Any]] = []
        for sentence in section.get("sentences") or []:
            if str(sentence.get("sentence_type") or "") == "FACT":
                exempt_fact_sentence_count += 1
                continue
            retained_sentences.append(sentence)
        section["sentences"] = retained_sentences
    return (
        render_structured_draft(audit_draft, content_plan),
        exempt_fact_sentence_count,
    )


def save_rendered_draft(artifact_root: Path, rendered: str) -> str:
    return _write_text(Path(artifact_root) / "rendered_draft.txt", rendered)


def _sentences(text: str) -> list[str]:
    return [value.strip() for value in SENTENCE_RE.findall(text or "") if value.strip()]


def _norm(value: Any) -> str:
    return re.sub(r"[^\u3400-\u9fffA-Za-z0-9%％]", "", str(value or "")).replace("本县", "我县")


def _known_department_hits(sentence: str) -> list[str]:
    hits: list[str] = []
    enumerated_context = bool(re.search(r"(?:统筹|组织|联合|协调)[^。；]{0,90}[、，,]", sentence))
    for canonical, aliases in DEPARTMENT_ALIAS_GROUPS.items():
        for alias in aliases:
            if alias not in sentence:
                continue
            if alias in {"公安", "文旅", "应急", "卫健", "城管", "交通", "消防"}:
                direct = re.search(rf"{re.escape(alias)}(?:机关|部门|局|救援机构|救援队伍)", sentence)
                enumerated = enumerated_context and re.search(
                    rf"(?:^|[、，,]){re.escape(alias)}(?:[、，,等]|$)", sentence,
                )
                if not (direct or enumerated):
                    continue
            if canonical not in hits:
                hits.append(canonical)
            break
    return hits


def _specifics(sentence: str) -> dict[str, list[str]]:
    exempt_phrases = ("一套经验", "一项系统工程", "一个思路", "一种方式", "多个环节", "多方协同", "几点体会")
    exempt_spans = [match.span() for phrase in exempt_phrases for match in re.finditer(re.escape(phrase), sentence)]
    quantities = []
    for match in PRECISE_NUMBER_RE.finditer(sentence):
        if any(start <= match.start() and match.end() <= end for start, end in exempt_spans):
            continue
        quantities.append(match.group(0))
    for match in FREQUENCY_RE.finditer(sentence):
        if any(start <= match.start() and match.end() <= end for start, end in exempt_spans):
            continue
        quantities.append(match.group(0))
    quantities = list(dict.fromkeys(quantities))
    time_values = [
        value for value in quantities
        if re.search(r"年|月|日|秒|分钟|小时|天|每日|每天|每周|每月|季度|多次|多轮|多套|三个圈层|未发生一起", value)
    ]
    quantity_values = [value for value in quantities if value not in time_values]
    equipment = list(dict.fromkeys(EQUIPMENT_VALUE_RE.findall(sentence)))
    places = list(dict.fromkeys(_norm(value) for value in PLACE_VALUE_RE.findall(sentence)))
    mechanisms = [
        _norm(normalize_mechanism_value(value)) for value in MECHANISM_VALUE_RE.findall(sentence)
        if _norm(normalize_mechanism_value(value)) and "系统工程" not in value
    ]
    if re.search(r"红黄蓝[^，。；]{0,16}(?:管理|机制|制度)", sentence):
        mechanisms.append(_norm(re.search(r"红黄蓝[^，。；]{0,16}(?:管理|机制|制度)", sentence).group(0)))
    results = [
        term for term in RESULT_TERMS
        if term in sentence
        and not (term == "处置" and re.search(r"处置(?:能力|水平)", sentence))
    ]
    absolutes = [term for term in ABSOLUTE_TERMS if term in sentence]
    return {
        "quantity_or_date": quantity_values,
        "time_or_frequency": list(dict.fromkeys(time_values)),
        "organization_or_post": _known_department_hits(sentence),
        "equipment_or_system": equipment,
        "place_or_functional_area": places,
        "mechanism_or_workgroup": list(dict.fromkeys(mechanisms)),
        "result_statement": results,
        "absolute_statement": absolutes,
    }


_LOCAL_FACT_ANCHORS = (
    "我县", "本县", "我单位", "本单位", "我们", "县委", "县政府", "县分管领导", "相关责任主体",
    "活动期间", "活动当天",
    "县政法", "县公安", "县应急", "县卫健", "县城管", "县交通", "相关单位", "相关部门",
)
_OCCURRED_ACTIONS = (
    "牵头成立", "专题听取", "实地踏勘", "现场踏勘", "踏勘", "召开", "听取", "建立", "形成", "开展", "启动", "举办",
    "设置", "配备", "组织", "统筹", "实行", "划定", "投入", "安排", "提供", "完成", "发现",
    "处置", "实现", "未发生", "确保了", "取得成效", "取得", "有效提升", "巡查", "汇总", "负责", "下设",
)
_GENERAL_MARKERS = (
    "应当", "应该", "需要", "必须", "可以", "建议", "如果", "可能", "有条件时",
    "有助于", "有利于", "便于", "利于", "关键在于", "重在", "核心是",
    "下一步", "将进一步", "将持续", "要进一步", "需进一步", "应进一步",
    "持续推进", "应坚持", "要坚持", "持续用力", "贯通起来", "奠定基础",
    "是否", "越靠前", "越小", "才能", "难以",
)
_TRANSITION_MARKERS = ("总的来看", "综上", "由此可见", "以上是", "下一步", "实践启示", "概而言之")


def classify_fact_sentence(sentence: str, *, is_first: bool = False) -> str:
    text = sentence.strip()
    if not text:
        return "transition_or_summary"
    if text.startswith("#") or HEADING_RE.match(text):
        return "title_or_heading"
    if is_first and len(_han(text)) <= 40 and not re.search(r"[。！？；;]$", text):
        return "title_or_heading"
    specifics = _specifics(text)
    concrete_specifics = any(specifics[key] for key in (
        "quantity_or_date", "time_or_frequency", "organization_or_post", "equipment_or_system",
        "place_or_functional_area", "mechanism_or_workgroup",
    ))
    local_anchor = any(marker in text for marker in _LOCAL_FACT_ANCHORS)
    occurred = any(action in text for action in _OCCURRED_ACTIONS)
    advisory = any(marker in text for marker in _GENERAL_MARKERS)
    if any(marker in text for marker in _TRANSITION_MARKERS) and not (concrete_specifics and occurred):
        return "transition_or_summary"
    explicit_occurrence = bool(re.search(r"(?:已|已经|正在|目前|活动期间|活动当天|实际开展|已经形成)", text))
    if advisory and not explicit_occurrence:
        return "general_analysis"
    if local_anchor and occurred:
        return "concrete_local_fact"
    if concrete_specifics and occurred:
        return "concrete_local_fact"
    if specifics["result_statement"] and local_anchor:
        return "concrete_local_fact"
    if specifics["result_statement"] and re.search(r"(?:已|已经|圆满|取得|实现了|完成了|形成了|未发生)", text):
        return "concrete_local_fact"
    return "general_analysis"


def _item_values(item: dict[str, Any], category: str) -> list[str]:
    keys = {
        "quantity_or_date": ("quantities", "times"),
        "time_or_frequency": ("times", "quantities"),
        "organization_or_post": ("departments", "canonical_subject", "subject"),
        "equipment_or_system": ("equipment",),
        "place_or_functional_area": ("places", "canonical_object", "object"),
        "mechanism_or_workgroup": ("mechanisms", "canonical_object", "object"),
        "result_statement": ("results", "result", "canonical_action", "action"),
        "absolute_statement": ("results", "result", "qualifier"),
    }.get(category, ())
    values: list[str] = []
    for key in keys:
        raw = item.get(key)
        if isinstance(raw, list):
            values.extend(str(value) for value in raw)
        elif raw:
            values.append(str(raw))
    return values


def _aliases(item: dict[str, Any]) -> list[str]:
    value = item.get("allowed_aliases") or {}
    if isinstance(value, dict):
        return [str(alias) for aliases in value.values() for alias in (aliases or [])]
    return [str(alias) for alias in value] if isinstance(value, list) else []


def _value_matches(value: str, candidate: str) -> bool:
    left, right = _norm(value), _norm(candidate)
    if not left or not right:
        return False
    if left == right or (len(left) >= 4 and len(right) >= 4 and (left in right or right in left)):
        return True
    if len(left) >= 4 and len(right) >= 4:
        a = {left[index:index + 2] for index in range(len(left) - 1)}
        b = {right[index:index + 2] for index in range(len(right) - 1)}
        return len(a & b) / max(1, len(a | b)) >= 0.66
    return False


_SUBJECT_PATTERNS = tuple(sorted({
    "县委政法委", "县分管领导带队", "县分管领导", "政法部门", "公安机关", "应急管理部门",
    "卫生健康部门", "城市管理部门", "交通运输部门", "消防救援机构", "县委", "县政府",
    "我县", "本县", "本单位", "我单位", "我们", "相关责任主体", "相关单位", "相关部门",
    "上半年", "党组会议", "班子成员", "每次学习", "办公室",
    *(alias for aliases in DEPARTMENT_ALIAS_GROUPS.values() for alias in aliases),
}, key=len, reverse=True))


def _subject_from_text(text: str) -> str:
    normalized = _norm(text)
    known = next((value for value in _SUBJECT_PATTERNS if _norm(value) in normalized), "")
    if known:
        return known
    local_org = re.search(r"(?:县|市|区)[\u4e00-\u9fff]{1,10}(?:局|委|办|部门|机关|中心|大队|支队)", text)
    return local_org.group(0) if local_org else ""


def _action_in_clause(clause: str) -> tuple[int, str]:
    matches = [
        (match.start(), action) for action in _OCCURRED_ACTIONS
        for match in [re.search(re.escape(action), clause)] if match
        if not (
            (action == "负责" and clause[match.end():].startswith("人"))
            or (action == "处置" and clause[match.end():].startswith("能力"))
        )
    ]
    if not matches:
        return -1, ""
    specific_matches = [pair for pair in matches if pair[1] not in {"开展", "组织"}]
    if specific_matches:
        matches = specific_matches
    matches.sort(key=lambda pair: (pair[0], -len(pair[1])))
    return matches[0]


def _base_propositions(sentence: str) -> list[dict[str, str]]:
    facts: list[dict[str, str]] = []
    current_subject = ""
    for raw_clause in re.split(r"[，,；;]", sentence):
        clause = raw_clause.strip("。！？!? ")
        if not clause:
            continue
        position, action = _action_in_clause(clause)
        if not action:
            carried = _subject_from_text(clause)
            if carried:
                current_subject = carried
            continue
        before = clause[:position]
        mentioned_subject = _subject_from_text(before)
        if mentioned_subject:
            current_subject = mentioned_subject
        after = clause[position + len(action):]
        subject = _subject_from_text(before) or current_subject
        obj = _norm(after)
        if not obj:
            object_match = re.search(r"(?:对|围绕)(.+?)(?:进行|开展)?$", before)
            if object_match:
                obj = _norm(object_match.group(1))
        obj = re.sub(r"^(?:了|对|围绕|有关|相关)", "", obj)
        facts.append({
            "fact_item": _norm(clause),
            "subject": _norm(subject),
            "action": action,
            "object": obj[:30],
        })
    return facts


def _component_match(value: str, candidates: Iterable[str]) -> bool:
    normalized = _norm(value)
    if not normalized:
        return False
    return any(_value_matches(normalized, candidate) for candidate in candidates if candidate)


def _combo_matches(
    fact: dict[str, str], evidence: list[dict[str, Any]], *, result_only: bool = False,
    sentence: str = "",
) -> list[str]:
    matched: list[str] = []
    for item in evidence:
        if result_only and item.get("evidence_type") != "result":
            continue
        if sentence and _anchor_sentence_mapping(sentence, item)["matched"]:
            matched.append(str(item.get("evidence_id")))
            continue
        aliases = item.get("allowed_aliases") or {}
        subject_values = [item.get("canonical_subject"), item.get("subject")]
        action_values = [item.get("canonical_action"), item.get("action")]
        object_values = [item.get("canonical_object"), item.get("object"), item.get("result")]
        if isinstance(aliases, dict):
            subject_values.extend(aliases.get("subject") or [])
            action_values.extend(aliases.get("action") or [])
            object_values.extend(aliases.get("object") or [])
        object_value = fact.get("object", "")
        object_matches = (
            _component_match(object_value, object_values)
            if object_value else True
        )
        if (
            _component_match(fact.get("subject", ""), subject_values)
            and _component_match(fact.get("action", ""), action_values)
            and object_matches
        ):
            matched.append(str(item.get("evidence_id")))
    return matched


def _specific_matches(category: str, value: str, evidence: list[dict[str, Any]]) -> list[str]:
    exact = [
        str(item.get("evidence_id")) for item in evidence
        if any(_value_matches(value, candidate) for candidate in _item_values(item, category))
    ]
    if exact:
        return exact
    return [
        str(item.get("evidence_id")) for item in evidence
        if any(_value_matches(value, alias) for alias in _aliases(item))
    ]


def _status_item(
    *, sentence: str, fact: dict[str, Any], selected: list[dict[str, Any]], qualified: list[dict[str, Any]],
) -> dict[str, Any]:
    category = str(fact.get("claim_type") or "local_occurred_fact")
    value = str(fact.get("value") or fact.get("object") or fact.get("action") or "")
    is_result = category == "result_statement"
    if category == "local_occurred_fact" or is_result:
        selected_ids = _combo_matches(fact, selected, result_only=is_result, sentence=sentence)
        all_ids = _combo_matches(fact, qualified, result_only=is_result, sentence=sentence)
        attempted = [{
            "layer": "subject_action_object" if not is_result else "result_subject_action_object",
            "selected_matches": selected_ids, "all_qualified_matches": all_ids,
        }]
    else:
        selected_ids = _specific_matches(category, value, selected)
        all_ids = _specific_matches(category, value, qualified)
        attempted = [{"layer": "exact_or_alias", "selected_matches": selected_ids, "all_qualified_matches": all_ids}]
    if selected_ids:
        status, mapped, reason = "SUPPORTED_SELECTED", sorted(set(selected_ids)), ""
    elif all_ids:
        status, mapped, reason = "OUT_OF_PLAN", sorted(set(all_ids)), "matched_only_all_qualified_evidence"
    else:
        status, mapped, reason = "UNSUPPORTED", [], "no_qualified_evidence_match"
    return {
        "claim_type": category,
        "value": value,
        "sentence": sentence,
        "original_sentence": sentence,
        "fact_item": str(fact.get("fact_item") or value),
        "claim_classification": "concrete_local_fact",
        "subject": str(fact.get("subject") or ""),
        "action": str(fact.get("action") or ""),
        "object": str(fact.get("object") or ""),
        "normalized_subject": str(fact.get("subject") or ""),
        "normalized_action": str(fact.get("action") or ""),
        "normalized_object": str(fact.get("object") or ""),
        "specific_details": {category: value} if category != "local_occurred_fact" else {},
        "mapping_status": status,
        "mapped_evidence_ids": mapped,
        "attempted_matches": attempted,
        "rejection_reason": reason,
        "passed": status == "SUPPORTED_SELECTED",
    }


def _evidence_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        return list(value.get("evidence") or [])
    return list(value or [])


def check_unsupported_facts(
    raw_response: str,
    selected_evidence: Any,
    task: Any,
    *,
    all_qualified_evidence: Any | None = None,
) -> dict[str, Any]:
    """Split concrete sentences into independently mapped fact items."""
    del task
    selected = _evidence_list(selected_evidence)
    qualified = _evidence_list(all_qualified_evidence) if all_qualified_evidence is not None else selected
    claims: list[dict[str, Any]] = []
    classifications: list[dict[str, str]] = []
    counts: Counter[str] = Counter()
    mixed_sentence_count = 0
    for index, sentence in enumerate(_sentences(raw_response)):
        classification = classify_fact_sentence(sentence, is_first=index == 0)
        counts[classification] += 1
        classifications.append({"sentence": sentence, "claim_classification": classification})
        if classification == "general_analysis":
            claims.append({
                "claim_type": "general_analysis", "value": "", "sentence": sentence,
                "original_sentence": sentence, "fact_item": sentence,
                "claim_classification": "general_analysis", "subject": "", "action": "", "object": "",
                "normalized_subject": "", "normalized_action": "", "normalized_object": "",
                "specific_details": {}, "mapping_status": "GENERAL_ANALYSIS", "mapped_evidence_ids": [],
                "attempted_matches": [], "rejection_reason": "", "passed": True,
            })
            continue
        if classification != "concrete_local_fact":
            continue
        sentence_items: list[dict[str, Any]] = []
        propositions = _base_propositions(sentence)
        for proposition in propositions:
            result_assertion = proposition["action"] in {"实现", "完成", "形成", "未发生", "取得成效", "取得", "有效提升", "确保了"}
            fact = {**proposition, "claim_type": "result_statement" if result_assertion else "local_occurred_fact"}
            sentence_items.append(_status_item(
                sentence=sentence, fact=fact, selected=selected, qualified=qualified,
            ))
        anchor = propositions[0] if propositions else {"subject": "", "action": "", "object": "", "fact_item": ""}
        specifics = _specifics(sentence)
        for category, values in specifics.items():
            if category == "result_statement":
                continue
            for value in values:
                fact = {
                    **anchor, "claim_type": category, "value": value,
                    "fact_item": f"{category}:{value}",
                }
                sentence_items.append(_status_item(
                    sentence=sentence, fact=fact, selected=selected, qualified=qualified,
                ))
        if len(sentence_items) > 1:
            mixed_sentence_count += 1
        claims.extend(sentence_items)
    supported = [claim for claim in claims if claim["mapping_status"] == "SUPPORTED_SELECTED"]
    out_of_plan = [claim for claim in claims if claim["mapping_status"] == "OUT_OF_PLAN"]
    unsupported = [claim for claim in claims if claim["mapping_status"] == "UNSUPPORTED"]
    blocking = unsupported + out_of_plan
    if unsupported:
        status = "FAIL_UNSUPPORTED_FACT"
    elif out_of_plan:
        status = "FAIL_OUT_OF_PLAN_FACT"
    else:
        status = "PASS"
    return {
        "flow3_v3_status": status,
        "flow3_v32_status": status,
        "passed": not blocking,
        "concrete_local_fact_count": counts["concrete_local_fact"],
        "general_analysis_count": counts["general_analysis"],
        "title_or_heading_count": counts["title_or_heading"],
        "transition_or_summary_count": counts["transition_or_summary"],
        "successful_mapped_fact_count": len(supported),
        "supported_selected_count": len(supported),
        "out_of_plan_count": len(out_of_plan),
        "unsupported_count": len(unsupported),
        "unsupported_claim_count": len(unsupported),
        "blocking_fact_count": len(blocking),
        "claims": claims,
        "sentence_classifications": classifications,
        "unsupported_claims": blocking,
        "out_of_plan_claims": out_of_plan,
        "strictly_unsupported_claims": unsupported,
        "mixed_sentence_split_count": mixed_sentence_count,
        "whole_sentence_failure_count": 0,
        "all_successful_claims_have_mapped_evidence": all(
            claim["mapped_evidence_ids"] for claim in supported
        ),
        "all_failed_claims_have_rejection_reason": all(
            claim["rejection_reason"] for claim in blocking
        ),
        "checker_modified_response": False,
        "checked_categories": [
            "quantity_or_date", "time_or_frequency", "organization_or_post", "equipment_or_system",
            "place_or_functional_area", "mechanism_or_workgroup", "result_statement", "absolute_statement",
        ],
    }


def _han(value: str) -> str:
    return "".join(re.findall(r"[\u3400-\u9fff]", value or ""))


def _source_chunks(retrieval_payload: dict[str, Any]) -> list[dict[str, Any]]:
    chunks = []
    for index, item in enumerate(retrieval_payload.get("selected_chunks") or []):
        text = clean_source_residue(item.get("text") or item.get("chunk_text") or "")
        chunks.append({
            "index": index,
            "document_id": str(item.get("document_id") or item.get("doc_id") or ""),
            "chunk_id": str(item.get("chunk_id") or (item.get("metadata") or {}).get("chunk_id") or ""),
            "text": text,
            "han": _han(text),
        })
    return chunks


def _matching_blocks(left: str, right: str, minimum: int) -> list[str]:
    matcher = SequenceMatcher(None, left, right, autojunk=False)
    return [left[block.a:block.a + block.size] for block in matcher.get_matching_blocks() if block.size >= minimum]


def _ngrams(text: str, size: int) -> set[str]:
    return {text[index:index + size] for index in range(max(0, len(text) - size + 1))}


def _similarity(left: str, right: str) -> float:
    a = _ngrams(_han(left), 3)
    b = _ngrams(_han(right), 3)
    if not a or not b:
        return 0.0
    return len(a & b) / max(1, len(a | b))


def _headings(text: str) -> list[str]:
    headings = []
    for line in (text or "").splitlines():
        match = HEADING_RE.match(line.strip())
        if match:
            headings.append(match.group(1).strip())
    return headings


def _heading_core(value: str) -> set[str]:
    cleaned = re.sub(r"聚力|坚持|全面|着力|切实|构建|打造|提升|推动|工作|体系|机制", "", _han(value))
    return _ngrams(cleaned, 2)


def _source_headings(chunks: list[dict[str, Any]]) -> list[str]:
    result = []
    for chunk in chunks:
        first = next((line.strip() for line in chunk["text"].splitlines() if line.strip()), "")
        match = HEADING_RE.match(first)
        if match:
            result.append(match.group(1).strip())
    return result


def _paragraph_chunk_path(raw_response: str, chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    path = []
    paragraphs = [value.strip() for value in re.split(r"\n+", raw_response or "") if len(_han(value)) >= 20]
    for paragraph_index, paragraph in enumerate(paragraphs):
        scored = sorted(((_similarity(paragraph, chunk["text"]), chunk) for chunk in chunks), key=lambda pair: pair[0], reverse=True)
        if scored and scored[0][0] >= 0.12:
            score, chunk = scored[0]
            path.append({
                "paragraph_index": paragraph_index,
                "source_chunk_index": chunk["index"],
                "chunk_id": chunk["chunk_id"],
                "similarity": round(score, 4),
            })
    return path


def check_source_paraphrase_risk(
    raw_response: str,
    retrieval_payload: dict[str, Any],
    *,
    unsupported_fact_present: bool = False,
) -> dict[str, Any]:
    chunks = _source_chunks(retrieval_payload)
    draft_han = _han(raw_response)
    hard_reasons: list[dict[str, Any]] = []
    high_risk_reasons: list[dict[str, Any]] = []
    review_reasons: list[dict[str, Any]] = []
    exact_blocks: list[dict[str, Any]] = []

    for chunk in chunks:
        for block in _matching_blocks(draft_han, chunk["han"], 18):
            allowed = any(block in _han(expression) for expression in FIXED_EXPRESSION_ALLOWLIST)
            record = {"chunk_id": chunk["chunk_id"], "length": len(block), "text": block, "fixed_expression": allowed}
            exact_blocks.append(record)
            if len(block) >= 24 and not allowed:
                hard_reasons.append({"rule": "continuous_24_han", **record})
    distinct_long = {(item["chunk_id"], item["text"]) for item in exact_blocks if item["length"] >= 18 and not item["fixed_expression"]}
    if len(distinct_long) >= 2:
        hard_reasons.append({"rule": "two_or_more_long_reuses", "count": len(distinct_long)})

    source_phrase_pools: dict[int, set[str]] = {}
    for chunk in chunks:
        for size in (4, 6, 8):
            source_phrase_pools.setdefault(size, set()).update(_ngrams(chunk["han"], size))
    for paragraph_index, paragraph in enumerate(re.split(r"\n+", raw_response or "")):
        paragraph_han = _han(paragraph)
        hit_counts = {
            size: len({phrase for phrase in pool if phrase in paragraph_han})
            for size, pool in source_phrase_pools.items()
        }
        if hit_counts.get(4, 0) >= 10 or hit_counts.get(6, 0) >= 4 or hit_counts.get(8, 0) >= 2:
            high_risk_reasons.append({"rule": "stacked_source_phrases", "paragraph_index": paragraph_index, "hit_counts": hit_counts})

    draft_headings = _headings(raw_response)
    source_headings = _source_headings(chunks)
    heading_pairs = []
    if len(draft_headings) >= 2 and len(draft_headings) == len(source_headings):
        for draft_heading, source_heading in zip(draft_headings, source_headings):
            left, right = _heading_core(draft_heading), _heading_core(source_heading)
            overlap = len(left & right) / max(1, len(left | right))
            heading_pairs.append(round(overlap, 4))
        if heading_pairs and all(score >= 0.45 for score in heading_pairs):
            hard_reasons.append({"rule": "complete_heading_mapping", "scores": heading_pairs})

    path = _paragraph_chunk_path(raw_response, chunks)
    indexes = [item["source_chunk_index"] for item in path]
    monotonic = len(indexes) >= 3 and indexes == sorted(indexes) and len(set(indexes)) >= 3
    if monotonic and len(path) >= max(3, len([p for p in raw_response.splitlines() if len(_han(p)) >= 20]) // 2):
        hard_reasons.append({"rule": "source_sequence_overall_reproduction", "path": path})
    elif len(path) >= 3 and indexes == sorted(indexes):
        high_risk_reasons.append({"rule": "monotonic_evidence_path", "path": path})

    near_synonym_pairs = []
    source_sentences = [(chunk["chunk_id"], sentence) for chunk in chunks for sentence in _sentences(chunk["text"]) if len(_han(sentence)) >= 18]
    for draft_sentence in [value for value in _sentences(raw_response) if len(_han(value)) >= 18]:
        best = max(((_similarity(draft_sentence, source_sentence), chunk_id) for chunk_id, source_sentence in source_sentences), default=(0.0, ""))
        if best[0] >= 0.68:
            near_synonym_pairs.append({"chunk_id": best[1], "similarity": round(best[0], 4), "draft_sentence": draft_sentence})
    if len(near_synonym_pairs) >= 2:
        hard_reasons.append({"rule": "multiple_near_synonym_sentences", "matches": near_synonym_pairs})
    elif near_synonym_pairs:
        high_risk_reasons.append({"rule": "near_synonym_sentence", "matches": near_synonym_pairs})

    # Fact-audit failures are reported independently and never promote a
    # paraphrase finding.  hard_block requires standalone reuse evidence.
    del unsupported_fact_present

    if hard_reasons:
        level = "hard_block"
    elif high_risk_reasons:
        level = "high_risk"
    elif review_reasons:
        level = "review_required"
    else:
        level = "pass"
    return {
        "flow3_v3_status": "FAIL_SOURCE_PARAPHRASE" if level == "hard_block" else "PASS",
        "risk_level": level,
        "hard_block": level == "hard_block",
        "hard_reasons": hard_reasons,
        "high_risk_reasons": high_risk_reasons,
        "review_reasons": review_reasons,
        "character_matches": exact_blocks,
        "heading_mapping_scores": heading_pairs,
        "paragraph_source_path": path,
        "checker_modified_response": False,
    }
