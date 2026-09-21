"""Deterministic, semantics-preserving renderer for a gated FLOW4 input."""

from __future__ import annotations

import hashlib

from app.models.flow4 import (
    Flow4Input,
    Flow4RenderResult,
    Flow4RenderTraceItem,
    Flow4RenderValidationSummary,
    Flow4RenderedSection,
    Flow4RenderedSentence,
    project_section_body_lines,
    project_rendered_paragraphs,
    render_section_lines,
)
from app.services.flow4.integrity_gate import determine_flow4_risk_status


def render_full_article(handoff):
    from app.models.full_article import FullArticleProjection, FullArticleRenderResult, article_blocks
    from app.services.flow4.integrity_gate import validate_full_article_handoff
    value = validate_full_article_handoff(handoff)
    candidate = value.candidate
    return FullArticleRenderResult(handoff=value, projection=FullArticleProjection(
        content_sha256=candidate.content_sha256, candidate_digest=candidate.digest,
        blocks=article_blocks(candidate.content)))


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _validated_snapshot(flow4_input: Flow4Input) -> Flow4Input:
    if not isinstance(flow4_input, Flow4Input):
        raise TypeError("renderer input must be a gate-approved Flow4Input")

    # Flow4Input is mutable for transport compatibility. Re-validation prevents
    # rendering a core payload changed after the handoff gate released it.
    return Flow4Input.model_validate(flow4_input.model_dump(mode="python"))


def render_flow4_input(flow4_input: Flow4Input) -> Flow4RenderResult:
    """Render a gate-approved Flow4Input without changing any content atom."""

    source = _validated_snapshot(flow4_input)
    draft = source.parsed_structured_draft
    include_title = (
        source.render_options is None
        or source.render_options.include_title_in_plain_text
    )

    blocks: list[str] = []
    if include_title:
        blocks.append(draft.title)

    render_trace: list[Flow4RenderTraceItem] = []
    rendered_sections: list[Flow4RenderedSection] = []
    anchors = {
        anchor.evidence_id: anchor
        for anchor in source.fact_anchor_pack.fact_anchors
    }
    join_adjacent_facts = str(
        source.content_plan.model_dump(mode="python").get("planner_owner") or ""
    ) == "work_summary_planner_v1"
    for section in draft.sections:
        rendered_sentences = tuple(
            Flow4RenderedSentence(
                sentence_type=sentence.sentence_type,
                evidence_ids=tuple(sentence.evidence_ids),
                text=sentence.text,
                subsection_id=sentence.subsection_id,
                paragraph_id=sentence.paragraph_id,
                paragraph_order=sentence.paragraph_order,
                plan_step=sentence.plan_step,
                writing_focus_role=sentence.writing_focus_role,
                expansion_stage=sentence.expansion_stage,
                content_function=sentence.content_function,
                work_chain_role=sentence.work_chain_role,
            )
            for sentence in section.sentences
        )
        for sentence_index, sentence in enumerate(section.sentences):
            render_trace.append(
                Flow4RenderTraceItem(
                    section_id=section.section_id,
                    sentence_index=sentence_index,
                    sentence_type=sentence.sentence_type,
                    evidence_ids=tuple(sentence.evidence_ids),
                    rendered_text_sha256=_sha256_text(sentence.text),
                    anchor_roles=(
                        tuple(
                            anchors[evidence_id].role
                            for evidence_id in sentence.evidence_ids
                        )
                        if sentence.sentence_type == "FACT"
                        else ()
                    ),
                    trace_status=(
                        "MAPPED"
                        if sentence.sentence_type == "FACT"
                        else "ANALYSIS_UNANCHORED"
                    ),
                    subsection_id=sentence.subsection_id,
                    paragraph_id=sentence.paragraph_id,
                    paragraph_order=sentence.paragraph_order,
                    plan_step=sentence.plan_step,
                    writing_focus_role=sentence.writing_focus_role,
                    expansion_stage=sentence.expansion_stage,
                    content_function=sentence.content_function,
                    work_chain_role=sentence.work_chain_role,
                )
            )
        rendered_section = Flow4RenderedSection(
            section_id=section.section_id,
            heading=section.heading,
            subsections=tuple(section.subsections),
            sentences=rendered_sentences,
            paragraphs=project_rendered_paragraphs(rendered_sentences, section.subsections),
            body_lines=project_section_body_lines(
                rendered_sentences,
                section.subsections,
                join_adjacent_facts=join_adjacent_facts,
            ),
            join_adjacent_facts=join_adjacent_facts,
        )
        rendered_sections.append(rendered_section)
        blocks.append("\n".join(render_section_lines(rendered_section)))

    reports = source.validation_reports
    return Flow4RenderResult(
        title=draft.title,
        include_title_in_final_text=include_title,
        sections=tuple(rendered_sections),
        final_text="\n\n".join(blocks),
        render_trace=tuple(render_trace),
        validation_summary=Flow4RenderValidationSummary(
            structured=reports.structured,
            fact=reports.fact,
            analysis=reports.analysis,
            flow3_status=source.source.flow3_status,
            risk_status=determine_flow4_risk_status(source),
        ),
    )
