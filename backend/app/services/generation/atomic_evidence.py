"""Flow 3 v3 adapter from frozen Flow 2 results to atomic evidence.

The adapter is deterministic and non-generative.  Source text is used only while
building and validating records; it is never retained in the model-visible pack.
"""

from __future__ import annotations

import hashlib
import json
import re
import copy
from collections import Counter
from enum import Enum
from typing import Any, Iterable

from app.services.generation.evidence_safety import MAX_LOCAL_ISOLATION_COUNT
from app.services.generation.reference_structure import validate_reference_structure
from app.services.generation.work_summary_planner import (
    REFERENCE_COMPATIBLE,
    REFERENCE_INCOMPATIBLE,
    REFERENCE_PARTIAL,
    is_work_summary_task,
    plan_work_summary,
    replan_work_summary_sections,
)
from app.services.generation.work_summary_evidence_chain import (
    CHAIN_CLOSED_LOOP,
    CHAIN_MULTI_STEP,
    ORDER_AVAILABLE,
    build_safe_work_chain_pack,
    model_visible_chain_summary,
)
from app.services.generation.work_summary_capacity_budget import (
    build_evidence_capacity_budget,
    refresh_capacity_budget_with_anchors,
)


EVIDENCE_TYPES = {"context", "risk", "action", "mechanism", "scene", "result", "expression"}
FACT_STATUSES = {"explicit", "derived_generalization", "style_only"}
SEMANTIC_STATE_CONTRACT_VERSION = "semantic-state-contract-1"


def admit_full_draft_registry(registry: dict, *, expected_sha256: str) -> dict:
    """Admit an already authorized snapshot, without changing any original scope.

    The expected digest must come from the local source owner, never from the
    public generation request. This is integrity admission, not Fact Safety.
    """
    from app.models.full_article import canonical_json, text_sha, FullArticleContractError
    snapshot = copy.deepcopy(registry)
    if not snapshot or text_sha(canonical_json(snapshot)) != expected_sha256:
        raise FullArticleContractError("FULL_DRAFT_AUTHORIZATION_SNAPSHOT_CHANGED")
    for eid, row in snapshot.items():
        anchor, fact, source = row.get("anchor", {}), row.get("fact", {}), row.get("source_scope", {})
        scope = row.get("section_id")
        if (not isinstance(scope, str) or not scope or scope != scope.strip() or scope.upper() == "GLOBAL" or scope != anchor.get("section_id")
                or eid != anchor.get("evidence_id") or eid != fact.get("evidence_id")
                or not source.get("document_id") or not source.get("chunk_id")
                or not source.get("source_span")
                or text_sha(source["source_span"]) != source.get("source_span_sha256")):
            raise FullArticleContractError("FULL_DRAFT_SOURCE_OR_SCOPE_INVALID")
        # Unknown states and uninterpreted limitations remain explicit review
        # inputs. No default writability or COMPLETED state is supplied here.
        if (anchor.get("semantic_state") not in SEMANTIC_STATES or not fact.get("semantic_state")
                or anchor["semantic_state"] != fact["semantic_state"]
                or not isinstance(fact.get("writability"), dict)):
            raise FullArticleContractError("FULL_DRAFT_STATE_OR_USAGE_UNAVAILABLE")
    return snapshot


def build_full_draft_anchor_input(task: Any, retrieval: dict, *, evidence_pack: dict | None = None) -> dict:
    """Current RAG -> existing Atomic owner -> plan-free Anchor projection.

    Existing explicit evidence_scope_id belongs to the source owner. Otherwise
    a *new* atom is confined to its exact document/chunk/span identity; this is
    source authorization, not an inferred article section. Existing registries
    use admit_full_draft_registry and are never re-scoped by this function.
    No content plan, heading, importance, order, budget or Writer task is made.
    """
    from app.models.full_article import canonical_json, text_sha, FullArticleContractError
    from app.services.generation.work_summary_evidence_group import writable_fact_diagnostic
    if retrieval.get("retrieval_status") != "SUFFICIENT" or not retrieval.get("selected_chunks"):
        raise FullArticleContractError("FULL_DRAFT_MATERIAL_UNAVAILABLE")
    authoritative = build_atomic_evidence_pack(task, retrieval, exact_source_spans=True)
    report = validate_atomic_evidence_pack(authoritative, retrieval)
    if not report["passed"]:
        raise InvalidEvidencePackError(report)
    # A caller cannot upgrade state, remove a limitation or insert an ID in a
    # separately supplied pack. Reproduce the current owner's deterministic input.
    if evidence_pack is not None and canonical_json(evidence_pack) != canonical_json(authoritative):
        raise FullArticleContractError("FULL_DRAFT_ATOMIC_INPUT_CHANGED")
    chunks = {}
    for chunk in retrieval["selected_chunks"]:
        meta = chunk.get("metadata") or {}
        key = (str(chunk.get("document_id") or chunk.get("doc_id") or ""),
               str(chunk.get("chunk_id") or meta.get("chunk_id") or ""))
        if key in chunks and canonical_json(chunks[key]) != canonical_json(chunk):
            raise FullArticleContractError("FULL_DRAFT_SOURCE_IDENTITY_AMBIGUOUS")
        chunks[key] = chunk
    registry, unavailable = {}, []
    for atom in authoritative.get("evidence", []):
        eid = atom["evidence_id"]
        key = (atom.get("document_id"), atom.get("chunk_id"))
        chunk = chunks.get(key, {})
        meta = chunk.get("metadata") or {}
        source_text = str(chunk.get("text") or chunk.get("chunk_text") or "")
        span = next((s for s in _source_fragments(source_text, exact_source_spans=True)
                     if text_sha(s) == atom.get("source_span_sha256")), "")
        declared = [obj["evidence_scope_id"] for obj in (chunk, meta) if "evidence_scope_id" in obj]
        reason = ""
        if not all(key) or not span or span not in source_text:
            reason = "SOURCE_BINDING_UNAVAILABLE"
        elif declared and (any(not isinstance(s, str) or not s.strip() or s != s.strip() or s.upper() == "GLOBAL" for s in declared)
                           or len(set(declared)) != 1):
            reason = "SOURCE_SCOPE_UNAVAILABLE"
        elif (atom.get("semantic_state") not in SEMANTIC_STATES
              or atom.get("semantic_state_contract_version") != SEMANTIC_STATE_CONTRACT_VERSION):
            reason = "SEMANTIC_STATE_UNAVAILABLE"
        elif evidence_writability(atom, authoritative) != "fact_anchorable":
            reason = "NOT_FACT_ANCHORABLE"
        if reason:
            unavailable.append({"evidence_id": eid, "status": "UNAVAILABLE", "reason": reason,
                                "atomic_evidence": copy.deepcopy(atom)})
            continue
        source = {"document_id": key[0], "chunk_id": key[1],
                  "source_span": span, "source_span_sha256": atom["source_span_sha256"]}
        scope = declared[0] if declared else "SOURCE-" + text_sha(canonical_json({
            k: source[k] for k in ("document_id", "chunk_id", "source_span_sha256")}))
        exact = _bound_exact_values(atom)
        anchor = {"evidence_id": eid, "section_id": scope, "role": "supporting",
                  "canonical_subject": str(atom.get("canonical_subject") or atom.get("subject") or ""),
                  "canonical_action": str(atom.get("canonical_action") or atom.get("action") or ""),
                  "canonical_object": str(atom.get("canonical_object") or atom.get("object") or atom.get("result") or ""),
                  "exact_values": exact, "forbidden_detail_types": [k for k in _EXACT_VALUE_FIELDS if k not in exact],
                  **_semantic_state_fields_for_propagation(atom)}
        constraints = {"atomic": copy.deepcopy(atom),
                       "source": {k: copy.deepcopy(v) for k, v in chunk.items() if k in {"limitation", "limitations", "writability"}},
                       "metadata": {k: copy.deepcopy(v) for k, v in meta.items() if k in {"limitation", "limitations", "writability"}}}
        # Never clean placeholders, shorten qualifiers, or reinterpret a source
        # limitation for Prompt appearance. W2B sees original restrictions.
        restrictions = [obj[k] for obj in (atom, chunk, meta) for k in ("limitation", "limitations") if obj.get(k)]
        usage = copy.deepcopy(atom.get("writability") or writable_fact_diagnostic(span))
        for obj in (chunk, meta):
            if obj.get("writability"):
                restrictions.append({"source_writability": copy.deepcopy(obj["writability"])})
        fact = {"evidence_id": eid, "fact": span, "source_kind": "source_span", "exact_values": exact,
                "semantic_state": atom["semantic_state"], "writability": usage,
                "semantic_constraints": semantic_state_payload(atom)}
        registry[eid] = {"section_id": scope, "anchor": anchor, "fact": fact, "source_scope": source,
                         "limitations": copy.deepcopy(restrictions), "input_constraints": constraints}
    if not registry:
        raise FullArticleContractError("FULL_DRAFT_NO_USABLE_ANCHORS")
    digest = text_sha(canonical_json(registry))
    return {"registry": admit_full_draft_registry(registry, expected_sha256=digest),
            "registry_sha256": digest, "unavailable": unavailable,
            "restrictions": {"atomic_prohibited_inference": copy.deepcopy(authoritative["prohibited_inference"]),
                **{k: copy.deepcopy(retrieval[k]) for k in (
                "prohibited_inference", "source_policy", "limitation", "limitations", "time_risk_overlay") if k in retrieval}},
            "atomic_report": report, "atomic_evidence": authoritative,
            "scope_policy": "EXPLICIT_SOURCE_SCOPE_OR_EXACT_SOURCE_IDENTITY"}


class SemanticState(str, Enum):
    """Deterministic source-fact state owned by Atomic Evidence."""

    COMPLETED = "COMPLETED"
    ONGOING = "ONGOING"
    PARTIAL = "PARTIAL"
    PROBLEM = "PROBLEM"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
    GAP = "GAP"
    RESULT = "RESULT"
    PLAN = "PLAN"
    RECOMMENDATION = "RECOMMENDATION"
    CONDITIONAL = "CONDITIONAL"
    CONTEXT = "CONTEXT"
    UNKNOWN = "UNKNOWN"


SEMANTIC_STATES = {state.value for state in SemanticState}

# Ordered deterministic signals. Longer directional expressions are matched
# before their shorter action words; this is not a free-standing blacklist.
_PLAN_SIGNAL_RE = re.compile(
    r"下一步(?:将|拟|计划)?|今后(?:将|拟|计划)?|后续(?:将|拟|计划)?|未来(?:将|拟|计划)?|"
    r"计划(?:于|在|开展|实施|推进|完善|建设|完成)?|拟(?:于|将|开展|实施|推进|完善|建设)|"
    r"力争|争取|预计|预期|目标(?:是|为|到|在)|"
    r"(?:\d{4}年(?:底|末)?|年底|年末|本年度|未来\d+年)(?:之)?前|"
    r"将要|将继续|将进一步|"
    r"将(?:组织|召开|建立|开展|推进|实施|落实|整改|完善|建设|完成|提升|加强|改进|纳入)"
)
_RECOMMENDATION_SIGNAL_RE = re.compile(
    r"需要进一步|建议|应当|应予|仍需|亟待|有待|需进一步|"
    r"需要(?=[\u3400-\u9fff]{0,6}(?:完善|推进|落实|加强|改进|提升|建立|完成))|"
    r"应(?=[\u3400-\u9fff]{1,8}(?:完善|推进|落实|加强|改进|提升|建立|完成))"
)
_CONDITIONAL_SIGNAL_RE = re.compile(
    r"(?:^|[，；])(?:如|若|一旦|视|根据)[^，。；]{0,18}(?:则|时|后|情况)"
)
_GAP_SIGNAL_RE = re.compile(
    r"与[^，。；]{0,18}相比[^，。；]{0,8}(?:存在)?(?:明显)?差距|"
    r"存在明显差距|存在差距|明显差距|未达到(?:要求|目标|标准)?"
)
_NOT_IMPLEMENTED_SIGNAL_RE = re.compile(
    r"尚未完成|仍未完成|未能|尚未|仍未|未完成|未落实|"
    r"没有(?:完成|落实|实施|建立|形成|实现)"
)
_PROBLEM_SIGNAL_RE = re.compile(
    r"频次不够高|频次不高|频次不足|落实不到位|效果不平衡|"
    r"不够(?:完善|充分|健全|成熟|规范|深入|有力|高)|不充分|不到位|"
    r"不完善|不足|存在(?:突出|明显)?问题|短板|薄弱"
)
_PARTIAL_SIGNAL_RE = re.compile(
    r"部分(?:完成|落实|实施|建成|形成|实现|达到|环节)?|"
    r"[一二三四五六七八九十两\d]+项中完成[一二三四五六七八九十两\d]+项"
)
_ONGOING_SIGNAL_RE = re.compile(
    r"正在(?:推进|开展|实施|建设|落实|整改|完善|提升|改进)|"
    r"持续(?:推进|开展|实施|建设|落实|整改|完善|提升|改进)|"
    r"逐步(?:推进|开展|实施|建设|落实|完善|提升|改进)|"
    r"尚处于[^，。；]{0,10}(?:阶段|过程中)|推进中|建设中|实施中"
)
_RESULT_SIGNAL_RE = re.compile(
    r"(?:得到|获得)(?:明显|显著|有效|持续)?(?:提升|改善|增强|提高)|"
    r"取得(?:明显|显著|积极|阶段性|初步|良好)?(?:成效|成果|效果|进展)|"
    r"产生(?:明显|显著|积极|良好)?(?:成效|效果|结果)"
)
_NEGATIVE_RESULT_SIGNAL_RE = re.compile(
    r"(?:未|没有)(?:发生|出现)[^，。；]{0,8}(?:事故|案件|事件)"
)
_COMPLETED_SIGNAL_RE = re.compile(
    r"已经(?:组织|召开|完成|建成|建立|开展|推进|实施|形成|实现|达到|落实|整改|完善|建设|提升|取得)|"
    r"已(?:组织|召开|完成|建成|建立|开展|推进|实施|形成|实现|达到|落实|整改|完善|建设|提升|取得)|"
    r"完成(?:了)?|建成(?:了)?|形成(?:了)?|实现(?:了)?|达到(?:了)?"
)
_NEGATION_MARKER_RE = re.compile(
    r"频次不够高|频次不高|频次不足|落实不到位|存在明显差距|存在差距|"
    r"明显差距|尚未完成|仍未完成|未达到|未完成|未落实|未能|尚未|仍未|"
    r"没有|不够|不充分|不到位|不完善|不足"
)
_MODALITY_MARKER_RE = re.compile(
    r"下一步|今后|后续|未来|将要|将继续|将进一步|计划|拟|建议|应当|应予|"
    r"力争|争取|预计|预期|目标(?:是|为|到|在)|"
    r"(?:\d{4}年(?:底|末)?|年底|年末|本年度|未来\d+年)(?:之)?前|"
    r"需要进一步|仍需|亟待|有待|正在|持续|逐步|尚处于|部分"
)
_COMPLETION_MARKER_RE = re.compile(
    r"已经(?:组织|召开|完成|建成|建立|开展|推进|实施|形成|实现|达到|落实|整改|完善|建设|提升|取得)|"
    r"已(?:组织|召开|完成|建成|建立|开展|推进|实施|形成|实现|达到|落实|整改|完善|建设|提升|取得)|"
    r"完成|建成|形成|实现|达到|"
    r"取得|得到(?:明显|显著|有效)?(?:提升|改善|增强|提高)"
)
_DIRECTIONAL_COMPLETION_RE = re.compile(
    r"(?:已经|已)(?:全面|全部|圆满|顺利|有效|明显|显著)?"
    r"(?:组织|召开|完成|建成|建立|开展|推进|实施|形成|实现|达到|落实|整改|完善|建设|提升|取得)|"
    r"(?:全面|全部|圆满|顺利)(?:完成|建成|实现|达到|落实)|"
    r"取得(?:明显|显著|积极|阶段性|初步|良好)?(?:成效|成果|效果|进展)|"
    r"(?:得到|获得)(?:明显|显著|有效|持续)?(?:提升|改善|增强|提高)"
)
FORBIDDEN_MODEL_FIELDS = {
    "raw_text", "source_paragraph", "source_outline", "source_heading",
    "complete_chunk", "complete_document", "original_sentence",
}
WEB_TAILS = (
    "中华人民共和国公安部", "达州市达川区人民政府", "中国长安网", "常州长安网",
)
HEADING_RE = re.compile(r"^\s*(?:[一二三四五六七八九十]+、|第[一二三四五六七八九十]+[章节]|（[一二三四五六七八九十]+）)\s*")
ENUM_RE = re.compile(r"^\s*(?:[一二三四五六七八九十]+是|[一二三四五六七八九十]+要|首先|其次|再次|最后)\s*")
HAN_RE = re.compile(r"[\u3400-\u9fff]")
# Structured canonical fields may legitimately exceed 20 Han characters.
# The remaining guard is anomaly-only and does not alter normal business facts.
LONG_HAN_RE = re.compile(r"[\u3400-\u9fff]{97,}")
NUMBER_RE = re.compile(r"(?:\d+(?:\.\d+)?|[一二三四五六七八九十百千万亿两]+)(?:年|月|日|次|名|人|项|个|分钟|小时|天|场|套|台|条|起|件|%|％)?")

# Longest match wins; stable sorting preserves the rule declaration order for
# equal-length candidates. A set here loses that priority across hash seeds.
ACTION_VERBS = tuple(sorted((
    "专题研究",
    "牵头成立", "专题听取", "实地踏勘", "现场明确", "量身制定", "严格落实", "动态巡控",
    "协同处置", "会商研判", "监测预警", "正面引导", "延长运营", "加密班次", "开通专线",
    "整改销号", "排查整改", "复查验收", "建档立卡", "分级分类", "闭环管理",
    "优先引导", "重点照看", "组织", "召开", "审定", "成立", "统筹", "建立", "实行", "依托",
    "设置", "划定", "投入", "安排", "协调", "开展", "排查", "检测", "整治", "使用", "严查",
    "盯防", "巡查", "劝阻", "模拟", "检验", "完善", "补齐", "配齐", "处置", "上报", "介入",
    "化解", "发布", "引导", "回应", "实施", "投放", "保障", "提供", "维护", "分解", "明确",
    "形成", "构建", "推动", "实现", "完成", "发现", "防止", "提升", "确保", "纳入", "覆盖",
    "通过", "评审", "创建", "制定", "印发", "签订", "培训", "演练", "检查",
    "督促", "建档", "整改", "复核", "复查", "销号", "处罚", "立案", "约谈",
    "申报", "验收", "推广", "转化", "应用", "达到", "取得", "落实", "推进",
), key=len, reverse=True))
RISK_MARKERS = ("风险", "隐患", "问题", "拥堵", "踩踏", "事故", "冲突", "故障", "疾病", "舆情")
RESULT_MARKERS = (
    "实现", "完成", "发现", "处置", "未发生", "零事故", "见效",
    "达到", "形成", "通过", "取得",
)
MECHANISM_MARKERS = ("机制", "体系", "格局", "台账", "清单", "专班", "指挥部", "预案", "联席会议", "责任边界")
SCENE_MARKERS = ("现场", "入口", "出入口", "舞台", "观众区", "核心区", "通道", "道路", "活动区域")

DEPARTMENT_ALIAS_GROUPS = {
    "县委政法委": ("县委政法委", "政法部门"),
    "公安机关": ("公安机关", "公安", "公安部门", "公安局"),
    "文旅部门": ("文旅部门", "文化旅游部门", "文旅", "文化和旅游部门"),
    "应急管理部门": ("应急管理部门", "应急部门", "应急管理局", "应急"),
    "卫生健康部门": ("卫生健康部门", "卫健部门", "卫健", "卫健局"),
    "城市管理部门": ("城市管理部门", "城管部门", "城管", "城管局"),
    "交通运输部门": ("交通运输部门", "交通部门", "交通", "交通运输局"),
    "消防救援机构": ("消防救援机构", "消防部门", "消防", "消防救援队伍"),
}
ACTION_ALIAS_GROUPS = {
    "实地踏勘": ("实地踏勘", "现场踏勘"),
    "专题听取": ("专题听取", "专题听取汇报"),
    "牵头成立": ("牵头成立",),
    "统筹": ("统筹", "统筹协调"),
    "设置": ("设置",),
    "提供": ("提供",),
}
PRECISE_NUMBER_RE = re.compile(
    r"(?:\d+(?:\.\d+)?|[一二三四五六七八九十百千万亿两]+)"
    r"(?:年|个月|月|日|次|名|人|项|个|分钟|秒|小时|天|场|套|台|条|起|件|处|家|户|批|万元|元|%|％)"
)
FREQUENCY_RE = re.compile(
    r"(?:提前[^，。；]{0,8}|活动前[^，。；]{0,8}|每日|每天|每周|每月|每季度|每年|"
    r"多次|多轮(?:次)?|多套|多场|多个|三个圈层|未发生一起)"
)
ATOMIC_PLACEHOLDER_RE = re.compile(
    r"(?:[×Xx]{2,}|[*＊]{2,}|_{2,}|＿{2,}|待填|待补|待确定|某某)"
)
MAX_NORMAL_SUBJECT_CHARS = 96
MAX_NORMAL_OBJECT_CHARS = 160
MAX_ATOMS_PER_CHUNK = 64
PLACE_VALUE_RE = re.compile(
    r"[\u3400-\u9fff]{0,10}(?:活动场地|出入口|疏散通道|舞台区域|周边道路|观众聚集区|"
    r"舞台核心区|安检入口|活动区域|停车区域|服务点|救助点|备勤点|功能区)"
)
EQUIPMENT_VALUE_RE = re.compile(
    r"(?:X光机|金属探测器|无人机|视频监控|人流监测系统|无线对讲机|急救设备|"
    r"广播系统|闸机|警务终端|充电宝|饮水机|急救箱|雨具|消防车|救护车)"
)
MECHANISM_VALUE_RE = re.compile(
    r"[\u3400-\u9fff‘’“”\"A-Za-z0-9、]{0,24}(?:机制|制度|工作专班|指挥部|工作组|"
    r"风险台账|问题清单|整改台账|预案|一场一策|一档管理)"
)


class InvalidEvidencePackError(RuntimeError):
    def __init__(self, report: dict[str, Any]):
        super().__init__("flow3_v31_status=BLOCKED_INVALID_ATOMIC_EVIDENCE")
        self.report = report
        self.flow3_v3_status = "BLOCKED_INVALID_ATOMIC_EVIDENCE"


class InvalidContentPlanError(RuntimeError):
    def __init__(self, report: dict[str, Any]):
        super().__init__("flow3_v31_status=BLOCKED_INVALID_CONTENT_PLAN")
        self.report = report
        self.flow3_v3_status = "BLOCKED_INVALID_CONTENT_PLAN"


class InvalidEvidenceSelectionError(RuntimeError):
    def __init__(self, report: dict[str, Any]):
        super().__init__("flow3_v32_status=BLOCKED_INVALID_EVIDENCE_SELECTION")
        self.report = report
        self.flow3_v3_status = "BLOCKED_INVALID_EVIDENCE_SELECTION"


class InvalidFactAnchorsError(RuntimeError):
    def __init__(self, report: dict[str, Any]):
        super().__init__("flow3_v33_status=BLOCKED_INVALID_FACT_ANCHORS")
        self.report = report
        self.flow3_v3_status = "BLOCKED_INVALID_FACT_ANCHORS"


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip("，,。；;：:、-—")


def _signal_matches(pattern: re.Pattern[str], text: str, *, limit: int = 4) -> list[str]:
    """Return minimal source markers in source order, without retaining prose."""
    matches: list[str] = []
    for match in pattern.finditer(_compact(text)):
        marker = str(match.group(0) or "")[:18]
        if marker and marker not in matches:
            matches.append(marker)
        if len(matches) >= limit:
            break
    return matches


def normalize_semantic_state(value: Any) -> str:
    try:
        return SemanticState(str(value or "")).value
    except ValueError:
        return SemanticState.UNKNOWN.value


def classify_semantic_state(text: str, *, evidence_type: str = "") -> dict[str, Any]:
    """Classify source state conservatively using ordered local signals only."""
    compact = _compact(text)
    negation_markers = _signal_matches(_NEGATION_MARKER_RE, compact)
    modality_markers = _signal_matches(_MODALITY_MARKER_RE, compact)
    completion_markers = _signal_matches(_COMPLETION_MARKER_RE, compact)

    ordered_rules = (
        (SemanticState.PLAN, _PLAN_SIGNAL_RE),
        (SemanticState.RECOMMENDATION, _RECOMMENDATION_SIGNAL_RE),
        (SemanticState.CONDITIONAL, _CONDITIONAL_SIGNAL_RE),
        # "未发生事故" is an evidenced outcome, not an unimplemented action.
        (SemanticState.RESULT, _NEGATIVE_RESULT_SIGNAL_RE),
        (SemanticState.GAP, _GAP_SIGNAL_RE),
        (SemanticState.NOT_IMPLEMENTED, _NOT_IMPLEMENTED_SIGNAL_RE),
        (SemanticState.PROBLEM, _PROBLEM_SIGNAL_RE),
        (SemanticState.PARTIAL, _PARTIAL_SIGNAL_RE),
        (SemanticState.ONGOING, _ONGOING_SIGNAL_RE),
        (SemanticState.RESULT, _RESULT_SIGNAL_RE),
        (SemanticState.COMPLETED, _COMPLETED_SIGNAL_RE),
    )
    state = SemanticState.UNKNOWN
    state_evidence: list[str] = []
    for candidate, pattern in ordered_rules:
        matches = _signal_matches(pattern, compact)
        if matches:
            state = candidate
            state_evidence = matches
            break
    if state is SemanticState.UNKNOWN and evidence_type == "result":
        state = SemanticState.RESULT
    elif state is SemanticState.UNKNOWN and evidence_type == "context":
        state = SemanticState.CONTEXT

    return {
        "semantic_state_contract_version": SEMANTIC_STATE_CONTRACT_VERSION,
        "semantic_state": state.value,
        "state_evidence": state_evidence,
        "state_qualifier": state_evidence[0] if state_evidence else "",
        "negation_markers": negation_markers,
        "modality_markers": modality_markers,
        "completion_markers": completion_markers,
    }


def semantic_state_payload(item: dict[str, Any]) -> dict[str, Any]:
    """Copy state metadata without reclassifying; missing legacy state is UNKNOWN."""
    def marker_list(name: str) -> list[str]:
        values = item.get(name) or []
        if not isinstance(values, list):
            return []
        return list(dict.fromkeys(str(value)[:18] for value in values if str(value)))[:4]

    return {
        "semantic_state_contract_version": str(item.get("semantic_state_contract_version") or ""),
        "semantic_state": normalize_semantic_state(item.get("semantic_state")),
        "state_evidence": marker_list("state_evidence"),
        "state_qualifier": str(item.get("state_qualifier") or "")[:18],
        "negation_markers": marker_list("negation_markers"),
        "modality_markers": marker_list("modality_markers"),
        "completion_markers": marker_list("completion_markers"),
    }


def _semantic_state_fields_for_propagation(item: dict[str, Any]) -> dict[str, Any]:
    """Keep historical anchor/outbound shapes unchanged when state never existed."""
    if (
        "semantic_state" not in item
        and not item.get("semantic_state_contract_version")
    ):
        return {}
    return semantic_state_payload(item)


_SEMANTIC_STATE_COMPATIBILITY = {
    SemanticState.COMPLETED.value: {SemanticState.COMPLETED.value},
    SemanticState.ONGOING.value: {SemanticState.ONGOING.value},
    SemanticState.PARTIAL.value: {SemanticState.PARTIAL.value},
    SemanticState.PROBLEM.value: {SemanticState.PROBLEM.value, SemanticState.GAP.value},
    SemanticState.NOT_IMPLEMENTED.value: {SemanticState.NOT_IMPLEMENTED.value},
    SemanticState.GAP.value: {SemanticState.GAP.value, SemanticState.PROBLEM.value},
    SemanticState.RESULT.value: {SemanticState.RESULT.value},
    SemanticState.PLAN.value: {SemanticState.PLAN.value},
    SemanticState.RECOMMENDATION.value: {SemanticState.RECOMMENDATION.value},
    SemanticState.CONDITIONAL.value: {SemanticState.CONDITIONAL.value},
    SemanticState.CONTEXT.value: {SemanticState.CONTEXT.value, SemanticState.UNKNOWN.value},
    SemanticState.UNKNOWN.value: {SemanticState.UNKNOWN.value, SemanticState.CONTEXT.value},
}


def check_semantic_state_consistency(anchor: dict[str, Any], fact_text: str) -> dict[str, Any]:
    """Validate writer state against an explicit Atomic Evidence state contract."""
    source_state = normalize_semantic_state(anchor.get("semantic_state"))
    candidate = classify_semantic_state(fact_text)
    candidate_state = candidate["semantic_state"]
    contract_applies = (
        str(anchor.get("semantic_state_contract_version") or "")
        == SEMANTIC_STATE_CONTRACT_VERSION
    )
    passed = (
        not contract_applies
        or candidate_state in _SEMANTIC_STATE_COMPATIBILITY[source_state]
    )
    completion_upgrade = bool(_DIRECTIONAL_COMPLETION_RE.search(_compact(fact_text)))
    if contract_applies and source_state in {
        SemanticState.ONGOING.value,
        SemanticState.PARTIAL.value,
        SemanticState.PROBLEM.value,
        SemanticState.NOT_IMPLEMENTED.value,
        SemanticState.GAP.value,
        SemanticState.PLAN.value,
        SemanticState.RECOMMENDATION.value,
        SemanticState.CONTEXT.value,
        SemanticState.UNKNOWN.value,
    } and completion_upgrade:
        passed = False
    return {
        "passed": passed,
        "contract_applies": contract_applies,
        "source_state": source_state,
        "candidate_state": candidate_state,
        "completion_upgrade": completion_upgrade,
        "state_evidence": list(anchor.get("state_evidence") or []),
    }


def clean_source_residue(value: Any) -> str:
    text = str(value or "").replace("\u3000", " ")
    for tail in WEB_TAILS:
        text = text.replace(tail, "")
    text = re.sub(r"https?://\S+|www\.\S+", "", text, flags=re.I)
    return text.strip()


def _is_heading(value: str) -> bool:
    text = value.strip()
    if not text:
        return True
    if HEADING_RE.match(text) and not re.search(r"[。！？；]", text) and len(text) <= 80:
        return True
    return bool(re.match(r"^.{0,16}(?:工作)?(?:材料|报告|方案|要点|讲话|总结)$", text) and len(text) <= 55)


def _source_fragments(text: str, *, exact_source_spans: bool = False) -> Iterable[str]:
    cleaned = clean_source_residue(text)
    for sentence in re.split(r"[。！？；\n]+", cleaned):
        sentence = ENUM_RE.sub("", sentence.strip())
        if not sentence or _is_heading(sentence):
            continue
        carried_subject = ""
        clauses = re.split(r"[，,；;](?![^“”]*”)|(?<=\S)(?:同时|并且|此外|以及)(?=\S)", sentence)
        for clause in clauses:
            clause = ENUM_RE.sub("", clause.strip())
            positions = _verb_positions(clause)
            if positions:
                first_position = positions[0][0]
                explicit_subject = _compact(clause[:first_position])
                if explicit_subject and explicit_subject not in {"坚持", "通过", "根据", "结合", "针对", "依托", "围绕"}:
                    carried_subject = _short(
                        explicit_subject, MAX_NORMAL_SUBJECT_CHARS,
                    )
                elif first_position == 0 and carried_subject and not exact_source_spans:
                    # The full-article source view must remain a contiguous
                    # source quote. A guessed subject (possibly a noun phrase
                    # containing a verb) must never be spliced into a new fact.
                    clause = f"{carried_subject}{clause}"
            if len(_compact(clause)) >= 4 and not _is_heading(clause):
                yield clause


def _short(value: str, limit: int = MAX_NORMAL_OBJECT_CHARS) -> str:
    value = _compact(value)
    if len(value) <= limit:
        return value
    # Preserve a numeric expression when truncation would otherwise remove it.
    number = NUMBER_RE.search(value)
    if number and number.start() >= limit:
        head = value[: max(4, limit - len(number.group(0)) - 1)]
        return f"{head}·{number.group(0)}"
    return value[:limit]


def _verb_positions(clause: str) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    occupied: set[int] = set()
    for verb in ACTION_VERBS:
        for match in re.finditer(re.escape(verb), clause):
            cells = set(range(match.start(), match.end()))
            if occupied & cells:
                continue
            occupied.update(cells)
            found.append((match.start(), verb))
    ordered = sorted(found)
    ordered = [
        (start, verb)
        for index, (start, verb) in enumerate(ordered)
        if not (
            index + 1 < len(ordered)
            and re.fullmatch(
                r"(?:综合)?(?:协调)?(?:部门|单位|机构|专班|办公室|委员会|主体|人员)",
                clause[start + len(verb):ordered[index + 1][0]],
            )
        )
    ]
    if len(ordered) <= 1:
        return ordered
    propositions = [ordered[0]]
    previous_start, previous_verb = ordered[0]
    for start, verb in ordered[1:]:
        between = clause[previous_start + len(previous_verb):start]
        # A later verb starts a new proposition only when the source sentence
        # itself supplies a coordination/sequence boundary.  This prevents
        # nominal phrases such as “完成标准化创建” from being misread as two
        # predicates while retaining “完成……、通过……、建立……”.
        if re.search(
            r"(?:[、，,；;]|并且?|同时|随后|继而|之后|后|再|则)"
            r"(?:仍未|尚未|未能|没有|未|已经|已|正在|持续|将继续|将)?$",
            between,
        ):
            propositions.append((start, verb))
            previous_start, previous_verb = start, verb
    return propositions


def _evidence_type(clause: str, verb: str) -> str:
    if verb in RESULT_MARKERS:
        return "result"
    if any(marker in clause for marker in MECHANISM_MARKERS):
        return "mechanism"
    if any(marker in clause for marker in RISK_MARKERS) and not verb:
        return "risk"
    if verb:
        return "action"
    if any(marker in clause for marker in SCENE_MARKERS):
        return "scene"
    if any(marker in clause for marker in RISK_MARKERS):
        return "risk"
    return "context"


def _object_parts(value: str) -> list[str]:
    value = _compact(value)
    value = re.split(r"[。！？；;]", value, maxsplit=1)[0]
    parts = [part for part in re.split(r"、|及其|以及|与|和", value) if _compact(part)]
    return parts[:8] or [value]


OBJECT_SCOPE_PREFIX_RE = re.compile(
    r"^(?:"
    r"上半年|下半年|今年以来|年初以来|近期|目前|累计|先后|持续|常态化|"
    r"全县|全市|全区|全省|全州|全乡|全镇|全街道|县域|辖区|"
    r"县政府|县人民政府|县政府办公室|政府系统|全县政府系统|全市政府系统|全区政府系统|"
    r"党组会议|局党组会议|党委会议|班子会议|办公会议"
    r")+"
)
OBJECT_LEADING_ACTION_RE = re.compile(
    r"^(?:组织|开展|推进|召开|研究|专题研究|专题听取|部署|落实|督导|督查|检查|专项督查)+"
)
OBJECT_TRAILING_PLACEHOLDER_QUANTITY_RE = re.compile(
    r"(?:[×Xx*＊]+(?:次|项|个|家|户|人|名|场|套|台|条|起|件|处|批|元|万元|%|％))$"
)


def _canonical_object_candidate(value: str) -> str:
    compact = _compact(value)
    if not compact:
        return ""
    previous = ""
    while compact and compact != previous:
        previous = compact
        compact = OBJECT_SCOPE_PREFIX_RE.sub("", compact)
        compact = OBJECT_LEADING_ACTION_RE.sub("", compact)
    compact = OBJECT_TRAILING_PLACEHOLDER_QUANTITY_RE.sub("", compact)
    return compact or _compact(value)


def _predicate_start(clause: str, verb_start: int) -> tuple[int, str]:
    """Keep a local state marker attached to the predicate it modifies."""

    before = clause[:verb_start]
    for marker in (
        "仍未", "尚未", "未能", "没有", "未", "已经", "已",
        "正在", "持续", "将继续", "将",
    ):
        if before.endswith(marker):
            return verb_start - len(marker), marker
    return verb_start, ""


def _planner_block_from_chunk(
    chunk: dict[str, Any], atom: dict[str, Any],
) -> tuple[str, str]:
    metadata = chunk.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}
    coverage_role = str(chunk.get("coverage_role") or "").strip()
    content_role = str(
        chunk.get("content_role") or metadata.get("content_role") or coverage_role
    ).strip()
    state = normalize_semantic_state(atom.get("semantic_state"))
    if state in {
        SemanticState.PROBLEM.value,
        SemanticState.GAP.value,
        SemanticState.NOT_IMPLEMENTED.value,
    }:
        return "问题与不足", content_role or "problem"
    if state in {
        SemanticState.PLAN.value,
        SemanticState.RECOMMENDATION.value,
    } or coverage_role == "next_step":
        return "下一步安排", content_role or "next_step"
    heading = str(
        chunk.get("section_heading") or metadata.get("section_heading") or ""
    ).strip()
    heading = re.sub(
        r"^\s*(?:第[一二三四五六七八九十\d]+[章节部分]|"
        r"[一二三四五六七八九十\d]+[\u3001.]|"
        r"[（(][一二三四五六七八九十\d]+[）)])\s*",
        "",
        heading,
    ).strip(" ，,。；;：:—-")
    if heading:
        return heading[:32], content_role
    if coverage_role == "achievement_status":
        return "工作成效与进展", content_role
    obj = str(atom.get("canonical_object") or atom.get("object") or "").strip()
    return (obj[:32] if len(obj) >= 4 else "主要工作"), content_role


def _canonical_subject_candidate(value: str) -> str:
    compact = _compact(value)
    if not compact:
        return ""
    compact = re.sub(r"^(?:上半年|下半年|今年以来|年初以来|近期|目前|累计|先后)", "", compact)
    for marker in ("带头", "结合", "围绕", "聚焦", "依托", "通过", "按照", "根据"):
        position = compact.find(marker)
        if position >= 2:
            compact = compact[:position]
            break
    compact = compact.rstrip("均都并和及")
    return compact or _compact(value)


def _canonical_department(value: str) -> str:
    compact = _compact(value)
    for canonical, aliases in DEPARTMENT_ALIAS_GROUPS.items():
        if any(_compact(alias) == compact for alias in aliases):
            return canonical
    return compact


def _extract_departments(clause: str) -> list[str]:
    found: list[str] = []
    explicit_context = bool(re.search(r"(?:统筹|组织|联合|协调)[^。；]{0,80}[、，,]", clause))
    for canonical, aliases in DEPARTMENT_ALIAS_GROUPS.items():
        matched = False
        for alias in aliases:
            if alias not in clause:
                continue
            if alias in {"公安", "文旅", "应急", "卫健", "城管", "交通", "消防"}:
                direct = re.search(rf"{re.escape(alias)}(?:机关|部门|局|救援机构|救援队伍)", clause)
                enumerated = explicit_context and re.search(
                    rf"(?:^|[、，,]){re.escape(alias)}(?:[、，,等]|$)", clause,
                )
                if not (direct or enumerated):
                    continue
            matched = True
            break
        if matched and canonical not in found:
            found.append(canonical)
    return found


def _alias_values(canonical: str, groups: dict[str, tuple[str, ...]]) -> list[str]:
    for group_canonical, aliases in groups.items():
        if canonical == group_canonical or canonical in aliases:
            return list(dict.fromkeys((group_canonical, *aliases)))
    return [canonical] if canonical else []


def normalize_mechanism_value(value: str) -> str:
    compact = _compact(value).strip("‘’“”\"")
    for action in ACTION_VERBS:
        if action in compact:
            compact = compact.rsplit(action, 1)[-1]
            break
    return compact.strip("‘’“”\"")


def _structured_specifics(
    clause: str, *, subject: str, action: str, obj: str, result: str,
) -> dict[str, Any]:
    departments = _extract_departments(clause)
    canonical_subject = _canonical_department(subject)
    if canonical_subject in DEPARTMENT_ALIAS_GROUPS and canonical_subject not in departments:
        departments.insert(0, canonical_subject)
    quantities = list(dict.fromkeys(PRECISE_NUMBER_RE.findall(clause)))
    times = [value for value in quantities if re.search(r"年|月|日|分钟|秒|小时|天", value)]
    times.extend(value for value in FREQUENCY_RE.findall(clause) if value not in times)
    places = list(dict.fromkeys(_compact(value) for value in PLACE_VALUE_RE.findall(clause)))
    equipment = list(dict.fromkeys(EQUIPMENT_VALUE_RE.findall(clause)))
    mechanisms = [normalize_mechanism_value(value) for value in MECHANISM_VALUE_RE.findall(clause)]
    mechanisms = [value for value in mechanisms if value]
    result_values = [result] if result else []
    return {
        "canonical_subject": canonical_subject,
        "canonical_action": action,
        "canonical_object": obj or result,
        "quantities": quantities,
        "times": list(dict.fromkeys(times)),
        "departments": departments,
        "equipment": equipment,
        "places": places,
        "mechanisms": list(dict.fromkeys(mechanisms)),
        "results": result_values,
        "allowed_aliases": {
            "subject": _alias_values(canonical_subject, DEPARTMENT_ALIAS_GROUPS),
            "action": _alias_values(action, ACTION_ALIAS_GROUPS),
            "object": [obj or result] if (obj or result) else [],
        },
    }


def _atoms_from_clause(
    clause: str, *, document_id: str, chunk_id: str, document_role: str,
    source_order: dict[str, Any] | None = None,
    semantic_context: str | None = None,
    exact_source_spans: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    clause_sha256 = _sha(clause)
    if ATOMIC_PLACEHOLDER_RE.search(clause):
        return [], [{
            "document_id": document_id,
            "chunk_id": chunk_id,
            "source_span_sha256": clause_sha256,
            "reason": "unsafe_placeholder",
        }]
    positions = _verb_positions(clause)
    discarded: list[dict[str, str]] = []
    if not positions:
        compact = _compact(clause)
        evidence_type = _evidence_type(compact, "")
        if evidence_type == "context" and len(compact) > 28:
            discarded.append({
                "document_id": document_id, "chunk_id": chunk_id,
                "source_span_sha256": clause_sha256, "reason": "unreliable_unparsed_clause",
            })
            return [], discarded
        positions = [(0, "呈现")]

    atoms: list[dict[str, Any]] = []
    carried_subject = ""
    proposition_count = len(positions)
    clause_state_metadata = classify_semantic_state(
        semantic_context or clause,
        evidence_type=_evidence_type(clause, ""),
    )
    for index, (verb_start, verb) in enumerate(positions):
        predicate_start, state_prefix = _predicate_start(clause, verb_start)
        next_start = positions[index + 1][0] if index + 1 < proposition_count else len(clause)
        if index + 1 < proposition_count:
            next_start, _next_prefix = _predicate_start(clause, next_start)
        before = clause[:predicate_start] if index == 0 else ""
        after = clause[verb_start + (0 if verb == "呈现" else len(verb)):next_start]
        if exact_source_spans and index == 0 and not before and verb == "实现":
            # In "实现了X的Y", X is explicitly present in the result clause.
            # Recover X from that clause, never from a guessed previous subject.
            # This only affects extraction fields; the source quote/hash stays
            # byte-for-byte intact. Ambiguous/absent X keeps existing rejection.
            result_subject = re.fullmatch(r"了?([^，,；;的]{2,24})的(.+)", after)
            if result_subject:
                before, after = result_subject.groups()
        after = re.sub(r"^[、，,；;与和及]+", "", after).strip()
        action = f"{state_prefix}{verb}" if verb != "呈现" else "反映"
        proposition_text = "".join(
            part for part in (before, action, after) if part
        )
        evidence_type = _evidence_type(proposition_text, "" if verb == "呈现" else verb)
        state_metadata = classify_semantic_state(
            proposition_text,
            evidence_type=evidence_type,
        )
        if (
            state_metadata["semantic_state"] == SemanticState.UNKNOWN.value
            and clause_state_metadata["semantic_state"] == SemanticState.CONDITIONAL.value
        ):
            state_metadata = clause_state_metadata
        if (
            state_metadata["semantic_state"] == SemanticState.UNKNOWN.value
            and semantic_context
            and semantic_context != clause
        ):
            contextual_state = classify_semantic_state(
                semantic_context,
                evidence_type=evidence_type,
            )
            if contextual_state["semantic_state"] != SemanticState.UNKNOWN.value:
                state_metadata = contextual_state
        state_only_predicate = (
            state_metadata["semantic_state"] in {
                SemanticState.PROBLEM.value,
                SemanticState.GAP.value,
            }
            and not _compact(after)
            and bool(state_metadata["state_qualifier"])
        )
        if state_only_predicate:
            before = re.sub(r"(?:不够|不足|不充分|尚未|仍未|未|不)$", "", before)
        if index == 0:
            subject_source = re.split(r"[，,；;]", before)[-1]
            subject_source = re.split(r"、|以及|与|和", subject_source, maxsplit=1)[0]
            carried_subject = _short(
                _canonical_subject_candidate(subject_source),
                MAX_NORMAL_SUBJECT_CHARS,
            )
        subject = carried_subject or "相关责任主体"
        if subject in {"坚持", "通过", "根据", "结合", "针对", "依托", "围绕"} or subject.startswith(("将", "把", "对")):
            subject = "相关责任主体"
        parts = (
            [state_metadata["state_qualifier"]]
            if state_only_predicate
            else _object_parts(after or clause)
        )
        for atom_index, part in enumerate(parts):
            obj = _short(
                _canonical_object_candidate(part),
                MAX_NORMAL_OBJECT_CHARS,
            )
            if len(obj) < 2:
                continue
            atom_action = "存在" if state_only_predicate else action
            atom_source = "".join(
                part_value for part_value in (subject, atom_action, obj) if part_value
            )
            payload = {
                "document_id": document_id,
                "chunk_id": chunk_id,
                "document_role": document_role,
                "evidence_type": evidence_type,
                "subject": subject,
                "action": atom_action,
                "object": obj if evidence_type != "result" else "",
                "condition": "",
                "qualifier": state_metadata["state_qualifier"],
                "result": obj if evidence_type == "result" else "",
                "fact_status": "explicit",
                "source_span_sha256": clause_sha256,
                "source_clause_sha256": clause_sha256,
            }
            payload.update(state_metadata)
            payload.update(_structured_specifics(
                proposition_text,
                subject=subject,
                action=atom_action,
                obj=payload["object"],
                result=payload["result"],
            ))
            joined = "".join(str(payload.get(name) or "") for name in ("subject", "action", "object", "result"))
            stable = json.dumps(payload, ensure_ascii=False, sort_keys=True)
            payload["evidence_id"] = f"EV-{_sha(stable)[:12].upper()}"
            if source_order and source_order.get("available"):
                payload["source_order"] = {
                    **source_order,
                    "atom_index": index * 100 + atom_index,
                }
            atoms.append(payload)
    if not atoms:
        discarded.append({
            "document_id": document_id, "chunk_id": chunk_id,
            "source_span_sha256": clause_sha256, "reason": "empty_after_atomic_split",
        })
    return atoms, discarded


def _task_tokens(task: Any) -> set[str]:
    text = "".join(str(getattr(task, name, "") or "") for name in (
        "title", "document_type", "writing_purpose", "business_domain", "level", "org_type",
    ))
    han = "".join(re.findall(r"[\u3400-\u9fff]", text))
    return {
        han[index:index + size]
        for size in (2, 3, 4)
        for index in range(max(0, len(han) - size + 1))
    }


def _select_core_evidence(task: Any, evidence: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    tokens = _task_tokens(task)
    preferred_types = {"action": 5, "mechanism": 4, "risk": 3, "scene": 2, "result": 1, "context": 0}
    scored: list[tuple[int, str, dict[str, Any]]] = []
    for item in evidence:
        visible = "".join(str(item.get(key) or "") for key in (
            "canonical_subject", "canonical_action", "canonical_object", "result",
        ))
        relevance = sum(1 for token in tokens if token in visible)
        primary = 2 if item["document_role"] == "primary" else 0
        quality = 0
        subject = str(item.get("canonical_subject") or "")
        obj = str(item.get("canonical_object") or "")
        action = str(item.get("canonical_action") or "")
        if subject and subject not in {"相关责任主体", "全面", "高位", "牵头", "持续", "不断"}:
            quality += 4
        if action and action != "反映":
            quality += 2
        if action in {"专题听取", "实地踏勘", "排查", "设置", "提供", "统筹"}:
            quality += 4
        if item.get("departments") or item.get("mechanisms") or item.get("places"):
            quality += 3
        if 3 <= len(_compact(obj)) <= 18:
            quality += 2
        if any(marker in visible for marker in ("聚力", "累计", "下一步", "将认真总结", "不断提升")):
            quality -= 12
        if subject in {"持续", "不断"}:
            quality -= 10
        if item.get("quantities") or item.get("times"):
            quality += 5
        scored.append((
            relevance * 10 + primary + preferred_types.get(item["evidence_type"], 0) + quality,
            str(item["evidence_id"]), item,
        ))
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    # Atomic Evidence is the legal fact inventory, not a Top-K synopsis.  Keep
    # every accepted proposition available to Planning; ranking remains useful
    # only for deterministic ordering and never becomes an information gate.
    required = [entry[1] for entry in scored]
    optional: list[str] = []
    return required, optional


def build_atomic_evidence_pack(task: Any, retrieval_payload: dict[str, Any], *,
                               exact_source_spans: bool = False) -> dict[str, Any]:
    primary_id = str(retrieval_payload.get("primary_document_id") or "")
    auxiliary_ids = [str(value) for value in (retrieval_payload.get("auxiliary_document_ids") or [])][:2]
    selected_ids = {primary_id, *auxiliary_ids}
    evidence: list[dict[str, Any]] = []
    discarded: list[dict[str, str]] = []
    seen: set[tuple[str, ...]] = set()

    chunks = [item for item in (retrieval_payload.get("selected_chunks") or []) if str(item.get("document_id") or item.get("doc_id") or "") in selected_ids]
    primary_chunks = [item for item in chunks if str(item.get("document_id") or item.get("doc_id") or "") == primary_id]
    auxiliary_chunks = [item for item in chunks if item not in primary_chunks]
    primary_types: set[str] = set()

    for chunk in primary_chunks + auxiliary_chunks:
        document_id = str(chunk.get("document_id") or chunk.get("doc_id") or "")
        metadata = chunk.get("metadata") or {}
        chunk_id = str(chunk.get("chunk_id") or metadata.get("chunk_id") or "")
        role = "primary" if document_id == primary_id else "auxiliary"
        text = str(chunk.get("text") or chunk.get("chunk_text") or "")
        if not document_id or not chunk_id or not text:
            discarded.append({
                "document_id": document_id, "chunk_id": chunk_id,
                "source_span_sha256": _sha(text), "reason": "missing_source_identity_or_text",
            })
            continue
        accepted_from_chunk = 0
        has_chunk_index = "chunk_index" in chunk or "chunk_index" in metadata
        has_section_index = "section_index" in chunk or "section_index" in metadata
        source_order_base = (
            {
                "available": True,
                "section_index": int(chunk.get("section_index", metadata.get("section_index", 0)) or 0),
                "chunk_index": int(chunk.get("chunk_index", metadata.get("chunk_index", 0)) or 0),
            }
            if has_chunk_index and has_section_index
            else None
        )
        fragments = list(_source_fragments(text, exact_source_spans=exact_source_spans))
        for clause_index, clause in enumerate(fragments):
            source_titles = {
                _compact(value)
                for value in (
                    chunk.get("source_title"),
                    chunk.get("document_title"),
                    metadata.get("title"),
                    metadata.get("document_title"),
                )
                if _compact(value)
            }
            if any(title in _compact(clause) for title in source_titles):
                discarded.append({
                    "document_id": document_id,
                    "chunk_id": chunk_id,
                    "source_span_sha256": _sha(clause),
                    "reason": "source_title_residue",
                })
                continue
            semantic_context = clause
            if clause.endswith(("方面", "过程中", "工作中")) and clause_index + 1 < len(fragments):
                following = fragments[clause_index + 1]
                following_state = classify_semantic_state(following)["semantic_state"]
                if following_state in {
                    SemanticState.GAP.value,
                    SemanticState.PROBLEM.value,
                    SemanticState.NOT_IMPLEMENTED.value,
                    SemanticState.PARTIAL.value,
                }:
                    semantic_context = f"{clause}，{following}"
            atoms, rejects = _atoms_from_clause(
                clause,
                document_id=document_id,
                chunk_id=chunk_id,
                document_role=role,
                source_order=(
                    {**source_order_base, "clause_index": clause_index}
                    if source_order_base else None
                ),
                semantic_context=semantic_context,
                exact_source_spans=exact_source_spans,
            )
            discarded.extend(rejects)
            for atom in atoms:
                coverage_role = str(chunk.get("coverage_role") or "").strip()
                if (
                    normalize_semantic_state(atom.get("semantic_state"))
                    == SemanticState.UNKNOWN.value
                    and coverage_role in {"next_step", "problem"}
                ):
                    section_state = (
                        SemanticState.PLAN
                        if coverage_role == "next_step"
                        else SemanticState.PROBLEM
                    )
                    section_marker = "下一步章节" if coverage_role == "next_step" else "问题章节"
                    atom.update({
                        "semantic_state_contract_version": SEMANTIC_STATE_CONTRACT_VERSION,
                        "semantic_state": section_state.value,
                        "state_evidence": [section_marker],
                        "state_qualifier": section_marker,
                        "modality_markers": [section_marker] if section_state is SemanticState.PLAN else [],
                    })
                planner_block, source_content_role = _planner_block_from_chunk(
                    chunk, atom,
                )
                atom["planner_business_block"] = planner_block
                atom["source_content_role"] = source_content_role
                if role == "auxiliary" and atom["evidence_type"] in primary_types:
                    discarded.append({
                        "document_id": document_id, "chunk_id": chunk_id,
                        "source_span_sha256": atom["source_span_sha256"],
                        "reason": "auxiliary_does_not_fill_primary_type_gap",
                    })
                    continue
                key = tuple(str(atom.get(name) or "") for name in (
                    "document_id", "evidence_type", "subject", "action", "object", "result",
                    "semantic_state", "state_qualifier",
                ))
                if key in seen:
                    discarded.append({
                        "document_id": document_id,
                        "chunk_id": chunk_id,
                        "evidence_id": str(atom.get("evidence_id") or ""),
                        "source_span_sha256": str(
                            atom.get("source_span_sha256") or ""
                        ),
                        "reason": "duplicate_proposition",
                    })
                    continue
                seen.add(key)
                evidence.append(atom)
                accepted_from_chunk += 1
                if role == "primary":
                    primary_types.add(atom["evidence_type"])
                if accepted_from_chunk >= MAX_ATOMS_PER_CHUNK:
                    discarded.append({
                        "document_id": document_id,
                        "chunk_id": chunk_id,
                        "source_span_sha256": _sha(text),
                        "reason": "chunk_atom_safety_limit",
                    })
                    break
            if accepted_from_chunk >= MAX_ATOMS_PER_CHUNK:
                break

    if exact_source_spans:
        # A split can classify a nominal completion word differently from its
        # sibling (e.g. a deadline noun under a next-step source). Preserve both
        # original states, IDs and evidence; do not silently choose a winner.
        by_source: dict[tuple, list[dict]] = {}
        for atom in evidence:
            key = tuple(atom[k] for k in ("document_id", "chunk_id", "source_span_sha256"))
            by_source.setdefault(key, []).append(atom)
        for siblings in by_source.values():
            states = {a["semantic_state"] for a in siblings}
            if len(states) > 1:
                conflict = {"kind": "SOURCE_STATE_CONFLICT", "requires_review": True,
                            "evidence_states": {a["evidence_id"]: a["semantic_state"] for a in siblings},
                            "instruction": "同一来源片段存在状态差异；保留各状态，未经核实不得合成确定完成结论。"}
                for atom in siblings:
                    atom["limitation"] = copy.deepcopy(conflict)
    evidence.sort(key=lambda item: (
        0 if item.get("document_role") == "primary" else 1,
        int((item.get("source_order") or {}).get("section_index", 0)),
        int((item.get("source_order") or {}).get("chunk_index", 0)),
        int((item.get("source_order") or {}).get("clause_index", 0)),
        int((item.get("source_order") or {}).get("atom_index", 0)),
        str(item.get("evidence_id") or ""),
    ))
    required, optional = _select_core_evidence(task, evidence)
    return {
        "schema_version": "flow3-v3.1-atomic-evidence-2",
        "primary_document_id": primary_id,
        "auxiliary_document_ids": auxiliary_ids,
        "evidence": evidence,
        "required_evidence": required,
        "optional_evidence": optional,
        "prohibited_inference": [
            "证据未提供的数量、日期、次数、人员规模、设备、部门、地点和时限",
            "证据未提供的工作结果、事故、案件及绝对化结论",
            "把一般性建议表述为已经发生的本地事实",
        ],
        "discarded_evidence": discarded,
    }


def model_visible_evidence(pack: dict[str, Any]) -> list[dict[str, Any]]:
    allowed = (
        "evidence_id", "document_id", "chunk_id", "document_role", "evidence_type",
        "subject", "action", "object", "condition", "qualifier", "result", "fact_status",
        "semantic_state_contract_version", "semantic_state", "state_evidence",
        "state_qualifier", "negation_markers", "modality_markers", "completion_markers",
        "canonical_subject", "canonical_action", "canonical_object", "quantities", "times",
        "departments", "equipment", "places", "mechanisms", "results", "allowed_aliases",
    )
    return [{key: item.get(key, "") for key in allowed} for item in pack.get("evidence") or []]


def _source_texts(retrieval_payload: dict[str, Any]) -> list[str]:
    return [clean_source_residue(item.get("text") or item.get("chunk_text") or "") for item in retrieval_payload.get("selected_chunks") or []]


def _copied_han_issue(item: dict[str, Any], sources: list[str]) -> dict[str, Any] | None:
    source_compacts = [re.sub(r"\s+", "", source) for source in sources]
    visible_fields = ("subject", "action", "object", "condition", "qualifier", "result")
    for key in visible_fields:
        value = str(item.get(key) or "")
        for run in LONG_HAN_RE.findall(value):
            if any(run in source for source in source_compacts):
                return {
                    "rule": "oversized_source_copy",
                    "evidence_id": item.get("evidence_id"),
                    "field": key,
                    "length": len(run),
                }
    return None


def _copied_han_join_warning(item: dict[str, Any], sources: list[str]) -> dict[str, Any] | None:
    joined = "".join(str(item.get(key) or "") for key in ("subject", "action", "object", "condition", "qualifier", "result"))
    source_compacts = [re.sub(r"\s+", "", source) for source in sources]
    for run in LONG_HAN_RE.findall(joined):
        if any(run in source for source in source_compacts):
            return {
                "rule": "oversized_structured_field_join",
                "evidence_id": item.get("evidence_id"),
                "length": len(run),
            }
    return None


def _validate_atomic_evidence_pack_once(
    pack: dict[str, Any], retrieval_payload: dict[str, Any],
) -> dict[str, Any]:
    hard_issues: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    evidence = list(pack.get("evidence") or [])
    visible = model_visible_evidence(pack)
    sources = _source_texts(retrieval_payload)
    source_titles = [str(item.get("source_title") or "") for item in retrieval_payload.get("ranked_documents") or []]

    if not evidence:
        hard_issues.append({"rule": "nonempty", "detail": "no usable atomic evidence"})
    for item in evidence:
        forbidden = sorted(FORBIDDEN_MODEL_FIELDS & set(item))
        if forbidden:
            hard_issues.append({"rule": "forbidden_fields", "evidence_id": item.get("evidence_id"), "detail": forbidden})
        if item.get("evidence_type") not in EVIDENCE_TYPES or item.get("fact_status") not in FACT_STATUSES:
            hard_issues.append({"rule": "enum", "evidence_id": item.get("evidence_id")})
        if item.get("semantic_state_contract_version"):
            if item.get("semantic_state_contract_version") != SEMANTIC_STATE_CONTRACT_VERSION:
                hard_issues.append({"rule": "semantic_state_contract_version", "evidence_id": item.get("evidence_id")})
            if item.get("semantic_state") not in SEMANTIC_STATES:
                hard_issues.append({"rule": "semantic_state_enum", "evidence_id": item.get("evidence_id")})
            if any(not isinstance(item.get(name), list) for name in (
                "state_evidence", "negation_markers", "modality_markers", "completion_markers",
            )):
                hard_issues.append({"rule": "semantic_state_marker_shape", "evidence_id": item.get("evidence_id")})
        if item.get("fact_status") != "style_only" and (not item.get("document_id") or not item.get("chunk_id")):
            hard_issues.append({"rule": "source_identity", "evidence_id": item.get("evidence_id")})
        joined = "".join(str(item.get(key) or "") for key in ("subject", "action", "object", "condition", "qualifier", "result"))
        if any(tail in joined for tail in WEB_TAILS):
            hard_issues.append({"rule": "web_tail", "evidence_id": item.get("evidence_id")})
        if any(re.search(r"[、，,；;]", str(item.get(key) or "")) for key in ("subject", "object", "result")):
            hard_issues.append({"rule": "multiple_facts", "evidence_id": item.get("evidence_id")})
        if item.get("evidence_type") == "risk" and item.get("action") in ACTION_VERBS and not any(marker in joined for marker in RISK_MARKERS):
            hard_issues.append({"rule": "type_conflict", "evidence_id": item.get("evidence_id")})
        copied_issue = _copied_han_issue(item, sources)
        if copied_issue:
            hard_issues.append(copied_issue)
        else:
            copied_warning = _copied_han_join_warning(item, sources)
            if copied_warning:
                warnings.append(copied_warning)
    serialized_visible = json.dumps(visible, ensure_ascii=False)
    for title in source_titles:
        normalized = _compact(title)
        if normalized and normalized in _compact(serialized_visible):
            hard_issues.append({"rule": "source_title", "detail": _sha(title)})
    if any(len(source) >= 40 and _compact(source) in _compact(serialized_visible) for source in sources):
        hard_issues.append({"rule": "complete_chunk"})
    roles_by_type: dict[str, set[str]] = {}
    for item in evidence:
        roles_by_type.setdefault(item["evidence_type"], set()).add(item["document_role"])
    for evidence_type, roles in roles_by_type.items():
        if roles == {"primary", "auxiliary"}:
            hard_issues.append({"rule": "auxiliary_overlap", "detail": evidence_type})
    type_distribution = dict(sorted(Counter(item.get("evidence_type") for item in evidence).items()))
    report = {
        "flow3_v3_status": "PASS" if not hard_issues else "BLOCKED_INVALID_ATOMIC_EVIDENCE",
        "passed": not hard_issues,
        "hard_issue_count": len(hard_issues),
        "warning_count": len(warnings),
        "hard_issues": hard_issues,
        "warnings": warnings,
        "evidence_count": len(evidence),
        "evidence_type_distribution": type_distribution,
        "discarded_evidence_count": len(pack.get("discarded_evidence") or []),
        "contains_complete_document": False,
        "contains_complete_chunk": any(issue["rule"] == "complete_chunk" for issue in hard_issues),
        "contains_source_title_or_heading": any(issue["rule"] == "source_title" for issue in hard_issues),
        "contains_copied_20_han": False,
        "contains_oversized_source_copy": any(
            issue["rule"] == "oversized_source_copy" for issue in hard_issues
        ),
        "web_tail_clean": not any(issue["rule"] == "web_tail" for issue in hard_issues),
    }
    return report


def validate_atomic_evidence_pack(
    pack: dict[str, Any], retrieval_payload: dict[str, Any],
) -> dict[str, Any]:
    """Validate once, isolate one local purity defect, then validate again.

    ``multiple_facts`` remains an Atomic Purity signal.  A single affected atom
    is removed from the usable pack under the existing local-isolation limit;
    every other hard issue found during the initial validation is preserved.
    This keeps source identity, full-source-copy, and other systemic failures
    blocking even when they occur on the same atom.
    """
    initial_report = _validate_atomic_evidence_pack_once(pack, retrieval_payload)
    multiple_fact_issues = [
        issue
        for issue in initial_report.get("hard_issues") or []
        if issue.get("rule") == "multiple_facts"
    ]
    candidate_ids = list(dict.fromkeys(
        str(issue.get("evidence_id") or "") for issue in multiple_fact_issues
    ))
    base_diagnostics = {
        "isolated_count": 0,
        "isolated_evidence_ids": [],
        "isolation_reason": "multiple_facts" if multiple_fact_issues else "",
        "remaining_atom_count": len(pack.get("evidence") or []),
        "local_isolation_limit": MAX_LOCAL_ISOLATION_COUNT,
    }
    if not multiple_fact_issues:
        initial_report.update(base_diagnostics)
        return initial_report

    evidence = list(pack.get("evidence") or [])
    evidence_by_id = {
        str(item.get("evidence_id") or ""): item for item in evidence
    }
    identity_is_valid = (
        all(candidate_ids)
        and len(candidate_ids) == len(multiple_fact_issues)
        and all(evidence_id in evidence_by_id for evidence_id in candidate_ids)
    )
    if (
        not identity_is_valid
        or len(candidate_ids) > MAX_LOCAL_ISOLATION_COUNT
    ):
        initial_report.update({
            **base_diagnostics,
            "local_isolation_status": (
                "REJECTED_INVALID_EVIDENCE_IDENTITY"
                if not identity_is_valid
                else "REJECTED_LIMIT_EXCEEDED"
            ),
            "local_isolation_candidate_count": len(candidate_ids),
        })
        return initial_report

    isolated_ids = set(candidate_ids)
    isolated_items = [
        item for item in evidence
        if str(item.get("evidence_id") or "") in isolated_ids
    ]
    pack["evidence"] = [
        item for item in evidence
        if str(item.get("evidence_id") or "") not in isolated_ids
    ]
    for field in ("required_evidence", "optional_evidence"):
        pack[field] = [
            evidence_id for evidence_id in pack.get(field) or []
            if str(evidence_id) not in isolated_ids
        ]
    discarded = pack.setdefault("discarded_evidence", [])
    for item in isolated_items:
        discarded.append({
            "document_id": str(item.get("document_id") or ""),
            "chunk_id": str(item.get("chunk_id") or ""),
            "evidence_id": str(item.get("evidence_id") or ""),
            "source_span_sha256": str(item.get("source_span_sha256") or ""),
            "reason": "multiple_facts",
        })

    revalidated_report = _validate_atomic_evidence_pack_once(pack, retrieval_payload)
    preserved_hard_issues = [
        issue
        for issue in initial_report.get("hard_issues") or []
        if issue.get("rule") != "multiple_facts"
    ]
    combined_hard_issues = list(revalidated_report.get("hard_issues") or [])
    known = {json.dumps(issue, ensure_ascii=False, sort_keys=True) for issue in combined_hard_issues}
    for issue in preserved_hard_issues:
        key = json.dumps(issue, ensure_ascii=False, sort_keys=True)
        if key not in known:
            combined_hard_issues.append(issue)
            known.add(key)
    revalidated_report.update({
        "flow3_v3_status": (
            "PASS" if not combined_hard_issues else "BLOCKED_INVALID_ATOMIC_EVIDENCE"
        ),
        "passed": not combined_hard_issues,
        "hard_issue_count": len(combined_hard_issues),
        "hard_issues": combined_hard_issues,
        "contains_complete_chunk": any(
            issue.get("rule") == "complete_chunk" for issue in combined_hard_issues
        ),
        "contains_source_title_or_heading": any(
            issue.get("rule") == "source_title" for issue in combined_hard_issues
        ),
        "contains_oversized_source_copy": any(
            issue.get("rule") == "oversized_source_copy"
            for issue in combined_hard_issues
        ),
        "web_tail_clean": not any(
            issue.get("rule") == "web_tail" for issue in combined_hard_issues
        ),
        "isolated_count": len(candidate_ids),
        "isolated_evidence_ids": candidate_ids,
        "isolation_reason": "multiple_facts",
        "remaining_atom_count": len(pack.get("evidence") or []),
        "local_isolation_limit": MAX_LOCAL_ISOLATION_COUNT,
        "local_isolation_status": "ISOLATED_AND_REVALIDATED",
        "local_isolation_candidate_count": len(candidate_ids),
    })
    return revalidated_report


_PLAN_GOALS = {
    "context": "交代任务背景与需要解决的核心问题",
    "risk": "分析风险约束并提炼前置防范思路",
    "mechanism": "说明组织协同和运行机制",
    "action": "展开可由证据支撑的主要实践",
    "scene": "说明重点场景中的工作落点",
    "result": "归纳有证据支撑的成效与启示",
    "expression": "提炼符合文种边界的表达重点",
}
_PLAN_TYPE_ORDER = {name: index for index, name in enumerate(
    ("context", "risk", "mechanism", "action", "scene", "result", "expression")
)}

EVIDENCE_POLICY_FACT_REQUIRED = "fact_required"
EVIDENCE_POLICY_ANALYSIS_ONLY = "analysis_only"


def _minimum_fact_anchors(section: dict[str, Any]) -> int:
    if str(section.get("evidence_policy") or EVIDENCE_POLICY_FACT_REQUIRED) != EVIDENCE_POLICY_FACT_REQUIRED:
        return 0
    return max(1, int(section.get("minimum_fact_anchors") or 2))
_SECTION_REPLAN_TOPIC_FAMILIES = (
    ("系统", "平台", "数字化", "信息化", "数据"),
    ("项目", "工程", "建设", "施工"),
    ("服务", "保障", "办理", "供给"),
    ("风险", "隐患", "排查", "整改", "治理"),
)
_ANALYSIS_ONLY_SECTION_ROLES = {
    "background_introduction",
    "problem_analysis",
    "next_step_arrangement",
}
_ROLE_SECTION_PLANS = {
    "background_introduction": {
        "section_goal": "交代任务背景和行文依据，不新增现实事实",
        "writing_focus": ["说明任务背景、总体要求和篇章承接关系"],
        "expansion_path": ["概括写作背景", "说明一般工作要求", "衔接主体内容", "自然引出后文"],
    },
    "problem_analysis": {
        "section_goal": "分析一般性问题和薄弱环节，不编造具体事件、数字或现实结果",
        "writing_focus": ["围绕一般性短板、风险和改进空间开展克制分析"],
        "expansion_path": ["概括问题表现", "分析一般原因", "说明潜在影响", "承接改进方向"],
    },
    "next_step_arrangement": {
        "section_goal": "使用未来态和部署态提出下一步工作安排",
        "writing_focus": ["围绕责任落实、重点任务和常态长效提出后续安排"],
        "expansion_path": ["明确下一步总体方向", "部署重点任务", "提出推进要求", "作未来态总结收束"],
    },
}
_SECTION_ROLE_WORD_WEIGHTS = {
    "work_progress": 1.5,
    "problem_analysis": 1.0,
    "next_step_arrangement": 1.0,
}


def derive_target_length_range(target_words: int) -> dict[str, int]:
    """Derive the inclusive 90%-110% range using deterministic integer rules."""
    target = max(1, int(target_words))
    return {
        "min_words": (target * 90 + 99) // 100,
        "max_words": (target * 110) // 100,
    }


def _allocate_words_by_weights(
    target_total: int,
    weights: list[float],
) -> list[int]:
    if not weights:
        return []
    weights = [max(0.0, float(weight)) for weight in weights]
    if not any(weights):
        weights = [1.0] * len(weights)
    raw = [target_total * weight / sum(weights) for weight in weights]
    allocations = [max(1, int(value)) for value in raw]
    residue = target_total - sum(allocations)
    if residue > 0:
        order = sorted(
            range(len(raw)),
            key=lambda index: (raw[index] - int(raw[index]), -index),
            reverse=True,
        )
        for index in order[:residue]:
            allocations[index] += 1
    elif residue < 0:
        order = sorted(
            range(len(raw)),
            key=lambda index: (raw[index] - int(raw[index]), index),
        )
        for index in order:
            if residue == 0:
                break
            removable = min(allocations[index] - 1, -residue)
            if removable <= 0:
                continue
            allocations[index] -= removable
            residue += removable
    return allocations


def _allocate_words_by_role(
    target_total: int,
    section_roles: list[str],
    *,
    default_weight: float = 1.0,
) -> list[int]:
    return _allocate_words_by_weights(
        target_total,
        [
            max(0.0, float(_SECTION_ROLE_WORD_WEIGHTS.get(role, default_weight)))
            for role in section_roles
        ],
    )


def _evidence_policy_for_role(section_role: str) -> str:
    return (
        EVIDENCE_POLICY_ANALYSIS_ONLY
        if section_role in _ANALYSIS_ONLY_SECTION_ROLES
        else EVIDENCE_POLICY_FACT_REQUIRED
    )


def _analysis_only_plan_fields(section_role: str) -> dict[str, Any]:
    plan = _ROLE_SECTION_PLANS[section_role]
    return {
        "section_goal": plan["section_goal"],
        "section_focus": list(plan["writing_focus"]),
        "writing_focus": list(plan["writing_focus"]),
        "expansion_path": list(plan["expansion_path"]),
        "evidence_usage_plan": {
            "core_evidence_ids": [],
            "supporting_evidence_ids": [],
            "usage_steps": [
                "本部分不要求FACT，不得创建或借用evidence_id",
                "只写一般性分析、未来部署和安全的公文衔接",
            ],
            "evidence_roles": [],
        },
    }

_PLAN_EXPANSION_TEMPLATES = {
    "context": ["交代与本部分证据相关的任务背景", "围绕已绑定事实说明工作承接关系", "提炼问题意识和行文落点", "以一般性总结承接后续内容"],
    "risk": ["说明风险识别的写作背景", "围绕已绑定事实展开风险约束", "提炼前置防范或研判逻辑", "以一般性要求收束本部分"],
    "mechanism": ["说明组织协同的必要背景", "写清已绑定事实中的主体和动作", "分析协同运行的过程逻辑", "归纳机制性写作重点"],
    "action": ["交代工作任务或事项背景", "围绕已绑定事实写清主要动作", "展开过程说明和组织逻辑", "作方法意义层面的总结提升"],
    "scene": ["说明重点场景的工作背景", "围绕场景事实写清工作落点", "展开场景中的过程关系", "以一般性工作取向收束"],
    "result": ["交代成效归纳的证据边界", "只围绕已绑定结果事实展开", "说明成效与工作过程的对应关系", "作不越界的启示总结"],
    "expression": ["说明本部分表达任务", "围绕已绑定事实组织文种语言", "展开表达层次和衔接逻辑", "以符合文种边界的总结收束"],
}


def _plan_evidence_key(item: dict[str, Any]) -> tuple[int, str, str, str]:
    """Semantic ordering deliberately excludes source chunk/order metadata."""
    return (
        _PLAN_TYPE_ORDER.get(str(item.get("evidence_type") or ""), 99),
        str(item.get("canonical_action") or item.get("action") or ""),
        str(item.get("canonical_object") or item.get("object") or item.get("result") or ""),
        str(item.get("evidence_id") or ""),
    )


def _evidence_label(item: dict[str, Any]) -> str:
    subject = str(item.get("canonical_subject") or item.get("subject") or "").strip()
    action = str(item.get("canonical_action") or item.get("action") or "").strip()
    obj = str(item.get("canonical_object") or item.get("object") or item.get("result") or "").strip()
    return "".join(part for part in (subject, action, obj) if part)


def _section_writing_focus(
    dominant_type: str, subjects: list[str], actions: list[str], focuses: list[str],
) -> list[str]:
    focus: list[str] = []
    if subjects:
        focus.append(f"围绕{subjects[0]}等主体写清责任或工作承接关系")
    if actions:
        focus.append(f"围绕{actions[0]}等动作展开工作过程")
    if focuses:
        focus.append(f"围绕{focuses[0]}等对象组织事实层次")
    focus.append(f"按“{_PLAN_GOALS.get(dominant_type, '展开证据支撑内容')}”控制本部分写作方向")
    return focus[:4]


def _section_evidence_usage_plan(
    evidence_ids: list[str], evidence_by_id: dict[str, dict[str, Any]], *, required_count: int,
) -> dict[str, Any]:
    core_ids = evidence_ids[:required_count]
    supporting_ids = evidence_ids[required_count:]
    return {
        "core_evidence_ids": core_ids,
        "supporting_evidence_ids": supporting_ids,
        "usage_steps": [
            "先用core_evidence_ids写清本部分事实骨架",
            "再围绕同一事实补充过程说明、组织逻辑和必要总结",
            "supporting_evidence_ids只作同主题补强，不得改写为新增事实任务",
        ],
        "evidence_roles": [
            {
                "evidence_id": evidence_id,
                "role": "core" if evidence_id in core_ids else "supporting",
                "write_as": _evidence_label(evidence_by_id[evidence_id]),
            }
            for evidence_id in evidence_ids
            if evidence_id in evidence_by_id
        ],
    }


def _build_section_plan_fields(
    *, dominant_type: str, subjects: list[str], actions: list[str], focuses: list[str],
    evidence_ids: list[str], evidence_by_id: dict[str, dict[str, Any]], required_count: int,
) -> dict[str, Any]:
    return {
        "writing_focus": _section_writing_focus(dominant_type, subjects, actions, focuses),
        "expansion_path": list(_PLAN_EXPANSION_TEMPLATES.get(dominant_type, _PLAN_EXPANSION_TEMPLATES["action"])),
        "evidence_usage_plan": _section_evidence_usage_plan(
            evidence_ids, evidence_by_id, required_count=required_count,
        ),
    }


def _refresh_section_quality_fields(
    section: dict[str, Any], evidence_by_id: dict[str, dict[str, Any]],
) -> None:
    evidence_ids = list(section.get("selected_evidence_ids") or [])
    if not evidence_ids:
        evidence_ids = list(section.get("required_evidence_ids") or []) + list(section.get("optional_evidence_ids") or [])
    bound = [evidence_by_id[evidence_id] for evidence_id in evidence_ids if evidence_id in evidence_by_id]
    if not bound:
        section.setdefault("writing_focus", [])
        section.setdefault("expansion_path", [])
        section.setdefault("evidence_usage_plan", {"core_evidence_ids": [], "supporting_evidence_ids": [], "usage_steps": [], "evidence_roles": []})
        return
    types = Counter(str(item.get("evidence_type") or "context") for item in bound)
    dominant_type = sorted(types, key=lambda value: (-types[value], _PLAN_TYPE_ORDER.get(value, 99)))[0]
    subjects = list(dict.fromkeys(
        str(item.get("canonical_subject") or item.get("subject") or "") for item in bound
        if item.get("canonical_subject") or item.get("subject")
    ))
    actions = list(dict.fromkeys(
        str(item.get("canonical_action") or item.get("action") or "") for item in bound
        if item.get("canonical_action") or item.get("action")
    ))
    focuses = list(dict.fromkeys(
        str(item.get("canonical_object") or item.get("object") or item.get("result") or "")
        for item in bound if item.get("canonical_object") or item.get("object") or item.get("result")
    ))
    required_count = len(section.get("core_evidence_ids") or section.get("required_evidence_ids") or evidence_ids[:1])
    section.update(_build_section_plan_fields(
        dominant_type=dominant_type, subjects=subjects, actions=actions, focuses=focuses,
        evidence_ids=evidence_ids, evidence_by_id=evidence_by_id, required_count=required_count,
    ))


def _attach_reference_structure_guidance(
    sections: list[dict[str, Any]],
    reference_structure: dict[str, Any] | None,
) -> bool:
    """Attach one fact-free reference role to each deterministic plan section."""
    if (
        not reference_structure
        or not validate_reference_structure(reference_structure).get("passed")
        or reference_structure.get("status") != "EXTRACTED"
        or reference_structure.get("source_role") != "REFERENCE_STRUCTURE_ONLY_NOT_FACT_SOURCE"
        or not sections
    ):
        return False
    reference_sections = list(reference_structure.get("primary_structure") or [])
    if not reference_sections or len(reference_sections) != len(sections):
        return False

    for section, reference_section in zip(sections, reference_sections):
        section_role = str(reference_section.get("section_role") or "")
        section["section_role"] = section_role
        section["evidence_policy"] = _evidence_policy_for_role(section_role)
        section["reference_structure_guidance"] = {
            "source_role": "REFERENCE_STRUCTURE_ONLY_NOT_FACT_SOURCE",
            "section_roles": [section_role],
            "organization_focus": [str(reference_section.get("organization_focus") or "")],
            "development_logic": list(reference_section.get("development_logic") or []),
            "paragraph_functions": list(reference_section.get("paragraph_functions") or []),
            "fact_boundary": "仅指导组织顺序；所有现实事实仍须来自本section合法Fact Anchor",
        }
    return True


def _build_work_summary_content_plan(
    task: Any,
    evidence_pack: dict[str, Any],
    reference_structure: dict[str, Any] | None,
) -> dict[str, Any]:
    """Adapt dynamic work-summary intentions to the existing Content Plan schema."""

    evidence_by_id = {
        str(item.get("evidence_id") or ""): item
        for item in evidence_pack.get("evidence") or []
        if item.get("evidence_id")
    }
    dynamic_plan = plan_work_summary(task, evidence_pack, reference_structure)
    target_total = max(1, int(getattr(task, "target_words", 0) or 0))
    length_range = derive_target_length_range(target_total)
    intentions = list(dynamic_plan.get("sections") or [])
    allocations = _allocate_words_by_weights(
        target_total,
        [float(item.get("relative_weight") or item.get("importance") or 1.0) for item in intentions],
    )
    sections: list[dict[str, Any]] = []
    for index, intention in enumerate(intentions):
        section_role = str(intention.get("compatibility_role") or "work_progress")
        required_ids = [
            str(value) for value in intention.get("evidence_ids") or []
            if str(value) in evidence_by_id
        ]
        evidence_policy = (
            EVIDENCE_POLICY_FACT_REQUIRED
            if required_ids
            else EVIDENCE_POLICY_ANALYSIS_ONLY
        )
        common_fields = {
            "section_id": f"SEC-{index + 1:02d}",
            "section_role": section_role,
            "evidence_policy": evidence_policy,
            "content_functions": list(intention.get("content_functions") or []),
            "section_purpose": str(intention.get("section_purpose") or ""),
            "business_block": str(intention.get("business_block") or ""),
            "evidence_focus": list(intention.get("evidence_focus") or []),
            "importance": float(intention.get("importance") or 1.0),
            "priority": int(intention.get("priority") or index + 1),
            "problem_relationship": str(intention.get("problem_relationship") or "not_supported"),
            "unfinished_relationship": str(intention.get("unfinished_relationship") or "not_supported"),
            "next_step_relationship": str(intention.get("next_step_relationship") or "not_supported"),
            "related_evidence_ids": list(intention.get("related_evidence_ids") or []),
            "relative_weight": float(intention.get("relative_weight") or 1.0),
            "merged_from": list(intention.get("merged_from") or []),
            "recommended_words": allocations[index] if index < len(allocations) else 1,
            "minimum_fact_anchors": int(
                intention.get("minimum_fact_anchors")
                if intention.get("minimum_fact_anchors") is not None
                else (min(2, len(required_ids)) if required_ids else 0)
            ),
        }
        if intention.get("reference_structure_guidance"):
            common_fields["reference_structure_guidance"] = copy.deepcopy(
                intention["reference_structure_guidance"]
            )
        if evidence_policy == EVIDENCE_POLICY_ANALYSIS_ONLY:
            if section_role not in _ROLE_SECTION_PLANS:
                section_role = "problem_analysis"
                common_fields["section_role"] = section_role
            sections.append({
                **common_fields,
                **_analysis_only_plan_fields(section_role),
                "required_evidence_ids": [],
                "optional_evidence_ids": [],
                "allowed_fact_subjects": [],
                "allowed_fact_actions": [],
                "prohibited_specifics": [
                    "具体事件、数字、机构、职责和已完成动作",
                    "已经取得、形成、实现或提升的现实成果",
                ],
            })
            continue

        bound = [evidence_by_id[evidence_id] for evidence_id in required_ids]
        types = Counter(str(item.get("evidence_type") or "context") for item in bound)
        dominant_type = sorted(
            types,
            key=lambda value: (-types[value], _PLAN_TYPE_ORDER.get(value, 99)),
        )[0]
        subjects = list(dict.fromkeys(
            str(item.get("canonical_subject") or item.get("subject") or "")
            for item in bound if item.get("canonical_subject") or item.get("subject")
        ))
        actions = list(dict.fromkeys(
            str(item.get("canonical_action") or item.get("action") or "")
            for item in bound if item.get("canonical_action") or item.get("action")
        ))
        focuses = list(dict.fromkeys(
            str(item.get("canonical_object") or item.get("object") or item.get("result") or "")
            for item in bound if item.get("canonical_object") or item.get("object") or item.get("result")
        ))
        sections.append({
            **common_fields,
            "section_goal": str(intention.get("section_purpose") or _PLAN_GOALS[dominant_type]),
            "section_focus": list(dict.fromkeys([
                *list(intention.get("evidence_focus") or []),
                *focuses,
            ]))[:6],
            **_build_section_plan_fields(
                dominant_type=dominant_type,
                subjects=subjects,
                actions=actions,
                focuses=focuses[:6],
                evidence_ids=required_ids,
                evidence_by_id=evidence_by_id,
                required_count=len(required_ids),
            ),
            "required_evidence_ids": required_ids,
            "optional_evidence_ids": [],
            "allowed_fact_subjects": subjects,
            "allowed_fact_actions": actions,
            "prohibited_specifics": [
                "未绑定证据中的具体数字、时间、次数、时限",
                "未绑定证据中的部门职责、设备、地点、机制和结果",
            ],
        })

    reference_used = any(section.get("reference_structure_guidance") for section in sections)
    seed_payload = {
        "task": [str(getattr(task, name, "") or "") for name in (
            "title", "document_type", "writing_purpose", "business_domain", "level", "org_type",
            "target_words", "reference_scope",
        )],
        "planner": dynamic_plan,
    }
    plan = {
        "schema_version": "flow3-v3.1-content-plan-1",
        "plan_id": f"PLAN-{_sha(json.dumps(seed_payload, ensure_ascii=False, sort_keys=True))[:12].upper()}",
        "article_purpose": f"围绕{getattr(task, 'writing_purpose', '') or '用户目的'}形成工作总结",
        "recommended_length_range": length_range,
        "target_total": target_total,
        "section_count": len(sections),
        "required_structure_roles": [str(section.get("section_role") or "") for section in sections],
        "sections": sections,
        "allowed_general_analysis": [
            "只在当前业务板块及已绑定事实边界内说明组织逻辑",
            "问题和下一步仅按Planner确定的位置克制表达",
        ],
        "global_prohibited_claims": [
            "把一般性分析写成本地已经实施的事实",
            "增加未获证据支持的数字、频次、部门职责、设备、地点、机制或成效",
            "为达到目标字数虚构现实细节",
        ],
        "planning_basis": "work_summary_quality_contract_task_business_blocks_reference_compatibility_and_evidence",
        "planner_owner": "work_summary_planner_v1",
        "work_summary_planner": dynamic_plan,
        "reference_compatibility": copy.deepcopy(dynamic_plan.get("reference_compatibility") or {}),
        "uses_source_title": False,
        "uses_source_heading": False,
        "uses_source_chunk_order": False,
        "uses_section_count_template": False,
    }
    if reference_used:
        plan["uses_reference_structure"] = True
        plan["reference_structure"] = reference_structure
    return plan


def build_content_plan(
    task: Any,
    evidence_pack: dict[str, Any],
    reference_structure: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a local, deterministic, evidence-bound construction plan."""
    if is_work_summary_task(task):
        return _build_work_summary_content_plan(task, evidence_pack, reference_structure)

    evidence_by_id = {
        str(item.get("evidence_id") or ""): item
        for item in evidence_pack.get("evidence") or []
        if item.get("evidence_id")
    }
    required = [value for value in evidence_pack.get("required_evidence") or [] if value in evidence_by_id]
    optional = [value for value in evidence_pack.get("optional_evidence") or [] if value in evidence_by_id]
    required.sort(key=lambda evidence_id: _plan_evidence_key(evidence_by_id[evidence_id]))
    optional.sort(key=lambda evidence_id: _plan_evidence_key(evidence_by_id[evidence_id]))

    target_words = max(0, int(getattr(task, "target_words", 0) or 0))
    target_total = max(1, target_words)
    length_range = derive_target_length_range(target_total)
    valid_reference_sections: list[dict[str, Any]] = []
    if (
        reference_structure
        and validate_reference_structure(reference_structure).get("passed")
        and reference_structure.get("status") == "EXTRACTED"
        and reference_structure.get("source_role") == "REFERENCE_STRUCTURE_ONLY_NOT_FACT_SOURCE"
    ):
        candidate_sections = list(reference_structure.get("primary_structure") or [])
        if 3 <= len(candidate_sections) <= 5:
            valid_reference_sections = candidate_sections

    if valid_reference_sections:
        section_roles = [str(item.get("section_role") or "") for item in valid_reference_sections]
        section_count = len(section_roles)
    else:
        if len(required) >= 8:
            section_count = 5
        elif len(required) >= 7:
            section_count = 4
        else:
            section_count = min(3, len(required))
        section_roles = ["work_progress"] * section_count

    fact_section_indexes = [
        index for index, role in enumerate(section_roles)
        if _evidence_policy_for_role(role) == EVIDENCE_POLICY_FACT_REQUIRED
    ]
    if not fact_section_indexes:
        fact_section_indexes = list(range(section_count))
        section_roles = ["work_progress"] * section_count

    buckets_by_index: dict[int, list[str]] = {index: [] for index in fact_section_indexes}
    for index, evidence_id in enumerate(required):
        section_index = fact_section_indexes[index % len(fact_section_indexes)]
        buckets_by_index[section_index].append(evidence_id)
    optional_buckets_by_index: dict[int, list[str]] = {index: [] for index in fact_section_indexes}
    # Optional evidence remains available in the audited pack, but is not
    # automatically assigned.  A deterministic plan must never turn optional
    # material into de-facto mandatory coverage.
    optional_limit = 0
    for index, evidence_id in enumerate(optional[:optional_limit]):
        section_index = fact_section_indexes[index % len(fact_section_indexes)]
        optional_buckets_by_index[section_index].append(evidence_id)

    sections: list[dict[str, Any]] = []
    role_word_allocations = _allocate_words_by_role(target_total, section_roles)
    for index, section_role in enumerate(section_roles):
        words = role_word_allocations[index] if index < len(role_word_allocations) else 1
        evidence_policy = _evidence_policy_for_role(section_role)
        required_ids = list(buckets_by_index.get(index, []))
        optional_ids = list(optional_buckets_by_index.get(index, []))
        if evidence_policy == EVIDENCE_POLICY_ANALYSIS_ONLY:
            sections.append({
                "section_id": f"SEC-{index + 1:02d}",
                "section_role": section_role,
                "evidence_policy": evidence_policy,
                **_analysis_only_plan_fields(section_role),
                "required_evidence_ids": [],
                "optional_evidence_ids": [],
                "allowed_fact_subjects": [],
                "allowed_fact_actions": [],
                "prohibited_specifics": [
                    "具体事件、数字、机构、职责和已完成动作",
                    "已经取得、形成、实现或提升的现实成果",
                ],
                "recommended_words": words,
            })
            continue

        bound_ids = required_ids + optional_ids
        bound = [evidence_by_id[evidence_id] for evidence_id in bound_ids]
        types = Counter(str(item.get("evidence_type") or "context") for item in bound)
        dominant_type = sorted(types, key=lambda value: (-types[value], _PLAN_TYPE_ORDER.get(value, 99)))[0]
        subjects = list(dict.fromkeys(
            str(item.get("canonical_subject") or item.get("subject") or "") for item in bound
            if item.get("canonical_subject") or item.get("subject")
        ))
        actions = list(dict.fromkeys(
            str(item.get("canonical_action") or item.get("action") or "") for item in bound
            if item.get("canonical_action") or item.get("action")
        ))
        focuses = list(dict.fromkeys(
            str(item.get("canonical_object") or item.get("object") or item.get("result") or "")
            for item in bound if item.get("canonical_object") or item.get("object") or item.get("result")
        ))
        sections.append({
            "section_id": f"SEC-{index + 1:02d}",
            "section_role": section_role,
            "evidence_policy": evidence_policy,
            "section_goal": _PLAN_GOALS[dominant_type],
            "section_focus": focuses[:4],
            **_build_section_plan_fields(
                dominant_type=dominant_type, subjects=subjects, actions=actions, focuses=focuses[:4],
                evidence_ids=bound_ids, evidence_by_id=evidence_by_id, required_count=len(required_ids),
            ),
            "required_evidence_ids": required_ids,
            "optional_evidence_ids": optional_ids,
            "allowed_fact_subjects": subjects,
            "allowed_fact_actions": actions,
            "prohibited_specifics": [
                "未绑定证据中的具体数字、时间、次数、时限",
                "未绑定证据中的部门职责、设备、地点、机制和结果",
            ],
            "recommended_words": words,
        })

    reference_applied = _attach_reference_structure_guidance(
        sections,
        reference_structure if valid_reference_sections else None,
    )
    seed_payload = {
        "task": [str(getattr(task, name, "") or "") for name in (
            "title", "document_type", "writing_purpose", "business_domain", "level", "org_type",
            "target_words", "reference_scope",
        )],
        "required": required,
        "optional_used": optional[:optional_limit],
    }
    if reference_applied:
        seed_payload["reference_structure"] = reference_structure
    seed = json.dumps(seed_payload, ensure_ascii=False, sort_keys=True)
    plan = {
        "schema_version": "flow3-v3.1-content-plan-1",
        "plan_id": f"PLAN-{_sha(seed)[:12].upper()}",
        "article_purpose": f"围绕{getattr(task, 'writing_purpose', '') or '用户目的'}形成{getattr(task, 'document_type', '') or '公文初稿'}",
        "recommended_length_range": {
            "min_words": length_range["min_words"],
            "max_words": length_range["max_words"],
        },
        "target_total": target_total,
        "section_count": section_count,
        "required_structure_roles": list(section_roles),
        "sections": sections,
        "allowed_general_analysis": [
            "说明任务涉及多个环节及协同必要性",
            "分析风险前置识别与现场压力之间的一般关系",
            "提炼指挥链条、协同效率和服务保障的一般经验",
        ],
        "global_prohibited_claims": [
            "把一般性分析写成本地已经实施的事实",
            "增加未获证据支持的数字、频次、部门职责、设备、地点、机制或成效",
            "为达到目标字数虚构现实细节",
        ],
        "planning_basis": (
            "task_fields_atomic_evidence_and_fact_free_reference_structure"
            if reference_applied
            else "task_fields_and_atomic_evidence_only"
        ),
        "uses_source_title": False,
        "uses_source_heading": False,
        "uses_source_chunk_order": False,
    }
    if reference_applied:
        plan["uses_reference_structure"] = True
        plan["reference_structure"] = reference_structure
    return plan


def validate_content_plan(
    plan: dict[str, Any], evidence_pack: dict[str, Any], retrieval_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    hard_issues: list[dict[str, Any]] = []
    evidence_ids = {str(item.get("evidence_id") or "") for item in evidence_pack.get("evidence") or []}
    required = list(evidence_pack.get("required_evidence") or [])
    optional = set(evidence_pack.get("optional_evidence") or [])
    sections = list(plan.get("sections") or [])
    section_count = int(plan.get("section_count") or 0)
    dynamic_work_summary = plan.get("planner_owner") == "work_summary_planner_v1"
    if section_count != len(sections) or section_count <= 0:
        hard_issues.append({"rule": "section_count", "detail": section_count})
    elif not dynamic_work_summary and not 3 <= section_count <= 5:
        hard_issues.append({"rule": "section_count", "detail": section_count})
    assigned_required: list[str] = []
    used_optional: list[str] = []
    for section in sections:
        evidence_policy = str(section.get("evidence_policy") or EVIDENCE_POLICY_FACT_REQUIRED)
        section_required = list(section.get("required_evidence_ids") or [])
        section_optional = list(section.get("optional_evidence_ids") or [])
        section_selected = list(section.get("selected_evidence_ids") or [])
        plan_ids = section_required + section_optional + section_selected
        if evidence_policy == EVIDENCE_POLICY_FACT_REQUIRED and not section_required:
            hard_issues.append({"rule": "section_without_required_evidence", "section_id": section.get("section_id")})
        if evidence_policy == EVIDENCE_POLICY_ANALYSIS_ONLY and plan_ids:
            hard_issues.append({"rule": "analysis_section_has_fact_binding", "section_id": section.get("section_id")})
        if evidence_policy not in {EVIDENCE_POLICY_FACT_REQUIRED, EVIDENCE_POLICY_ANALYSIS_ONLY}:
            hard_issues.append({"rule": "evidence_policy", "section_id": section.get("section_id")})
        if any(value not in evidence_ids for value in plan_ids):
            hard_issues.append({"rule": "unknown_evidence_id", "section_id": section.get("section_id")})
        if any(value not in optional for value in section_optional):
            hard_issues.append({"rule": "nonoptional_assignment", "section_id": section.get("section_id")})
        for field in ("section_goal", "writing_focus", "expansion_path", "evidence_usage_plan"):
            if field not in section:
                hard_issues.append({
                    "rule": "missing_section_planning_field",
                    "field": field,
                    "section_id": section.get("section_id"),
                })
        if not isinstance(section.get("writing_focus"), list) or not section.get("writing_focus"):
            hard_issues.append({"rule": "empty_writing_focus", "section_id": section.get("section_id")})
        if not isinstance(section.get("expansion_path"), list) or len(section.get("expansion_path") or []) < 3:
            hard_issues.append({"rule": "weak_expansion_path", "section_id": section.get("section_id")})
        if dynamic_work_summary:
            for field in (
                "content_functions", "section_purpose", "business_block", "evidence_focus",
                "importance", "priority", "problem_relationship",
                "unfinished_relationship", "next_step_relationship",
            ):
                if field not in section:
                    hard_issues.append({
                        "rule": "missing_dynamic_planner_field",
                        "field": field,
                        "section_id": section.get("section_id"),
                    })
            if not isinstance(section.get("content_functions"), list) or not section.get("content_functions"):
                hard_issues.append({"rule": "empty_content_functions", "section_id": section.get("section_id")})
        usage = section.get("evidence_usage_plan") or {}
        usage_ids = [
            str(value)
            for value in (
                list(usage.get("core_evidence_ids") or [])
                + list(usage.get("supporting_evidence_ids") or [])
                + [
                    role.get("evidence_id")
                    for role in usage.get("evidence_roles") or []
                    if isinstance(role, dict)
                ]
            )
            if value
        ]
        section_known_ids = set(plan_ids)
        if section_selected:
            section_known_ids |= set(section_selected)
        if any(value not in evidence_ids or value not in section_known_ids for value in usage_ids):
            hard_issues.append({"rule": "evidence_usage_plan_unbound_id", "section_id": section.get("section_id")})
        assigned_required.extend(section_required)
        used_optional.extend(section_optional)
    if Counter(assigned_required) != Counter(required):
        hard_issues.append({"rule": "required_assignment", "expected": required, "actual": assigned_required})
    if len(assigned_required) != len(set(assigned_required)):
        hard_issues.append({"rule": "repeated_required_evidence"})
    if optional and set(used_optional) == optional:
        hard_issues.append({"rule": "all_optional_forced"})
    recommended = plan.get("recommended_length_range") or {}
    min_words = int(recommended.get("min_words") or 0)
    max_words = int(recommended.get("max_words") or 0)
    if not (0 < min_words <= max_words):
        hard_issues.append({"rule": "recommended_length_range", "detail": recommended})
    if "target_total" in plan:
        target_total = int(plan.get("target_total") or 0)
        allocated = sum(int(section.get("recommended_words") or 0) for section in sections)
        if allocated != target_total:
            hard_issues.append({"rule": "recommended_words_sum", "target_total": target_total, "actual": allocated})
        if not dynamic_work_summary and any(
            int(section.get("recommended_words") or 0) < int(target_total * 0.20)
            for section in sections
        ):
            hard_issues.append({"rule": "section_minimum_share", "target_total": target_total})
        if plan.get("schema_version") == "flow3-v3.3-content-plan-1":
            normalized_goals = [_normalized_goal(section.get("section_goal")) for section in sections]
            if len(normalized_goals) != len(set(normalized_goals)):
                hard_issues.append({"rule": "duplicate_section_goal"})
    serialized = json.dumps(plan, ensure_ascii=False)
    if (
        re.search(r"[一二三四五六七八九十]个聚力", serialized)
        or any(marker in serialized for marker in ("FLOW3-CASE-", "primary_document_id"))
    ):
        hard_issues.append({"rule": "forbidden_planning_basis"})
    if retrieval_payload:
        source_titles = [str(item.get("source_title") or "") for item in retrieval_payload.get("ranked_documents") or []]
        if any(title and title in serialized for title in source_titles):
            hard_issues.append({"rule": "source_title_leak"})
    if plan.get("uses_source_title") or plan.get("uses_source_heading") or plan.get("uses_source_chunk_order"):
        hard_issues.append({"rule": "source_structure_dependency"})
    reference = plan.get("reference_structure")
    if plan.get("uses_reference_structure"):
        if (
            not isinstance(reference, dict)
            or not validate_reference_structure(reference).get("passed")
            or reference.get("status") != "EXTRACTED"
            or reference.get("source_role") != "REFERENCE_STRUCTURE_ONLY_NOT_FACT_SOURCE"
        ):
            hard_issues.append({"rule": "invalid_reference_structure_boundary"})
        if dynamic_work_summary:
            compatibility = plan.get("reference_compatibility") or {}
            if compatibility.get("status") not in {REFERENCE_COMPATIBLE, REFERENCE_PARTIAL}:
                hard_issues.append({"rule": "reference_compatibility_not_usable"})
            guided = [
                section.get("reference_structure_guidance")
                for section in sections
                if section.get("reference_structure_guidance") is not None
            ]
            if not guided or any(
                not isinstance(guidance, dict)
                or guidance.get("source_role") != "REFERENCE_STRUCTURE_ONLY_NOT_FACT_SOURCE"
                for guidance in guided
            ):
                hard_issues.append({"rule": "missing_compatible_reference_guidance"})
        else:
            for section in sections:
                guidance = section.get("reference_structure_guidance")
                if (
                    not isinstance(guidance, dict)
                    or guidance.get("source_role") != "REFERENCE_STRUCTURE_ONLY_NOT_FACT_SOURCE"
                ):
                    hard_issues.append({
                        "rule": "missing_reference_structure_guidance",
                        "section_id": section.get("section_id"),
                    })
            expected_roles = list(reference.get("overall_progression") or []) if isinstance(reference, dict) else []
            actual_roles = [str(section.get("section_role") or "") for section in sections]
            if actual_roles != expected_roles or list(plan.get("required_structure_roles") or []) != expected_roles:
                hard_issues.append({
                    "rule": "reference_structure_role_coverage",
                    "expected": expected_roles,
                    "actual": actual_roles,
                })
    elif reference is not None or any(
        section.get("reference_structure_guidance") is not None for section in sections
    ):
        hard_issues.append({"rule": "unmarked_reference_structure"})
    if dynamic_work_summary:
        compatibility = plan.get("reference_compatibility") or {}
        if compatibility.get("status") not in {
            REFERENCE_COMPATIBLE,
            REFERENCE_PARTIAL,
            REFERENCE_INCOMPATIBLE,
        }:
            hard_issues.append({"rule": "reference_compatibility_status"})
        if plan.get("uses_section_count_template") is not False:
            hard_issues.append({"rule": "work_summary_section_count_template"})
    return {
        "flow3_v3_status": "PASS" if not hard_issues else "BLOCKED_INVALID_CONTENT_PLAN",
        "passed": not hard_issues,
        "hard_issue_count": len(hard_issues),
        "hard_issues": hard_issues,
        "section_count": section_count,
        "required_evidence_assigned_once": Counter(assigned_required) == Counter(required),
        "optional_evidence_forced": bool(optional and set(used_optional) == optional),
        "recommended_length_can_be_below_target": False,
        "uses_source_title": False,
        "uses_source_heading": False,
        "uses_source_chunk_order": False,
        "uses_reference_structure": bool(plan.get("uses_reference_structure")),
    }


def _semantic_ngrams(value: Any, size: int = 2) -> set[str]:
    han = "".join(re.findall(r"[\u3400-\u9fffA-Za-z0-9]", str(value or "")))
    return {han[index:index + size] for index in range(max(0, len(han) - size + 1))}


def _selection_visible(item: dict[str, Any]) -> str:
    return "".join(str(item.get(key) or "") for key in (
        "evidence_type", "canonical_subject", "canonical_action", "canonical_object",
        "subject", "action", "object", "result",
    ))


def _near_duplicate(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_action = _compact(left.get("canonical_action") or left.get("action"))
    right_action = _compact(right.get("canonical_action") or right.get("action"))
    left_object = _compact(left.get("canonical_object") or left.get("object") or left.get("result"))
    right_object = _compact(right.get("canonical_object") or right.get("object") or right.get("result"))
    if left_action == right_action and left_object == right_object:
        return True
    a, b = _semantic_ngrams(left_object), _semantic_ngrams(right_object)
    overlap = len(a & b) / max(1, len(a | b))
    return bool(left_action == right_action and overlap >= 0.72)


def _selection_score(
    item: dict[str, Any], *, section_text: str, task_text: str, need: str | None,
) -> int:
    visible = _selection_visible(item)
    section_overlap = len(_semantic_ngrams(visible) & _semantic_ngrams(section_text))
    task_overlap = len(_semantic_ngrams(visible) & _semantic_ngrams(task_text))
    category = str(item.get("evidence_type") or "")
    score = section_overlap * 5 + task_overlap * 2
    if need == "core" and category in {"action", "mechanism"}:
        score += 25
    if need == "context" and category in {"context", "risk", "scene"}:
        score += 25
    subject = _compact(item.get("canonical_subject") or item.get("subject"))
    action = _compact(item.get("canonical_action") or item.get("action"))
    obj = _compact(item.get("canonical_object") or item.get("object") or item.get("result"))
    if action and action != "反映":
        score += 4
    if subject and subject not in {"相关责任主体", "全面", "持续", "不断"}:
        score += 3
    if 3 <= len(obj) <= 18:
        score += 3
    if any(marker in visible for marker in ("聚力", "下一步", "将认真总结")):
        score -= 30
    return score


_NON_ANCHOR_SUBJECTS = {"相关责任主体", "有关方面", "各方", "相关单位", "相关部门"}
_INCOMPLETE_SUBJECTS = {
    "全面", "持续", "不断", "高位", "牵头", "圆满", "在现场", "累计为群众",
}
_EXACT_VALUE_FIELDS = (
    "time", "frequency", "quantity", "organization", "organization_role",
    "equipment", "mechanism", "place", "result",
)


def _paired_symbols_closed(value: str) -> bool:
    pairs = (("“", "”"), ("‘", "’"), ("《", "》"), ("（", "）"), ("(", ")"), ("【", "】"), ("[", "]"))
    return all(value.count(left) == value.count(right) for left, right in pairs) and value.count('"') % 2 == 0


def evidence_completeness(
    item: dict[str, Any], evidence_pack: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Check whether a triple is independently writable using atomic fields only."""
    subject = _compact(item.get("canonical_subject") or item.get("subject"))
    action = _compact(item.get("canonical_action") or item.get("action"))
    obj = _compact(item.get("canonical_object") or item.get("object") or item.get("result"))
    reasons: list[str] = []
    if not subject or not action or not obj:
        reasons.append("empty_core_component")
    if obj and not _paired_symbols_closed(obj):
        reasons.append("unclosed_paired_symbol")
    if obj and re.search(r"(?:以及|并且|或者|和|与|及|或|、|，|的|对|向|为|从|由)$", obj):
        reasons.append("dangling_connector")
    if obj and re.search(r"(?:将|正|已|成|并)$", obj):
        reasons.append("truncated_ending")
    if obj and re.search(r"[“‘《（(【\[]$", obj):
        reasons.append("dangling_open_symbol")
    if subject in _INCOMPLETE_SUBJECTS or subject.startswith(("以", "通过", "根据", "结合", "针对", "依托", "围绕")):
        reasons.append("non_independent_subject")
    if any(marker in subject for marker in ("坚决扛起", "持续", "不断", "高位", "聚力")):
        reasons.append("non_independent_subject")
    if action and action in subject:
        reasons.append("action_embedded_in_subject")
    if subject and action and obj.startswith(f"{subject}{action}"):
        reasons.append("repeated_triple_as_object")
    if evidence_pack and item.get("source_span_sha256"):
        siblings = [
            value for value in evidence_pack.get("evidence") or []
            if value.get("source_span_sha256") == item.get("source_span_sha256")
            and _compact(value.get("canonical_subject") or value.get("subject")) == subject
            and _compact(value.get("canonical_action") or value.get("action")) == action
            and value.get("evidence_id") != item.get("evidence_id")
        ]
        if siblings and len(obj) <= 2 and not re.search(r"(?:部门|单位|机构|区域|道路|通道|场地|汇报|会议|专班|边界|重点)$", obj):
            reasons.append("truncated_parallel_fragment")
    return {"passed": not reasons, "reasons": list(dict.fromkeys(reasons))}


def evidence_writability(
    item: dict[str, Any], evidence_pack: dict[str, Any] | None = None,
) -> str:
    """Classify one atom without consulting source prose, titles, IDs, or source order."""
    completeness = evidence_completeness(item, evidence_pack)
    if not completeness["passed"]:
        return "unwritable"
    subject = _compact(item.get("canonical_subject") or item.get("subject"))
    action = _compact(item.get("canonical_action") or item.get("action"))
    obj = _compact(item.get("canonical_object") or item.get("object") or item.get("result"))
    departments = {
        _compact(value) for value in item.get("departments") or [] if _compact(value)
    }
    uniquely_normalized = bool(subject and subject in departments and len(departments) == 1)
    generic_subject = (
        (subject in _NON_ANCHOR_SUBJECTS and not uniquely_normalized)
        or any(marker in subject for marker in ("坚决扛起", "持续", "不断", "高位", "聚力"))
    )
    directional = action in {"反映", "呈现"} or str(item.get("evidence_type") or "") in {
        "risk", "context", "scene", "expression",
    }
    return (
        "fact_anchorable"
        if subject and action and obj and not generic_subject and not directional
        else "analysis_support_only"
    )


def _repair_duplicate_goals_from_focus(content_plan: dict[str, Any]) -> dict[str, Any]:
    repaired = copy.deepcopy(content_plan)
    seen: set[str] = set()
    for section in repaired.get("sections") or []:
        normalized = _normalized_goal(section.get("section_goal"))
        if normalized in seen:
            focus = "、".join(str(value) for value in (section.get("section_focus") or [])[:2] if value)
            focus = focus or "本部分重点"
            section["section_goal"] = f"围绕{focus}说明重点工作落点"
            normalized = _normalized_goal(section["section_goal"])
            suffix = 2
            while normalized in seen:
                section["section_goal"] = f"围绕{focus}说明第{suffix}层工作落点"
                normalized = _normalized_goal(section["section_goal"])
                suffix += 1
        seen.add(normalized)
    return repaired


_SECTION_THEMES = {
    "risk": {
        "goal": ("风险", "防范", "隐患", "安全", "前置"),
        "actions": ("排查", "踏勘", "听取", "检查", "评估", "明确"),
        "objects": ("风险", "隐患", "安全", "场地", "通道", "道路", "管控重点", "安保汇报"),
    },
    "coordination": {
        "goal": ("组织", "协同", "机制", "联动", "统筹", "责任", "专班", "部门"),
        "actions": ("召开", "统筹", "实行", "成立", "构建", "明确", "协调"),
        "objects": ("会议", "分工", "责任", "专班", "部门", "联动", "指挥", "边界", "条块", "协同", "职责"),
    },
    "service": {
        "goal": ("服务", "保障", "便民", "救助"),
        "actions": ("提供", "设置", "保障", "安排"),
        "objects": ("服务", "救助", "饮水", "咨询", "保障"),
    },
    "result": {
        "goal": ("成效", "结果", "总结", "完成"),
        "actions": ("实现", "完成", "取得"),
        "objects": ("目标", "任务", "事故", "事件", "舆情"),
    },
}


def section_evidence_match(item: dict[str, Any], section: dict[str, Any]) -> dict[str, Any]:
    """Measure direct semantic fit from section goal/focus and atomic fields only."""
    goal = _compact(section.get("section_goal"))
    focus = _compact("".join(str(value) for value in section.get("section_focus") or []))
    action = _compact(item.get("canonical_action") or item.get("action"))
    obj = _compact(item.get("canonical_object") or item.get("object") or item.get("result"))
    visible = f"{action}{obj}"
    focus_overlap = len(_semantic_ngrams(visible) & _semantic_ngrams(focus))
    goal_overlap = len(_semantic_ngrams(visible) & _semantic_ngrams(goal))
    theme_hits: list[str] = []
    theme_score = 0
    for name, theme in _SECTION_THEMES.items():
        if not any(marker in goal or marker in focus for marker in theme["goal"]):
            continue
        if any(marker in action for marker in theme["actions"]) or any(marker in obj for marker in theme["objects"]):
            theme_hits.append(name)
            theme_score += 18
    score = focus_overlap * 8 + goal_overlap * 3 + theme_score
    provenance_free_legacy_record = not item.get("source_span_sha256")
    direct = focus_overlap > 0 or theme_score > 0 or goal_overlap >= 2 or provenance_free_legacy_record
    if provenance_free_legacy_record:
        score += 1
    return {
        "matched": direct,
        "score": score,
        "focus_overlap": focus_overlap,
        "goal_overlap": goal_overlap,
        "theme_hits": theme_hits,
        "provenance_free_legacy_record": provenance_free_legacy_record,
    }


def _bound_exact_values(item: dict[str, Any]) -> dict[str, list[str]]:
    """Keep only exact values demonstrably attached to this atom's local triple."""
    triple = _compact("".join(str(item.get(name) or "") for name in (
        "canonical_subject", "canonical_action", "canonical_object", "subject", "action", "object", "result",
    )))
    subject = _compact(item.get("canonical_subject") or item.get("subject"))

    def contained(values: Iterable[Any]) -> list[str]:
        return list(dict.fromkeys(
            str(value) for value in values
            if _compact(value) and _compact(value) in triple
        ))

    organizations = list(dict.fromkeys(
        str(value) for value in item.get("departments") or []
        if _compact(value) and (
            _compact(value) == subject
            or _compact(value) in subject
            or subject in _compact(value)
            or _compact(value) in triple
            or (
                _compact(item.get("canonical_object") or item.get("object"))
                and _compact(item.get("canonical_object") or item.get("object")) in _compact(value)
            )
        )
    ))
    result_values = contained(item.get("results") or [])
    if item.get("result") and _compact(item.get("result")) in triple:
        result_values = list(dict.fromkeys([*result_values, str(item["result"])]))
    bound_times = contained(item.get("times") or [])
    frequencies = [value for value in bound_times if FREQUENCY_RE.search(value)]
    times = [value for value in bound_times if value not in set(frequencies)]
    return {
        key: values for key, values in {
            "time": times,
            "frequency": frequencies,
            "quantity": contained(item.get("quantities") or []),
            "organization": organizations,
            "organization_role": [],
            "equipment": contained(item.get("equipment") or []),
            "mechanism": contained(item.get("mechanisms") or []),
            "place": contained(item.get("places") or []),
            "result": result_values,
        }.items() if values
    }


def build_fact_anchor_pack(
    selection: dict[str, Any], evidence_pack: dict[str, Any],
) -> dict[str, Any]:
    """Build anchors with completeness, section-fit, and source-span deduplication."""
    by_id = {
        str(item.get("evidence_id") or ""): item
        for item in evidence_pack.get("evidence") or [] if item.get("evidence_id")
    }
    anchors: list[dict[str, Any]] = []
    analysis: list[dict[str, Any]] = []
    sections = list((selection.get("content_plan") or {}).get("sections") or [])
    section_by_id = {str(section.get("section_id") or ""): section for section in sections}
    candidate_groups: dict[str, list[dict[str, str]]] = {}
    for section in sections:
        section_id = str(section.get("section_id") or "")
        for evidence_id in section.get("selected_evidence_ids") or []:
            item = by_id.get(str(evidence_id), {})
            span = str(item.get("source_span_sha256") or f"EVIDENCE:{evidence_id}")
            candidate_groups.setdefault(span, []).append({"evidence_id": str(evidence_id), "section_id": section_id})
    span_state: dict[str, dict[str, Any]] = {}
    deleted_by_span: dict[str, list[dict[str, str]]] = {}
    for section in sections:
        section_id = str(section.get("section_id") or "")
        core_ids = set(section.get("core_evidence_ids") or [])
        for evidence_id in section.get("selected_evidence_ids") or []:
            item = by_id.get(str(evidence_id))
            if not item:
                continue
            writability = evidence_writability(item, evidence_pack)
            match = section_evidence_match(item, section)
            span = str(item.get("source_span_sha256") or f"EVIDENCE:{evidence_id}")
            if writability == "unwritable":
                deleted_by_span.setdefault(span, []).append({
                    "evidence_id": str(evidence_id), "reason": "incomplete_or_context_dependent",
                })
                continue
            if not match["matched"]:
                deleted_by_span.setdefault(span, []).append({
                    "evidence_id": str(evidence_id), "reason": "section_goal_or_focus_mismatch",
                })
                continue
            existing = span_state.get(span)
            if existing and existing["section_id"] != section_id:
                deleted_by_span.setdefault(span, []).append({
                    "evidence_id": str(evidence_id), "reason": "source_span_cross_section",
                })
                continue
            if writability == "analysis_support_only":
                if existing:
                    deleted_by_span.setdefault(span, []).append({
                        "evidence_id": str(evidence_id), "reason": "source_span_already_retained",
                    })
                    continue
                analysis.append({
                    "evidence_id": str(evidence_id),
                    "section_id": section_id,
                    "category": str(item.get("evidence_type") or ""),
                    "canonical_subject": str(item.get("canonical_subject") or item.get("subject") or ""),
                    "canonical_action": str(item.get("canonical_action") or item.get("action") or ""),
                    "canonical_object": str(item.get("canonical_object") or item.get("object") or item.get("result") or ""),
                    **_semantic_state_fields_for_propagation(item),
                })
                span_state[span] = {"section_id": section_id, "kind": "analysis", "item": item}
                continue
            role = "core" if evidence_id in core_ids else "supporting"
            if existing:
                existing_item = existing.get("item") or {}
                independent = existing.get("kind") == "fact" and not _near_duplicate(existing_item, item)
                already_supporting = any(
                    anchor.get("section_id") == section_id and anchor.get("role") == "supporting"
                    and by_id.get(str(anchor.get("evidence_id")), {}).get("source_span_sha256") == item.get("source_span_sha256")
                    for anchor in anchors
                )
                if not independent or already_supporting:
                    deleted_by_span.setdefault(span, []).append({
                        "evidence_id": str(evidence_id),
                        "reason": "same_span_not_independent" if not independent else "same_span_supporting_limit",
                    })
                    continue
                role = "supporting"
            exact_values = _bound_exact_values(item)
            anchors.append({
                "evidence_id": str(evidence_id),
                "section_id": section_id,
                "role": role,
                "canonical_subject": str(item.get("canonical_subject") or item.get("subject") or ""),
                "canonical_action": str(item.get("canonical_action") or item.get("action") or ""),
                "canonical_object": str(item.get("canonical_object") or item.get("object") or item.get("result") or ""),
                "exact_values": exact_values,
                "forbidden_detail_types": [name for name in _EXACT_VALUE_FIELDS if name not in exact_values],
                **_semantic_state_fields_for_propagation(item),
            })
            if not existing:
                span_state[span] = {"section_id": section_id, "kind": "fact", "item": item}
    for section in sections:
        section_id = str(section.get("section_id") or "")
        section_anchors = [anchor for anchor in anchors if anchor.get("section_id") == section_id]
        core_spans = {
            str((by_id.get(str(anchor.get("evidence_id") or ""), {}) or {}).get("source_span_sha256") or f"EVIDENCE:{anchor.get('evidence_id')}")
            for anchor in section_anchors
            if anchor.get("role") == "core"
        }
        core_count = sum(anchor.get("role") == "core" for anchor in section_anchors)
        promotion_candidates: list[tuple[int, int, str, dict[str, Any]]] = []
        for anchor in section_anchors:
            if anchor.get("role") != "supporting":
                continue
            source = by_id.get(str(anchor.get("evidence_id") or ""), {})
            span = str(source.get("source_span_sha256") or f"EVIDENCE:{anchor.get('evidence_id')}")
            if span in core_spans:
                continue
            match = section_evidence_match(source, section)
            exact_value_count = sum(len(value) for value in (anchor.get("exact_values") or {}).values())
            promotion_candidates.append((
                int(match.get("score") or 0),
                exact_value_count,
                str(anchor.get("evidence_id") or ""),
                anchor,
            ))
        for _score, _exact_value_count, _evidence_id, anchor in sorted(
            promotion_candidates, key=lambda value: (-value[0], -value[1], value[2])
        ):
            if core_count >= _minimum_fact_anchors(section):
                break
            source = by_id.get(str(anchor.get("evidence_id") or ""), {})
            span = str(source.get("source_span_sha256") or f"EVIDENCE:{anchor.get('evidence_id')}")
            anchor["role"] = "core"
            core_spans.add(span)
            core_count += 1
    dedup_entries: list[dict[str, Any]] = []
    for span, candidates in sorted(candidate_groups.items()):
        candidate_ids = [item["evidence_id"] for item in candidates]
        retained = [anchor for anchor in anchors if anchor.get("evidence_id") in candidate_ids]
        deleted = deleted_by_span.get(span, [])
        dedup_entries.append({
            "source_span_sha256": span,
            "original_candidate_evidence_ids": candidate_ids,
            "retained_core_evidence_id": next((item["evidence_id"] for item in retained if item.get("role") == "core"), ""),
            "retained_supporting_evidence_ids": [item["evidence_id"] for item in retained if item.get("role") == "supporting"],
            "deleted_supporting_evidence_ids": [item["evidence_id"] for item in deleted],
            "deletion_reasons": deleted,
            "section_id": sorted({item["section_id"] for item in candidates}),
        })
    retained_section_spans: dict[str, set[str]] = {}
    retained_core_spans: Counter[str] = Counter()
    for anchor in anchors:
        source = by_id.get(str(anchor.get("evidence_id") or ""), {})
        span = str(source.get("source_span_sha256") or f"EVIDENCE:{anchor.get('evidence_id')}")
        retained_section_spans.setdefault(span, set()).add(str(anchor.get("section_id") or ""))
        if anchor.get("role") == "core":
            retained_core_spans[span] += 1
    for item in analysis:
        source = by_id.get(str(item.get("evidence_id") or ""), {})
        span = str(source.get("source_span_sha256") or f"EVIDENCE:{item.get('evidence_id')}")
        retained_section_spans.setdefault(span, set()).add(str(item.get("section_id") or ""))
    cross_conflicts = sum(len(values) > 1 for values in retained_section_spans.values())
    multi_core_conflicts = sum(value > 1 for value in retained_core_spans.values())
    replan_metadata = selection.get("replan_metadata")
    if isinstance(replan_metadata, dict) and replan_metadata.get("replan_applied"):
        replan_metadata["fact_anchor_rebuild_performed"] = True
        content_plan = selection.get("content_plan") or {}
        content_plan["work_summary_section_replan"] = copy.deepcopy(replan_metadata)
    return {
        "schema_version": "flow3-v3.3-fact-anchor-1",
        "fact_anchors": anchors,
        "analysis_support_pack": analysis,
        "source_span_dedup_report": {
            "passed": cross_conflicts == 0 and multi_core_conflicts == 0,
            "source_span_cross_section_conflict_count": cross_conflicts,
            "source_span_multi_core_conflict_count": multi_core_conflicts,
            "entries": dedup_entries,
        },
    }


def _normalized_goal(value: Any) -> str:
    return re.sub(r"[^\u3400-\u9fffA-Za-z0-9]", "", str(value or ""))


def _goal_signature(section: dict[str, Any], anchors: list[dict[str, Any]]) -> tuple[str, str]:
    section_anchors = [item for item in anchors if item.get("section_id") == section.get("section_id")]
    first = section_anchors[0] if section_anchors else {}
    return (
        _compact(first.get("canonical_action") or ""),
        _compact(first.get("canonical_object") or next(iter(section.get("section_focus") or []), "")),
    )


def repair_content_plan(
    task: Any, content_plan: dict[str, Any], fact_anchor_pack: dict[str, Any],
    *, target_words_provided: bool | None = None,
) -> dict[str, Any]:
    """Fix duplicate goals and allocate the user's target word count exactly."""
    del target_words_provided
    repaired = _repair_duplicate_goals_from_focus(content_plan)
    sections = list(repaired.get("sections") or [])
    anchors = list(fact_anchor_pack.get("fact_anchors") or [])

    target_total = max(1, int(getattr(task, "target_words", 0) or 0))
    repaired["recommended_length_range"] = derive_target_length_range(target_total)
    counts = [sum(item.get("section_id") == section.get("section_id") for item in anchors) for section in sections]
    section_count = len(sections)
    section_roles = [str(section.get("section_role") or "") for section in sections]
    if repaired.get("planner_owner") == "work_summary_planner_v1":
        allocations = _allocate_words_by_weights(
            target_total,
            [
                max(0.1, float(section.get("relative_weight") or section.get("importance") or 1.0))
                + (counts[index] * 0.25)
                for index, section in enumerate(sections)
            ],
        )
    elif section_count and not repaired.get("uses_reference_structure"):
        allocations = _allocate_words_by_role(target_total, section_roles)
    else:
        floor = int(target_total * 0.20)
        allocations = [floor] * section_count
        remaining = target_total - sum(allocations)
        weight_total = sum(counts) or section_count
        raw_shares = [remaining * (count or (1 if not sum(counts) else 0)) / weight_total for count in counts]
        whole = [int(value) for value in raw_shares]
        for index, value in enumerate(whole):
            allocations[index] += value
        residue = target_total - sum(allocations)
        order = sorted(range(section_count), key=lambda index: (-(raw_shares[index] - whole[index]), index))
        for index in order[:residue]:
            allocations[index] += 1
    for section, words in zip(sections, allocations):
        section["recommended_words"] = words
    anchor_items = {
        str(item.get("evidence_id") or ""): {
            "evidence_id": str(item.get("evidence_id") or ""),
            "evidence_type": str(item.get("category") or item.get("evidence_type") or "action"),
            "canonical_subject": str(item.get("canonical_subject") or ""),
            "canonical_action": str(item.get("canonical_action") or ""),
            "canonical_object": str(item.get("canonical_object") or ""),
            "subject": str(item.get("canonical_subject") or ""),
            "action": str(item.get("canonical_action") or ""),
            "object": str(item.get("canonical_object") or ""),
        }
        for item in (
            list(fact_anchor_pack.get("fact_anchors") or [])
            + list(fact_anchor_pack.get("analysis_support_pack") or [])
        )
        if item.get("evidence_id")
    }
    for section in sections:
        if anchor_items:
            _refresh_section_quality_fields(section, anchor_items)
    if (
        repaired.get("planner_owner") == "work_summary_planner_v1"
        and isinstance(repaired.get("evidence_capacity_budget"), dict)
    ):
        capacity_budget = refresh_capacity_budget_with_anchors(
            repaired["evidence_capacity_budget"],
            {**repaired, "sections": sections},
            fact_anchor_pack,
        )
        capacity_by_section = {
            str(row.get("section_id") or ""): row
            for row in capacity_budget.get("section_capacity") or []
        }
        for section in sections:
            capacity = capacity_by_section.get(str(section.get("section_id") or ""), {})
            for field in (
                "capacity_status", "available_fact_anchorable", "distinct_fact_source_spans",
                "available_work_chains", "chain_member_count", "supporting_evidence_count",
                "analysis_support_only_count", "confirmed_fact_anchor_count",
                "confirmed_analysis_support_count", "requested_word_budget",
                "recommended_relative_budget", "recommended_capacity_aware_word_budget",
            ):
                if field in capacity:
                    section[field] = capacity[field]
        repaired["evidence_capacity_budget"] = capacity_budget
        repaired["requested_target_words"] = capacity_budget["requested_target_words"]
        repaired["evidence_supported_words"] = capacity_budget["evidence_supported_words"]
        repaired["capacity_gap"] = capacity_budget["capacity_gap"]
    repaired["sections"] = sections
    repaired["target_total"] = target_total
    repaired["schema_version"] = "flow3-v3.3-content-plan-1"
    return repaired


def validate_fact_anchor_pack(
    fact_anchor_pack: dict[str, Any], selection: dict[str, Any],
    evidence_pack: dict[str, Any] | None = None,
    fact_anchor_outbound: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    anchors = list(fact_anchor_pack.get("fact_anchors") or [])
    analysis = list(fact_anchor_pack.get("analysis_support_pack") or [])
    base_anchor_fields = {
        "evidence_id", "section_id", "role", "canonical_subject", "canonical_action",
        "canonical_object", "exact_values", "forbidden_detail_types",
    }
    semantic_state_fields = {
        "semantic_state_contract_version", "semantic_state", "state_evidence",
        "state_qualifier", "negation_markers", "modality_markers", "completion_markers",
    }
    allowed_anchor_field_sets = {
        frozenset(base_anchor_fields),
        frozenset(base_anchor_fields | semantic_state_fields),
    }
    selected_by_section = {
        str(section.get("section_id") or ""): set(section.get("selected_evidence_ids") or [])
        for section in (selection.get("content_plan") or {}).get("sections") or []
    }
    sections_by_id = {
        str(section.get("section_id") or ""): section
        for section in (selection.get("content_plan") or {}).get("sections") or []
    }
    by_id = {
        str(item.get("evidence_id") or ""): item
        for item in (evidence_pack or {}).get("evidence") or []
    }
    incomplete_count = 0
    section_mismatch_count = 0
    analysis_mismatch_count = 0
    for anchor in anchors:
        section_id = str(anchor.get("section_id") or "")
        if frozenset(anchor) not in allowed_anchor_field_sets:
            issues.append({"rule": "anchor_fields", "evidence_id": anchor.get("evidence_id")})
        if anchor.get("evidence_id") not in selected_by_section.get(section_id, set()):
            issues.append({"rule": "anchor_not_selected_in_section", "evidence_id": anchor.get("evidence_id")})
        if any(not _compact(anchor.get(name)) for name in (
            "canonical_subject", "canonical_action", "canonical_object",
        )):
            issues.append({"rule": "empty_core_triple", "evidence_id": anchor.get("evidence_id")})
        source_item = by_id.get(str(anchor.get("evidence_id") or ""), anchor)
        if evidence_writability(source_item, evidence_pack) != "fact_anchorable":
            incomplete_count += 1
            issues.append({"rule": "non_writable_anchor", "evidence_id": anchor.get("evidence_id")})
        section = sections_by_id.get(section_id, {})
        if section and not section_evidence_match(source_item, section)["matched"]:
            section_mismatch_count += 1
            issues.append({"rule": "anchor_section_mismatch", "evidence_id": anchor.get("evidence_id")})
        exact_values = anchor.get("exact_values") or {}
        forbidden = set(anchor.get("forbidden_detail_types") or [])
        conflict = sorted(forbidden & set(exact_values))
        if conflict:
            issues.append({"rule": "exact_forbidden_conflict", "evidence_id": anchor.get("evidence_id"), "detail": conflict})
        expected_exact = _bound_exact_values(source_item) if by_id else exact_values
        if exact_values != expected_exact:
            issues.append({"rule": "unbound_exact_value", "evidence_id": anchor.get("evidence_id")})
        if (
            by_id
            and _semantic_state_fields_for_propagation(source_item)
            and semantic_state_payload(anchor) != semantic_state_payload(source_item)
        ):
            issues.append({"rule": "semantic_state_not_read_only", "evidence_id": anchor.get("evidence_id")})
    for section_id, selected_ids in selected_by_section.items():
        section = sections_by_id.get(section_id, {})
        evidence_policy = str(section.get("evidence_policy") or EVIDENCE_POLICY_FACT_REQUIRED)
        section_anchors = [item for item in anchors if item.get("section_id") == section_id]
        section_analysis = [item for item in analysis if item.get("section_id") == section_id]
        minimum_anchors = _minimum_fact_anchors(section)
        if evidence_policy == EVIDENCE_POLICY_FACT_REQUIRED and len(section_anchors) < minimum_anchors:
            issues.append({"rule": "section_anchor_count", "section_id": section_id})
        if evidence_policy == EVIDENCE_POLICY_FACT_REQUIRED and sum(item.get("role") == "core" for item in section_anchors) < minimum_anchors:
            issues.append({"rule": "section_core_anchor_count", "section_id": section_id})
        if evidence_policy == EVIDENCE_POLICY_ANALYSIS_ONLY and (section_anchors or section_analysis):
            issues.append({"rule": "analysis_section_has_anchor", "section_id": section_id})
        if len(section_analysis) > 1:
            issues.append({"rule": "section_analysis_support_limit", "section_id": section_id})
        if any(item.get("evidence_id") in {anchor.get("evidence_id") for anchor in anchors} for item in section_analysis):
            issues.append({"rule": "analysis_became_anchor", "section_id": section_id})
        if not {item.get("evidence_id") for item in section_anchors + section_analysis} <= selected_ids:
            issues.append({"rule": "classification_not_selected", "section_id": section_id})
        for item in section_analysis:
            source_item = by_id.get(str(item.get("evidence_id") or ""), item)
            if section and not section_evidence_match(source_item, section)["matched"]:
                analysis_mismatch_count += 1
                issues.append({"rule": "analysis_section_mismatch", "evidence_id": item.get("evidence_id")})
    dedup_report = fact_anchor_pack.get("source_span_dedup_report") or {}
    if dedup_report and not dedup_report.get("passed"):
        issues.append({"rule": "source_span_dedup", "report": dedup_report})
    outbound = anchors if fact_anchor_outbound is None else fact_anchor_outbound
    if outbound != anchors:
        issues.append({"rule": "fact_anchor_outbound_mismatch"})
    return {
        "flow3_v33_status": "PASS" if not issues else "BLOCKED_INVALID_FACT_ANCHORS",
        "passed": not issues,
        "hard_issue_count": len(issues),
        "hard_issues": issues,
        "fact_anchorable_count": len(anchors),
        "analysis_support_only_count": len(analysis),
        "section_anchor_counts": {
            section_id: sum(item.get("section_id") == section_id for item in anchors)
            for section_id in selected_by_section
        },
        "incomplete_anchor_count": incomplete_count,
        "section_goal_mismatch_count": section_mismatch_count,
        "analysis_support_mismatch_count": analysis_mismatch_count,
        "source_span_cross_section_conflict_count": int(dedup_report.get("source_span_cross_section_conflict_count") or 0),
        "source_span_multi_core_conflict_count": int(dedup_report.get("source_span_multi_core_conflict_count") or 0),
        "outbound_matches_local": outbound == anchors,
    }


def build_outbound_evidence_record(item: dict[str, Any]) -> dict[str, Any]:
    exact_values = {
        "numbers": list(item.get("quantities") or []),
        "time_or_frequency": list(item.get("times") or []),
        "organizations": list(item.get("departments") or []),
        "places": list(item.get("places") or []),
        "results": list(item.get("results") or []),
    }
    subject = str(item.get("canonical_subject") or item.get("subject") or "")
    action = str(item.get("canonical_action") or item.get("action") or "")
    obj = str(item.get("canonical_object") or item.get("object") or item.get("result") or "")
    category = str(item.get("evidence_type") or "")
    return {
        "evidence_id": str(item.get("evidence_id") or ""),
        "category": category,
        "canonical_subject": subject,
        "canonical_action": action,
        "canonical_object": obj,
        **_semantic_state_fields_for_propagation(item),
        "exact_values": {key: value for key, value in exact_values.items() if value},
        "writing_support": _evidence_writing_support(
            category=category,
            subject=subject,
            action=action,
            obj=obj,
            exact_values=exact_values,
        ),
    }


def _evidence_writing_support(
    *, category: str, subject: str, action: str, obj: str, exact_values: dict[str, list[Any]],
) -> dict[str, Any]:
    """Paragraph guidance derived only from already exposed evidence fields."""
    detail_types = [key for key, value in exact_values.items() if value]
    focus_parts = [part for part in (subject, action, obj) if part]
    return {
        "source_boundary": "derived_from_atomic_fields_only",
        "fact_core": "".join(focus_parts),
        "paragraph_role": {
            "context": "background_or_problem_setup",
            "risk": "risk_constraint_and_prevention_logic",
            "action": "work_action_and_process_development",
            "mechanism": "coordination_and_operating_logic",
            "scene": "scene_based_work_arrangement",
            "result": "verified_result_summary_only",
            "expression": "official_expression_reference_only",
        }.get(category, "paragraph_support"),
        "safe_expansion_angles": [
            "explain_subject_action_object_relationship",
            "connect_this_fact_to_section_goal",
            "add_process_or_organization_logic_without_new_facts",
            "close_with_general_method_summary",
        ],
        "available_detail_types": detail_types,
        "prohibited_expansion": [
            "new_numbers",
            "new_time_or_frequency",
            "new_organizations_or_duties",
            "new_places_equipment_or_mechanisms",
            "new_results_or_effects",
        ],
    }


def _work_summary_selection_assignments(
    sections: list[dict[str, Any]],
    evidence_by_id: dict[str, dict[str, Any]],
    evidence_pack: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Assign organizational compatibility without creating fact relations."""

    fact_sections = [
        section for section in sections
        if str(section.get("evidence_policy") or EVIDENCE_POLICY_FACT_REQUIRED)
        == EVIDENCE_POLICY_FACT_REQUIRED
    ]
    assignments: dict[str, dict[str, Any]] = {}
    for evidence_id, item in evidence_by_id.items():
        writability = evidence_writability(item, evidence_pack)
        if writability == "unwritable":
            continue
        explicit_block = _compact(
            item.get("business_block") or item.get("planner_business_block")
        )
        exact_block_section_ids = {
            str(section.get("section_id") or "")
            for section in fact_sections
            if explicit_block
            and _compact(section.get("business_block")) == explicit_block
        }
        semantic_role_section_ids = {
            str(section.get("section_id") or "")
            for section in fact_sections
            if evidence_id in set(section.get("required_evidence_ids") or [])
            and str(section.get("section_role") or "") in {
                "problem_analysis", "next_step_arrangement",
            }
        }
        candidates: list[tuple[int, float, int, str, dict[str, Any]]] = []
        compatible: list[str] = []
        for section in fact_sections:
            match = section_evidence_match(item, section)
            section_id = str(section.get("section_id") or "")
            block = _compact(section.get("business_block"))
            exact_block = bool(explicit_block and block and explicit_block == block)
            if semantic_role_section_ids and section_id not in semantic_role_section_ids:
                continue
            if (
                not semantic_role_section_ids
                and exact_block_section_ids
                and section_id not in exact_block_section_ids
            ):
                continue
            base_ids = set(section.get("required_evidence_ids") or [])
            if not match["matched"] and not exact_block:
                continue
            compatible.append(section_id)
            score = int(match.get("score") or 0)
            if exact_block:
                score += 80
            if evidence_id in base_ids:
                score += 35
            candidates.append((
                score,
                float(section.get("importance") or 1.0),
                -int(section.get("priority") or 0),
                section_id,
                section,
            ))
        if not candidates:
            continue
        _score, _importance, _priority, _section_id, selected = max(candidates)
        assignments[evidence_id] = {
            "section_id": str(selected.get("section_id") or ""),
            "business_block": str(selected.get("business_block") or ""),
            "section_compatibility": sorted(set(compatible)),
            "fact_anchorable": writability == "fact_anchorable",
            "analysis_support_only": writability == "analysis_support_only",
        }
    return assignments


def _select_work_summary_evidence_once(
    task: Any,
    evidence_pack: dict[str, Any],
    base_content_plan: dict[str, Any],
) -> dict[str, Any]:
    evidence_by_id = {
        str(item.get("evidence_id") or ""): item
        for item in evidence_pack.get("evidence") or [] if item.get("evidence_id")
    }
    plan = _repair_duplicate_goals_from_focus(base_content_plan)
    sections = copy.deepcopy(list(plan.get("sections") or []))
    assignments = _work_summary_selection_assignments(sections, evidence_by_id, evidence_pack)
    work_chain_pack = build_safe_work_chain_pack(
        list(evidence_by_id.values()), assignments,
    )
    chains = list(work_chain_pack.get("chains") or [])
    chain_by_id = {str(chain.get("chain_id") or ""): chain for chain in chains}
    chains_by_member: dict[str, list[str]] = {}
    for chain in chains:
        for evidence_id in chain.get("member_evidence_ids") or []:
            chains_by_member.setdefault(str(evidence_id), []).append(str(chain.get("chain_id") or ""))

    states = [
        {"section": section, "ids": [], "chain_ids": []}
        for section in sections
    ]
    fact_states = [
        state for state in states
        if str(state["section"].get("evidence_policy") or EVIDENCE_POLICY_FACT_REQUIRED)
        == EVIDENCE_POLICY_FACT_REQUIRED
    ]
    used: set[str] = set()
    used_span_sections: dict[str, str] = {}
    used_span_ids: dict[str, list[str]] = {}

    def span_key(evidence_id: str) -> str:
        item = evidence_by_id[evidence_id]
        return str(item.get("source_span_sha256") or f"EVIDENCE:{evidence_id}")

    def can_add(state: dict[str, Any], evidence_id: str) -> bool:
        if evidence_id in used or evidence_id not in evidence_by_id:
            return False
        item = evidence_by_id[evidence_id]
        assignment = assignments.get(evidence_id, {})
        section_id = str(state["section"].get("section_id") or "")
        if section_id not in set(assignment.get("section_compatibility") or []):
            return False
        span = span_key(evidence_id)
        bound_section = used_span_sections.get(span)
        if bound_section and bound_section != section_id:
            return False
        retained = used_span_ids.get(span, [])
        if len(retained) >= 2:
            return False
        return not any(_near_duplicate(item, evidence_by_id[value]) for value in retained)

    def add_one(state: dict[str, Any], evidence_id: str) -> bool:
        if not can_add(state, evidence_id):
            return False
        state["ids"].append(evidence_id)
        used.add(evidence_id)
        span = span_key(evidence_id)
        used_span_sections[span] = str(state["section"].get("section_id") or "")
        used_span_ids.setdefault(span, []).append(evidence_id)
        return True

    def distinct_fact_spans(state: dict[str, Any]) -> set[str]:
        return {
            span_key(value)
            for value in state["ids"]
            if evidence_writability(evidence_by_id[value], evidence_pack) == "fact_anchorable"
        }

    def minimum_fact_spans(state: dict[str, Any]) -> int:
        return _minimum_fact_anchors(state["section"])

    def chain_rank(chain: dict[str, Any]) -> tuple[int, int, int, str]:
        completeness = {
            CHAIN_CLOSED_LOOP: 3,
            CHAIN_MULTI_STEP: 2,
            "PARTIAL": 1,
        }.get(str(chain.get("chain_completeness") or ""), 0)
        return (
            completeness,
            int(chain.get("source_span_count") or 0),
            int(chain.get("fact_anchorable_count") or 0),
            str(chain.get("chain_id") or ""),
        )

    def compatible_chains(state: dict[str, Any]) -> list[dict[str, Any]]:
        section = state["section"]
        section_id = str(section.get("section_id") or "")
        block = str(section.get("business_block") or "")
        candidates: list[dict[str, Any]] = []
        for chain in chains:
            if str(chain.get("business_block") or "") != block:
                continue
            members = [str(value) for value in chain.get("member_evidence_ids") or []]
            if not members or not all(
                section_id in set(assignments.get(value, {}).get("section_compatibility") or [])
                for value in members
            ):
                continue
            candidates.append(chain)
        return sorted(candidates, key=chain_rank, reverse=True)

    def add_chain(state: dict[str, Any], chain: dict[str, Any]) -> bool:
        member_ids = [
            str(value) for value in (
                chain.get("ordered_evidence_ids")
                if chain.get("order_status") == ORDER_AVAILABLE
                else chain.get("member_evidence_ids")
            ) or []
        ]
        added = [value for value in member_ids if add_one(state, value)]
        if len(added) >= 2:
            chain_id = str(chain.get("chain_id") or "")
            if chain_id not in state["chain_ids"]:
                state["chain_ids"].append(chain_id)
        return bool(added)

    def compatible_fact_candidates(state: dict[str, Any]) -> list[str]:
        section = state["section"]
        section_id = str(section.get("section_id") or "")
        base_ids = set(section.get("required_evidence_ids") or [])
        preserved_ids = set(section.get("replan_preserved_evidence_ids") or [])
        block = str(section.get("business_block") or "")
        rows: list[tuple[int, int, int, int, str]] = []
        for evidence_id, assignment in assignments.items():
            if not assignment.get("fact_anchorable") or not can_add(state, evidence_id):
                continue
            if section_id not in set(assignment.get("section_compatibility") or []):
                continue
            match = section_evidence_match(evidence_by_id[evidence_id], section)
            same_block = int(str(assignment.get("business_block") or "") == block)
            rows.append((
                int(evidence_id in preserved_ids),
                same_block,
                int(evidence_id in base_ids),
                int(match.get("score") or 0),
                evidence_id,
            ))
        return [row[-1] for row in sorted(rows, reverse=True)]

    def density_preserving_target(state: dict[str, Any]) -> int:
        section_id = str(state["section"].get("section_id") or "")
        available = sum(
            assignment.get("fact_anchorable")
            and section_id in set(assignment.get("section_compatibility") or [])
            for assignment in assignments.values()
        )
        return max(
            minimum_fact_spans(state),
            int(round(2.0 * max(1, available) ** 0.5)),
        )

    # Protect sections with the least compatible capacity first.  This prevents
    # an important-but-rich section from consuming facts needed by a sparse one.
    def available_span_count(state: dict[str, Any]) -> int:
        return len({span_key(value) for value in compatible_fact_candidates(state)})

    for state in sorted(
        fact_states,
        key=lambda value: (
            available_span_count(value),
            int(value["section"].get("priority") or 0),
        ),
    ):
        preserved_ids = set(state["section"].get("replan_preserved_evidence_ids") or [])
        for evidence_id in compatible_fact_candidates(state):
            if evidence_id in preserved_ids:
                add_one(state, evidence_id)
        for chain in compatible_chains(state):
            if len(distinct_fact_spans(state)) >= minimum_fact_spans(state):
                break
            if add_chain(state, chain):
                break
        for evidence_id in compatible_fact_candidates(state):
            if len(distinct_fact_spans(state)) >= minimum_fact_spans(state):
                break
            add_one(state, evidence_id)

    # After the safety minimum, retain every compatible independent proposition.
    # Planning owns coverage; it must not minimize the evidence set and then use
    # that self-created shortage as proof that the material is sparse.
    for state in sorted(
        fact_states,
        key=lambda value: (
            int(value["section"].get("priority") or 0),
            -float(value["section"].get("importance") or 1.0),
        ),
    ):
        remaining_chains = [
            chain for chain in compatible_chains(state)
            if str(chain.get("chain_id") or "") not in state["chain_ids"]
        ]
        for chain in remaining_chains:
            if (
                state["chain_ids"]
                and len(distinct_fact_spans(state)) >= density_preserving_target(state)
            ):
                break
            add_chain(state, chain)

        for evidence_id in compatible_fact_candidates(state):
            if len(distinct_fact_spans(state)) >= density_preserving_target(state):
                break
            add_one(state, evidence_id)

        analysis_candidates = [
            evidence_id for evidence_id, assignment in assignments.items()
            if assignment.get("analysis_support_only") and can_add(state, evidence_id)
        ]
        if analysis_candidates:
            preserved_ids = set(state["section"].get("replan_preserved_evidence_ids") or [])
            analysis_candidates.sort(
                key=lambda value: (
                    -int(value in preserved_ids),
                    -int(section_evidence_match(evidence_by_id[value], state["section"]).get("score") or 0),
                    value,
                )
            )
            add_one(state, analysis_candidates[0])

    selected_ids: list[str] = []
    rebound_sections: list[dict[str, Any]] = []
    for state in states:
        section = state["section"]
        ids = list(state["ids"])
        core_ids: list[str] = []
        core_spans: set[str] = set()
        for evidence_id in ids:
            if evidence_writability(evidence_by_id[evidence_id], evidence_pack) != "fact_anchorable":
                continue
            span = span_key(evidence_id)
            if span in core_spans:
                continue
            core_ids.append(evidence_id)
            core_spans.add(span)
        supporting_ids = [value for value in ids if value not in core_ids]
        selected_items = [evidence_by_id[value] for value in ids]
        selected_chain_summaries: list[dict[str, Any]] = []
        ordered_chain_ids: list[str] = []
        for chain_id in state["chain_ids"]:
            chain = chain_by_id[chain_id]
            selected_members = [
                str(value) for value in chain.get("member_evidence_ids") or [] if str(value) in ids
            ]
            if len(selected_members) < 2:
                continue
            summary = model_visible_chain_summary(chain)
            summary["member_evidence_ids"] = selected_members
            summary["ordered_evidence_ids"] = [
                str(value) for value in chain.get("ordered_evidence_ids") or [] if str(value) in ids
            ]
            selected_chain_summaries.append(summary)
            ordered_chain_ids.extend(summary["ordered_evidence_ids"])
        section["base_required_evidence_ids"] = [
            value for value in section.get("required_evidence_ids") or [] if value in ids
        ]
        section["core_evidence_ids"] = core_ids
        section["supporting_evidence_ids"] = supporting_ids
        section["selected_evidence_ids"] = ids
        section["work_chain_ids"] = [value["chain_id"] for value in selected_chain_summaries]
        section["ordered_chain_evidence_ids"] = ordered_chain_ids
        section["work_chain_summaries"] = selected_chain_summaries
        section["allowed_fact_subjects"] = list(dict.fromkeys(
            str(item.get("canonical_subject") or item.get("subject") or "")
            for item in selected_items if item.get("canonical_subject") or item.get("subject")
        ))
        section["allowed_fact_actions"] = list(dict.fromkeys(
            str(item.get("canonical_action") or item.get("action") or "")
            for item in selected_items if item.get("canonical_action") or item.get("action")
        ))
        _refresh_section_quality_fields(section, evidence_by_id)
        rebound_sections.append(section)
        selected_ids.extend(ids)

    rebound_plan = copy.deepcopy(plan)
    rebound_plan["sections"] = rebound_sections
    rebound_plan["recommended_length_range"] = derive_target_length_range(
        int(getattr(task, "target_words", 0) or 0)
    )
    rebound_plan["evidence_selection_applied_after_base_plan"] = True
    rebound_plan["uses_work_chain_grouping"] = True
    rebound_plan["creates_facts"] = False
    rebound_plan["work_chain_summaries"] = [
        model_visible_chain_summary(chain) for chain in chains
    ]
    capacity_budget = build_evidence_capacity_budget(
        rebound_plan,
        list(evidence_by_id.values()),
        work_chain_pack,
        requested_target_words=int(getattr(task, "target_words", 0) or 0),
    )
    capacity_by_section = {
        str(row.get("section_id") or ""): row
        for row in capacity_budget.get("section_capacity") or []
    }
    for section in rebound_sections:
        capacity = capacity_by_section.get(str(section.get("section_id") or ""), {})
        for field in (
            "capacity_status", "available_fact_anchorable", "distinct_fact_source_spans",
            "available_work_chains", "chain_member_count", "supporting_evidence_count",
            "analysis_support_only_count", "requested_word_budget",
            "recommended_relative_budget", "recommended_capacity_aware_word_budget",
        ):
            if field in capacity:
                section[field] = capacity[field]
    rebound_plan["evidence_capacity_budget"] = capacity_budget
    rebound_plan["requested_target_words"] = capacity_budget["requested_target_words"]
    rebound_plan["evidence_supported_words"] = capacity_budget["evidence_supported_words"]
    rebound_plan["capacity_gap"] = capacity_budget["capacity_gap"]

    selected_local = [
        {
            **evidence_by_id[evidence_id],
            "writability": evidence_writability(evidence_by_id[evidence_id], evidence_pack),
            "section_match": section_evidence_match(
                evidence_by_id[evidence_id],
                next(section for section in rebound_sections if evidence_id in section.get("selected_evidence_ids", [])),
            ),
        }
        for evidence_id in selected_ids
    ]
    fact_section_count = len(fact_states)
    minimum_fact_total = sum(_minimum_fact_anchors(state["section"]) for state in fact_states)
    available_count = sum(
        len(row.get("fact_anchorable_evidence_ids") or [])
        + min(1, len(row.get("analysis_support_only_evidence_ids") or []))
        for row in work_chain_pack.get("section_evidence_map") or []
    )
    return {
        "schema_version": "flow3-v3.3-evidence-selection-1",
        "base_plan_id": base_content_plan.get("plan_id"),
        "selection_applied_after_base_plan": True,
        "section_count": len(states),
        "minimum_total": minimum_fact_total,
        "maximum_total": max(len(selected_ids), available_count),
        "selected_evidence_ids": selected_ids,
        "selected_evidence_count": len(selected_ids),
        "selected_evidence_local": selected_local,
        "selected_evidence_outbound": [
            build_outbound_evidence_record(evidence_by_id[value]) for value in selected_ids
        ],
        "content_plan": rebound_plan,
        "work_chain_pack": work_chain_pack,
        "capacity_budget": capacity_budget,
        "recommended_length_range": rebound_plan["recommended_length_range"],
        "uses_source_title": False,
        "uses_source_heading": False,
        "uses_source_order": any(
            chain.get("order_status") == ORDER_AVAILABLE for chain in chains
        ),
    }


def _select_work_summary_evidence(
    task: Any,
    evidence_pack: dict[str, Any],
    base_content_plan: dict[str, Any],
) -> dict[str, Any]:
    """Probe local capacity, replan once when safe, then return final selection."""

    probe = _select_work_summary_evidence_once(task, evidence_pack, base_content_plan)
    probe_sections = list((probe.get("content_plan") or {}).get("sections") or [])
    by_id = {
        str(item.get("evidence_id") or ""): item
        for item in evidence_pack.get("evidence") or []
        if item.get("evidence_id")
    }

    def span_id(evidence_id: str) -> str:
        item = by_id.get(evidence_id, {})
        return str(item.get("source_span_sha256") or f"EVIDENCE:{evidence_id}")

    def core_span_count(section: dict[str, Any]) -> int:
        return len({
            span_id(str(evidence_id))
            for evidence_id in section.get("core_evidence_ids") or []
            if evidence_writability(by_id.get(str(evidence_id), {}), evidence_pack)
            == "fact_anchorable"
        })

    insufficient = [
        str(section.get("section_id") or "")
        for section in probe_sections
        if str(section.get("evidence_policy") or EVIDENCE_POLICY_FACT_REQUIRED)
        == EVIDENCE_POLICY_FACT_REQUIRED
        and core_span_count(section) < _minimum_fact_anchors(section)
    ]
    preserved_by_section = {
        section_id: [
            str(evidence_id)
            for section in probe_sections
            if str(section.get("section_id") or "") == section_id
            for evidence_id in section.get("selected_evidence_ids") or []
            if evidence_writability(by_id.get(str(evidence_id), {}), evidence_pack) != "unwritable"
        ]
        for section_id in insufficient
    }

    safe_candidates: dict[str, list[dict[str, Any]]] = {}
    insufficient_set = set(insufficient)
    adequate_sections = [
        section for section in probe_sections
        if str(section.get("evidence_policy") or EVIDENCE_POLICY_FACT_REQUIRED)
        == EVIDENCE_POLICY_FACT_REQUIRED
        and str(section.get("section_id") or "") not in insufficient_set
    ]

    def topic_text(section: dict[str, Any]) -> str:
        return str(section.get("business_block") or "")

    def same_source_chunk(left_id: str, right_id: str) -> bool:
        left, right = by_id.get(left_id, {}), by_id.get(right_id, {})
        return bool(
            left.get("document_id")
            and left.get("document_id") == right.get("document_id")
            and left.get("chunk_id")
            and left.get("chunk_id") == right.get("chunk_id")
        )

    def shares_topic_family(left_id: str, right_id: str) -> bool:
        left_text = _selection_visible(by_id.get(left_id, {}))
        right_text = _selection_visible(by_id.get(right_id, {}))
        return any(
            any(marker in left_text for marker in family)
            and any(marker in right_text for marker in family)
            for family in _SECTION_REPLAN_TOPIC_FAMILIES
        )

    for weak_id in insufficient:
        weak = next(
            (section for section in probe_sections if str(section.get("section_id") or "") == weak_id),
            {},
        )
        weak_evidence_ids = preserved_by_section.get(weak_id) or []
        weak_grams = _semantic_ngrams(topic_text(weak))
        candidates: list[dict[str, Any]] = []
        for target in adequate_sections:
            target_id = str(target.get("section_id") or "")
            target_evidence_ids = [str(value) for value in target.get("selected_evidence_ids") or []]
            target_grams = _semantic_ngrams(topic_text(target))
            topic_overlap = len(weak_grams & target_grams)
            topic_ratio = topic_overlap / max(1, min(len(weak_grams), len(target_grams)))
            semantic_bridge = topic_overlap >= 2 and topic_ratio >= 0.25
            provenance_bridge = any(
                same_source_chunk(left_id, right_id)
                and shares_topic_family(left_id, right_id)
                for left_id in weak_evidence_ids
                for right_id in target_evidence_ids
            )
            if not semantic_bridge and not provenance_bridge:
                continue
            candidates.append({
                "target_section_id": target_id,
                "compatibility_score": topic_overlap * 10 + (40 if provenance_bridge else 0),
                "target_priority": int(target.get("priority") or 0),
                "compatibility_reasons": [
                    *( ["semantic_topic_overlap"] if semantic_bridge else [] ),
                    *( ["same_source_chunk_bridge"] if provenance_bridge else [] ),
                ],
            })
        safe_candidates[weak_id] = candidates

    replanned_plan, metadata = replan_work_summary_sections(
        base_content_plan,
        insufficient_section_ids=insufficient,
        safe_merge_candidates=safe_candidates,
        preserved_evidence_ids=preserved_by_section,
    )
    if not metadata.get("replan_applied"):
        probe["replan_metadata"] = metadata
        probe["content_plan"]["work_summary_section_replan"] = copy.deepcopy(metadata)
        return probe

    final = _select_work_summary_evidence_once(task, evidence_pack, replanned_plan)
    initial_ids = set(probe.get("selected_evidence_ids") or [])
    final_ids = set(final.get("selected_evidence_ids") or [])
    must_preserve = {
        evidence_id
        for values in preserved_by_section.values()
        for evidence_id in values
    }
    if not must_preserve <= final_ids:
        metadata.update({
            "replan_applied": False,
            "replan_reason": "PRESERVATION_FAILED",
            "post_replan_section_count": metadata["pre_replan_section_count"],
            "merged_section_mapping": {},
            "preserved_evidence_ids": sorted(must_preserve & final_ids),
            "dropped_evidence_ids": sorted(must_preserve - final_ids),
            "reselection_performed": True,
        })
        probe["replan_metadata"] = metadata
        probe["content_plan"]["work_summary_section_replan"] = copy.deepcopy(metadata)
        return probe

    metadata.update({
        "preserved_evidence_ids": sorted(initial_ids & final_ids),
        "dropped_evidence_ids": sorted(initial_ids - final_ids),
        "reselection_performed": True,
    })
    final["replan_metadata"] = metadata
    final["content_plan"]["work_summary_section_replan"] = copy.deepcopy(metadata)
    return final


def select_evidence_for_content_plan(
    task: Any, evidence_pack: dict[str, Any], base_content_plan: dict[str, Any],
) -> dict[str, Any]:
    """Select directly matching, complete evidence under document-specific policy."""
    if base_content_plan.get("planner_owner") == "work_summary_planner_v1":
        return _select_work_summary_evidence(task, evidence_pack, base_content_plan)
    evidence_by_id = {
        str(item.get("evidence_id") or ""): item
        for item in evidence_pack.get("evidence") or [] if item.get("evidence_id")
    }
    goal_repaired_plan = _repair_duplicate_goals_from_focus(base_content_plan)
    sections = copy.deepcopy(list(goal_repaired_plan.get("sections") or []))
    used: set[str] = set()
    used_spans: dict[str, list[str]] = {}
    all_ids = sorted(evidence_by_id)
    section_states = [{"section": section, "ids": []} for section in sections]
    fact_section_states = [
        state for state in section_states
        if str(state["section"].get("evidence_policy") or EVIDENCE_POLICY_FACT_REQUIRED)
        == EVIDENCE_POLICY_FACT_REQUIRED
    ]

    def span_key(item: dict[str, Any]) -> str:
        return str(item.get("source_span_sha256") or f"EVIDENCE:{item.get('evidence_id')}")

    def span_available(item: dict[str, Any], state: dict[str, Any]) -> bool:
        retained_ids = used_spans.get(span_key(item), [])
        if not retained_ids:
            return True
        section_id = str(state["section"].get("section_id") or "")
        retained_sections = {
            str(section_state["section"].get("section_id") or "")
            for section_state in section_states
            if any(evidence_id in section_state["ids"] for evidence_id in retained_ids)
        }
        if retained_sections and retained_sections != {section_id}:
            return False
        if len(retained_ids) >= 2:
            return False
        return not any(_near_duplicate(item, evidence_by_id[evidence_id]) for evidence_id in retained_ids)

    def fact_core_spans(state: dict[str, Any]) -> set[str]:
        spans: set[str] = set()
        for evidence_id in state["ids"]:
            item = evidence_by_id[evidence_id]
            if evidence_writability(item, evidence_pack) == "fact_anchorable":
                spans.add(span_key(item))
        return spans

    def preferred_for_section(item: dict[str, Any], current_match: dict[str, Any]) -> bool:
        if evidence_writability(item, evidence_pack) != "fact_anchorable":
            return True
        scores = [
            section_evidence_match(item, candidate_section)["score"]
            for candidate_section in sections
            if section_evidence_match(item, candidate_section)["matched"]
        ]
        return current_match["score"] + 7 >= max(scores, default=current_match["score"])

    def candidates_for(state: dict[str, Any], writability: str | None) -> list[tuple[int, str]]:
        section = state["section"]
        bound = [evidence_by_id[evidence_id] for evidence_id in state["ids"]]
        candidates: list[tuple[int, str]] = []
        for evidence_id in all_ids:
            if evidence_id in used:
                continue
            item = evidence_by_id[evidence_id]
            writable = evidence_writability(item, evidence_pack)
            if writable == "unwritable" or (writability and writable != writability):
                continue
            if (
                writable == "fact_anchorable"
                and len(fact_core_spans(state)) < 2
                and span_key(item) in fact_core_spans(state)
            ):
                continue
            if writable == "analysis_support_only" and any(
                evidence_writability(existing, evidence_pack) == "analysis_support_only" for existing in bound
            ):
                continue
            if any(_near_duplicate(item, existing) for existing in bound):
                continue
            match = section_evidence_match(item, section)
            if match["matched"] and span_available(item, state) and preferred_for_section(item, match):
                quality = 8 if writable == "fact_anchorable" else 0
                candidates.append((match["score"] + quality, evidence_id))
        return sorted(candidates, key=lambda pair: (-pair[0], pair[1]))

    def choose(state: dict[str, Any], writability: str | None) -> str | None:
        candidates = candidates_for(state, writability)
        if not candidates:
            return None
        selected_id = candidates[0][1]
        state["ids"].append(selected_id)
        used.add(selected_id)
        used_spans.setdefault(span_key(evidence_by_id[selected_id]), []).append(selected_id)
        return selected_id

    for state in fact_section_states:
        while len(fact_core_spans(state)) < 2 and len(state["ids"]) < 5:
            if choose(state, "fact_anchorable") is None:
                break
        if len(state["ids"]) < 5:
            choose(state, "analysis_support_only")
        while len(state["ids"]) < 3:
            if choose(state, "fact_anchorable") is None:
                break

    fact_section_count = len(fact_section_states)
    minimum_total = max(10, 3 * fact_section_count)
    maximum_total = min(15, 5 * fact_section_count)
    while len(used) < minimum_total:
        progress = False
        for state in sorted(fact_section_states, key=lambda value: (len(value["ids"]), value["section"].get("section_id", ""))):
            if len(state["ids"]) < 5 and choose(state, "fact_anchorable"):
                progress = True
                break
        if not progress:
            break

    selected_ids: list[str] = []
    rebound_sections: list[dict[str, Any]] = []
    for state in section_states:
        section = state["section"]
        ids = list(state["ids"])
        core_ids: list[str] = []
        core_spans: set[str] = set()
        for evidence_id in ids:
            item = evidence_by_id[evidence_id]
            if evidence_writability(item, evidence_pack) != "fact_anchorable":
                continue
            span = span_key(item)
            if span in core_spans:
                continue
            core_ids.append(evidence_id)
            core_spans.add(span)
            if len(core_ids) >= 2:
                break
        supporting_ids = [evidence_id for evidence_id in ids if evidence_id not in core_ids]
        selected_items = [evidence_by_id[evidence_id] for evidence_id in ids]
        section["base_required_evidence_ids"] = [
            evidence_id for evidence_id in section.get("required_evidence_ids") or [] if evidence_id in ids
        ]
        section["core_evidence_ids"] = core_ids
        section["supporting_evidence_ids"] = supporting_ids
        section["selected_evidence_ids"] = ids
        section["allowed_fact_subjects"] = list(dict.fromkeys(
            str(item.get("canonical_subject") or item.get("subject") or "") for item in selected_items
            if item.get("canonical_subject") or item.get("subject")
        ))
        section["allowed_fact_actions"] = list(dict.fromkeys(
            str(item.get("canonical_action") or item.get("action") or "") for item in selected_items
            if item.get("canonical_action") or item.get("action")
        ))
        _refresh_section_quality_fields(section, evidence_by_id)
        rebound_sections.append(section)
        selected_ids.extend(ids)

    selected_count = len(selected_ids)
    length_range = derive_target_length_range(int(getattr(task, "target_words", 0) or 0))

    rebound_plan = copy.deepcopy(goal_repaired_plan)
    rebound_plan["sections"] = rebound_sections
    rebound_plan["recommended_length_range"] = length_range
    rebound_plan["evidence_selection_applied_after_base_plan"] = True
    selected_local = [
        {
            **evidence_by_id[evidence_id],
            "writability": evidence_writability(evidence_by_id[evidence_id], evidence_pack),
            "section_match": section_evidence_match(
                evidence_by_id[evidence_id],
                next(section for section in rebound_sections if evidence_id in section.get("selected_evidence_ids", [])),
            ),
        }
        for evidence_id in selected_ids
    ]
    outbound = [
        build_outbound_evidence_record(evidence_by_id[evidence_id])
        for evidence_id in selected_ids
    ]
    return {
        "schema_version": "flow3-v3.3-evidence-selection-1",
        "base_plan_id": base_content_plan.get("plan_id"),
        "selection_applied_after_base_plan": True,
        "section_count": len(section_states),
        "minimum_total": minimum_total,
        "maximum_total": maximum_total,
        "selected_evidence_ids": selected_ids,
        "selected_evidence_count": selected_count,
        "selected_evidence_local": selected_local,
        "selected_evidence_outbound": outbound,
        "content_plan": rebound_plan,
        "recommended_length_range": length_range,
        "uses_source_title": False,
        "uses_source_heading": False,
        "uses_source_order": False,
    }


def validate_evidence_selection(selection: dict[str, Any], evidence_pack: dict[str, Any]) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    all_ids = {str(item.get("evidence_id") or "") for item in evidence_pack.get("evidence") or []}
    selected_ids = list(selection.get("selected_evidence_ids") or [])
    selected_set = set(selected_ids)
    sections = list((selection.get("content_plan") or {}).get("sections") or [])
    dynamic_work_summary = (
        (selection.get("content_plan") or {}).get("planner_owner")
        == "work_summary_planner_v1"
    )
    minimum_total = int(selection.get("minimum_total") or 0)
    maximum_total = int(selection.get("maximum_total") or 0)
    if not selection.get("selection_applied_after_base_plan") or not selection.get("base_plan_id"):
        issues.append({"rule": "selection_order"})
    if len(selected_ids) != len(selected_set):
        issues.append({"rule": "duplicate_binding"})
    if not selected_set <= all_ids:
        issues.append({"rule": "unknown_evidence"})
    if len(selected_ids) < minimum_total:
        issues.append({
            "rule": "insufficient_factual_capacity",
            "actual": len(selected_ids),
            "range": [minimum_total, maximum_total],
        })
    elif not dynamic_work_summary and len(selected_ids) > maximum_total:
        issues.append({"rule": "selected_total", "actual": len(selected_ids), "range": [minimum_total, maximum_total]})
    if selected_set == all_ids and not dynamic_work_summary:
        issues.append({"rule": "all_evidence_selected"})
    flattened: list[str] = []
    by_id = {str(item.get("evidence_id") or ""): item for item in evidence_pack.get("evidence") or []}
    span_sections: dict[str, set[str]] = {}
    section_mismatch_count = 0
    analysis_mismatch_count = 0
    for section in sections:
        section_id = str(section.get("section_id") or "")
        evidence_policy = str(section.get("evidence_policy") or EVIDENCE_POLICY_FACT_REQUIRED)
        ids = list(section.get("selected_evidence_ids") or [])
        core = list(section.get("core_evidence_ids") or [])
        if (
            evidence_policy == EVIDENCE_POLICY_FACT_REQUIRED
            and not dynamic_work_summary
            and not 3 <= len(ids) <= 5
        ):
            issues.append({"rule": "section_binding_count", "section_id": section.get("section_id"), "actual": len(ids)})
        core_spans = {
            str(by_id.get(value, {}).get("source_span_sha256") or f"EVIDENCE:{value}")
            for value in core
        }
        minimum_core = _minimum_fact_anchors(section)
        if evidence_policy == EVIDENCE_POLICY_FACT_REQUIRED and (
            len(core) < minimum_core
            or any(evidence_writability(by_id.get(value, {}), evidence_pack) != "fact_anchorable" for value in core)
            or len(core_spans) < minimum_core
        ):
            issues.append({"rule": "section_core_fact_count", "section_id": section.get("section_id")})
        if evidence_policy == EVIDENCE_POLICY_ANALYSIS_ONLY and (ids or core):
            issues.append({"rule": "analysis_section_has_evidence", "section_id": section.get("section_id")})
        if sum(evidence_writability(by_id.get(value, {}), evidence_pack) == "analysis_support_only" for value in ids) > 1:
            issues.append({"rule": "section_analysis_support_limit", "section_id": section.get("section_id")})
        for evidence_id in ids:
            item = by_id.get(evidence_id, {})
            writable = evidence_writability(item, evidence_pack)
            if writable == "unwritable":
                issues.append({"rule": "incomplete_selected_evidence", "section_id": section_id, "evidence_id": evidence_id})
            matched = section_evidence_match(item, section)["matched"]
            if not matched:
                if writable == "analysis_support_only":
                    analysis_mismatch_count += 1
                    issues.append({"rule": "analysis_section_mismatch", "section_id": section_id, "evidence_id": evidence_id})
                else:
                    section_mismatch_count += 1
                    issues.append({"rule": "fact_section_mismatch", "section_id": section_id, "evidence_id": evidence_id})
            span = str(item.get("source_span_sha256") or f"EVIDENCE:{evidence_id}")
            span_sections.setdefault(span, set()).add(section_id)
        flattened.extend(ids)
    cross_section_spans = sorted(span for span, bound_sections in span_sections.items() if len(bound_sections) > 1)
    if cross_section_spans:
        issues.append({"rule": "source_span_cross_section", "source_spans": cross_section_spans})
    if flattened != selected_ids:
        issues.append({"rule": "binding_sum_mismatch"})
    outbound = list(selection.get("selected_evidence_outbound") or [])
    if {item.get("evidence_id") for item in outbound} != selected_set or len(outbound) != len(selected_ids):
        issues.append({"rule": "outbound_identity_mismatch"})
    allowed_fields = {
        "evidence_id", "category", "canonical_subject",
        "canonical_action", "canonical_object", "exact_values", "writing_support",
        "semantic_state_contract_version", "semantic_state", "state_evidence",
        "state_qualifier", "negation_markers", "modality_markers", "completion_markers",
    }
    for item in outbound:
        if set(item) - allowed_fields:
            issues.append({"rule": "outbound_forbidden_field", "fields": sorted(set(item) - allowed_fields)})
        support = item.get("writing_support") or {}
        if not isinstance(support, dict):
            issues.append({"rule": "outbound_writing_support_shape", "evidence_id": item.get("evidence_id")})
        elif support.get("source_boundary") != "derived_from_atomic_fields_only":
            issues.append({"rule": "outbound_writing_support_boundary", "evidence_id": item.get("evidence_id")})
    if dynamic_work_summary:
        chain_pack = selection.get("work_chain_pack") or {}
        if chain_pack.get("creates_facts") is not False:
            issues.append({"rule": "work_chain_must_not_create_facts"})
        analysis_ids = set(chain_pack.get("analysis_support_only_evidence_ids") or [])
        for chain in chain_pack.get("chains") or []:
            member_ids = set(chain.get("member_evidence_ids") or [])
            ordered_ids = set(chain.get("ordered_evidence_ids") or [])
            if chain.get("creates_facts") is not False:
                issues.append({"rule": "work_chain_must_not_create_facts", "chain_id": chain.get("chain_id")})
            if not member_ids <= all_ids or not ordered_ids <= member_ids:
                issues.append({"rule": "work_chain_unknown_evidence", "chain_id": chain.get("chain_id")})
            if member_ids & analysis_ids:
                issues.append({"rule": "analysis_support_in_fact_chain", "chain_id": chain.get("chain_id")})
    warning_rules = {
        "insufficient_factual_capacity",
        "section_binding_count",
    }
    warnings = [issue for issue in issues if issue.get("rule") in warning_rules]
    hard_issues = [issue for issue in issues if issue.get("rule") not in warning_rules]
    return {
        "flow3_v32_status": "PASS" if not hard_issues else "BLOCKED_INVALID_EVIDENCE_SELECTION",
        "passed": not hard_issues,
        "hard_issue_count": len(hard_issues),
        "hard_issues": hard_issues,
        "warning_count": len(warnings),
        "warnings": warnings,
        "selected_evidence_count": len(selected_ids),
        "minimum_total": minimum_total,
        "maximum_total": maximum_total,
        "section_binding_counts": {
            section.get("section_id"): len(section.get("selected_evidence_ids") or []) for section in sections
        },
        "selection_applied_after_base_plan": bool(selection.get("selection_applied_after_base_plan")),
        "all_evidence_sent": selected_set == all_ids,
        "uses_source_order": bool(selection.get("uses_source_order")),
        "section_goal_mismatch_count": section_mismatch_count,
        "analysis_support_mismatch_count": analysis_mismatch_count,
        "source_span_cross_section_conflict_count": len(cross_section_spans),
    }


def isolate_fact_section_mismatches(
    selection: dict[str, Any],
    evidence_pack: dict[str, Any],
    validation_report: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Remove mismatched section bindings once without reallocating evidence.

    The Atomic Evidence records remain untouched.  Recovery is deliberately
    limited to the exact ``(section_id, evidence_id)`` bindings reported as
    ``fact_section_mismatch`` and cannot run a second time on the same
    selection.  The existing selection validator remains the sole policy for
    all minimum, identity, coverage, and other hard rules.
    """
    initial_report = copy.deepcopy(
        validation_report or validate_evidence_selection(selection, evidence_pack)
    )
    existing_recovery = selection.get("local_selection_recovery") or {}
    if existing_recovery.get("attempted"):
        report = validate_evidence_selection(selection, evidence_pack)
        report["local_selection_recovery"] = copy.deepcopy(existing_recovery)
        return selection, report

    mismatches = [
        issue
        for issue in initial_report.get("hard_issues") or []
        if issue.get("rule") == "fact_section_mismatch"
    ]
    if not mismatches:
        return selection, initial_report

    recovered = copy.deepcopy(selection)
    mismatch_bindings = {
        (
            str(issue.get("section_id") or ""),
            str(issue.get("evidence_id") or ""),
        )
        for issue in mismatches
    }
    evidence_by_id = {
        str(item.get("evidence_id") or ""): item
        for item in evidence_pack.get("evidence") or []
    }
    isolated_ids: list[str] = []
    recovered_sections: list[dict[str, Any]] = []
    for section in (recovered.get("content_plan") or {}).get("sections") or []:
        section_id = str(section.get("section_id") or "")
        removed_here = {
            evidence_id
            for bound_section_id, evidence_id in mismatch_bindings
            if bound_section_id == section_id
        }
        if removed_here:
            isolated_ids.extend(
                evidence_id
                for evidence_id in section.get("selected_evidence_ids") or []
                if str(evidence_id) in removed_here
            )
            for field in (
                "selected_evidence_ids",
                "core_evidence_ids",
                "supporting_evidence_ids",
                "base_required_evidence_ids",
            ):
                if field in section:
                    section[field] = [
                        evidence_id for evidence_id in section.get(field) or []
                        if str(evidence_id) not in removed_here
                    ]
            selected_items = [
                evidence_by_id[evidence_id]
                for evidence_id in section.get("selected_evidence_ids") or []
                if evidence_id in evidence_by_id
            ]
            section["allowed_fact_subjects"] = list(dict.fromkeys(
                str(item.get("canonical_subject") or item.get("subject") or "")
                for item in selected_items
                if item.get("canonical_subject") or item.get("subject")
            ))
            section["allowed_fact_actions"] = list(dict.fromkeys(
                str(item.get("canonical_action") or item.get("action") or "")
                for item in selected_items
                if item.get("canonical_action") or item.get("action")
            ))
            _refresh_section_quality_fields(section, evidence_by_id)
        recovered_sections.append(section)

    content_plan = recovered.get("content_plan") or {}
    content_plan["sections"] = recovered_sections
    recovered["content_plan"] = content_plan
    selected_ids = [
        str(evidence_id)
        for section in recovered_sections
        for evidence_id in section.get("selected_evidence_ids") or []
    ]
    selected_set = set(selected_ids)
    recovered["selected_evidence_ids"] = selected_ids
    recovered["selected_evidence_count"] = len(selected_ids)
    recovered["selected_evidence_local"] = [
        item for item in recovered.get("selected_evidence_local") or []
        if str(item.get("evidence_id") or "") in selected_set
    ]
    recovered["selected_evidence_outbound"] = [
        item for item in recovered.get("selected_evidence_outbound") or []
        if str(item.get("evidence_id") or "") in selected_set
    ]
    recovery = {
        "attempted": True,
        "applied": bool(isolated_ids),
        "recovery_round_count": 1,
        "max_recovery_rounds": 1,
        "isolation_reason": "fact_section_mismatch",
        "isolated_binding_count": len(mismatch_bindings),
        "isolated_evidence_ids": list(dict.fromkeys(isolated_ids)),
        "isolated_bindings": [
            {"section_id": section_id, "evidence_id": evidence_id}
            for section_id, evidence_id in sorted(mismatch_bindings)
        ],
        "reassigned_evidence_ids": [],
        "remaining_selected_evidence_count": len(selected_ids),
    }
    recovered["local_selection_recovery"] = recovery
    report = validate_evidence_selection(recovered, evidence_pack)
    report["local_selection_recovery"] = copy.deepcopy(recovery)
    return recovered, report
