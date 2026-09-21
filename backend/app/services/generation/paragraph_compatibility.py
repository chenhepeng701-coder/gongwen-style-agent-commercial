"""The sole SectionWriter -> frozen FLOW4 v3 paragraph transport adapter."""
from __future__ import annotations

from typing import Any

from app.models.flow4 import ParsedStructuredDraft
from app.services.generation.section_writer import (
    SectionWriterDraft, WriterInput, WriterBatch, validate_batch_draft,
    build_structural_coverage_record,
)


class ParagraphCompatibilityAdapter:
    """No prose edits, new planning, evidence selection, or paragraph inference."""

    def convert(self, draft: SectionWriterDraft, inputs: WriterInput) -> tuple[dict[str, Any], dict[str, Any]]:
        nodes = inputs.nodes
        # Recheck the whole frozen contract even if a caller bypassed Writer.write.
        validation_scope = WriterBatch(
            "BATCH-01", tuple(n["outline_node_id"] for n in nodes),
            tuple(p["paragraph_id"] for n in nodes for p in n["paragraphs"]),
            0, 0, 0, 0, 0, "", "",
        )
        validated = validate_batch_draft(draft.model_dump_json(), inputs, validation_scope)
        specs = {p["paragraph_id"]: p for n in nodes for p in n["paragraphs"]}
        intents = {p.paragraph_intent_id: p for n in inputs.outline.nodes for p in n.paragraph_intents}
        known = {eid for p in specs.values() for eid in p["evidence_scope"]["allowed_evidence_ids"]}
        sections, trace = [], []
        for section in validated.sections:
            sentences = []
            for paragraph in section.paragraphs:
                intent = intents[paragraph.paragraph_intent_id]
                coverage = build_structural_coverage_record(
                    paragraph.model_dump(mode="json"), specs[paragraph.paragraph_id], known,
                )
                indexes = []
                for sentence in paragraph.sentences:
                    indexes.append(len(sentences))
                    sentences.append({**sentence.model_dump(mode="json"),
                                      "paragraph_id": paragraph.paragraph_id,
                                      "paragraph_order": paragraph.paragraph_order})
                trace.append({"outline_node_id": section.outline_node_id,
                              "section_id": section.section_id,
                              "paragraph_intent_id": paragraph.paragraph_intent_id,
                              "paragraph_id": paragraph.paragraph_id,
                              "paragraph_order": paragraph.paragraph_order,
                              "sentence_indexes": indexes,
                              # Extend the existing Writer trace, never FLOW4 schema.
                              # Frozen guidance + original text support the sole M4
                              # Quality Owner; no semantic review happens here.
                              "writing_goal": intent.writing_goal,
                              "must_cover": list(intent.must_cover),
                              "source_fields": list(intent.source_fields),
                              "text_stage": "WRITER_BEFORE_FACT_SAFETY",
                              "paragraph_text": "".join(s.text for s in paragraph.sentences),
                              "structural_coverage": coverage.model_dump(mode="json")})
            sections.append({"section_id": section.section_id,
                             "heading": section.heading, "sentences": sentences})
        result = {"title": validated.title, "sections": sections}
        # Verify the actual frozen schema. Do not leak Writer metadata into it.
        ParsedStructuredDraft.model_validate(result)
        return result, {"status": "PASS", "outline_sha": inputs.outline.outline_sha,
                        "semantic_coverage_status": "NOT_EVALUATED_M4_OWNER",
                        "paragraph_count": len(trace), "paragraphs": trace,
                        "sentence_text_changed": False, "evidence_identity_changed": False}
