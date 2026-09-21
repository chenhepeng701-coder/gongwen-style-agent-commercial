"""Production paragraph-first Writer. No planning, retrieval or quality rewrite.

The model fills frozen paragraphs; it cannot create their identities or order.
Intermediate Writer metadata stays here, never in FLOW4 extensions.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import math
import re
import time
from difflib import SequenceMatcher
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, Literal, Mapping, get_args

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, ValidationError, field_validator

from app.models.flow4 import StructuredSentence
from app.models.generation import OfficialDraftTask

from app.services.generation.article_outline import (
    ArticleOutline, ArticleOutlineProjector, canonical_json, stable_sha,
)
from app.services.generation.dynamic_budget import DynamicBudgetPlan, build_dynamic_budget
from app.services.generation.fact_policy import FACT_POLICY_ALIGNMENT
from app.services.generation.atomic_evidence import classify_semantic_state, semantic_state_payload
from app.services.generation.paragraph_task import EXPANSION_TEXT, validate_paragraph_tasks
from app.services.generation.writing_reference import FEATURES, reference_guidance

LEGACY_WRITER = "LEGACY"
SECTION_WRITER_EXPERIMENTAL = "SECTION_WRITER_EXPERIMENTAL"
MAX_SECTION_WRITER_BATCHES = 3
SYSTEM_PROMPT = (
    "首要任务是逐段表达CURRENT CONTENT TARGET并写成正常公文段落；WRITING GUIDANCE只指导组织表达，不是正文内容；"
    "Evidence只为具体事实提供支撑，不是逐条压缩清单。"
    "只输出一个严格JSON对象，不输出Markdown或解释。"
)
# Reference the frozen transport owner; never maintain a second enum here.
Flow4SentenceType = StructuredSentence.model_fields["sentence_type"].annotation
_PARAGRAPH_IDENTITY_FIELDS = ("paragraph_id", "paragraph_order", "paragraph_intent_id", "outline_node_id", "section_id")


class SectionWriterContractError(ValueError):
    """Local contract failure; never a public delivery verdict."""

    def __init__(self, code: str, *, location: str = "", report: Any = None):
        self.code, self.location, self.report = code, location, report
        # Do not put model text or source material into exception/log messages.
        super().__init__(f"{code}@{location}")


def select_writer_mode(mode: str = LEGACY_WRITER) -> str:
    if mode not in {LEGACY_WRITER, SECTION_WRITER_EXPERIMENTAL}:
        raise SectionWriterContractError("UNKNOWN_WRITER_MODE")
    return mode


def paragraph_identity(intent_id: str) -> str:
    return "P-" + hashlib.sha256(intent_id.encode("utf-8")).hexdigest()[:24]


_ENUM = re.compile(r"^\s*(?:[一二三四五六七八九十]+(?:是|、|[.．])|[（(][一二三四五六七八九十0-9]+[）)]|\d+[、.．]\s*(?!\d))\s*")
_SLOT = re.compile(r"^(?:focus|goal|writing_focus|writing_goal|展开重点|写作重点|写作目标)\s*[:：]\s*", re.I)


def normalize_writer_text(value: str) -> str:
    """Only remove explicit slot/enumeration prefixes and exact repeated clauses."""
    result: list[str] = []
    for clause in re.split(r"[；;\n]", value):
        cleaned = clause.strip()
        for _ in range(3):
            prior = cleaned
            cleaned = _ENUM.sub("", _SLOT.sub("", cleaned)).strip()
            if cleaned == prior:
                break
        if cleaned and cleaned not in result:
            result.append(cleaned)
    return "；".join(result)


_LEGACY_EVIDENCE_GUIDANCE_FIELDS = (
    "required_evidence_ids", "core_evidence_ids",
    "supporting_evidence_ids", "optional_evidence_ids",
)


def is_paragraph_development_guidance(value: str) -> bool:
    """Keep writing guidance; do not render legacy Evidence-consumption steps.

    M2 may retain classification language for its own trace. Once the legal
    paragraph-local pool is frozen, those labels are not a Writer assignment
    order and must not compete with the Paragraph Goal in the live prompt.
    """
    normalized = normalize_writer_text(value)
    return bool(normalized) and not any(
        field in normalized for field in _LEGACY_EVIDENCE_GUIDANCE_FIELDS
    )


@dataclass(frozen=True)
class WriterInput:
    """Detached canonical snapshots prevent caller/provider mutation of sources."""

    outline_json: str
    budget_json: str
    frozen_json: str
    pack_json: str
    anchor_json: str
    nodes_json: str
    normalization_trace_json: str
    # Request provenance, separate from frozen M1/M2 writing intentions.
    task_title: str = ""

    @property
    def outline(self) -> ArticleOutline:
        return ArticleOutline.model_validate_json(self.outline_json)

    @property
    def budget(self) -> DynamicBudgetPlan:
        return DynamicBudgetPlan.model_validate_json(self.budget_json)

    @property
    def nodes(self) -> list[dict[str, Any]]:
        return json.loads(self.nodes_json)

    @property
    def content_plan(self) -> dict[str, Any]:
        return json.loads(self.frozen_json)["content_plan"]

    @property
    def fact_anchor_pack(self) -> dict[str, Any]:
        return json.loads(self.anchor_json)


def project_writer_budget(inputs: WriterInput, start: int, end: int) -> dict[str, Any]:
    """Writing view of frozen M2 allocations, never a new allocation policy.

    *_words retains the upstream field names. Delivery counts Unicode code
    points, not linguistic words or provider tokens. Soft limits remain
    transferable; only record_actual_usage owns the article budget verdict.
    """
    budget = inputs.budget
    by_id = {n.outline_node_id: n for n in budget.node_budgets}
    nodes = [by_id[n["outline_node_id"]] for n in inputs.nodes[start:end]]
    return {
        "budget_unit": "unicode_characters",
        "total_target_words": budget.total_target_words,
        "total_hard_max_words": budget.total_hard_max_words,
        "batch_target_words": sum(n.target_words for n in nodes),
        "batch_soft_limit_words": sum(n.soft_max_words for n in nodes),
    }


def _writer_semantic_projection(section: Mapping[str, Any], intent: Any) -> dict[str, Any]:
    """Recover topic and development instructions from frozen field ownership.

    business_block/topic name the subject matter, never a factual actor or a
    proven result. Mixed writing_focus, section_goal, relation_basis and usage
    steps cannot authorize content or actors: retain them in frozen provenance.
    Do not attempt to recover a topic by parsing their natural-language text.
    """
    if "paragraph_tasks" in intent.source_fields:
        matches = [t for t in section.get("paragraph_tasks", [])
                   if "paragraph_tasks." + t["task_id"] in intent.source_fields
                   and t["central_idea"] == intent.writing_goal]
        if len(matches) != 1:
            raise SectionWriterContractError("PARAGRAPH_TASK_SOURCE_MISMATCH")
        task = matches[0]
        return {"content_target": [task["central_idea"]],
                "writing_guidance": [EXPANSION_TEXT[c] for c in task["expansion_order"]]}
    chains = {c["chain_id"]: c for c in section.get("work_chain_summaries", [])}
    topics = []
    for cid in intent.work_chain_ids:
        chain = chains.get(cid, {})
        topic = chain.get("topic") or chain.get("business_block")
        if isinstance(topic, str) and topic.strip():
            topics.append(topic.strip())
    if not topics:
        topic = section.get("business_block")
        if isinstance(topic, str) and topic.strip():
            topics.append(topic.strip())
    if not topics:
        raise SectionWriterContractError("M3_CONTENT_TARGET_SOURCE_NOT_AVAILABLE",
                                         location=intent.paragraph_intent_id)
    # expansion_path has an organizational role; unlike writing_focus it is
    # never interpreted as extracted subjects/actions/objects. Keep source order.
    path = section.get("expansion_path", [])
    guidance = _dedupe_exact_strings([path] if isinstance(path, str) else path)
    targets = _dedupe_exact_strings(topics)
    functions = set(section.get("content_functions", []))
    guidance = [value for value in guidance if value not in targets and value not in functions]
    return {"content_target": targets, "writing_guidance": guidance}


def _safe_task_title(value: Any) -> str:
    """Only the normalized request title may supply the article title/topic."""
    if not isinstance(value, str) or not value.strip():
        raise SectionWriterContractError(
            "M3_SAFE_TITLE_SOURCE_NOT_AVAILABLE", location="OfficialDraftTask.title",
            report={"missing_source": "ArticleGenRequest.title -> OfficialDraftTask.title"},
        )
    return value.strip()


def _writer_capacity_guidance(
    frozen: Mapping[str, Any], section: Mapping[str, Any],
) -> dict[str, Any]:
    """Project existing capacity diagnostics, never calculate a writing quota.

    Section diagnostics apply to its frozen paragraphs; article estimates are
    context only, not paragraph allocations. No diagnostic prose, Evidence IDs
    or other sections' material is exposed through this guidance channel.
    """
    capacity = frozen.get("capacity_budget") or {}
    article_fields = ("capacity_gap", "evidence_supported_words", "evidence_supported_word_range")
    section_fields = ("capacity_status", "recommended_capacity_aware_word_budget")
    return {
        "semantic_role": "WRITING_GUIDANCE_ONLY",
        "article": {key: capacity[key] for key in article_fields if key in capacity},
        "section": {key: section[key] for key in section_fields if key in section},
        "estimates_are_binding": False,
        "stop_when_supported_content_exhausted": True,
    }


def build_writer_input(
    outline: ArticleOutline, budget: DynamicBudgetPlan,
    frozen_article_plan: Any, evidence_group_pack: Mapping[str, Any],
    fact_anchor_pack: Mapping[str, Any] | None = None,
    *, task: OfficialDraftTask | None = None,
) -> WriterInput:
    """Consume, verify and polish M2 contracts without changing any M2 owner."""
    frozen = (frozen_article_plan.to_dict() if hasattr(frozen_article_plan, "to_dict")
              else dict(frozen_article_plan))
    # Re-derive only to verify provenance. Never choose different Evidence/nodes.
    verified = ArticleOutlineProjector().project(frozen, evidence_group_pack).outline
    if verified != outline:
        raise SectionWriterContractError("OUTLINE_SOURCE_MISMATCH")
    budget = DynamicBudgetPlan.model_validate_json(budget.model_dump_json())
    if build_dynamic_budget(outline, requested_total_words=budget.total_target_words,
                            total_hard_max_words=budget.total_hard_max_words,
                            policy=budget.policy) != budget:
        raise SectionWriterContractError("BUDGET_SOURCE_MISMATCH")
    if budget.total_target_words != frozen["content_plan"]["target_total"]:
        raise SectionWriterContractError("BUDGET_TOTAL_SOURCE_MISMATCH")
    anchors_pack = dict(fact_anchor_pack or frozen.get("fact_anchor_pack") or {})
    if "fact_anchor_pack" in frozen and anchors_pack != frozen["fact_anchor_pack"]:
        raise SectionWriterContractError("ANCHOR_SOURCE_MISMATCH")
    anchors = {a["evidence_id"]: a for a in anchors_pack.get("fact_anchors", [])}
    if len(anchors) != len(anchors_pack.get("fact_anchors", [])):
        raise SectionWriterContractError("DUPLICATE_ANCHOR_ID")
    groups = {g["group_id"]: g for g in evidence_group_pack["groups"]}
    ownership = {o.evidence_group_id: o for o in outline.mapping_report.evidence_ownership}
    node_budgets = {b.outline_node_id: b for b in budget.node_budgets}
    sources = {s["section_id"]: s for s in frozen["content_plan"]["sections"]}
    if frozen["content_plan"].get("paragraph_task_owner"):
        try:
            validate_paragraph_tasks(frozen["content_plan"], evidence_group_pack)
        except ValueError as exc:
            raise SectionWriterContractError("PARAGRAPH_TASK_SOURCE_MISMATCH") from exc
    seen_evidence: set[str] = set()
    nodes, trace = [], []
    # Frozen FLOW4 requires the original Section identities. Fail before calling
    # a model for a topology that needs a separately authorised transport design.
    if (any(n.level != 1 or len(n.source_section_ids) != 1 for n in outline.nodes)
            or [n.source_section_ids[0] for n in outline.nodes] != list(sources)):
        raise SectionWriterContractError("M3_SCOPE_EXPANSION_REQUIRED", location="outline_topology")
    for node in outline.nodes:
        nb = node_budgets[node.outline_node_id]
        ibs = {b.paragraph_intent_id: b for b in nb.paragraph_intent_budgets}
        sid = node.source_section_ids[0]
        paragraphs = []
        chains = {c["chain_id"]: c for c in sources[sid].get("work_chain_summaries", [])}
        for order, intent in enumerate(node.paragraph_intents):
            ib = ibs[intent.paragraph_intent_id]
            evidence = []
            for gid in intent.evidence_group_ids:
                group, owner = groups[gid], ownership[gid]
                if (owner.primary_outline_node_id != node.outline_node_id
                        or owner.primary_paragraph_intent_id != intent.paragraph_intent_id
                        or owner.source_section_id != sid
                        or owner.semantic_state != group.get("semantic_state", "UNKNOWN")):
                    raise SectionWriterContractError("EVIDENCE_OWNERSHIP_MISMATCH", location=gid)
                facts = {f["evidence_id"]: f for f in group.get("facts", [])}
                if set(facts) != set(group["evidence_ids"]) or len(facts) != len(group.get("facts", [])):
                    raise SectionWriterContractError("EVIDENCE_FACTS_MISMATCH", location=gid)
                for eid in group["evidence_ids"]:
                    if eid in seen_evidence or eid not in anchors or anchors[eid]["section_id"] != sid:
                        raise SectionWriterContractError("EVIDENCE_SCOPE_MISMATCH", location=eid)
                    seen_evidence.add(eid)
                    fact, anchor = facts[eid], anchors[eid]
                    if fact.get("semantic_state", "UNKNOWN") != anchor.get("semantic_state", "UNKNOWN"):
                        raise SectionWriterContractError("SEMANTIC_STATE_SOURCE_MISMATCH", location=eid)
                    if not isinstance(fact.get("fact"), str) or not fact["fact"].strip():
                        raise SectionWriterContractError("EMPTY_EVIDENCE_FACT", location=eid)
                    evidence.append({
                        "evidence_id": eid, "fact": fact["fact"],
                        "semantic_state": fact.get("semantic_state", "UNKNOWN"),
                        "exact_values": fact.get("exact_values", {}),
                        "forbidden_detail_types": anchor.get("forbidden_detail_types", []),
                    })
            goal = normalize_writer_text(intent.writing_goal)
            semantics = _writer_semantic_projection(sources[sid], intent)
            # Keep all frozen writing guidance in source order, including items
            # also present in the goal. Semantic coverage belongs to M4.
            points = [
                normalize_writer_text(v) for v in intent.must_cover
                if is_paragraph_development_guidance(v)
            ]
            allowed_ids = [e["evidence_id"] for e in evidence]
            editorial = next((t for t in sources[sid].get("paragraph_tasks", [])
                              if set(t["supporting_evidence_ids"]) == set(allowed_ids)), None)
            # Older frozen editorial chains already own paragraph grouping.
            # Consume that contract without rerunning M2 or parsing mixed goals.
            editorial_chains = [chains[c] for c in intent.work_chain_ids if c in chains]
            if (editorial is None and editorial_chains
                    and all(c.get("creates_facts") is False
                            and c.get("order_status") == "EDITORIAL_ORDER_ONLY"
                            for c in editorial_chains)):
                editorial = {
                    "task_id": intent.paragraph_intent_id,
                    "section_role": sources[sid]["section_role"],
                    "paragraph_role": intent.paragraph_role,
                    "central_idea": "；".join(semantics["content_target"]),
                    "subtopics": semantics["content_target"], "paragraph_order": order,
                    "supporting_evidence_ids": allowed_ids,
                    # The frozen chain assigns content, not mandatory ID quotas.
                    "must_cover_fact_ids": [],
                    "must_cover_content": semantics["content_target"],
                    "expansion_order": [],
                    "expansion_guidance": semantics["writing_guidance"],
                    "semantic_states": {e["evidence_id"]: e["semantic_state"] for e in evidence},
                    "previous_task_id": None, "next_task_id": None,
                    "responds_to_task_ids": [], "facts_that_must_not_repeat": [],
                    "source_contract": "FROZEN_EDITORIAL_WORK_CHAIN",
                    "source_evidence_order": list(dict.fromkeys(e for c in editorial_chains
                        for e in c.get("ordered_evidence_ids", []) if e in allowed_ids)),
                }
            if editorial:
                for item in evidence:
                    state = semantic_state_payload(anchors[item["evidence_id"]])
                    item["state_contract"] = {k: state[k] for k in ("state_qualifier", "negation_markers", "modality_markers")
                                              if state.get(k)}
            trace.append({"paragraph_intent_id": intent.paragraph_intent_id,
                          "source_fields": intent.source_fields,
                          "original_goal": intent.writing_goal, "writer_goal": goal,
                          "model_visible_semantics": semantics,
                          "mixed_goal_destination": "INTERNAL_TRACE_COMPLETION_ONLY",
                          "must_cover_mapping": [{"source": v, "normalized": normalize_writer_text(v),
                                                  "covered_by_goal": normalize_writer_text(v) in goal.split("；"),
                                                  "rendered_as_paragraph_development_guidance":
                                                      v in semantics["writing_guidance"],
                                                  "destination": (
                                                      "CONTENT_TARGET" if v in semantics["content_target"] else
                                                      "WRITING_GUIDANCE" if v in semantics["writing_guidance"] else
                                                      "CONTENT_FUNCTION" if v in sources[sid].get("content_functions", []) else
                                                      "INTERNAL_TRACE")}
                                                 for v in intent.must_cover]})
            if not goal:
                raise SectionWriterContractError("EMPTY_WRITING_GOAL", location=intent.paragraph_intent_id)
            # Soft maximum is a proportional view of the existing node reserve,
            # not a new budget source and never a paragraph rejection threshold.
            soft_max = ib.target_words + (nb.reserve_share_words * ib.target_words // max(1, nb.target_words))
            chain_refs = []
            for cid in intent.work_chain_ids:
                if cid not in chains:
                    raise SectionWriterContractError("WORK_CHAIN_SOURCE_MISSING", location=cid)
                chain = chains[cid]
                allowed = {e["evidence_id"] for e in evidence}
                chain_refs.append({"chain_id": cid,
                                   "member_evidence_ids": [e for e in chain.get("member_evidence_ids", []) if e in allowed],
                                   "chain_completeness": chain.get("chain_completeness", "")})
            paragraphs.append({
                "paragraph_id": paragraph_identity(intent.paragraph_intent_id),
                "paragraph_order": order, "paragraph_intent_id": intent.paragraph_intent_id,
                "outline_node_id": node.outline_node_id, "section_id": sid,
                "paragraph_role": intent.paragraph_role, "writing_goal": goal,
                # Legacy goal/must_cover stay internal for the frozen completion
                # diagnostic. Only this source-aware projection reaches Writer.
                **semantics,
                "capacity_guidance": _writer_capacity_guidance(frozen, sources[sid]),
                "content_functions": list(dict.fromkeys(
                    value for value in sources[sid].get("content_functions", [])
                    if isinstance(value, str) and value.strip()
                )),
                "must_cover": points, "forbidden_claims": intent.forbidden_claims,
                "allow_general_synthesis": intent.allow_general_synthesis,
                "budget": {"target_words": ib.target_words, "soft_min_words": ib.minimum_contract_words,
                           "soft_max_words": soft_max, "soft_range_basis": "node_reserve_proportional_projection"},
                "work_chain_references": chain_refs, "allowed_evidence": evidence,
                # M2 owns the legal paragraph-local pool. M3 may select within
                # it by Paragraph Goal and budget; upstream required/core labels
                # are not reinterpreted as fixed-ID coverage obligations here.
                "evidence_scope": {"allowed_evidence_ids": allowed_ids},
                "writer_task_contract": ({
                    **editorial,
                    "writing_reference_guidance": reference_guidance(
                        frozen["content_plan"].get("writing_reference", {}), editorial["section_role"]),
                    "paragraph_budget": {"target_words": ib.target_words, "soft_max_words": soft_max},
                } if editorial else None),
            })
        node_evidence = {e["evidence_id"] for p in paragraphs for e in p["allowed_evidence"]}
        node_chain_refs = []
        for cid in node.work_chain_ids:
            if cid not in chains:
                raise SectionWriterContractError("WORK_CHAIN_SOURCE_MISSING", location=cid)
            chain = chains[cid]
            node_chain_refs.append({"chain_id": cid,
                                    "member_evidence_ids": [e for e in chain.get("member_evidence_ids", []) if e in node_evidence],
                                    "chain_completeness": chain.get("chain_completeness", "")})
        nodes.append({"outline_node_id": node.outline_node_id, "section_id": sid,
                      "order": node.order, "macro_role": node.macro_role,
                      "heading_intent": normalize_writer_text(node.heading_intent),
                      "heading_source": node.heading_text, "required": node.required,
                      "target_words": nb.target_words,
                      "soft_range": [nb.soft_min_words, nb.soft_max_words],
                      "work_chain_references": node_chain_refs,
                      "paragraphs": paragraphs})
    return WriterInput(outline.model_dump_json(), budget.model_dump_json(), canonical_json(frozen),
                       canonical_json(evidence_group_pack), canonical_json(anchors_pack),
                       canonical_json(nodes), canonical_json(trace),
                       _safe_task_title(task.title if isinstance(task, OfficialDraftTask) else None))


class _StrictOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WriterSentence(_StrictOutput):
    sentence_type: Flow4SentenceType
    text: StrictStr = Field(min_length=1)
    evidence_ids: tuple[StrictStr, ...]

    @field_validator("sentence_type", mode="before")
    @classmethod
    def normalize_type_case(cls, value: Any) -> Any:
        # Only case-equivalent aliases. Semantic labels have no safe mapping.
        for legal in get_args(Flow4SentenceType):
            if isinstance(value, str) and value.casefold() == legal.casefold():
                return legal
        return value


class WriterParagraph(_StrictOutput):
    paragraph_id: StrictStr
    paragraph_order: StrictInt = Field(ge=0)
    paragraph_intent_id: StrictStr
    outline_node_id: StrictStr
    section_id: StrictStr
    sentences: tuple[WriterSentence, ...] = Field(min_length=1)


class WriterSection(_StrictOutput):
    outline_node_id: StrictStr
    section_id: StrictStr
    heading: StrictStr = Field(min_length=1)
    paragraphs: tuple[WriterParagraph, ...] = Field(min_length=1)


class SectionWriterDraft(_StrictOutput):
    outline_sha: StrictStr
    sections: tuple[WriterSection, ...] = Field(min_length=1)
    # Only batch 1 may language-polish the article title; no extra title call.
    title: StrictStr | None = None


class ParagraphCompletionRecord(_StrictOutput):
    """M3's narrow answer to whether a frozen Paragraph Goal was attempted.

    This is not a style or semantic-quality score. It only rejects the
    unmistakable shape where a multi-function Goal is collapsed into one FACT
    sentence even though the paragraph has safe development capacity.
    """

    paragraph_id: str
    paragraph_intent_id: str
    paragraph_role: str
    completion_status: Literal["COMPLETED", "FALSE_PARAGRAPH_COMPLETION"]
    goal_unit_count: int = Field(ge=1)
    content_functions: tuple[str, ...]
    development_load: int = Field(ge=1)
    allow_general_synthesis: bool
    allowed_evidence_count: int = Field(ge=0)
    used_evidence_count: int = Field(ge=0)
    fact_sentence_count: int = Field(ge=0)
    analysis_sentence_count: int = Field(ge=0)
    actual_words: int = Field(ge=0)
    target_words: int = Field(ge=0)
    budget_context_underdeveloped: bool
    reasons: tuple[str, ...]
    quality_findings: tuple[dict[str, Any], ...] = ()


class WriterCompletionReport(_StrictOutput):
    status: Literal["PASS", "FAIL"]
    code: Literal["PASS", "FALSE_PARAGRAPH_COMPLETION"]
    completed_paragraph_intent_ids: tuple[str, ...]
    false_completion_paragraph_intent_ids: tuple[str, ...]
    paragraphs: tuple[ParagraphCompletionRecord, ...]
    rule_basis: str = (
        "Paragraph Goal/content functions + relevant evidence density + "
        "allow_general_synthesis + paragraph target budget; no fixed sentence "
        "minimum, fixed budget percentage, or Evidence-ID coverage quota"
    )


_CONTENT_FUNCTION_ID = re.compile(r"^[a-z][a-z0-9_]*$")


def _content_chars(text: str) -> str:
    return re.sub(r"[^\w]", "", text)


def _content_pairs(text: str) -> set[str]:
    value = _content_chars(text)
    return {value[i:i+2] for i in range(len(value)-1)}


def writer_quality_findings(paragraph: WriterParagraph, spec: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Inspectable M3 review signals, not fact verdicts or rewrite instructions.

    Alignment is deliberately source-driven, including qualifiers absent from
    exact_values. Missing spans may be legitimate paraphrases: retain excerpts
    for human review, never claim semantic equivalence from a lexical score.
    State classification is reused from its existing owner, never redefined.
    """
    task = spec.get("writer_task_contract")
    if not task:
        return []
    findings = []
    facts = {e["evidence_id"]: e for e in spec["allowed_evidence"]}
    body = "".join(s.text for s in paragraph.sentences)

    def add(code, **details):
        findings.append({"code": code, "review_only": True, **details})

    for eid, fact in facts.items():
        positions = [i for i, s in enumerate(paragraph.sentences) if eid in s.evidence_ids]
        if not positions:
            continue
        expressed = "".join(paragraph.sentences[i].text for i in positions)
        source = _content_chars(fact["fact"])
        output = _content_chars(expressed)
        # No hand-maintained qualifier vocabulary: report source deletions,
        # including responsibility scope, frequency, conditions and time spans.
        missing = [source[i:j] for op, i, j, _, _ in
                   SequenceMatcher(None, source, output, autojunk=False).get_opcodes()
                   if op in {"delete", "replace"} and j-i >= 2]
        if missing:
            add("KEY_QUALIFIER_OMITTED", evidence_id=eid, sentence_indices=positions,
                source=fact["fact"], output=expressed, missing_source_spans=missing,
                certainty="POSSIBLE_SOURCE_DETAIL_LOSS_REQUIRES_REVIEW")
        state = fact["semantic_state"]
        observed = classify_semantic_state(expressed)["semantic_state"]
        # Only directional upgrades are asserted here; UNKNOWN is not proof
        # of lost status, and a paraphrase need not repeat a literal marker.
        if (state in {"PLAN", "PLAN_GOAL", "ONGOING", "NOT_IMPLEMENTED", "GAP", "PROBLEM", "PARTIAL"}
                and observed in {"COMPLETED", "RESULT"}):
            add("SEMANTIC_STATE_NOT_EXPRESSED", evidence_id=eid, sentence_indices=positions,
                expected_state=state, observed_state=observed, output=expressed)

    units = []
    for i, sentence in enumerate(paragraph.sentences):
        for clause in re.split(r"[。！？!?]", sentence.text):
            if _content_chars(clause):
                units.append((i, clause, set(sentence.evidence_ids)))
        # Analyze clauses too: a negative statement must not hide a later plan.
        plan_clauses = [c for c in re.split(r"[，,；;。]", sentence.text)
                        if classify_semantic_state(c)["semantic_state"] == "PLAN"
                        or ("将" in c and classify_semantic_state(c)["semantic_state"] == "UNKNOWN"
                            and not any("将" in e["fact"] for e in facts.values()))]
        supports = [facts[e] for e in sentence.evidence_ids if e in facts]
        for clause in plan_clauses:
            authorized = [e for e in supports if e["semantic_state"] in {"PLAN", "PLAN_GOAL"}]
            # Mere citation of an unrelated PLAN cannot authorize this action.
            if not authorized or not any(len(_content_pairs(clause) & _content_pairs(e["fact"])) >= 2
                                         for e in authorized):
                add("UNAUTHORIZED_PLAN", sentence_indices=[i], output=clause,
                    cited_states=[e["semantic_state"] for e in supports])
        if sentence.sentence_type == "ANALYSIS":
            # Uncited new source-absent clauses need review even when the state
            # classifier cannot recognize their future verb (no new lexicon).
            novelty = _content_pairs(sentence.text) - _content_pairs("".join(e["fact"] for e in facts.values()))
            if len(novelty) >= 4:
                add("UNSUPPORTED_ANALYSIS_REVIEW", sentence_indices=[i], output=sentence.text)
        if len(sentence.evidence_ids) >= 3 and len(re.findall(r"[；;]", sentence.text)) >= 3:
            add("EVIDENCE_LIST_CONCATENATION", sentence_indices=[i], output=sentence.text)

    for index, (i, first, first_ids) in enumerate(units):
        a = _content_chars(first); at = _content_pairs(first)
        for j, second, second_ids in units[index+1:]:
            b = _content_chars(second); bt = _content_pairs(second)
            if min(len(a), len(b)) < 6:
                continue
            shared = at & bt
            same_support = not (second_ids - first_ids) or bool(first_ids & second_ids)
            near = SequenceMatcher(None, a, b, autojunk=False).ratio() >= .65
            containment = len(shared) / max(1, min(len(at), len(bt))) >= .55
            # A genuinely new supported detail blocks the repetition signal.
            source_pairs = _content_pairs("".join(facts[e]["fact"] for e in second_ids if e in facts))
            new_supported = (bt - at) & source_pairs
            if a == b or (same_support and (near or containment) and len(new_supported) < 3):
                add("REPEATED_CORE_FACT", sentence_indices=[i, j], excerpts=[first, second])

    center = _content_pairs(task["central_idea"]) & _content_pairs("".join(e["fact"] for e in facts.values()))
    if center and len(center & _content_pairs(body)) < min(2, len(center)):
        add("CENTRAL_IDEA_NOT_EXECUTED", central_idea=task["central_idea"], output=body)
    important = [facts[e]["fact"] for e in task["must_cover_fact_ids"] if e in facts]
    if important and not any(len(_content_pairs(v) & _content_pairs(body)) >= 2 for v in important):
        add("CENTRAL_IDEA_NOT_EXECUTED", central_idea=task["central_idea"], output=body)
    # Detect a source-by-source rendering across sentences, not only semicolons.
    fact_sentences = [s for s in paragraph.sentences if s.sentence_type == "FACT"]
    singleton = [s for s in fact_sentences if len(s.evidence_ids) == 1 and s.evidence_ids[0] in facts]
    if len(singleton) >= 3 and len(singleton) == len(fact_sentences):
        ids = [s.evidence_ids[0] for s in singleton]
        copied = all(SequenceMatcher(None, _content_chars(s.text),
                     _content_chars(facts[s.evidence_ids[0]]["fact"]), autojunk=False).ratio() >= .8
                     for s in singleton)
        if len(set(ids)) == len(ids) and copied:
            add("EVIDENCE_LIST_CONCATENATION", sentence_indices=[i for i,s in enumerate(paragraph.sentences)
                                                               if s.sentence_type == "FACT"],
                certainty="POSSIBLE_SOURCE_BY_SOURCE_LIST_REQUIRES_REVIEW")
    return findings


def _quality_completion_reasons(paragraph: WriterParagraph, spec: Mapping[str, Any]) -> list[str]:
    return list(dict.fromkeys(f["code"] for f in writer_quality_findings(paragraph, spec)))


def evaluate_writer_completion(
    draft: SectionWriterDraft, inputs: WriterInput,
) -> WriterCompletionReport:
    """Detect only obvious Goal non-execution before M4 receives a Candidate.

    Multiple FACT sentences may complete a paragraph without ANALYSIS, and a
    simple or evidence-constrained Goal may remain concise. Budget is used as
    context derived from the frozen Goal axes, never as a standalone ratio.
    """

    specs = {p["paragraph_id"]: p for n in inputs.nodes for p in n["paragraphs"]}
    records: list[ParagraphCompletionRecord] = []
    for section in draft.sections:
        for paragraph in section.paragraphs:
            spec = specs[paragraph.paragraph_id]
            goal_units = tuple(dict.fromkeys(
                value.strip()
                for value in re.split(r"[；;]", spec["writing_goal"])
                if value.strip()
            )) or (spec["writing_goal"],)
            content_functions = tuple(dict.fromkeys(
                value for value in (spec["paragraph_role"], *spec["must_cover"])
                if _CONTENT_FUNCTION_ID.fullmatch(value)
            ))
            development_load = max(1, len(goal_units), len(content_functions))
            facts = [s for s in paragraph.sentences if s.sentence_type == "FACT"]
            analyses = [s for s in paragraph.sentences if s.sentence_type == "ANALYSIS"]
            used = {eid for sentence in paragraph.sentences for eid in sentence.evidence_ids}
            actual = sum(len(sentence.text) for sentence in paragraph.sentences)
            target = int(spec["budget"]["target_words"])
            # The primary budget context is merely below/at target, not a fixed
            # percentage. Sparse synthesis needs the stricter Goal-axis signal
            # below so a concise evidence-constrained paragraph is not padded.
            budget_context_underdeveloped = actual < target
            sparse_goal_axis_shortfall = actual * development_load < target
            single_fact_only = len(paragraph.sentences) == 1 and len(facts) == 1
            multi_function_goal = development_load > 1
            allowed_count = len(spec["allowed_evidence"])
            reasons: list[str] = _quality_completion_reasons(paragraph, spec)
            if (
                single_fact_only
                and multi_function_goal
                and allowed_count >= 2
                and budget_context_underdeveloped
            ):
                reasons.append("MULTI_FUNCTION_GOAL_COLLAPSED_TO_SINGLE_FACT")
            if (
                single_fact_only
                and multi_function_goal
                and allowed_count < 2
                and spec["allow_general_synthesis"]
                and sparse_goal_axis_shortfall
            ):
                reasons.append("GENERAL_SYNTHESIS_ALLOWED_BUT_GOAL_DEVELOPMENT_ABSENT")
            records.append(ParagraphCompletionRecord(
                paragraph_id=paragraph.paragraph_id,
                quality_findings=tuple(writer_quality_findings(paragraph, spec)),
                paragraph_intent_id=paragraph.paragraph_intent_id,
                paragraph_role=spec["paragraph_role"],
                completion_status=("FALSE_PARAGRAPH_COMPLETION" if reasons else "COMPLETED"),
                goal_unit_count=len(goal_units), content_functions=content_functions,
                development_load=development_load,
                allow_general_synthesis=bool(spec["allow_general_synthesis"]),
                allowed_evidence_count=allowed_count, used_evidence_count=len(used),
                fact_sentence_count=len(facts), analysis_sentence_count=len(analyses),
                actual_words=actual, target_words=target,
                budget_context_underdeveloped=budget_context_underdeveloped,
                reasons=tuple(reasons),
            ))
    failed = tuple(r.paragraph_intent_id for r in records
                   if r.completion_status == "FALSE_PARAGRAPH_COMPLETION")
    completed = tuple(r.paragraph_intent_id for r in records
                      if r.completion_status == "COMPLETED")
    return WriterCompletionReport(
        status="FAIL" if failed else "PASS",
        code="FALSE_PARAGRAPH_COMPLETION" if failed else "PASS",
        completed_paragraph_intent_ids=completed,
        false_completion_paragraph_intent_ids=failed,
        paragraphs=tuple(records),
    )


def measure_writer_usage(draft: SectionWriterDraft, inputs: WriterInput) -> dict[str, Any]:
    """Measure original prose, including frozen FLOW4 layout; never rewrite it.

    Shared by per-call diagnostics and the pre-safety M3 usage recorder. The
    final rendered delivery is still measured separately after Fact Safety.
    Local import avoids generator -> SectionWriter's existing import cycle.
    """
    from app.services.generation.generator import count_official_draft_words

    count = count_official_draft_words
    specs = {p["paragraph_id"]: p for n in inputs.nodes for p in n["paragraphs"]}
    budgets = {n.outline_node_id: n for n in inputs.budget.node_budgets}
    first_node = inputs.nodes[0]["outline_node_id"]
    paragraphs, nodes = [], []
    for section in draft.sections:
        if section.outline_node_id not in budgets:
            raise SectionWriterContractError("WRITER_USAGE_IDENTITY_UNKNOWN")
        body = 0
        for paragraph in section.paragraphs:
            spec = specs.get(paragraph.paragraph_id)
            if spec is None or any(getattr(paragraph, f) != spec[f] for f in _PARAGRAPH_IDENTITY_FIELDS):
                raise SectionWriterContractError("WRITER_USAGE_IDENTITY_UNKNOWN")
            actual = sum(count(s.text) for s in paragraph.sentences)
            body += actual
            paragraphs.append({"paragraph_id": paragraph.paragraph_id,
                               "paragraph_intent_id": paragraph.paragraph_intent_id,
                               **spec["budget"], "actual_words": actual,
                               "delta_words": actual - spec["budget"]["target_words"]})
        # Same title/heading/separator attribution as the existing M3 recorder:
        # paragraph line breaks local; title + all inter-block breaks on node 1.
        overhead = count(section.heading) + count("\n" * len(section.paragraphs))
        if section.outline_node_id == first_node:
            overhead += count(draft.title) + count("\n\n" * len(inputs.nodes))
        nb = budgets[section.outline_node_id]
        nodes.append({"outline_node_id": section.outline_node_id,
                      "target_words": nb.target_words, "soft_max_words": nb.soft_max_words,
                      "body_words": body, "layout_words": overhead, "actual_words": body + overhead,
                      "delta_words": body + overhead - nb.target_words})
    return {"budget_unit": "unicode_characters", "paragraph_usage": paragraphs, "node_usage": nodes,
            "total_actual_words": sum(n["actual_words"] for n in nodes),
            "count_basis": "Writer characters plus frozen FLOW4 heading/title/separator layout"}


@dataclass(frozen=True)
class WriterContextLimits:
    max_prompt_chars: int = 24000
    max_context_tokens: int = 16000
    max_output_tokens: int = 7000
    max_paragraphs: int = 16
    # Unitless execution-load guard. It complements, rather than replaces,
    # provider context/token limits. The score is defined below from only
    # paragraph-local prompt inputs and is never a prose-quality score.
    max_batch_semantic_load: int = 140

    def __post_init__(self):
        if min(self.max_prompt_chars, self.max_context_tokens, self.max_output_tokens,
               self.max_paragraphs, self.max_batch_semantic_load) <= 0:
            raise SectionWriterContractError("INVALID_CONTEXT_LIMITS")
        if self.max_prompt_chars > 30000:
            raise SectionWriterContractError("PROMPT_CONTEXT_GUARD_EXCEEDED")


@dataclass(frozen=True)
class WriterBatch:
    batch_id: str
    node_ids: tuple[str, ...]
    paragraph_ids: tuple[str, ...]
    target_words: int
    evidence_count: int
    prompt_chars: int
    estimated_prompt_tokens: int
    estimated_output_tokens: int
    prompt_sha256: str
    prompt: str
    semantic_load: int = 0
    # Adapter's canonical validation-only scope does not build a writer prompt.
    budget_projection_json: str = "{}"

    def summary(self) -> dict[str, Any]:
        return {**{k: v for k, v in vars(self).items() if k not in {"prompt", "budget_projection_json"}},
                "budget_contract": json.loads(self.budget_projection_json),
                "provider_output_cap_tokens": None,
                "provider_output_cap_basis": "not_set_current_provider_interface_has_no_output_cap",
                "token_estimates_are_planning_heuristics_not_budget_units": True}


@dataclass(frozen=True)
class WriterBatchPlan:
    outline_sha: str
    budget_sha: str
    batches: tuple[WriterBatch, ...]
    plan_sha: str

    def summary(self) -> dict[str, Any]:
        return {"outline_sha": self.outline_sha, "budget_sha": self.budget_sha,
                "batch_count": len(self.batches), "batches": [b.summary() for b in self.batches],
                "plan_sha": self.plan_sha}


def paragraph_evidence_aliases(inputs: WriterInput) -> dict[str, dict[str, str]]:
    """Wire-only address book; canonical evidence and M2 ownership stay intact.

    Pn is the frozen article paragraph ordinal (not batch/response order), Em
    is its frozen allowed-evidence ordinal. A qualified alias cannot be copied
    to another paragraph and silently acquire a different global identity.
    Only the paragraph-local keys, never this address book, enter the prompt.
    """
    result = {}
    for ordinal, spec in enumerate((p for n in inputs.nodes for p in n["paragraphs"]), 1):
        ids = spec["evidence_scope"]["allowed_evidence_ids"]
        facts = [e["evidence_id"] for e in spec["allowed_evidence"]]
        scope = spec["evidence_scope"]
        if (set(scope) != {"allowed_evidence_ids"}
                or len(ids) != len(set(ids)) or len(facts) != len(set(facts))
                or set(ids) != set(facts)
                or spec["paragraph_id"] in result):
            raise SectionWriterContractError("WRITER_EVIDENCE_TRANSPORT_SCOPE_INVALID",
                                             location=spec["paragraph_id"])
        result[spec["paragraph_id"]] = {f"P{ordinal}/E{i}": eid for i, eid in enumerate(ids, 1)}
    return result


def _dedupe_exact_strings(values: Any) -> list[str]:
    """Prompt-only exact de-duplication; source contracts remain untouched."""
    return list(dict.fromkeys(value for value in values if isinstance(value, str) and value))


@dataclass(frozen=True)
class _ParagraphWritingUnit:
    """Private execution boundary for one frozen ParagraphIntent.

    The JSON contains one Goal, one budget, one legal Evidence pool and only
    evidence-free neighbour context. It is neither a production stage nor a
    public schema. Batches may transport several units but cannot redefine one.
    """

    node_index: int
    paragraph_id: str
    prompt_json: str
    evidence_count: int
    guidance_count: int
    forbidden_claim_count: int
    prompt_chars: int
    semantic_load: int

    @property
    def prompt_payload(self) -> dict[str, Any]:
        return json.loads(self.prompt_json)


def _build_paragraph_writing_units(inputs: WriterInput) -> tuple[_ParagraphWritingUnit, ...]:
    """Materialize paragraph-local tasks before any batch is planned."""
    nodes = inputs.nodes
    sources = {s["section_id"]: s for s in inputs.content_plan["sections"]}
    intents = {p.paragraph_intent_id: p for n in inputs.outline.nodes for p in n.paragraph_intents}
    aliases = paragraph_evidence_aliases(inputs)
    flattened = [
        (node_index, node, paragraph)
        for node_index, node in enumerate(nodes)
        for paragraph in node["paragraphs"]
    ]
    units: list[_ParagraphWritingUnit] = []
    for ordinal, (node_index, node, paragraph) in enumerate(flattened):
        local = {eid: alias for alias, eid in aliases[paragraph["paragraph_id"]].items()}
        allowed_evidence = [
            {**{k: v for k, v in evidence.items() if k != "state_contract"},
             "evidence_id": local[evidence["evidence_id"]]}
            for evidence in paragraph["allowed_evidence"]
        ]
        chains = []
        for chain in (*paragraph["work_chain_references"], *node.get("work_chain_references", [])):
            members = [local[eid] for eid in chain["member_evidence_ids"] if eid in local]
            projected = {**chain, "member_evidence_ids": members}
            if members and projected not in chains:
                chains.append(projected)

        def neighbour_heading(index: int) -> str | None:
            if not 0 <= index < len(flattened):
                return None
            return flattened[index][1]["heading_intent"]

        budget = dict(paragraph["budget"])
        budget.pop("soft_range_basis", None)
        must_cover = _dedupe_exact_strings(paragraph["must_cover"])
        content_functions = _dedupe_exact_strings(paragraph.get("content_functions", []))
        forbidden_claims = _dedupe_exact_strings(paragraph["forbidden_claims"])
        # Re-project even when replaying an older frozen WriterInput whose
        # nodes still contain the pre-separation goal and must_cover fields.
        semantics = _writer_semantic_projection(sources[paragraph["section_id"]],
                                                intents[paragraph["paragraph_intent_id"]])
        payload = {
            "content_target": semantics["content_target"],
            "paragraph_role": paragraph["paragraph_role"],
            "content_functions": content_functions,
            "writing_guidance": semantics["writing_guidance"],
            "capacity_guidance": _writer_capacity_guidance(
                json.loads(inputs.frozen_json), sources[paragraph["section_id"]]),
            "writing_evidence_kind": "AVAILABLE SUPPORTING MATERIAL",
            "allowed_evidence": allowed_evidence,
            "work_chain_references": chains,
            "evidence_scope": {
                key: [local[eid] for eid in ids]
                for key, ids in paragraph["evidence_scope"].items()
            },
            "forbidden_claims": forbidden_claims,
            "allow_general_synthesis": paragraph["allow_general_synthesis"],
            "output_contract": "仅返回身份字段及sentences；FACT引用本段别名，ANALYSIS的evidence_ids为空。",
            "task_boundary": "CURRENT_PARAGRAPH_TASK",
            **{field: paragraph[field] for field in _PARAGRAPH_IDENTITY_FIELDS},
            "coherence_context": {
                "context_kind": "COHERENCE_CONTEXT",
                "previous_heading": neighbour_heading(ordinal - 1),
                "next_heading": neighbour_heading(ordinal + 1),
            },
            "budget": budget,
        }
        task = paragraph.get("writer_task_contract")
        if task:
            for evidence, original in zip(allowed_evidence, paragraph["allowed_evidence"]):
                state = original.get("state_contract", {})
                evidence["state_contract"] = {k: state[k] for k in ("state_qualifier", "negation_markers", "modality_markers")
                                              if state.get(k)}
            def neighbour_task(index: int) -> dict[str, Any] | None:
                if not 0 <= index < len(flattened):
                    return None
                other = flattened[index][2]
                return {"paragraph_id": other["paragraph_id"],
                        "central_idea": other["content_target"]}

            # All content pointers are aliases within this paragraph. Cross-task
            # relations carry editorial identities only, never neighbour facts.
            payload["writer_task_contract"] = {
                "version": "writer-task-2", "source_role": "ORGANIZATION_ONLY_NOT_FACT_AUTHORIZATION",
                "task_id": task["task_id"], "section_role": task["section_role"],
                "paragraph_role": task["paragraph_role"], "central_idea": task["central_idea"],
                "subtopics": task["subtopics"],
                "paragraph_order": task["paragraph_order"],
                "must_cover_facts": [local[e] for e in task["must_cover_fact_ids"]],
                "must_cover_content": task.get("must_cover_content", [task["central_idea"]]),
                "supporting_evidence_pool": [local[e] for e in task["supporting_evidence_ids"]],
                "expansion_order": task.get("expansion_guidance") or [EXPANSION_TEXT[c] for c in task["expansion_order"]],
                "relation_to_previous": neighbour_task(ordinal - 1),
                "relation_to_next": neighbour_task(ordinal + 1),
                "responds_to_task_ids": task["responds_to_task_ids"],
                "facts_that_must_not_repeat": "其他段落承担的事实不在本段池内；本段已表达事实不得换措辞重复总结",
                "plan_authorized_evidence": [local[e["evidence_id"]] for e in paragraph["allowed_evidence"]
                                             if e["semantic_state"] in {"PLAN", "PLAN_GOAL"}],
                "stop_when_supported_content_exhausted": True,
                "semantic_state": {local[e]: s for e, s in task["semantic_states"].items()},
                "paragraph_budget": budget,
                "writing_reference_guidance": {
                    "feature_codes": [g["feature_code"] for g in task["writing_reference_guidance"]],
                    "reference_ids": sorted({r for g in task["writing_reference_guidance"] for r in g["reference_ids"]}),
                },
            }
            if not task["writing_reference_guidance"]:
                payload["writer_task_contract"].pop("writing_reference_guidance")
            if task.get("source_evidence_order"):
                payload["writer_task_contract"]["source_evidence_order"] = [local[e] for e in task["source_evidence_order"]]
            if task["subtopics"] == [task["central_idea"]]:
                payload["writer_task_contract"].pop("subtopics")
            # New contract owns these meanings once. Removing duplicate views
            # reduces prompt pressure without deleting any organizational task.
            for field in ("content_functions", "writing_guidance", "work_chain_references", "coherence_context", "capacity_guidance",
                          "content_target", "paragraph_role", "budget", "evidence_scope",
                          "output_contract", "writing_evidence_kind"):
                payload.pop(field, None)
        # Model order is semantic, not alphabetical. Canonical source snapshots
        # and their hashes continue to use canonical_json unchanged.
        prompt_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        # Raw task/constraint counts stay inspectable. The k-char term captures
        # additional paragraph-local schema pressure without reading output or
        # case IDs. No Evidence is required to be consumed by this score.
        evidence_count = len(allowed_evidence)
        # Preserve the existing frozen workload estimate and Batch boundaries;
        # tracing a requirement does not make it a model-visible content target.
        guidance_count = len(must_cover) + len(content_functions)
        forbidden_count = len(forbidden_claims)
        semantic_load = (
            1 + evidence_count + guidance_count + forbidden_count
            + math.ceil(len(prompt_json) / 1000)
        )
        units.append(_ParagraphWritingUnit(
            node_index=node_index,
            paragraph_id=paragraph["paragraph_id"],
            prompt_json=prompt_json,
            evidence_count=evidence_count,
            guidance_count=guidance_count,
            forbidden_claim_count=forbidden_count,
            prompt_chars=len(prompt_json),
            semantic_load=semantic_load,
        ))
    return tuple(units)


def _paragraph_local_nodes(inputs: WriterInput, start: int, end: int) -> list[dict[str, Any]]:
    """Render pre-built Writing Units inside minimal section containers."""
    source_nodes = inputs.nodes
    units = _build_paragraph_writing_units(inputs)
    by_node: dict[int, list[dict[str, Any]]] = {}
    for unit in units:
        by_node.setdefault(unit.node_index, []).append(unit.prompt_payload)
    return [
        {
            "outline_node_id": node["outline_node_id"],
            "section_id": node["section_id"],
            "order": node["order"],
            "macro_role": node["macro_role"],
            "heading_intent": node["heading_intent"],
            "heading_source": node["heading_source"],
            "target_words": node["target_words"],
            "paragraphs": by_node[node_index],
        }
        for node_index, node in enumerate(source_nodes[start:end], start=start)
    ]


def _batch_prompt(inputs: WriterInput, start: int, end: int, batch_number: int) -> str:
    nodes, outline = inputs.nodes, inputs.outline
    payload = {
        "outline_sha": outline.outline_sha,
        "batch_id": f"BATCH-{batch_number:02d}",
        "article_content": {
            "title_source": _safe_task_title(inputs.task_title),
            "source_field": "OfficialDraftTask.title",
            "source_role": "USER_REQUEST_TITLE_OR_TOPIC",
        },
        "global_writing_guidance": {
            "title": {"semantic_role": "TITLE_WRITING_GUIDANCE", "text": outline.title_intent},
            "introduction": {"semantic_role": "INTRODUCTION_WRITING_GUIDANCE",
                             "text": outline.introduction_intent if start == 0 else ""},
        },
        "global_context": {
            "document_type": outline.document_type,
            "macro_structure": [{"order": n["order"], "role": n["macro_role"]} for n in nodes],
            "previous_node_role": nodes[start-1]["macro_role"] if start else None,
            "next_node_role": nodes[end]["macro_role"] if end < len(nodes) else None,
            "global_tone": "成熟、克制、连贯的机关公文；不要解释写作过程。",
            **project_writer_budget(inputs, start, end),
            "write_article_opening_here": start == 0, "write_article_closing_here": end == len(nodes),
        },
        "nodes": _paragraph_local_nodes(inputs, start, end),
        "evidence_transport": "paragraph-local-alias/v1",
        "fact_policy": {p.architecture_fact_class: p.prompt_rules for p in FACT_POLICY_ALIGNMENT.fact_classes},
        "placeholder_policy": FACT_POLICY_ALIGNMENT.placeholder_confirmation_decision,
        # Current provider has no response_format argument. Expose the exact
        # canonical enum schema in the prompt; local validation remains strict.
        "sentence_type_schema": StructuredSentence.model_json_schema()["properties"]["sentence_type"],
    }
    references = inputs.content_plan.get("writing_reference", {})
    if references.get("selected"):
        codes = {code for source in references["selected"] for code in source["feature_codes"] if code in FEATURES}
        payload["writing_reference"] = {
            "source_role": "WRITING_REFERENCE_NOT_CONTENT_EVIDENCE",
            "feature_definitions": {code: FEATURES[code] for code in sorted(codes)},
            "source_features": {source["reference_id"]: [c for c in source["feature_codes"] if c in FEATURES]
                                for source in references["selected"]},
        }
    execution_rules = (
        "【本次成文执行】先在段内区分已有Evidence的动作、对象、限定和状态，再围绕中心组织句群；不返回思考过程。"
        "主题句尽量和第一项实质动作或问题合写，不先复述任务标题再复述材料，末句有新信息才保留。"
        "同源重复事实合并引用，同一命题不以概括句再说一次；证据用尽立即自然结束，即使明显短于预算。"
        "保留所用事实的完整含义：exact_values不是限定全集，必须核对fact原文中的主体、对象、时间范围、"
        "数量语境、频次、否定、条件及状态。可改句法，不能把具体限定改为宽泛概括。"
        "state_contract来自既有Anchor，不是新事实；UNKNOWN不授权自行升级状态。"
        "仅plan_authorized_evidence列出的证据能够支持对应计划，且只授权其原有事项、对象、目标和期限。"
        "NEXT_STEP标题、段落角色或邻段有计划，不授权把本段NOT_IMPLEMENTED/PROBLEM/GAP改成未来承诺。"
        "一般性ANALYSIS只能承接，不得安排新工作；无对应PLAN依据时不写将、进一步建立、持续推进等新承诺。"
        "多条材料围绕同一工作过程组织：中心及动作或问题→必要对象和具体内容→已有进度/结果或有依据的安排。"
        "逻辑关联只用于编排，不把并列材料擅改为因果；不依次改述各条Evidence再附总结。"
        "输出前逐段核对原文限定、中心、邻段分工、重复和计划授权；只在本次成文内完成，不追加调用。"
    )
    rules = (
        "【成熟段落任务】有writer_task_contract时，它是M2冻结的组织施工图，central_idea对应下文content_target，"
        "expansion_order对应writing_guidance，paragraph_budget对应budget，supporting_evidence_pool对应evidence_scope。"
        "逐段执行central_idea与expansion_order，"
        "不得缩减为章节大意。must_cover_facts指本段主要事实的展开位置，允许语义等价合并；不要求逐证据照抄。"
        "CONTENT_EVIDENCE仅指本段allowed_evidence；WRITING_REFERENCE仅指writing_reference_guidance中的受控写法特征，"
        "只改变组织方式，不提供单位、时间、数据、主体、项目或成果。禁止照抄参考文句。"
        "同一核心事实只表达一次。后一句必须提供新事实、新限定、新关系或必要承接；材料用完立即结束，"
        "不在首句、句中、末句三次概括同一个不足，不靠同义归纳补预算。"
        "压缩时优先保留决定含义的时间及范围、频次、数量语境、状态、否定、条件、主体与对象；"
        "例如‘某年及以前’不得只留下‘某年’，‘基本完成’不得变成‘完成’，计划目标不得成为现实结果。"
        "按中心组织正常句群，不把事实逐条以分号连接后加万能总结。问题按约束及具体表现组织；"
        "下步按责任、机制、能力、技术等本段实际主题承接，不因组织关系声称存在证据未支持的因果。"
        "在本次输出前自查：重复概括、限定丢失、偏离中心、清单拼接；在本次正常写作内修正，不追加调用。"
        "【第一层任务：表达CURRENT CONTENT TARGET】按nodes→paragraphs逐段成稿。每个task_boundary=CURRENT_PARAGRAPH_TASK"
        "都是独立Writing Unit；Batch只负责同次传输，不形成共享写作任务、共享Evidence池或跨段约束池。"
        "coherence_context仅用于衔接且不具备事实授权。对每个段落，先理解content_target（CURRENT CONTENT TARGET）正文要讲的主题，"
        "再结合paragraph_role及content_functions理解必要内容功能，按writing_guidance（WRITING GUIDANCE）组织表达，"
        "再从allowed_evidence（AVAILABLE SUPPORTING EVIDENCE）按需选择材料支撑具体事实，最后写成正常公文段落。"
        "content_target只确定主题，不证明其中的成效或现实主体；具体事实仍须本段证据支持。"
        "WRITING GUIDANCE只说明怎样写，不能作为正文要讲的内容，不要在正文解释本段写法、展开顺序或本文应围绕什么。"
        "global_writing_guidance中的TITLE_WRITING_GUIDANCE只指导标题组织，INTRODUCTION_WRITING_GUIDANCE只指导总起和承接；"
        "两者都不是CONTENT，不得复制或改述为标题、首句或正文。title的内容来源只能是article_content.title_source，"
        "不得从写作意图、章节标题或参考结构猜造全文标题。用户标题/主题只确定写作范围，不授权新增现实事实。"
        "开头只能依据已知业务主题、当前content_target、本段合法Evidence以及不新增事实的一般性承接；"
        "不得写成‘本文……’‘本次总结围绕……’‘本部分……’‘按照……写法……’等写作过程说明。"
        "Evidence是写作材料，不是逐条压缩清单，Evidence消费不得取代Goal完成。"
        "下文Goal仅指CURRENT CONTENT TARGET；内容功能是组织职责，不是新增事实授权，也不是需要逐项写入正文的清单。"
        "Paragraph Goal完成意味着其主要内容功能已经在正文中形成基本展开；不能把已引用Evidence或存在一个FACT句等同于完成，"
        "也不能把消费完Evidence等同于完成。"
        "Goal包含多个内容功能且材料中等或充足时，应按事实对象、工作过程或推进逻辑形成清晰层次，必要时自然使用多个句子和多个FACT，"
        "并在允许时加入不新增具体事实的承接或总结；不得把多条Evidence压进一个超长FACT句后结束。"
        "材料稀疏或Goal简单时允许克制短写，不为凑句数或凑字数机械灌水。句子数量按任务自然决定，不设固定句数、固定预算比例，"
        "也不要求FACT固定搭配ANALYSIS或每段必须出现ANALYSIS。"
        "capacity_guidance是已冻结的材料容量诊断：article仅为全文背景，section适用于当前段落所在章节；"
        "MATERIALLY_SPARSE、FACT_INSUFFICIENT、FACT_SPARSE或evidence_constrained表示材料只支持有限展开，允许合理短写。"
        "容量建议及supported range是非约束性估计，不是新字数上限或最低长度，不按Evidence数量换算字数。"
        "当CONTENT TARGET、Evidence与合法general synthesis已无新的受支持内容可写时，立即结束本段，可以明显短于target。"
        "content_functions和writing_guidance不要求逐项补齐：只表达实际有支撑的功能，未获Evidence支持的结果、指标、创新等不得用ANALYSIS猜补。"
        "若段落首句不能从标题、上文或明确语境自然继承主体，应先建立清晰的主题锚点或合法主体锚点；"
        "不要求句句显式写主语，也不得为建立锚点发明机构、人员、项目或其他具体主体。"
        "只有Evidence明确支持的主体才可作为现实主体；时间、动作、方式和业务主题不能自动升级为主体。"
        "Evidence没有明确主体时允许主题锚点表达，不得自行补出我局、有关部门、工作专班等事实主体。"
        "allow_general_synthesis为true时，可在不新增具体事实的前提下完成Goal所需的一般性承接、过程组织、归纳、总结或安排逻辑；"
        "这仅是必要表达的许可，不承担填满剩余预算的义务。稀疏段落始终遵循事实支持边界优先于内容需要，内容需要优先于篇幅目标；"
        "不得为了靠近target重复事实、重复总结、推演效果或因果、发明过程或责任主体。"
        "为false时不得为扩写而生成ANALYSIS。PROBLEM缺少具体问题Evidence时，只能作不引入具体问题事实的克制概括；"
        "PLAN/PENDING状态的NEXT_STEP可展开安排顺序和推进逻辑，但不得写成已经完成。"
        "只把组织指导用于行文，不要求返回逐项句子索引自证表。"
        "不得增删或重排节点、段落；所有身份字段逐字复制。标题语言化只能发生在本次调用内，不能改变章节功能。"
        "【输出段落合同】paragraphs中的sentences数组允许并应按Goal需要容纳正常多句展开，包括多个FACT以及允许时必要的ANALYSIS；"
        "一个ParagraphIntent不等于一个核心FACT句。sentences是安全元数据，不是FACT清单，不要求每个FACT后接ANALYSIS。"
        "每段evidence_scope独立：只能用本段allowed_evidence_ids对应的allowed_evidence；"
        "evidence_id是段落局部别名Pn/Em，必须完整复制到evidence_ids；不同段落的E1不是同一证据。"
        "不得省略或改写Pn/前缀，不得返回全局Evidence ID，别名不能出现在正文text中。"
        "禁止借用同batch邻段证据、共享证据池、重分配或扩大作用域。合法池内证据均按Goal按需选择，不要求全量消费；"
        "AVAILABLE SUPPORTING MATERIAL是可选支撑池，不是TO-DO LIST；可以全部使用，也可以只用一部分，"
        "全部使用本身不构成错误，未使用本身也不构成错误；只在具体事实需要支撑时使用，不得为消费全部材料破坏段落结构。"
        "没有明确选择信息时保持中性，不假定材料顺序代表优先级或相关性评分，不设置最低使用数量。"
        "未使用合法材料不得制造失败，任何上游材料分类都不构成逐Evidence ID硬覆盖门。"
        "同证据可支持多句，邻段引用不算本段使用。"
        "sentence_type只能取sentence_type_schema正式enum："
        + canonical_json(list(get_args(Flow4SentenceType))) + "。paragraph_role、writing_guidance、Work Chain标签仅是写法上下文，禁止作句型或正文。"
        "FACT必须引用实际表达的合法evidence_ids；ANALYSIS必须为空数组，只允许不新增事实的综合表达。"
        "F0禁止虚构主体、事项、数字、成果、因果或把PLAN/PENDING变为COMPLETED；F1不得补写不确定具体值；"
        "F2允许无新增事实的承接、归纳、概括，不必伪造证据。正文禁止XX、TODO、【待补充】等字面占位符。"
        "预算是必须执行的写作约束。预算字段虽名为words，单位均为字符：汉字、标点、数字、字母、空格和换行各计1，不是英文词数或token；"
        "JSON键、身份字段和evidence_ids不计正文篇幅。"
        "每段budget的target_words是可用写作空间和指导线，不是最低交付长度，不要求材料不足时尽量写满；"
        "soft_min_words至soft_max_words为可调剂范围；最低值只保障段落存在，不是建议写作长度，也不构成机械最低字数；"
        "不要按固定比例填充，也不要逐项扩写到上限。"
        "节点target_words含本节标题和段落换行，须在节点预算内为其留量。"
        "本批batch_target_words同样只是空间指导，以batch_soft_limit_words为软上限；全文total_target_words和total_hard_max_words"
        "是所有批次合计，不能每批各用一遍。第一节点还承担全文标题及章节分隔符篇幅。"
        "允许局部超目标并用其他段落释放的预算调剂，但不得假定并发批次已释放预算；全文含标题、标点和换行必须不超hard max。"
        "成稿前在本次输出内统筹篇幅；保留所有required段落，不得靠删段、删事实或极端压缩凑数。"
        "只在第一批第一段写全文总起；中间批次直接衔接章节任务，不重写总起或全文结尾。"
        "PROBLEM必须保持问题状态，NEXT_STEP是同一篇文章的下一步，不另写一篇文章。"
        "只输出outline_sha、sections、title三个根字段。sections逐项含outline_node_id、section_id、heading、paragraphs；"
        "paragraphs逐项仅含paragraph_id、paragraph_order、paragraph_intent_id、outline_node_id、section_id、sentences；"
        "sentences逐项仅含sentence_type、text、evidence_ids。text不得嵌入标题或空行分段。"
        + ("title仅依据article_content.title_source整理为文章标题，不新增事实，不复述TITLE_WRITING_GUIDANCE。"
           if start == 0 else "title必须为null，沿用第一批标题。")
    )
    if any(p.get("writer_task_contract") for n in nodes[start:end] for p in n["paragraphs"]):
        # One coherent instruction path for mature tasks, instead of appending
        # their contract to the full legacy field-by-field instruction manual.
        rules = execution_rules + (
            "【成熟段落任务】writer_task_contract是冻结的组织施工图，不授权事实。"
            "按central_idea、paragraph_role、must_cover_facts和expansion_order组织本段，"
            "结合relation_to_previous/next区分邻段职责，不把中心意思或写法说明直接复制进正文。"
            "must_cover_content是主题内容，不是已发生的事实。无必写ID时按中心选择主要材料，不新增必用配额。"
            "CONTENT_EVIDENCE仅指本段allowed_evidence。WRITING_REFERENCE仅指受控写法特征，不提供事实。"
            "邻段信息只作组织衔接，不提供主体、状态、成果或因果。"
            "同源同义材料可以合并引用；材料原有时间、范围、频次、责任和完成条件必须随事实保留。"
            "每个task_boundary=CURRENT_PARAGRAPH_TASK独立，Batch没有共享证据池。"
            "FACT只引用本段supporting_evidence_pool中的完整Pn/Em别名，不能借用邻段、改写别名或写入正文。"
            "ANALYSIS的evidence_ids必须为空；allow_general_synthesis=false时不得生成ANALYSIS。"
            "ANALYSIS不是空余篇幅填充物：只有不新增事实且必要的承接可用；不推断业务承诺、成果或因果。"
            "一般组织词不能替代被删去的具体信息。不得根据标题或角色改变证据状态。"
            "没有主体依据时用业务主题承接，不发明我局、专班、责任部门等主体。"
            "完整正文优先，不输出写作过程、证据说明、评价或【待补充】等占位符。"
            "paragraph_budget单位为字符，target_words是空间指导，不是最低篇幅；可以自然短于目标。"
            "不设固定句数、Evidence最低使用数量或FACT/ANALYSIS搭配。材料耗尽可以结束。"
            "同一段可多个FACT，也可合句，但不能逐条材料换词排列；不能靠抽象总结占预算。"
            "保留全部冻结段落及顺序，节点预算含标题和换行；全文标题、正文和分隔符合计不得超过total_hard_max_words。"
            "第一批第一段自然总起，不解释本文如何写；中间批次直接承接，不另写全文开头或结尾。"
            "标题内容只能依据article_content.title_source；global_writing_guidance仅指导写法，不能成为正文或事实。"
            "每段必须复制paragraph_id、paragraph_order、paragraph_intent_id、outline_node_id、section_id，"
            "仅再输出sentences。每句仅含sentence_type、text、evidence_ids。"
            "sentence_type严格使用sentence_type_schema的枚举："
            + canonical_json(list(get_args(Flow4SentenceType))) + "。text不嵌入标题或空行。"
            "只输出outline_sha、sections、title三个根字段；sections含outline_node_id、section_id、heading、paragraphs。"
            + ("title仅根据用户主题整理，不增加事实。" if start == 0 else "title必须为null。")
        )
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    # Fail closed on unexpected global IDs embedded in guidance/facts too; do
    # not rewrite source prose or silently publish a second shared address pool.
    if any(eid in serialized for mapping in paragraph_evidence_aliases(inputs).values()
           for eid in mapping.values()):
        raise SectionWriterContractError("WRITER_GLOBAL_EVIDENCE_ID_EXPOSED")
    return rules + "\n" + serialized


def _make_batch(inputs: WriterInput, start: int, end: int, index: int) -> WriterBatch:
    prompt = _batch_prompt(inputs, start, end, index)
    nodes = inputs.nodes[start:end]
    paragraphs = [p for n in nodes for p in n["paragraphs"]]
    budget_projection = project_writer_budget(inputs, start, end)
    target = budget_projection["batch_target_words"]
    evidence_count = sum(len(p["allowed_evidence"]) for p in paragraphs)
    semantic_load = sum(
        unit.semantic_load for unit in _build_paragraph_writing_units(inputs)
        if start <= unit.node_index < end
    )
    # Existing batching heuristic retained, NOT a formal length/token conversion
    # or a provider cap. Includes JSON metadata; no new ratios are introduced.
    output_tokens = math.ceil(target * 1.5 + len(paragraphs) * 170 + evidence_count * 50 + 300)
    return WriterBatch(
        batch_id=f"BATCH-{index:02d}",
        node_ids=tuple(n["outline_node_id"] for n in nodes),
        paragraph_ids=tuple(p["paragraph_id"] for p in paragraphs),
        target_words=target,
        evidence_count=evidence_count,
        prompt_chars=len(prompt) + len(SYSTEM_PROMPT),
        estimated_prompt_tokens=math.ceil((len(prompt) + len(SYSTEM_PROMPT)) / 2),
        estimated_output_tokens=output_tokens,
        prompt_sha256=hashlib.sha256((SYSTEM_PROMPT + prompt).encode()).hexdigest(),
        prompt=prompt,
        semantic_load=semantic_load,
        budget_projection_json=canonical_json({
            **budget_projection,
            "node_targets": {n["outline_node_id"]: n["target_words"] for n in nodes},
            "paragraph_budgets": {p["paragraph_id"]: p["budget"] for p in paragraphs},
        }),
    )


def build_batch_plan(inputs: WriterInput, limits: WriterContextLimits | None = None) -> WriterBatchPlan:
    limits = limits or WriterContextLimits()
    count = len(inputs.nodes)
    # Search contiguous partitions: the fewest calls that satisfy BOTH provider
    # capacity and paragraph-task semantic capacity, then balance semantic load.
    # There is no case/evidence-ID rule and no model-based batching decision.
    for batch_count in range(1, min(MAX_SECTION_WRITER_BATCHES, count) + 1):
        candidates = []
        for cuts in itertools.combinations(range(1, count), batch_count - 1):
            edges = (0, *cuts, count)
            batches = tuple(_make_batch(inputs, a, b, i+1)
                            for i, (a, b) in enumerate(zip(edges, edges[1:])))
            if all(b.prompt_chars <= limits.max_prompt_chars
                   and b.estimated_prompt_tokens + b.estimated_output_tokens <= limits.max_context_tokens
                   and b.estimated_output_tokens <= limits.max_output_tokens
                   and len(b.paragraph_ids) <= limits.max_paragraphs
                   and b.semantic_load <= limits.max_batch_semantic_load for b in batches):
                score = (
                    max(b.semantic_load for b in batches),
                    max(b.estimated_prompt_tokens + b.estimated_output_tokens for b in batches),
                    cuts,
                )
                candidates.append((score, batches))
        if candidates:
            batches = min(candidates, key=lambda pair: pair[0])[1]
            unsigned = {"outline_sha": inputs.outline.outline_sha, "budget_sha": inputs.budget.budget_sha,
                        "batches": [b.summary() for b in batches]}
            return WriterBatchPlan(inputs.outline.outline_sha, inputs.budget.budget_sha, batches, stable_sha(unsigned))
    raise SectionWriterContractError("WRITER_CONTEXT_EXCEEDS_THREE_BATCHES")


_PLACEHOLDER = re.compile(r"(?i)(?<![a-z])(?:X{2,}|TODO|TBD)(?![a-z])|【待(?:补充|确认)[^】]*】|[×✕]{2,}")


class StructuralCoverageRecord(_StrictOutput):
    """Deterministic references and identity only; never a prose-quality verdict.

    used_evidence_ids includes all declared IDs; valid_used_evidence_ids is its
    intersection with allowed. Illegal means unknown; cross-paragraph means
    known elsewhere but not allowed here. Positions are paragraph-local, zero
    based, with repeated use across sentences retained in source order.
    """

    paragraph_id: str
    paragraph_intent_id: str
    allowed_evidence_ids: tuple[str, ...]
    # Retained as zero-valued trace compatibility fields. M3 has no fixed-ID
    # coverage obligation; only illegal/cross-paragraph references are gated.
    required_evidence_ids: tuple[str, ...] = ()
    used_evidence_ids: tuple[str, ...]
    valid_used_evidence_ids: tuple[str, ...]
    missing_required_evidence_ids: tuple[str, ...] = ()
    illegal_evidence_ids: tuple[str, ...]
    cross_paragraph_evidence_ids: tuple[str, ...]
    sentence_indexes_by_evidence_id: dict[str, tuple[int, ...]]
    structural_coverage_status: Literal["PASS", "FAIL"]
    coverage_kind: Literal["STRUCTURAL_EVIDENCE_COVERAGE"] = "STRUCTURAL_EVIDENCE_COVERAGE"
    semantic_coverage_status: Literal["NOT_EVALUATED_M4_OWNER"] = "NOT_EVALUATED_M4_OWNER"
    issues: tuple[dict[str, Any], ...]


def build_structural_coverage_record(
    paragraph: Mapping[str, Any], spec: Mapping[str, Any], known_evidence_ids: set[str],
) -> StructuralCoverageRecord:
    """Use frozen paragraph scope and actual sentence references, never prose.

    This also diagnoses schema-rejected JSON. A record PASS alone does not
    replace the Writer parser, Fact Safety, budget, or FLOW4 hard gates.
    """
    issues: list[dict[str, Any]] = []
    def issue(code: str, **detail: Any):
        issues.append({"code": code, "paragraph_id": spec["paragraph_id"],
                       "paragraph_intent_id": spec["paragraph_intent_id"], **detail})
    # Diagnostic fixtures may only supply a subset; production specs always
    # contain all five frozen identity fields and the parser checks all five.
    if any(paragraph.get(f) != spec[f] for f in _PARAGRAPH_IDENTITY_FIELDS if f in spec):
        issue("WRITER_PARAGRAPH_IDENTITY_ORDER")
    allowed = set(spec["evidence_scope"]["allowed_evidence_ids"])
    positions: dict[str, list[int]] = {}
    sentences = paragraph.get("sentences")
    shape_valid = (isinstance(sentences, (list, tuple)) and bool(sentences)
        and all(isinstance(s, Mapping) and isinstance(s.get("evidence_ids"), (list, tuple))
                and all(isinstance(eid, str) for eid in s["evidence_ids"]) for s in sentences))
    if not shape_valid:
        issue("PARAGRAPH_DIAGNOSTIC_SHAPE_INVALID")
    else:
        for index, sentence in enumerate(sentences):
            refs = set(sentence["evidence_ids"])
            for eid in sorted(refs):
                positions.setdefault(eid, []).append(index)
            for eid in sorted(refs - allowed):
                issue("CROSS_PARAGRAPH_EVIDENCE" if eid in known_evidence_ids else "ILLEGAL_EVIDENCE_REFERENCE",
                      sentence_index=index, evidence_id=eid)
    used = set(positions)
    return StructuralCoverageRecord(
        paragraph_id=spec["paragraph_id"], paragraph_intent_id=spec["paragraph_intent_id"],
        allowed_evidence_ids=tuple(sorted(allowed)), required_evidence_ids=(),
        used_evidence_ids=tuple(sorted(used)), valid_used_evidence_ids=tuple(sorted(used & allowed)),
        missing_required_evidence_ids=(),
        illegal_evidence_ids=tuple(sorted(used - allowed - known_evidence_ids)),
        cross_paragraph_evidence_ids=tuple(sorted((used - allowed) & known_evidence_ids)),
        sentence_indexes_by_evidence_id={eid: tuple(positions[eid]) for eid in sorted(positions)},
        structural_coverage_status="FAIL" if issues else "PASS", issues=tuple(issues),
    )


def paragraph_consumption_issues(
    paragraph: Mapping[str, Any], spec: Mapping[str, Any], known_evidence_ids: set[str],
) -> list[dict[str, Any]]:
    """The same deterministic coverage owner drives diagnostics and hard gates."""
    return list(build_structural_coverage_record(paragraph, spec, known_evidence_ids).issues)


def diagnose_batch_consumption(
    raw: str, inputs: WriterInput, batch: WriterBatch,
) -> list[dict[str, Any]]:
    """Inspect raw references without accepting invalid sentence types as prose."""
    data = json.loads(raw)
    expected = [n for n in inputs.nodes if n["outline_node_id"] in batch.node_ids]
    specs = {p["paragraph_id"]: p for n in expected for p in n["paragraphs"]}
    known = {e["evidence_id"] for n in inputs.nodes for p in n["paragraphs"] for e in p["allowed_evidence"]}
    issues = []
    seen = set()
    for section in data.get("sections", []):
        for paragraph in section.get("paragraphs", []):
            pid = paragraph.get("paragraph_id")
            if pid not in specs or pid in seen:
                issues.append({"code": "WRITER_PARAGRAPH_IDENTITY_ORDER", "paragraph_id": pid})
                continue
            seen.add(pid)
            issues.extend(paragraph_consumption_issues(paragraph, specs[pid], known))
    for pid in specs.keys() - seen:
        issues.append({"code": "WRITER_REQUIRED_PARAGRAPH_MISSING_OR_ADDED", "paragraph_id": pid})
    return issues


def _parse_writer_schema(raw: str, batch: WriterBatch) -> SectionWriterDraft:
    # Deterministic syntax-only recovery. Never infer missing paragraphs or IDs.
    text = raw.strip()
    if text.startswith("```json\n") and text.endswith("```"):
        text = text[8:-3].strip()
    try:
        return SectionWriterDraft.model_validate_json(text)
    except ValidationError as exc:
        errors = [{"type": e["type"], "loc": e["loc"]} for e in exc.errors()]
        raise SectionWriterContractError("WRITER_OUTPUT_SCHEMA_INVALID", location=batch.batch_id,
                                         report=errors) from None


def parse_writer_response(raw: str, inputs: WriterInput, batch: WriterBatch) -> SectionWriterDraft:
    """Strict wire decoder followed by the unchanged canonical evidence gates.

    Canonical/global-ID replay and Adapter rechecks use validate_batch_draft
    directly. Live Writer responses must use local aliases: no format guessing,
    global-ID fallback, ownership repair, or sentence/prose rewriting.
    """
    draft = _parse_writer_schema(raw, batch)
    aliases = paragraph_evidence_aliases(inputs)
    known_aliases = {alias: eid for mapping in aliases.values() for alias, eid in mapping.items()}
    known_ids = set(known_aliases.values())
    specs = {p["paragraph_id"]: p for n in inputs.nodes if n["outline_node_id"] in batch.node_ids
             for p in n["paragraphs"]}
    sections, issues = [], []
    for section in draft.sections:
        paragraphs = []
        for paragraph in section.paragraphs:
            spec = specs.get(paragraph.paragraph_id)
            if (spec is None or any(getattr(paragraph, f) != spec[f] for f in _PARAGRAPH_IDENTITY_FIELDS)
                    or section.section_id != spec["section_id"]
                    or section.outline_node_id != spec["outline_node_id"]):
                raise SectionWriterContractError("WRITER_PARAGRAPH_IDENTITY_ORDER", location=paragraph.paragraph_id)
            local = aliases[paragraph.paragraph_id]
            sentences = []
            for index, sentence in enumerate(paragraph.sentences):
                resolved = []
                for reference in sentence.evidence_ids:
                    if reference in local:
                        resolved.append(local[reference])
                        continue
                    # Diagnose foreign aliases/IDs, but never add them to the
                    # paragraph, silently drop them, or borrow another scope.
                    eid = known_aliases.get(reference, reference)
                    code = ("CROSS_PARAGRAPH_EVIDENCE" if reference in known_aliases or (eid in known_ids and eid not in local.values())
                            else "WRITER_GLOBAL_EVIDENCE_ID_FORBIDDEN" if eid in known_ids
                            else "UNKNOWN_EVIDENCE_ALIAS")
                    issues.append({"code": code, "paragraph_id": paragraph.paragraph_id,
                                   "sentence_index": index, "returned_reference": reference,
                                   "evidence_id": eid})
                sentences.append(sentence.model_copy(update={"evidence_ids": tuple(resolved)}))
            paragraphs.append(paragraph.model_copy(update={"sentences": tuple(sentences)}))
        sections.append(section.model_copy(update={"paragraphs": tuple(paragraphs)}))
    if issues:
        raise SectionWriterContractError(issues[0]["code"], location=issues[0]["paragraph_id"], report=issues)
    prose = [draft.title or "", *(s.heading for s in draft.sections),
             *(s.text for n in draft.sections for p in n.paragraphs for s in p.sentences)]
    if any(re.search(r"P\d+/E\d+", value) for value in prose):
        raise SectionWriterContractError("WRITER_EVIDENCE_ALIAS_IN_PROSE", location=batch.batch_id)
    resolved = draft.model_copy(update={"sections": tuple(sections)})
    return validate_batch_draft(resolved.model_dump_json(), inputs, batch)


def validate_batch_draft(raw: str, inputs: WriterInput, batch: WriterBatch) -> SectionWriterDraft:
    """Validate canonical global IDs; also used for unchanged historical raw."""
    draft = _parse_writer_schema(raw, batch)
    expected = [n for n in inputs.nodes if n["outline_node_id"] in batch.node_ids]
    if draft.outline_sha != inputs.outline.outline_sha:
        raise SectionWriterContractError("WRITER_OUTLINE_SHA_MISMATCH")
    if [s.outline_node_id for s in draft.sections] != [n["outline_node_id"] for n in expected]:
        raise SectionWriterContractError("WRITER_NODE_IDENTITY_ORDER")
    if batch.batch_id == "BATCH-01":
        if not draft.title or not draft.title.strip():
            raise SectionWriterContractError("WRITER_TITLE_REQUIRED")
    elif draft.title is not None:
        raise SectionWriterContractError("WRITER_EXTRA_TITLE")
    if draft.title and (_PLACEHOLDER.search(draft.title) or "\n" in draft.title):
        raise SectionWriterContractError("WRITER_TITLE_INVALID")
    for section, node in zip(draft.sections, expected):
        if section.section_id != node["section_id"]:
            raise SectionWriterContractError("WRITER_SECTION_IDENTITY", location=section.outline_node_id)
        if not section.heading.strip() or "\n" in section.heading or _PLACEHOLDER.search(section.heading):
            raise SectionWriterContractError("WRITER_HEADING_INVALID", location=section.section_id)
        if len(section.paragraphs) != len(node["paragraphs"]):
            raise SectionWriterContractError("WRITER_REQUIRED_PARAGRAPH_MISSING_OR_ADDED", location=section.section_id)
        for paragraph, spec in zip(section.paragraphs, node["paragraphs"]):
            if any(getattr(paragraph, f) != spec[f] for f in _PARAGRAPH_IDENTITY_FIELDS):
                raise SectionWriterContractError("WRITER_PARAGRAPH_IDENTITY_ORDER", location=spec["paragraph_id"])
            for sentence in paragraph.sentences:
                if not sentence.text.strip() or "\n" in sentence.text or "\r" in sentence.text:
                    raise SectionWriterContractError("WRITER_SENTENCE_BOUNDARY_INVALID", location=paragraph.paragraph_id)
                if _PLACEHOLDER.search(sentence.text):
                    raise SectionWriterContractError("NO_LITERAL_PLACEHOLDER_IN_ARTICLE_BODY", location=paragraph.paragraph_id)
                if len(sentence.evidence_ids) != len(set(sentence.evidence_ids)):
                    raise SectionWriterContractError("DUPLICATE_SENTENCE_EVIDENCE", location=paragraph.paragraph_id)
                if sentence.sentence_type == "FACT" and not sentence.evidence_ids:
                    raise SectionWriterContractError("FACT_EVIDENCE_REQUIRED", location=paragraph.paragraph_id)
                if sentence.sentence_type == "ANALYSIS" and (sentence.evidence_ids or not spec["allow_general_synthesis"]):
                    raise SectionWriterContractError("ANALYSIS_CONTRACT_FAILURE", location=paragraph.paragraph_id)
    issues = diagnose_batch_consumption(draft.model_dump_json(), inputs, batch)
    if issues:
        raise SectionWriterContractError(issues[0]["code"], location=issues[0]["paragraph_id"], report=issues)
    return draft


@dataclass(frozen=True)
class WriterExecution:
    draft: SectionWriterDraft
    batch_plan: WriterBatchPlan
    raw_responses: tuple[str, ...]
    calls: tuple[dict[str, Any], ...]
    writer_wall_ms: float


class SectionWriter:
    """Sole new Writer owner: one bounded pass, with no fallback or repair call."""

    def __init__(self, limits: WriterContextLimits | None = None):
        self.limits = limits or WriterContextLimits()

    def write(self, inputs: WriterInput, *, provider: Any, temperature: float = 0.7,
              external_authorized: bool = False, local_fixture: bool = False,
              on_call_completed: Callable[[WriterBatch, str, dict[str, Any]], None] | None = None) -> WriterExecution:
        if not external_authorized and not local_fixture:
            raise SectionWriterContractError("M3_SECTION_WRITER_REAL_RUN_AUTHORIZATION_REQUIRED")
        # Local mode accepts only an explicit fixture marker and must additionally
        # be run under the test harness network guard. No auto provider creation.
        if local_fixture and getattr(provider, "section_writer_local_fixture", False) is not True:
            raise SectionWriterContractError("LOCAL_FIXTURE_PROVIDER_REQUIRED")
        plan = build_batch_plan(inputs, self.limits)
        if not 1 <= len(plan.batches) <= MAX_SECTION_WRITER_BATCHES:
            raise SectionWriterContractError("SECTION_WRITER_CALL_BUDGET_EXCEEDED")
        started = time.perf_counter()

        def call(batch: WriterBatch):
            attempts = []
            def count_attempt(attempt: int):
                if attempt != 1 or attempts:
                    raise SectionWriterContractError("PROVIDER_RETRY_FORBIDDEN", location=batch.batch_id)
                attempts.append(attempt)
            call_start = time.perf_counter()
            raw = ""
            error_type = None
            try:
                raw = provider.generate_text(prompt=batch.prompt, system_prompt=SYSTEM_PROMPT,
                                             temperature=temperature, retry_limit=0,
                                             on_http_request_attempt=count_attempt)
                if not isinstance(raw, str):
                    raise SectionWriterContractError("PROVIDER_RESPONSE_TYPE_INVALID", location=batch.batch_id)
            except Exception as exc:
                error_type = type(exc).__name__
                raise
            finally:
                metrics = {**batch.summary(), "latency_ms": (time.perf_counter() - call_start) * 1000,
                           "http_attempts": len(attempts), "output_chars": len(raw) if isinstance(raw, str) else 0,
                           "estimated_output_tokens_actual": math.ceil(len(raw) / 2) if isinstance(raw, str) else 0,
                           "provider_usage_tokens": None, "provider_error_type": error_type}
                # Diagnostic-only schema view retains usage even when evidence
                # gates subsequently fail. It never accepts or repairs a draft.
                try:
                    measured = measure_writer_usage(_parse_writer_schema(raw if isinstance(raw, str) else "", batch), inputs)
                except SectionWriterContractError as exc:
                    metrics["budget_diagnostic"] = {"status": "UNAVAILABLE", "reason": exc.code}
                else:
                    contract = json.loads(batch.budget_projection_json)
                    actual = measured["total_actual_words"]
                    metrics["budget_diagnostic"] = {
                        **measured, "diagnostic_only": True,
                        "delta_words": actual - contract["batch_target_words"],
                        "batch_soft_limit_status": ("ABOVE_SOFT_LIMIT" if actual > contract["batch_soft_limit_words"]
                                                    else "WITHIN_SOFT_LIMIT"),
                        "article_budget_status": "PENDING_ARTICLE_VALIDATION",
                    }
                if on_call_completed:
                    on_call_completed(batch, raw if isinstance(raw, str) else "", metrics)
            if not local_fixture and len(attempts) != 1:
                raise SectionWriterContractError("PROVIDER_ATTEMPT_ACCOUNTING_MISSING", location=batch.batch_id)
            parsed = parse_writer_response(raw, inputs, batch)
            return parsed, raw, metrics

        # Batch contexts share only immutable global constraints. Assemble in plan
        # order regardless of completion order, retaining all call measurements.
        with ThreadPoolExecutor(max_workers=len(plan.batches)) as executor:
            rows = list(executor.map(call, plan.batches))
        draft = SectionWriterDraft(outline_sha=inputs.outline.outline_sha, title=rows[0][0].title,
                                   sections=tuple(s for row in rows for s in row[0].sections))
        return WriterExecution(draft, plan, tuple(row[1] for row in rows), tuple(row[2] for row in rows),
                               (time.perf_counter() - started) * 1000)
