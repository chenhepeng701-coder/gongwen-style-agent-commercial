"""M1/M2 editorial paragraph planning; never a fact or semantic-state owner.

Groups existing legal facts by their explicit subject matter. Editorial order
does not assert temporal/causal relations. Work-chain records remain unchanged.
"""
from __future__ import annotations

import copy
import re
from collections import defaultdict
from statistics import median
from typing import Any, Mapping

from app.services.generation.writing_reference import reference_guidance
from app.services.generation.work_summary_evidence_group import _compact, writable_fact_diagnostic

# Organizational themes, not a fixed outline: a theme exists only if the
# current evidence contains its markers. No case/section IDs or target quotas.
TOPICS = (
    ("数字化与监测预警", ("数字化", "智能", "预警", "在线监控", "大数据")),
    ("教育培训与能力建设", ("培训", "教育", "宣贯", "知晓率", "应急处置能力", "演练", "能力考核", "宣传", "安全生产月")),
    ("制度标准与政策落实", ("标准化", "制度化", "规范化", "政策", "激励", "保险费率")),
    ("责任落实与组织保障", ("责任", "用工", "劳务", "负责人", "专班", "调度", "考核", "会议", "统筹", "任务落地")),
    ("专业检查与执法", ("专家", "专业", "执法", "检查", "行刑")),
    ("隐患整改与工程治理", ("整改", "销号", "清零", "工程", "设施", "设备", "消防", "逃生通道", "自查自改", "重大事故隐患")),
    ("运行评估与响应", ("评估", "响应", "预案", "风险信号")),
    ("服务保障", ("服务", "救助", "就业", "保障")),
)
EXPANSION = {
    "work_progress": ("TOPIC", "ACTION", "QUALIFIED_PROGRESS"),
    "major_work": ("TOPIC", "ACTION", "QUALIFIED_PROGRESS"),
    "problem_analysis": ("CONSTRAINT", "DISTINCT_MANIFESTATIONS"),
    "next_step_arrangement": ("SUPPORTED_RESPONSE", "PLANNED_ACTION", "CONDITION_OR_NODE"),
}
EXPANSION_TEXT = {
    "TOPIC": "以本段主题统领材料",
    "ACTION": "组织已有对象与动作",
    "QUALIFIED_PROGRESS": "保留限定说明进度或结果，有依据才写结果",
    "CONSTRAINT": "指出证据明确的约束",
    "DISTINCT_MANIFESTATIONS": "展开不同表现，不同义复述，不补推原因",
    "SUPPORTED_RESPONSE": "有对应依据时承接问题或未完成事项",
    "PLANNED_ACTION": "组织计划动作，保持每条材料原有状态",
    "CONDITION_OR_NODE": "保留已有条件、范围及目标时点，不补造节点",
}
PLAN_STATES = {"PLAN", "PLAN_GOAL"}
PROBLEM_STATES = {"PROBLEM", "GAP", "NOT_IMPLEMENTED"}


def _terms(text: str) -> set[str]:
    text = re.sub(r"[^\u4e00-\u9fff]", "", text)
    return {text[i:i+2] for i in range(len(text)-1)} - {"工作", "安全", "生产", "推进", "方面"}


def _topic(text: str) -> str:
    # Long/specific marker wins over generic responsibility or safety wording.
    for name, markers in TOPICS:
        if any(m in text for m in markers):
            return name
    return ""


def _role(fact: Mapping[str, Any]) -> str:
    state = fact.get("semantic_state")
    if state in PLAN_STATES:
        return "planned_action"
    if state in PROBLEM_STATES:
        return "problem_manifestation"
    return "work_progress"


def _shared_object(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    """Require substantive source wording, not location/length/section identity."""
    ignored = {"安全生产", "有关工作", "相关工作", "工作推进", "具体事项", "存在问题", "方面存在"}
    def phrases(fact):
        text = re.sub(r"[^\u4e00-\u9fff]", "", str(fact.get("object") or fact["fact"]))
        return {text[i:i+4] for i in range(len(text)-3)} - ignored
    return bool(phrases(left) & phrases(right))


def _response_link(left: Mapping[str, Any], right: Mapping[str, Any]) -> str:
    """A response must be stated in source text; shared topic is insufficient."""
    if left.get("semantic_state") in PLAN_STATES and right.get("semantic_state") in PROBLEM_STATES:
        plan, problem = left, right
    elif right.get("semantic_state") in PLAN_STATES and left.get("semantic_state") in PROBLEM_STATES:
        plan, problem = right, left
    else:
        return ""
    match = re.search(r"(?:针对|为解决|回应)([^，。；]+)", plan["fact"])
    if not match:
        return ""
    target = match.group(1)
    # A verbatim problem phrase (including its limitation) must be in the
    # response target. No speculative relation from similar keywords/chains.
    problem_text = str(problem["fact"]).strip("。；，")
    obj = str(problem.get("object") or "").strip("。；，")
    candidates = [problem_text]
    if re.search(r"未|不|缺|不足|迟缓|有限", obj):
        candidates.append(obj)
    if any(len(v) >= 4 and v in target for v in candidates if v):
        return match.group(0)
    return ""


def _compatible(left, right):
    lt, rt = _topic(left["fact"]), _topic(right["fact"])
    same_center = (bool(lt) and lt == rt) or (not lt and not rt and _shared_object(left, right))
    if _role(left) != _role(right):
        return bool(_response_link(left, right))
    return same_center


def _partition(facts):
    """Complete-link partition; background never becomes a grouping bridge."""
    groups, supporting = [], []
    for fact in facts:
        diagnostic = fact.get("writability") or writable_fact_diagnostic(fact["fact"])
        if diagnostic["usage"] == "SUPPORT_ONLY":
            supporting.append(fact)
            continue
        target = next((rows for rows in groups if all(_compatible(fact, row) for row in rows)), None)
        if target is None:
            groups.append([fact])
        else:
            target.append(fact)
    # An incomplete clause can accompany related material, but is never a
    # primary fact or a rationale for merging otherwise unrelated groups.
    for fact in supporting:
        related = next((rows for rows in groups if _topic(fact["fact"]) and any(
            _topic(r["fact"]) == _topic(fact["fact"]) for r in rows)), None)
        ranked = [(len(_terms(fact["fact"]) & _terms("".join(r["fact"] for r in rows))), i)
                  for i, rows in enumerate(groups)]
        best = max(ranked, default=(0, -1))
        if related is not None:
            related.append(fact)
        elif best[0] >= 2:
            groups[best[1]].append(fact)
        elif groups and (fact.get("writability") or writable_fact_diagnostic(fact["fact"])).get("reason") == "context_clause_without_independent_assertion":
            # Section framing is explicitly not a factual manifestation.
            groups[0].append(fact)
        else:
            groups.append([fact])
    return groups


def _source_order(rows, section):
    """Use existing chain order only inside an already coherent paragraph."""
    by_id = {f["evidence_id"]: f for f in rows}
    predecessors = {eid: set() for eid in by_id}
    for chain in section.get("work_chain_summaries", []):
        ordered = [eid for eid in chain.get("ordered_evidence_ids", []) if eid in by_id]
        for earlier, later in zip(ordered, ordered[1:]):
            if earlier != later:
                predecessors[later].add(earlier)
    result, remaining = [], list(by_id)
    while remaining:
        eid = next((e for e in remaining if not predecessors[e].intersection(remaining)), None)
        if eid is None:
            return rows, "EXISTING_CHAIN_ORDER_CONFLICT_SOURCE_ORDER_RETAINED"
        result.append(by_id[eid])
        remaining.remove(eid)
    return result, "EXISTING_CHAIN_ORDER_WITH_STABLE_UNLINKED_SOURCE_ORDER"


def plan_paragraph_tasks(content_plan: Mapping[str, Any], evidence_group_pack: Mapping[str, Any],
                         writing_reference: Mapping[str, Any]) -> dict[str, Any]:
    """Called by PlanningEngine before freeze, with unchanged legal material.

    Existing Evidence Group owner decides writable facts; this function neither
    adds nor drops one. Unclassified facts join only a lexically related group;
    otherwise they keep a source-local topic, never a section-wide catch-all.
    """
    plan = copy.deepcopy(dict(content_plan))
    plan["writing_reference"] = copy.deepcopy(dict(writing_reference))
    facts_by_section: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for group in evidence_group_pack.get("groups", []):
        facts_by_section[group["section_id"]].extend(copy.deepcopy(group["facts"]))
    all_tasks = []
    for section in plan["sections"]:
        sid = section["section_id"]
        facts = facts_by_section[sid]
        if not facts:
            continue
        grouped = _partition(facts)
        lengths = [s.get("paragraph_chars_median", 0) for s in writing_reference.get("selected", [])
                   if s.get("paragraph_chars_median", 0) > 0]
        density_unit = median(lengths) if lengths else 160
        tasks = []
        role = section["section_role"]
        guidance = reference_guidance(writing_reference, role)
        for order, rows in enumerate(grouped):
            complete = [f for f in rows if (f.get("writability") or writable_fact_diagnostic(f["fact"]))["usage"] != "SUPPORT_ONLY"]
            incomplete = [f for f in rows if f not in complete]
            topics = list(dict.fromkeys(_topic(f["fact"]) or f["fact"][:24] for f in complete))
            topic = "；".join(topics) if topics else "附属片段（缺少独立成句条件）"
            states = {_role(f) for f in complete}
            paragraph_role = next(iter(states)) if len(states) == 1 else "supported_problem_response" if states else "supporting_context_only"
            links = [link for i, f in enumerate(complete) for other in complete[i+1:] if (link := _response_link(f, other))]
            # State differences remain explicit. This ordering is editorial,
            # never a temporal or causal claim about the source facts.
            rows, order_basis = _source_order(rows, section)
            rows = sorted(rows, key=lambda f: (f in incomplete, 0 if links and _role(f) == "problem_manifestation" else 1))
            unique = list(dict.fromkeys(f["fact"] for f in rows))
            primary = max(complete, key=lambda f: (len(_terms(topic) & _terms(f["fact"])), len(f["fact"])), default=None)
            expansion_role = "problem_analysis" if states == {"problem_manifestation"} else "next_step_arrangement" if "planned_action" in states else role
            expansion = list(EXPANSION.get(expansion_role, EXPANSION["work_progress"]))
            restrictions = [EXPANSION_TEXT[c] for c in expansion]
            restrictions += ["各材料按原主体、对象、时间、否定和状态分别表述；并列组织不表示因果、同一主体或完成状态。",
                             "同一核心事实只表达一次；重复材料可合并引用，不得换措辞反复总结。"]
            restrictions += [f"INCOMPLETE_WRITABLE_FACT：片段‘{f['fact']}’仅可作附属背景，不得独立写作完整FACT；不得补造主语、用途对象、经费、项目或结论；无法附属使用时保留待核。" for f in incomplete]
            if not complete:
                restrictions = restrictions[-len(incomplete):] + ["本任务没有完整事实，不安排独立正文段落，只保留待核附属材料。"]
            task = {
                "task_id": f"{sid}-PT-{order:02d}", "section_role": role,
                "paragraph_role": paragraph_role, "central_idea": topic, "paragraph_order": order,
                "subtopics": topics or [topic],
                "supporting_evidence_ids": [f["evidence_id"] for f in rows],
                "must_cover_fact_ids": [primary["evidence_id"]] if primary else [],
                "expansion_order": expansion,
                "expansion_guidance": restrictions,
                "source_evidence_order": [f["evidence_id"] for f in rows],
                "evidence_order_basis": order_basis,
                "local_evidence_pool": copy.deepcopy(rows),
                "must_preserve_semantic_meaning": {f["evidence_id"]: {k: copy.deepcopy(f.get(k)) for k in ("fact", "subject", "object", "semantic_state", "semantic_constraints")} for f in rows},
                "support_only_evidence_ids": [f["evidence_id"] for f in incomplete],
                "grouping_rationale": ("源文明确回应：" + "；".join(links)) if links else ("同属“" + topic + "”，按各自主体、对象及状态并列展开；附属片段不构成中心事实。"),
                "responsibility": "仅展开本段完整事实的共同主题；保留每条材料状态，附属片段不补全。" if complete else "附属片段待核，不独立成文。",
                "response_source_phrases": links,
                "semantic_states": {f["evidence_id"]: f["semantic_state"] for f in rows},
                "writing_reference_guidance": guidance,
                "target_weight": max(0.1, sum(map(len, unique)) / density_unit),
                "budget_basis": {"distinct_material_chars": sum(map(len, unique)),
                                 "reference_paragraph_chars_median": density_unit,
                                 "paragraph_function": role, "stop_when_exhausted": True},
                "creates_facts": False,
            }
            tasks.append(task)
        for i, task in enumerate(tasks):
            task["previous_task_id"] = tasks[i-1]["task_id"] if i else None
            task["next_task_id"] = tasks[i+1]["task_id"] if i+1 < len(tasks) else None
            task["facts_that_must_not_repeat"] = [f["evidence_id"] for other in tasks if other is not task
                                                  for f in facts if f["evidence_id"] in other["supporting_evidence_ids"]]
        section["paragraph_tasks"] = tasks
        all_tasks.extend(tasks)
    # Topic correspondence is organizational, never a claim of causation.
    problems = [t for t in all_tasks if t["section_role"] == "problem_analysis"]
    for task in all_tasks:
        task["responds_to_task_ids"] = [p["task_id"] for p in problems if
            task["section_role"] == "next_step_arrangement" and any(
                _response_link(f, other) for f in task["local_evidence_pool"] for other in p["local_evidence_pool"])]
    for i, task in enumerate(all_tasks):
        for label, index in (("previous", i-1), ("next", i+1)):
            other = all_tasks[index] if 0 <= index < len(all_tasks) else None
            task["relation_to_" + label] = ({"task_id": other["task_id"], "central_idea": other["central_idea"],
                                           "boundary": "相邻组织关系，不暗示因果或成果；对方核心事实不在本段重写。"} if other else None)
    plan["paragraph_task_owner"] = "planning_engine_v1"
    return plan


def validate_paragraph_tasks(plan: Mapping[str, Any], groups: Mapping[str, Any]) -> None:
    material = {f["evidence_id"]: f for g in groups["groups"] for f in g["facts"]}
    facts = {f["evidence_id"]: (g["section_id"], f["semantic_state"])
             for g in groups["groups"] for f in g["facts"]}
    assigned = []
    for section in plan["sections"]:
        for order, task in enumerate(section.get("paragraph_tasks", [])):
            ids = task["supporting_evidence_ids"]
            if (task["paragraph_order"] != order or task["creates_facts"] is not False
                    or not set(task["must_cover_fact_ids"]) <= set(ids)
                    or set(task["semantic_states"]) != set(ids)):
                raise ValueError("INVALID_PARAGRAPH_TASK")
            for eid in ids:
                if facts.get(eid) != (section["section_id"], task["semantic_states"][eid]):
                    raise ValueError("PARAGRAPH_TASK_FACT_BOUNDARY_MISMATCH")
            if "local_evidence_pool" in task:
                def payload(f):
                    return {k: v for k, v in f.items() if k != "fact_unit_id"}
                # Older legal group snapshots lack additive projection metadata.
                # Compare every supplied source field, allowing the regenerated
                # owner projection to add metadata without changing those fields.
                pool = task["local_evidence_pool"]
                if (len(pool) != len(ids) or any(
                        any((_compact(material[e].get(k)) != _compact(v)) if k == "fact"
                            else material[e].get(k) != v for k, v in payload(f).items())
                        for f, e in zip(pool, ids)) or task["source_evidence_order"] != ids):
                    raise ValueError("PARAGRAPH_TASK_SOURCE_MUTATION")
                meaning = {f["evidence_id"]: {k: copy.deepcopy(f.get(k)) for k in
                           ("fact", "subject", "object", "semantic_state", "semantic_constraints")} for f in pool}
                if task["must_preserve_semantic_meaning"] != meaning:
                    raise ValueError("PARAGRAPH_TASK_SOURCE_MUTATION")
                support_only = {e for e in ids if (material[e].get("writability") or writable_fact_diagnostic(material[e]["fact"]))["usage"] == "SUPPORT_ONLY"}
                if (set(task["support_only_evidence_ids"]) != support_only
                        or support_only & set(task["must_cover_fact_ids"])):
                    raise ValueError("INCOMPLETE_WRITABLE_FACT_AS_PRIMARY")
                complete = [material[e] for e in ids if e not in support_only]
                if any(not _compatible(f, other) for i, f in enumerate(complete) for other in complete[i+1:]):
                    raise ValueError("PARAGRAPH_TASK_UNRELATED_MATERIAL")
            assigned.extend(ids)
    if len(assigned) != len(set(assigned)) or set(assigned) != set(facts):
        raise ValueError("PARAGRAPH_TASK_OWNERSHIP_MISMATCH")
