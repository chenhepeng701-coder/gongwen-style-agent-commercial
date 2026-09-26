"""Current task + Digest -> unchanged Writer input contract.

Exact-quote reading cards replace the live Brief call, not its literary synthesis.
All admission, IDs, contextual state and permissions stay with existing owners.
Used by the explicit full_article_drafting route; no Provider, fallback or storage.
"""
from dataclasses import asdict, dataclass
import json
import time

from app.models.full_article import FullArticleContractError, canonical_json
from app.services.generation.atomic_evidence import build_full_draft_anchor_input
from app.services.generation.full_draft_material import (
    FullDraftMaterialPacket, _brief_conflict_limitations, _validate_brief_outbound_ids,
)
from app.services.generation.material_digest import MaterialDigest


def _assemble_reading_brief(packet, digest):
    from app.services.generation.commercial_genre_runtime import reading_group_key

    document_type = json.loads(packet.task_json).get("document_type")
    index = digest.reading_index()
    registry = json.loads(packet.registry_json)
    legal_ids = registry.keys()
    unavailable_ids = {row["evidence_id"] for row in json.loads(packet.unavailable_json)}
    view = packet.evidence_writing_view()
    for row in view:
        row["limitations"] = _brief_conflict_limitations(row["limitations"], legal_ids, unavailable_ids)
    overlay = packet.overlay()
    # Same outbound restrictions as the existing Brief owner, without reading
    # its LLM prompt assets or bypassing the known LF/CRLF asset lock.
    _validate_brief_outbound_ids([json.loads(packet.task_json), view,
                                 json.loads(packet.restrictions_json), overlay], legal_ids)
    groups = {}
    for row in view:
        unit = index.get(registry[row["evidence_id"]]["source_scope"]["source_span"])
        if unit is None:
            raise FullArticleContractError("TASK_ASSEMBLY_DIGEST_SPAN_MISSING")
        # Lexical navigation only: never claim equal action words mean the same
        # event, object, causal chain or article section. No Evidence is selected out.
        key = reading_group_key(document_type, tuple(action["text"] for action in unit["actions"]))
        groups.setdefault(key, []).append((row, unit))
    matters = []
    for position, (actions, rows) in enumerate(groups.items(), 1):
        ids = [row["evidence_id"] for row, _ in rows]
        # Repeated quotes are displayed once, but ALL admitted IDs remain bound.
        quotes = list(dict.fromkeys(row["text"] for row, _ in rows))
        limits = []
        for row, unit in rows:
            details = {"semantic_state": row["semantic_state"],
                       "semantic_constraints": row["semantic_constraints"],
                       "writability": row["writability"],
                       "limitations": row["limitations"],
                       "source_constraints": row["source_constraints"],
                       "literal_signals": unit["literal_signals"]}
            limits.append({"evidence_ids": [row["evidence_id"]], "text": canonical_json(details)})
        matters.append({"matter_id": f"READING-{position}",
                        "title": "原文动作词索引：" + (" / ".join(actions) or "无显式动作词"),
                        "summary": {"evidence_ids": ids, "text": "\n".join(quotes)},
                        "evidence_ids": ids, "limits": limits})
    # Only known, current deterministic findings are promoted to uncertainties.
    # Other limitations remain verbatim in cards + the full Evidence view.
    if not isinstance(overlay, dict) or not isinstance(overlay.get("findings", []), list):
        raise FullArticleContractError("TASK_ASSEMBLY_OVERLAY_UNINTERPRETABLE")
    uncertainties = []
    for finding in overlay.get("findings", []):
        if (not isinstance(finding, dict) or finding.get("evidence_id") not in legal_ids
                or type(finding.get("blocks_strong_conclusion")) is not bool):
            raise FullArticleContractError("TASK_ASSEMBLY_OVERLAY_UNINTERPRETABLE")
        uncertainties.append({"evidence_ids": [finding["evidence_id"]],
                              "issue": canonical_json(finding),
                              "blocks_strong_conclusion": finding["blocks_strong_conclusion"]})
    value = {"version": "material-brief-stage2-1", "input_sha256": packet.brief_input_sha256,
             "overview": ("本视图为确定性任务组装产物（deterministic_task_assembly），不是模型生成的材料摘要。"
                          f"本次合法材料共{len(view)}条，按原文动作词形成{len(matters)}组阅读索引。"
                          "摘要保留原句；同组不表示同一事件、因果关系或写作章节。"
                          "状态、授权与限制以各条Evidence为准，取舍由Writer决定。"),
             "matters": matters, "uncertainties": uncertainties}
    _validate_brief_outbound_ids(value, legal_ids)
    return value


@dataclass(frozen=True)
class TaskAssembly:
    anchor_input_json: str
    packet: FullDraftMaterialPacket
    elapsed_ms: float


def assemble_task_from_assets(request, retrieval, assets):
    """Shared production/offline composition; no complete chunk reaches Atomic.

Assets may be precomputed without any task. Current retrieval still owns the
authorized text and all roles, restrictions, ordering and admission context.
"""
    from app.services.generation.material_digest import compose_material_digest
    from app.services.generation.stable_material_assets import authorized_retrieval_view

    digest = compose_material_digest(retrieval, assets)
    return assemble_task(request, authorized_retrieval_view(retrieval), digest)


def assemble_task(request, retrieval, digest: MaterialDigest):
    """Rebuild every task-bound decision; return a validated packet, no model.

    Accept the existing ArticleGenRequest, preserving normalization/hard_max and
    the existing material packet schema. This is not a new public generation route.
    """
    from app.services.generation.generator import build_official_draft_task, derive_final_delivery_length_gate

    started = time.perf_counter()
    digest.validate_sources(retrieval)
    task = build_official_draft_task(request)
    anchors = build_full_draft_anchor_input(task, retrieval, source_substrate=digest.substrate)
    fields = {**asdict(task), "style": request.style, "scene": request.scene,
              "hard_max": derive_final_delivery_length_gate(task.target_words)["max_words"]}
    packet = FullDraftMaterialPacket.from_authorized_input(fields, anchors)
    packet = packet.with_brief(_assemble_reading_brief(packet, digest))
    return TaskAssembly(canonical_json(anchors), packet, (time.perf_counter() - started) * 1000)
