"""FrozenArticlePlan -> frozen ArticleOutline production contract.

This module is a deterministic projection layer.  It never retrieves evidence,
rebuilds Fact Anchors or Work Chains, calls an LLM, or changes planning state.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from typing import Any, Iterable, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator


ARTICLE_OUTLINE_CONTRACT_VERSION = "article-outline-production-1"
OUTLINE_PROJECTION_MAP_VERSION = "outline-projection-map-production-1"
OUTLINE_STATUS_FROZEN = "FROZEN"
PROJECTION_STATUS_PASS = "PASS"
INSUFFICIENT_PLAN_FOR_ARTICLE_OUTLINE = "INSUFFICIENT_PLAN_FOR_ARTICLE_OUTLINE"
OUTLINE_PROJECTION_FAIL = "OUTLINE_PROJECTION_FAIL"
EXTERNAL_MODEL_CALLS = 0

_MACRO_ROLE_BY_SECTION_ROLE = {
    "work_progress": "BODY_WORK",
    "major_work": "BODY_WORK",
    "problem_analysis": "PROBLEM",
    "problem": "PROBLEM",
    "next_step_arrangement": "NEXT_STEP",
    "next_step": "NEXT_STEP",
}
_READABLE_ROLE = {
    "BODY_WORK": "主体工作",
    "PROBLEM": "存在问题",
    "NEXT_STEP": "下一步工作",
}
_CONTENT_FUNCTION_LABEL = {
    "major_work": "重点工作",
    "concrete_object_progress": "具体任务进展",
    "period_task_progress": "阶段任务进展",
    "verifiable_result": "可核验结果",
    "reform_innovation": "改革创新",
    "indicator_status": "指标状态",
    "unfinished_task": "未完成事项",
    "problem_analysis": "问题分析",
    "next_step_arrangement": "下一步安排",
}
_SPACE_RE = re.compile(r"\s+")
_ENUMERATION_PREFIX_RE = re.compile(
    r"^\s*(?:(?:[一二三四五六七八九十]+(?:是|、|[.．]))|"
    r"(?:（[一二三四五六七八九十]+）)|(?:\d+(?:、|[.．])))\s*"
)


class OutlineProjectionError(RuntimeError):
    """Auditable local failure with no planning fallback."""

    def __init__(self, code: str, report: Mapping[str, Any]):
        self.code = str(code)
        self.report = dict(report)
        super().__init__(f"{self.code}: {self.report.get('reason', 'projection failed')}")


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ParagraphIntent(_StrictFrozenModel):
    paragraph_intent_id: str = Field(min_length=1)
    paragraph_role: str = Field(min_length=1)
    writing_goal: str = Field(min_length=1)
    evidence_group_ids: tuple[str, ...] = ()
    must_cover: tuple[str, ...] = ()
    forbidden_claims: tuple[str, ...] = ()
    allow_general_synthesis: bool
    target_weight: float = Field(gt=0)
    source_section_ids: tuple[str, ...] = Field(min_length=1)
    source_fields: tuple[str, ...] = Field(min_length=1)
    work_chain_ids: tuple[str, ...] = ()


class OutlineNode(_StrictFrozenModel):
    outline_node_id: str = Field(min_length=1)
    parent_outline_node_id: str | None
    level: int = Field(ge=1)
    order: int = Field(ge=1)
    macro_role: str = Field(min_length=1)
    source_section_ids: tuple[str, ...] = Field(min_length=1)
    heading_intent: str = Field(min_length=1)
    heading_text: str = Field(min_length=1)
    paragraph_intents: tuple[ParagraphIntent, ...]
    evidence_group_ids: tuple[str, ...]
    work_chain_ids: tuple[str, ...]
    weight: float = Field(gt=0)
    required: bool = True


class NodeProjectionMapping(_StrictFrozenModel):
    outline_node_id: str
    source_section_ids: tuple[str, ...]
    parent_outline_node_id: str | None
    outline_level: int
    outline_order: int
    macro_role: str
    heading_intent: str
    paragraph_intent_ids: tuple[str, ...]
    evidence_group_ids: tuple[str, ...]
    work_chain_ids: tuple[str, ...]
    mapping_type: Literal["1:1", "N:1", "1:N", "1:N_CONTAINER"]
    mapping_reason: str


class EvidenceOwnership(_StrictFrozenModel):
    evidence_group_id: str
    source_section_id: str
    primary_outline_node_id: str
    primary_paragraph_intent_id: str
    semantic_state: str


class OutlineProjectionMap(_StrictFrozenModel):
    version: Literal["outline-projection-map-production-1"]
    source_plan_sha: str = Field(min_length=64, max_length=64)
    source_evidence_group_pack_sha: str = Field(min_length=64, max_length=64)
    outline_id: str
    node_mappings: tuple[NodeProjectionMapping, ...]
    evidence_ownership: tuple[EvidenceOwnership, ...]
    unassigned_evidence_groups: tuple[str, ...] = ()
    duplicate_evidence_ownership: tuple[str, ...] = ()
    cross_role_assignments: tuple[dict[str, Any], ...] = ()
    mapping_loss: tuple[dict[str, Any], ...] = ()
    projection_status: Literal["PASS"]
    semantic_state_transformations: int = Field(ge=0)
    external_model_calls: Literal[0] = 0
    projection_map_sha: str = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def validate_projection_hash(self) -> "OutlineProjectionMap":
        expected = stable_sha(self.model_dump(mode="json", exclude={"projection_map_sha"}))
        if self.projection_map_sha != expected:
            raise ValueError("projection_map_sha mismatch")
        return self


class ArticleOutline(_StrictFrozenModel):
    version: Literal["article-outline-production-1"]
    outline_id: str
    source_plan_id: str
    source_plan_sha: str = Field(min_length=64, max_length=64)
    source_evidence_group_pack_sha: str = Field(min_length=64, max_length=64)
    planning_owner: Literal["planning_engine_v1"]
    planner_trace_label: str
    document_type: str
    title_intent: str
    introduction_intent: str
    nodes: tuple[OutlineNode, ...] = Field(min_length=1)
    mapping_report: OutlineProjectionMap
    outline_status: Literal["FROZEN"]
    generation_status: Literal["PASS"]
    outline_sha: str = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def validate_outline_contract(self) -> "ArticleOutline":
        if self.mapping_report.outline_id != self.outline_id:
            raise ValueError("mapping report outline identity mismatch")
        if self.mapping_report.source_plan_sha != self.source_plan_sha:
            raise ValueError("mapping report source plan mismatch")
        if (
            self.mapping_report.source_evidence_group_pack_sha
            != self.source_evidence_group_pack_sha
        ):
            raise ValueError("mapping report Evidence Group Pack mismatch")
        expected = stable_sha(self.model_dump(mode="json", exclude={"outline_sha"}))
        if self.outline_sha != expected:
            raise ValueError("outline_sha mismatch")
        return self


class ProjectionResult(_StrictFrozenModel):
    outline: ArticleOutline
    validation_report: dict[str, Any]
    readable_outline: str


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _jsonable(value.model_dump(mode="json"))
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(
        _jsonable(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def stable_sha(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "")).strip()


def _clean_heading(value: Any) -> str:
    return _ENUMERATION_PREFIX_RE.sub("", _clean(value)).strip("：:；;，,。 ")


def _strings(values: Any) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        return ()
    return tuple(dict.fromkeys(_clean(value) for value in values if _clean(value)))


def _section_id(section: Mapping[str, Any]) -> str:
    return _clean(section.get("section_id"))


def _macro_role(section: Mapping[str, Any]) -> str:
    role = _clean(section.get("section_role"))
    if not role:
        raise OutlineProjectionError(
            INSUFFICIENT_PLAN_FOR_ARTICLE_OUTLINE,
            {"reason": "Frozen section has no section_role", "section_id": _section_id(section)},
        )
    return _MACRO_ROLE_BY_SECTION_ROLE.get(role, f"OTHER:{role}")


def _positive_weight(value: Any, *, fallback: float = 1.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        result = fallback
    return result if result > 0 else fallback


def _section_weight(section: Mapping[str, Any]) -> float:
    if section.get("paragraph_tasks"):
        return sum(t["target_weight"] for t in section["paragraph_tasks"])
    for field in (
        "relative_weight",
        "recommended_relative_budget",
        "importance",
        "recommended_words",
        "priority",
    ):
        if section.get(field) not in (None, ""):
            return _positive_weight(section.get(field))
    return 1.0


def _heading_intent(sections: Sequence[Mapping[str, Any]]) -> str:
    values = tuple(
        dict.fromkeys(
            _clean(section.get("section_goal") or section.get("section_purpose"))
            for section in sections
            if _clean(section.get("section_goal") or section.get("section_purpose"))
        )
    )
    if not values:
        raise OutlineProjectionError(
            INSUFFICIENT_PLAN_FOR_ARTICLE_OUTLINE,
            {
                "reason": "Frozen section has no heading intent source",
                "source_section_ids": [_section_id(section) for section in sections],
            },
        )
    return "；".join(values)


def _heading_text(sections: Sequence[Mapping[str, Any]], macro_role: str) -> str:
    blocks = tuple(
        dict.fromkeys(
            _clean_heading(section.get("business_block") or section.get("section_purpose"))
            for section in sections
            if _clean_heading(section.get("business_block") or section.get("section_purpose"))
        )
    )
    block = "、".join(blocks) or _READABLE_ROLE.get(macro_role, "本部分")
    if macro_role == "PROBLEM":
        return block if "问题" in block else f"{block}方面存在的问题"
    if macro_role == "NEXT_STEP":
        return block if any(word in block for word in ("下一步", "安排", "计划")) else f"{block}下一步安排"
    return block


def _forbidden_claims(
    plan: Mapping[str, Any], section: Mapping[str, Any], macro_role: str
) -> tuple[str, ...]:
    claims = [
        *_strings(plan.get("global_prohibited_claims")),
        *_strings(section.get("prohibited_specifics")),
    ]
    if macro_role == "PROBLEM":
        claims.append("不得把问题、差距或未实施事项改写为已完成成绩")
    elif macro_role == "NEXT_STEP":
        claims.append("不得把PLAN、PENDING、建议或后续任务改写为COMPLETED")
    else:
        claims.append("不得跨章节搬运Evidence或改变Semantic State")
    return tuple(dict.fromkeys(_clean(value) for value in claims if _clean(value)))


def _must_cover(section: Mapping[str, Any], chain: Mapping[str, Any] | None = None) -> tuple[str, ...]:
    values: list[str] = []
    if chain:
        values.extend(_strings(chain.get("relation_basis")))
        if _clean(chain.get("chain_completeness")):
            values.append(_clean(chain.get("chain_completeness")))
    values.extend(_strings(section.get("writing_focus")))
    values.extend(_strings(section.get("expansion_path")))
    values.extend(_strings(section.get("content_functions")))
    usage = section.get("evidence_usage_plan")
    if isinstance(usage, Mapping):
        values.extend(_strings(usage.get("usage_steps")))
    return tuple(dict.fromkeys(value for value in values if value))


def _source_fields(section: Mapping[str, Any], *, include_chain: bool) -> tuple[str, ...]:
    fields = tuple(
        field
        for field in (
            "section_role",
            "section_goal",
            "section_purpose",
            "business_block",
            "writing_focus",
            "expansion_path",
            "content_functions",
            "evidence_usage_plan",
            "prohibited_specifics",
        )
        if section.get(field) not in (None, "", [], {})
    )
    return (*fields, "work_chain_summaries") if include_chain else fields


def _chain_index(section: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    section_id = _section_id(section)
    result: dict[str, Mapping[str, Any]] = {}
    for chain in section.get("work_chain_summaries") or []:
        if not isinstance(chain, Mapping):
            continue
        chain_id = _clean(chain.get("chain_id"))
        if not chain_id:
            continue
        if _clean(chain.get("section_id")) not in ("", section_id):
            raise OutlineProjectionError(
                OUTLINE_PROJECTION_FAIL,
                {"reason": "Work Chain crosses Frozen section ownership", "chain_id": chain_id},
            )
        result[chain_id] = chain
    return result


def _chain_membership_index(
    chains: Mapping[str, Mapping[str, Any]],
) -> dict[str, set[str]]:
    result: dict[str, set[str]] = defaultdict(set)
    for chain_id, chain in chains.items():
        for evidence_id in _strings(chain.get("member_evidence_ids")):
            result[evidence_id].add(chain_id)
    return result


def _group_chain(
    group: Mapping[str, Any],
    chains: Mapping[str, Mapping[str, Any]],
    membership: Mapping[str, set[str]],
) -> str:
    evidence_ids = set(_strings(group.get("evidence_ids")))
    matches: set[str] = set(chains) if evidence_ids else set()
    for evidence_id in evidence_ids:
        matches &= membership.get(evidence_id, set())
    if len(matches) > 1:
        raise OutlineProjectionError(
            OUTLINE_PROJECTION_FAIL,
            {"reason": "Evidence Group matches multiple Work Chains", "group_id": _clean(group.get("group_id"))},
        )
    return next(iter(matches)) if matches else ""


def _chain_goal(chain: Mapping[str, Any]) -> str:
    candidates = [
        *_strings(chain.get("writing_purpose")),
        *_strings(chain.get("relation_basis")),
    ]
    for field in ("writing_purpose", "relation_basis", "chain_goal", "topic"):
        value = chain.get(field)
        if not isinstance(value, (list, tuple)) and _clean(value):
            candidates.append(_clean(value))
    readable = [value for value in candidates if re.search(r"[\u3400-\u9fff]", value)]
    return "；".join(dict.fromkeys(readable))


def _section_goal(section: Mapping[str, Any]) -> tuple[str, str]:
    focus = _strings(section.get("writing_focus"))
    if focus:
        return "；".join(focus), "writing_focus"
    path = _strings(section.get("expansion_path"))
    if path:
        return "；".join(path), "expansion_path"
    functions = _strings(section.get("content_functions"))
    if functions:
        return "、".join(_CONTENT_FUNCTION_LABEL.get(value, value) for value in functions), "content_functions"
    goal = _clean(section.get("section_goal") or section.get("section_purpose"))
    if goal:
        return goal, "section_goal"
    raise OutlineProjectionError(
        INSUFFICIENT_PLAN_FOR_ARTICLE_OUTLINE,
        {"reason": "No traceable ParagraphIntent goal", "section_id": _section_id(section)},
    )


def _paragraph_intents(
    *,
    node_id: str,
    section: Mapping[str, Any],
    groups: Sequence[Mapping[str, Any]],
    plan: Mapping[str, Any],
    start: int,
) -> tuple[ParagraphIntent, ...]:
    section_id = _section_id(section)
    macro_role = _macro_role(section)
    editorial_tasks = section.get("paragraph_tasks", [])
    if editorial_tasks:
        from app.services.generation.paragraph_task import EXPANSION_TEXT
        intents = []
        assigned = []
        for task in editorial_tasks:
            pool = set(task["supporting_evidence_ids"])
            owned = [g for g in groups if set(g["evidence_ids"]) <= pool]
            if {e for g in owned for e in g["evidence_ids"]} != pool:
                raise OutlineProjectionError(OUTLINE_PROJECTION_FAIL, {"reason": "paragraph task material mismatch"})
            assigned.extend(g["group_id"] for g in owned)
            intents.append(ParagraphIntent(
                paragraph_intent_id=f"{node_id}-PI-{start + len(intents):02d}",
                paragraph_role=task["paragraph_role"], writing_goal=task["central_idea"],
                evidence_group_ids=tuple(g["group_id"] for g in owned),
                must_cover=tuple(EXPANSION_TEXT[c] for c in task["expansion_order"]),
                forbidden_claims=_forbidden_claims(plan, section, macro_role),
                allow_general_synthesis=bool(plan.get("allowed_general_analysis")),
                target_weight=task["target_weight"], source_section_ids=(section_id,),
                source_fields=("paragraph_tasks", "writing_reference", "paragraph_tasks." + task["task_id"]), work_chain_ids=(),
            ))
        if len(assigned) != len(set(assigned)) or set(assigned) != {g["group_id"] for g in groups}:
            raise OutlineProjectionError(OUTLINE_PROJECTION_FAIL, {"reason": "paragraph task ownership mismatch"})
        return tuple(intents)
    chains = _chain_index(section)
    membership = _chain_membership_index(chains)
    groups_by_chain: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    unassigned: list[Mapping[str, Any]] = []
    for group in groups:
        chain_id = _group_chain(group, chains, membership)
        (groups_by_chain[chain_id] if chain_id and _chain_goal(chains[chain_id]) else unassigned).append(group)

    intents: list[ParagraphIntent] = []
    for chain_id in sorted(groups_by_chain):
        chain = chains[chain_id]
        goal = _chain_goal(chain)
        intent_id = f"{node_id}-PI-{start + len(intents):02d}"
        intents.append(
            ParagraphIntent(
                paragraph_intent_id=intent_id,
                paragraph_role="WORK_CHAIN_PURPOSE",
                writing_goal=goal,
                evidence_group_ids=tuple(_clean(group.get("group_id")) for group in groups_by_chain[chain_id]),
                must_cover=_must_cover(section, chain),
                forbidden_claims=_forbidden_claims(plan, section, macro_role),
                allow_general_synthesis=bool(plan.get("allowed_general_analysis")),
                target_weight=_positive_weight(len(_must_cover(section, chain)) + _section_weight(section)),
                source_section_ids=(section_id,),
                source_fields=_source_fields(section, include_chain=True),
                work_chain_ids=(chain_id,),
            )
        )

    if unassigned or not intents:
        goal, goal_field = _section_goal(section)
        intent_id = f"{node_id}-PI-{start + len(intents):02d}"
        intents.append(
            ParagraphIntent(
                paragraph_intent_id=intent_id,
                paragraph_role=(
                    _strings(section.get("content_functions"))[0]
                    if _strings(section.get("content_functions"))
                    else macro_role
                ),
                writing_goal=goal,
                evidence_group_ids=tuple(_clean(group.get("group_id")) for group in unassigned),
                must_cover=_must_cover(section),
                forbidden_claims=_forbidden_claims(plan, section, macro_role),
                allow_general_synthesis=bool(plan.get("allowed_general_analysis")),
                target_weight=_positive_weight(len(_must_cover(section)) + _section_weight(section)),
                source_section_ids=(section_id,),
                source_fields=tuple(dict.fromkeys((*_source_fields(section, include_chain=bool(chains)), goal_field))),
                work_chain_ids=(),
            )
        )
    return tuple(intents)


def _valid_merge_pair(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return bool(
        _macro_role(left) == _macro_role(right)
        and _clean(left.get("business_block"))
        and _clean(left.get("business_block")) == _clean(right.get("business_block"))
        and set(_strings(left.get("merged_from")))
        and set(_strings(left.get("merged_from"))) & set(_strings(right.get("merged_from")))
        and set(_strings(left.get("content_functions")))
        == set(_strings(right.get("content_functions")))
    )


def _section_groups(
    groups_by_section: Mapping[str, Sequence[Mapping[str, Any]]], section_ids: Iterable[str]
) -> list[Mapping[str, Any]]:
    return [group for section_id in section_ids for group in groups_by_section.get(section_id, ())]


def _explicit_subsections(section: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    values = [value for value in section.get("subsections") or [] if isinstance(value, Mapping)]
    if not values:
        return []
    guidance = section.get("reference_structure_guidance")
    if not isinstance(guidance, Mapping) or _clean(guidance.get("source_role")) != "REFERENCE_STRUCTURE_ONLY_NOT_FACT_SOURCE":
        raise OutlineProjectionError(
            INSUFFICIENT_PLAN_FOR_ARTICLE_OUTLINE,
            {"reason": "1:N lacks validated writing structure overlay", "section_id": _section_id(section)},
        )
    required = {"subsection_id", "topic", "function", "evidence_group_ids", "work_chain_ids"}
    if any(not required <= set(subsection) for subsection in values):
        raise OutlineProjectionError(
            INSUFFICIENT_PLAN_FOR_ARTICLE_OUTLINE,
            {"reason": "1:N lacks explicit Group/Chain mapping", "section_id": _section_id(section)},
        )
    return values


def _build_nodes(
    plan: Mapping[str, Any], evidence_group_pack: Mapping[str, Any]
) -> tuple[OutlineNode, ...]:
    sections = [value for value in plan.get("sections") or [] if isinstance(value, Mapping)]
    if not sections:
        raise OutlineProjectionError(
            INSUFFICIENT_PLAN_FOR_ARTICLE_OUTLINE,
            {"reason": "FrozenArticlePlan contains no sections"},
        )
    section_ids = [_section_id(section) for section in sections]
    if any(not value for value in section_ids) or len(section_ids) != len(set(section_ids)):
        raise OutlineProjectionError(OUTLINE_PROJECTION_FAIL, {"reason": "Frozen section IDs are empty or duplicated"})
    groups_by_section: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for group in evidence_group_pack.get("groups") or []:
        if isinstance(group, Mapping):
            groups_by_section[_clean(group.get("section_id"))].append(group)

    nodes: list[OutlineNode] = []
    section_index = 0
    order = 1
    while section_index < len(sections):
        section = sections[section_index]
        source_id = _section_id(section)
        subsections = _explicit_subsections(section)
        if subsections:
            container_id = f"ON-{order:02d}"
            macro_role = _macro_role(section)
            nodes.append(
                OutlineNode(
                    outline_node_id=container_id,
                    parent_outline_node_id=None,
                    level=1,
                    order=order,
                    macro_role=macro_role,
                    source_section_ids=(source_id,),
                    heading_intent=_heading_intent((section,)),
                    heading_text=_heading_text((section,), macro_role),
                    paragraph_intents=(),
                    evidence_group_ids=(),
                    work_chain_ids=_strings(section.get("work_chain_ids")),
                    weight=_section_weight(section),
                )
            )
            order += 1
            group_index = {
                _clean(group.get("group_id")): group
                for group in groups_by_section.get(source_id, ())
            }
            declared: list[str] = []
            for subsection in subsections:
                group_ids = _strings(subsection.get("evidence_group_ids"))
                chain_ids = _strings(subsection.get("work_chain_ids"))
                if not group_ids or any(group_id not in group_index for group_id in group_ids):
                    raise OutlineProjectionError(OUTLINE_PROJECTION_FAIL, {"reason": "1:N references unknown Evidence Group"})
                declared.extend(group_ids)
                child_id = f"ON-{order:02d}"
                topic = _clean_heading(subsection.get("topic"))
                function = _clean(subsection.get("function"))
                intent = ParagraphIntent(
                    paragraph_intent_id=f"{child_id}-PI-01",
                    paragraph_role=function,
                    writing_goal=topic,
                    evidence_group_ids=group_ids,
                    must_cover=(topic, function),
                    forbidden_claims=_forbidden_claims(plan, section, macro_role),
                    allow_general_synthesis=bool(plan.get("allowed_general_analysis")),
                    target_weight=_positive_weight(subsection.get("weight") or _section_weight(section)),
                    source_section_ids=(source_id,),
                    source_fields=("subsections", "reference_structure_guidance"),
                    work_chain_ids=chain_ids,
                )
                nodes.append(
                    OutlineNode(
                        outline_node_id=child_id,
                        parent_outline_node_id=container_id,
                        level=2,
                        order=order,
                        macro_role=macro_role,
                        source_section_ids=(source_id,),
                        heading_intent=topic,
                        heading_text=topic,
                        paragraph_intents=(intent,),
                        evidence_group_ids=group_ids,
                        work_chain_ids=chain_ids,
                        weight=intent.target_weight,
                    )
                )
                order += 1
            known = set(group_index)
            if len(declared) != len(set(declared)) or set(declared) != known:
                raise OutlineProjectionError(
                    INSUFFICIENT_PLAN_FOR_ARTICLE_OUTLINE,
                    {"reason": "1:N Evidence ownership is not mutually exclusive and complete"},
                )
            section_index += 1
            continue

        merged = [section]
        if section_index + 1 < len(sections) and _valid_merge_pair(section, sections[section_index + 1]):
            merged.append(sections[section_index + 1])
        source_ids = tuple(_section_id(value) for value in merged)
        node_id = f"ON-{order:02d}"
        intents: list[ParagraphIntent] = []
        for source in merged:
            intents.extend(
                _paragraph_intents(
                    node_id=node_id,
                    section=source,
                    groups=_section_groups(groups_by_section, (_section_id(source),)),
                    plan=plan,
                    start=len(intents) + 1,
                )
            )
        node_groups = _section_groups(groups_by_section, source_ids)
        macro_role = _macro_role(section)
        nodes.append(
            OutlineNode(
                outline_node_id=node_id,
                parent_outline_node_id=None,
                level=1,
                order=order,
                macro_role=macro_role,
                source_section_ids=source_ids,
                heading_intent=_heading_intent(merged),
                heading_text=_heading_text(merged, macro_role),
                paragraph_intents=tuple(intents),
                evidence_group_ids=tuple(_clean(group.get("group_id")) for group in node_groups),
                work_chain_ids=tuple(dict.fromkeys(chain_id for value in merged for chain_id in _strings(value.get("work_chain_ids")))),
                weight=sum(_section_weight(value) for value in merged),
            )
        )
        order += 1
        section_index += len(merged)
    return tuple(nodes)


def _build_projection_map(
    *,
    source_plan_sha: str,
    group_pack_sha: str,
    outline_id: str,
    nodes: Sequence[OutlineNode],
    groups: Sequence[Mapping[str, Any]],
) -> OutlineProjectionMap:
    section_to_nodes: dict[str, list[str]] = defaultdict(list)
    for node in nodes:
        for section_id in node.source_section_ids:
            section_to_nodes[section_id].append(node.outline_node_id)
    group_index = {_clean(group.get("group_id")): group for group in groups}
    owners: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    cross_role: list[dict[str, Any]] = []
    mapping_loss: list[dict[str, Any]] = []
    for node in nodes:
        intent_owners: dict[str, list[str]] = defaultdict(list)
        for intent in node.paragraph_intents:
            for group_id in intent.evidence_group_ids:
                intent_owners[group_id].append(intent.paragraph_intent_id)
        intent_groups = set(intent_owners)
        missing = sorted(set(node.evidence_group_ids) - intent_groups)
        if missing:
            mapping_loss.append({"outline_node_id": node.outline_node_id, "group_ids": missing})
        for group_id in node.evidence_group_ids:
            source_section_id = _clean((group_index.get(group_id) or {}).get("section_id"))
            if source_section_id not in node.source_section_ids:
                cross_role.append({"group_id": group_id, "outline_node_id": node.outline_node_id})
            matching = intent_owners.get(group_id, [])
            if len(matching) == 1:
                owners[group_id].append((node.outline_node_id, matching[0], source_section_id))

    known = set(group_index)
    unassigned = tuple(sorted(known - set(owners)))
    duplicates = tuple(sorted(group_id for group_id, rows in owners.items() if len(rows) > 1))
    if unassigned or duplicates or cross_role or mapping_loss:
        raise OutlineProjectionError(
            OUTLINE_PROJECTION_FAIL,
            {
                "reason": "Evidence Group ownership is not complete and unique",
                "unassigned_evidence_groups": list(unassigned),
                "duplicate_evidence_ownership": list(duplicates),
                "cross_role_assignments": cross_role,
                "mapping_loss": mapping_loss,
            },
        )

    mappings: list[NodeProjectionMapping] = []
    for node in nodes:
        if len(node.source_section_ids) > 1:
            mapping_type = "N:1"
            reason = "consecutive same-role sections with explicit shared frozen merge basis"
        elif len(section_to_nodes[node.source_section_ids[0]]) > 1:
            mapping_type = "1:N" if node.evidence_group_ids else "1:N_CONTAINER"
            reason = "validated structure overlay with explicit Group/Chain ownership"
        else:
            mapping_type = "1:1"
            reason = "default FrozenArticlePlan section projection"
        mappings.append(
            NodeProjectionMapping(
                outline_node_id=node.outline_node_id,
                source_section_ids=node.source_section_ids,
                parent_outline_node_id=node.parent_outline_node_id,
                outline_level=node.level,
                outline_order=node.order,
                macro_role=node.macro_role,
                heading_intent=node.heading_intent,
                paragraph_intent_ids=tuple(value.paragraph_intent_id for value in node.paragraph_intents),
                evidence_group_ids=node.evidence_group_ids,
                work_chain_ids=node.work_chain_ids,
                mapping_type=mapping_type,
                mapping_reason=reason,
            )
        )
    ownership = tuple(
        EvidenceOwnership(
            evidence_group_id=group_id,
            source_section_id=rows[0][2],
            primary_outline_node_id=rows[0][0],
            primary_paragraph_intent_id=rows[0][1],
            semantic_state=_clean(group_index[group_id].get("semantic_state")) or "UNKNOWN",
        )
        for group_id, rows in sorted(owners.items())
    )
    unsigned = {
        "version": OUTLINE_PROJECTION_MAP_VERSION,
        "source_plan_sha": source_plan_sha,
        "source_evidence_group_pack_sha": group_pack_sha,
        "outline_id": outline_id,
        "node_mappings": tuple(mappings),
        "evidence_ownership": ownership,
        "unassigned_evidence_groups": (),
        "duplicate_evidence_ownership": (),
        "cross_role_assignments": (),
        "mapping_loss": (),
        "projection_status": PROJECTION_STATUS_PASS,
        "semantic_state_transformations": 0,
        "external_model_calls": EXTERNAL_MODEL_CALLS,
    }
    return OutlineProjectionMap(**unsigned, projection_map_sha=stable_sha(unsigned))


def render_readable_outline(outline: ArticleOutline) -> str:
    lines = [f"文章标题意图：{outline.title_intent}", "", f"开头：{outline.introduction_intent}", ""]
    numerals = "一二三四五六七八九十"
    top_index = 0
    child_index: dict[str, int] = defaultdict(int)
    for node in outline.nodes:
        if node.level == 1:
            top_index += 1
            prefix = numerals[top_index - 1] if top_index <= len(numerals) else str(top_index)
            lines.append(f"{prefix}、{node.heading_text}")
        else:
            parent = node.parent_outline_node_id or ""
            child_index[parent] += 1
            index = child_index[parent]
            prefix = numerals[index - 1] if index <= len(numerals) else str(index)
            lines.append(f"（{prefix}）{node.heading_text}")
        lines.append(f"  章节功能：{_READABLE_ROLE.get(node.macro_role, '既定章节')}；材料归属：已绑定{len(node.evidence_group_ids)}组")
        for index, intent in enumerate(node.paragraph_intents, start=1):
            lines.append(f"  段落任务{index}：{intent.writing_goal}")
            points = []
            for point in intent.must_cover:
                readable = _CONTENT_FUNCTION_LABEL.get(point, point)
                if re.search(r"[\u3400-\u9fff]", readable) and readable not in points:
                    points.append(readable)
            if points:
                lines.append(f"    展开重点：{'；'.join(points)}")
            if intent.evidence_group_ids:
                lines.append(f"    证据使用：{len(intent.evidence_group_ids)}组，按冻结归属使用")
            if intent.work_chain_ids:
                lines.append(f"    组织依据：{len(intent.work_chain_ids)}条既有工作链")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


class ArticleOutlineProjector:
    """Projection-only owner; PlanningEngine remains the sole Planner."""

    external_model_calls = EXTERNAL_MODEL_CALLS

    def project(
        self,
        frozen_article_plan: Mapping[str, Any] | Any,
        evidence_group_pack: Mapping[str, Any],
    ) -> ProjectionResult:
        frozen = (
            frozen_article_plan.to_dict()
            if hasattr(frozen_article_plan, "to_dict")
            else dict(frozen_article_plan)
        )
        source_plan_sha = _clean(frozen.get("frozen_plan_sha256"))
        plan = frozen.get("content_plan")
        if len(source_plan_sha) != 64 or not isinstance(plan, Mapping):
            raise OutlineProjectionError(OUTLINE_PROJECTION_FAIL, {"reason": "invalid FrozenArticlePlan identity or payload"})
        planning_owner = _clean(frozen.get("planning_owner"))
        if planning_owner != "planning_engine_v1":
            raise OutlineProjectionError(
                INSUFFICIENT_PLAN_FOR_ARTICLE_OUTLINE,
                {"reason": "PlanningEngine is not the frozen plan owner", "planning_owner": planning_owner},
            )
        groups = [value for value in evidence_group_pack.get("groups") or [] if isinstance(value, Mapping)]
        group_ids = [_clean(group.get("group_id")) for group in groups]
        if any(not value for value in group_ids) or len(group_ids) != len(set(group_ids)):
            raise OutlineProjectionError(OUTLINE_PROJECTION_FAIL, {"reason": "Evidence Group IDs are empty or duplicated"})
        section_ids = {
            _section_id(section)
            for section in plan.get("sections") or []
            if isinstance(section, Mapping)
        }
        unknown = sorted({_clean(group.get("section_id")) for group in groups} - section_ids)
        if unknown:
            raise OutlineProjectionError(OUTLINE_PROJECTION_FAIL, {"reason": "Evidence Groups reference unknown sections", "section_ids": unknown})

        group_pack_sha = stable_sha(evidence_group_pack)
        outline_id = f"AO-{stable_sha({'source_plan_sha': source_plan_sha, 'group_pack_sha': group_pack_sha})[:16].upper()}"
        nodes = _build_nodes(plan, evidence_group_pack)
        projection_map = _build_projection_map(
            source_plan_sha=source_plan_sha,
            group_pack_sha=group_pack_sha,
            outline_id=outline_id,
            nodes=nodes,
            groups=groups,
        )
        article_purpose = _clean(plan.get("article_purpose"))
        document_type = _clean(frozen.get("document_type"))
        title_intent = article_purpose or f"形成{document_type or '目标文种'}"
        unsigned = {
            "version": ARTICLE_OUTLINE_CONTRACT_VERSION,
            "outline_id": outline_id,
            "source_plan_id": _clean(frozen.get("plan_id") or plan.get("plan_id")),
            "source_plan_sha": source_plan_sha,
            "source_evidence_group_pack_sha": group_pack_sha,
            "planning_owner": planning_owner,
            "planner_trace_label": _clean(plan.get("planner_owner")),
            "document_type": document_type,
            "title_intent": title_intent,
            "introduction_intent": f"交代“{title_intent}”的写作范围，并引出冻结大纲各部分",
            "nodes": nodes,
            "mapping_report": projection_map,
            "outline_status": OUTLINE_STATUS_FROZEN,
            "generation_status": PROJECTION_STATUS_PASS,
        }
        outline = ArticleOutline(**unsigned, outline_sha=stable_sha(unsigned))
        validation = {
            "status": PROJECTION_STATUS_PASS,
            "source_plan_sha_preserved": outline.source_plan_sha == source_plan_sha,
            "evidence_ownership_complete": len(groups) == len(projection_map.evidence_ownership),
            "paragraph_intents_traceable": all(
                intent.source_fields and intent.source_section_ids and intent.writing_goal
                for node in nodes
                for intent in node.paragraph_intents
            ),
            "semantic_state_preserved_read_only": projection_map.semantic_state_transformations == 0,
            "mapping_type_counts": dict(sorted(Counter(row.mapping_type for row in projection_map.node_mappings).items())),
            "external_model_calls": self.external_model_calls,
        }
        readable = render_readable_outline(outline)
        return ProjectionResult(outline=outline, validation_report=validation, readable_outline=readable)
