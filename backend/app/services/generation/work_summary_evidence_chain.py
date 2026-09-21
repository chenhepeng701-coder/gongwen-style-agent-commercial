"""Deterministic, fact-preserving work-chain grouping for work summaries.

The owner in this module only records organizational relationships among
already existing atomic evidence.  It never changes an evidence record, merges
Fact Anchors, infers a missing work step, or creates prose/facts.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import re
from typing import Any, Mapping, Sequence


WORK_CHAIN_SCHEMA_VERSION = "work-summary-safe-chain-1"
ORDER_AVAILABLE = "ORDER_AVAILABLE"
ORDER_UNAVAILABLE = "ORDER_UNAVAILABLE"

CHAIN_PARTIAL = "PARTIAL"
CHAIN_MULTI_STEP = "MULTI_STEP"
CHAIN_CLOSED_LOOP = "CLOSED_LOOP"

_HAN_ALNUM_RE = re.compile(r"[^\u3400-\u9fffA-Za-z0-9]+")
_DISCOVERY_MARKERS = ("发现", "排查", "检查", "查摆", "隐患", "问题")
_RECTIFICATION_MARKERS = ("整改", "整治", "治理", "处置", "改进")
_REVIEW_MARKERS = ("复核", "验收", "回头看", "复查")
_CLOSE_MARKERS = ("销号", "清零", "闭环")


@dataclass(frozen=True)
class WorkChainMember:
    evidence_id: str
    evidence_type: str
    fact_anchorable: bool
    source_span_id: str
    source_position: tuple[int, int, int, int] | None
    business_block: str
    section_compatibility: tuple[str, ...]


@dataclass(frozen=True)
class SafeWorkChain:
    chain_id: str
    section_id: str
    business_block: str
    member_evidence_ids: tuple[str, ...]
    ordered_evidence_ids: tuple[str, ...]
    chain_members: tuple[WorkChainMember, ...]
    relation_basis: tuple[str, ...]
    chain_completeness: str
    order_status: str
    source_span_count: int
    fact_anchorable_count: int
    analysis_support_count: int
    creates_facts: bool = False


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _compact(value: Any) -> str:
    return _HAN_ALNUM_RE.sub("", str(value or ""))


def _ngrams(value: Any, size: int = 2) -> set[str]:
    compact = _compact(value)
    return {
        compact[index:index + size]
        for index in range(max(0, len(compact) - size + 1))
    }


def _visible_topic(item: Mapping[str, Any]) -> str:
    return "".join(
        str(item.get(name) or "")
        for name in (
            "canonical_subject", "subject", "canonical_action", "action",
            "canonical_object", "object", "result",
        )
    )


def _source_position(item: Mapping[str, Any]) -> tuple[int, int, int, int] | None:
    source_order = item.get("source_order")
    if not isinstance(source_order, Mapping) or not source_order.get("available"):
        return None
    names = ("section_index", "chunk_index", "clause_index", "atom_index")
    if any(not isinstance(source_order.get(name), int) for name in names):
        return None
    return tuple(int(source_order[name]) for name in names)  # type: ignore[return-value]


def _explicit_block(item: Mapping[str, Any]) -> str:
    return _compact(item.get("business_block") or item.get("planner_business_block"))


def _topic_coherent(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    business_block: str,
) -> tuple[bool, tuple[str, ...]]:
    """Require structured coherence; keyword similarity alone is insufficient."""

    signals: list[str] = []
    left_block, right_block = _explicit_block(left), _explicit_block(right)
    if left_block and right_block and left_block == right_block == _compact(business_block):
        signals.append("same_explicit_business_block")

    left_object = str(left.get("canonical_object") or left.get("object") or left.get("result") or "")
    right_object = str(right.get("canonical_object") or right.get("object") or right.get("result") or "")
    left_grams, right_grams = _ngrams(left_object), _ngrams(right_object)
    overlap = len(left_grams & right_grams)
    union = len(left_grams | right_grams)
    if overlap >= 2 and overlap / max(1, union) >= 0.34:
        signals.append("structured_object_overlap")

    block_grams = _ngrams(business_block)
    if block_grams and block_grams & left_grams and block_grams & right_grams:
        signals.append("both_objects_align_with_business_block")

    left_subject = _compact(left.get("canonical_subject") or left.get("subject"))
    right_subject = _compact(right.get("canonical_subject") or right.get("subject"))
    if left_subject and left_subject == right_subject and (
        "structured_object_overlap" in signals
        or "both_objects_align_with_business_block" in signals
    ):
        signals.append("same_structured_subject")

    return bool(signals), tuple(signals)


def _adjacent_source_relation(
    left: Mapping[str, Any], right: Mapping[str, Any], business_block: str,
) -> tuple[bool, tuple[str, ...]]:
    if str(left.get("document_id") or "") != str(right.get("document_id") or ""):
        return False, ()
    coherent, topic_signals = _topic_coherent(left, right, business_block)
    if not coherent:
        return False, ()
    left_position, right_position = _source_position(left), _source_position(right)
    if left_position is None or right_position is None:
        # Without reliable order, grouping needs the stronger combination of an
        # explicit block and one additional structured topic signal.
        strong = "same_explicit_business_block" in topic_signals and len(topic_signals) >= 2
        same_chunk = str(left.get("chunk_id") or "") == str(right.get("chunk_id") or "")
        return strong and same_chunk, (
            "same_document", "same_source_chunk", *topic_signals, ORDER_UNAVAILABLE,
        ) if strong and same_chunk else ()

    same_section = left_position[0] == right_position[0]
    same_chunk = left_position[:2] == right_position[:2]
    if same_chunk:
        if left_position[:3] == right_position[:3]:
            # Atomic propositions safely split from one coordinated source
            # clause keep atom order in steps of 100 (with +1 for parallel
            # objects).  They are adjacent facts, not an unordered bundle.
            adjacent = 0 < right_position[3] - left_position[3] <= 100
        else:
            adjacent = 0 < right_position[2] - left_position[2] <= 2
    else:
        adjacent = same_section and right_position[1] - left_position[1] == 1
    if not adjacent:
        return False, ()
    return True, (
        "same_document",
        "same_source_section" if same_section else "source_section_changed",
        "adjacent_source_order",
        *topic_signals,
        ORDER_AVAILABLE,
    )


def _completeness(items: Sequence[Mapping[str, Any]]) -> str:
    text = "".join(_visible_topic(item) for item in items)
    discovery = any(marker in text for marker in _DISCOVERY_MARKERS)
    rectification = any(marker in text for marker in _RECTIFICATION_MARKERS)
    review = any(marker in text for marker in _REVIEW_MARKERS)
    close = any(marker in text for marker in _CLOSE_MARKERS)
    if discovery and rectification and review and close:
        return CHAIN_CLOSED_LOOP
    distinct_steps = {
        (
            str(item.get("evidence_type") or ""),
            _compact(item.get("canonical_action") or item.get("action")),
        )
        for item in items
    }
    return CHAIN_MULTI_STEP if len(items) >= 3 and len(distinct_steps) >= 2 else CHAIN_PARTIAL


def _chain_id(section_id: str, business_block: str, evidence_ids: Sequence[str]) -> str:
    seed = json.dumps(
        {
            "section_id": section_id,
            "business_block": business_block,
            "evidence_ids": sorted(evidence_ids),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return f"CHAIN-{_sha(seed)[:12].upper()}"


def build_safe_work_chain_pack(
    evidence: Sequence[Mapping[str, Any]],
    assignments: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Group only strongly related, fact-anchorable evidence.

    ``assignments`` is produced by the Evidence Selection owner and contains
    section/business-block compatibility.  It is organizational metadata, not a
    fact source.
    """

    by_id = {
        str(item.get("evidence_id") or ""): item
        for item in evidence if item.get("evidence_id")
    }
    buckets: dict[tuple[str, str, str], list[Mapping[str, Any]]] = {}
    analysis_support_ids: list[str] = []
    section_rows: dict[tuple[str, str], dict[str, Any]] = {}
    for evidence_id, assignment in assignments.items():
        item = by_id.get(evidence_id)
        if not item:
            continue
        section_id = str(assignment.get("section_id") or "")
        business_block = str(assignment.get("business_block") or "")
        row = section_rows.setdefault((section_id, business_block), {
            "section_id": section_id,
            "business_block": business_block,
            "fact_anchorable_evidence_ids": [],
            "analysis_support_only_evidence_ids": [],
        })
        if assignment.get("fact_anchorable"):
            row["fact_anchorable_evidence_ids"].append(evidence_id)
            buckets.setdefault(
                (section_id, business_block, str(item.get("document_id") or "")), []
            ).append(item)
        else:
            row["analysis_support_only_evidence_ids"].append(evidence_id)
            analysis_support_ids.append(evidence_id)

    chains: list[SafeWorkChain] = []
    grouped_ids: set[str] = set()
    for (section_id, business_block, _document_id), items in sorted(buckets.items()):
        items = sorted(
            items,
            key=lambda item: (
                _source_position(item) is None,
                _source_position(item) or (0, 0, 0, 0),
                str(item.get("evidence_id") or ""),
            ),
        )
        components: list[list[Mapping[str, Any]]] = []
        component_basis: list[set[str]] = []
        for item in items:
            if not components:
                components.append([item])
                component_basis.append(set())
                continue
            linked, basis = _adjacent_source_relation(components[-1][-1], item, business_block)
            if linked:
                components[-1].append(item)
                component_basis[-1].update(basis)
            else:
                components.append([item])
                component_basis.append(set())

        for component, basis in zip(components, component_basis):
            if len(component) < 2:
                continue
            positions = [_source_position(item) for item in component]
            order_available = all(position is not None for position in positions)
            member_ids = tuple(str(item.get("evidence_id") or "") for item in component)
            ordered_ids = member_ids if order_available else ()
            members = tuple(
                WorkChainMember(
                    evidence_id=str(item.get("evidence_id") or ""),
                    evidence_type=str(item.get("evidence_type") or ""),
                    fact_anchorable=True,
                    source_span_id=str(item.get("source_span_sha256") or f"EVIDENCE:{item.get('evidence_id')}"),
                    source_position=_source_position(item),
                    business_block=business_block,
                    section_compatibility=tuple(
                        str(value) for value in (
                            assignments.get(str(item.get("evidence_id") or ""), {}).get("section_compatibility") or []
                        )
                    ),
                )
                for item in component
            )
            chain = SafeWorkChain(
                chain_id=_chain_id(section_id, business_block, member_ids),
                section_id=section_id,
                business_block=business_block,
                member_evidence_ids=member_ids,
                ordered_evidence_ids=ordered_ids,
                chain_members=members,
                relation_basis=tuple(sorted(basis)),
                chain_completeness=_completeness(component),
                order_status=ORDER_AVAILABLE if order_available else ORDER_UNAVAILABLE,
                source_span_count=len({member.source_span_id for member in members}),
                fact_anchorable_count=len(members),
                analysis_support_count=0,
                creates_facts=False,
            )
            chains.append(chain)
            grouped_ids.update(member_ids)

    all_fact_ids = {
        evidence_id
        for row in section_rows.values()
        for evidence_id in row["fact_anchorable_evidence_ids"]
    }
    return {
        "schema_version": WORK_CHAIN_SCHEMA_VERSION,
        "creates_facts": False,
        "chains": [asdict(chain) for chain in sorted(chains, key=lambda item: item.chain_id)],
        "ungrouped_evidence_ids": sorted(all_fact_ids - grouped_ids),
        "analysis_support_only_evidence_ids": sorted(set(analysis_support_ids)),
        "section_evidence_map": [
            {
                **row,
                "fact_anchorable_evidence_ids": sorted(set(row["fact_anchorable_evidence_ids"])),
                "analysis_support_only_evidence_ids": sorted(set(row["analysis_support_only_evidence_ids"])),
            }
            for _key, row in sorted(section_rows.items())
        ],
    }


def model_visible_chain_summary(chain: Mapping[str, Any]) -> dict[str, Any]:
    """Return Prompt-safe organizational metadata without source identities."""

    return {
        "chain_id": str(chain.get("chain_id") or ""),
        "section_id": str(chain.get("section_id") or ""),
        "business_block": str(chain.get("business_block") or ""),
        "ordered_evidence_ids": list(chain.get("ordered_evidence_ids") or []),
        "member_evidence_ids": list(chain.get("member_evidence_ids") or []),
        "relation_basis": list(chain.get("relation_basis") or []),
        "chain_completeness": str(chain.get("chain_completeness") or CHAIN_PARTIAL),
        "order_status": str(chain.get("order_status") or ORDER_UNAVAILABLE),
        "source_span_count": int(chain.get("source_span_count") or 0),
        "fact_anchorable_count": int(chain.get("fact_anchorable_count") or 0),
        "analysis_support_count": int(chain.get("analysis_support_count") or 0),
        "creates_facts": False,
    }


def build_final_work_chain_pack(
    evidence: Sequence[Mapping[str, Any]],
    content_plan: Mapping[str, Any],
    fact_anchor_pack: Mapping[str, Any],
) -> dict[str, Any]:
    """Rebuild chains from the final section/anchor binding.

    The existing safe-chain algorithm remains the sole chain algorithm.  This
    adapter only derives its assignments from final anchors instead of a
    pre-anchor Evidence Selection probe.
    """

    sections = {
        str(section.get("section_id") or ""): section
        for section in content_plan.get("sections") or []
        if isinstance(section, Mapping) and section.get("section_id")
    }
    assignments: dict[str, dict[str, Any]] = {}
    for fact_anchorable, records in (
        (True, fact_anchor_pack.get("fact_anchors") or []),
        (False, fact_anchor_pack.get("analysis_support_pack") or []),
    ):
        for record in records:
            if not isinstance(record, Mapping):
                continue
            evidence_id = str(record.get("evidence_id") or "")
            section_id = str(record.get("section_id") or "")
            section = sections.get(section_id)
            if not evidence_id or section is None:
                continue
            assignments[evidence_id] = {
                "section_id": section_id,
                "business_block": str(section.get("business_block") or ""),
                "section_compatibility": [section_id],
                "fact_anchorable": fact_anchorable,
                "analysis_support_only": not fact_anchorable,
            }
    return build_safe_work_chain_pack(evidence, assignments)


def project_work_chain_pack_onto_plan(
    content_plan: Mapping[str, Any],
    work_chain_pack: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a plan copy whose chain fields come only from ``work_chain_pack``."""

    plan = json.loads(json.dumps(content_plan, ensure_ascii=False))
    chains = [
        chain for chain in work_chain_pack.get("chains") or []
        if isinstance(chain, Mapping)
    ]
    by_section: dict[str, list[dict[str, Any]]] = {}
    for chain in chains:
        by_section.setdefault(str(chain.get("section_id") or ""), []).append(
            model_visible_chain_summary(chain)
        )
    for section in plan.get("sections") or []:
        section_id = str(section.get("section_id") or "")
        summaries = sorted(
            by_section.get(section_id, []),
            key=lambda item: str(item.get("chain_id") or ""),
        )
        section["work_chain_ids"] = [
            str(item.get("chain_id") or "") for item in summaries
        ]
        section["ordered_chain_evidence_ids"] = [
            str(evidence_id)
            for item in summaries
            for evidence_id in item.get("ordered_evidence_ids") or []
        ]
        section["work_chain_summaries"] = summaries
    plan["work_chain_summaries"] = [
        model_visible_chain_summary(chain)
        for chain in sorted(chains, key=lambda item: str(item.get("chain_id") or ""))
    ]
    plan["uses_work_chain_grouping"] = True
    plan["creates_facts"] = False
    return plan
