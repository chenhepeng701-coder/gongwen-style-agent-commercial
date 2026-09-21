"""Read-only candidate import and conservative citation proposals; no Writer."""
import json

from app.models.full_article import (
    CitationProposal, CitationSidecar, FullArticleCandidate, article_units, canonical_json,
    text_sha, FullArticleContractError,
)


def import_full_article(content: str, frozen: dict, provenance: dict, *, version=1, parent_digest=""):
    provenance = full_article_provenance(content, frozen["registry"], provenance)
    return FullArticleCandidate(content=content, version=version, parent_digest=parent_digest,
        evidence_json=canonical_json(frozen["registry"]),
        task_json=canonical_json({k: v for k, v in frozen.items() if k != "registry"}),
        provenance_json=canonical_json(provenance))


def full_article_provenance(content: str, registry: dict, provenance: dict) -> dict:
    """Shared CASE32/W2B/W3 identity vocabulary; hashes do not grant trust."""
    identity = {"article_hash": text_sha(content), "source_identity": text_sha(canonical_json({
        eid: row["source_scope"] for eid, row in registry.items()}))}
    if any(k in provenance and provenance[k] != v for k, v in identity.items()):
        raise FullArticleContractError("ARTICLE_PROVENANCE_IDENTITY_MISMATCH")
    return {**provenance, **identity}


def revise_full_article_content(candidate: FullArticleCandidate, content: str) -> FullArticleCandidate:
    """User edits create a new version; neither citations nor reports transfer."""
    provenance = json.loads(candidate.provenance_json)
    provenance.pop("article_hash", None)
    provenance = full_article_provenance(content, json.loads(candidate.evidence_json), provenance)
    return FullArticleCandidate(content=content, version=candidate.version + 1,
        parent_digest=candidate.digest, evidence_json=candidate.evidence_json,
        task_json=candidate.task_json, provenance_json=canonical_json(provenance))


def propose_exact_citations(candidate: FullArticleCandidate) -> CitationSidecar:
    """A proposal is not support. Never fuzzy-match or promote a substring.

    Only terminal sentence punctuation is ignored; all other qualifiers survive.
    Ambiguous duplicates stay unbound. Display headings never select a scope.
    """
    registry = json.loads(candidate.evidence_json)
    proposals = []
    for unit in article_units(candidate.content):
        text = candidate.content[unit.start:unit.end].rstrip("。！？!?")
        matches = [eid for eid, row in registry.items()
                   if row["source_scope"]["source_span"].strip().rstrip("。！？!?") == text]
        if len(matches) == 1:
            eid = matches[0]
            proposals.append(CitationProposal(unit_id=unit.unit_id,
                scope_id=registry[eid]["section_id"], evidence_ids=(eid,), origin="EXACT_WHOLE_UNIT"))
    return CitationSidecar(candidate_digest=candidate.digest,
        evidence_sha256=candidate.evidence_sha256, proposals=tuple(proposals))
