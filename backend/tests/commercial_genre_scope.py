"""Exact commercial dispatch migration pins; no historical approval is moved.

Pins authorize these candidate bytes only. Independent BASE parity tests and
the protected-owner comparisons remain necessary to prove behavior preservation.
"""
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
import subprocess


COMMERCIAL_GENRE_BASE_SHA = "7672e69ac60ea96b5610a59f77cc4c4c58894cfa"
# Exact stabilized commercial dispatch candidate; never derived at runtime.
COMMERCIAL_GENRE_CONTENT_SHA256 = {
    'backend/app/services/generation/full_draft_generation.py': '8c1bb9a4f5c92400655735c6e367f5c73e7404b9f1781ab78bc0db9f0dd5182e',
    'backend/app/services/generation/genres/contracts.py': 'adfc5b86e6bb3e79fe1a1131dfa21c068708b2b49dae498180a9d590edc68629',
    'backend/app/services/generation/genres/registry.py': '71b77f0fdf6b9492bf0c9b57bea8047348e177880012532d19b908e989030ce4',
    'backend/app/services/generation/genres/work_summary.py': '2eeccc6858b78361218ed2296d429fb787081f3b8e93a265984f1fb3539259c0',
    'backend/app/services/generation/official_document_drafting.py': '1f377ea7def7e862c04703e615485b3ba926abb3b4e6c46d0f20e77889f778e5',
    'backend/app/services/generation/task_assembly.py': 'df5d9bd7907080abb1ff0c4c7a884cbd9c9612ac92f8e92783761d75f37edb02',
    'backend/app/services/generation/commercial_genre_runtime.py': '11988dabf41942f075d70b8fc14cd0d8ce0081c53e1a351a98945a8442719409',
    'backend/app/services/generation/genres/commercial_contract.py': '24f9ad3cb0a05726ab5cc3dc25bf64463b8a8d37d78b374898f73835c63292d4',
    'backend/app/services/generation/genres/commercial_gate.py': '0d225340c47cb6fff4a73193d76a8da4d2e020f17cc7be12bdaa01313805747a',
    'backend/app/services/generation/genres/work_summary_commercial.py': '9bb6d5c3de0e2fe2a557c1e719c2363c6a97528ff0a717a63884733172253425',
}

# Task Assembly orchestration is an authorized dispatch seam, not an evidence
# authority. It has an exact migration pin and complete BASE output parity.
COMMERCIAL_DISPATCH_MIGRATIONS = (
    "backend/app/services/generation/task_assembly.py",
)

PROTECTED_OWNER_PATHS = (
    "backend/app/services/generation/generator.py",
    "backend/app/services/generation/atomic_evidence.py",
    "backend/app/services/generation/flow3_v3_trace.py",
    "backend/app/services/generation/full_article_candidate_adapter.py",
    "backend/app/services/generation/evidence_safety.py",
    "backend/app/services/generation/fact_policy.py",
    "backend/app/services/generation/safe_fact_rewriter.py",
    "backend/app/services/generation/fact_source_auditor.py",
    "backend/app/services/generation/source_substrate.py",
    "backend/app/services/generation/material_digest.py",
    "backend/app/services/generation/stable_material_assets.py",
    "backend/app/services/library/material_ingestion.py",
    "backend/app/services/library/semantic_preparation.py",
    "backend/app/services/parser/txt_parser.py",
    "backend/app/services/parser/docx_parser.py",
    "backend/app/services/retrieval/document_retriever.py",
    "backend/app/services/retrieval/official_semantic_chunker.py",
    "backend/app/services/retrieval/official_chunk_metadata.py",
    "backend/app/services/flow4/handoff.py",
    "backend/app/services/flow4/integrity_gate.py",
    "backend/app/services/flow4/renderer.py",
    "backend/app/services/flow4/artifact_delivery.py",
    "backend/app/models/full_article.py",
    "backend/app/services/generation/planning_engine.py",
    "backend/app/services/generation/work_summary_planner.py",
    "backend/app/services/generation/work_summary_evidence_group.py",
    "backend/app/services/generation/work_summary_execution_policy.py",
    "backend/app/services/generation/work_summary_quality_gate.py",
    "backend/app/services/generation/work_summary_quality_recovery.py",
    "backend/app/services/generation/full_article_quality.py",
    "backend/app/services/generation/delivery_decision.py",
    "backend/app/services/generation/full_draft_material.py",
    "backend/app/services/generation/genres/report.py",
    "backend/app/services/generation/genres/report_quality.py",
    "backend/app/services/generation/genres/report_retrieval.py",
    "backend/app/services/generation/genres/rules/report_offline.md",
    "backend/app/services/generation/genres/retrieval.py",
    "backend/app/services/generation/genres/work_summary_retrieval.py",
)


@lru_cache(maxsize=None)
def commercial_base_source(root: Path, path: str) -> bytes:
    return subprocess.check_output(
        ["git", "show", f"{COMMERCIAL_GENRE_BASE_SHA}:{path}"], cwd=root,
    ).replace(b"\r\n", b"\n")


def assert_commercial_genre_content(root: Path, path: str, *, message="") -> None:
    content = (root / path).read_bytes().replace(b"\r\n", b"\n")
    from genre_ui_scene_scope import GENRE_UI_SCENE_MIGRATIONS, assert_integration_content
    if path in GENRE_UI_SCENE_MIGRATIONS and sha256(content).hexdigest() != COMMERCIAL_GENRE_CONTENT_SHA256.get(path):
        assert_integration_content(root, path,
            message=message or path + " differs from exact MULTI-GENRE-COMMERCIAL-INFRASTRUCTURE content")
        return
    from upstream_suite_scope import UPSTREAM_SUITE_MIGRATIONS, assert_upstream_suite_content
    if path in UPSTREAM_SUITE_MIGRATIONS and sha256(content).hexdigest() != COMMERCIAL_GENRE_CONTENT_SHA256.get(path):
        assert_upstream_suite_content(root, path,
            message=message or path + " differs from exact MULTI-GENRE-COMMERCIAL-INFRASTRUCTURE content")
        return
    assert (path in COMMERCIAL_GENRE_CONTENT_SHA256
            and path not in PROTECTED_OWNER_PATHS
            and sha256(content).hexdigest() == COMMERCIAL_GENRE_CONTENT_SHA256[path]), (
        message or path + " differs from exact MULTI-GENRE-COMMERCIAL-INFRASTRUCTURE content"
    )


def assert_commercial_protected_owners_unchanged(root: Path) -> None:
    assert not set(PROTECTED_OWNER_PATHS) & set(COMMERCIAL_GENRE_CONTENT_SHA256)
    for path in PROTECTED_OWNER_PATHS:
        actual = (root / path).read_bytes().replace(b"\r\n", b"\n")
        from genre_ui_scene_scope import GENRE_UI_SCENE_MIGRATIONS, assert_integration_content
        if path in GENRE_UI_SCENE_MIGRATIONS and actual != commercial_base_source(root, path):
            assert_integration_content(root, path, message="Protected owner changed: " + path)
            continue
        from upstream_suite_scope import UPSTREAM_SUITE_MIGRATIONS, assert_upstream_suite_content
        if path in UPSTREAM_SUITE_MIGRATIONS and actual != commercial_base_source(root, path):
            assert_upstream_suite_content(root, path, message="Protected owner changed: " + path)
            continue
        assert actual == commercial_base_source(root, path), "Protected owner changed: " + path


def assert_commercial_migration_scope(root: Path) -> None:
    assert COMMERCIAL_GENRE_CONTENT_SHA256, "Commercial migration content has not been pinned"
    for path in COMMERCIAL_GENRE_CONTENT_SHA256:
        assert_commercial_genre_content(root, path)
    assert_commercial_protected_owners_unchanged(root)
    from multi_genre_scope import assert_p1_core_unchanged
    assert_p1_core_unchanged(root)
