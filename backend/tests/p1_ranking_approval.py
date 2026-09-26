"""Exact P1 owner approval; historical guard baselines remain unchanged."""
from hashlib import sha256
from pathlib import Path


DOCUMENT_RETRIEVER_PATH = "backend/app/services/retrieval/document_retriever.py"
# P1-TOPIC-FIRST-RANKING only. Normalize Windows line endings, nothing else.
P1_DOCUMENT_RETRIEVER_SHA256 = "fb5e709a1cc9166c024cbf6317f889f819223cb44cb3582ba972989b04d57348"


def assert_p1_document_retriever(root: Path) -> None:
    content = (root / DOCUMENT_RETRIEVER_PATH).read_bytes().replace(b"\r\n", b"\n")
    if sha256(content).hexdigest() == P1_DOCUMENT_RETRIEVER_SHA256:
        return
    # Only the exact multi-genre profile extraction is additionally authorized.
    # The original P1 hash and ranking AST remain independent immutable oracles.
    from multi_genre_scope import assert_multi_genre_content, assert_p1_core_unchanged
    assert_multi_genre_content(root, DOCUMENT_RETRIEVER_PATH,
        message=DOCUMENT_RETRIEVER_PATH + " differs from the exact P1-approved content")
    assert_p1_core_unchanged(root)
