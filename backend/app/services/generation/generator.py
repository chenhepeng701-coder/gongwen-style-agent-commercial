"""Article generation service.

request -> build query -> semantic retrieval -> prompt assembly -> LLM

Core product rule:
- no relevant material in the library -> no article generation
"""

from __future__ import annotations

import copy
import logging
import json
import math
import re
import ssl
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.config import get_settings
from app.models.generation import ArticleGenBatchResult, ArticleGenRequest, ArticleGenResult, OfficialDraftTask
from app.services.export.word_exporter import export_article_to_docx
from app.services.generation.atomic_evidence import (
    InvalidContentPlanError,
    InvalidEvidenceSelectionError,
    InvalidEvidencePackError,
    InvalidFactAnchorsError,
    build_atomic_evidence_pack,
    build_fact_anchor_pack,
    build_content_plan,
    derive_target_length_range,
    repair_content_plan,
    select_evidence_for_content_plan,
    validate_atomic_evidence_pack,
    validate_content_plan,
    validate_evidence_selection,
    validate_fact_anchor_pack,
)
from app.services.generation.fact_source_auditor import build_fact_source_audit, render_fact_source_audit_markdown
from app.services.generation.flow3_v3_trace import (
    AnalysisFactLeakError,
    OutOfPlanFactError,
    SourceParaphraseError,
    StructuredOutputError,
    UnsupportedFactError,
    apply_fact_safety_gate,
    build_fact_source_support,
    check_source_paraphrase_risk,
    check_unsupported_facts,
    initialize_trace,
    parse_and_validate_structured_draft,
    render_structured_draft,
    render_source_paraphrase_audit_text,
    save_check_reports,
    save_parsed_structured_draft,
    save_raw_response,
    save_rendered_draft,
    save_runtime_summary,
    save_v33_validation_reports,
    sanitize_introduction_analysis,
    validate_analysis_sentences,
    validate_fact_sentences,
)
from app.services.generation.new_draft_prompt_v3 import (
    build_atomic_new_draft_prompt,
    build_introduction_contract,
    build_structured_execution_repair_prompt,
    build_writer_plan_control_view,
    build_writer_plan_injection_report,
    build_writing_structure_overlay,
)
from app.services.generation.official_draft_rules import build_official_draft_rule_prompt_block
from app.services.generation.article_outline import (
    ArticleOutlineProjector,
    OutlineProjectionError,
)
from app.services.generation.dynamic_budget import (
    BudgetContractError,
    build_dynamic_budget,
)
from app.services.generation.planning_engine import FrozenArticlePlan, PlanningEngine
from app.services.generation.quality_test_draft import (
    sanitize_quality_test_draft,
)
from app.services.generation.rag_reference_splitter import build_rag_reference_block, split_rag_references
from app.services.generation.reference_structure import (
    empty_reference_structure,
    extract_reference_structure,
    validate_reference_structure,
)
from app.services.generation.section_rewrite_recovery import (
    execute_one_shot_section_rewrite,
)
from app.services.generation.section_expansion import (
    evaluate_introduction_quality,
    execute_one_shot_section_expansion,
    render_draft_for_length,
)
from app.services.generation.safe_fact_rewriter import (
    render_fact_rewrite_report_markdown,
    rewrite_high_risk_facts_locally,
)
from app.services.generation.structure_validator import render_structure_report_markdown, validate_structure
from app.services.generation.structured_execution_repair import (
    classify_structured_repair,
    run_structured_execution_repair,
)
from app.services.generation.section_writer import (
    LEGACY_WRITER, SECTION_WRITER_EXPERIMENTAL, SectionWriterContractError,
    select_writer_mode,
)
from app.services.generation.delivery_decision import (
    ArticleCandidate,
    CandidateRole,
    IssueRoute,
    LastKnownGoodArticle,
    deliver_artifacts_resilient,
    issue_item,
    qualified_decision,
    review_decision,
    route_issue,
)
from app.services.generation.targeted_quality_repairer import (
    render_targeted_repair_report_markdown,
    should_call_targeted_repair,
    targeted_quality_repair,
)
from app.services.generation.unsupported_claim_guard import (
    UnsupportedClaimGuard,
    derive_current_subject,
    render_unsupported_claim_guard_markdown,
)
from app.services.generation.writing_style_library import (
    apply_writing_style_guidance_to_content_plan,
    load_writing_style_guidance_pack,
)
from app.services.generation.writer_observation import observe_writer_density
from app.services.generation.work_summary_execution_policy import (
    TARGET_ORIENTED,
    build_work_summary_execution_policy,
    is_work_summary_content_plan,
    work_summary_length_report,
)
from app.services.generation.work_summary_planner import is_work_summary_task
from app.services.generation.work_summary_evidence_group import (
    build_work_summary_evidence_groups,
)
from app.services.generation.work_summary_quality_gate import (
    evaluate_work_summary_quality,
    should_run_work_summary_quality_gate,
    summarize_work_summary_quality_report,
)
from app.services.generation.work_summary_quality_recovery import (
    MAX_QUALITY_REPAIR_ROUNDS,
    execute_work_summary_quality_repair,
    fact_binding_snapshot,
)
from app.services.flow4 import (
    HandoffIntegrityError,
    build_flow4_input,
    create_flow3_output_envelope,
    deliver_flow4_artifacts,
    render_flow4_input,
)
from app.services.llm.factory import get_llm_provider
from app.utils.storage import atomic_write_text
from app.services.llm.openai_compatible_provider import LLMConnectionError
from app.services.retrieval.document_retriever import (
    build_retrieval_trace,
    retrieve_documents,
)
from app.services.retrieval.retriever import search_detailed
from app.services.retrieval.scene_classifier import classify_by_title_or_query
from app.utils.file_utils import sanitize_title_filename
from app.utils.runtime_paths import get_output_dir

logger = logging.getLogger(__name__)

_GENERATION_TIMING_KEYS = (
    "request_prepare_ms",
    "retrieval_ms",
    "atomic_evidence_ms",
    "reference_structure_ms",
    "planning_engine_ms",
    "content_plan_ms",
    "evidence_selection_ms",
    "fact_anchor_ms",
    "prompt_build_ms",
    "model_call_ms",
    "structured_parse_ms",
    "fact_validation_ms",
    "safe_repair_ms",
    "analysis_validation_ms",
    "flow4_render_ms",
    "artifact_save_ms",
    "total_ms",
)


def _new_generation_timings() -> dict[str, float]:
    return {key: 0.0 for key in _GENERATION_TIMING_KEYS}


def _record_elapsed(timings: dict[str, float], key: str, started: float) -> None:
    timings[key] = round(
        float(timings.get(key) or 0.0) + (time.perf_counter() - started) * 1000,
        3,
    )


def work_summary_writer_call_policy(task: Any) -> dict[str, Any]:
    """Return the production normal-path model-call contract."""

    enabled = is_work_summary_task(task)
    return {
        "single_writer_call": enabled,
        "section_expansion_allowed": not enabled,
        "section_rewrite_allowed": not enabled,
        "normal_model_call_count": 1 if enabled else None,
    }


FLOW3_DELIVERY_ALLOWED_STATUSES = frozenset({
    "PASS",
    "PASS_WITH_SAFE_REPAIR",
    "PASS_WITH_LOCAL_ISOLATION",
    "REVIEW_REQUIRED_DRAFT",
    "FAIL_UNSUPPORTED_FACT",
    "FAIL_ANALYSIS_FACT_LEAK",
})


def evaluate_flow3_delivery_contract(
    flow3_status: str,
    fact_report: dict[str, Any],
    *,
    article_renderable: bool = True,
) -> dict[str, Any]:
    """Rate Fact Safety separately from delivery of an existing article."""

    normalized_status = str(flow3_status or "").strip().upper()
    status_allowed = normalized_status in FLOW3_DELIVERY_ALLOWED_STATUSES
    fact_safety_status = str(
        fact_report.get("fact_safety_status") or normalized_status
    ).strip().upper()
    fact_safety_passed = fact_report.get("passed") is True
    hard_block_count = int(fact_report.get("hard_block_count") or 0)
    review_required = bool(
        not fact_safety_passed
        or hard_block_count
        or int(fact_report.get("unsupported_count") or 0)
        or int(fact_report.get("out_of_plan_count") or 0)
        or fact_report.get("review_required")
        or fact_report.get("fact_safety_blocked") is True
        or fact_report.get("delivery_blocked") is True
        or fact_report.get("revalidation_passed") is False
        or fact_report.get("structure_preserved") is False
        or fact_safety_status in {
            "REVIEW_REQUIRED_DRAFT",
            "FAIL_UNSUPPORTED_FACT",
            "FAIL_ANALYSIS_FACT_LEAK",
        }
    )
    repaired = normalized_status in {
        "PASS_WITH_SAFE_REPAIR",
        "PASS_WITH_LOCAL_ISOLATION",
    }
    if not article_renderable or not status_allowed:
        delivery_status = "FAILED"
    elif review_required:
        delivery_status = "SUCCESS_WITH_REVIEW"
    elif repaired:
        delivery_status = "SUCCESS_WITH_REPAIR"
    else:
        delivery_status = "SUCCESS"
    passed = delivery_status != "FAILED"
    return {
        "passed": passed,
        "flow3_status": normalized_status,
        "fact_safety_status": fact_safety_status,
        "delivery_status": delivery_status,
        "status_allowed": status_allowed,
        "fact_safety_passed": fact_safety_passed,
        "article_renderable": article_renderable,
        "review_required": review_required,
        "hard_block_count": hard_block_count,
        "revalidation_passed": fact_report.get("revalidation_passed") is True,
        "structure_preserved": fact_report.get("structure_preserved") is True,
    }


class _LLMRetryCounter(logging.Handler):
    """Observe provider retry warnings without changing provider behavior or payloads."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.count = 0

    def emit(self, record: logging.LogRecord) -> None:
        if str(record.msg).startswith("LLM_CONNECTION_INTERRUPTED"):
            self.count += 1

MAX_RAG_CHUNK_CHARS = 1000
MAX_RAG_CONTEXT_CHARS = 6000
MAX_CARD_ITEMS = 6
MAX_GENERATION_ATTEMPTS = 2
COPY_OVERLAP_THRESHOLD = 28
DEFAULT_SCENE = "未分类"
NO_MATERIAL_TITLE = "资料库素材不足，无法生成"
NO_MATERIAL_BODY = "未在资料库中检索到与当前主题高度相关的内容。请先上传或补充相关主题资料，再重新生成。"
DEFAULT_ARTICLE_MODE = "official_draft"
DEFAULT_EXPORT_FORMAT = "docx"
OFFICIAL_DRAFT_ONLY_MESSAGE = "当前版本仅支持 official_draft 公文初稿生成；旧生成模式已停用，请使用公文初稿模式重新生成。"
FACT_SAFE_GATE_ENABLED = True
FACT_SAFE_LOCAL_REWRITE_ENABLED = True
FACT_SAFE_LLM_REPAIR_ENABLED = True
FACT_SAFE_MAX_LLM_REPAIR_CALLS = 1
FACT_SAFE_MAX_REPAIR_ROUNDS = 1
MIN_TARGET_WORDS = 500
MAX_TARGET_WORDS = 5000
DEFAULT_TARGET_WORDS = 1200
OFFICIAL_DRAFT_FINAL_VALIDATE_ERROR = (
    "\u751f\u6210\u7ed3\u679c\u672a\u901a\u8fc7\u6700\u7ec8\u8d28\u91cf\u6821\u9a8c\uff1a"
    "\u8f93\u51fa\u4ecd\u5305\u542b\u683c\u5f0f\u6b8b\u7559\u3001\u5199\u6cd5\u5206\u6790\u53e3\u543b"
    "\u6216\u65e0\u6548\u5360\u4f4d\u5185\u5bb9\uff0c\u8bf7\u91cd\u65b0\u751f\u6210\u3002"
)
OFFICIAL_DRAFT_FENCE_LANGUAGE_LINES = {"text", "markdown", "md", "json", "python", "py"}
OFFICIAL_DRAFT_ANALYSIS_PHRASES = (
    "\u8fd9\u7c7b\u6750\u6599\u5e94\u8be5",
    "\u53ef\u4ee5\u4ece\u4ee5\u4e0b\u51e0\u4e2a\u65b9\u9762\u5199",
    "\u5199\u4f5c\u65f6\u5e94\u6ce8\u610f",
    "\u5efa\u8bae\u4ece\u4ee5\u4e0b\u65b9\u9762\u5c55\u5f00",
    "\u672c\u6587\u53ef\u4ece",
)
OFFICIAL_DRAFT_APPRECIATION_PHRASES = (
    "\u8fd9\u7bc7\u6750\u6599\u7684\u4eae\u70b9",
    "\u53ef\u501f\u9274\u4e4b\u5904",
    "\u5199\u6cd5\u4e0a",
    "\u7ed3\u6784\u4e0a",
    "\u8bed\u8a00\u4e0a",
)
OFFICIAL_DRAFT_PROMOTION_PHRASES = (
    "\u56de\u590d\u5173\u952e\u8bcd",
    "\u9886\u53d6\u8d44\u6599",
    "\u626b\u7801",
    "\u52a0\u5fae\u4fe1",
    "\u79c1\u4fe1\u83b7\u53d6",
)
OFFICIAL_DRAFT_SOURCE_PHRASES = (
    "\u53c2\u8003\u8d44\u6599\uff1a",
    "\u6765\u6e90\uff1a",
    "\u8d44\u6599\u6765\u6e90\uff1a",
    "\u6839\u636e\u8d44\u6599\u5e93",
)
OFFICIAL_DRAFT_PLACEHOLDER_PHRASES = (
    "\u3010\u5f85\u8865\u5145\u3011",
    "[\u5f85\u8865\u5145]",
    "XXX",
    "xx\u5e74xx\u6708",
    "\u67d0\u67d0\u5355\u4f4d",
    "\u67d0\u67d0\u9886\u5bfc",
)
OFFICIAL_DRAFT_PLACEHOLDER_PATTERNS = (
    r"\u3010\u5f85\u8865\u5145[^\u3011]*\u3011",
    r"\[\u5f85\u8865\u5145[^\]]*\]",
)
OFFICIAL_DRAFT_INTERNAL_PLACEHOLDER_PHRASES = (
    "\u5f85\u786e\u8ba4",
    "\u5f85\u8865\u5145",
    "TODO",
    "\u5185\u90e8\u5ba1\u6838\u63d0\u793a",
    "\u9700\u8981\u6838\u5b9e",
    "\u8bf7\u63d0\u4f9b\u8d44\u6599",
    "\u8bf7\u63d0\u4f9b\u672c\u5355\u4f4d",
)
OFFICIAL_DRAFT_INTERNAL_PLACEHOLDER_PATTERNS = (
    r"\u3010\s*\u5f85(?:\u786e\u8ba4|\u8865\u5145)[^\u3011]*\u3011",
    r"\[\s*TODO[^\]]*\]",
    r"(?i)\bTODO\b",
)
OFFICIAL_DRAFT_TEACHING_TONE_PHRASES = (
    "这类材料应该",
    "可以从以下几个方面写",
    "写作时要注意",
    "本文可围绕",
    "写法上",
    "可借鉴",
    "这类材料怎么写",
    "如何写好这类材料",
)
PARTY_LECTURE_DOCUMENT_TYPE = "党课讲稿"
PARTY_LECTURE_REPORT_TITLE_PHRASES = (
    "关于",
    "工作推进情况",
    "推进情况",
    "情况报告",
    "工作汇报",
    "报告",
)


def _needs_human_review_for_hybrid_gate(
    *,
    final_high_count: int,
    final_structure_valid: bool,
    targeted_repair_failed: bool,
    llm_repair_calls: int,
    final_content_empty_or_too_short: bool,
    final_output_written: bool,
    final_validate_failed: bool = False,
    sidecar_critical_write_failed: bool = False,
) -> bool:
    return bool(
        final_high_count > 1
        or not final_structure_valid
        or targeted_repair_failed
        or llm_repair_calls > FACT_SAFE_MAX_LLM_REPAIR_CALLS
        or final_content_empty_or_too_short
        or not final_output_written
        or final_validate_failed
        or sidecar_critical_write_failed
    )
PARTY_LECTURE_REPORT_BODY_PHRASES = (
    "现将",
    "报告如下",
    "现将有关情况报告如下",
    "现将推进情况报告如下",
    "现将工作推进情况报告如下",
    "特此报告",
    "工作进度与主要做法",
    "存在的主要问题",
    "下一步工作打算",
    "讲稿起草",
    "起草工作",
    "组织实施",
    "会后反馈",
    "推进情况",
    "工作推进情况",
    "整改推进情况",
    "党课活动总结",
    "专项工作",
    "所查摆的问题已完成整改",
    "下发限期整改通知书",
)
PARTY_LECTURE_TEACHING_TONE_PHRASES = (
    "同志们：",
    "同志们:",
    "今天，我们围绕",
    "今天我们围绕",
    "下面，我围绕",
    "下面我围绕",
)
FALLBACK_LATEST_CANDIDATE_VALIDATION_FAILED = "LATEST_CANDIDATE_VALIDATION_FAILED"
FALLBACK_LATEST_CANDIDATE_TRANSPORT_FAILED = "LATEST_CANDIDATE_TRANSPORT_FAILED"
FALLBACK_FACT_REPAIR_FAILED = "FACT_REPAIR_FAILED"
FALLBACK_DOCUMENT_TYPE_REFINEMENT_FAILED = "DOCUMENT_TYPE_REFINEMENT_FAILED"
FALLBACK_USER_FACT_RETENTION_FAILED = "USER_FACT_RETENTION_FAILED"
FACT_SAFETY_REWRITE_CONFLICT = "FACT_SAFETY_REWRITE_CONFLICT"
USER_FACT_RETENTION_ERROR_MESSAGE = "\u751f\u6210\u7ed3\u679c\u672a\u80fd\u5b8c\u6574\u4fdd\u7559\u7528\u6237\u63d0\u4f9b\u7684\u5173\u952e\u4e8b\u5b9e"
FACT_SAFETY_REWRITE_CONFLICT_MESSAGE = "\u65e0\u4f9d\u636e\u9648\u8ff0\u5b89\u5168\u6539\u5199\u4e0e\u7528\u6237\u660e\u786e\u4e8b\u5b9e\u4fdd\u7559\u51b2\u7a81"

STAGE_INITIAL_GENERATION = "INITIAL_GENERATION"
STAGE_REFINEMENT = "REFINEMENT"
STAGE_LOCAL_REWRITE = "LOCAL_REWRITE"
STAGE_FACT_REPAIR = "FACT_REPAIR"

SYSTEM_PROMPT = """你是一名有三十年工作经验的体制内笔杆子，熟悉党政机关、企事业单位各类公文写作，精通讲话稿、汇报材料、专题报告、经验交流、工作总结、通知、请示、函、纪要、调研报告、方案等文种。
要求：
1. 只输出最终文章，不输出解释、分析过程或提示词内容。
2. 必须基于给定资料库内容写作，不得绕开资料自由发挥，不得编造资料里没有的数据和事实。
3. 文章要像资深笔杆子写出的材料初稿，有机关气、不飘不空，不要写成 AI 总结稿。
4. 可以借鉴资料中的结构、场景、动作和表达节奏，但不能连续大段照抄，也不能把几篇资料简单拼接。
5. 句式要有长短变化，保留材料感、现场感和信息密度。
6. 注意公文规范：文种正确、格式得体、层级清晰、用语准确，符合党政机关公文处理惯例。"""

ARTICLE_TYPE_HINTS: dict[str, str] = {
    "讲话": "讲话稿",
    "发言": "讲话稿",
    "汇报": "汇报材料",
    "报告": "汇报材料",
    "专题": "专题材料",
    "专报": "专题材料",
    "经验": "经验交流",
    "交流": "经验交流",
    "解析": "公文解析",
    "公文": "公文解析",
}

OPENING_HINTS: list[tuple[str, tuple[str, ...]]] = [
    ("痛点切入", ("最怕", "怕的是", "难的是", "写不好", "不好写")),
    ("问题切入", ("问题", "短板", "隐患", "薄弱", "症结")),
    ("场景切入", ("现场", "场所", "作业", "管网", "运输", "企业")),
    ("任务切入", ("围绕", "聚焦", "紧盯", "针对", "抓住")),
    ("背景切入", ("当前", "今年", "近期", "随着", "面对")),
]

LEAD_OPENING_STRATEGIES = (
    "批语痛点型：围绕材料被退回、领导批内容空、站位不够、不具体等真实痛点起笔。",
    "熟词失效型：围绕“提高认识、压实责任、强化整改”等熟词很多但内容不实的问题起笔。",
    "任务场景型：围绕接到某类材料任务后，不是没内容，而是不知道怎么写实起笔。",
    "误区反转型：先点常见误区，再翻出真正的问题卡点。",
    "对比落差型：先摆普通写法和更好写法之间的落差，再说明差距出在哪。",
    "现场切入型：直接从资料库里的具体场景切入，例如城镇燃气、有限空间作业、粉尘涉爆、两客一危等。",
)

SCENE_HINTS = (
    "城镇燃气",
    "老旧管网",
    "餐饮场所用气不规范",
    "有限空间作业",
    "粉尘涉爆",
    "两客一危",
    "三超一疲劳",
    "重大事故隐患",
    "重点领域",
    "道路运输",
    "危化",
    "燃气",
    "管网",
    "餐饮场所",
    "有限空间",
    "粉尘涉爆",
    "运输",
    "车间",
    "园区",
    "矿山",
)

PROBLEM_HINTS = (
    "不规范",
    "薄弱",
    "隐患",
    "短板",
    "漏洞",
    "反复",
    "不到位",
    "风险点",
    "问题",
    "梗阻",
)

ACTION_HINTS = (
    "动态清零",
    "机械化换人",
    "自动化减人",
    "隐患举报专线",
    "整改销号",
    "逐项过筛",
    "逐项销号",
    "排查",
    "整治",
    "整改",
    "销号",
    "闭环",
    "挂牌督办",
    "压茬推进",
    "专班",
    "复盘",
    "督导",
)

GENERIC_CLICHE_PHRASES = (
    "提高认识",
    "压实责任",
    "强化落实",
    "守牢底线",
    "高度重视",
    "狠抓落实",
    "持续发力",
    "进一步",
)

LEAD_HANDOFF_OPTIONS = (
    "后续提纲和几组可参考表达，已同步整理在文末说明中，可按需查看。",
    "相关材料的后半部分和常用表达，已放在文末说明里做了整理。",
    "这类材料后续还有几组实用写法，文末说明中已同步整理。",
)

LEAD_BANNED_PHRASES = (
    "营销导流话术",
    "付费转化话术",
    "私下领取话术",
    "资料售卖话术",
)

LEAD_TITLE_ANALYSIS_MARKERS = (
    "怎么写",
    "别再",
    "别只会",
    "不空",
    "写实",
    "模板",
    "拆解",
    "写法",
    "现场感",
    "空表态",
)

FULL_ARTICLE_FORMAL_TITLE_MARKERS = (
    "讲话",
    "讲话稿",
    "发言",
    "表态发言",
    "汇报",
    "报告",
    "总结",
    "材料",
    "对照检查",
    "整改报告",
    "工作部署",
)

FULL_ARTICLE_BROAD_TITLE_TEMPLATES = (
    ("巡察整改", "关于巡察整改工作推进情况的报告"),
    ("安全生产", "在安全生产工作会议上的讲话"),
    ("党建工作", "关于党建工作推进情况的汇报"),
)

FULL_ARTICLE_TITLE_POLLUTION_MARKERS = (
    "参考资料：",
    "参考资料:",
    "怎么写不",
    "怎么就写不",
    "怎么写才不",
    "这样写才叫",
    "别只写",
    "领导真正看什么",
)

LEAD_FORMAL_TITLE_MARKERS = (
    "上的讲话",
    "讲话稿",
    "表态发言",
    "专题汇报",
    "工作总结",
    "对照检查材料",
    "民主生活会发言",
    "组织生活会发言",
)

LEAD_FORMAL_OPENINGS = (
    "同志们：",
    "各位领导、同志们：",
    "各位领导同志们：",
    "各位同志：",
    "今天召开这个会议",
)

LEAD_FORMAL_ENDINGS = (
    "我就讲这些，谢谢大家。",
    "我就讲这些，谢谢大家",
    "以上发言，不当之处请批评指正。",
    "以上发言，不当之处请批评指正",
)

LEAD_FORMAL_BODY_MARKERS = (
    "下面，我讲几点意见",
    "下面我讲几点意见",
    "我代表",
    "请大家认真抓好落实",
    "本人对照检查如下",
)

LEAD_ANALYSIS_BODY_MARKERS = (
    "这类材料",
    "怎么写",
    "写空",
    "写实",
    "写法",
    "材料里",
    "材料中",
    "表达",
    "段落",
    "提纲",
)

LEAD_FAKE_STORY_MARKERS = (
    "上周有个朋友问我",
    "有个写材料的朋友发来消息",
    "这种事我太熟悉了",
    "我之前见过一个单位",
    "上次我去某地调研",
    "我带着",
    "有个领导跟我说",
    "我认识一个办公室主任",
)

LEAD_PLACEHOLDER_MARKERS = (
    "XX",
    "某村",
    "某单位",
    "某地",
    "某企业",
    "某部门",
    "202X",
)

LEAD_PLACEHOLDER_PATTERNS = (
    r"(^|[^A-Za-z])X($|[^A-Za-z])",
    r"XX%",
    r"X\s*项",
    r"X\s*家",
)

LEAD_FLUFFY_METAPHORS = (
    "黏合剂",
    "第一颗扣子",
    "焊在一起",
    "四梁八柱",
    "压舱石",
    "定盘星",
    "组合拳",
    "一盘棋",
    "牵一发动全身",
    "打通最后一公里",
)

LEAD_REWRITE_DEMO_MARKERS = (
    "不要只写",
    "可以改成",
    "这句话的问题在于",
    "补一层动作后",
    "原句看着没错",
    "但它少了对象、场景和结果",
)

LEAD_STRUCTURE_OPENINGS = (
    "退稿批语切入",
    "任务压力切入",
    "熟词失效切入",
    "现场问题切入",
    "领导关注点切入",
    "基层执行难点切入",
    "材料修改痛点切入",
    "普通写法失效切入",
    "真实场景切入",
    "资料中某个具体动作切入",
)

LEAD_STRUCTURE_BODIES = (
    "问题递进",
    "场景展开",
    "修改前后对比",
    "从领导视角拆解",
    "从基层执行拆解",
    "从材料结构拆解",
    "从关键词误用拆解",
    "从真实工作流程拆解",
    "从任务落实链条拆解",
    "从“为什么写空”反推“怎么写实”",
)

LEAD_STRUCTURE_MATERIALS = (
    "具体场景带出",
    "关键词拆解",
    "工作动作拆解",
    "错误写法点评",
    "可用表达嵌入",
    "案例化说明",
    "清单化呈现",
    "段落改写示范",
    "场景转表达",
    "资料句式转公文表达",
)

LEAD_STRUCTURE_PACING = (
    "先吐槽再给方法",
    "先举例再总结",
    "先指出误区再拆解",
    "先给判断再铺素材",
    "先讲问题再给可用句子",
    "先讲场景再带出写法",
    "先讲一个常见退稿点，再展开解决办法",
    "先抛出材料痛点，再给资料库里的实料",
)

LEAD_STRUCTURE_ENDINGS = (
    "后续提纲承接",
    "常用表达承接",
    "完整框架承接",
    "更多场景承接",
    "模板整理承接",
    "相关写法承接",
    "后半部分内容承接",
)


class NoRelevantMaterialError(ValueError):
    """Raised when the library does not contain relevant material for generation."""

    def __init__(self, topic: str, rag_query: str, retrieved_count: int, top_score: float, reason: str):
        self.topic = topic
        self.rag_query = rag_query
        self.retrieved_count = retrieved_count
        self.top_score = round(top_score, 4)
        self.reason = reason
        message = (
            f"{NO_MATERIAL_TITLE}\n"
            f"未在资料库中检索到与“{topic}”高度相关的内容，请先补充相关主题资料后再重新生成。"
        )
        super().__init__(message)

    def to_response_detail(self) -> dict[str, Any]:
        return {
            "error_type": "MATERIAL_INSUFFICIENT",
            "legacy_error_type": "NO_RELEVANT_MATERIAL",
            "title": NO_MATERIAL_TITLE,
            "message": str(self),
            "rag_query": self.rag_query,
            "retrieved_count": self.retrieved_count,
            "top_score": self.top_score,
            "reason": self.reason,
            "fact_safety_blocked": False,
        }


class RetrievalInfraError(RuntimeError):
    """Raised when retrieval or embedding infrastructure is unavailable."""

    def __init__(self, rag_query: str, cause: Exception):
        self.rag_query = rag_query
        self.cause = cause
        super().__init__("资料检索服务暂时不可用，请稍后重试")

    def to_response_detail(self) -> dict[str, Any]:
        return {
            "error_type": "RETRIEVAL_INFRA_ERROR",
            "title": "资料检索服务暂时不可用",
            "message": str(self),
            "rag_query": self.rag_query,
            "fact_safety_blocked": False,
        }


@dataclass
class _ValidCandidate:
    title: str
    content: str
    self_check: dict[str, Any]
    stage: str
    attempt_number: int


@dataclass(frozen=True)
class UserFactAnchor:
    anchor_id: str
    source_field: str
    source_snippet: str
    quantity_text: str
    quantity_normalized: str
    unit: str
    subject_terms: tuple[str, ...]
    fact_type: str


@dataclass(frozen=True)
class UserFactCheckResult:
    passed: bool
    total_count: int
    passed_count: int
    missing_anchor_ids: tuple[str, ...]
    retained_anchor_ids: tuple[str, ...] = ()
    reassigned_anchor_ids: tuple[str, ...] = ()


class UserFactRetentionError(ValueError):
    """Raised when generated text drops user-supplied mechanical quantity facts."""

    def __init__(self, check_result: UserFactCheckResult | None = None):
        self.check_result = check_result
        super().__init__(USER_FACT_RETENTION_ERROR_MESSAGE)

    def to_response_detail(self) -> dict[str, Any]:
        return {
            "error_type": FALLBACK_USER_FACT_RETENTION_FAILED,
            "message": USER_FACT_RETENTION_ERROR_MESSAGE,
            "fact_safety_blocked": True,
        }


class FactSafetyRewriteConflictError(ValueError):
    """Raised when G3C safety rewrite conflicts with G3B user fact locks."""

    def __init__(self, check_result: UserFactCheckResult | None = None, rewrite_result: dict[str, Any] | None = None):
        self.check_result = check_result
        self.rewrite_result = rewrite_result or {}
        super().__init__(FACT_SAFETY_REWRITE_CONFLICT_MESSAGE)

    def to_response_detail(self) -> dict[str, Any]:
        check_payload = _user_fact_check_to_payload(self.check_result)
        return {
            "error_type": FACT_SAFETY_REWRITE_CONFLICT,
            "message": FACT_SAFETY_REWRITE_CONFLICT_MESSAGE,
            "fact_safety_blocked": True,
            "unverified_claim_count": int(self.rewrite_result.get("unsupported_claim_count", 0) or 0),
            "unverified_claim_types": list(self.rewrite_result.get("unsupported_claim_types", []) or []),
            "user_fact_check": check_payload,
            "rewrite_used": bool(self.rewrite_result.get("rewrite_used")),
        }


def _user_fact_check_to_payload(check_result: UserFactCheckResult | None) -> dict[str, Any]:
    if check_result is None:
        return {
            "passed": True,
            "expected_anchor_ids": [],
            "retained_anchor_ids": [],
            "missing_anchor_ids": [],
            "reassigned_anchor_ids": [],
            "expected_anchor_count": 0,
            "retained_anchor_count": 0,
        }
    retained = list(check_result.retained_anchor_ids)
    return {
        "passed": check_result.passed,
        "expected_anchor_ids": retained + list(check_result.missing_anchor_ids),
        "retained_anchor_ids": retained,
        "missing_anchor_ids": list(check_result.missing_anchor_ids),
        "reassigned_anchor_ids": list(check_result.reassigned_anchor_ids),
        "expected_anchor_count": check_result.total_count,
        "retained_anchor_count": check_result.passed_count,
    }


def _user_fact_check_payload(
    check_result: UserFactCheckResult | None,
    anchors: list[UserFactAnchor] | None = None,
) -> dict[str, Any]:
    payload = _user_fact_check_to_payload(check_result)
    if anchors is not None:
        payload["expected_anchor_ids"] = [anchor.anchor_id for anchor in anchors]
    return payload


USER_FACT_SOURCE_FIELDS = ("title", "writing_purpose")
USER_FACT_SUBJECT_STOPWORDS = (
    "\u5171",
    "\u5df2",
    "\u7d2f\u8ba1",
    "\u5f00\u5c55",
    "\u7ec4\u7ec7",
    "\u4e3e\u529e",
    "\u53ec\u5f00",
    "\u5b8c\u6210",
    "\u5f62\u6210",
    "\u8fdb\u884c",
    "\u5b9e\u65bd",
    "\u53c2\u52a0",
    "\u4e13\u9898",
    "\u5de5\u4f5c\u62a5\u544a",
    "\u5de5\u4f5c\u603b\u7ed3",
    "\u62a5\u544a",
    "\u603b\u7ed3",
)
USER_FACT_BOUNDARY_RE = re.compile(r"[\s,，。；;、：:\n\r\t]+")
USER_FACT_QUANTITY_RE = re.compile(
    r"(?P<date>\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*[日号]?)"
    r"|(?P<percentage>\d+(?:\.\d+)?\s*%)"
    r"|(?P<amount>\d+(?:\.\d+)?\s*(?:万|亿)?\s*元)"
    r"|(?P<duration>\d+(?:\.\d+)?\s*(?:小时|天|日|个月|月|年))"
    r"|(?P<person>\d+(?:\.\d+)?\s*(?:余)?\s*人)"
    r"|(?P<count>\d+(?:\.\d+)?\s*(?:余)?\s*(?:次|场|条|件|项|户))"
)


def _dedupe_preserve(items: list[str], limit: int | None = None) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for item in items:
        cleaned = re.sub(r"\s+", " ", (item or "").strip())
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        deduped.append(cleaned)
        if limit and len(deduped) >= limit:
            break
    return deduped


def _normalize_user_fact_number(text: str) -> str:
    compact = re.sub(r"\s+", "", text or "")
    number_match = re.search(r"\d+(?:\.\d+)?", compact)
    if not number_match:
        return compact
    number = number_match.group(0)
    return number[:-2] if number.endswith(".0") else number


def _normalize_user_fact_unit(text: str, fact_type: str) -> str:
    compact = re.sub(r"\s+", "", text or "")
    if fact_type == "PERCENTAGE":
        return "%"
    if fact_type == "DATE":
        return "\u65e5\u671f"
    for unit in ("\u4ebf\u5143", "\u4e07\u5143", "\u5143", "\u4f59\u4eba", "\u4eba", "\u5c0f\u65f6", "\u4e2a\u6708", "\u5929", "\u65e5", "\u6708", "\u5e74", "\u4f59\u6b21", "\u6b21", "\u573a", "\u6761", "\u4ef6", "\u9879", "\u6237"):
        if compact.endswith(unit):
            return unit.replace("\u4f59", "")
    return compact


def _fact_type_from_quantity_match(match: re.Match[str]) -> str:
    if match.group("date"):
        return "DATE"
    if match.group("percentage"):
        return "PERCENTAGE"
    if match.group("amount"):
        return "AMOUNT"
    if match.group("duration"):
        return "DURATION"
    if match.group("person"):
        return "PERSON_COUNT"
    return "COUNT"


def _nearest_boundary_left(text: str, index: int) -> int:
    boundaries = [m.end() for m in USER_FACT_BOUNDARY_RE.finditer(text[:index])]
    return boundaries[-1] if boundaries else 0


def _nearest_boundary_right(text: str, index: int) -> int:
    match = USER_FACT_BOUNDARY_RE.search(text[index:])
    return index + match.start() if match else len(text)


def _neighbor_quantity_left(text: str, index: int) -> int:
    end = 0
    for match in USER_FACT_QUANTITY_RE.finditer(text[:index]):
        end = max(end, match.end())
    return end


def _neighbor_quantity_right(text: str, index: int) -> int:
    match = USER_FACT_QUANTITY_RE.search(text[index:])
    return index + match.start() if match else len(text)


def _clean_user_fact_subject(text: str, *, prefer_tail: bool) -> str:
    cleaned = re.sub(r"\d+(?:\.\d+)?", "", text or "")
    cleaned = re.sub(r"[\s,，。；;、：:（）()\[\]【】\"'“”‘’]+", "", cleaned)
    for stopword in USER_FACT_SUBJECT_STOPWORDS:
        cleaned = cleaned.replace(stopword, "")
    parts = re.findall(r"[\u4e00-\u9fffA-Za-z]{2,}", cleaned)
    if not parts:
        return ""
    selected = parts[-1] if prefer_tail else parts[0]
    return selected[-12:] if prefer_tail else selected[:12]


def _expand_user_fact_subject_terms(subject: str) -> tuple[str, ...]:
    terms = [subject]
    if "\u7406\u8bba\u5b66\u4e60" in subject or "\u96c6\u4e2d\u5b66\u4e60" in subject:
        terms.extend(["\u7406\u8bba\u5b66\u4e60", "\u96c6\u4e2d\u5b66\u4e60"])
    if "\u4ea4\u6d41\u7814\u8ba8" in subject:
        terms.extend(["\u4ea4\u6d41\u7814\u8ba8", "\u7814\u8ba8"])
    return tuple(_dedupe_preserve([term for term in terms if len(term) >= 2], limit=4))


def _extract_subject_terms_for_quantity(snippet: str, start: int, end: int) -> tuple[str, ...]:
    left_start = max(_nearest_boundary_left(snippet, start), _neighbor_quantity_left(snippet, start))
    right_end = min(_nearest_boundary_right(snippet, end), _neighbor_quantity_right(snippet, end))
    left_text = snippet[left_start:start]
    right_text = snippet[end:right_end]
    left_subject = _clean_user_fact_subject(left_text, prefer_tail=True)
    right_subject = _clean_user_fact_subject(right_text, prefer_tail=False)

    subject = left_subject or right_subject
    if left_subject and right_subject and len(left_subject) <= 1 and len(right_subject) > len(left_subject):
        subject = right_subject
    return _expand_user_fact_subject_terms(subject)


def extract_user_fact_anchors(task: OfficialDraftTask) -> list[UserFactAnchor]:
    anchors: list[UserFactAnchor] = []
    for field_name in USER_FACT_SOURCE_FIELDS:
        field_value = str(getattr(task, field_name, "") or "").strip()
        if not field_value:
            continue
        for match in USER_FACT_QUANTITY_RE.finditer(field_value):
            quantity_text = re.sub(r"\s+", "", match.group(0))
            fact_type = _fact_type_from_quantity_match(match)
            subject_terms = _extract_subject_terms_for_quantity(field_value, match.start(), match.end())
            if not subject_terms:
                continue
            source_snippet = field_value[max(0, match.start() - 24): min(len(field_value), match.end() + 24)]
            anchor_index = len(anchors) + 1
            anchors.append(
                UserFactAnchor(
                    anchor_id=f"UF{anchor_index:03d}",
                    source_field=field_name,
                    source_snippet=source_snippet,
                    quantity_text=quantity_text,
                    quantity_normalized=_normalize_user_fact_number(quantity_text),
                    unit=_normalize_user_fact_unit(quantity_text, fact_type),
                    subject_terms=subject_terms,
                    fact_type=fact_type,
                )
            )
    return anchors


def _candidate_quantity_matches_anchor(match_text: str, anchor: UserFactAnchor) -> bool:
    fact_type = _fact_type_from_quantity_match(USER_FACT_QUANTITY_RE.match(match_text) or re.match(r"$^", ""))
    return (
        _normalize_user_fact_number(match_text) == anchor.quantity_normalized
        and _normalize_user_fact_unit(match_text, fact_type) == anchor.unit
    )


def _candidate_match_has_anchor_subject(text: str, match: re.Match[str], anchor: UserFactAnchor) -> bool:
    left_start = max(_nearest_boundary_left(text, match.start()), _neighbor_quantity_left(text, match.start()))
    right_end = min(_nearest_boundary_right(text, match.end()), _neighbor_quantity_right(text, match.end()))
    window = text[left_start:right_end]
    return any(term and term in window for term in anchor.subject_terms)


def validate_user_fact_anchors(text: str, anchors: list[UserFactAnchor]) -> UserFactCheckResult:
    if not anchors:
        return UserFactCheckResult(True, 0, 0, ())

    passed_ids: set[str] = set()
    reassigned_ids: set[str] = set()
    candidate_text = text or ""
    for match in USER_FACT_QUANTITY_RE.finditer(candidate_text):
        match_text = re.sub(r"\s+", "", match.group(0))
        window_by_anchor = {
            anchor.anchor_id: _candidate_match_has_anchor_subject(candidate_text, match, anchor)
            for anchor in anchors
        }
        for anchor in anchors:
            if anchor.anchor_id in passed_ids:
                continue
            if not _candidate_quantity_matches_anchor(match_text, anchor):
                continue
            if window_by_anchor[anchor.anchor_id]:
                passed_ids.add(anchor.anchor_id)
            elif any(
                other.anchor_id != anchor.anchor_id
                and window_by_anchor.get(other.anchor_id)
                for other in anchors
            ):
                reassigned_ids.add(anchor.anchor_id)

    missing = tuple(anchor.anchor_id for anchor in anchors if anchor.anchor_id not in passed_ids)
    retained = tuple(anchor.anchor_id for anchor in anchors if anchor.anchor_id in passed_ids)
    return UserFactCheckResult(
        passed=not missing,
        total_count=len(anchors),
        passed_count=len(anchors) - len(missing),
        missing_anchor_ids=missing,
        retained_anchor_ids=retained,
        reassigned_anchor_ids=tuple(anchor.anchor_id for anchor in anchors if anchor.anchor_id in reassigned_ids),
    )


def _render_user_fact_lock_prompt_block(anchors: list[UserFactAnchor]) -> str:
    if not anchors:
        return ""
    lines = [
        "\u3010\u7528\u6237\u660e\u786e\u6570\u91cf\u4e8b\u5b9e\u9501\u5b9a\u3011",
        "\u4ee5\u4e0b\u4e8b\u5b9e\u6765\u81ea\u7528\u6237\u8f93\u5165\uff0c\u751f\u6210\u3001\u7cbe\u4fee\u548c\u4e8b\u5b9e\u4fee\u590d\u65f6\u5fc5\u987b\u540c\u65f6\u4fdd\u7559\u4e8b\u9879\u4e0e\u6570\u91cf\uff0c\u4e0d\u5f97\u4e92\u6362\u3001\u6539\u7ed1\u6216\u5220\u9664\uff1a",
    ]
    for anchor in anchors:
        subject = anchor.subject_terms[0] if anchor.subject_terms else ""
        lines.append(f"- {anchor.anchor_id}: {subject} {anchor.quantity_text}")
    return "\n".join(lines) + "\n\n"


def _render_unsupported_claim_prompt_block() -> str:
    return (
        "【事实来源安全约束】\n"
        "1. 通用材料、模板材料、匿名材料和其他单位材料只能作为结构、表达和工作方向参考，不得改写为当前单位已经完成、已经存在或已经建立的事实。\n"
        "2. 用户没有直接提供本地数据、事件、人员、问题或制度依据时，不得自行补写数字、事件、人员、现存问题、已建制度、责任名单或上报链路。\n"
        "3. 检索材料只有明确出现当前目标主体，且事项、数字、频次、时限、责任规则一致时，才可作为当前主体事实依据；主题相似不能视为直接依据。\n"
        "4. 无依据的本地已完成工作、本地现存问题、本地已建制度，必须改为泛化风险表达或规范性要求，不得虚构“结合我局实际”“经排查发现”等依据，不得输出待补充、待确认等内部提示。\n"
        "5. 无依据的具体频次、时限、责任部门、责任人、等级划分和上报要求，必须去除具体值并改为可结合现行制度明确的自然表述，不得保留24小时、48小时、每季度、每半年、每两年等刚性规则，不得输出待补充、待确认等内部提示。\n\n"
    )


def _iter_exception_chain(exc: Exception) -> list[BaseException]:
    chain: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        chain.append(current)
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return chain


def _is_retrieval_infra_exception(exc: Exception) -> bool:
    infra_type_names = (
        "ConnectError",
        "ConnectionError",
        "ConnectionTimeout",
        "ConnectTimeout",
        "ReadTimeout",
        "TimeoutException",
        "RequestError",
        "SSLError",
        "SSLZeroReturnError",
        "SSLWantReadError",
        "SSLWantWriteError",
    )
    infra_markers = (
        "ssl",
        "connecterror",
        "connection error",
        "connection refused",
        "connection reset",
        "timeout",
        "timed out",
        "read timeout",
        "connect timeout",
        "embedding service unavailable",
        "provider network",
        "network unreachable",
        "temporary failure",
    )
    for item in _iter_exception_chain(exc):
        if isinstance(item, (TimeoutError, ssl.SSLError)):
            return True
        type_name = type(item).__name__
        if type_name in infra_type_names:
            return True
        message = str(item).lower()
        if any(marker in message for marker in infra_markers):
            return True
    return False


def _is_generation_transport_exception(exc: Exception) -> bool:
    if isinstance(exc, LLMConnectionError):
        return True
    return _is_retrieval_infra_exception(exc)


def _build_party_lecture_final_gate_error(self_check: dict[str, Any]) -> str:
    reasons: list[str] = []
    if self_check.get("party_lecture_title_report_hits"):
        reasons.append("标题仍为报告体")
    if self_check.get("party_lecture_body_report_hits"):
        reasons.append("正文仍为报告体")
    if not self_check.get("party_lecture_teaching_tone_hits"):
        reasons.append("缺少授课口吻")
    if not reasons:
        reasons = [str(item) for item in self_check.get("failures", []) if item]
    reason_text = "；".join(_dedupe_preserve(reasons)) or "文种校验未通过"
    return f"生成结果未通过“党课讲稿”文种校验：{reason_text}。请重试，或调整主题后再生成。"


def _parse_generated_article(text: str) -> tuple[str, str]:
    text = (text or "").strip()
    if not text:
        return "", ""

    lines = text.splitlines()
    title = ""
    body_start = 0

    for index, line in enumerate(lines):
        candidate = line.strip()
        if candidate:
            title = candidate.lstrip("#").strip()
            body_start = index + 1
            break

    while body_start < len(lines) and not lines[body_start].strip():
        body_start += 1

    body = "\n".join(lines[body_start:]).strip()
    return title, body


def clean_official_draft_text(text: str) -> str:
    """Remove transport/Markdown wrappers without changing official-draft wording."""
    cleaned_lines: list[str] = []
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line:
            cleaned_lines.append("")
            continue

        lower_line = line.lower()
        if line.startswith("```") or lower_line in OFFICIAL_DRAFT_FENCE_LANGUAGE_LINES:
            continue

        line = re.sub(r"^\s{0,3}#{1,6}\s*", "", line)
        line = re.sub(r"\*\*(.*?)\*\*", r"\1", line)
        line = line.replace("**", "")
        cleaned_lines.append(line.strip())

    return "\n".join(cleaned_lines).strip()


def _looks_like_json_payload(text: str) -> bool:
    stripped = (text or "").strip()
    if not stripped:
        return False
    if not (
        (stripped.startswith("{") and stripped.endswith("}"))
        or (stripped.startswith("[") and stripped.endswith("]"))
    ):
        return False
    if re.search(r'"\s*(title|content|body|text|articles?)\s*"\s*:', stripped, re.IGNORECASE):
        return True
    try:
        import json

        parsed = json.loads(stripped)
    except Exception:
        return bool(re.match(r"^\s*[\{\[]\s*['\"]?\w+['\"]?\s*:", stripped))
    return isinstance(parsed, (dict, list))


def _official_draft_has_banned_phrase(text: str) -> bool:
    checked_text = str(text or "")
    banned_phrases = (
        OFFICIAL_DRAFT_ANALYSIS_PHRASES
        + OFFICIAL_DRAFT_APPRECIATION_PHRASES
        + OFFICIAL_DRAFT_PROMOTION_PHRASES
        + OFFICIAL_DRAFT_SOURCE_PHRASES
        + OFFICIAL_DRAFT_PLACEHOLDER_PHRASES
    )
    return any(phrase in checked_text for phrase in banned_phrases) or any(
        re.search(pattern, checked_text) for pattern in OFFICIAL_DRAFT_PLACEHOLDER_PATTERNS
    )


def _official_draft_has_internal_placeholder_leak(text: str) -> bool:
    checked_text = str(text or "")
    return any(phrase in checked_text for phrase in OFFICIAL_DRAFT_INTERNAL_PLACEHOLDER_PHRASES) or any(
        re.search(pattern, checked_text) for pattern in OFFICIAL_DRAFT_INTERNAL_PLACEHOLDER_PATTERNS
    )


def validate_official_draft_final(title: str, content: str, target_words: int | None = None) -> None:
    content_text = (content or "").strip()
    title_text = (title or "").strip()
    if not content_text:
        raise ValueError(OFFICIAL_DRAFT_FINAL_VALIDATE_ERROR)

    chinese_chars = re.findall(r"[\u4e00-\u9fff]", content_text)
    if len(chinese_chars) < 80 and (target_words or DEFAULT_TARGET_WORDS) >= 120:
        raise ValueError(OFFICIAL_DRAFT_FINAL_VALIDATE_ERROR)

    combined_text = f"{title_text}\n{content_text}"
    if "**" in combined_text or "```" in combined_text:
        raise ValueError(OFFICIAL_DRAFT_FINAL_VALIDATE_ERROR)
    if _looks_like_json_payload(content_text):
        raise ValueError(OFFICIAL_DRAFT_FINAL_VALIDATE_ERROR)
    if _official_draft_has_internal_placeholder_leak(combined_text):
        raise ValueError(OFFICIAL_DRAFT_FINAL_VALIDATE_ERROR)
    if _official_draft_has_banned_phrase(combined_text):
        raise ValueError(OFFICIAL_DRAFT_FINAL_VALIDATE_ERROR)


def _safe_scene(scene: str | None) -> str:
    scene_text = (scene or "").strip()
    return scene_text or DEFAULT_SCENE


def _extract_topic_keyword(raw_title: str) -> str:
    raw_title = (raw_title or "").strip()
    topic = raw_title

    for marker in ("，要求", "。要求", "，需", "。需", "，适合", "。适合"):
        if marker in topic:
            topic = topic.split(marker, 1)[0]
            break

    for prefix in ("请写一篇关于", "写一篇关于", "写一篇", "围绕", "关于"):
        if topic.startswith(prefix):
            topic = topic[len(prefix) :]
            break

    for suffix in ("的公文初稿", "公文初稿", "的公文文章", "的文章", "文章"):
        if topic.endswith(suffix):
            topic = topic[: -len(suffix)]
            break

    topic = topic.strip("，。；; ")
    return topic or raw_title


def _clean_formal_title_text(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text or "").strip()
    return cleaned.strip(" \t\r\n，。；;：:")


def _extract_bracketed_title(raw_title: str) -> str:
    matches = re.findall(r"【([^】]+)】", raw_title or "")
    for candidate in reversed(matches):
        cleaned = _clean_formal_title_text(candidate)
        if cleaned:
            return cleaned
    return ""


def _looks_like_polluted_lead_title(title: str) -> bool:
    return any(marker in title for marker in FULL_ARTICLE_TITLE_POLLUTION_MARKERS)


def _looks_like_formal_full_article_title(title: str) -> bool:
    if not title or _looks_like_polluted_lead_title(title):
        return False
    return any(marker in title for marker in FULL_ARTICLE_FORMAL_TITLE_MARKERS)


def _build_broad_full_article_title(topic: str) -> str:
    cleaned = _clean_formal_title_text(topic)
    for keyword, title in FULL_ARTICLE_BROAD_TITLE_TEMPLATES:
        if cleaned == keyword:
            return title
    return f"关于{cleaned}工作推进情况的报告" if cleaned else "关于有关工作推进情况的报告"


def _resolve_full_article_title(raw_title: str) -> str:
    raw = _clean_formal_title_text(raw_title)
    bracket_title = _extract_bracketed_title(raw)
    if bracket_title:
        return bracket_title

    title_without_reference = re.split(r"参考资料[:：]", raw, maxsplit=1)[0]
    title_without_reference = _clean_formal_title_text(title_without_reference)
    if _looks_like_formal_full_article_title(title_without_reference):
        return title_without_reference

    topic = _extract_topic_keyword(title_without_reference or raw)
    if _looks_like_formal_full_article_title(topic):
        return topic
    return _build_broad_full_article_title(topic)


def _build_rag_query(request: ArticleGenRequest) -> str:
    topic = _extract_topic_keyword(request.title)
    parts = [topic or request.title.strip()]
    if request.scene and request.scene.strip():
        parts.append(request.scene.strip())
    if request.style:
        parts.append(request.style.strip())
    return "\n".join(parts)


def _safe_log_text(text: str | None, limit: int = 200) -> str:
    value = (text or "").replace("\r", " ").replace("\n", "\\n").strip()
    if len(value) > limit:
        return f"{value[:limit]}..."
    return value


def _normalize_anchor_text(text: str | None) -> str:
    return "".join(ch for ch in (text or "") if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")


def _normalize_title_text(text: str | None) -> str:
    cleaned = re.sub(r"[《》“”\"'：:，,。！？!？\s]+", "", text or "")
    return cleaned.strip()


TOPIC_ANCHOR_NOISE_PATTERNS = (
    re.compile(r"(?:19|20)\d{2}年"),
    re.compile(r"第?[一二三四1-4]季度"),
    re.compile(r"(?:上半年|下半年|全年|年度)"),
    re.compile(r"(?:全镇|全县|全市|全区|本单位|本部门)"),
)


def _strip_topic_anchor_noise(topic: str) -> str:
    cleaned = (topic or "").strip()
    for pattern in TOPIC_ANCHOR_NOISE_PATTERNS:
        cleaned = pattern.sub("", cleaned)
    cleaned = re.sub(r"\s+", "", cleaned)
    return cleaned.strip("，。；;：:、 ")


def _build_topic_anchor_terms(topic: str) -> list[str]:
    denoised_topic = _strip_topic_anchor_noise(topic)
    normalized = _normalize_anchor_text(denoised_topic or topic)
    if not normalized:
        return []
    if len(normalized) <= 4:
        return [normalized]

    anchors = {normalized}
    for size in (4, 5):
        if len(normalized) < size:
            continue
        for index in range(len(normalized) - size + 1):
            anchors.add(normalized[index : index + size])
    return sorted(anchors, key=len, reverse=True)


def _result_matches_topic(item: dict[str, Any], topic_anchor_terms: list[str]) -> bool:
    if not topic_anchor_terms:
        return True

    haystack = _normalize_anchor_text(
        "\n".join(
            [
                str(item.get("title") or ""),
                str(item.get("chunk_text") or ""),
            ]
        )
    )
    return any(anchor in haystack for anchor in topic_anchor_terms)


def _normalize_article_mode(mode: str | None) -> str:
    value = (mode or "").strip().lower()
    if value == "draft_article":
        return "official_draft"
    if value in ("official_draft", "full_article", "document_mode"):
        return value
    return DEFAULT_ARTICLE_MODE


def _normalize_target_words(target_words: int | None) -> int:
    try:
        value = int(target_words or DEFAULT_TARGET_WORDS)
    except (TypeError, ValueError):
        value = DEFAULT_TARGET_WORDS
    return max(MIN_TARGET_WORDS, min(MAX_TARGET_WORDS, value))


def _normalize_export_format(export_format: str | None) -> str:
    value = (export_format or DEFAULT_EXPORT_FORMAT).strip().lower()
    if value not in {"txt", "docx"}:
        raise ValueError("export_format must be 'txt' or 'docx'")
    return value


def _strip_optional_text(value: str | None) -> str:
    return (value or "").strip()


def build_official_draft_task(request: ArticleGenRequest) -> OfficialDraftTask:
    article_mode = (request.article_mode or "").strip().lower()
    if article_mode != "official_draft":
        raise ValueError(OFFICIAL_DRAFT_ONLY_MESSAGE)

    title = _strip_optional_text(request.title)
    if not title:
        raise ValueError("official_draft title must not be empty")

    document_type = _strip_optional_text(request.document_type)
    scene_compat = _strip_optional_text(request.scene) or None
    document_type_from_scene_compat = False
    if not document_type and scene_compat:
        document_type = scene_compat
        document_type_from_scene_compat = True

    return OfficialDraftTask(
        title=title,
        topic=_extract_topic_keyword(title),
        document_type=document_type,
        level=_strip_optional_text(request.level),
        org_type=_strip_optional_text(request.org_type),
        business_domain=_strip_optional_text(request.business_domain),
        writing_purpose=_strip_optional_text(request.writing_purpose),
        reference_scope=_strip_optional_text(request.reference_scope),
        target_words=_normalize_target_words(request.target_words),
        export_format=_normalize_export_format(request.export_format),
        scene_compat=scene_compat,
        document_type_from_scene_compat=document_type_from_scene_compat,
    )


def _lead_title_has_analysis_signal(title: str) -> bool:
    return any(marker in title for marker in LEAD_TITLE_ANALYSIS_MARKERS)


def _looks_like_formal_doc_title(title: str, request_title: str) -> bool:
    normalized_title = _normalize_title_text(title)
    normalized_request = _normalize_title_text(request_title)
    if normalized_title and normalized_request and normalized_title == normalized_request:
        return True
    return any(marker in title for marker in LEAD_FORMAL_TITLE_MARKERS) and not _lead_title_has_analysis_signal(title)


def _build_lead_reader_situation(
    request_title: str,
    article_types: list[str],
    material_card: dict[str, list[str]],
) -> str:
    topic = _extract_topic_keyword(request_title)
    scenes = material_card["scenes"][:3]
    problems = material_card["problems"][:3]
    actions = material_card["actions"][:3]
    expressions = material_card["expressions"][:2]

    scene_text = "、".join(scenes) if scenes else "资料里的重点场景"
    problem_text = "、".join(problems) if problems else "常见空话和落点不实"
    action_text = "、".join(actions) if actions else "具体动作和闭环安排"
    expression_text = "、".join(expressions) if expressions else "更实的材料表达"

    speech_like = _contains_article_type(article_types, "讲话稿")
    experience_like = _contains_article_type(article_types, "经验交流")
    analysis_like = _contains_article_type(article_types, "公文解析")
    title_mentions_summary = any(marker in request_title for marker in ("总结", "汇报"))
    title_mentions_rectify = any(marker in request_title for marker in ("整改", "巡察", "对照检查", "民主生活会", "组织生活会"))

    if title_mentions_rectify:
        return (
            f"读者多半正在赶一篇《{topic}》相关材料，最容易卡在表态和问题之间："
            f"嘴上会写，{problem_text}却落不实。真正难写的地方，不是再补几句态度，而是把{scene_text}"
            f"这些问题表现，和{action_text}这些整改动作接起来。正文要让人感觉你就在帮他改这篇稿。"
        )
    if speech_like:
        return (
            f"读者多半正在赶一篇《{topic}》相关讲话材料，最容易写成前面回头看、后面列任务的拼接稿。"
            f"真正卡住的是怎么把{scene_text}这些现实场景，和{action_text}这些后续动作串起来，"
            f"让稿子不是只剩安排口气，而是能看见抓手和落点。"
        )
    if title_mentions_summary or experience_like:
        return (
            f"读者多半正在赶一篇《{topic}》相关总结或汇报，表面上材料不少，真正难的是一写就只剩成绩和概括。"
            f"这篇文章要抓住{scene_text}这些场景，告诉他为什么{problem_text}会让材料发飘，"
            f"以及怎样把{action_text}和{expression_text}补进去。"
        )
    if analysis_like:
        return (
            f"读者多半正在找《{topic}》这类材料的可借鉴写法，卡点不在懂不懂道理，而在怎么把{scene_text}"
            f"这些实料转成可用表达。正文要围绕{problem_text}展开，像现场改稿，不像课堂讲义。"
        )
    return (
        f"读者多半正在赶一篇《{topic}》相关材料，手里不是完全没内容，而是写着写着只剩原则话。"
        f"这篇文章要围绕{scene_text}这些场景、{action_text}这些动作和{expression_text}这些表达展开，"
        f"让人一看就知道自己卡在{problem_text}哪里。"
    )


def _build_lead_specific_handoff(material_card: dict[str, list[str]]) -> str:
    handoff_terms = _dedupe_preserve(material_card["actions"] + material_card["keywords"] + material_card["scenes"], 4)
    if handoff_terms:
        joined = "、".join(handoff_terms)
        return f"后面我把“{joined}”这几类常用表达和局部改写句式，整理成了几组可以直接改写的句子，放在文末说明里，需要时可以翻一下。"
    return "后面我把这类材料里常见的落点句、动作句和收束句，整理成了几组可以直接改写的表达，放在文末说明里，需要时可以翻一下。"



def _extract_result_text(item: dict[str, Any]) -> str:
    return str(item.get("chunk_text") or item.get("text") or "").strip()


def _extract_source_title(item: dict[str, Any]) -> str:
    return str(item.get("title") or item.get("source") or "").strip()


def _extract_result_source(item: dict[str, Any]) -> str:
    title = _extract_source_title(item)
    file_path = str(item.get("file_path") or item.get("filename") or "").strip()
    filename = Path(file_path).name if file_path else ""
    if title and filename:
        return f"{title} ({filename})"
    return title or filename or "未知来源"


def _extract_result_metadata(item: dict[str, Any]) -> dict[str, Any]:
    metadata = item.get("metadata")
    if isinstance(metadata, dict):
        return metadata

    derived: dict[str, Any] = {}
    for key in ("file_type", "word_count", "scene_tags", "chunk_index", "title", "file_path"):
        value = item.get(key)
        if value not in (None, "", [], {}):
            derived[key] = value
    return derived


def _normalize_score(item: dict[str, Any]) -> float:
    raw_score = item.get("score")
    if raw_score is None:
        raw_score = item.get("distance")
    try:
        return round(float(raw_score), 4)
    except (TypeError, ValueError):
        return 0.0


def _normalize_rag_results(
    results: list[dict[str, Any]] | None,
    min_score: float,
    min_results: int,
    topic_anchor_terms: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    normalized: list[dict[str, Any]] = []
    filter_debug: list[dict[str, Any]] = []

    for item in results or []:
        text = _extract_result_text(item)
        if not text:
            filter_debug.append(
                {
                    "source": _extract_result_source(item),
                    "score": _normalize_score(item),
                    "reason": "empty_text",
                }
            )
            continue

        if not _result_matches_topic(item, topic_anchor_terms):
            filter_debug.append(
                {
                    "source": _extract_result_source(item),
                    "score": _normalize_score(item),
                    "reason": "topic_anchor_miss",
                }
            )
            continue

        score = _normalize_score(item)
        score_type = str(item.get("score_type") or "vector_similarity")
        metadata = _extract_result_metadata(item)
        chunk_id = item.get("chunk_id")
        if not chunk_id:
            doc_id = item.get("doc_id", "")
            chunk_index = item.get("chunk_index", 0)
            chunk_id = f"{doc_id}:{chunk_index}"

        normalized.append(
            {
                "doc_id": item.get("doc_id", ""),
                "chunk_id": chunk_id,
                "chunk_index": item.get("chunk_index", metadata.get("chunk_index")),
                "source": _extract_result_source(item),
                "source_title": _extract_source_title(item) or _extract_result_source(item),
                "score": score,
                "score_type": score_type,
                "metadata": metadata,
                "text": text[:MAX_RAG_CHUNK_CHARS],
            }
        )

    normalized.sort(key=lambda record: record["score"], reverse=True)

    deduped: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str]] = set()
    total_chars = 0

    for item in normalized:
        if item["score_type"] == "vector_similarity" and item["score"] < min_score:
            filter_debug.append(
                {
                    "source": item["source"],
                    "score": item["score"],
                    "reason": "score_below_threshold",
                }
            )
            continue

        text_key = " ".join(item["text"].split())
        dedupe_key = (item["source"], text_key)
        if dedupe_key in seen_keys:
            filter_debug.append(
                {
                    "source": item["source"],
                    "score": item["score"],
                    "reason": "duplicate_chunk",
                }
            )
            continue

        projected = total_chars + len(item["text"])
        if projected > MAX_RAG_CONTEXT_CHARS:
            filter_debug.append(
                {
                    "source": item["source"],
                    "score": item["score"],
                    "reason": "context_limit_exceeded",
                }
            )
            break

        seen_keys.add(dedupe_key)
        deduped.append(item)
        total_chars = projected

    if len(deduped) < min_results:
        return [], filter_debug

    return deduped, filter_debug


def _format_metadata_line(metadata: dict[str, Any]) -> str:
    if not metadata:
        return "无"

    ordered_keys = ("scene_tags", "chunk_index", "file_type", "word_count")
    parts: list[str] = []

    for key in ordered_keys:
        value = metadata.get(key)
        if value in (None, "", [], {}):
            continue
        if isinstance(value, list):
            value = ",".join(str(item) for item in value)
        parts.append(f"{key}={value}")

    for key, value in metadata.items():
        if key in ordered_keys or value in (None, "", [], {}):
            continue
        parts.append(f"{key}={value}")

    return "；".join(parts) if parts else "无"


def _format_rag_context(results: list[dict[str, Any]]) -> str:
    blocks: list[str] = []
    for index, item in enumerate(results, start=1):
        blocks.append(
            "\n".join(
                [
                    f"[资料{index}]",
                    f"来源：{item['source']}",
                    f"相似度：{item['score']}",
                    f"chunk_id：{item['chunk_id']}",
                    f"metadata：{_format_metadata_line(item['metadata'])}",
                    f"正文：{item['text']}",
                ]
            )
        )
    return "\n\n".join(blocks)


def _build_reference_sources(results: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    """Expose existing RAG hits for UI display without changing retrieval or prompt logic."""
    sources: list[dict[str, Any]] = []
    for item in results[:limit]:
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        file_path = str(metadata.get("file_path") or "").strip()
        filename = Path(file_path).name if file_path else ""
        title = str(item.get("source_title") or item.get("source") or "").strip()
        snippet = " ".join(str(item.get("text") or "").split())
        sources.append(
            {
                "reference_title": title,
                "reference_filename": filename,
                "reference_snippet": snippet[:320],
                "reference_score": item.get("score", 0.0),
                "reference_reason": "参考资料",
            }
        )
    return sources


def _split_text_units(text: str) -> list[str]:
    pieces = re.split(r"[。！？；\n]+", text or "")
    units: list[str] = []
    for piece in pieces:
        cleaned = piece.strip("，,；;：: ")
        if len(cleaned) < 6:
            continue
        units.append(cleaned)
    return units


def _contains_any(text: str, hints: tuple[str, ...]) -> bool:
    return any(hint in text for hint in hints)


def _select_units(units: list[str], hints: tuple[str, ...], limit: int = MAX_CARD_ITEMS) -> list[str]:
    selected = [unit for unit in units if _contains_any(unit, hints)]
    return _dedupe_preserve(selected, limit)


def _extract_data_points(units: list[str]) -> list[str]:
    selected = [
        unit
        for unit in units
        if re.search(r"\d", unit) or any(marker in unit for marker in ("公里", "家", "项", "次", "个", "%", "起"))
    ]
    return _dedupe_preserve(selected, MAX_CARD_ITEMS)


def _extract_keywords(material_card: dict[str, list[str]]) -> list[str]:
    matched: list[str] = []
    pools = (
        material_card["examples"]
        + material_card["scenes"]
        + material_card["problems"]
        + material_card["actions"]
        + material_card["expressions"]
    )
    for text in pools:
        for hint in SCENE_HINTS + ACTION_HINTS + PROBLEM_HINTS:
            if hint in text:
                matched.append(hint)
    return _dedupe_preserve(matched, MAX_CARD_ITEMS + 2)


def _build_content_material_card(results: list[dict[str, Any]]) -> dict[str, list[str]]:
    titles = _dedupe_preserve([item["source_title"] for item in results], MAX_CARD_ITEMS)
    units: list[str] = []
    for item in results:
        units.extend(_split_text_units(item["text"]))

    scenes = _select_units(units, SCENE_HINTS)
    problems = _select_units(units, PROBLEM_HINTS)
    actions = _select_units(units, ACTION_HINTS)
    data_points = _extract_data_points(units)
    examples = _dedupe_preserve(scenes + data_points + [unit for unit in units if "围绕" in unit or "针对" in unit], MAX_CARD_ITEMS)
    expressions = _dedupe_preserve(
        [
            unit
            for unit in units
            if 8 <= len(unit) <= 40 and (_contains_any(unit, ACTION_HINTS) or _contains_any(unit, PROBLEM_HINTS))
        ],
        MAX_CARD_ITEMS,
    )

    material_card = {
        "titles": titles,
        "examples": examples or titles[:MAX_CARD_ITEMS],
        "data_points": data_points,
        "scenes": scenes or examples[:MAX_CARD_ITEMS],
        "problems": problems or examples[:MAX_CARD_ITEMS],
        "actions": actions or expressions[:MAX_CARD_ITEMS],
        "expressions": expressions or examples[:MAX_CARD_ITEMS],
        "keywords": [],
    }
    material_card["keywords"] = _extract_keywords(material_card)
    return material_card


def _infer_article_type(results: list[dict[str, Any]]) -> list[str]:
    inferred: list[str] = []
    for item in results:
        title = item["source_title"]
        for keyword, article_type in ARTICLE_TYPE_HINTS.items():
            if keyword in title:
                inferred.append(article_type)
    return _dedupe_preserve(inferred or ["专题材料"], 3)


def _infer_opening_method(material_card: dict[str, list[str]]) -> str:
    for text in material_card["expressions"] + material_card["examples"]:
        for opening_type, markers in OPENING_HINTS:
            if _contains_any(text, markers):
                return opening_type
    return "任务切入"


def _contains_article_type(article_types: list[str], *targets: str) -> bool:
    return any(article_type in targets for article_type in article_types)


def _pick_best_label(candidates: list[tuple[str, int]], default: str) -> str:
    ranked = sorted(candidates, key=lambda item: (-item[1], candidates.index(item)))
    return ranked[0][0] if ranked and ranked[0][1] > 0 else default


def _infer_lead_structure_plan(
    request_title: str,
    article_types: list[str],
    material_card: dict[str, list[str]],
) -> dict[str, str]:
    title = request_title or ""
    scene_count = len(material_card["scenes"])
    problem_count = len(material_card["problems"])
    action_count = len(material_card["actions"])
    expression_count = len(material_card["expressions"])
    data_count = len(material_card["data_points"])
    example_count = len(material_card["examples"])

    speech_like = _contains_article_type(article_types, "讲话稿")
    report_like = _contains_article_type(article_types, "汇报材料", "专题材料")
    experience_like = _contains_article_type(article_types, "经验交流")
    analysis_like = _contains_article_type(article_types, "公文解析")
    title_mentions_speech = any(marker in title for marker in ("讲话", "发言", "表态"))
    title_mentions_deployment = any(marker in title for marker in ("部署", "推进", "安排", "落实"))
    title_mentions_summary = any(marker in title for marker in ("总结", "汇报", "经验", "复盘"))

    opening = _pick_best_label(
        [
            ("现场问题切入", scene_count * 3 + problem_count),
            ("真实场景切入", scene_count * 2 + example_count),
            ("资料中某个具体动作切入", action_count * 2 + scene_count),
            ("材料修改痛点切入", problem_count * 2 + expression_count),
            ("退稿批语切入", (problem_count + expression_count) + (2 if speech_like or title_mentions_speech else 0)),
            ("领导关注点切入", (3 if speech_like or title_mentions_speech else 0) + action_count + (2 if title_mentions_deployment else 0)),
            ("基层执行难点切入", action_count + scene_count),
            ("任务压力切入", action_count + (2 if report_like or speech_like else 0) + (3 if title_mentions_deployment else 0)),
            ("熟词失效切入", expression_count + problem_count),
            ("普通写法失效切入", expression_count + problem_count),
        ],
        "任务压力切入",
    )

    body = _pick_best_label(
        [
            ("场景展开", scene_count * 3 + action_count),
            ("问题递进", problem_count * 4 + expression_count + scene_count),
            ("从任务落实链条拆解", action_count * 3 + (4 if speech_like or report_like else 0) + (3 if title_mentions_deployment else 0)),
            ("从真实工作流程拆解", action_count * 2 + data_count),
            ("从基层执行拆解", action_count * 2 + scene_count),
            ("从领导视角拆解", (4 if speech_like or title_mentions_speech else 0) + problem_count + action_count + (2 if title_mentions_deployment else 0)),
            ("从材料结构拆解", expression_count + (2 if speech_like or report_like else 0)),
            ("从关键词误用拆解", expression_count * 2 + problem_count),
            ("修改前后对比", expression_count + problem_count + scene_count),
            ("从“为什么写空”反推“怎么写实”", problem_count * 3 + expression_count + scene_count),
        ],
        "问题递进",
    )

    material_way = _pick_best_label(
        [
            ("工作动作拆解", action_count * 3),
            ("具体场景带出", scene_count * 3),
            ("场景转表达", scene_count * 2 + expression_count),
            ("可用表达嵌入", expression_count * 2 + action_count),
            ("错误写法点评", problem_count * 2 + expression_count),
            ("案例化说明", example_count * 2 + data_count),
            ("清单化呈现", action_count + data_count + scene_count),
            ("段落改写示范", expression_count + problem_count),
            ("关键词拆解", expression_count + problem_count),
            ("资料句式转公文表达", expression_count + (2 if analysis_like else 0)),
        ],
        "具体场景带出",
    )

    pacing = _pick_best_label(
        [
            ("先讲场景再带出写法", scene_count * 2 + expression_count),
            ("先抛出材料痛点，再给资料库里的实料", problem_count * 2 + scene_count),
            ("先指出误区再拆解", problem_count * 2 + expression_count),
            ("先讲一个常见退稿点，再展开解决办法", problem_count + expression_count + (2 if speech_like else 0)),
            ("先给判断再铺素材", action_count + (2 if speech_like or report_like else 0)),
            ("先吐槽再给方法", problem_count + expression_count),
            ("先举例再总结", example_count * 2 + data_count + (2 if experience_like or title_mentions_summary else 0)),
            ("先讲问题再给可用句子", problem_count + expression_count * 2),
        ],
        "先抛出材料痛点，再给资料库里的实料",
    )

    ending = _pick_best_label(
        [
            ("完整框架承接", action_count + (2 if speech_like or report_like else 0) + (2 if title_mentions_deployment else 0)),
            ("后续提纲承接", action_count + (2 if speech_like else 0) + (2 if title_mentions_deployment else 0)),
            ("相关写法承接", expression_count * 2),
            ("常用表达承接", expression_count + problem_count),
            ("更多场景承接", scene_count * 2),
            ("后半部分内容承接", example_count + data_count),
            ("模板整理承接", example_count + (2 if title_mentions_summary else 0)),
        ],
        "相关写法承接",
    )

    structure = (
        f"不要套固定骨架。本次更适合用“{opening}”起笔，主体以“{body}”为主线，"
        f"素材优先采用“{material_way}”，节奏上走“{pacing}”，结尾用“{ending}”自然转入说明弱承接。"
    )
    progression = (
        f"先按“{pacing}”把读者带进本次主题，再围绕“{body}”把资料里的场景、动作、表达串起来；"
        f"如果“普通写法 / 更好写法”或“核心心法”不适合当前素材，就不要硬塞。"
    )
    return {
        "opening_method": opening,
        "structure": structure,
        "paragraph_progression": progression,
        "material_way": material_way,
        "ending_method": ending,
    }


def _infer_structure(
    request_title: str,
    article_types: list[str],
    material_card: dict[str, list[str]],
    article_mode: str,
) -> str:
    if article_mode == "draft_article":
        return _infer_lead_structure_plan(request_title, article_types, material_card)["structure"]
    if material_card["problems"] and material_card["actions"]:
        return "问题—原因—措施"
    if material_card["scenes"] and material_card["actions"]:
        return "现状—短板—路径"
    return "认识—责任—整改"


def _infer_paragraph_progression(
    request_title: str,
    article_types: list[str],
    material_card: dict[str, list[str]],
    article_mode: str,
) -> str:
    if article_mode == "draft_article":
        return _infer_lead_structure_plan(request_title, article_types, material_card)["paragraph_progression"]
    return "先点出当前任务或风险场景，再把问题表现、整改动作和结果链条展开，结尾回到工作要求和可执行抓手。"


def _build_style_sample_card(
    results: list[dict[str, Any]],
    material_card: dict[str, list[str]],
    article_mode: str,
    request_title: str,
) -> dict[str, Any]:
    article_types = _infer_article_type(results)
    lead_plan = (
        _infer_lead_structure_plan(request_title, article_types, material_card)
        if article_mode == "draft_article"
        else {}
    )
    return {
        "article_types": article_types,
        "opening_method": lead_plan.get("opening_method", _infer_opening_method(material_card)),
        "structure": _infer_structure(request_title, article_types, material_card, article_mode),
        "paragraph_progression": _infer_paragraph_progression(request_title, article_types, material_card, article_mode),
        "material_way": lead_plan.get("material_way", "工作动作拆解"),
        "ending_method": lead_plan.get("ending_method", "相关写法承接"),
        "reader_situation": (
            _build_lead_reader_situation(request_title, article_types, material_card)
            if article_mode == "draft_article"
            else ""
        ),
        "specific_handoff": (
            _build_lead_specific_handoff(material_card) if article_mode == "draft_article" else ""
        ),
        "expression_features": _dedupe_preserve(material_card["expressions"] + material_card["actions"], 5),
    }


def _render_bullets(items: list[str], fallback: str = "无明显素材") -> str:
    if not items:
        return f"- {fallback}"
    return "\n".join(f"- {item}" for item in items)


def _format_content_material_card(material_card: dict[str, list[str]]) -> str:
    return (
        "【内容素材卡】\n"
        "命中文章标题：\n"
        f"{_render_bullets(material_card['titles'])}\n"
        "具体实例：\n"
        f"{_render_bullets(material_card['examples'])}\n"
        "具体数据：\n"
        f"{_render_bullets(material_card['data_points'], '资料中未提取到明确数字，正文不要自行编造数据')}\n"
        "具体工作场景：\n"
        f"{_render_bullets(material_card['scenes'])}\n"
        "具体问题表现：\n"
        f"{_render_bullets(material_card['problems'])}\n"
        "具体措施动作：\n"
        f"{_render_bullets(material_card['actions'])}\n"
        "可借鉴表达：\n"
        f"{_render_bullets(material_card['expressions'])}\n"
        "可用关键词：\n"
        f"{_render_bullets(material_card['keywords'], '请从资料标题和具体场景里自然提炼关键词')}"
    )


def _format_style_sample_card(style_card: dict[str, Any], article_mode: str) -> str:
    lines = [
        "【风格样本卡】",
        "文章类型：",
        _render_bullets(style_card["article_types"]),
        "开头方式：",
        f"- {style_card['opening_method']}",
        "结构组织提示：",
        f"- {style_card['structure']}",
        "段落推进方式：",
        f"- {style_card['paragraph_progression']}",
        "素材呈现优先级：",
        f"- {style_card['material_way']}",
    ]
    if article_mode == "draft_article":
        lines.extend(
            [
                "读者当前处境：",
                f"- {style_card['reader_situation'] or '按当前主题和资料命中情况，代入正在赶稿的人最容易卡住的那一层'}",
                "结尾承接方式：",
                f"- {style_card['ending_method']}",
                "本篇更具体的说明承接：",
                f"- {style_card['specific_handoff'] or '承接里要写清楚文末说明具体整理了什么'}",
            ]
        )
    lines.extend(
        [
            "可学习但不能照抄的表达特点：",
            _render_bullets(style_card["expression_features"], "用短句点问题，用中长句落场景和动作，结尾收束到可执行抓手"),
        ]
    )
    return "\n".join(lines)


def _build_mode_task(article_mode: str, style_card: dict[str, Any] | None = None) -> str:
    if article_mode == "official_draft":
        return (
            "official_draft 正文初稿模式：直接生成一篇可继续修改的公文正文初稿。\n"
            "输出目标：\n"
            "- 第一行输出公文标题，空一行后输出正文。\n"
            "- 正文要按用户主题和文种语境组织为可在 Word 中继续修改的材料初稿。\n"
            "- 允许根据资料库内容重组结构、场景、动作和表达，但不得离开资料库编造事实。\n"
            "- 资料不足处使用泛化、可修改的公文表达，不输出“待补充”“待确认”“TODO”等内部占位提示。\n"
            "禁止事项：\n"
            "- 不要写写法分析文，不要写“这类材料应该怎么写”。\n"
            "- 不要输出“可以从以下几个方面写”“写作时要注意”“本文可围绕”“写法上”“可借鉴”等教学口吻。\n"
            "- 不要输出公众号、引流、赏析、爆款、SEO、营销转化相关内容。\n"
            "- 不要输出 Markdown、JSON、来源标记、Prompt 说明、自检说明或解释性前言。\n"
            "- 不要编造资料库没有的数据、人名、单位、日期、项目、案例。\n"
            "- 不要把参考资料简单拼接，也不要连续大段照抄资料库原文。\n"
            "写作要求：\n"
            "- 语言保持机关公文和单位材料口吻，表达稳妥、具体、可修改。\n"
            "- 章节结构服务于本次文种和主题，不做完整文种白名单推断，不硬套固定模板。\n"
            "- 内容要落到背景、认识、问题、措施、要求、安排等与本次主题相关的正文段落中。\n"
        )
    if article_mode == "full_article":
        return (
            "公文初稿定位：基于资料库真人材料生成的可修改初稿（半成品）。\n"
            "这是一份需要笔杆子补充真实数据后才能使用的初稿——结构框架、文种格式、章节逻辑已完全正确，但具体数据、人名、日期等需根据本单位实际情况替换。\n"
            "AI 不知道你单位的实际情况——但它已经把骨架和内容方向全部搭好了，你只需把占位符换成真实信息。\n"
            "\n"
            "【文种识别与结构】\n"
            "根据主题自动判断文种，严格按该文种的标准结构组织：\n"
            "- 通知：缘由 → 事项 → 执行要求\n"
            "- 报告：引言 → 工作进展与成效 → 存在问题与分析 → 下一步打算\n"
            "- 请示：请示缘由 → 请示事项 → 结尾用语（\"妥否，请批示\"）\n"
            "- 讲话稿：称谓 → 开场（背景/意义）→ 主体（成绩-问题-措施）→ 结尾号召\n"
            "- 汇报材料：基本情况 → 主要做法与成效 → 存在困难与问题 → 下一步打算与建议\n"
            "- 工作总结：工作回顾 → 主要成绩 → 问题与不足 → 经验体会 → 下一步打算\n"
            "- 调研报告：调研背景与目的 → 基本情况 → 问题分析 → 对策建议\n"
            "- 方案：指导思想 → 目标任务 → 工作措施与步骤 → 时间节点 → 保障机制\n"
            "- 函：发函缘由 → 函告/商洽事项 → 结尾用语\n"
            "- 纪要：会议概况 → 议定事项 → 工作要求\n"
            "\n"
            "【素材归位规则】\n"
            "内容素材卡中的素材，按章节功能自动归位，不要全堆在一个章节：\n"
            "- 场景类素材 → 归入\"背景/现状/基本情况\"章节\n"
            "- 问题类素材 → 归入\"存在问题/困难\"章节\n"
            "- 措施/动作类素材 → 归入\"工作部署/措施/下一步\"章节\n"
            "- 表达/句式类素材 → 融入各章节的公文用语中\n"
            "每个章节至少吸收 1-2 条相关素材。素材不足的章节，用规范的公文语句填充框架，不得编造。\n"
            "\n"
            "【占位符规范】\n"
            "以下信息 AI 无法从资料库获取时，必须改为泛化表达或删除具体值，不得输出内部占位提示：\n"
            "- 具体统计数据 → 改为“相关数据可结合实际进一步细化”一类自然表述\n"
            "- 人名、职务 → 删除具体姓名职务或改为岗位职责类表述\n"
            "- 具体日期、时间节点 → 改为“适时”“定期”“按要求”等泛化表述\n"
            "- 单位/部门名称 → 使用当前上下文已有名称；没有依据时不硬填\n"
            "- 文件编号、文号 → 没有依据时不输出\n"
            "原则：宁可泛化表达，绝不编造假数据；不得输出【待补充】、【待确认】、TODO 等占位符。\n"
            "\n"
            "【写作要求】\n"
            "- 开门见山：第一句点明主题，不铺垫不绕弯\n"
            "- 层次分明：章节之间逻辑递进，不跳跃不重复\n"
            "- 用语规范：符合党政机关公文处理习惯，不用口语、网络用语\n"
            "- 句式变化：长短句结合，不要通篇\"要……要……要……\"\n"
            "- 落到具体：每段都要有具体场景、动作或问题，不写空话套话\n"
            "- 信息密度：每句话承载信息，不灌水\n"
            "- 篇幅控制：按目标字数生成，核心章节详写、辅助章节简写\n"
            "\n"
            "【禁止事项】\n"
            "- 不要写成公文文章、写作教程、写法分析文\n"
            "- 不要用\"笔者\"\"本文\"\"我建议\"等评论口吻或第一人称\n"
            "- 不要加说明承接、初稿话术、文章推荐\n"
            "- 不要编造资料库没有的数据、案例、人名、日期\n"
            "- 不要大段照抄资料库原文（连续 28 字以上相同即算照抄）\n"
            "- 不要出现\"XX\"\"某某\"\"某单位\"等模板占位符，也不要改成【待补充：XX】这类内部提示\n"
            "- 不要用\"第一，\"\"第二，\"\"第三，\"机械罗列（用自然段落推进）\n"
            "- 不要把几篇资料简单拼接成拼盘稿"
        )

    style_card = style_card or {}
    dynamic_structure_hint = style_card.get(
        "structure",
        "不要套固定骨架，必须根据主题、文种、场景、动作和表达方式临场组织文章。",
    )
    dynamic_progression = style_card.get(
        "paragraph_progression",
        "正文要根据本次资料命中情况自然推进，而不是照搬单一模板。",
    )
    reader_situation = style_card.get(
        "reader_situation",
        "把读者当成正在赶这篇材料的人，直接帮他判断哪里虚、哪里该补场景和动作。",
    )
    specific_handoff = style_card.get(
        "specific_handoff",
        "文末说明承接要写清楚整理了哪些可直接改写的表达。",
    )
    return (
        "公文初稿定位：资料库真人文章借鉴 + 公文结构重组 + 说明弱承接。\n"
        "- 你不是在写教材，也不是在上写作课。你是一名长期给基层办公室人员改材料的人，熟悉退稿意见，知道哪些话看着正确但实际很空。整篇文章要像懂材料的人在现场改稿。\n"
        "要求：\n"
        "- 只要当前模式是公文初稿，最终输出就必须是面向写材料人的公文写法分析文，不是正式讲话稿、正式表态发言、正式汇报材料，也不是可直接提交的完整公文正文。\n"
        "- 结构必须动态生成，不要套固定模板，不要从固定模板库机械选一个骨架。\n"
        f"- 本次结构组织提示：{dynamic_structure_hint}\n"
        f"- 本次段落推进提示：{dynamic_progression}\n"
        f"- 本次读者处境：{reader_situation}\n"
        "- 标题必须改写成公文分析标题，体现“怎么写 / 如何写实 / 为什么写空 / 别只会写XX”这类写法分析属性，不能直接照用正式公文题名。\n"
        "- 如果主题本身是“讲话稿、表态发言、汇报材料、工作总结、对照检查材料”等，正文也必须始终在讲“这类材料怎么写”，不能整篇直接代写成正式材料。\n"
        "- 开头必须根据用户主题、文种、内容素材卡、资料库具体场景和写材料人的真实痛点动态生成，不得复用固定句子，不得做固定开头句库随机抽取。\n"
        "- 开头策略库只作为思路，不是固定句库；每次只能按当前主题临场生成。可用策略包括：\n"
        + "\n".join(f"  * {strategy}" for strategy in LEAD_OPENING_STRATEGIES)
        + "\n"
        "- 主体推进方式要从本次资料里临场组合，不要每篇都强制写成“误区判断→普通写法 vs 更好写法→一句核心心法”。可用推进方式包括：\n"
        + "\n".join(f"  * {strategy}" for strategy in LEAD_STRUCTURE_BODIES)
        + "\n"
        "- 素材呈现方式要跟着命中资料走，不要固定只用一种说法。可用呈现方式包括：\n"
        + "\n".join(f"  * {strategy}" for strategy in LEAD_STRUCTURE_MATERIALS)
        + "\n"
        "- 文章节奏也要自然变化，不要每篇都同一个开头和同一个落点。可用节奏包括：\n"
        + "\n".join(f"  * {strategy}" for strategy in LEAD_STRUCTURE_PACING)
        + "\n"
        "- 结尾承接必须保留说明弱承接，但句式和落点要自然变化。可用承接方式包括：\n"
        + "\n".join(f"  * {strategy}" for strategy in LEAD_STRUCTURE_ENDINGS)
        + "\n"
        "- 开头从写材料人的真实痛点、退稿点、任务压力、现场场景或具体动作切入，不要每篇都用“写XX最怕什么”“很多人误以为”或“说到底，核心心法只有一句话”。\n"
        "- 中间必须给出资料库里的具体场景、具体实例、具体动作和可直接套用的表达。\n"
        "- 可以局部示范一小段讲话稿、表态发言或汇报材料，但只能作为示范片段，整篇文章必须始终是“写法分析文”。\n"
        "- 禁止整篇用正式公文框架展开，尤其不要以“同志们：”“各位领导、同志们：”“今天召开这个会议”“下面，我讲几点意见”“我代表……”来组织正文，也不要以“我就讲这些，谢谢大家”“以上发言，不当之处请批评指正”收束全文。\n"
        "- 禁止虚构个人经历和关系链，不要写“上周有个朋友问我”“我之前见过一个单位”“上次我去某地调研”“有个领导跟我说”这类假人味故事。直接写这类材料最容易卡在哪里。\n"
        "- 禁止保留 X、XX、某村、某单位、某地、某企业、某部门、202X、XX%、X项、X家 这类模板占位符；没有真实数字时，改成自然的泛化表达，不要硬编数字。\n"
        "- 少用漂亮但虚的比喻，不要拿“黏合剂、第一颗扣子、焊在一起、压舱石、定盘星、组合拳、一盘棋、牵一发动全身、打通最后一公里”代替具体动作。\n"
        "- 每篇至少给出 1-2 组局部改稿示范。可以自然写成“不要只写……可以改成……”“这句话的问题在于……补一层动作后，可以这样写……”这类形式，但示范必须紧贴资料库实料，短、实、可借鉴。\n"
        "- 不要只讲方法，要明确指出某句话为什么空、少了哪个对象、哪个场景、哪个动作、哪一层闭环，再给出更实的改法。\n"
        "- 不要硬广，不要出现营销导流、付费转化、私下领取或资料售卖表达。\n"
        "- 禁止虚构作者资历，不要写“干了十年材料岗”这类人设。\n"
        "- 禁止“亲测有效”“私藏”“福利”等营销口吻。\n"
        "- “普通写法 / 更好写法”只在确实适合时使用，最多出现一次；“核心心法”不是每篇必备，也不要固定用一句话硬收尾。\n"
        "- 不要每篇都写成“你是不是也……”这类同一开头结构。\n"
        "- 批量生成时尽量拉开首段句式、主体推进方式和结尾承接角度，避免重复、机械、像 AI。\n"
        "- 正文里不要出现“结构方案”“本文采用”“下面分三点”等自我拆解式提示，直接输出自然文章。\n"
        f"- 说明弱承接不能只写“已整理”。本篇更具体的承接方式：{specific_handoff}\n"
        f"- 末尾弱承接只能写成这类自然动作中的一种：{'; '.join(LEAD_HANDOFF_OPTIONS)}"
    )


def _build_self_check_rules(article_mode: str) -> str:
    rules = [
        "- 至少包含 3 个具体场景。",
        "- 至少包含 3 个具体措施/动作。",
        "- 自然使用资料库关键词或表达，不要只剩空泛套话。",
        "- 不要连续堆“提高认识、压实责任、强化落实、守牢底线”等套话。",
        "- 不要连续大段照抄资料库原文，发现照抄痕迹要先改写再输出。",
    ]
    if article_mode == "draft_article":
        rules.append("- 末尾必须有说明弱承接，且不能出现硬广禁词。")
        rules.append("- 标题必须像公文写法分析标题，不能直接等于正式公文题名，也不能还是“在XX上的讲话”“专题汇报”“对照检查材料”这种正式材料标题。")
        rules.append("- 正文必须是在讲“这类材料怎么写”，不能误生成完整讲话稿、完整表态发言、完整汇报材料或完整对照检查材料。")
        rules.append("- 不要以“同志们：”“各位领导、同志们：”“今天召开这个会议”“下面，我讲几点意见”“我代表……”组织整篇正文。")
        rules.append("- 不要以“我就讲这些，谢谢大家”“以上发言，不当之处请批评指正”这类正式公文结尾收束全文。")
        rules.append("- 不要虚构朋友、领导、调研、个人经历，不要为了自然编故事。")
        rules.append("- 不要保留 X、XX、某村、某单位、某地、某企业、某部门、202X、XX%、X项、X家 这类占位符。")
        rules.append("- 少讲漂亮比喻，多做局部改稿；每篇至少要有 1 组可直接借鉴的改写示范。")
        rules.append("- 说明承接要写清楚文末整理了什么，不能只说“相关内容已整理”。")
        rules.append("- 检查开头和主体是否模板化，不要固定复用“写XX最怕什么”“很多人误以为”“说到底，核心心法只有一句话”。")
        rules.append("- “普通写法 / 更好写法”最多出现一次；“核心心法”如无必要不要出现。")
        rules.append("- 如果仍然高度模板化，最多整体重写一次，不要无限重写。")
    elif article_mode == "official_draft":
        rules.append("- 必须直接输出标题和正文，不能写成写法分析文或写作教程。")
        rules.append("- 不得出现“这类材料应该”“可以从以下几个方面写”“写作时要注意”“本文可围绕”“写法上”“可借鉴”等教学口吻。")
        rules.append("- 不得输出 Markdown、JSON、来源标记、Prompt 说明或自检说明。")
        rules.append("- 资料库没有的数据、人名、单位、日期、项目不得编造；资料不足处使用泛化公文表达，不输出内部占位提示。")
    else:
        rules.append("- 文种判断正确？（通知不像报告，讲话稿不像汇报材料）")
        rules.append("- 结构章节完整？（该有的章节都写了，顺序正确）")
        rules.append("- 素材归位合理？（场景在现状段、问题在问题段、措施在措施段）")
        rules.append("- 无编造数据？（资料库没有的不瞎写，改为泛化表达，不标【待补充】）")
        rules.append("- 用语规范？（无口语、无网络用语、无 AI 腔）")
        rules.append("- 无大段照抄？（和资料库原文比对，连续 28 字以上相同要改写）")
        rules.append("- 无内部占位？（不得输出【待补充】、【待确认】、TODO 等提示）")
        rules.append("- 篇幅合理？（核心章节扎实，辅助章节不冗长）")
    return "【基础自检规则】\n" + "\n".join(rules)


def _build_official_draft_document_type_hint(document_type: str) -> str:
    document_type = (document_type or "").strip()
    if document_type == "党课讲稿":
        return (
            "- 党课讲稿：应有授课口吻，可使用“同志们：”开场；必须生成党课讲稿正文本身；"
            "不得生成党课活动总结，不得生成讲稿起草说明，不得生成工作推进情况报告；"
            "不得写成“现将……报告如下”的报告体材料，不得使用“现将……报告如下”“特此报告”。"
        )
    if document_type == "工作汇报":
        return "- 工作汇报：可以使用汇报体，可以有工作进展、存在问题、下一步打算。"
    if document_type == "工作总结":
        return "- 工作总结：应偏阶段性回顾，可包含成绩、做法、问题、下一步。"
    if document_type == "领导讲话":
        return "- 领导讲话：应有讲话口吻，不得写成普通工作总结。"
    return "- 按本次 document_type 组织标题和正文结构，不套用资料中的其他体裁。"


def build_official_draft_prompt(
    task: OfficialDraftTask,
    rag_context: str,
    reference_sources: list[dict[str, Any]] | None = None,
    rag_reference_block: str = "",
    user_fact_anchors: list[UserFactAnchor] | None = None,
) -> str:
    """Build the dedicated official_draft prompt from the normalized task."""
    document_type = task.document_type or "未指定"
    reference_count = len(reference_sources or [])
    reference_scope = task.reference_scope or "按当前命中的资料库内容参考"

    return (
        "【任务定义】\n"
        "你要生成的是一篇可供修改使用的公文正文初稿。\n"
        "本任务只生成公文初稿本身，不生成写法分析、赏析、公众号文案或教学说明。\n\n"
        "【文种定义】\n"
        f"document_type：{document_type}\n"
        "document_type 是最终成品文种，不是要汇报的工作对象，不是资料名称，不是活动名称。\n"
        f"如果 document_type={document_type}，则必须生成{document_type}正文本身；"
        "不得因为标题、资料标题或资料体裁中出现其他文种词而改变本次 document_type。\n"
        "如果 document_type=党课讲稿，则必须生成党课讲稿正文本身；不得生成党课活动总结；"
        "不得生成讲稿起草说明；不得生成工作推进情况报告；不得生成“现将……报告如下”的报告体材料。\n"
        f"{_build_official_draft_document_type_hint(document_type)}\n"
        f"{build_official_draft_rule_prompt_block(document_type)}\n\n"
        "【写作对象】\n"
        f"标题或主题：{task.title}\n"
        f"主题关键词：{task.topic}\n"
        f"目标字数：{task.target_words}字左右\n\n"
        "【任务字段】\n"
        f"document_type（最终成品文种）：{document_type}\n"
        f"writing_purpose（写作目的）：{task.writing_purpose or '未指定'}\n"
        "writing_purpose 是本次写作目的，用于决定任务语气和内容侧重，不得覆盖 document_type。\n"
        f"business_domain（内容领域）：{task.business_domain or '未指定'}\n"
        "business_domain 是内容领域，只影响材料内容，不决定文种。\n"
        f"level（层级语气）：{task.level or '未指定'}\n"
        "level 用于控制层级语气，不得写成正文中的机械标签。\n"
        f"org_type（单位语境）：{task.org_type or '未指定'}\n"
        "org_type 用于控制单位语境，不得写成正文中的机械标签。\n"
        f"reference_scope（资料参考范围）：{reference_scope}\n"
        f"reference_sources_count：{reference_count}\n\n"
        f"{_render_user_fact_lock_prompt_block(user_fact_anchors or [])}"
        f"{_render_unsupported_claim_prompt_block()}"
        "【资料库参考内容】\n"
        "资料库内容只能作为内容参考和表达参考。\n"
        "不得直接照搬资料标题；不得因为资料中出现“报告”“总结”“推进情况”就改变本次 document_type。\n"
        "如资料体裁与本次 document_type 不一致，应吸收其中事实、措施、案例、表达，不照搬结构。\n"
        "不得虚构资料中没有的具体日期、人数、金额、会议名称等不可确认事实。\n"
        "事实只能来自用户输入或内容参考；写法参考只能学习结构、语气和表达方式，不能作为事实来源，不能改变 document_type。\n"
        "如果内容参考没有明确支撑，不得把一般风险写成已经发生过的本地事实；不得无依据写“有的村出现过”“个别干部虚报冒领”“项目验收时有的”“群众反映强烈”“巡察反馈发现”“日常监督发现”“走访发现”“我们镇这几年出台了”“镇党委会落实了”“镇纪委发现了”等来源型或本地型事实。\n"
        "内容参考没有明确支撑时，不得使用暗含具体来源的表达；避免无来源使用“近年来通报的典型案例”“近年通报案例显示”“从通报案例看”“从近年来查处情况看”“从近年来监督检查情况看”“从近年巡察反馈看”“从近年日常监督看”“从群众反映情况看”等说法。\n"
        "无来源时要使用风险表达和泛化表达，可以写“容易出现”“需要防范”“群众关注度高”“重点环节”“薄弱环节”“一般表现”，不要写成“已经发生”“已经发现”“群众反映”。\n"
        "如果只是一般风险表达，应改为“从一些基层违纪违法案例看”“从基层廉政风险的一般表现看”“从基层干部违纪问题的常见表现看”“从群众身边不正之风的典型风险看”“从实际工作中容易出现的廉政风险看”等稳妥表达。\n"
        "无来源事实替换示例：不写“低保评议有的村出现过优亲厚友的问题”，改写为“在低保评议等环节，容易出现优亲厚友、审核不严等廉政风险，必须严格程序、公开透明”。\n"
        "无来源暗含来源替换示例：不写“但大家看看近年来通报的典型案例，很多问题就出在小节上”，改写为“从一些基层违纪违法案例看，很多问题往往就出在小节失守上”，或改写为“从基层廉政风险的一般表现看，很多问题往往就出在小节失守上”。\n"
        "无来源事实替换示例：不写“发放救灾款时个别干部虚报冒领”，改写为“在救灾款物发放等群众关注度高的事项中，要防止虚报冒领、审核不严、发放不规范等问题”。\n"
        "无来源事实替换示例：不写“项目验收时有的睁一只眼闭一只眼”，改写为“在项目验收等关键环节，要防止把关不严、程序空转、监督缺位等风险”。\n"
        "无来源事实替换示例：不写“群众反映强烈的征地拆迁、惠农补贴发放等敏感事项”，改写为“征地拆迁、惠农补贴发放等群众关注度高、廉政风险较集中的敏感事项”。\n"
        "无来源事实替换示例：不写“我们镇上这几年也出了不少制度”，改写为“制度建设不能停留在纸面上，关键要看执行是否到位、程序是否闭环、监督是否有力”。\n"
        f"{rag_reference_block or rag_context}\n\n"
        "【写作要求】\n"
        "直接围绕 document_type 生成标题和正文，语气、结构、称谓、开头和收束均服务于最终成品文种。\n"
        "writing_purpose 只用于调整侧重，business_domain 只用于限定内容领域，level 和 org_type 只用于调节表达尺度。\n"
        "正文应有机关单位材料口吻，表达稳妥、具体、可修改。\n"
        "资料不足处使用概括性表述，不硬编数据、人物、时间、地点、会议名称、项目名称。\n\n"
        "资料不足时不要退回空泛模板，仍要保持标题动态、段落有层次；每段尽量兼顾思想引导、风险提示、制度要求和担当落点。\n"
        "可以使用基层工作场景和常见风险，但必须用“风险可能性、一般表现、需要防范”的方式表达，不能伪装成具体单位已经发生的事实。\n\n"
        "【禁止项】\n"
        "禁止写成写法分析文。\n"
        "禁止写成赏析稿。\n"
        "禁止写成公众号引流文。\n"
        "禁止输出“这类材料应该如何写”。\n"
        "禁止输出“可以从以下几个方面写”。\n"
        "禁止输出教学说明。\n"
        "禁止输出 Markdown 加粗符号。\n"
        "禁止输出 Markdown 标题符号。\n"
        "禁止输出 JSON。\n"
        "禁止输出代码块。\n"
        "禁止输出资料来源标记。\n"
        "禁止虚构具体日期、人数、金额、会议名称等不可确认事实。\n"
        "禁止在最终正文中出现【待补充】、【待确认】、TODO、需要核实、请提供资料等内部占位提示。\n\n"
        "【输出格式】\n"
        "直接输出标题和正文。\n"
        "第一行输出标题，空一行后输出正文。\n"
        "不要解释。\n"
        "不要教学。\n"
        "不要分析。\n"
        "不要列“写作思路”。\n"
        "不要列“参考资料”。\n"
        "不要使用 Markdown。"
    )


def _build_rag_prompt(
    request: ArticleGenRequest,
    task: OfficialDraftTask,
    prompt_scene: str,
    rag_context: str,
    gen_cfg: Any,
    retrieval_scene: str,
    article_mode: str,
    target_words: int,
    material_card: dict[str, list[str]],
    style_card: dict[str, Any],
    full_article_title: str = "",
) -> str:
    style_text = request.style.strip() if request.style else "未额外指定"
    mode_label = "公文初稿" if article_mode == "full_article" else "公文初稿"
    if article_mode == "official_draft":
        mode_label = "official_draft 正文初稿"
    topic_title = full_article_title if article_mode == "full_article" and full_article_title else task.title
    task_field_lines = []
    if task.document_type:
        task_field_lines.append(f"文种类型：{task.document_type}")
    if task.level:
        task_field_lines.append(f"层级：{task.level}")
    if task.org_type:
        task_field_lines.append(f"单位类型：{task.org_type}")
    if task.business_domain:
        task_field_lines.append(f"业务领域：{task.business_domain}")
    if task.writing_purpose:
        task_field_lines.append(f"写作目的：{task.writing_purpose}")
    if task.reference_scope:
        task_field_lines.append(f"参考资料范围：{task.reference_scope}")
    task_fields_block = "\n".join(task_field_lines)
    if task_fields_block:
        task_fields_block += "\n"
    scene_line = f"场景：{prompt_scene}\n" if prompt_scene else ""
    retrieval_scene_line = ""
    if retrieval_scene and retrieval_scene != prompt_scene:
        retrieval_scene_line = f"资料参考场景：{retrieval_scene}\n"
    full_article_title_line = ""
    if article_mode == "full_article" and full_article_title:
        full_article_title_line = f"正式文章标题：{full_article_title}\n"
    output_title_rule = (
        f"1. 第一行必须输出正式文章标题：{full_article_title}。\n"
        if article_mode == "full_article" and full_article_title
        else "1. 第一行输出文章标题。\n"
    )
    main_document_type = task.document_type or "未指定"
    official_draft_document_type_rules = ""
    if article_mode == "official_draft":
        official_draft_document_type_rules = (
            "【主文种约束】\n"
            f"本次主文种：{main_document_type}\n"
            "- 必须按本次主文种直接生成公文正文初稿，标题和正文结构均以本次主文种为准。\n"
            "- 不得写成其他文种；标题、主题、资料场景或参考材料中出现的其他文种词，只能作为内容背景，不能改变主文种。\n"
            "- 当主文种为“党课讲稿”时，标题和正文必须体现党课讲稿属性。\n"
            "- 当主文种为“党课讲稿”时，不得写成“情况报告”“工作汇报”“推进情况报告”。\n"
            "- 当主文种为“党课讲稿”时，不得使用“现将有关情况报告如下”“现将推进情况报告如下”等报告体口吻。\n"
            "- 当主文种为“党课讲稿”时，正文应围绕教育引导、理论阐释、问题警示、行动要求展开。\n"
            "- 如果主文种不是“报告/工作汇报”，不得使用“现将有关情况报告如下”“现将推进情况报告如下”等报告体口吻。\n\n"
        )

    return (
        "【用户写作需求】\n"
        f"主题：{topic_title}\n"
        f"{full_article_title_line}"
        f"{scene_line}"
        f"{retrieval_scene_line}"
        f"{task_fields_block}"
        f"生成模式：{mode_label}\n"
        f"目标字数：{target_words}字左右\n"
        f"补充要求：{style_text}\n\n"
        f"{_format_content_material_card(material_card)}\n\n"
        f"{_format_style_sample_card(style_card, article_mode)}\n\n"
        "【资料库参考内容】\n"
        f"{rag_context}\n\n"
        "【写作任务】\n"
        f"{official_draft_document_type_rules}"
        f"{_build_mode_task(article_mode, style_card)}\n"
        "写作硬约束：\n"
        "- retrieved_count > 0 才允许生成，当前就是基于已命中的资料写作。\n"
        "- PROMPT_HAS_RAG_CONTEXT=true 才允许生成，不能恢复普通 AI fallback。\n"
        "- 可以借鉴、改写、重组资料库素材，但不能照抄，也不能离开资料库自由发挥。\n"
        "- 资料里没有的数据、案例、部门动作一律不要自造。\n\n"
        "【风格约束】\n"
        "1. 学资料里的真人结构和表达节奏，不写通用 AI 总结稿。\n"
        "2. 不要机械堆“第一、第二、第三”，尽量自然推进段落。\n"
        "3. 不要满篇“不是……而是……”，句式要有长短变化。\n"
        "4. 不要把几篇资料简单拼接成拼盘稿。\n"
        "5. 每一段都尽量落到具体场景、问题、动作、结果，而不是只喊方向。\n\n"
        f"{_build_self_check_rules(article_mode)}\n\n"
        "【输出要求】\n"
        f"{output_title_rule}"
        "2. 空一行后输出正文。\n"
        f"3. 目标字数：{target_words}字左右。\n"
        f"4. 可接受范围：{gen_cfg.min_length}-{gen_cfg.max_length} 字。\n"
        "5. 只输出最终文章，不输出自检过程。"
    )


def _normalize_compare_text(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _run_basic_self_check(
    title: str,
    request_title: str,
    content: str,
    article_mode: str,
    document_type: str,
    material_card: dict[str, list[str]],
    rag_results: list[dict[str, Any]],
) -> dict[str, Any]:
    scene_terms = material_card["keywords"] + material_card["scenes"]
    action_terms = material_card["actions"] + material_card["expressions"]
    keyword_terms = material_card["keywords"] + material_card["expressions"]

    scene_hits = _dedupe_preserve([term for term in scene_terms if term and term in content])
    action_hits = _dedupe_preserve([term for term in action_terms if term and term in content])
    keyword_hits = _dedupe_preserve([term for term in keyword_terms if term and term in content])
    generic_hits = _dedupe_preserve([term for term in GENERIC_CLICHE_PHRASES if term in content])
    teaching_tone_hits = _dedupe_preserve(
        [phrase for phrase in OFFICIAL_DRAFT_TEACHING_TONE_PHRASES if phrase in content],
        6,
    )
    templated_opening = bool(re.search(r"写.{0,12}最怕", content[:120]))
    repeated_compare = content.count("普通写法") > 1 or content.count("更好写法") > 1
    repeated_xinfa = content.count("核心心法") > 1
    fixed_phrase_hits = _dedupe_preserve(
        [phrase for phrase in ("很多人误以为", "说到底，核心心法只有一句话") if phrase in content]
    )
    formal_opening_hit = next((phrase for phrase in LEAD_FORMAL_OPENINGS if content.startswith(phrase)), "")
    formal_ending_hit = next((phrase for phrase in LEAD_FORMAL_ENDINGS if content.rstrip().endswith(phrase)), "")
    formal_body_hits = _dedupe_preserve([phrase for phrase in LEAD_FORMAL_BODY_MARKERS if phrase in content], 6)
    analysis_body_hits = _dedupe_preserve([phrase for phrase in LEAD_ANALYSIS_BODY_MARKERS if phrase in content], 6)
    title_formal = _looks_like_formal_doc_title(title, request_title)
    fake_story_hits = _dedupe_preserve([phrase for phrase in LEAD_FAKE_STORY_MARKERS if phrase in content], 6)
    placeholder_hits = _dedupe_preserve([marker for marker in LEAD_PLACEHOLDER_MARKERS if marker in content], 6)
    placeholder_hits.extend(
        _dedupe_preserve(
            [pattern for pattern in LEAD_PLACEHOLDER_PATTERNS if re.search(pattern, content)],
            6,
        )
    )
    placeholder_hits = _dedupe_preserve(placeholder_hits, 6)
    fluffy_metaphor_hits = _dedupe_preserve([phrase for phrase in LEAD_FLUFFY_METAPHORS if phrase in content], 6)
    rewrite_demo_hits = _dedupe_preserve([phrase for phrase in LEAD_REWRITE_DEMO_MARKERS if phrase in content], 6)

    paragraphs = [paragraph.strip() for paragraph in content.splitlines() if paragraph.strip()]
    has_consecutive_cliche = any(
        sum(1 for phrase in GENERIC_CLICHE_PHRASES if phrase in paragraph) >= 3 for paragraph in paragraphs
    )

    normalized_content = _normalize_compare_text(content)
    copied_segments: list[str] = []
    for item in rag_results:
        for unit in _split_text_units(item["text"]):
            normalized_unit = _normalize_compare_text(unit)
            if len(normalized_unit) >= COPY_OVERLAP_THRESHOLD and normalized_unit in normalized_content:
                copied_segments.append(unit)
                break

    service_handoff_ok = True
    if article_mode == "draft_article":
        service_handoff_ok = "说明" in content and not any(banned in content for banned in LEAD_BANNED_PHRASES)

    too_hollow = article_mode == "full_article" and (len(scene_hits) < 3 or len(action_hits) < 3) and len(generic_hits) >= 2
    formal_doc_body = bool(formal_opening_hit or formal_ending_hit or len(formal_body_hits) >= 2)
    weak_analysis_orientation = article_mode == "draft_article" and len(analysis_body_hits) < 2
    handoff_too_generic = article_mode == "draft_article" and "说明" in content and "已整理" in content and "可以直接改写" not in content and "常用表达" not in content and "几组" not in content
    is_party_lecture_official_draft = (
        article_mode == "official_draft" and (document_type or "").strip() == PARTY_LECTURE_DOCUMENT_TYPE
    )
    party_lecture_title_report_hits: list[str] = []
    party_lecture_body_report_hits: list[str] = []
    party_lecture_teaching_tone_hits: list[str] = []
    if is_party_lecture_official_draft:
        party_lecture_title_report_hits = _dedupe_preserve(
            [
                phrase
                for phrase in PARTY_LECTURE_REPORT_TITLE_PHRASES
                if phrase != "关于" and phrase in title
            ]
            + (["关于……推进情况的报告"] if re.search(r"关于.+推进情况.*报告", title) else []),
            6,
        )
        party_lecture_body_report_hits = _dedupe_preserve(
            [phrase for phrase in PARTY_LECTURE_REPORT_BODY_PHRASES if phrase in content],
            8,
        )
        party_lecture_teaching_tone_hits = _dedupe_preserve(
            [phrase for phrase in PARTY_LECTURE_TEACHING_TONE_PHRASES if phrase in content],
            4,
        )

    failures: list[str] = []
    if len(scene_hits) < 3:
        failures.append("具体场景不足")
    if len(action_hits) < 3:
        failures.append("具体措施不足")
    if len(keyword_hits) < 3:
        failures.append("资料库关键词使用不足")
    if has_consecutive_cliche:
        failures.append("连续套话过多")
    if copied_segments:
        failures.append("存在疑似照抄")
    if article_mode == "official_draft" and teaching_tone_hits:
        failures.append("出现写法分析或教学口吻")
    if is_party_lecture_official_draft and party_lecture_title_report_hits:
        failures.append("党课讲稿标题误生成报告体")
    if is_party_lecture_official_draft and party_lecture_body_report_hits:
        failures.append("党课讲稿正文误生成报告体")
    if is_party_lecture_official_draft and not party_lecture_teaching_tone_hits:
        failures.append("党课讲稿缺少授课口吻，未直接面向听众展开。")
    if article_mode == "draft_article" and not service_handoff_ok:
        failures.append("缺少说明弱承接")
    if article_mode == "draft_article" and title_formal:
        failures.append("标题像正式公文题名")
    if article_mode == "draft_article" and weak_analysis_orientation:
        failures.append("正文不像写法分析文章")
    if article_mode == "draft_article" and formal_doc_body:
        failures.append("误生成正式公文正文")
    if article_mode == "draft_article" and fake_story_hits:
        failures.append("存在假人味故事")
    if article_mode == "draft_article" and placeholder_hits:
        failures.append("存在模板占位符")
    if article_mode == "draft_article" and len(fluffy_metaphor_hits) >= 2:
        failures.append("漂亮比喻过多")
    if article_mode == "draft_article" and not rewrite_demo_hits:
        failures.append("缺少局部改稿示范")
    if article_mode == "draft_article" and handoff_too_generic:
        failures.append("说明承接过于笼统")
    if article_mode == "draft_article" and (templated_opening or fixed_phrase_hits):
        failures.append("开头模板化")
    if article_mode == "draft_article" and repeated_compare:
        failures.append("普通写法/更好写法使用过度")
    if article_mode == "draft_article" and repeated_xinfa:
        failures.append("核心心法使用过度")
    if too_hollow:
        failures.append("公文初稿偏虚")

    return {
        "passed": not failures,
        "failures": failures,
        "scene_hits": scene_hits[:6],
        "action_hits": action_hits[:6],
        "keyword_hits": keyword_hits[:6],
        "generic_hits": generic_hits[:6],
        "teaching_tone_hits": teaching_tone_hits,
        "copied_segments": copied_segments[:3],
        "service_handoff_ok": service_handoff_ok,
        "templated_opening": templated_opening,
        "fixed_phrase_hits": fixed_phrase_hits,
        "formal_opening_hit": formal_opening_hit,
        "formal_ending_hit": formal_ending_hit,
        "formal_body_hits": formal_body_hits,
        "analysis_body_hits": analysis_body_hits,
        "title_formal": title_formal,
        "fake_story_hits": fake_story_hits,
        "placeholder_hits": placeholder_hits,
        "fluffy_metaphor_hits": fluffy_metaphor_hits,
        "rewrite_demo_hits": rewrite_demo_hits,
        "handoff_too_generic": handoff_too_generic,
        "party_lecture_title_report_hits": party_lecture_title_report_hits,
        "party_lecture_body_report_hits": party_lecture_body_report_hits,
        "party_lecture_teaching_tone_hits": party_lecture_teaching_tone_hits,
        "party_lecture_report_guard_failed": is_party_lecture_official_draft
        and bool(party_lecture_title_report_hits or party_lecture_body_report_hits),
        "party_lecture_final_gate_failed": is_party_lecture_official_draft
        and bool(
            party_lecture_title_report_hits
            or party_lecture_body_report_hits
            or not party_lecture_teaching_tone_hits
        ),
    }


def _build_revision_prompt(
    base_prompt: str,
    previous_content: str,
    self_check: dict[str, Any],
    article_mode: str,
) -> str:
    repair_lines = [
        "【上轮自检未通过，必须整体改写】",
        f"失败项：{'；'.join(self_check['failures'])}",
        f"已命中场景：{'；'.join(self_check['scene_hits']) or '无'}",
        f"已命中动作：{'；'.join(self_check['action_hits']) or '无'}",
        f"已命中关键词：{'；'.join(self_check['keyword_hits']) or '无'}",
    ]
    if self_check["generic_hits"]:
        repair_lines.append(f"需要删减的套话：{'；'.join(self_check['generic_hits'])}")
    if self_check.get("teaching_tone_hits"):
        repair_lines.append(f"需要删除的写法分析或教学口吻：{'；'.join(self_check['teaching_tone_hits'])}")
    if self_check["copied_segments"]:
        repair_lines.append(f"需要改写的疑似照抄片段：{'；'.join(self_check['copied_segments'])}")
    if self_check.get("fixed_phrase_hits"):
        repair_lines.append(f"需要删除的模板句：{'；'.join(self_check['fixed_phrase_hits'])}")
    if self_check.get("formal_body_hits"):
        repair_lines.append(f"需要删除的正式公文框架句：{'；'.join(self_check['formal_body_hits'])}")
    if self_check.get("formal_opening_hit"):
        repair_lines.append(f"不得再用正式称呼开头：{self_check['formal_opening_hit']}")
    if self_check.get("formal_ending_hit"):
        repair_lines.append(f"不得再用正式公文结尾：{self_check['formal_ending_hit']}")
    if self_check.get("fake_story_hits"):
        repair_lines.append(f"需要删除的假人味故事：{'；'.join(self_check['fake_story_hits'])}")
    if self_check.get("placeholder_hits"):
        repair_lines.append(f"需要删掉的占位符：{'；'.join(self_check['placeholder_hits'])}")
    if self_check.get("fluffy_metaphor_hits"):
        repair_lines.append(f"需要压减的漂亮比喻：{'；'.join(self_check['fluffy_metaphor_hits'])}")
    if self_check.get("party_lecture_title_report_hits"):
        repair_lines.append(f"党课讲稿标题报告体命中：{'；'.join(self_check['party_lecture_title_report_hits'])}")
    if self_check.get("party_lecture_body_report_hits"):
        repair_lines.append(f"党课讲稿正文报告体命中：{'；'.join(self_check['party_lecture_body_report_hits'])}")

    repair_lines.extend(
        [
            "修正要求：",
            "- 重新组织全文，不要在原稿上局部补丁式添加。",
            "- 必须补足具体场景、具体动作和资料库表达，但不能照抄原句。",
            "- 删除空话套话，把泛化表达改成具体风险点、整改动作、结果链条。",
            "- 文章语气要像在现场改稿，不要像AI上写作课。",
        ]
    )
    if article_mode == "draft_article":
        repair_lines.append("- 结尾补上一句自然的说明弱承接，不要写硬广。")
        repair_lines.append("- 换掉固定骨架，正文组织方式要根据本次素材重新搭，不要再回到同一套模板。")
        repair_lines.append("- 这是公文初稿，不是正式公文正文；要讲这类材料怎么写，不能直接代写完整讲话稿、表态发言、汇报材料或对照检查材料。")
        repair_lines.append("- 标题也要改成公文分析标题，不能直接照抄用户给的正式材料题名。")
        repair_lines.append("- 不要编朋友、调研、领导交流这类假故事；直接判断这类材料哪里空、该补什么对象、场景和动作。")
        repair_lines.append("- 至少补 1 组局部改稿示范，明确指出哪句话空、少了什么，再给出更实写法。")
        repair_lines.append("- 说明承接要写具体，说明文末整理了哪些关键词、动作句或可直接改写的表达。")
    elif article_mode == "official_draft":
        repair_lines.append("- 这是 official_draft 正文初稿模式，必须直接输出公文标题和正文。")
        repair_lines.append("- 不要写写法分析文，不要讲“这类材料怎么写”，不要输出教学口吻。")
        repair_lines.append("- 不要输出 Markdown、JSON、来源标记、Prompt 说明或自检说明。")
        repair_lines.append("- 资料不足处使用泛化公文表达，不得编造事实，不得输出“待补充”“待确认”“TODO”等内部占位提示。")
        repair_lines.append("- 通用材料和其他单位材料只能作写法参考，不得改写为当前单位已完成工作、现存问题、已建制度或具体频次时限责任规则。")
        if self_check.get("party_lecture_report_guard_failed"):
            repair_lines.extend(
                [
                    "",
                    "上一次输出错误地写成了关于党课讲稿的工作推进情况报告。",
                    "请完全改写为党课讲稿正文本身。",
                    "",
                    "硬性要求：",
                    "1. 标题不得包含“关于……推进情况的报告”。",
                    "2. 标题不得包含“工作推进情况”“情况报告”“工作汇报”。",
                    "3. 正文不得出现“现将……报告如下”。",
                    "4. 正文不得围绕讲稿起草、组织实施、会后反馈、整改推进展开。",
                    "5. 正文必须以授课口吻直接面向党员干部。",
                    "6. 正文建议以“同志们：”开场。",
                    "7. 正文结构应围绕：",
                    "   一、为什么要守牢廉洁底线",
                    "   二、基层干部廉政风险主要在哪里",
                    "   三、如何知敬畏、存戒惧、守底线",
                    "   四、怎样把廉洁履职落到行动上",
                    "8. 允许使用资料中的廉政风险、基层案例、纪律要求作为内容素材。",
                    "9. 不得编造具体日期、人名、人数和真实案件数据。",
                ]
            )
    else:
        repair_lines.append("- 保持材料初稿口吻，不要写成公文教程。")

    return f"{base_prompt}\n\n" + "\n".join(repair_lines) + f"\n\n【上轮草稿】\n{previous_content}"


def _flow3_v3_provider_model(provider: Any) -> str:
    config = getattr(provider, "config", None)
    if isinstance(config, dict):
        return str(config.get("model") or config.get("model_name") or "")
    return str(getattr(config, "model", "") or getattr(config, "model_name", "") or "")


class ExternalAuthorizationRequired(RuntimeError):
    def __init__(self, prepared: dict[str, Any]):
        super().__init__("flow3_v33_status=BLOCKED_EXTERNAL_AUTHORIZATION")
        self.prepared = prepared
        self.flow3_v3_status = "BLOCKED_EXTERNAL_AUTHORIZATION"


class FinalDeliveryQualityError(ValueError):
    """Raised before artifact delivery when the final draft misses a hard gate."""

    def __init__(
        self,
        status: str,
        *,
        actual_words: int,
        min_words: int,
        max_words: int,
        missing_structure_roles: list[str] | None = None,
    ):
        super().__init__(status)
        self.status = status
        self.actual_words = actual_words
        self.min_words = min_words
        self.max_words = max_words
        self.missing_structure_roles = list(missing_structure_roles or [])


def count_official_draft_words(final_text: str) -> int:
    """Single backend word-count contract used by the gate and UI/history."""
    return len(str(final_text or ""))


def derive_final_delivery_length_gate(target_words: int) -> dict[str, int]:
    """Hard delivery gate for official drafts, separate from plan guidance."""
    target = max(1, int(target_words))
    min_words = max(80, (target * 70 + 99) // 100)
    max_words = max(target + 500, (target * 140) // 100)
    return {
        "min_words": min_words,
        "max_words": max_words,
    }


def validate_final_delivery_quality(
    *,
    final_text: str,
    target_words: int,
    content_plan: dict[str, Any],
    structured_draft: dict[str, Any],
    validation_reports: dict[str, Any] | None = None,
    flow4_risk_status: str = "NO_RECORDED_RISK",
) -> dict[str, Any]:
    """Enforce length, required structure, safety and FLOW4 readiness pre-delivery."""
    gate_report = evaluate_final_delivery_length_and_structure(
        final_text=final_text,
        target_words=target_words,
        content_plan=content_plan,
        structured_draft=structured_draft,
    )
    if gate_report["length_gate_status"] != "PASS":
        raise FinalDeliveryQualityError(
            gate_report["length_gate_status"],
            actual_words=gate_report["actual_words"],
            min_words=gate_report["min_words"],
            max_words=gate_report["max_words"],
        )
    if gate_report["structure_gate_status"] != "PASS":
        raise FinalDeliveryQualityError(
            gate_report["structure_gate_status"],
            actual_words=gate_report["actual_words"],
            min_words=gate_report["min_words"],
            max_words=gate_report["max_words"],
            missing_structure_roles=gate_report["missing_structure_roles"],
        )

    reports = validation_reports or {}
    structured = reports.get("structured") or {}
    fact = reports.get("fact") or {}
    analysis = reports.get("analysis") or {}
    if (
        not structured.get("passed")
        or int(structured.get("invalid_evidence_reference_count") or 0) != 0
    ):
        raise ValueError("FAIL_STRUCTURED_OUTPUT")
    review_required = bool(
        not fact.get("passed")
        or int(fact.get("unsupported_count") or 0) != 0
        or int(fact.get("out_of_plan_count") or 0) != 0
        or int(fact.get("evidence_contract_hard_block_count") or 0) != 0
        or int(fact.get("hard_block_count") or 0) != 0
        or not analysis.get("passed")
        or int(analysis.get("analysis_fact_leak_count") or 0) != 0
        or flow4_risk_status == "REVIEW_REQUIRED"
    )

    work_summary_report = gate_report.get("work_summary_length_report") or {}
    return {
        "status": "REVIEW_REQUIRED_DRAFT" if review_required else "PASS",
        "delivery_status": (
            "SUCCESS_WITH_REVIEW" if review_required else "SUCCESS"
        ),
        "review_required": review_required,
        "actual_words": gate_report["actual_words"],
        "min_words": gate_report["min_words"],
        "max_words": gate_report["max_words"],
        "required_structure_roles": gate_report["required_structure_roles"],
        "covered_structure_roles": gate_report["covered_structure_roles"],
        "work_summary_length_report": work_summary_report,
        **work_summary_report,
    }


def evaluate_final_delivery_length_and_structure(
    *,
    final_text: str,
    target_words: int,
    content_plan: dict[str, Any],
    structured_draft: dict[str, Any],
) -> dict[str, Any]:
    """Report the real preview gates without changing formal delivery behavior."""
    actual_words = count_official_draft_words(final_text)
    length_range = derive_final_delivery_length_gate(target_words)
    min_words = length_range["min_words"]
    max_words = length_range["max_words"]
    work_summary = is_work_summary_content_plan(content_plan)
    execution_policy = (
        build_work_summary_execution_policy(content_plan)
        if work_summary
        else None
    )
    length_gate_status = "PASS"
    under_target_blocked = bool(
        actual_words < min_words
        and (
            not work_summary
            or str((execution_policy or {}).get("length_mode") or "")
            == TARGET_ORIENTED
        )
    )
    if under_target_blocked:
        length_gate_status = "FAIL_UNDER_LENGTH"
    elif actual_words > max_words:
        length_gate_status = "FAIL_OVER_LENGTH"

    plan_sections = [
        section
        for section in content_plan.get("sections") or []
        if isinstance(section, dict)
    ]
    required_ids = [str(section.get("section_id") or "") for section in plan_sections]
    required_roles = [str(section.get("section_role") or "") for section in plan_sections]
    draft_sections = [
        section
        for section in structured_draft.get("sections") or []
        if isinstance(section, dict)
    ]
    actual_ids = [str(section.get("section_id") or "") for section in draft_sections]
    draft_by_id = {
        section_id: section
        for section_id, section in zip(actual_ids, draft_sections)
    }
    # The FrozenArticlePlan projection owns section roles. Heading wording is
    # prose and must never be used as a HARD signal that a planned role exists.
    covered_roles: list[str] = []
    missing_roles: list[str] = []
    seen_ids: set[str] = set()
    duplicate_ids: list[str] = []
    for section_id in actual_ids:
        if section_id in seen_ids and section_id not in duplicate_ids:
            duplicate_ids.append(section_id)
        seen_ids.add(section_id)
    unknown_ids = list(dict.fromkeys(
        section_id for section_id in actual_ids if section_id not in required_ids
    ))
    missing_ids = [section_id for section_id in required_ids if section_id not in actual_ids]
    order_matches_plan = actual_ids == required_ids
    empty_ids: list[str] = []
    for section_id, required_role in zip(required_ids, required_roles):
        draft_section = draft_by_id.get(section_id)
        body_present = bool(
            draft_section
            and any(
                isinstance(sentence, dict)
                and str(sentence.get("text") or "").strip()
                for sentence in draft_section.get("sentences") or []
            )
        )
        if not body_present:
            missing_roles.append(required_role)
            if draft_section is not None:
                empty_ids.append(section_id)
            continue
        covered_roles.append(required_role)
    structure_issues = bool(
        missing_ids
        or empty_ids
        or duplicate_ids
        or unknown_ids
        or not order_matches_plan
    )
    report = {
        "length_gate_status": length_gate_status,
        "structure_gate_status": (
            "FAIL_STRUCTURE_INCOMPLETE"
            if structure_issues
            else "PASS"
        ),
        "actual_words": actual_words,
        "min_words": min_words,
        "max_words": max_words,
        "required_structure_roles": required_roles,
        "covered_structure_roles": covered_roles,
        "missing_structure_roles": missing_roles,
        "required_section_ids": required_ids,
        "actual_section_ids": actual_ids,
        "missing_section_ids": missing_ids,
        "empty_section_ids": empty_ids,
        "duplicate_section_ids": duplicate_ids,
        "unknown_section_ids": unknown_ids,
        "section_order_matches_plan": order_matches_plan,
    }
    if work_summary:
        report["work_summary_length_report"] = work_summary_length_report(
            content_plan=content_plan,
            actual_length=actual_words,
            under_target_blocked=under_target_blocked,
        )
    return report


def _flow3_v33_write_json(path: Path, value: Any) -> str:
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path.resolve())


def _unexpected_m2_shadow_failure_report(
    exc: Exception,
    *,
    stage: str,
    component: str,
) -> dict[str, Any]:
    """Build a non-sensitive diagnostic for an unexpected M2 shadow failure."""

    return {
        "generation_status": "SHADOW_GENERATION_FAILED",
        "failure_report": {
            "message": "shadow generation failed",
            "error_type": type(exc).__name__,
            "stage": stage,
            "component": component,
        },
        "external_model_calls": 0,
    }


def _fact_anchor_distribution_snapshot(
    fact_anchor_pack: dict[str, Any],
    section_ids: list[str] | None = None,
) -> dict[str, Any]:
    anchors = [
        item
        for item in fact_anchor_pack.get("fact_anchors") or []
        if isinstance(item, dict)
    ]
    ordered_section_ids = list(
        dict.fromkeys(
            [
                *[
                    str(section_id)
                    for section_id in section_ids or []
                    if section_id
                ],
                *sorted(
                    {
                        str(item.get("section_id") or "")
                        for item in anchors
                        if item.get("section_id")
                    }
                ),
            ]
        )
    )
    return {
        "section_anchor_counts": {
            section_id: sum(
                str(item.get("section_id") or "") == section_id
                for item in anchors
            )
            for section_id in ordered_section_ids
        },
        "evidence_ids_by_section": {
            section_id: sorted(
                str(item.get("evidence_id") or "")
                for item in anchors
                if str(item.get("section_id") or "") == section_id
            )
            for section_id in ordered_section_ids
        },
        "anchor_roles": sorted(
            (
                str(item.get("evidence_id") or ""),
                str(item.get("section_id") or ""),
                str(item.get("role") or ""),
            )
            for item in anchors
        ),
    }


def _fact_anchor_distribution_integrity_report(
    baseline: dict[str, Any],
    overlay: dict[str, Any],
) -> dict[str, Any]:
    baseline_roles = {
        item[0]: (item[1], item[2])
        for item in baseline.get("anchor_roles") or []
    }
    overlay_roles = {
        item[0]: (item[1], item[2])
        for item in overlay.get("anchor_roles") or []
    }
    baseline_ids = set(baseline_roles)
    overlay_ids = set(overlay_roles)
    moved = {
        evidence_id
        for evidence_id in baseline_ids & overlay_ids
        if baseline_roles[evidence_id][0]
        != overlay_roles[evidence_id][0]
    }
    changed = {
        evidence_id
        for evidence_id in baseline_ids | overlay_ids
        if baseline_roles.get(evidence_id)
        != overlay_roles.get(evidence_id)
    }
    dropped = baseline_ids - overlay_ids
    added = overlay_ids - baseline_ids
    passed = not (changed or moved or dropped or added)
    return {
        "baseline_section_anchor_counts": baseline.get(
            "section_anchor_counts",
            {},
        ),
        "overlay_section_anchor_counts": overlay.get(
            "section_anchor_counts",
            {},
        ),
        "baseline_evidence_ids_by_section": baseline.get(
            "evidence_ids_by_section",
            {},
        ),
        "overlay_evidence_ids_by_section": overlay.get(
            "evidence_ids_by_section",
            {},
        ),
        "changed_anchor_count": len(changed),
        "moved_evidence_count": len(moved),
        "dropped_evidence_count": len(dropped),
        "added_evidence_count": len(added),
        "integrity_status": "PASS" if passed else "FAIL",
    }


def prepare_flow3_v33_structured_generation(
    request: ArticleGenRequest,
    *,
    artifact_root: Path,
    search_fn: Any = search_detailed,
    retrieval_payload: dict[str, Any] | None = None,
    timings: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Run the complete local V3.3 preflight in the mandated order."""
    preflight_started = time.perf_counter()
    timings = timings if timings is not None else _new_generation_timings()
    root = Path(artifact_root)
    root.mkdir(parents=True, exist_ok=True)
    stage_started = time.perf_counter()
    task = build_official_draft_task(request)
    _record_elapsed(timings, "request_prepare_ms", stage_started)

    stage_started = time.perf_counter()
    retrieval = retrieval_payload or retrieve_documents(task, persist_trace=False, search_fn=search_fn)
    _record_elapsed(timings, "retrieval_ms", stage_started)
    retrieval_trace = build_retrieval_trace(retrieval)
    selected_chunks = list(retrieval.get("selected_chunks") or [])
    if retrieval.get("retrieval_status") != "SUFFICIENT" or not selected_chunks:
        raise NoRelevantMaterialError(
            topic=task.topic, rag_query=str(retrieval.get("query_text") or task.title),
            retrieved_count=0, top_score=0.0,
            reason=str(retrieval.get("insufficient_reason") or "no_document_passed_relevance_gate"),
        )

    stage_started = time.perf_counter()
    evidence_pack = build_atomic_evidence_pack(task, retrieval)
    atomic_report = validate_atomic_evidence_pack(evidence_pack, retrieval)
    _record_elapsed(timings, "atomic_evidence_ms", stage_started)
    if not atomic_report["passed"]:
        _flow3_v33_write_json(root / "atomic_evidence_quality_report.json", atomic_report)
        raise InvalidEvidencePackError(atomic_report)

    stage_started = time.perf_counter()
    reference_structure = extract_reference_structure(task, retrieval)
    reference_structure_report = validate_reference_structure(reference_structure)
    if not reference_structure_report["passed"]:
        reference_structure = empty_reference_structure()
    _record_elapsed(timings, "reference_structure_ms", stage_started)

    frozen_article_plan: FrozenArticlePlan | None = None
    planning_engine_summary: dict[str, Any] | None = None
    planning_artifact_paths: dict[str, str] = {}
    if is_work_summary_task(task):
        from app.services.generation.writing_reference import select_writing_references
        writing_reference = select_writing_references(
            task, excluded_document_ids=tuple(dict.fromkeys(
                str(row.get("document_id") or row.get("doc_id") or "") for row in selected_chunks
            )),
        )
        _flow3_v33_write_json(root / "writing_reference_selection.json", writing_reference)
        planning_result = PlanningEngine().plan(
            task,
            evidence_pack,
            reference_structure=reference_structure,
            retrieval=retrieval,
            target_words_provided="target_words" in request.model_fields_set,
            artifact_root=root,
            writing_reference=writing_reference,
        )
        frozen_article_plan = planning_result.frozen_article_plan
        planning_projection = planning_result.legacy_projection()
        base_plan = planning_result.initial_content_plan
        repaired_plan = planning_projection["content_plan"]
        selection = planning_projection["selection"]
        anchor_pack = planning_projection["fact_anchor_pack"]
        plan_report = planning_result.content_plan_report
        selection_report = planning_result.selection_report
        anchor_report = planning_result.fact_anchor_report
        writing_style_guidance_pack = planning_result.writing_style_guidance_pack
        writing_style_guidance_report = planning_result.writing_style_guidance_report
        writing_style_plan_overlay_report = (
            planning_result.writing_style_plan_overlay_report
        )
        planning_engine_summary = planning_result.planning_engine_summary
        planning_artifact_paths = planning_result.artifact_paths
        timings["planning_engine_ms"] = float(
            planning_engine_summary.get("planning_engine_ms") or 0.0
        )
    else:
        stage_started = time.perf_counter()
        base_plan = build_content_plan(
            task,
            evidence_pack,
            reference_structure=reference_structure,
        )
        _record_elapsed(timings, "content_plan_ms", stage_started)

        stage_started = time.perf_counter()
        selection = select_evidence_for_content_plan(task, evidence_pack, base_plan)
        _record_elapsed(timings, "evidence_selection_ms", stage_started)

        stage_started = time.perf_counter()
        anchor_pack = build_fact_anchor_pack(selection, evidence_pack)
        _record_elapsed(timings, "fact_anchor_ms", stage_started)

        stage_started = time.perf_counter()
        repaired_plan = repair_content_plan(
            task, selection["content_plan"], anchor_pack,
            target_words_provided="target_words" in request.model_fields_set,
        )
        writing_style_guidance_result = load_writing_style_guidance_pack(task)
        writing_style_guidance_pack = writing_style_guidance_result.get("pack")
        writing_style_guidance_report = writing_style_guidance_result.get("report") or {}
        repaired_plan, writing_style_plan_overlay_report = (
            apply_writing_style_guidance_to_content_plan(
                repaired_plan,
                writing_style_guidance_pack,
                task=task,
            )
        )
        selection["content_plan"] = repaired_plan
        _record_elapsed(timings, "content_plan_ms", stage_started)
        plan_report = validate_content_plan(repaired_plan, evidence_pack, retrieval)
        selection_report = validate_evidence_selection(selection, evidence_pack)
        fact_anchor_outbound = anchor_pack["fact_anchors"]
        anchor_report = validate_fact_anchor_pack(
            anchor_pack, selection, evidence_pack, fact_anchor_outbound,
        )

    introduction_contract = build_introduction_contract(
        task.document_type,
        int(repaired_plan.get("target_total") or task.target_words),
        list(repaired_plan.get("sections") or []),
    )
    fact_anchor_outbound = anchor_pack["fact_anchors"]
    if frozen_article_plan is not None:
        frozen_article_plan.assert_projection_integrity(
            content_plan=repaired_plan,
            selection=selection,
            fact_anchor_pack=anchor_pack,
        )
    stage_started = time.perf_counter()
    fact_source_support = build_fact_source_support(
        anchor_pack,
        selection["selected_evidence_local"],
        retrieval,
    )
    _record_elapsed(timings, "fact_anchor_ms", stage_started)
    evidence_group_pack = (
        build_work_summary_evidence_groups(
            repaired_plan,
            anchor_pack,
            fact_source_support,
        )
        if is_work_summary_task(task)
        else {"schema_version": "not-applicable", "groups": []}
    )
    baseline_anchor_distribution = (
        _fact_anchor_distribution_snapshot(
            anchor_pack,
            [
                str(section.get("section_id") or "")
                for section in repaired_plan.get("sections") or []
            ],
        )
    )
    writing_structure_overlay = build_writing_structure_overlay(
        repaired_plan,
        task.document_type,
    )
    article_outline_result = None
    dynamic_budget_plan = None
    outline_contract_generation_report: dict[str, Any] = {
        "generation_status": "NOT_APPLICABLE",
        "external_model_calls": 0,
    }
    if frozen_article_plan is not None:
        shadow_component = "article_outline"
        try:
            article_outline_result = ArticleOutlineProjector().project(
                frozen_article_plan,
                evidence_group_pack,
            )
            shadow_component = "dynamic_budget"
            delivery_length_gate = derive_final_delivery_length_gate(
                int(repaired_plan.get("target_total") or task.target_words)
            )
            dynamic_budget_plan = build_dynamic_budget(
                article_outline_result.outline,
                requested_total_words=int(
                    repaired_plan.get("target_total") or task.target_words
                ),
                total_hard_max_words=int(delivery_length_gate["max_words"]),
            )
            outline_contract_generation_report = {
                "generation_status": "PASS",
                "source_plan_sha": article_outline_result.outline.source_plan_sha,
                "outline_sha": article_outline_result.outline.outline_sha,
                "budget_sha": dynamic_budget_plan.budget_sha,
                "mapping_validation": article_outline_result.validation_report,
                "budget_validation": dynamic_budget_plan.validation.model_dump(
                    mode="json"
                ),
                "external_model_calls": 0,
            }
        except (OutlineProjectionError, BudgetContractError) as exc:
            # M2 is a passive/shadow contract.  Persist the explicit contract
            # failure while leaving the frozen Legacy Writer handoff unchanged.
            outline_contract_generation_report = {
                "generation_status": exc.code,
                "failure_report": exc.report,
                "external_model_calls": 0,
            }
        except Exception as exc:
            # M2 is passive until M3.  Unexpected ordinary runtime failures
            # remain observable but cannot block the unchanged Legacy Writer.
            outline_contract_generation_report = (
                _unexpected_m2_shadow_failure_report(
                    exc,
                    stage="generation",
                    component=shadow_component,
                )
            )
            logger.warning(
                "M2 shadow generation failed: error_type=%s stage=%s component=%s",
                type(exc).__name__,
                "generation",
                shadow_component,
            )
    overlay_anchor_distribution = (
        _fact_anchor_distribution_snapshot(
            anchor_pack,
            [
                str(section.get("section_id") or "")
                for section in repaired_plan.get("sections") or []
            ],
        )
    )
    fact_anchor_distribution_integrity_report = (
        _fact_anchor_distribution_integrity_report(
            baseline_anchor_distribution,
            overlay_anchor_distribution,
        )
    )
    writer_plan_control = (
        build_writer_plan_control_view(
            content_plan=repaired_plan,
            work_summary_execution_policy=build_work_summary_execution_policy(
                repaired_plan,
                document_type=str(task.document_type or ""),
            ),
            evidence_group_pack=evidence_group_pack,
            writing_structure_overlay=writing_structure_overlay,
            frozen_article_plan=frozen_article_plan,
        )
        if is_work_summary_task(task)
        else {}
    )
    writer_plan_injection_report = (
        build_writer_plan_injection_report(writer_plan_control)
        if writer_plan_control else {}
    )
    stage_started = time.perf_counter()
    prompt = build_atomic_new_draft_prompt(
        task, evidence_pack, repaired_plan, selection["selected_evidence_outbound"], anchor_pack,
        writing_structure_overlay,
        writing_style_guidance_pack=writing_style_guidance_pack,
        evidence_group_pack=evidence_group_pack,
        frozen_article_plan=frozen_article_plan,
    ) if plan_report["passed"] and selection_report["passed"] and anchor_report["passed"] else ""
    prompt_metrics = {
        "character_count": len(prompt),
        "estimated_input_tokens": math.ceil(len(prompt) / 2),
        "token_estimate_method": "ceil(unicode_character_count/2); heuristic only",
        "fact_anchor_count": len(anchor_pack.get("fact_anchors") or []),
        "evidence_group_count": len(evidence_group_pack.get("groups") or []),
        "accepted_grouped_evidence_count": int(
            evidence_group_pack.get("accepted_evidence_count") or 0
        ),
    }
    if writer_plan_injection_report:
        prompt_metrics["complete_plan_control_injected"] = bool(
            writer_plan_injection_report.get("complete_plan_control_injected")
        )
    _record_elapsed(timings, "prompt_build_ms", stage_started)
    if frozen_article_plan is not None:
        frozen_article_plan.assert_projection_integrity(
            content_plan=repaired_plan,
            selection=selection,
            fact_anchor_pack=anchor_pack,
        )

    artifact_started = time.perf_counter()
    paths = {
        **planning_artifact_paths,
        "retrieval_trace": _flow3_v33_write_json(
            root / "retrieval_trace.json", retrieval_trace,
        ),
        "content_plan_before": _flow3_v33_write_json(root / "content_plan_before.json", base_plan),
        "content_plan_after": _flow3_v33_write_json(root / "content_plan_after.json", repaired_plan),
        "selected_evidence_local": _flow3_v33_write_json(root / "selected_evidence_local.json", selection["selected_evidence_local"]),
        "analysis_support_pack": _flow3_v33_write_json(root / "analysis_support_pack.json", anchor_pack["analysis_support_pack"]),
        "fact_anchor_pack": _flow3_v33_write_json(root / "fact_anchor_pack.json", fact_anchor_outbound),
        "fact_anchor_outbound": _flow3_v33_write_json(root / "fact_anchor_outbound.json", fact_anchor_outbound),
        "fact_source_support": _flow3_v33_write_json(root / "fact_source_support.json", fact_source_support),
        "evidence_group_pack": _flow3_v33_write_json(root / "evidence_group_pack.json", evidence_group_pack),
        "writer_prompt_metrics": _flow3_v33_write_json(root / "writer_prompt_metrics.json", prompt_metrics),
        "source_span_dedup_report": _flow3_v33_write_json(root / "source_span_dedup_report.json", anchor_pack["source_span_dedup_report"]),
        "reference_structure": _flow3_v33_write_json(root / "reference_structure.json", reference_structure),
        "reference_structure_quality_report": _flow3_v33_write_json(
            root / "reference_structure_quality_report.json", reference_structure_report,
        ),
        "atomic_evidence_quality_report": _flow3_v33_write_json(root / "atomic_evidence_quality_report.json", atomic_report),
        "content_plan_quality_report": _flow3_v33_write_json(root / "content_plan_quality_report.json", plan_report),
        "evidence_selection_report": _flow3_v33_write_json(root / "evidence_selection_report.json", selection_report),
        "fact_anchor_quality_report": _flow3_v33_write_json(root / "fact_anchor_quality_report.json", anchor_report),
        "writing_structure_overlay": _flow3_v33_write_json(
            root / "writing_structure_overlay.json",
            writing_structure_overlay,
        ),
        "writing_style_guidance_report": _flow3_v33_write_json(
            root / "writing_style_guidance_report.json",
            writing_style_guidance_report,
        ),
        "writing_style_plan_overlay_report": _flow3_v33_write_json(
            root / "writing_style_plan_overlay_report.json",
            writing_style_plan_overlay_report,
        ),
        "fact_anchor_distribution_integrity_report": _flow3_v33_write_json(
            root / "fact_anchor_distribution_integrity_report.json",
            fact_anchor_distribution_integrity_report,
        ),
    }
    shadow_component = "article_outline"
    try:
        if article_outline_result is not None:
            paths["article_outline"] = _flow3_v33_write_json(
                root / "article_outline.json",
                article_outline_result.outline.model_dump(mode="json"),
            )
            shadow_component = "outline_projection_map"
            paths["outline_projection_map"] = _flow3_v33_write_json(
                root / "outline_projection_map.json",
                article_outline_result.outline.mapping_report.model_dump(mode="json"),
            )
            shadow_component = "article_outline_readable"
            atomic_write_text(
                root / "article_outline_readable.txt",
                article_outline_result.readable_outline,
                encoding="utf-8",
            )
            paths["article_outline_readable"] = str(
                (root / "article_outline_readable.txt").resolve()
            )
        if dynamic_budget_plan is not None:
            shadow_component = "dynamic_budget"
            paths["dynamic_budget"] = _flow3_v33_write_json(
                root / "dynamic_budget.json",
                dynamic_budget_plan.model_dump(mode="json"),
            )
        shadow_component = "outline_contract_generation_report"
        paths["outline_contract_generation_report"] = _flow3_v33_write_json(
            root / "outline_contract_generation_report.json",
            outline_contract_generation_report,
        )
    except Exception as exc:
        outline_contract_generation_report = (
            _unexpected_m2_shadow_failure_report(
                exc,
                stage="persistence",
                component=shadow_component,
            )
        )
        logger.warning(
            "M2 shadow generation failed: error_type=%s stage=%s component=%s",
            type(exc).__name__,
            "persistence",
            shadow_component,
        )
        try:
            paths["outline_contract_generation_report"] = _flow3_v33_write_json(
                root / "outline_contract_generation_report.json",
                outline_contract_generation_report,
            )
        except Exception as report_exc:
            logger.warning(
                "M2 shadow diagnostic persistence failed: error_type=%s "
                "stage=%s component=%s",
                type(report_exc).__name__,
                "persistence",
                "outline_contract_generation_report",
            )
    if writer_plan_injection_report:
        paths["writer_plan_injection_report"] = _flow3_v33_write_json(
            root / "writer_plan_injection_report.json",
            writer_plan_injection_report,
        )
    if writing_style_guidance_pack:
        paths["writing_style_guidance_pack"] = _flow3_v33_write_json(
            root / "writing_style_guidance_pack.json",
            writing_style_guidance_pack,
        )
    atomic_write_text(root / "prompt.txt", prompt, encoding="utf-8")
    paths["prompt"] = str((root / "prompt.txt").resolve())
    _record_elapsed(timings, "artifact_save_ms", artifact_started)
    timings["preflight_local_total_ms"] = round(
        (time.perf_counter() - preflight_started) * 1000,
        3,
    )
    preflight_status = (
        "BLOCKED_INVALID_CONTENT_PLAN" if not plan_report["passed"] else
        "BLOCKED_INVALID_EVIDENCE_SELECTION" if not selection_report["passed"] else
        "BLOCKED_INVALID_FACT_ANCHORS" if not anchor_report["passed"] else
        "BLOCKED_EXTERNAL_AUTHORIZATION"
    )
    preflight_summary = {
        "flow3_v33_status": preflight_status,
        "atomic_evidence_count": len(evidence_pack.get("evidence") or []),
        "selected_evidence_count": selection.get("selected_evidence_count", 0),
        "fact_anchorable_count": anchor_report.get("fact_anchorable_count", 0),
        "analysis_support_only_count": anchor_report.get("analysis_support_only_count", 0),
        "section_anchor_counts": anchor_report.get("section_anchor_counts", {}),
        "incomplete_anchor_count": anchor_report.get("incomplete_anchor_count", 0),
        "source_span_cross_section_conflict_count": anchor_report.get("source_span_cross_section_conflict_count", 0),
        "source_span_multi_core_conflict_count": anchor_report.get("source_span_multi_core_conflict_count", 0),
        "section_goal_mismatch_count": anchor_report.get("section_goal_mismatch_count", 0),
        "analysis_support_mismatch_count": anchor_report.get("analysis_support_mismatch_count", 0),
        "reference_structure_status": reference_structure.get("status"),
        "reference_structure_section_count": len(reference_structure.get("primary_structure") or []),
        "retrieval_trace_redacted": bool(retrieval_trace.get("redacted")),
        "retrieval_selected_chunk_count": int(
            retrieval_trace.get("selected_chunk_count") or 0
        ),
        "writing_structure_overlay_applied": bool(
            writing_structure_overlay.get("sections")
        ),
        "writing_style_plan_overlay_applied": bool(
            writing_style_plan_overlay_report.get("applied")
        ),
        "subsection_counts": {
            str(section.get("section_id") or ""): len(
                section.get("subsections") or []
            )
            for section in writing_structure_overlay.get("sections") or []
        },
        "fact_anchor_distribution_integrity_status": (
            fact_anchor_distribution_integrity_report[
                "integrity_status"
            ]
        ),
        "writing_style_library_enabled": bool(
            writing_style_guidance_report.get("enabled")
        ),
        "writing_style_library_loaded": bool(
            writing_style_guidance_report.get("loaded")
        ),
        "planning_owner": (
            planning_engine_summary.get("planning_owner")
            if planning_engine_summary else "legacy_non_work_summary_planning"
        ),
        "frozen_plan_sha256": (
            frozen_article_plan.frozen_plan_sha256
            if frozen_article_plan is not None else None
        ),
        "article_outline_status": outline_contract_generation_report[
            "generation_status"
        ],
        "article_outline_sha": outline_contract_generation_report.get(
            "outline_sha"
        ),
        "dynamic_budget_sha": outline_contract_generation_report.get(
            "budget_sha"
        ),
        "planning_engine_ms": float(timings.get("planning_engine_ms") or 0.0),
        "target_total": repaired_plan.get("target_total"),
        "introduction_required": bool(introduction_contract["required"]),
        "model_call_count": 0,
        "writer_prompt_metrics": prompt_metrics,
        "model_retry_count": 0,
        "model_retry_observed": False,
        "case2_or_case3_run": False,
        "flow4_calls": 0,
        "timings": dict(timings),
    }
    paths["preflight_summary"] = _flow3_v33_write_json(root / "preflight_summary.json", preflight_summary)
    prepared = {
        "flow3_v33_status": preflight_status,
        "task": task, "request": request, "retrieval_result": retrieval,
        "evidence_pack": evidence_pack, "atomic_report": atomic_report,
        "reference_structure": reference_structure,
        "reference_structure_report": reference_structure_report,
        "base_content_plan": base_plan, "content_plan": repaired_plan,
        "content_plan_report": plan_report, "selection": selection,
        "selection_report": selection_report, "fact_anchor_pack": anchor_pack,
        "fact_source_support": fact_source_support,
        "evidence_group_pack": evidence_group_pack,
        "article_outline": (
            article_outline_result.outline
            if article_outline_result is not None else None
        ),
        "dynamic_budget": dynamic_budget_plan,
        "outline_contract_generation_report": (
            outline_contract_generation_report
        ),
        "writer_plan_control": writer_plan_control,
        "writer_plan_injection_report": writer_plan_injection_report,
        "writer_prompt_metrics": prompt_metrics,
        "fact_anchor_report": anchor_report, "prompt": prompt, "paths": paths,
        "introduction_contract": introduction_contract,
        "writing_structure_overlay": writing_structure_overlay,
        "writing_style_guidance_pack": writing_style_guidance_pack,
        "writing_style_guidance_report": writing_style_guidance_report,
        "writing_style_plan_overlay_report": writing_style_plan_overlay_report,
        "fact_anchor_distribution_integrity_report": (
            fact_anchor_distribution_integrity_report
        ),
        "frozen_article_plan": frozen_article_plan,
        "planning_engine_summary": planning_engine_summary,
        "preflight_summary": preflight_summary,
        "timings": timings,
    }
    # Gate priority is fixed and independent of the order reports were computed.
    if not plan_report["passed"]:
        raise InvalidContentPlanError(plan_report)
    if not selection_report["passed"]:
        raise InvalidEvidenceSelectionError(selection_report)
    if not anchor_report["passed"]:
        raise InvalidFactAnchorsError(anchor_report)
    return prepared


def _revalidate_bound_structured_draft(
    draft: dict[str, Any],
    *,
    content_plan: dict[str, Any],
    fact_anchor_pack: dict[str, Any],
    introduction_contract: dict[str, Any] | None,
    writing_structure_overlay: dict[str, Any] | None,
    writer_plan_control: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Revalidate canonical evidence IDs without rebinding Writer unit IDs."""
    return parse_and_validate_structured_draft(
        json.dumps(draft, ensure_ascii=False),
        content_plan,
        fact_anchor_pack,
        introduction_contract,
        writing_structure_overlay,
        None,
        writer_plan_control,
    )


def _quality_return_state(
    report: dict[str, Any],
    *,
    quality_status: str,
    reason: str,
    repair_rounds: int,
) -> dict[str, Any]:
    updated = {
        **report,
        "quality_status": quality_status,
        "repair_required": False,
        "repair_rounds": repair_rounds,
        "return_reason": reason,
    }
    updated["quality_gate_summary"] = summarize_work_summary_quality_report(
        updated,
    )
    return updated


def _validate_quality_repair_writer_execution_projection(
    *,
    before_draft: dict[str, Any],
    candidate_draft: dict[str, Any],
    target_section_ids: list[str],
    prior_structured_report: dict[str, Any],
) -> dict[str, Any]:
    """Validate the Writer projection that survives canonical Evidence Binding.

    Writer execution metadata is intentionally removed by the canonical binder.
    Reapplying the raw-Writer validator to a bound draft would therefore invent
    missing-field failures.  The post-binding contract is preservation: section
    identity/order and every FACT non-text field must remain byte-equivalent.
    """

    before_section_ids = [
        str(section.get("section_id") or "")
        for section in before_draft.get("sections") or []
        if isinstance(section, dict)
    ]
    candidate_section_ids = [
        str(section.get("section_id") or "")
        for section in candidate_draft.get("sections") or []
        if isinstance(section, dict)
    ]
    bindings_unchanged = (
        fact_binding_snapshot(before_draft, target_section_ids)
        == fact_binding_snapshot(candidate_draft, target_section_ids)
    )
    prior_writer_report = dict(
        prior_structured_report.get("writer_execution_report") or {}
    )
    prior_hard_issues = list(prior_writer_report.get("hard_issues") or [])
    prior_contract_usable = bool(
        not prior_writer_report
        or prior_writer_report.get("passed")
        or str(prior_writer_report.get("status") or "") == "NOT_APPLICABLE"
    )
    passed = bool(
        before_section_ids == candidate_section_ids
        and bindings_unchanged
        and prior_contract_usable
    )
    return {
        "schema_version": "writer-execution-bound-repair-preservation-1",
        "status": "PASS" if passed else "FAIL_WRITER_EXECUTION_PRESERVATION",
        "passed": passed,
        "validation_scope": "POST_BINDING_FROZEN_EXECUTION_PROJECTION",
        "section_identity_order_unchanged": (
            before_section_ids == candidate_section_ids
        ),
        "fact_execution_projection_unchanged": bindings_unchanged,
        "prior_writer_execution_status": str(
            prior_writer_report.get("status") or "NOT_RECORDED"
        ),
        "prior_writer_execution_hard_issue_count": len(prior_hard_issues),
        "raw_writer_metadata_reconstructed": False,
    }


def run_work_summary_commercial_quality_loop(
    *,
    task_metadata: Any,
    draft: dict[str, Any],
    prepared: dict[str, Any],
    validation_results: dict[str, Any],
    provider: Any,
    temperature: float,
) -> dict[str, Any]:
    """Orchestrate check -> bounded repair -> safety revalidation -> recheck."""

    content_plan = prepared["content_plan"]
    fact_anchor_pack = prepared["fact_anchor_pack"]
    source_support = prepared.get("fact_source_support") or {}
    selection = prepared.get("selection") or {}
    work_chain_metadata = selection.get("work_chain_pack") or {}
    capacity_metadata = (
        content_plan.get("evidence_capacity_budget")
        or selection.get("capacity_budget")
        or {}
    )
    frozen_article_plan = prepared.get("frozen_article_plan")

    def assert_frozen_integrity() -> None:
        if isinstance(frozen_article_plan, FrozenArticlePlan):
            frozen_article_plan.assert_projection_integrity(
                content_plan=content_plan,
                selection=selection,
                fact_anchor_pack=fact_anchor_pack,
            )

    def evaluate(candidate: dict[str, Any]) -> dict[str, Any]:
        return evaluate_work_summary_quality(
            task_metadata=task_metadata,
            final_structured_draft=candidate,
            rendered_final_text=render_structured_draft(candidate, content_plan),
            repaired_content_plan=content_plan,
            fact_anchor_pack=fact_anchor_pack,
            work_chain_metadata=work_chain_metadata,
            capacity_metadata=capacity_metadata,
            validation_results=validation_results,
        )

    current_draft = copy.deepcopy(draft)
    current_validation = copy.deepcopy(validation_results)
    quality_report = evaluate(current_draft)
    quality_report["repair_rounds"] = 0
    quality_report["quality_gate_summary"] = summarize_work_summary_quality_report(
        quality_report,
    )
    trace: dict[str, Any] = {
        "schema_version": "work-summary-commercial-quality-loop-1",
        "max_repair_rounds": MAX_QUALITY_REPAIR_ROUNDS,
        "repair_rounds": 0,
        "model_call_count": 0,
        "batch_calls": [],
        "before_quality_issues": copy.deepcopy(quality_report.get("issues") or []),
        "repair_targets": [],
        "after_quality_issues": copy.deepcopy(quality_report.get("issues") or []),
        "final_quality_status": str(quality_report.get("quality_status") or ""),
    }
    while (
        quality_report.get("quality_status") == "REPAIR_REQUIRED"
        and trace["repair_rounds"] < MAX_QUALITY_REPAIR_ROUNDS
    ):
        repair_round = int(trace["repair_rounds"]) + 1
        repair_result = execute_work_summary_quality_repair(
            draft=current_draft,
            quality_result=quality_report,
            frozen_article_plan=frozen_article_plan,
            content_plan=content_plan,
            evidence_group_pack=prepared.get("evidence_group_pack") or {},
            provider=provider,
            temperature=temperature,
            repair_round=repair_round,
        )
        repair_report = dict(repair_result.get("report") or {})
        trace["repair_rounds"] = repair_round
        trace["model_call_count"] += int(
            repair_report.get("model_call_count") or 0
        )
        trace["repair_targets"].append({
            "repair_round": repair_round,
            "section_ids": list(repair_report.get("target_section_ids") or []),
            "issue_ids": [
                str(issue.get("issue_id") or "")
                for issue in quality_report.get("issues") or []
                if issue.get("severity") == "BLOCKING"
                and issue.get("repairable")
            ],
        })
        round_trace: dict[str, Any] = {
            "repair_round": repair_round,
            "repair_report": repair_report,
            "raw_response": str(repair_result.get("raw_response") or ""),
            "revalidation": {},
        }
        trace["batch_calls"].append(round_trace)
        if repair_report.get("status") != "MERGED_PENDING_FULL_REVALIDATION":
            quality_report = _quality_return_state(
                quality_report,
                quality_status="REPAIR_FAILED",
                reason=str(repair_report.get("status") or "REPAIR_NOT_MERGED"),
                repair_rounds=repair_round,
            )
            break

        candidate = dict(repair_result["structured_draft"])
        target_ids = list(repair_report.get("target_section_ids") or [])
        bindings_before = fact_binding_snapshot(current_draft, target_ids)
        assert_frozen_integrity()
        rebound, structured_report = _revalidate_bound_structured_draft(
            candidate,
            content_plan=content_plan,
            fact_anchor_pack=fact_anchor_pack,
            introduction_contract=prepared.get("introduction_contract"),
            writing_structure_overlay=prepared.get("writing_structure_overlay"),
            # Canonical Evidence Binding has intentionally removed raw Writer
            # execution fields.  Validate their surviving projection below.
            writer_plan_control=None,
        )
        writer_execution_report = (
            _validate_quality_repair_writer_execution_projection(
                before_draft=current_draft,
                candidate_draft=rebound or candidate,
                target_section_ids=target_ids,
                prior_structured_report=current_validation.get("structured") or {},
            )
        )
        bindings_after_structured = fact_binding_snapshot(
            rebound or candidate,
            target_ids,
        )
        structured_passed = bool(
            rebound is not None
            and structured_report.get("passed")
            and writer_execution_report.get("passed")
            and bindings_after_structured == bindings_before
        )
        round_trace["revalidation"].update({
            "structured_validation": {
                "passed": bool(structured_report.get("passed")),
                "status": str(structured_report.get("flow3_v33_status") or ""),
            },
            "writer_execution_validation": {
                "passed": bool(
                    writer_execution_report.get("passed")
                ),
                "status": str(
                    writer_execution_report.get("status")
                    or "IMPORTED_FROM_STRUCTURED_VALIDATION"
                ),
            },
            "evidence_binding_validation": {
                "passed": bindings_after_structured == bindings_before
                and not int(
                    structured_report.get("invalid_evidence_reference_count") or 0
                ),
                "invalid_evidence_reference_count": int(
                    structured_report.get("invalid_evidence_reference_count") or 0
                ),
                "fact_bindings_unchanged": (
                    bindings_after_structured == bindings_before
                ),
            },
        })
        if not structured_passed:
            round_trace["revalidation"].update({
                "analysis_validation": {"passed": False, "status": "NOT_RUN_STRUCTURED_REJECT"},
                "fact_safety": {"passed": False, "status": "NOT_RUN_STRUCTURED_REJECT"},
            })
            quality_report = _quality_return_state(
                quality_report,
                quality_status="REPAIR_FAILED",
                reason="POST_REPAIR_STRUCTURED_OR_BINDING_REVALIDATION_FAILED",
                repair_rounds=repair_round,
            )
            break

        assert rebound is not None
        safe_candidate, fact_report, safe_repair_report = apply_fact_safety_gate(
            rebound,
            fact_anchor_pack,
            source_support,
            content_plan=content_plan,
            frozen_plan_integrity_check=assert_frozen_integrity,
            frozen_plan_sha256=(
                frozen_article_plan.frozen_plan_sha256
                if isinstance(frozen_article_plan, FrozenArticlePlan)
                else ""
            ),
        )
        analysis_report = validate_analysis_sentences(safe_candidate)
        final_structured_draft, final_structured_report = (
            _revalidate_bound_structured_draft(
                safe_candidate,
                content_plan=content_plan,
                fact_anchor_pack=fact_anchor_pack,
                introduction_contract=prepared.get("introduction_contract"),
                writing_structure_overlay=prepared.get("writing_structure_overlay"),
                writer_plan_control=None,
            )
        )
        final_writer_execution_report = (
            _validate_quality_repair_writer_execution_projection(
                before_draft=current_draft,
                candidate_draft=final_structured_draft or safe_candidate,
                target_section_ids=target_ids,
                prior_structured_report=current_validation.get("structured") or {},
            )
        )
        final_structured_report["writer_execution_report"] = (
            final_writer_execution_report
        )
        bindings_after_safety = fact_binding_snapshot(
            final_structured_draft or safe_candidate,
            target_ids,
        )
        fact_safety_passed = bool(
            fact_report.get("passed")
            and analysis_report.get("passed")
            and final_structured_draft is not None
            and final_structured_report.get("passed")
            and final_writer_execution_report.get("passed")
            and bindings_after_safety == bindings_before
        )
        round_trace["revalidation"].update({
            "analysis_validation": {
                "passed": bool(analysis_report.get("passed")),
                "status": str(analysis_report.get("flow3_v33_status") or ""),
                "analysis_fact_leak_count": int(
                    analysis_report.get("analysis_fact_leak_count") or 0
                ),
            },
            "fact_safety": {
                "passed": bool(fact_report.get("passed")),
                "status": str(
                    fact_report.get("fact_safety_status")
                    or fact_report.get("flow3_v33_status")
                    or ""
                ),
                "unsupported_count": int(fact_report.get("unsupported_count") or 0),
                "safe_repair_report": safe_repair_report,
            },
            "final_structured_validation": {
                "passed": bool(final_structured_report.get("passed")),
                "status": str(final_structured_report.get("flow3_v33_status") or ""),
            },
            "final_writer_execution_validation": {
                "passed": bool(final_writer_execution_report.get("passed")),
                "status": str(final_writer_execution_report.get("status") or ""),
                "validation_scope": str(
                    final_writer_execution_report.get("validation_scope") or ""
                ),
            },
            "final_evidence_binding_validation": {
                "passed": bindings_after_safety == bindings_before,
                "fact_bindings_unchanged": bindings_after_safety == bindings_before,
            },
        })
        if not fact_safety_passed:
            quality_report = _quality_return_state(
                quality_report,
                quality_status="REPAIR_FAILED",
                reason="POST_REPAIR_FACT_SAFETY_REVALIDATION_FAILED",
                repair_rounds=repair_round,
            )
            break

        assert final_structured_draft is not None
        current_draft = final_structured_draft
        current_validation = {
            **current_validation,
            "structured": final_structured_report,
            "fact": fact_report,
            "analysis": analysis_report,
        }
        validation_results.clear()
        validation_results.update(current_validation)
        quality_report = evaluate(current_draft)
        quality_report["repair_rounds"] = repair_round
        quality_report["quality_gate_summary"] = (
            summarize_work_summary_quality_report(quality_report)
        )
        round_trace["after_quality_status"] = str(
            quality_report.get("quality_status") or ""
        )
        round_trace["after_quality_issues"] = copy.deepcopy(
            quality_report.get("issues") or []
        )
        trace["after_quality_issues"] = copy.deepcopy(
            quality_report.get("issues") or []
        )
        assert_frozen_integrity()
        if quality_report.get("quality_status") == "PASS":
            break

    if (
        quality_report.get("blocking_issue_count")
        and int(trace["repair_rounds"]) >= MAX_QUALITY_REPAIR_ROUNDS
        and quality_report.get("quality_status") == "REPAIR_REQUIRED"
    ):
        quality_report = _quality_return_state(
            quality_report,
            quality_status="REVIEW_REQUIRED",
            reason="MAX_QUALITY_REPAIR_ROUNDS_EXHAUSTED",
            repair_rounds=int(trace["repair_rounds"]),
        )
    quality_report["repair_rounds"] = int(trace["repair_rounds"])
    quality_report["quality_gate_summary"] = summarize_work_summary_quality_report(
        quality_report,
    )
    trace["after_quality_issues"] = copy.deepcopy(
        quality_report.get("issues") or []
    )
    trace["final_quality_status"] = str(
        quality_report.get("quality_status") or ""
    )
    return {
        "structured_draft": current_draft,
        "validation_results": current_validation,
        "quality_report": quality_report,
        "repair_trace": trace,
        "repair_rounds": int(trace["repair_rounds"]),
        "model_call_count": int(trace["model_call_count"]),
    }


def generate_flow3_v33_structured_draft(
    request: ArticleGenRequest,
    *,
    artifact_root: Path,
    provider: Any | None = None,
    search_fn: Any = search_detailed,
    retrieval_payload: dict[str, Any] | None = None,
    external_authorized: bool = False,
    prepared: dict[str, Any] | None = None,
    quality_test_mode: bool = False,
    structured_repair_decider: Any | None = None,
    structured_model_repairer: Any | None = None,
    writer_mode: str = LEGACY_WRITER,
    section_writer_local_fixture: bool = False,
    section_writer_limits: Any | None = None,
) -> dict[str, Any]:
    """One-call structured generation followed by local validation and rendering."""
    if select_writer_mode(writer_mode) == SECTION_WRITER_EXPERIMENTAL:
        # Explicit service-only route: never invoke RAG, Legacy, expansion or
        # model repair here. The commercial API keeps its default Legacy call.
        if prepared is None or provider is None:
            raise SectionWriterContractError("FROZEN_SECTION_WRITER_INPUT_REQUIRED")
        from app.services.generation.section_writer_integration import generate_section_writer_experimental
        return generate_section_writer_experimental(
            prepared=prepared, provider=provider, artifact_root=artifact_root,
            temperature=request.temperature if request.temperature is not None else 0.7,
            external_authorized=external_authorized,
            local_fixture=section_writer_local_fixture, limits=section_writer_limits,
        )
    root = Path(artifact_root)
    prepared = prepared or prepare_flow3_v33_structured_generation(
        request, artifact_root=root, search_fn=search_fn, retrieval_payload=retrieval_payload,
    )
    timings = prepared.setdefault("timings", _new_generation_timings())
    active_task = prepared.get("task") or build_official_draft_task(request)
    prepared.setdefault(
        "writing_structure_overlay",
        build_writing_structure_overlay(
            prepared["content_plan"],
            active_task.document_type,
        ),
    )
    prepared.setdefault(
        "fact_anchor_distribution_integrity_report",
        _fact_anchor_distribution_integrity_report(
            _fact_anchor_distribution_snapshot(
                prepared["fact_anchor_pack"],
                [
                    str(section.get("section_id") or "")
                    for section in prepared[
                        "content_plan"
                    ].get("sections") or []
                ],
            ),
            _fact_anchor_distribution_snapshot(
                prepared["fact_anchor_pack"],
                [
                    str(section.get("section_id") or "")
                    for section in prepared[
                        "content_plan"
                    ].get("sections") or []
                ],
            ),
        ),
    )
    prepared.setdefault("preflight_summary", {})
    prepared["preflight_summary"].setdefault(
        "subsection_counts",
        {
            str(section.get("section_id") or ""): len(
                section.get("subsections") or []
            )
            for section in prepared[
                "writing_structure_overlay"
            ].get("sections") or []
        },
    )
    prepared.setdefault(
        "introduction_contract",
        build_introduction_contract(
            "",
            int(
                prepared["content_plan"].get("target_total")
                or active_task.target_words
            ),
            list(prepared["content_plan"].get("sections") or []),
        ),
    )
    frozen_article_plan = prepared.get("frozen_article_plan")
    if isinstance(frozen_article_plan, FrozenArticlePlan):
        frozen_article_plan.assert_projection_integrity(
            content_plan=prepared["content_plan"],
            selection=prepared["selection"],
            fact_anchor_pack=prepared["fact_anchor_pack"],
        )
    if not external_authorized:
        manifest = {
            "flow3_v33_status": "BLOCKED_EXTERNAL_AUTHORIZATION", "model_call_count": 0,
            "expansion_triggered": False, "expansion_status": "NOT_RUN",
            "initial_draft_length": 0, "merged_draft_length": 0,
            "final_draft_length": 0,
            "case2_or_case3_run": False, "flow4_calls": 0, "entered_postprocessing": False,
            "paths": prepared["paths"],
            "writing_structure_overlay_applied": bool(
                prepared.get("writing_structure_overlay", {}).get(
                    "sections"
                )
            ),
            "subsection_structure_status": "NOT_RUN",
            "subsection_counts": prepared["preflight_summary"].get(
                "subsection_counts",
                {},
            ),
            "fact_anchor_distribution_integrity_status": prepared[
                "fact_anchor_distribution_integrity_report"
            ]["integrity_status"],
        }
        save_runtime_summary(root, manifest=manifest, summary=manifest)
        raise ExternalAuthorizationRequired(prepared)

    active_provider = provider or get_llm_provider()
    retry_counter = _LLMRetryCounter()
    provider_logger = logging.getLogger("app.services.llm.openai_compatible_provider")
    provider_logger.addHandler(retry_counter)
    stage_started = time.perf_counter()
    try:
        raw_response = active_provider.generate_text(
            prompt=prepared["prompt"],
            system_prompt="只输出严格符合协议的一个JSON对象。",
            temperature=request.temperature if request.temperature is not None else 0.7,
        )
    finally:
        _record_elapsed(timings, "model_call_ms", stage_started)
        provider_logger.removeHandler(retry_counter)
    artifact_started = time.perf_counter()
    raw_path = save_raw_response(root, str(raw_response))
    flow_candidates = LastKnownGoodArticle()
    raw_candidate = flow_candidates.preserve_raw(
        str(raw_response),
        stage="WRITER_RAW_RESPONSE",
    )
    _record_elapsed(timings, "artifact_save_ms", artifact_started)
    stage_started = time.perf_counter()
    draft, structured_report = parse_and_validate_structured_draft(
        str(raw_response), prepared["content_plan"], prepared["fact_anchor_pack"],
        prepared["introduction_contract"],
        prepared.get("writing_structure_overlay"),
        prepared.get("evidence_group_pack"),
        prepared.get("writer_plan_control"),
    )
    _record_elapsed(timings, "structured_parse_ms", stage_started)
    structured_repair_check = classify_structured_repair(
        draft,
        structured_report,
    )
    repair_action = (
        str(structured_repair_decider(structured_repair_check))
        if callable(structured_repair_decider)
        else (
            "CONTINUE"
            if structured_repair_check["validation_status"] == "PASS"
            else "LEGACY_RAISE"
        )
    )
    structured_repair_result: dict[str, Any] | None = None
    structured_repair_result_path: str | None = None
    structured_repair_trace_paths: dict[str, str] = {}

    def _call_structured_model_repair(repair_input: dict[str, Any]) -> str:
        if callable(structured_model_repairer):
            return str(structured_model_repairer(repair_input))
        repair_prompt = build_structured_execution_repair_prompt(repair_input)
        call_index = len(structured_repair_trace_paths) // 2 + 1
        section_id = re.sub(
            r"[^A-Za-z0-9_-]",
            "_",
            str(repair_input.get("section_id") or f"SECTION-{call_index}"),
        )
        prompt_key = f"structured_repair_prompt_{call_index:02d}_{section_id}"
        raw_key = f"structured_repair_raw_{call_index:02d}_{section_id}"
        prompt_path = root / f"{prompt_key}.txt"
        raw_repair_path = root / f"{raw_key}.txt"
        atomic_write_text(prompt_path, repair_prompt, encoding="utf-8")
        structured_repair_trace_paths[prompt_key] = str(prompt_path.resolve())
        model_started = time.perf_counter()
        try:
            repair_response = active_provider.generate_text(
                prompt=repair_prompt,
                system_prompt=(
                    "只输出严格符合协议的一个JSON对象；只返回当前失败章节。"
                ),
                temperature=(
                    request.temperature
                    if request.temperature is not None
                    else 0.7
                ),
            )
        finally:
            _record_elapsed(timings, "model_call_ms", model_started)
        atomic_write_text(
            raw_repair_path,
            str(repair_response),
            encoding="utf-8",
        )
        structured_repair_trace_paths[raw_key] = str(
            raw_repair_path.resolve()
        )
        return str(repair_response)

    if structured_repair_check["validation_status"] == "PASS":
        repair_outcome = run_structured_execution_repair(
            raw_response=str(raw_response),
            initial_draft=draft,
            initial_report=structured_report,
            content_plan=prepared["content_plan"],
            fact_anchor_pack=prepared["fact_anchor_pack"],
            introduction_contract=prepared["introduction_contract"],
            writing_structure_overlay=prepared.get(
                "writing_structure_overlay"
            ),
            evidence_group_pack=prepared.get("evidence_group_pack"),
            writer_plan_control=prepared.get("writer_plan_control"),
            frozen_article_plan=prepared.get("frozen_article_plan"),
        )
    elif repair_action == "REPAIR":
        repair_outcome = run_structured_execution_repair(
            raw_response=str(raw_response),
            initial_draft=draft,
            initial_report=structured_report,
            content_plan=prepared["content_plan"],
            fact_anchor_pack=prepared["fact_anchor_pack"],
            introduction_contract=prepared["introduction_contract"],
            writing_structure_overlay=prepared.get(
                "writing_structure_overlay"
            ),
            evidence_group_pack=prepared.get("evidence_group_pack"),
            writer_plan_control=prepared.get("writer_plan_control"),
            frozen_article_plan=prepared.get("frozen_article_plan"),
            model_repairer=_call_structured_model_repair,
        )
    elif structured_repair_check["validation_status"] == "FAIL_UNREPAIRABLE":
        repair_outcome = run_structured_execution_repair(
            raw_response=str(raw_response),
            initial_draft=draft,
            initial_report=structured_report,
            content_plan=prepared["content_plan"],
            fact_anchor_pack=prepared["fact_anchor_pack"],
            introduction_contract=prepared["introduction_contract"],
            writing_structure_overlay=prepared.get(
                "writing_structure_overlay"
            ),
            evidence_group_pack=prepared.get("evidence_group_pack"),
            writer_plan_control=prepared.get("writer_plan_control"),
            frozen_article_plan=prepared.get("frozen_article_plan"),
        )
    else:
        repair_outcome = None

    if repair_outcome is not None:
        draft = repair_outcome["structured_draft"]
        structured_report = repair_outcome["structured_report"]
        structured_repair_result = repair_outcome["repair_result"]
        structured_repair_result["check_decision"] = {
            **structured_repair_check,
            "orchestrator_action": repair_action,
        }
        recheck_decision = {
            "phase": "RECHECK",
            "validation_status": (
                "PASS"
                if structured_report.get("passed")
                else "FAIL_UNREPAIRED"
            ),
            "repair_status": structured_repair_result["repair_status"],
            "recommended_action": (
                "CONTINUE"
                if structured_report.get("passed")
                else "RETURN_UNREPAIRED_STATE"
            ),
            "article_exists": structured_repair_result["article_exists"],
            "article_recoverable": structured_repair_result[
                "article_recoverable"
            ],
        }
        recheck_action = (
            str(structured_repair_decider(recheck_decision))
            if callable(structured_repair_decider)
            else recheck_decision["recommended_action"]
        )
        structured_repair_result["recheck_decision"] = {
            **recheck_decision,
            "orchestrator_action": recheck_action,
        }
        structured_repair_result_path = _flow3_v33_write_json(
            root / "structured_execution_repair_result.json",
            structured_repair_result,
        )
    else:
        recheck_action = "LEGACY_RAISE"
    subsection_structure_report = structured_report.get(
        "subsection_structure_report",
        {
            "overlay_applied": bool(
                prepared.get("writing_structure_overlay", {}).get(
                    "sections"
                )
            ),
            "structure_status": "INVALID",
        },
    )
    subsection_report_path = _flow3_v33_write_json(
        root / "subsection_structure_validation_report.json",
        subsection_structure_report,
    )
    writer_execution_report = structured_report.get(
        "writer_execution_report",
        {
            "schema_version": "writer-execution-report-1",
            "status": "INVALID_STRUCTURED_OUTPUT",
            "passed": False,
            "executed_sections": [],
            "executed_plan_steps": [],
            "executed_writing_focus": [],
            "executed_expansion_path": [],
            "unexecuted_items": [],
            "risk_items": [],
        },
    )
    writer_execution_report_path = _flow3_v33_write_json(
        root / "writer_execution_report.json",
        writer_execution_report,
    )
    parsed_path = save_parsed_structured_draft(root, draft or {})
    not_run_fact = {"flow3_v33_status": "NOT_RUN", "passed": False, "reason": "structured_output_failed"}
    not_run_analysis = {"flow3_v33_status": "NOT_RUN", "passed": False, "reason": "structured_output_failed"}
    if not structured_report["passed"] and (
        callable(structured_repair_decider)
        or not quality_test_mode
        or draft is None
    ):
        report_paths = save_v33_validation_reports(
            root, structured_report=structured_report,
            fact_report=not_run_fact, analysis_report=not_run_analysis,
        )
        unrepaired_state = callable(structured_repair_decider)
        initial_draft_length = (
            len(render_draft_for_length(draft, prepared["content_plan"]))
            if isinstance(draft, dict)
            else 0
        )
        manifest = {
            "flow3_v33_status": (
                "RETURN_UNREPAIRED_STATE"
                if unrepaired_state
                else "FAIL_STRUCTURED_OUTPUT"
            ),
            "model_call_count": (
                1
                + int(
                    (structured_repair_result or {}).get("model_call_count")
                    or 0
                )
            ),
            "expansion_triggered": False, "expansion_status": "NOT_RUN",
            "initial_draft_length": initial_draft_length,
            "merged_draft_length": initial_draft_length,
            "final_draft_length": initial_draft_length,
            "raw_response_saved_before_checks": True, "rendered": False,
            "case2_or_case3_run": False, "flow4_calls": 0, "entered_postprocessing": False,
            "paths": {**prepared["paths"], "raw_structured_response": raw_path,
                      "parsed_structured_draft": parsed_path,
                      "subsection_structure_validation_report": subsection_report_path,
                      "writer_execution_report": writer_execution_report_path,
                      **(
                          {
                              "structured_execution_repair_result": (
                                  structured_repair_result_path
                              )
                          }
                          if structured_repair_result_path else {}
                      ),
                      **structured_repair_trace_paths,
                      **report_paths},
            "structured_repair_status": str(
                (structured_repair_result or {}).get("repair_status")
                or "NOT_ATTEMPTED"
            ),
            "structured_repair_round": int(
                (structured_repair_result or {}).get("repair_round") or 0
            ),
            "structured_repair_next_step": str(
                (structured_repair_result or {}).get("next_step")
                or "RETURN_UNREPAIRED_STATE"
            ),
            "article_exists": bool(
                (structured_repair_result or {}).get("article_exists")
                or raw_candidate is not None
            ),
            "article_recoverable": bool(
                (structured_repair_result or {}).get("article_recoverable")
                or raw_candidate is not None
            ),
            "candidate_versions": flow_candidates.lineage_summary(),
            "writing_structure_overlay_applied": bool(
                subsection_structure_report.get("overlay_applied")
            ),
            "subsection_structure_status": str(
                subsection_structure_report.get("structure_status")
                or "INVALID"
            ),
            "subsection_counts": prepared["preflight_summary"].get(
                "subsection_counts",
                {},
            ),
            "fact_anchor_distribution_integrity_status": prepared[
                "fact_anchor_distribution_integrity_report"
            ]["integrity_status"],
        }
        save_runtime_summary(root, manifest=manifest, summary=manifest)
        if unrepaired_state:
            binding = (
                flow_candidates.bind_report(
                    raw_candidate.candidate_id,
                    "STRUCTURE",
                    structured_report,
                )
                if raw_candidate is not None
                else None
            )
            return {
                **prepared,
                **manifest,
                "structured_draft": draft or {},
                "structured_report": structured_report,
                "structured_repair_result": structured_repair_result or {},
                "rendered_draft": raw_candidate.content if raw_candidate else "",
                "recoverable_candidate": raw_candidate,
                "candidate_report_bindings": (
                    [asdict(binding)] if binding is not None else []
                ),
                "issue_route": route_issue(
                    "FAIL_STRUCTURED_OUTPUT",
                    candidate_exists=raw_candidate is not None,
                ).value,
            }
        raise StructuredOutputError(structured_report, str(root.resolve()))

    assert draft is not None
    writer_call_policy = work_summary_writer_call_policy(active_task)
    single_call_work_summary = bool(writer_call_policy["single_writer_call"])
    if structured_report["passed"] and not single_call_work_summary:
        expansion_result = execute_one_shot_section_expansion(
            task=active_task,
            draft=draft,
            content_plan=prepared["content_plan"],
            fact_anchor_pack=prepared["fact_anchor_pack"],
            provider=active_provider,
            artifact_root=root,
            temperature=(
                request.temperature if request.temperature is not None else 0.7
            ),
            introduction_contract=prepared["introduction_contract"],
        )
    else:
        initial_length = len(
            render_draft_for_length(draft, prepared["content_plan"])
        )
        expansion_result = {
            "structured_draft": draft,
            "model_call_count": 0,
            "report": {
                "expansion_triggered": False,
                "initial_total_length": initial_length,
                "initial_section_lengths": {},
                "target_total": int(
                    prepared["content_plan"].get("target_total")
                    or active_task.target_words
                ),
                "target_range": derive_target_length_range(
                    int(
                        prepared["content_plan"].get("target_total")
                        or active_task.target_words
                    )
                ),
                "deficient_sections": [],
                "section_deficits": {},
                "expansion_model_call_count": 0,
                "expanded_section_lengths": {},
                "merged_total_length": initial_length,
                "merged_section_lengths": {},
                "duplicate_rejection_count": 0,
                "invalid_section_count": 0,
                "invalid_evidence_id_count": 0,
                "expansion_status": (
                    "NOT_RUN_SINGLE_CALL_WORK_SUMMARY"
                    if single_call_work_summary and structured_report["passed"]
                    else "NOT_RUN_INVALID_INITIAL_STRUCTURE"
                ),
            },
            "paths": {},
        }
    draft = expansion_result["structured_draft"]
    model_call_count = (
        1
        + int((structured_repair_result or {}).get("model_call_count") or 0)
        + int(expansion_result["model_call_count"])
    )
    expansion_report = expansion_result["report"]
    expansion_status = str(expansion_report["expansion_status"])
    introduction_quality_report = dict(
        expansion_report.get("introduction_quality_report") or {}
    )
    stage_started = time.perf_counter()
    draft, structured_report = _revalidate_bound_structured_draft(
        draft,
        content_plan=prepared["content_plan"],
        fact_anchor_pack=prepared["fact_anchor_pack"],
        introduction_contract=prepared["introduction_contract"],
        writing_structure_overlay=prepared.get("writing_structure_overlay"),
        writer_plan_control=prepared.get("writer_plan_control"),
    )
    _record_elapsed(timings, "structured_parse_ms", stage_started)
    assert draft is not None
    subsection_structure_report = structured_report.get(
        "subsection_structure_report",
        subsection_structure_report,
    )
    subsection_report_path = _flow3_v33_write_json(
        root / "subsection_structure_validation_report.json",
        subsection_structure_report,
    )
    writer_execution_report = structured_report.get(
        "writer_execution_report",
        writer_execution_report,
    )
    writer_execution_report_path = _flow3_v33_write_json(
        root / "writer_execution_report.json",
        writer_execution_report,
    )
    artifact_started = time.perf_counter()
    original_parsed_path = _flow3_v33_write_json(
        root / "parsed_structured_draft_before_fact_safety.json",
        draft,
    )
    writer_density_observation = observe_writer_density(
        draft,
        prepared["content_plan"],
    )
    writer_density_observation_path = _flow3_v33_write_json(
        root / "writer_density_observation.json",
        writer_density_observation,
    )
    _record_elapsed(timings, "artifact_save_ms", artifact_started)
    frozen_article_plan = prepared.get("frozen_article_plan")
    if isinstance(frozen_article_plan, FrozenArticlePlan):
        frozen_article_plan.assert_projection_integrity(
            content_plan=prepared["content_plan"],
            selection=prepared["selection"],
            fact_anchor_pack=prepared["fact_anchor_pack"],
        )
    frozen_integrity_check = None
    frozen_plan_sha256 = ""
    if isinstance(frozen_article_plan, FrozenArticlePlan):
        frozen_plan_sha256 = frozen_article_plan.frozen_plan_sha256

        def frozen_integrity_check() -> None:
            frozen_article_plan.assert_projection_integrity(
                content_plan=prepared["content_plan"],
                selection=prepared["selection"],
                fact_anchor_pack=prepared["fact_anchor_pack"],
            )

    draft, fact_report, safe_repair_report = apply_fact_safety_gate(
        draft,
        prepared["fact_anchor_pack"],
        prepared.get("fact_source_support") or {},
        content_plan=prepared["content_plan"],
        frozen_plan_integrity_check=frozen_integrity_check,
        frozen_plan_sha256=frozen_plan_sha256,
    )
    initial_evidence_safety_report = dict(
        safe_repair_report.get("evidence_safety_report") or {}
    )
    section_rewrite_result = (
        {
            "structured_draft": draft,
            "raw_response": "",
            "report": {
                "attempted": False,
                "model_call_count": 0,
                "status": "NOT_RUN_SINGLE_CALL_WORK_SUMMARY",
            },
        }
        if single_call_work_summary
        else execute_one_shot_section_rewrite(
            draft=draft,
            evidence_safety_report=initial_evidence_safety_report,
            fact_anchor_pack=prepared["fact_anchor_pack"],
            source_support=prepared.get("fact_source_support") or {},
            provider=active_provider,
            temperature=(
                request.temperature if request.temperature is not None else 0.7
            ),
        )
    )
    section_rewrite_report = dict(section_rewrite_result["report"])
    section_rewrite_raw_path: str | None = None
    if section_rewrite_report.get("attempted"):
        model_call_count += int(section_rewrite_report.get("model_call_count") or 0)
        section_rewrite_raw_path = _flow3_v33_write_json(
            root / "section_rewrite_raw_response.json",
            {"raw_response": section_rewrite_result.get("raw_response") or ""},
        )
    if section_rewrite_report.get("status") == "MERGED_PENDING_FULL_REVALIDATION":
        if frozen_integrity_check is not None:
            frozen_integrity_check()
        rewritten_draft, rewritten_structured_report = (
            _revalidate_bound_structured_draft(
                section_rewrite_result["structured_draft"],
                content_plan=prepared["content_plan"],
                fact_anchor_pack=prepared["fact_anchor_pack"],
                introduction_contract=prepared["introduction_contract"],
                writing_structure_overlay=prepared.get(
                    "writing_structure_overlay"
                ),
                writer_plan_control=prepared.get("writer_plan_control"),
            )
        )
        if rewritten_draft is not None:
            draft = rewritten_draft
        structured_report = rewritten_structured_report
        writer_execution_report = structured_report.get(
            "writer_execution_report",
            writer_execution_report,
        )
        writer_execution_report_path = _flow3_v33_write_json(
            root / "writer_execution_report.json",
            writer_execution_report,
        )
        draft, fact_report, safe_repair_report = apply_fact_safety_gate(
            draft,
            prepared["fact_anchor_pack"],
            prepared.get("fact_source_support") or {},
            content_plan=prepared["content_plan"],
            frozen_plan_integrity_check=frozen_integrity_check,
            frozen_plan_sha256=frozen_plan_sha256,
        )
        final_evidence_safety_report = dict(
            safe_repair_report.get("evidence_safety_report") or {}
        )
        section_rewrite_report.update({
            "status": (
                "PASS"
                if structured_report.get("passed") and fact_report.get("passed")
                else "BLOCKED_AFTER_REVALIDATION"
            ),
            "structured_validation_status": str(
                structured_report.get("flow3_v33_status") or ""
            ),
            "structured_validation_passed": bool(
                structured_report.get("passed")
            ),
            "evidence_safety_status": str(
                final_evidence_safety_report.get("status") or ""
            ),
            "evidence_safety_passed": bool(fact_report.get("passed")),
            "frozen_plan_sha256_before": frozen_plan_sha256,
            "frozen_plan_sha256_after": frozen_plan_sha256,
            "frozen_plan_sha256_unchanged": True,
        })
        safe_repair_report["section_rewrite_recovery"] = section_rewrite_report
        safe_repair_report["initial_evidence_safety_report"] = (
            initial_evidence_safety_report
        )
        if frozen_integrity_check is not None:
            frozen_integrity_check()
    timings["fact_validation_ms"] = round(
        float(timings.get("fact_validation_ms") or 0.0)
        + float(safe_repair_report.get("fact_validation_ms") or 0.0),
        3,
    )
    timings["safe_repair_ms"] = round(
        float(timings.get("safe_repair_ms") or 0.0)
        + float(safe_repair_report.get("safe_repair_ms") or 0.0),
        3,
    )
    artifact_started = time.perf_counter()
    parsed_path = save_parsed_structured_draft(root, draft)
    evidence_safety_report = dict(
        safe_repair_report.get("evidence_safety_report") or {}
    )
    evidence_safety_path = _flow3_v33_write_json(
        root / "evidence_safety_report.json",
        evidence_safety_report,
    )
    section_rewrite_report_path = _flow3_v33_write_json(
        root / "section_rewrite_report.json",
        section_rewrite_report,
    )
    locally_repaired_path: str | None = None
    if int(evidence_safety_report.get("local_repair_success_count") or 0):
        locally_repaired_path = _flow3_v33_write_json(
            root / "locally_repaired_structured_draft.json",
            draft,
        )
    safe_repair_path = _flow3_v33_write_json(
        root / "safe_repair_report.json",
        safe_repair_report,
    )
    _record_elapsed(timings, "artifact_save_ms", artifact_started)
    stage_started = time.perf_counter()
    analysis_report = validate_analysis_sentences(draft)
    _record_elapsed(timings, "analysis_validation_ms", stage_started)
    introduction_safety_issue_count = sum(
        1
        for leak in analysis_report.get("leaks") or []
        if str(leak.get("section_id") or "") == "INTRO"
    )
    if introduction_safety_issue_count:
        introduction_quality_report["safety_issue_count"] = (
            introduction_safety_issue_count
        )
        introduction_quality_report["final_status"] = "INTRODUCTION_UNSAFE"
        _flow3_v33_write_json(
            root / "introduction_quality_report.json",
            introduction_quality_report,
        )
    report_paths = save_v33_validation_reports(
        root, structured_report=structured_report,
        fact_report=fact_report, analysis_report=analysis_report,
    )
    if expansion_status in {"EXPANSION_INVALID", "EXPANSION_PARTIAL"}:
        status = expansion_status
    elif not structured_report["passed"]:
        status = "FAIL_STRUCTURED_OUTPUT"
    elif (
        fact_report.get("fact_safety_blocked")
        or int(fact_report.get("hard_block_count") or 0)
    ):
        status = "FAIL_UNSUPPORTED_FACT"
    elif not fact_report["passed"] or not analysis_report["passed"]:
        status = "REVIEW_REQUIRED_DRAFT"
    elif str(introduction_quality_report.get("final_status") or "") not in {
        "PASS",
        "NOT_REQUIRED",
    }:
        status = str(
            introduction_quality_report.get("final_status")
            or "INTRODUCTION_INVALID"
        )
    else:
        status = str(fact_report.get("flow3_v33_status") or "PASS")

    rendered = ""
    rendered_path: str | None = None
    paraphrase_report: dict[str, Any] = {"not_run": "validation failed", "hard_block": False, "risk_level": "not_run"}
    if status in {
        "PASS",
        "PASS_WITH_SAFE_REPAIR",
        "PASS_WITH_LOCAL_ISOLATION",
        "REVIEW_REQUIRED_DRAFT",
        "FAIL_UNSUPPORTED_FACT",
        "FAIL_ANALYSIS_FACT_LEAK",
    }:
        artifact_started = time.perf_counter()
        rendered = render_structured_draft(draft, prepared["content_plan"])
        rendered_path = save_rendered_draft(root, rendered)
        _record_elapsed(timings, "artifact_save_ms", artifact_started)
        paraphrase_audit_text, exempt_fact_sentence_count = (
            render_source_paraphrase_audit_text(
                draft,
                prepared["content_plan"],
            )
        )
        paraphrase_report = check_source_paraphrase_risk(
            paraphrase_audit_text,
            prepared["retrieval_result"],
        )
        paraphrase_report["audit_scope"] = "title_headings_and_analysis_sentences"
        paraphrase_report["exempt_fact_sentence_count"] = exempt_fact_sentence_count
        _flow3_v33_write_json(root / "source_paraphrase_risk.json", paraphrase_report)
        status = "FAIL_SOURCE_PARAPHRASE" if paraphrase_report.get("hard_block") else status
    else:
        _flow3_v33_write_json(root / "source_paraphrase_risk.json", paraphrase_report)

    final_candidate: ArticleCandidate | None = raw_candidate
    if str(rendered or "").strip():
        flow_candidates.update(
            title=str((draft or {}).get("title") or request.title),
            content=rendered,
            structured_draft=draft,
            stage="FACT_SAFETY_RECHECK_DRAFT",
            safety_boundary=(
                "FACT_SAFE"
                if fact_report.get("passed") and analysis_report.get("passed")
                else "REVIEW_REQUIRED"
            ),
            role=(
                CandidateRole.REPAIRED.value
                if int(safe_repair_report.get("safe_repair_count") or 0)
                else CandidateRole.PARSED.value
            ),
            parent_candidate_id=(raw_candidate.candidate_id if raw_candidate else ""),
        )
        final_candidate = flow_candidates.versions[-1]
    candidate_report_bindings = []
    if final_candidate is not None:
        for report_type, value in (
            ("STRUCTURE", structured_report),
            ("FACT_SAFETY", fact_report),
            ("ANALYSIS", analysis_report),
            ("SOURCE_PARAPHRASE", paraphrase_report),
        ):
            candidate_report_bindings.append(
                asdict(flow_candidates.bind_report(
                    final_candidate.candidate_id,
                    report_type,
                    value,
                ))
            )

    fact_safety_status = str(
        fact_report.get("fact_safety_status") or status
    )
    delivery_status = str(
        fact_report.get("delivery_status")
        or (
            "SUCCESS_WITH_REVIEW"
            if status in {
                "REVIEW_REQUIRED_DRAFT",
                "FAIL_UNSUPPORTED_FACT",
                "FAIL_ANALYSIS_FACT_LEAK",
            }
            else "SUCCESS_WITH_REPAIR"
            if status in {
                "PASS_WITH_SAFE_REPAIR",
                "PASS_WITH_LOCAL_ISOLATION",
            }
            else "SUCCESS"
        )
    )
    manifest = {
        "flow3_v33_status": status, "model": _flow3_v3_provider_model(active_provider),
        "fact_safety_status": fact_safety_status,
        "delivery_status": delivery_status,
        "model_call_count": model_call_count, "raw_response_saved_before_checks": True,
        "model_retry_count": retry_counter.count,
        "model_retry_observed": bool(retry_counter.count),
        "candidate_versions": flow_candidates.lineage_summary(),
        "selected_candidate_id": (
            final_candidate.candidate_id if final_candidate else ""
        ),
        "structured_repair_status": str(
            (structured_repair_result or {}).get("repair_status")
            or "NOT_NEEDED"
        ),
        "structured_repair_round": int(
            (structured_repair_result or {}).get("repair_round") or 0
        ),
        "structured_repair_type": str(
            (structured_repair_result or {}).get("repair_type") or "NONE"
        ),
        "structured_repair_next_step": str(
            (structured_repair_result or {}).get("next_step")
            or "CONTINUE_EVIDENCE_BINDING_AND_FACT_SAFETY"
        ),
        "writer_prompt_metrics": prepared.get("writer_prompt_metrics") or {},
        "writer_call_policy": writer_call_policy,
        "expansion_triggered": bool(expansion_report["expansion_triggered"]),
        "expansion_status": expansion_status,
        "initial_draft_length": int(expansion_report["initial_total_length"]),
        "merged_draft_length": int(expansion_report["merged_total_length"]),
        "final_draft_length": int(expansion_report["merged_total_length"]),
        "structured_output_valid": structured_report["passed"],
        "renderer_modified_sentences": False, "rendered": bool(rendered_path),
        "case2_or_case3_run": False, "flow4_calls": 0, "entered_postprocessing": False,
        "fact_safety_rewrite_used": bool(safe_repair_report["safe_repair_count"]),
        "section_rewrite_attempt_count": int(
            section_rewrite_report.get("attempt_count") or 0
        ),
        "section_rewrite_status": str(
            section_rewrite_report.get("status") or "NOT_ELIGIBLE"
        ),
        "evidence_safety_status": str(
            fact_report.get("evidence_safety_status") or ""
        ),
        "timings": dict(timings),
        "introduction_required": bool(introduction_quality_report.get("required")),
        "introduction_present": bool(
            introduction_quality_report.get("introduction_present")
        ),
        "introduction_initial_length": int(
            introduction_quality_report.get("initial_length") or 0
        ),
        "introduction_final_length": int(
            introduction_quality_report.get("final_length") or 0
        ),
        "introduction_status": str(
            introduction_quality_report.get("final_status") or "NOT_REQUIRED"
        ),
        "writing_structure_overlay_applied": bool(
            subsection_structure_report.get("overlay_applied")
        ),
        "subsection_structure_status": str(
            subsection_structure_report.get("structure_status")
            or "NOT_APPLIED"
        ),
        "subsection_counts": prepared["preflight_summary"].get(
            "subsection_counts",
            {},
        ),
        "fact_anchor_distribution_integrity_status": prepared[
            "fact_anchor_distribution_integrity_report"
        ]["integrity_status"],
        "paths": {**prepared["paths"], **expansion_result["paths"],
                  "raw_structured_response": raw_path,
                   "parsed_structured_draft_before_fact_safety": original_parsed_path,
                   "writer_density_observation": writer_density_observation_path,
                  "parsed_structured_draft": parsed_path,
                  "evidence_safety_report": evidence_safety_path,
                  "section_rewrite_report": section_rewrite_report_path,
                  **(
                      {"section_rewrite_raw_response": section_rewrite_raw_path}
                      if section_rewrite_raw_path else {}
                  ),
                  **(
                      {"locally_repaired_structured_draft": locally_repaired_path}
                      if locally_repaired_path else {}
                  ),
                  "safe_repair_report": safe_repair_path,
                  "subsection_structure_validation_report": subsection_report_path,
                  "writer_execution_report": writer_execution_report_path,
                  **(
                      {
                          "structured_execution_repair_result": (
                              structured_repair_result_path
                          )
                      }
                      if structured_repair_result_path else {}
                  ),
                  **structured_repair_trace_paths,
                  **report_paths,
                  "rendered_draft": rendered_path,
                  "source_paraphrase_risk": str((root / "source_paraphrase_risk.json").resolve())},
    }
    summary = {
        **manifest,
        "atomic_evidence_count": len(prepared["evidence_pack"].get("evidence") or []),
        "selected_evidence_count": prepared["selection"]["selected_evidence_count"],
        "fact_anchorable_count": prepared["fact_anchor_report"]["fact_anchorable_count"],
        "analysis_support_only_count": prepared["fact_anchor_report"]["analysis_support_only_count"],
        "section_anchor_counts": prepared["fact_anchor_report"]["section_anchor_counts"],
        "target_total": prepared["content_plan"].get("target_total"),
        "supported_selected_count": fact_report["supported_selected_count"],
        "unsupported_count": fact_report["unsupported_count"],
        "hard_block_count": fact_report["hard_block_count"],
        "safe_repair_count": fact_report["safe_repair_count"],
        "evidence_safety_status": fact_report["evidence_safety_status"],
        "section_rewrite_required_count": int(
            evidence_safety_report.get("section_rewrite_required_count") or 0
        ),
        "draft_rewrite_required": bool(
            evidence_safety_report.get("draft_rewrite_required")
        ),
        "evidence_contract_hard_block_count": int(
            evidence_safety_report.get("hard_block_count") or 0
        ),
        "analysis_fact_leak_count": analysis_report["analysis_fact_leak_count"],
        "writer_density_observation": writer_density_observation,
        "out_of_plan_count": 0,
        "source_paraphrase_risk_level": paraphrase_report.get("risk_level"),
    }
    save_runtime_summary(root, manifest=manifest, summary=summary)
    if status == "FAIL_STRUCTURED_OUTPUT" and not quality_test_mode:
        raise StructuredOutputError(structured_report, str(root.resolve()))
    if status in {"EXPANSION_INVALID", "EXPANSION_PARTIAL"} and not quality_test_mode:
        raise ValueError(status)
    if status == "FAIL_SOURCE_PARAPHRASE" and not (
        callable(structured_repair_decider) and final_candidate is not None
    ):
        raise SourceParaphraseError(paraphrase_report, str(root.resolve()))
    return {
        **prepared,
        **summary,
        "structured_draft": draft,
        "structured_repair_result": structured_repair_result or {},
        "rendered_draft": rendered,
        "recoverable_candidate": final_candidate,
        "candidate_versions": flow_candidates.lineage_summary(),
        "candidate_report_bindings": candidate_report_bindings,
        "issue_route": (
            IssueRoute.QUALITY_REPAIR_REQUIRED.value
            if status == "FAIL_SOURCE_PARAPHRASE"
            else ""
        ),
    }


def generate_flow3_v3_raw_draft(
    request: ArticleGenRequest,
    *,
    artifact_root: Path,
    provider: Any | None = None,
    search_fn: Any = search_detailed,
    retrieval_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the isolated Flow 3 v3 acceptance path without retries or Flow 4.

    The function is deliberately separate from the accepted Flow 1/2 code and
    the existing post-processing pipeline.  It makes exactly one provider call
    after the evidence gate, saves the response first, and never rewrites it.
    """
    task = build_official_draft_task(request)
    if retrieval_payload is None:
        retrieval_payload = retrieve_documents(task, persist_trace=False, search_fn=search_fn)
    selected_chunks = list(retrieval_payload.get("selected_chunks") or [])
    if retrieval_payload.get("retrieval_status") != "SUFFICIENT" or not selected_chunks:
        ranked = list(retrieval_payload.get("ranked_documents") or [])
        primary_id = retrieval_payload.get("primary_document_id")
        primary = next((item for item in ranked if item.get("document_id") == primary_id), {})
        top_score = float((primary.get("score_components") or {}).get("semantic_aggregate", 0.0) or 0.0)
        raise NoRelevantMaterialError(
            topic=task.topic,
            rag_query=str(retrieval_payload.get("query_text") or task.title),
            retrieved_count=0,
            top_score=top_score,
            reason=str(retrieval_payload.get("insufficient_reason") or "no_document_passed_relevance_gate"),
        )

    evidence_pack = build_atomic_evidence_pack(task, retrieval_payload)
    quality_report = validate_atomic_evidence_pack(evidence_pack, retrieval_payload)
    if quality_report["passed"]:
        reference_structure = extract_reference_structure(task, retrieval_payload)
        reference_structure_report = validate_reference_structure(reference_structure)
        if not reference_structure_report["passed"]:
            reference_structure = empty_reference_structure()
        base_content_plan = build_content_plan(
            task,
            evidence_pack,
            reference_structure=reference_structure,
        )
        content_plan_quality_report = validate_content_plan(base_content_plan, evidence_pack, retrieval_payload)
    else:
        reference_structure = empty_reference_structure()
        reference_structure_report = validate_reference_structure(reference_structure)
        base_content_plan = {}
        content_plan_quality_report = {
            "flow3_v3_status": "BLOCKED_INVALID_ATOMIC_EVIDENCE",
            "passed": False,
            "hard_issue_count": 1,
            "hard_issues": [{"rule": "not_run_after_invalid_atomic_evidence"}],
        }
    if quality_report["passed"] and content_plan_quality_report["passed"]:
        evidence_selection = select_evidence_for_content_plan(task, evidence_pack, base_content_plan)
        evidence_selection_report = validate_evidence_selection(evidence_selection, evidence_pack)
        content_plan = evidence_selection["content_plan"]
        selected_evidence_local = evidence_selection["selected_evidence_local"]
        selected_evidence_outbound = evidence_selection["selected_evidence_outbound"]
    else:
        evidence_selection = {}
        evidence_selection_report = {
            "flow3_v32_status": content_plan_quality_report["flow3_v3_status"],
            "passed": False,
            "hard_issue_count": 1,
            "hard_issues": [{"rule": "not_run_after_previous_gate"}],
        }
        content_plan = base_content_plan
        selected_evidence_local = []
        selected_evidence_outbound = []
    prompt = (
        build_atomic_new_draft_prompt(
            task, evidence_pack, content_plan, selected_evidence_outbound,
        )
        if (
            quality_report["passed"] and content_plan_quality_report["passed"]
            and evidence_selection_report["passed"]
        ) else ""
    )
    trace = initialize_trace(
        Path(artifact_root),
        retrieval_result=retrieval_payload,
        evidence_pack=evidence_pack,
        quality_report=quality_report,
        content_plan=content_plan,
        content_plan_quality_report=content_plan_quality_report,
        evidence_selection_report=evidence_selection_report,
        selected_evidence_local=selected_evidence_local,
        selected_evidence_outbound=selected_evidence_outbound,
        prompt=prompt,
    )
    if not quality_report["passed"]:
        error = InvalidEvidencePackError(quality_report)
        error.trace_dir = trace["trace_dir"]
        raise error
    if not content_plan_quality_report["passed"]:
        error = InvalidContentPlanError(content_plan_quality_report)
        error.trace_dir = trace["trace_dir"]
        raise error
    if not evidence_selection_report["passed"]:
        error = InvalidEvidenceSelectionError(evidence_selection_report)
        error.trace_dir = trace["trace_dir"]
        raise error

    active_provider = provider or get_llm_provider()
    raw_response = active_provider.generate_text(
        prompt=prompt,
        system_prompt="严格遵守用户提示中的新稿、证据和输出边界。",
        temperature=request.temperature if request.temperature is not None else 0.7,
    )
    raw_response_path = save_raw_response(Path(artifact_root), str(raw_response))
    unsupported_report = check_unsupported_facts(
        str(raw_response), selected_evidence_local, task,
        all_qualified_evidence=evidence_pack["evidence"],
    )
    paraphrase_report = check_source_paraphrase_risk(
        str(raw_response),
        retrieval_payload,
        unsupported_fact_present=not unsupported_report["passed"],
    )
    report_paths = save_check_reports(
        Path(artifact_root),
        unsupported_report=unsupported_report,
        paraphrase_report=paraphrase_report,
    )
    status = "READY_FOR_CASE1_USER_REVIEW"
    if unsupported_report["unsupported_count"]:
        status = "FAIL_UNSUPPORTED_FACT"
    elif unsupported_report["out_of_plan_count"]:
        status = "FAIL_OUT_OF_PLAN_FACT"
    elif paraphrase_report["risk_level"] == "hard_block":
        status = "FAIL_SOURCE_PARAPHRASE"
    elif unsupported_report["supported_selected_count"] < 6:
        status = "FAIL_UNSUPPORTED_FACT"
    manifest = {
        "flow3_v3_status": status,
        "model": _flow3_v3_provider_model(active_provider),
        "model_call_count": 1,
        "raw_response_saved_before_checks": True,
        "checker_modified_response": False,
        "entered_postprocessing": False,
        "flow4_calls": 0,
        "paths": {**trace, "raw_response": raw_response_path, **report_paths},
    }
    summary = {
        "flow3_v3_status": status,
        "primary_document_id": evidence_pack["primary_document_id"],
        "auxiliary_document_ids": evidence_pack["auxiliary_document_ids"],
        "evidence_count": len(evidence_pack["evidence"]),
        "required_evidence_count": len(evidence_pack["required_evidence"]),
        "optional_evidence_count": len(evidence_pack["optional_evidence"]),
        "evidence_type_distribution": quality_report["evidence_type_distribution"],
        "evidence_quality_passed": quality_report["passed"],
        "selected_evidence_count": len(selected_evidence_local),
        "evidence_selection_passed": evidence_selection_report["passed"],
        "content_plan_section_count": content_plan["section_count"],
        "content_plan_quality_passed": content_plan_quality_report["passed"],
        "recommended_length_range": content_plan["recommended_length_range"],
        "concrete_local_fact_count": unsupported_report["concrete_local_fact_count"],
        "general_analysis_count": unsupported_report["general_analysis_count"],
        "successful_mapped_fact_count": unsupported_report["successful_mapped_fact_count"],
        "supported_selected_count": unsupported_report["supported_selected_count"],
        "out_of_plan_count": unsupported_report["out_of_plan_count"],
        "unsupported_count": unsupported_report["unsupported_count"],
        "unsupported_claim_count": unsupported_report["unsupported_claim_count"],
        "source_paraphrase_risk": paraphrase_report["risk_level"],
        "case2_or_case3_run": False,
    }
    summary_paths = save_runtime_summary(Path(artifact_root), manifest=manifest, summary=summary)
    trace.update({"raw_response": raw_response_path, **report_paths, **summary_paths})

    if unsupported_report["unsupported_count"] or unsupported_report["supported_selected_count"] < 6:
        raise UnsupportedFactError(unsupported_report, trace["trace_dir"])
    if unsupported_report["out_of_plan_count"]:
        raise OutOfPlanFactError(unsupported_report, trace["trace_dir"])
    if paraphrase_report["risk_level"] == "hard_block":
        raise SourceParaphraseError(paraphrase_report, trace["trace_dir"])
    return {
        "flow3_v3_status": "READY_FOR_CASE1_USER_REVIEW",
        "task": task,
        "retrieval_result": retrieval_payload,
        "evidence_pack": evidence_pack,
        "evidence_quality_report": quality_report,
        "content_plan": content_plan,
        "content_plan_quality_report": content_plan_quality_report,
        "reference_structure": reference_structure,
        "reference_structure_quality_report": reference_structure_report,
        "evidence_selection": evidence_selection,
        "evidence_selection_report": evidence_selection_report,
        "selected_evidence_local": selected_evidence_local,
        "selected_evidence_outbound": selected_evidence_outbound,
        "prompt": prompt,
        "raw_response": str(raw_response),
        "unsupported_fact_report": unsupported_report,
        "source_paraphrase_risk": paraphrase_report,
        "trace": trace,
        "model_call_count": 1,
        "entered_postprocessing": False,
        "flow4_calls": 0,
    }


class ArticleGenerator:
    """Core article generation pipeline."""

    @staticmethod
    def _load_flow3_v33_json(artifact_root: Path, filename: str) -> dict[str, Any]:
        path = Path(artifact_root) / filename
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"FLOW3 artifact must contain one JSON object: {path}")
        return value

    @staticmethod
    def _render_recoverable_draft(
        draft: dict[str, Any] | None,
        content_plan: dict[str, Any],
    ) -> str:
        """Render one readable body without treating it as contract-qualified."""

        candidate = draft if isinstance(draft, dict) else {}
        try:
            rendered = render_structured_draft(candidate, content_plan)
        except (KeyError, TypeError, ValueError):
            rendered = ""
        if str(rendered or "").strip():
            return str(rendered).strip()

        lines: list[str] = []
        introduction = str(candidate.get("introduction") or "").strip()
        if introduction:
            lines.append(introduction)
        for section in candidate.get("sections") or []:
            if not isinstance(section, dict):
                continue
            heading = str(section.get("heading") or "").strip()
            if heading:
                lines.append(heading)
            for sentence in section.get("sentences") or []:
                if isinstance(sentence, dict):
                    text = str(sentence.get("text") or "").strip()
                    if text:
                        lines.append(text)
        return "\n\n".join(lines).strip()

    @staticmethod
    def _fact_safety_boundary(fact_report: dict[str, Any]) -> str:
        review_required = bool(
            fact_report.get("passed") is not True
            or int(fact_report.get("unsupported_count") or 0)
            or int(fact_report.get("out_of_plan_count") or 0)
            or int(fact_report.get("hard_block_count") or 0)
            or fact_report.get("review_required")
            or fact_report.get("delivery_blocked") is True
        )
        return "REVIEW_REQUIRED" if review_required else "FACT_SAFE"

    def _build_review_delivery_article(
        self,
        *,
        candidate: ArticleCandidate,
        request: ArticleGenRequest,
        prepared: dict[str, Any],
        started_at: float,
        issues: list[dict[str, Any]],
        failure_stage: str,
        reason: str,
        delivery_warnings: list[str] | None = None,
        flow3_result: dict[str, Any] | None = None,
        gate_report: dict[str, Any] | None = None,
    ) -> ArticleGenResult:
        """Construct the only review-delivery shape used after a body exists."""

        normalized_issues = list(issues)
        if not normalized_issues:
            normalized_issues.append(
                issue_item(
                    stage=failure_stage,
                    issue_type="REVIEW_REQUIRED",
                    description=reason,
                    reason=reason,
                )
            )
        warnings = list(delivery_warnings or [])
        decision = review_decision(
            issues=normalized_issues,
            failure_stage=failure_stage,
            reason=reason,
            delivery_warnings=warnings,
        )
        report = gate_report or evaluate_final_delivery_length_and_structure(
            final_text=candidate.content,
            target_words=prepared["task"].target_words,
            content_plan=prepared["content_plan"],
            structured_draft=candidate.structured_draft,
        )
        selected_chunks = list(
            prepared["retrieval_result"].get("selected_chunks") or []
        )
        flow3 = flow3_result or {}
        return ArticleGenResult(
            article_mode="official_draft",
            title=candidate.title or request.title,
            content=candidate.content,
            scene=_safe_scene(prepared["task"].document_type),
            retrieved_count=len(selected_chunks),
            generation_time=round(time.time() - started_at, 2),
            save_path="",
            docx_path="",
            export_format=prepared["task"].export_format,
            reference_sources=_build_reference_sources(selected_chunks),
            review_notes=[
                str(item.get("risk_description") or reason)
                for item in normalized_issues
            ],
            word_count=count_official_draft_words(candidate.content),
            selected_candidate_stage=candidate.stage,
            final_pipeline_stage_completed=False,
            fact_safety_rewrite_used=bool(flow3.get("fact_safety_rewrite_used")),
            user_confirmation_required=True,
            delivery_status="SUCCESS_WITH_REVIEW",
            official_success=False,
            target_word_count=prepared["task"].target_words,
            fact_risk_count=sum(
                1 for item in normalized_issues
                if "FACT" in str(item.get("risk_type") or "")
            ),
            analysis_risk_count=sum(
                1 for item in normalized_issues
                if "ANALYSIS" in str(item.get("risk_type") or "")
            ),
            fact_gap_count=0,
            length_gate_status=str(report.get("length_gate_status") or "UNKNOWN"),
            structure_gate_status=str(report.get("structure_gate_status") or "UNKNOWN"),
            risk_items=normalized_issues,
            delivery_warnings=warnings,
            failure_class="RECOVERABLE_CONTENT_FAILURE",
            failure_stage=failure_stage,
            reason=reason,
            next_step=decision.next_step,
            review_suggestion=decision.next_step,
            delivery_decision=decision,
        )

    @staticmethod
    def _render_flow4_for_delivery(
        *,
        parsed_structured_draft: dict[str, Any],
        prepared: dict[str, Any],
        validation_reports: dict[str, Any],
        flow3_status: str,
        run_id: str,
        artifact_root: Path,
        output_dir: Path,
        timings: dict[str, float],
    ) -> tuple[Any, dict[str, Any]]:
        envelope = create_flow3_output_envelope(
            parsed_structured_draft=parsed_structured_draft,
            content_plan=prepared["content_plan"],
            fact_anchor_pack=prepared["fact_anchor_pack"],
            validation_reports=validation_reports,
            task=prepared["task"],
        )
        flow4_input = build_flow4_input(
            envelope,
            run_id=run_id,
            flow3_status=flow3_status,
            artifact_root=artifact_root,
            render_options={
                "formats": [prepared["task"].export_format],
                "include_title_in_plain_text": False,
                "output_name": parsed_structured_draft["title"],
                "output_dir": str(output_dir / "flow4"),
            },
        )
        stage_started = time.perf_counter()
        render_result = render_flow4_input(flow4_input)
        _record_elapsed(timings, "flow4_render_ms", stage_started)
        gate_report = validate_final_delivery_quality(
            final_text=render_result.final_text,
            target_words=prepared["task"].target_words,
            content_plan=prepared["content_plan"],
            structured_draft=parsed_structured_draft,
            validation_reports=validation_reports,
            flow4_risk_status=render_result.validation_summary.risk_status,
        )
        return render_result, gate_report

    def _save_delivery_runtime_best_effort(
        self,
        *,
        artifact_root: Path,
        updates: dict[str, Any],
    ) -> str | None:
        """Persist non-body audit state without converting it into body loss."""

        try:
            summary_path = artifact_root / "run_summary.json"
            manifest_path = artifact_root / "runtime_manifest.json"
            existing_summary = (
                self._load_flow3_v33_json(artifact_root, summary_path.name)
                if summary_path.exists()
                else {}
            )
            existing_manifest = (
                self._load_flow3_v33_json(artifact_root, manifest_path.name)
                if manifest_path.exists()
                else {}
            )
            save_runtime_summary(
                artifact_root,
                manifest={**existing_manifest, **updates},
                summary={**existing_summary, **updates},
            )
        except Exception as exc:  # audit persistence is not the article body
            return f"运行审计摘要保存失败：{type(exc).__name__}"
        return None

    async def generate_commercial_flow(self, request: ArticleGenRequest) -> ArticleGenBatchResult:
        """Run the production FLOW3-V3.3 -> FLOW4 chain without legacy post-processing."""

        if request.generation_route == "full_article_drafting":
            from app.services.generation.full_draft_generation import generate_full_draft
            return await generate_full_draft(request)

        output_dir = get_output_dir()
        provider = get_llm_provider()
        quality_test_mode = bool(get_settings().quality_test_mode)
        articles: list[ArticleGenResult] = []
        count = max(1, min(request.count, 10))

        for _ in range(count):
            started_at = time.time()
            run_started = time.perf_counter()
            timings = _new_generation_timings()
            last_known_good = LastKnownGoodArticle()
            run_id = f"FLOW3-RUN-{uuid.uuid4().hex.upper()}"
            artifact_root = output_dir / "flow3" / run_id
            prepared = prepare_flow3_v33_structured_generation(
                request,
                artifact_root=artifact_root,
                timings=timings,
            )
            prepared.setdefault(
                "introduction_contract",
                build_introduction_contract(
                    "",
                    int(
                        prepared["content_plan"].get("target_total")
                        or prepared["task"].target_words
                    ),
                    list(prepared["content_plan"].get("sections") or []),
                ),
            )

            def _production_structured_repair_decision(
                decision: dict[str, Any],
            ) -> str:
                """Production Orchestrator owns repair/recheck/continue."""

                validation_status = str(
                    decision.get("validation_status") or ""
                )
                if validation_status == "PASS":
                    return "CONTINUE"
                if validation_status == "FAIL_REPAIRABLE":
                    return "REPAIR"
                return "RETURN_UNREPAIRED_STATE"

            flow3_result = generate_flow3_v33_structured_draft(
                request,
                artifact_root=artifact_root,
                provider=provider,
                external_authorized=True,
                prepared=prepared,
                quality_test_mode=quality_test_mode,
                structured_repair_decider=(
                    _production_structured_repair_decision
                ),
            )
            flow3_status = str(flow3_result["flow3_v33_status"])
            writer_draft = flow3_result.get("structured_draft")
            writer_text = self._render_recoverable_draft(
                writer_draft if isinstance(writer_draft, dict) else {},
                prepared["content_plan"],
            )
            upstream_candidate = flow3_result.get("recoverable_candidate")
            if (
                not writer_text
                and isinstance(upstream_candidate, ArticleCandidate)
            ):
                writer_text = upstream_candidate.content
            last_known_good.update(
                title=str(
                    (
                        (writer_draft or {}).get("title")
                        if isinstance(writer_draft, dict)
                        else ""
                    )
                    or request.title
                ),
                content=writer_text,
                structured_draft=(
                    writer_draft if isinstance(writer_draft, dict) else {}
                ),
                stage="WRITER_STRUCTURED_DRAFT",
                safety_boundary="WRITER_UNVERIFIED",
                role=(
                    upstream_candidate.role
                    if isinstance(upstream_candidate, ArticleCandidate)
                    else CandidateRole.PARSED.value
                ),
            )
            if flow3_status == "RETURN_UNREPAIRED_STATE":
                structured_report = dict(
                    flow3_result.get("structured_report") or {}
                )
                structured_report["structured_repair_result"] = dict(
                    flow3_result.get("structured_repair_result") or {}
                )
                if (
                    last_known_good.article_exists
                    and last_known_good.article_recoverable
                    and last_known_good.candidate is not None
                ):
                    repair_result = structured_report[
                        "structured_repair_result"
                    ]
                    hard_issues = list(structured_report.get("hard_issues") or [])
                    issues = [
                        issue_item(
                            stage="STRUCTURED_EXECUTION",
                            issue_type=str(item.get("rule") or "STRUCTURED_REPAIR_FAILED"),
                            description=(
                                "结构化或执行合同仍未通过自动修复，需要人工复核。"
                            ),
                            section_id=str(item.get("section_id") or ""),
                            paragraph_index=(
                                int(item["index"])
                                if item.get("index") is not None
                                else -1
                            ),
                            reason=str(item),
                        )
                        for item in hard_issues
                        if isinstance(item, dict)
                    ]
                    if not issues:
                        issues = [
                            issue_item(
                                stage="STRUCTURED_EXECUTION",
                                issue_type=str(
                                    repair_result.get("repair_status")
                                    or "STRUCTURED_REPAIR_FAILED"
                                ),
                                description=(
                                    "结构化或执行修复已耗尽，但正文完整可读。"
                                ),
                            )
                        ]
                    articles.append(
                        self._build_review_delivery_article(
                            candidate=last_known_good.candidate,
                            request=request,
                            prepared=prepared,
                            started_at=started_at,
                            issues=issues,
                            failure_stage="STRUCTURED_EXECUTION",
                            reason=(
                                "Structured / Execution Repair 未达到正式合格线；"
                                "保留完整正文供人工复核。"
                            ),
                            flow3_result=flow3_result,
                        )
                    )
                    continue
                raise StructuredOutputError(
                    structured_report,
                    str(artifact_root.resolve()),
                )

            parsed_structured_draft = self._load_flow3_v33_json(
                artifact_root,
                "parsed_structured_draft.json",
            )
            validation_reports: dict[str, Any] = {
                "structured": self._load_flow3_v33_json(
                    artifact_root,
                    "structured_validation_report.json",
                ),
                "fact": self._load_flow3_v33_json(
                    artifact_root,
                    "fact_validation_report.json",
                ),
                "analysis": self._load_flow3_v33_json(
                    artifact_root,
                    "analysis_validation_report.json",
                ),
                "preflight": prepared["preflight_summary"],
                "content_plan_quality": prepared["content_plan_report"],
                "evidence_selection": prepared["selection_report"],
                "fact_anchor_quality": prepared["fact_anchor_report"],
                "atomic_evidence_quality": prepared["atomic_report"],
            }
            source_paraphrase_path = artifact_root / "source_paraphrase_risk.json"
            if source_paraphrase_path.exists():
                validation_reports["source_paraphrase"] = self._load_flow3_v33_json(
                    artifact_root,
                    source_paraphrase_path.name,
                )
                if flow3_status == "FAIL_SOURCE_PARAPHRASE":
                    validation_reports["u2_issue_route"] = {
                        "route": IssueRoute.QUALITY_REPAIR_REQUIRED.value,
                        "next_owner": "M4",
                    }
            fact_boundary = self._fact_safety_boundary(
                validation_reports["fact"]
            )
            parsed_text = self._render_recoverable_draft(
                parsed_structured_draft,
                prepared["content_plan"],
            )
            last_known_good.update(
                title=str(parsed_structured_draft.get("title") or request.title),
                content=parsed_text,
                structured_draft=parsed_structured_draft,
                stage="FACT_SAFETY_DRAFT",
                safety_boundary=fact_boundary,
                role=(
                    CandidateRole.REPAIRED.value
                    if int(validation_reports["fact"].get("safe_repair_count") or 0)
                    else CandidateRole.PARSED.value
                ),
                parent_candidate_id=(
                    last_known_good.candidate.candidate_id
                    if last_known_good.candidate is not None
                    else ""
                ),
            )
            fact_candidate = last_known_good.versions[-1]
            for report_type in ("structured", "fact", "analysis", "source_paraphrase"):
                report_value = validation_reports.get(report_type)
                if isinstance(report_value, dict):
                    last_known_good.bind_report(
                        fact_candidate.candidate_id,
                        report_type.upper(),
                        report_value,
                    )

            if quality_test_mode:
                sanitized = sanitize_quality_test_draft(
                    parsed_structured_draft,
                    structured_report=validation_reports["structured"],
                    fact_report=validation_reports["fact"],
                    analysis_report=validation_reports["analysis"],
                )
                sanitized_draft = sanitized["structured_draft"]
                introduction_cleanup = sanitize_introduction_analysis(
                    sanitized_draft
                )
                if flow3_result.get("introduction_required"):
                    sanitized_draft["introduction"] = introduction_cleanup["text"]
                sanitized_text = render_structured_draft(
                    sanitized_draft,
                    prepared["content_plan"],
                )
                final_introduction_quality = evaluate_introduction_quality(
                    draft=sanitized_draft,
                    introduction_contract=prepared["introduction_contract"],
                )
                introduction_report_path = (
                    artifact_root / "introduction_quality_report.json"
                )
                saved_introduction_report = (
                    self._load_flow3_v33_json(
                        artifact_root,
                        introduction_report_path.name,
                    )
                    if introduction_report_path.exists()
                    else {}
                )
                final_introduction_status = str(
                    final_introduction_quality.get("status") or "NOT_REQUIRED"
                )
                if (
                    introduction_cleanup["safety_issue_count"]
                    and final_introduction_status != "PASS"
                ):
                    final_introduction_status = (
                        "INTRODUCTION_INSUFFICIENT_AFTER_SAFETY_CLEANUP"
                    )
                saved_introduction_report.update(
                    {
                        "required": bool(
                            prepared["introduction_contract"].get("required")
                        ),
                        "introduction_present": bool(
                            final_introduction_quality.get(
                                "introduction_present"
                            )
                        ),
                        "final_length": int(
                            final_introduction_quality.get("length") or 0
                        ),
                        "safety_issue_count": int(
                            introduction_cleanup["safety_issue_count"]
                        ),
                        "final_status": final_introduction_status,
                    }
                )
                if prepared["introduction_contract"].get("required"):
                    _flow3_v33_write_json(
                        introduction_report_path,
                        saved_introduction_report,
                    )
                    atomic_write_text(
                        artifact_root / "final_introduction.txt",
                        str(sanitized_draft.get("introduction") or ""),
                        encoding="utf-8",
                    )
                _flow3_v33_write_json(
                    artifact_root / "quality_test_sanitized_draft.json",
                    sanitized_draft,
                )
                atomic_write_text(
                    artifact_root / "quality_test_preview.txt",
                    sanitized_text,
                    encoding="utf-8",
                )
                gate_report = evaluate_final_delivery_length_and_structure(
                    final_text=sanitized_text,
                    target_words=prepared["task"].target_words,
                    content_plan=prepared["content_plan"],
                    structured_draft=sanitized_draft,
                )
                initial_draft_length = int(
                    flow3_result.get("initial_draft_length")
                    or len(render_draft_for_length(
                        parsed_structured_draft,
                        prepared["content_plan"],
                    ))
                )
                merged_draft_length = int(
                    flow3_result.get("merged_draft_length")
                    or len(render_draft_for_length(
                        parsed_structured_draft,
                        prepared["content_plan"],
                    ))
                )
                expansion_status = str(
                    flow3_result.get("expansion_status") or "NOT_NEEDED"
                )
                if (
                    flow3_result.get("expansion_triggered")
                    and merged_draft_length >= gate_report["min_words"]
                    and gate_report["actual_words"] < gate_report["min_words"]
                ):
                    expansion_status = "LENGTH_INSUFFICIENT_AFTER_SAFETY_CLEANUP"
                summary_updates = {
                    "model_call_count": int(
                        flow3_result.get("model_call_count") or 1
                    ),
                    "expansion_triggered": bool(
                        flow3_result.get("expansion_triggered")
                    ),
                    "expansion_status": expansion_status,
                    "initial_draft_length": initial_draft_length,
                    "merged_draft_length": merged_draft_length,
                    "final_draft_length": gate_report["actual_words"],
                    "introduction_required": bool(
                        prepared["introduction_contract"].get("required")
                    ),
                    "introduction_present": bool(
                        final_introduction_quality.get("introduction_present")
                    ),
                    "introduction_initial_length": int(
                        saved_introduction_report.get("initial_length") or 0
                    ),
                    "introduction_final_length": int(
                        final_introduction_quality.get("length") or 0
                    ),
                    "introduction_status": final_introduction_status,
                    "flow4_calls": 0,
                    **(
                        gate_report.get("work_summary_length_report") or {}
                    ),
                }
                runtime_summary_path = artifact_root / "run_summary.json"
                runtime_manifest_path = artifact_root / "runtime_manifest.json"
                existing_summary = (
                    self._load_flow3_v33_json(
                        artifact_root,
                        runtime_summary_path.name,
                    )
                    if runtime_summary_path.exists()
                    else {"flow3_v33_status": flow3_status}
                )
                existing_manifest = (
                    self._load_flow3_v33_json(
                        artifact_root,
                        runtime_manifest_path.name,
                    )
                    if runtime_manifest_path.exists()
                    else {"flow3_v33_status": flow3_status}
                )
                save_runtime_summary(
                    artifact_root,
                    manifest={**existing_manifest, **summary_updates},
                    summary={**existing_summary, **summary_updates},
                )
                expansion_report_path = (
                    artifact_root / "section_expansion_report.json"
                )
                if expansion_report_path.exists():
                    saved_expansion_report = self._load_flow3_v33_json(
                        artifact_root,
                        expansion_report_path.name,
                    )
                    saved_expansion_report["expansion_status"] = expansion_status
                    saved_expansion_report["final_draft_length"] = gate_report[
                        "actual_words"
                    ]
                    _flow3_v33_write_json(
                        expansion_report_path,
                        saved_expansion_report,
                    )
                selected_chunks = list(
                    prepared["retrieval_result"].get("selected_chunks") or []
                )
                articles.append(
                    ArticleGenResult(
                        article_mode="official_draft",
                        title=str(sanitized_draft.get("title") or ""),
                        content=sanitized_text,
                        scene=_safe_scene(prepared["task"].document_type),
                        retrieved_count=len(selected_chunks),
                        generation_time=round(time.time() - started_at, 2),
                        save_path="",
                        docx_path="",
                        export_format=prepared["task"].export_format,
                        reference_sources=_build_reference_sources(selected_chunks),
                        review_notes=[],
                        word_count=gate_report["actual_words"],
                        selected_candidate_stage="QUALITY_TEST_SANITIZED_PREVIEW",
                        final_pipeline_stage_completed=False,
                        fact_safety_rewrite_used=False,
                        user_confirmation_required=True,
                        quality_test_mode=True,
                        delivery_status="QUALITY_TEST_DRAFT",
                        official_success=False,
                        target_word_count=prepared["task"].target_words,
                        fact_risk_count=sanitized["fact_risk_count"],
                        analysis_risk_count=sanitized["analysis_risk_count"],
                        fact_gap_count=sanitized["fact_gap_count"],
                        length_gate_status=gate_report["length_gate_status"],
                        structure_gate_status=gate_report["structure_gate_status"],
                        risk_items=sanitized["risk_items"],
                    )
                )
                continue

            quality_report: dict[str, Any] | None = None
            quality_report_path = ""
            quality_repair_trace_path = ""
            commercial_quality_review_required = False
            if should_run_work_summary_quality_gate(
                prepared["task"], prepared["content_plan"],
            ):
                quality_gate_started = time.perf_counter()
                try:
                    quality_loop_result = run_work_summary_commercial_quality_loop(
                        task_metadata=prepared["task"],
                        draft=parsed_structured_draft,
                        prepared=prepared,
                        validation_results=validation_reports,
                        provider=provider,
                        temperature=(
                            request.temperature
                            if request.temperature is not None
                            else 0.7
                        ),
                    )
                except (
                    LLMConnectionError,
                    ValidationError,
                    ValueError,
                    RuntimeError,
                ) as exc:
                    if last_known_good.candidate is None:
                        raise
                    articles.append(
                        self._build_review_delivery_article(
                            candidate=last_known_good.candidate,
                            request=request,
                            prepared=prepared,
                            started_at=started_at,
                            issues=[
                                issue_item(
                                    stage="COMMERCIAL_QUALITY_REPAIR",
                                    issue_type=type(exc).__name__,
                                    description=(
                                        "商业质量修复未完成，已保留修复前完整正文。"
                                    ),
                                    reason=str(exc),
                                )
                            ],
                            failure_stage="COMMERCIAL_QUALITY_REPAIR",
                            reason="商业质量修复未达到正式合格线。",
                            flow3_result=flow3_result,
                        )
                    )
                    continue
                parsed_structured_draft = dict(
                    quality_loop_result["structured_draft"]
                )
                validation_reports = dict(
                    quality_loop_result["validation_results"]
                )
                quality_report = dict(quality_loop_result["quality_report"])
                quality_report["orchestrator_next_step"] = (
                    "CONTINUE_TO_FLOW4"
                    if quality_report.get("quality_status") == "PASS"
                    else "CONTINUE_EXISTING_FLOW_WITH_QUALITY_RETURN_STATE"
                )
                quality_report["quality_gate_summary"] = (
                    summarize_work_summary_quality_report(quality_report)
                )
                save_parsed_structured_draft(
                    artifact_root,
                    parsed_structured_draft,
                )
                save_v33_validation_reports(
                    artifact_root,
                    structured_report=validation_reports["structured"],
                    fact_report=validation_reports["fact"],
                    analysis_report=validation_reports["analysis"],
                )
                save_rendered_draft(
                    artifact_root,
                    render_structured_draft(
                        parsed_structured_draft,
                        prepared["content_plan"],
                    ),
                )
                quality_report_path = _flow3_v33_write_json(
                    artifact_root / "work_summary_quality_report.json",
                    quality_report,
                )
                quality_repair_trace_path = _flow3_v33_write_json(
                    artifact_root / "work_summary_quality_repair_report.json",
                    quality_loop_result["repair_trace"],
                )
                flow3_result["model_call_count"] = int(
                    flow3_result.get("model_call_count") or 0
                ) + int(quality_loop_result.get("model_call_count") or 0)
                _record_elapsed(timings, "quality_gate_ms", quality_gate_started)
                quality_status = str(
                    quality_report.get("quality_status") or ""
                )
                commercial_quality_review_required = quality_status != "PASS"
                quality_text = self._render_recoverable_draft(
                    parsed_structured_draft,
                    prepared["content_plan"],
                )
                accepted = last_known_good.update(
                    title=str(
                        parsed_structured_draft.get("title") or request.title
                    ),
                    content=quality_text,
                    structured_draft=parsed_structured_draft,
                    stage="COMMERCIAL_QUALITY_REPAIR_DRAFT",
                    safety_boundary=self._fact_safety_boundary(
                        validation_reports["fact"]
                    ),
                    role=CandidateRole.QUALITY.value,
                    parent_candidate_id=(
                        last_known_good.candidate.candidate_id
                        if last_known_good.candidate is not None
                        else ""
                    ),
                )
                quality_candidate = last_known_good.versions[-1]
                last_known_good.bind_report(
                    quality_candidate.candidate_id,
                    "COMMERCIAL_QUALITY",
                    quality_report,
                )
                for report_type in ("structured", "fact", "analysis"):
                    report_value = validation_reports.get(report_type)
                    if isinstance(report_value, dict):
                        last_known_good.bind_report(
                            quality_candidate.candidate_id,
                            report_type.upper(),
                            report_value,
                        )
                if not accepted and last_known_good.candidate is not None:
                    parsed_structured_draft = copy.deepcopy(
                        last_known_good.candidate.structured_draft
                    )

            try:
                pre_flow4_text = render_structured_draft(
                    parsed_structured_draft,
                    prepared["content_plan"],
                )
            except (ValidationError, ValueError, TypeError, RuntimeError) as exc:
                if last_known_good.candidate is None:
                    raise
                articles.append(
                    self._build_review_delivery_article(
                        candidate=last_known_good.candidate,
                        request=request,
                        prepared=prepared,
                        started_at=started_at,
                        issues=[
                            issue_item(
                                stage="FLOW4_PRE_RENDER",
                                issue_type=type(exc).__name__,
                                description=(
                                    "FLOW4 前渲染失败，已保留最后一个完整正文。"
                                ),
                                reason=str(exc),
                            )
                        ],
                        failure_stage="FLOW4_PRE_RENDER",
                        reason="FLOW4 前正文渲染需要人工复核。",
                        flow3_result=flow3_result,
                    )
                )
                continue
            last_known_good.update(
                title=str(parsed_structured_draft.get("title") or request.title),
                content=pre_flow4_text,
                structured_draft=parsed_structured_draft,
                stage="FLOW4_PRE_BODY",
                safety_boundary=self._fact_safety_boundary(
                    validation_reports["fact"]
                ),
            )
            pre_flow4_gate = evaluate_final_delivery_length_and_structure(
                final_text=pre_flow4_text,
                target_words=prepared["task"].target_words,
                content_plan=prepared["content_plan"],
                structured_draft=parsed_structured_draft,
            )
            if pre_flow4_gate["length_gate_status"] != "PASS":
                if last_known_good.candidate is None:
                    raise FinalDeliveryQualityError(
                        pre_flow4_gate["length_gate_status"],
                        actual_words=pre_flow4_gate["actual_words"],
                        min_words=pre_flow4_gate["min_words"],
                        max_words=pre_flow4_gate["max_words"],
                    )
                articles.append(
                    self._build_review_delivery_article(
                        candidate=last_known_good.candidate,
                        request=request,
                        prepared=prepared,
                        started_at=started_at,
                        issues=[
                            issue_item(
                                stage="FINAL_DELIVERY_QUALITY",
                                issue_type=pre_flow4_gate["length_gate_status"],
                                description=(
                                    "正文完整，但篇幅未达到正式交付质量线。"
                                ),
                                reason=(
                                    f"actual={pre_flow4_gate['actual_words']}, "
                                    f"range={pre_flow4_gate['min_words']}-"
                                    f"{pre_flow4_gate['max_words']}"
                                ),
                            )
                        ],
                        failure_stage="FINAL_DELIVERY_QUALITY",
                        reason="篇幅 Gate 未达到正式合格线。",
                        flow3_result=flow3_result,
                        gate_report=pre_flow4_gate,
                    )
                )
                continue

            frozen_article_plan = prepared.get("frozen_article_plan")
            if isinstance(frozen_article_plan, FrozenArticlePlan):
                try:
                    frozen_article_plan.assert_projection_integrity(
                        content_plan=prepared["content_plan"],
                        selection=prepared["selection"],
                        fact_anchor_pack=prepared["fact_anchor_pack"],
                    )
                except (ValidationError, ValueError, RuntimeError) as exc:
                    if last_known_good.candidate is None:
                        raise
                    articles.append(
                        self._build_review_delivery_article(
                            candidate=last_known_good.candidate,
                            request=request,
                            prepared=prepared,
                            started_at=started_at,
                            issues=[
                                issue_item(
                                    stage="FLOW3_FLOW4_CONTRACT",
                                    issue_type=type(exc).__name__,
                                    description=(
                                        "正文存在，但 FLOW3→FLOW4 交接完整性需要人工复核。"
                                    ),
                                    reason=str(exc),
                                )
                            ],
                            failure_stage="FLOW3_FLOW4_CONTRACT",
                            reason="FLOW3→FLOW4 合同复核未通过。",
                            flow3_result=flow3_result,
                            gate_report=pre_flow4_gate,
                        )
                    )
                    continue
            delivery_contract = evaluate_flow3_delivery_contract(
                flow3_status,
                validation_reports["fact"],
                article_renderable=bool(pre_flow4_text.strip()),
            )
            if not delivery_contract["passed"]:
                if last_known_good.candidate is None:
                    raise ValueError("FAILED_DELIVERY_RESULT")
                articles.append(
                    self._build_review_delivery_article(
                        candidate=last_known_good.candidate,
                        request=request,
                        prepared=prepared,
                        started_at=started_at,
                        issues=[
                            issue_item(
                                stage="FLOW3_DELIVERY_CONTRACT",
                                issue_type=str(
                                    delivery_contract.get("flow3_status")
                                    or "FAILED_DELIVERY_RESULT"
                                ),
                                description=(
                                    "正文完整可恢复，但 FLOW3 交付合同未达到正式合格线。"
                                ),
                            )
                        ],
                        failure_stage="FLOW3_DELIVERY_CONTRACT",
                        reason="FLOW3 交付合同需要人工复核。",
                        flow3_result=flow3_result,
                        gate_report=pre_flow4_gate,
                    )
                )
                continue

            repair_applied = bool(
                str(flow3_result.get("structured_repair_status") or "")
                == "REPAIRED"
                or flow3_status in {
                    "PASS_WITH_SAFE_REPAIR",
                    "PASS_WITH_LOCAL_ISOLATION",
                }
                or (
                    quality_report is not None
                    and int(quality_report.get("repair_rounds") or 0) > 0
                    and quality_report.get("quality_status") == "PASS"
                )
            )

            if (
                delivery_contract["delivery_status"] == "SUCCESS_WITH_REVIEW"
                or commercial_quality_review_required
            ):
                try:
                    review_metadata = sanitize_quality_test_draft(
                        parsed_structured_draft,
                        structured_report=validation_reports["structured"],
                        fact_report=validation_reports["fact"],
                        analysis_report=validation_reports["analysis"],
                    )
                except (ValidationError, ValueError, TypeError, RuntimeError) as exc:
                    review_metadata = {
                        "fact_risk_count": 0,
                        "analysis_risk_count": 0,
                        "fact_gap_count": 0,
                        "risk_items": [
                            issue_item(
                                stage="REVIEW_METADATA",
                                issue_type=type(exc).__name__,
                                description=(
                                    "自动风险摘要失败，需人工复核完整正文。"
                                ),
                                reason=str(exc),
                            )
                        ],
                    }
                risk_items = list(review_metadata["risk_items"])
                if commercial_quality_review_required:
                    for quality_issue in list(
                        (quality_report or {}).get("issues") or []
                    ):
                        if not isinstance(quality_issue, dict):
                            continue
                        risk_items.append(
                            issue_item(
                                stage="COMMERCIAL_QUALITY",
                                issue_type=str(
                                    quality_issue.get("issue_type")
                                    or "COMMERCIAL_QUALITY_REVIEW_REQUIRED"
                                ),
                                description=str(
                                    quality_issue.get("description")
                                    or quality_issue.get("message")
                                    or "商业质量修复耗尽，需要人工复核。"
                                ),
                                section_id=str(
                                    quality_issue.get("section_id") or ""
                                ),
                                reason=str(
                                    quality_issue.get("evidence")
                                    or quality_issue
                                ),
                            )
                        )
                if not risk_items:
                    risk_items.append({
                        "section_id": "",
                        "section_heading": "",
                        "paragraph_index": -1,
                        "field_name": "content",
                        "risk_type": (
                            "COMMERCIAL_QUALITY_REVIEW_REQUIRED"
                            if commercial_quality_review_required
                            else "FACT_SAFETY_REVIEW_REQUIRED"
                        ),
                        "internal_error_type": (
                            str((quality_report or {}).get("quality_status") or "")
                            if commercial_quality_review_required
                            else delivery_contract["fact_safety_status"]
                        ),
                        "risk_description": (
                            "商业质量修复已耗尽，需要人工复核文章质量。"
                            if commercial_quality_review_required
                            else "文章存在无法自动确认的事实风险，需要对照原始资料人工复核。"
                        ),
                    })
                output_base = self._build_output_base_path(
                    str(parsed_structured_draft.get("title") or request.title)
                )
                delivery_warnings: list[str] = []
                try:
                    if prepared["task"].export_format == "docx":
                        save_path = self._export_article_docx(
                            str(parsed_structured_draft.get("title") or request.title),
                            pre_flow4_text,
                            str(output_base),
                        )
                        docx_path = save_path
                    else:
                        save_path = self._save_article_txt(
                            str(parsed_structured_draft.get("title") or request.title),
                            pre_flow4_text,
                            _safe_scene(prepared["task"].document_type),
                            output_base,
                        )
                        docx_path = ""
                except Exception as exc:
                    save_path = ""
                    docx_path = ""
                    delivery_warnings.append(
                        f"{prepared['task'].export_format.upper()} 产物生成失败："
                        f"{type(exc).__name__}"
                    )
                review_runtime_updates = {
                    "fact_safety_status": delivery_contract[
                        "fact_safety_status"
                    ],
                    "delivery_status": "SUCCESS_WITH_REVIEW",
                    "rendered": True,
                    "entered_postprocessing": False,
                    "flow4_calls": 0,
                    "review_required": True,
                    "delivery_artifact_path": save_path,
                }
                runtime_warning = self._save_delivery_runtime_best_effort(
                    artifact_root=artifact_root,
                    updates=review_runtime_updates,
                )
                if runtime_warning:
                    delivery_warnings.append(runtime_warning)
                selected_chunks = list(
                    prepared["retrieval_result"].get("selected_chunks") or []
                )
                articles.append(
                    ArticleGenResult(
                        article_mode="official_draft",
                        title=str(
                            parsed_structured_draft.get("title") or request.title
                        ),
                        content=pre_flow4_text,
                        scene=_safe_scene(prepared["task"].document_type),
                        retrieved_count=len(selected_chunks),
                        generation_time=round(time.time() - started_at, 2),
                        save_path=save_path,
                        docx_path=docx_path,
                        export_format=prepared["task"].export_format,
                        reference_sources=_build_reference_sources(
                            selected_chunks
                        ),
                        review_notes=[
                            str(item.get("risk_description") or "需人工复核")
                            for item in risk_items
                        ],
                        word_count=pre_flow4_gate["actual_words"],
                        selected_candidate_stage="FLOW3_REVIEW_DELIVERY",
                        final_pipeline_stage_completed=False,
                        fact_safety_rewrite_used=bool(
                            flow3_result.get("fact_safety_rewrite_used")
                        ),
                        user_confirmation_required=True,
                        delivery_status="SUCCESS_WITH_REVIEW",
                        official_success=False,
                        target_word_count=prepared["task"].target_words,
                        fact_risk_count=review_metadata["fact_risk_count"],
                        analysis_risk_count=review_metadata[
                            "analysis_risk_count"
                        ],
                        fact_gap_count=review_metadata["fact_gap_count"],
                        length_gate_status=pre_flow4_gate[
                            "length_gate_status"
                        ],
                        structure_gate_status=pre_flow4_gate[
                            "structure_gate_status"
                        ],
                        risk_items=risk_items,
                        delivery_warnings=delivery_warnings,
                        failure_class="RECOVERABLE_CONTENT_FAILURE",
                        failure_stage=(
                            "COMMERCIAL_QUALITY"
                            if commercial_quality_review_required
                            else "FACT_SAFETY"
                        ),
                        reason=(
                            "商业质量修复耗尽，正文需要人工复核。"
                            if commercial_quality_review_required
                            else "事实安全仍有剩余风险，正文需要人工复核。"
                        ),
                        next_step=(
                            "请对照问题位置和原始资料人工复核后再作为正式稿使用。"
                        ),
                        review_suggestion=(
                            "请对照问题位置和原始资料人工复核后再作为正式稿使用。"
                        ),
                    )
                )
                continue

            try:
                render_result, gate_report = self._render_flow4_for_delivery(
                    parsed_structured_draft=parsed_structured_draft,
                    prepared=prepared,
                    validation_reports=validation_reports,
                    flow3_status=flow3_status,
                    run_id=run_id,
                    artifact_root=artifact_root,
                    output_dir=output_dir,
                    timings=timings,
                )
            except (
                FinalDeliveryQualityError,
                HandoffIntegrityError,
                ValidationError,
                ValueError,
                RuntimeError,
            ) as exc:
                if last_known_good.candidate is None:
                    raise
                articles.append(
                    self._build_review_delivery_article(
                        candidate=last_known_good.candidate,
                        request=request,
                        prepared=prepared,
                        started_at=started_at,
                        issues=[
                            issue_item(
                                stage="FLOW4",
                                issue_type=type(exc).__name__,
                                description=(
                                    "FLOW4 普通合同或渲染校验未通过，已保留 FLOW4 前正文。"
                                ),
                                reason=str(exc),
                            )
                        ],
                        failure_stage="FLOW4",
                        reason="FLOW4 后处理需要人工复核。",
                        flow3_result=flow3_result,
                        gate_report=pre_flow4_gate,
                    )
                )
                continue
            last_known_good.update(
                title=render_result.title,
                content=render_result.final_text,
                structured_draft=parsed_structured_draft,
                stage="FLOW4_RENDERED_TEXT",
                safety_boundary=self._fact_safety_boundary(
                    validation_reports["fact"]
                ),
            )
            artifact_started = time.perf_counter()
            delivered_artifacts, delivery_warnings = deliver_artifacts_resilient(
                render_result,
                output_dir / "flow4",
                formats=(prepared["task"].export_format,),
                output_name=parsed_structured_draft["title"],
                deliverer=deliver_flow4_artifacts,
            )
            _record_elapsed(timings, "artifact_save_ms", artifact_started)
            artifact = delivered_artifacts[0] if delivered_artifacts else None
            timings["total_ms"] = round((time.perf_counter() - run_started) * 1000, 3)
            local_stage_total_ms = round(sum(
                float(value or 0.0)
                for key, value in timings.items()
                if key not in {"model_call_ms", "total_ms"}
            ), 3)
            final_runtime_updates = {
                "flow3_v33_status": flow3_status,
                "fact_safety_status": delivery_contract[
                    "fact_safety_status"
                ],
                "delivery_status": (
                    "SUCCESS_WITH_REVIEW"
                    if (
                        gate_report.get("review_required")
                        or render_result.validation_summary.risk_status
                        == "REVIEW_REQUIRED"
                    )
                    else (
                        "SUCCESS_WITH_REPAIR"
                        if repair_applied
                        else "SUCCESS"
                    )
                ),
                "rendered": True,
                "entered_postprocessing": True,
                "flow4_calls": 1,
                "timings": dict(timings),
                "local_stages_total_ms": local_stage_total_ms,
                "flow4_artifact_path": artifact.file_path if artifact else "",
                "delivery_warnings": delivery_warnings,
                **(
                    {
                        "work_summary_quality_report_path": quality_report_path,
                        "work_summary_quality_gate_summary": (
                            summarize_work_summary_quality_report(quality_report)
                        ),
                        "work_summary_quality_repair_report_path": (
                            quality_repair_trace_path
                        ),
                        "work_summary_quality_status": str(
                            quality_report.get("quality_status") or ""
                        ),
                        "work_summary_quality_repair_rounds": int(
                            quality_report.get("repair_rounds") or 0
                        ),
                    }
                    if quality_report is not None
                    else {}
                ),
                **(
                    gate_report.get("work_summary_length_report") or {}
                ),
            }
            runtime_warning = self._save_delivery_runtime_best_effort(
                artifact_root=artifact_root,
                updates=final_runtime_updates,
            )
            if runtime_warning:
                delivery_warnings.append(runtime_warning)
            logger.info(
                "GENERATION_TIMINGS run_id=%s status=%s model_call_count=%s model_retry_count=%s timings=%s",
                run_id,
                flow3_status,
                int(flow3_result.get("model_call_count") or 0),
                int(flow3_result.get("model_retry_count") or 0),
                json.dumps(timings, ensure_ascii=False, sort_keys=True),
            )
            try:
                review_metadata = sanitize_quality_test_draft(
                    parsed_structured_draft,
                    structured_report=validation_reports["structured"],
                    fact_report=validation_reports["fact"],
                    analysis_report=validation_reports["analysis"],
                )
            except (ValidationError, ValueError, TypeError, RuntimeError) as exc:
                if last_known_good.candidate is None:
                    raise
                articles.append(
                    self._build_review_delivery_article(
                        candidate=last_known_good.candidate,
                        request=request,
                        prepared=prepared,
                        started_at=started_at,
                        issues=[
                            issue_item(
                                stage="FINAL_REVIEW_METADATA",
                                issue_type=type(exc).__name__,
                                description=(
                                    "风险摘要无法自动完成，正文已保留并需人工复核。"
                                ),
                                reason=str(exc),
                            )
                        ],
                        failure_stage="FINAL_REVIEW_METADATA",
                        reason="最终风险摘要需要人工复核。",
                        delivery_warnings=delivery_warnings,
                        flow3_result=flow3_result,
                        gate_report=gate_report,
                    )
                )
                continue
            review_required = bool(
                gate_report.get("review_required")
                or render_result.validation_summary.risk_status == "REVIEW_REQUIRED"
            )
            final_risk_items = list(review_metadata["risk_items"])
            if review_required and not final_risk_items:
                final_risk_items.append(
                    issue_item(
                        stage="FLOW4_VALIDATION",
                        issue_type="FLOW4_REVIEW_REQUIRED",
                        description=(
                            "FLOW4 校验标记需人工复核，请对照原始资料检查全文。"
                        ),
                    )
                )
            selected_chunks = list(
                prepared["retrieval_result"].get("selected_chunks") or []
            )
            articles.append(
                ArticleGenResult(
                    article_mode="official_draft",
                    title=render_result.title,
                    content=render_result.final_text,
                    scene=_safe_scene(prepared["task"].document_type),
                    retrieved_count=len(selected_chunks),
                    generation_time=round(time.time() - started_at, 2),
                    save_path=artifact.file_path if artifact else "",
                    docx_path=(
                        artifact.file_path
                        if artifact and artifact.file_type == "docx"
                        else ""
                    ),
                    export_format=(
                        artifact.file_type
                        if artifact
                        else prepared["task"].export_format
                    ),
                    reference_sources=_build_reference_sources(selected_chunks),
                    review_notes=(
                        ["REVIEW_REQUIRED"] if review_required else []
                    ),
                    word_count=gate_report["actual_words"],
                    selected_candidate_stage="FLOW4_ARTIFACT_DELIVERY",
                    final_pipeline_stage_completed=True,
                    fact_safety_rewrite_used=bool(
                        flow3_result.get("fact_safety_rewrite_used")
                    ),
                    user_confirmation_required=review_required,
                    delivery_status=(
                        "SUCCESS_WITH_REVIEW"
                        if review_required
                        else (
                            "SUCCESS_WITH_REPAIR"
                            if repair_applied
                            else "SUCCESS"
                        )
                    ),
                    official_success=not review_required,
                    fact_risk_count=review_metadata["fact_risk_count"],
                    analysis_risk_count=review_metadata["analysis_risk_count"],
                    fact_gap_count=0,
                    risk_items=final_risk_items,
                    delivery_warnings=delivery_warnings,
                    failure_class=(
                        "RECOVERABLE_CONTENT_FAILURE"
                        if review_required
                        else (
                            "RECOVERABLE_POSTPROCESS_FAILURE"
                            if delivery_warnings
                            else "NONE"
                        )
                    ),
                    failure_stage=(
                        "FLOW4_VALIDATION"
                        if review_required
                        else ("ARTIFACT_DELIVERY" if delivery_warnings else "")
                    ),
                    reason=(
                        "FLOW4 校验仍有需人工确认的风险。"
                        if review_required
                        else (
                            "部分 Artifact 失败，API 正文仍完整交付。"
                            if delivery_warnings
                            else ""
                        )
                    ),
                    next_step=(
                        "请对照问题位置和原始资料人工复核。"
                        if review_required
                        else (
                            "使用 API 正文，并按警告重试所需附件。"
                            if delivery_warnings
                            else "可交付正式稿。"
                        )
                    ),
                    review_suggestion=(
                        "请对照问题位置和原始资料人工复核。"
                        if review_required
                        else ""
                    ),
                    delivery_decision=qualified_decision(
                        repaired=repair_applied,
                        delivery_warnings=delivery_warnings,
                    ) if not review_required else None,
                )
            )

        return ArticleGenBatchResult(
            articles=articles,
            count=len(articles),
            output_dir=str(output_dir),
        )

    def _build_output_base_path(self, title: str) -> Path:
        safe_name = sanitize_title_filename(title, "未命名文章")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = get_output_dir()
        for _ in range(20):
            unique_id = uuid.uuid4().hex[:8]
            candidate = (output_dir / f"{timestamp}_{unique_id}_{safe_name}").resolve()
            if not candidate.with_suffix(".txt").exists() and not candidate.with_suffix(".docx").exists():
                return candidate
        raise FileExistsError("Unable to allocate a unique export path")

    def _save_article_txt(self, title: str, content: str, scene: str, base_path: Path) -> str:
        file_path = base_path.with_suffix(".txt")
        if file_path.exists():
            raise FileExistsError(f"Export path already exists: {file_path}")
        text = f"标题：{title}\n场景：{scene}\n\n{content}"
        file_path.write_text(text, encoding="utf-8")
        return str(file_path)

    def _export_article_docx(self, title: str, content: str, base_path: str) -> str:
        base_file = Path(base_path).resolve()
        output_path = base_file.with_suffix(".docx")
        if output_path.exists():
            raise FileExistsError(f"Export path already exists: {output_path}")
        return export_article_to_docx(
            result={
                "recommended_title": title,
                "body": content,
                "summary": "",
                "risk_words": [],
            },
            cover_image_path="",
            structure_image_path="",
            output_path=str(output_path),
        )

    def _write_fact_source_audit(
        self,
        final_content: str,
        task: OfficialDraftTask,
        content_references: list[dict[str, str]],
        base_path: Path,
    ) -> str:
        audit_path = base_path.with_name(f"{base_path.name}_fact_audit.md")
        audit_result = build_fact_source_audit(final_content, task, content_references)
        audit_markdown = render_fact_source_audit_markdown(audit_result)
        audit_path.write_text(audit_markdown, encoding="utf-8")
        return str(audit_path)

    def _write_hybrid_sidecar(self, base_path: Path, suffix: str, markdown: str) -> str:
        sidecar_path = base_path.with_name(f"{base_path.name}_{suffix}.md")
        sidecar_path.parent.mkdir(parents=True, exist_ok=True)
        sidecar_path.write_text(markdown, encoding="utf-8")
        return str(sidecar_path)

    def _risk_counts(self, audit_result: dict[str, Any]) -> dict[str, int]:
        return {
            "HIGH": int(audit_result.get("high_risk_count", 0) or 0),
            "MEDIUM": int(audit_result.get("medium_risk_count", 0) or 0),
            "LOW": int(audit_result.get("low_risk_count", 0) or 0),
        }

    def _summarize_references(self, references: list[dict[str, Any]], max_chars: int = 1200) -> str:
        parts: list[str] = []
        for item in references[:5]:
            source = str(item.get("source") or item.get("title") or "资料片段")
            text = str(item.get("text") or item.get("content") or item.get("chunk_text") or "")
            if text.strip():
                parts.append(f"【{source}】{text.strip()[:260]}")
        return "\n".join(parts)[:max_chars]

    def _render_hybrid_gate_summary(self, gate_result: dict[str, Any]) -> str:
        initial_counts = gate_result.get("initial_counts", {})
        after_local_counts = gate_result.get("after_local_counts", {})
        final_counts = gate_result.get("final_counts", {})
        warnings = gate_result.get("warnings", []) or []
        lines = [
            "# official_draft Hybrid Fact Safe Gate Summary",
            "",
            f"- FACT_SAFE_GATE_ENABLED: {FACT_SAFE_GATE_ENABLED}",
            f"- FACT_SAFE_LOCAL_REWRITE_ENABLED: {FACT_SAFE_LOCAL_REWRITE_ENABLED}",
            f"- FACT_SAFE_LLM_REPAIR_ENABLED: {FACT_SAFE_LLM_REPAIR_ENABLED}",
            f"- FACT_SAFE_MAX_LLM_REPAIR_CALLS: {FACT_SAFE_MAX_LLM_REPAIR_CALLS}",
            f"- FACT_SAFE_MAX_REPAIR_ROUNDS: {FACT_SAFE_MAX_REPAIR_ROUNDS}",
            f"- 初审 HIGH/MEDIUM/LOW: {initial_counts.get('HIGH', 0)}/{initial_counts.get('MEDIUM', 0)}/{initial_counts.get('LOW', 0)}",
            f"- 本地降险后 HIGH/MEDIUM/LOW: {after_local_counts.get('HIGH', 0)}/{after_local_counts.get('MEDIUM', 0)}/{after_local_counts.get('LOW', 0)}",
            f"- 是否调用 DeepSeek 定向修复: {gate_result.get('llm_repair_called')}",
            f"- 调用原因: {'；'.join(gate_result.get('llm_repair_reasons') or []) or '无'}",
            f"- DeepSeek 调用次数: {gate_result.get('llm_repair_calls', 0)}",
            f"- G3C 无依据陈述改写: {gate_result.get('fact_safety_rewrite_used')}",
            f"- G3C 待确认陈述数量: {gate_result.get('unverified_claim_count', 0)}",
            f"- G3C 待确认类型: {', '.join(gate_result.get('unverified_claim_types') or []) or 'none'}",
            f"- 最终 HIGH/MEDIUM/LOW: {final_counts.get('HIGH', 0)}/{final_counts.get('MEDIUM', 0)}/{final_counts.get('LOW', 0)}",
            f"- 初始结构校验结果: {gate_result.get('structure_initial', {}).get('is_valid')}",
            f"- 最终结构校验结果: {gate_result.get('structure_final', {}).get('is_valid')}",
            f"- 是否输出最终正文: {gate_result.get('final_output_ready')}",
            f"- NEEDS_HUMAN_REVIEW: {gate_result.get('needs_human_review')}",
            "",
            "## Warnings",
        ]
        lines.extend([f"- {warning}" for warning in warnings] or ["无。"])
        lines.append("")
        return "\n".join(lines)

    def _run_hybrid_fact_safe_gate(
        self,
        final_title: str,
        final_content: str,
        task: OfficialDraftTask,
        rag_reference_split: dict[str, Any],
        base_path: Path,
        provider: Any,
        user_fact_anchors: list[UserFactAnchor] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        warnings: list[str] = []
        user_fact_anchors = user_fact_anchors or []
        content_references = list(rag_reference_split.get("content_references", []) or [])
        style_references = list(rag_reference_split.get("style_references", []) or [])

        audit_initial = build_fact_source_audit(final_content, task, content_references)
        structure_initial = validate_structure(final_content, task.document_type, title=final_title)
        candidate_content = final_content
        local_result = {
            "rewritten_text": candidate_content,
            "rewrite_report": {
                "document_type": task.document_type,
                "attempted_count": 0,
                "success_count": 0,
                "items": [],
            },
        }

        if FACT_SAFE_LOCAL_REWRITE_ENABLED:
            local_result = rewrite_high_risk_facts_locally(
                candidate_content,
                audit_initial,
                document_type=task.document_type,
                user_input_summary=task.title,
                content_reference_summary=self._summarize_references(content_references),
                user_fact_anchors=user_fact_anchors,
            )
            candidate_content = str(local_result.get("rewritten_text") or candidate_content)

        audit_after_local = build_fact_source_audit(candidate_content, task, content_references)
        structure_after_local = validate_structure(candidate_content, task.document_type, title=final_title)
        need_llm_repair, repair_reasons = should_call_targeted_repair(
            audit_after_local,
            structure_after_local,
            local_result,
            max_calls=FACT_SAFE_MAX_LLM_REPAIR_CALLS,
            calls_used=0,
        )

        repair_result = {
            "repaired_text": candidate_content,
            "repair_report": {
                "called_llm": False,
                "call_reason": repair_reasons,
                "input_high_count": 0,
                "structure_issues": structure_after_local.get("warnings", []),
                "success": False,
                "error": "",
                "call_count": 0,
                "token_cost": None,
            },
        }
        if need_llm_repair and FACT_SAFE_LLM_REPAIR_ENABLED and FACT_SAFE_MAX_REPAIR_ROUNDS >= 1:
            high_items = [item for item in audit_after_local.get("items", []) if item.get("risk_level") == "HIGH"]
            repair_result = targeted_quality_repair(
                original_text=final_content,
                locally_rewritten_text=candidate_content,
                audit_items_high=high_items,
                structure_report=structure_after_local.get("structure_report", {}),
                document_type=task.document_type,
                task_model=task,
                content_references_summary=self._summarize_references(content_references),
                style_references_summary=self._summarize_references(style_references),
                provider=provider,
                call_llm=True,
            )
            repair_result["repair_report"]["call_reason"] = repair_reasons
            candidate_content = str(repair_result.get("repaired_text") or candidate_content)
            if not repair_result.get("repair_report", {}).get("success"):
                warnings.append("DeepSeek 定向修复失败或返回为空，已输出当前最佳正文。")

        user_direct_facts = [
            task.title,
            task.writing_purpose,
            task.reference_scope,
            task.business_domain,
            task.level,
            task.org_type,
        ]
        user_fact_check_before_g3c = validate_user_fact_anchors(candidate_content, user_fact_anchors)
        unsupported_guard_result = UnsupportedClaimGuard().inspect_and_rewrite(
            candidate_content,
            user_direct_facts=user_direct_facts,
            retrieval_top5=(content_references + style_references)[:5],
            target_subject=derive_current_subject(task),
            document_type=task.document_type,
            user_fact_anchors=user_fact_anchors,
        )
        candidate_content = str(unsupported_guard_result.get("rewritten_text") or candidate_content)

        user_fact_check = validate_user_fact_anchors(candidate_content, user_fact_anchors)
        unsupported_guard_result["user_fact_check_before_rewrite"] = _user_fact_check_payload(user_fact_check_before_g3c, user_fact_anchors)
        unsupported_guard_result["user_fact_check_after_rewrite"] = _user_fact_check_payload(user_fact_check, user_fact_anchors)
        user_fact_fallback = False
        if not user_fact_check.passed:
            if unsupported_guard_result.get("rewrite_used"):
                conflict_diagnostics = {
                    "error_type": FACT_SAFETY_REWRITE_CONFLICT,
                    "candidate_stage": STAGE_LOCAL_REWRITE,
                    "rewrite_used": bool(unsupported_guard_result.get("rewrite_used")),
                    "unsupported_claim_types": list(unsupported_guard_result.get("unsupported_claim_types", []) or []),
                    "user_fact_check_before_rewrite": _user_fact_check_payload(user_fact_check_before_g3c, user_fact_anchors),
                    "user_fact_check_after_rewrite": _user_fact_check_payload(user_fact_check, user_fact_anchors),
                }
                unsupported_guard_result["conflict_diagnostics"] = conflict_diagnostics
                self._write_hybrid_sidecar(
                    base_path,
                    "fact_safety_rewrite_conflict",
                    json.dumps(conflict_diagnostics, ensure_ascii=False, indent=2),
                )
                raise FactSafetyRewriteConflictError(user_fact_check, unsupported_guard_result)
            warnings.append(USER_FACT_RETENTION_ERROR_MESSAGE)
            candidate_content = final_content
            user_fact_fallback = True
            user_fact_check = validate_user_fact_anchors(candidate_content, user_fact_anchors)
            if not user_fact_check.passed:
                raise UserFactRetentionError(user_fact_check)

        audit_final = build_fact_source_audit(candidate_content, task, content_references)
        structure_final = validate_structure(candidate_content, task.document_type, title=final_title)
        final_counts = self._risk_counts(audit_final)
        final_content = clean_official_draft_text(candidate_content)
        repair_report = repair_result.get("repair_report", {}) or {}
        llm_repair_calls = int(repair_report.get("call_count", 0) or 0)
        targeted_repair_failed = bool(repair_report.get("called_llm") and not repair_report.get("success"))
        selected_candidate_stage = STAGE_FACT_REPAIR if repair_report.get("success") else STAGE_LOCAL_REWRITE
        if user_fact_fallback:
            selected_candidate_stage = ""
        elif not repair_report.get("called_llm"):
            selected_candidate_stage = STAGE_LOCAL_REWRITE if local_result.get("rewritten_text") != final_content else ""
        final_content_empty_or_too_short = len(re.findall(r"[\u4e00-\u9fff]", final_content)) < 80
        final_output_ready = bool(final_content.strip())

        local_report = local_result.get("rewrite_report", {})
        local_failed = any(
            item.get("risk_level_before") == "HIGH"
            and not item.get("success")
            and item.get("failure_reason") not in {"not_high_risk", "matched_content_reference", "claim_type_not_local_rewritable"}
            for item in local_report.get("items", []) or []
        )
        if local_failed:
            warnings.append("存在本地降险失败片段，但最终状态达标时不触发强制人工复核。")
        if repair_report.get("called_llm"):
            warnings.append("DeepSeek 定向修复已调用。")
        if unsupported_guard_result.get("rewrite_used"):
            warnings.append("G3C 无依据陈述安全改写已执行。")
        if final_counts["HIGH"] > 1:
            warnings.append("最终 HIGH 数量仍大于 1。")
        elif final_counts["HIGH"] == 1:
            warnings.append("最终仍有 1 个 HIGH，建议人工关注。")
        if not structure_final.get("is_valid"):
            warnings.append("最终结构校验失败。")
        if final_content_empty_or_too_short:
            warnings.append("最终正文为空或过短。")
        if llm_repair_calls > FACT_SAFE_MAX_LLM_REPAIR_CALLS:
            warnings.append("DeepSeek 定向修复调用次数超过上限。")

        needs_human_review = _needs_human_review_for_hybrid_gate(
            final_high_count=final_counts["HIGH"],
            final_structure_valid=bool(structure_final.get("is_valid")),
            targeted_repair_failed=targeted_repair_failed,
            llm_repair_calls=llm_repair_calls,
            final_content_empty_or_too_short=final_content_empty_or_too_short,
            final_output_written=final_output_ready,
        )

        gate_result = {
            "audit_initial": audit_initial,
            "audit_after_local": audit_after_local,
            "audit_final": audit_final,
            "structure_initial": structure_initial,
            "structure_after_local": structure_after_local,
            "structure_final": structure_final,
            "local_result": local_result,
            "repair_result": repair_result,
            "unsupported_claim_guard": unsupported_guard_result,
            "initial_counts": self._risk_counts(audit_initial),
            "after_local_counts": self._risk_counts(audit_after_local),
            "final_counts": final_counts,
            "llm_repair_called": bool(repair_report.get("called_llm")),
            "llm_repair_reasons": repair_reasons,
            "llm_repair_calls": llm_repair_calls,
            "targeted_repair_failed": targeted_repair_failed,
            "selected_candidate_stage": selected_candidate_stage,
            "fallback_used": targeted_repair_failed or user_fact_fallback,
            "fallback_reason": FALLBACK_USER_FACT_RETENTION_FAILED if user_fact_fallback else (FALLBACK_FACT_REPAIR_FAILED if targeted_repair_failed else None),
            "final_pipeline_stage_completed": not (targeted_repair_failed or user_fact_fallback),
            "user_fact_check": {
                "passed": user_fact_check.passed,
                "total_count": user_fact_check.total_count,
                "passed_count": user_fact_check.passed_count,
                "missing_anchor_ids": list(user_fact_check.missing_anchor_ids),
                "retained_anchor_ids": list(user_fact_check.retained_anchor_ids),
                "reassigned_anchor_ids": list(user_fact_check.reassigned_anchor_ids),
            },
            "needs_human_review": needs_human_review,
            "fact_safety_rewrite_used": bool(unsupported_guard_result.get("rewrite_used")),
            "user_confirmation_required": bool(unsupported_guard_result.get("user_confirmation_required")),
            "unverified_claim_count": int(unsupported_guard_result.get("unsupported_claim_count", 0) or 0),
            "unverified_claim_types": list(unsupported_guard_result.get("unsupported_claim_types", []) or []),
            "final_output_ready": final_output_ready,
            "warnings": warnings,
        }

        self._write_hybrid_sidecar(base_path, "fact_audit_initial", render_fact_source_audit_markdown(audit_initial))
        self._write_hybrid_sidecar(base_path, "fact_audit_after_local", render_fact_source_audit_markdown(audit_after_local))
        self._write_hybrid_sidecar(base_path, "fact_audit_final", render_fact_source_audit_markdown(audit_final))
        self._write_hybrid_sidecar(base_path, "fact_rewrite_report", render_fact_rewrite_report_markdown(local_report))
        self._write_hybrid_sidecar(base_path, "unsupported_claim_guard", render_unsupported_claim_guard_markdown(unsupported_guard_result))
        self._write_hybrid_sidecar(base_path, "structure_report", render_structure_report_markdown(structure_final))
        self._write_hybrid_sidecar(base_path, "targeted_repair_report", render_targeted_repair_report_markdown(repair_result.get("repair_report", {})))
        self._write_hybrid_sidecar(base_path, "hybrid_gate_summary", self._render_hybrid_gate_summary(gate_result))

        return final_content, gate_result

    async def generate(self, request: ArticleGenRequest) -> ArticleGenBatchResult:
        return await self.generate_official_draft(request)

    async def generate_official_draft(self, request: ArticleGenRequest) -> ArticleGenBatchResult:
        task = build_official_draft_task(request)

        settings = get_settings()
        gen_cfg = settings.generation
        output_dir = get_output_dir()
        provider = get_llm_provider()
        start_time = time.time()

        article_mode = "official_draft"
        target_words = task.target_words
        export_format = task.export_format

        prompt_scene = task.document_type
        if prompt_scene:
            scene = _safe_scene(prompt_scene)
        else:
            try:
                classify_result = classify_by_title_or_query(task.title)
                scene = _safe_scene(classify_result.get("primary_scene"))
            except Exception:
                scene = DEFAULT_SCENE

        rag_query = task.title
        logger.info("ARTICLE_MODE=%s", article_mode)
        logger.info("TARGET_WORDS=%s", target_words)

        try:
            retrieval_payload = retrieve_documents(task, search_fn=search_detailed)
            rag_query = str(retrieval_payload.get("query_text") or rag_query)
        except Exception as exc:
            if _is_retrieval_infra_exception(exc):
                logger.exception("RAG_INFRA_FAILED error_type=%s", type(exc).__name__)
                raise RetrievalInfraError(rag_query=rag_query, cause=exc) from exc
            logger.exception("RAG_INTERNAL_FAILED error_type=%s", type(exc).__name__)
            raise

        logger.info("RAG_QUERY=%s", rag_query)
        logger.info("RAG_TOP_K=%s", "100_DOCUMENT_LEVEL")

        raw_rag_results = list(retrieval_payload.get("raw_chunk_candidates", []) or [])
        raw_results_count = len(raw_rag_results)
        ranked_documents = list(retrieval_payload.get("ranked_documents", []) or [])
        primary_document_id = retrieval_payload.get("primary_document_id")
        primary_document = next(
            (item for item in ranked_documents if item.get("document_id") == primary_document_id),
            {},
        )
        top_score = float(
            (primary_document.get("score_components") or {}).get("semantic_aggregate", 0.0)
            or 0.0
        )
        top_source = str(primary_document.get("source_title", "") or "")
        diagnostics = retrieval_payload.get("retrieval_diagnostics") or {}
        filtered_samples = list(diagnostics.get("filtered_samples", []) or [])
        rebuilt_doc_ids = list(diagnostics.get("rebuilt_doc_ids", []) or [])
        rag_results = list(retrieval_payload.get("selected_chunks", []) or [])
        rag_filter_debug = [
            {
                "source": item.get("source_title", ""),
                "score": item.get("document_score", 0.0),
                "reason": item.get("selection_reason", "rejected"),
            }
            for item in retrieval_payload.get("rejected_documents", []) or []
        ]
        valid_results_count = len(rag_results)
        logger.info("RAG_RAW_RESULTS_COUNT=%s", raw_results_count)
        logger.info("RAG_RESULTS_COUNT=%s", len(rag_results))
        logger.info("RAG_TOP_SCORE=%s", round(top_score, 4))
        logger.info("RAG_TOP_SOURCE=%s", top_source)

        if rebuilt_doc_ids:
            logger.info("RAG_REBUILT_DOC_IDS=%s", ",".join(rebuilt_doc_ids))

        for item in rag_results:
            logger.info(
                "RAG_RESULT source=%s score=%s text_length=%s preview=%s",
                item["source"],
                item["score"],
                len(item["text"]),
                _safe_log_text(item["text"], 200),
            )

        for item in filtered_samples:
            logger.info(
                "RAG_FILTERED_CHUNK source=%s reason=%s preview=%s",
                _extract_result_source(item),
                item.get("filter_reason", ""),
                _safe_log_text(_extract_result_text(item), 200),
            )

        for item in rag_filter_debug:
            logger.info(
                "RAG_RESULT_FILTER source=%s score=%s reason=%s",
                item["source"],
                item["score"],
                item["reason"],
            )

        has_rag_context = (
            retrieval_payload.get("retrieval_status") == "SUFFICIENT"
            and bool(rag_results)
        )
        rag_empty_fallback = not has_rag_context
        logger.info("RAG_VALID_RESULTS_COUNT=%s", valid_results_count)
        logger.info("PROMPT_HAS_RAG_CONTEXT=%s", str(has_rag_context).lower())
        logger.info("RAG_EMPTY_FALLBACK=%s", str(rag_empty_fallback).lower())

        if not has_rag_context:
            reason = str(
                retrieval_payload.get("insufficient_reason")
                or "no_document_passed_relevance_gate"
            )
            topic = task.topic
            error = NoRelevantMaterialError(
                topic=topic,
                rag_query=rag_query,
                retrieved_count=0,
                top_score=top_score,
                reason=reason if raw_results_count == 0 else f"{reason};raw_results={raw_results_count}",
            )
            logger.warning("NO_MATERIAL_NO_GENERATION=%s", error.to_response_detail())
            raise error

        material_card = _build_content_material_card(rag_results)
        rag_context = _format_rag_context(rag_results)
        rag_reference_split = split_rag_references(rag_results)
        rag_reference_block = build_rag_reference_block(
            rag_reference_split["content_references"],
            rag_reference_split["style_references"],
        )
        reference_sources = _build_reference_sources(rag_results)
        user_fact_anchors = extract_user_fact_anchors(task)
        generation_prompt = build_official_draft_prompt(
            task=task,
            rag_context=rag_context,
            reference_sources=reference_sources,
            rag_reference_block=rag_reference_block,
            user_fact_anchors=user_fact_anchors,
        )

        logger.info(
            "PROMPT_USER_REQUIREMENT=%s",
            _safe_log_text(
                "\n".join(
                    [
                        f"主题：{task.title}",
                        f"最终成品文种：{task.document_type or '未指定'}",
                        f"写作目的：{task.writing_purpose or '未指定'}",
                        f"内容领域：{task.business_domain or '未指定'}",
                        f"层级语气：{task.level or '未指定'}",
                        f"单位语境：{task.org_type or '未指定'}",
                        f"生成模式：{article_mode}",
                        f"目标字数：{target_words}",
                    ]
                ),
                1000,
            ),
        )
        logger.info("PROMPT_MATERIAL_CARD=%s", _safe_log_text(_format_content_material_card(material_card), 2000))
        logger.info("PROMPT_RAG_CONTEXT=%s", _safe_log_text(rag_context, 2000))
        logger.info("PROMPT_TASK=%s", _safe_log_text(generation_prompt, 1200))

        count = max(1, min(request.count, 10))
        articles: list[ArticleGenResult] = []

        for index in range(count):
            temperature = request.temperature or 0.7
            if count > 1:
                temperature = min(2.0, max(0.1, temperature + (index - (count - 1) / 2) * 0.05))

            attempt_prompt = generation_prompt
            final_title = task.title
            final_content = ""
            final_self_check: dict[str, Any] | None = None
            last_valid_candidate: _ValidCandidate | None = None
            hybrid_gate_result: dict[str, Any] = {}
            latest_failure_reason: str | None = None
            latest_failure_error: Exception | None = None
            fallback_used = False
            fallback_reason: str | None = None
            selected_candidate_stage = STAGE_INITIAL_GENERATION
            final_pipeline_stage_completed = True
            max_attempts = MAX_GENERATION_ATTEMPTS
            if (
                article_mode == "official_draft"
                and task.document_type == PARTY_LECTURE_DOCUMENT_TYPE
            ):
                max_attempts += 1

            for attempt in range(max_attempts):
                attempt_stage = STAGE_INITIAL_GENERATION if attempt == 0 else STAGE_REFINEMENT
                try:
                    response_text = provider.generate_text(
                        prompt=attempt_prompt,
                        system_prompt=SYSTEM_PROMPT,
                        temperature=temperature,
                    )
                except Exception as exc:
                    latest_failure_error = exc
                    if last_valid_candidate and _is_generation_transport_exception(exc):
                        fallback_used = True
                        fallback_reason = FALLBACK_LATEST_CANDIDATE_TRANSPORT_FAILED
                        final_pipeline_stage_completed = False
                        logger.warning(
                            "GENERATION_TRANSPORT_FAILED_USING_LAST_VALID attempt=%s stage=%s error_type=%s",
                            attempt + 1,
                            attempt_stage,
                            type(exc).__name__,
                        )
                        break
                    raise

                generated_title, generated_body = _parse_generated_article(response_text)
                final_title = generated_title or task.title
                if article_mode == "full_article" and full_article_title:
                    final_title = full_article_title
                final_content = generated_body or response_text.strip()
                self_check = _run_basic_self_check(
                    final_title,
                    task.title,
                    final_content,
                    article_mode,
                    task.document_type,
                    material_card,
                    rag_results,
                )
                final_self_check = self_check
                candidate_title = clean_official_draft_text(final_title)
                candidate_content = clean_official_draft_text(final_content)
                candidate_gate_error = ""
                candidate_validate_error = ""
                candidate_fact_check = validate_user_fact_anchors(candidate_content, user_fact_anchors)
                if self_check.get("party_lecture_final_gate_failed"):
                    candidate_gate_error = _build_party_lecture_final_gate_error(self_check)
                else:
                    try:
                        validate_official_draft_final(candidate_title, candidate_content, target_words=target_words)
                    except Exception as exc:
                        candidate_validate_error = str(exc)
                        latest_failure_error = exc

                if candidate_content.strip() and not candidate_gate_error and not candidate_validate_error and candidate_fact_check.passed:
                    last_valid_candidate = _ValidCandidate(
                        title=candidate_title,
                        content=candidate_content,
                        self_check=self_check,
                        stage=attempt_stage,
                        attempt_number=attempt + 1,
                    )
                    latest_failure_reason = None
                    latest_failure_error = None
                    logger.info(
                        "LAST_VALID_CANDIDATE_UPDATED attempt=%s stage=%s",
                        attempt + 1,
                        attempt_stage,
                    )
                elif candidate_gate_error:
                    latest_failure_reason = FALLBACK_DOCUMENT_TYPE_REFINEMENT_FAILED
                    latest_failure_error = ValueError(candidate_gate_error)
                elif not candidate_fact_check.passed:
                    latest_failure_reason = FALLBACK_USER_FACT_RETENTION_FAILED
                    latest_failure_error = UserFactRetentionError(candidate_fact_check)
                else:
                    latest_failure_reason = FALLBACK_LATEST_CANDIDATE_VALIDATION_FAILED
                    latest_failure_error = ValueError(candidate_validate_error or OFFICIAL_DRAFT_FINAL_VALIDATE_ERROR)

                logger.info(
                    "SELF_CHECK_RESULT attempt=%s passed=%s failures=%s scene_hits=%s action_hits=%s keyword_hits=%s",
                    attempt + 1,
                    self_check["passed"],
                    "|".join(self_check["failures"]) or "none",
                    "|".join(self_check["scene_hits"]) or "none",
                    "|".join(self_check["action_hits"]) or "none",
                    "|".join(self_check["keyword_hits"]) or "none",
                )

                if self_check["passed"] and last_valid_candidate and latest_failure_error is None:
                    break
                if attempt + 1 >= max_attempts:
                    break

                attempt_prompt = _build_revision_prompt(generation_prompt, final_content, self_check, article_mode)
                logger.info("SELF_CHECK_RETRY=true")

            if final_self_check and final_self_check.get("party_lecture_final_gate_failed"):
                logger.warning(
                    "PARTY_LECTURE_FINAL_GATE_FAILED title_hits=%s body_hits=%s tone_hits=%s failures=%s",
                    "|".join(final_self_check.get("party_lecture_title_report_hits", [])) or "none",
                    "|".join(final_self_check.get("party_lecture_body_report_hits", [])) or "none",
                    "|".join(final_self_check.get("party_lecture_teaching_tone_hits", [])) or "none",
                    "|".join(final_self_check.get("failures", [])) or "none",
                )
                if last_valid_candidate:
                    fallback_used = True
                    fallback_reason = FALLBACK_DOCUMENT_TYPE_REFINEMENT_FAILED
                    final_pipeline_stage_completed = False
                else:
                    raise ValueError(_build_party_lecture_final_gate_error(final_self_check))

            if last_valid_candidate is None:
                if latest_failure_error:
                    raise latest_failure_error
                raise ValueError(OFFICIAL_DRAFT_FINAL_VALIDATE_ERROR)

            if latest_failure_error and not fallback_used and (
                latest_failure_reason in {
                    FALLBACK_LATEST_CANDIDATE_VALIDATION_FAILED,
                    FALLBACK_USER_FACT_RETENTION_FAILED,
                }
            ):
                fallback_used = True
                fallback_reason = latest_failure_reason
                final_pipeline_stage_completed = False

            final_title = last_valid_candidate.title
            final_content = last_valid_candidate.content
            selected_candidate_stage = last_valid_candidate.stage
            validate_official_draft_final(final_title, final_content, target_words=target_words)
            final_fact_check = validate_user_fact_anchors(final_content, user_fact_anchors)
            if not final_fact_check.passed:
                raise UserFactRetentionError(final_fact_check)

            base_path = self._build_output_base_path(final_title)
            if FACT_SAFE_GATE_ENABLED:
                try:
                    final_content, hybrid_gate_result = self._run_hybrid_fact_safe_gate(
                        final_title=final_title,
                        final_content=final_content,
                        task=task,
                        rag_reference_split=rag_reference_split,
                        base_path=base_path,
                        provider=provider,
                        user_fact_anchors=user_fact_anchors,
                    )
                    validate_official_draft_final(final_title, final_content, target_words=target_words)
                    gate_fact_check = validate_user_fact_anchors(final_content, user_fact_anchors)
                    if not gate_fact_check.passed:
                        raise UserFactRetentionError(gate_fact_check)
                    gate_selected_stage = str(hybrid_gate_result.get("selected_candidate_stage") or "")
                    if gate_selected_stage:
                        selected_candidate_stage = gate_selected_stage
                    if "final_pipeline_stage_completed" in hybrid_gate_result and not fallback_used:
                        final_pipeline_stage_completed = bool(
                            hybrid_gate_result.get("final_pipeline_stage_completed")
                        )
                    if hybrid_gate_result.get("fallback_used"):
                        fallback_used = True
                        fallback_reason = str(hybrid_gate_result.get("fallback_reason") or FALLBACK_FACT_REPAIR_FAILED)
                        if gate_selected_stage:
                            selected_candidate_stage = gate_selected_stage
                        final_pipeline_stage_completed = bool(
                            hybrid_gate_result.get("final_pipeline_stage_completed", False)
                        )
                    logger.info(
                        "HYBRID_FACT_SAFE_GATE_DONE final_high=%s needs_human_review=%s",
                        hybrid_gate_result.get("final_counts", {}).get("HIGH", 0),
                        hybrid_gate_result.get("needs_human_review"),
                    )
                except Exception as exc:
                    logger.warning("HYBRID_FACT_SAFE_GATE_FAILED=%s", exc)
                    raise
            else:
                try:
                    fact_audit_path = self._write_fact_source_audit(
                        final_content,
                        task,
                        rag_reference_split["content_references"],
                        base_path,
                    )
                    logger.info("FACT_SOURCE_AUDIT_WRITTEN=%s", fact_audit_path)
                except Exception as exc:
                    logger.warning("FACT_SOURCE_AUDIT_WRITE_FAILED=%s", exc)

            if export_format == "txt":
                save_path = self._save_article_txt(final_title, final_content, scene, base_path)
                docx_path = ""
            else:
                docx_path = self._export_article_docx(final_title, final_content, str(base_path))
                save_path = docx_path

            articles.append(
                ArticleGenResult(
                    article_mode=article_mode,
                    title=final_title,
                    content=final_content,
                    scene=scene,
                    retrieved_count=len(rag_results),
                    tokens_used=0,
                    generation_time=round(time.time() - start_time, 2),
                    save_path=save_path,
                    docx_path=docx_path,
                    export_format=export_format,
                    reference_sources=reference_sources,
                    word_count=len(final_content),
                    fallback_used=fallback_used,
                    fallback_reason=fallback_reason,
                    selected_candidate_stage=selected_candidate_stage,
                    final_pipeline_stage_completed=final_pipeline_stage_completed,
                    fact_safety_rewrite_used=bool(hybrid_gate_result.get("fact_safety_rewrite_used", False)),
                    user_confirmation_required=bool(hybrid_gate_result.get("user_confirmation_required", False)),
                    unverified_claim_count=int(hybrid_gate_result.get("unverified_claim_count", 0) or 0),
                    unverified_claim_types=list(hybrid_gate_result.get("unverified_claim_types", []) or []),
                )
            )

        return ArticleGenBatchResult(
            articles=articles,
            count=len(articles),
            output_dir=str(output_dir),
        )
