"""Scene-level style aggregation — combine style cards across documents in the same scene."""

import json
from collections import defaultdict
from pathlib import Path

from app.config import get_settings
from app.services.library.indexer import list_documents, get_document
from app.services.library.style_extractor import get_style_info
from app.services.retrieval.scene_classifier import SCENE_TAXONOMY
from app.utils.runtime_paths import get_library_styles_dir


def _load_document_style(doc_id: str) -> dict | None:
    """Load style card for a document, returning full data dict."""
    info = get_style_info(doc_id)
    if info is None:
        return None
    return info


def _group_by_scene() -> dict[str, list[str]]:
    """Group document IDs by their primary scene tag.

    Returns {scene_name: [doc_id, ...]}
    """
    docs = list_documents()["items"]
    groups = defaultdict(list)
    for doc in docs:
        if doc.scene_tags:
            primary = doc.scene_tags[0]  # first tag is primary scene
            groups[primary].append(doc.id)
    return dict(groups)


def _merge_feature_dicts(dicts: list[dict]) -> dict:
    """Merge a list of feature dicts (sentence_pattern, word_habit, etc.).

    For simple string fields, concatenate unique values.
    For list fields, merge and deduplicate.
    Returns a representative merged dict.
    """
    if not dicts:
        return {}

    # Collect all keys
    all_keys = set()
    for d in dicts:
        all_keys.update(d.keys())

    merged = {}
    for key in all_keys:
        values = []
        for d in dicts:
            v = d.get(key)
            if v is None:
                continue
            if isinstance(v, list):
                values.extend(v)
            elif isinstance(v, str) and v.strip():
                values.append(v.strip())
            elif isinstance(v, dict):
                values.append(v)

        if not values:
            merged[key] = ""
        elif all(isinstance(v, str) for v in values):
            # Deduplicate strings, limit length
            unique = list(dict.fromkeys(values))  # preserve order, deduplicate
            if key in ("high_freq_terms", "typical_examples"):
                merged[key] = unique[:10]  # max 10 list items
            else:
                merged[key] = "；".join(unique[:5])  # max 5 unique string values
        elif key in ("high_freq_terms", "typical_examples") and all(isinstance(v, str) for v in values):
            merged[key] = list(dict.fromkeys(values))[:10]
        else:
            merged[key] = values

    return merged


def aggregate_scene_style(scene_name: str) -> dict | None:
    """Aggregate style features across all documents in a scene.

    Returns a scene-level style profile dict with:
        - scene_name
        - document_count
        - aggregated_style: merged StyleCard
        - source_docs: list of contributing doc summaries
    """
    groups = _group_by_scene()
    doc_ids = groups.get(scene_name, [])
    if not doc_ids:
        return None

    # Load style cards for each document
    styles = []
    source_docs = []
    for doc_id in doc_ids:
        doc = get_document(doc_id)
        if doc is None:
            continue
        style = _load_document_style(doc_id)
        if style is None:
            continue
        styles.append(style["style_card"])
        source_docs.append({
            "doc_id": doc_id,
            "title": doc.title,
            "word_count": doc.word_count,
        })

    if not styles:
        return None

    # Aggregate each dimension
    dims = ["sentence_pattern", "word_habit", "opening_paradigm", "closing_paradigm", "argument_rhythm", "tone_register"]
    aggregated = {}
    for dim in dims:
        dim_dicts = [s.get(dim, {}) for s in styles if s.get(dim)]
        aggregated[dim] = _merge_feature_dicts(dim_dicts)

    return {
        "scene_name": scene_name,
        "document_count": len(source_docs),
        "aggregated_style": aggregated,
        "source_docs": source_docs,
    }


def get_all_scene_profiles() -> list[dict]:
    """Get style profiles for all scenes that have documents and styles."""
    groups = _group_by_scene()
    all_scene_names = set(SCENE_TAXONOMY.keys()) if isinstance(SCENE_TAXONOMY, dict) else {s["name"] for s in SCENE_TAXONOMY}

    profiles = []
    for scene_name in sorted(set(list(groups.keys()))):
        profile = aggregate_scene_style(scene_name)
        if profile:
            profiles.append(profile)
    return profiles


def get_scene_profile_summary(scene_name: str) -> dict | None:
    """Get a brief summary of a scene's style profile.

    Returns a compact summary suitable for display:
        - scene_name, document_count
        - tone: overall tone
        - pattern_types: opening/closing pattern names
        - top_terms: most frequent characteristic terms
    """
    profile = aggregate_scene_style(scene_name)
    if profile is None:
        return None

    ag = profile["aggregated_style"]

    summary = {
        "scene_name": scene_name,
        "document_count": profile["document_count"],
        "tone": ag.get("tone_register", {}).get("overall_tone", ""),
        "opening_type": ag.get("opening_paradigm", {}).get("pattern_type", ""),
        "closing_type": ag.get("closing_paradigm", {}).get("pattern_type", ""),
        "argument_logic": ag.get("argument_rhythm", {}).get("progression_logic", ""),
        "top_terms": ag.get("word_habit", {}).get("high_freq_terms", [])[:8],
        "sentence_length": ag.get("sentence_pattern", {}).get("length_tendency", ""),
    }
    return summary
