"""Explicit multi-genre migration allowance; historical baselines never move.

Historical byte guards keep their old hashes and reject every future byte edit.
Only these exact migration candidate contents may replace a historical owner.
Independent BASE comparisons below also protect the shared safety and ranking
owners: an updated migration pin cannot alone approve a semantic regression.
"""
import ast
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
import subprocess


MULTI_GENRE_BASE_SHA = "0d789f7b563acd6f95b71b795d18fbf5a5072651"
# Exact migration candidate pins; all original P1/P3-B approval values remain unchanged.
MULTI_GENRE_CONTENT_SHA256 = {
    'backend/app/models/generation.py': '6fe2aba16f53530a116b6a3fb823b46b25d8f169d0c7fd97529ee2d4a9deb5d4',
    'backend/app/services/generation/delivery_decision.py': '5d7456e91c00858084b719cc321f5b9e99e2f680139e6163c47c992304c740e1',
    'backend/app/services/generation/full_article_quality.py': '2d3994edfa3f57cf7029532e6f709e91829a4660354d48bf529b135287a2276c',
    'backend/app/services/generation/full_draft_generation.py': 'fe8cfe58fb938f3b090b0c3e74a31b264a56832c4fda0e645ad60ca96cf7bac9',
    'backend/app/services/generation/full_draft_material.py': 'b0811b43b238adf8d2b120d9ae50dcda88f08826fe553ae0672c4814f817ea5b',
    'backend/app/services/generation/genres/__init__.py': 'fd88ece93c0b24b4d92e9d2bbef75a85370a4b41c62e22210521ccd9fc98ba4b',
    'backend/app/services/generation/genres/contracts.py': '6fb0b852c92360efd12eb87e227e9101f427fa0c435f2c2b1cfafb8929d1e190',
    'backend/app/services/generation/genres/registry.py': 'd709da77b7057bc79b0d453602f7002de54a6d6e3264681efc7385b1f26979a7',
    'backend/app/services/generation/genres/report.py': '69553092401dd23ce7c268f013f23d70beb1bd5ad6e96190d506d8980151105c',
    'backend/app/services/generation/genres/report_quality.py': 'c82732efcc1f79f4fd6886a1fcdd0304923208893f5aacb28f5dd8e98028ad52',
    'backend/app/services/generation/genres/report_retrieval.py': '7d8469675191329ff7df96a7ce948a7a3e53af3756fc585065274870ea8d2fdc',
    'backend/app/services/generation/genres/retrieval.py': '8fd648b09ec129467915353de2bf6226333cff48f24ded356cef102e24636309',
    'backend/app/services/generation/genres/rules/report_offline.md': 'f74cc813fd49beea2d89b00c4cd41fd60d776ea2a9abde664c614d73061fd050',
    'backend/app/services/generation/genres/work_summary.py': '7d5c0a5734ed2d481988a4280dca79cf75667f00f52539ca5a19254d31bd56bf',
    'backend/app/services/generation/genres/work_summary_retrieval.py': '2582670802b3ccd3dd993a872e8336160162f63a66b477187ae4ca92a7e9c2fc',
    'backend/app/services/generation/official_document_drafting.py': '68efb4e2d411448c1cc513e73aa8c7538793f3a191c647b7ac97c693bd43006f',
    'backend/app/services/generation/work_summary_quality_gate.py': '7187979a76cb71f2bb1fcf4a6c9443a570746721f11599d42adf20c8bf8c8fd7',
    'backend/app/services/retrieval/document_retriever.py': 'e366dff3319f3d6fba4797b08fb212d7a006045fdf75bbb200faf8e2b7892767',
}

SHARED_CORE_UNCHANGED = (
    "backend/app/services/generation/atomic_evidence.py",
    "backend/app/services/generation/flow3_v3_trace.py",
    "backend/app/services/generation/full_article_candidate_adapter.py",
    "backend/app/services/generation/evidence_safety.py",
    "backend/app/services/generation/fact_policy.py",
    "backend/app/services/generation/safe_fact_rewriter.py",
    "backend/app/services/generation/fact_source_auditor.py",
    "backend/app/services/generation/task_assembly.py",
    "backend/app/services/generation/source_substrate.py",
    "backend/app/services/generation/material_digest.py",
    "backend/app/services/generation/stable_material_assets.py",
    "backend/app/services/library/material_ingestion.py",
    "backend/app/services/library/semantic_preparation.py",
    "backend/app/services/parser/txt_parser.py",
    "backend/app/services/parser/docx_parser.py",
    "backend/app/services/retrieval/official_semantic_chunker.py",
    "backend/app/services/retrieval/official_chunk_metadata.py",
    "backend/app/services/flow4/handoff.py",
    "backend/app/services/flow4/integrity_gate.py",
    "backend/app/services/flow4/renderer.py",
    "backend/app/services/flow4/artifact_delivery.py",
    "backend/app/models/full_article.py",
)
P1_RETRIEVER = "backend/app/services/retrieval/document_retriever.py"
P1_UNCHANGED_FUNCTIONS = (
    "_split_positive_and_negative", "_field_payload", "prepare_retrieval_request",
    "_meaningful_ngrams", "_coverage", "_field_match", "_source_role",
    "_role_compatibility", "_similarity", "_raw_candidate",
    "_aggregate_documents", "_select_documents", "_selected_chunk",
    "_public_document", "build_retrieval_trace", "_persist_trace", "_empty_result",
)
P1_UNCHANGED_CONSTANTS = (
    "RELEVANCE_THRESHOLD", "RAW_CANDIDATE_LIMIT", "PRIMARY_TEXT_LIMIT",
    "AUXILIARY_TEXT_LIMIT", "MAX_AUXILIARY_DOCUMENTS", "MAX_AUXILIARY_SEMANTIC_GAP",
    "_NEGATION_MARKERS", "_RETRIEVAL_PREFIX_RE", "_DISABLED_REFERENCE_SCOPES",
    "_UNKNOWN_VALUES", "_GENERIC_TOPIC_TERMS", "_RESULT_KEYS",
)


@lru_cache(maxsize=None)
def migration_base_source(root: Path, path: str) -> bytes:
    return subprocess.check_output(
        ["git", "show", f"{MULTI_GENRE_BASE_SHA}:{path}"], cwd=root,
    ).replace(b"\r\n", b"\n")


def assert_multi_genre_content(root: Path, path: str, *, message="") -> None:
    content = (root / path).read_bytes().replace(b"\r\n", b"\n")
    if path in MULTI_GENRE_CONTENT_SHA256 and sha256(content).hexdigest() == MULTI_GENRE_CONTENT_SHA256[path]:
        return
    # Keep the Foundation pins unchanged. This task separately proves those
    # pins against its merge BASE and permits only exact commercial wiring.
    from commercial_genre_scope import assert_commercial_genre_content
    assert_commercial_genre_content(root, path,
        message=message or path + " differs from exact MULTI-GENRE-FOUNDATION migration content")


def assert_shared_core_unchanged(root: Path) -> None:
    for path in SHARED_CORE_UNCHANGED:
        from commercial_genre_scope import COMMERCIAL_DISPATCH_MIGRATIONS, assert_commercial_genre_content
        if path in COMMERCIAL_DISPATCH_MIGRATIONS:
            current = (root / path).read_bytes().replace(b"\r\n", b"\n")
            if current != migration_base_source(root, path):
                assert_commercial_genre_content(root, path)
            continue
        assert (root / path).read_bytes().replace(b"\r\n", b"\n") == migration_base_source(root, path), path


def assert_p1_core_unchanged(root: Path) -> None:
    def nodes(source):
        result = {}
        for node in ast.parse(source).body:
            if isinstance(node, ast.FunctionDef):
                result[node.name] = ast.dump(node, include_attributes=False)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        result[target.id] = ast.dump(node, include_attributes=False)
        return result
    before = nodes(migration_base_source(root, P1_RETRIEVER))
    after = nodes((root / P1_RETRIEVER).read_bytes())
    for name in (*P1_UNCHANGED_FUNCTIONS, *P1_UNCHANGED_CONSTANTS):
        assert before[name] == after[name], "P1 shared function or constant changed: " + name


def assert_migration_scope(root: Path) -> None:
    assert MULTI_GENRE_CONTENT_SHA256, "Migration content has not been pinned"
    for path in MULTI_GENRE_CONTENT_SHA256:
        assert_multi_genre_content(root, path)
    assert_shared_core_unchanged(root)
    assert_p1_core_unchanged(root)
