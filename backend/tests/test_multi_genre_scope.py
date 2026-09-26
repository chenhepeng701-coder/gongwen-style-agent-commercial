"""Migration allowances stay narrow while original approvals remain immutable."""
from hashlib import sha256
from pathlib import Path

import pytest

from multi_genre_scope import (
    MULTI_GENRE_CONTENT_SHA256, assert_migration_scope, assert_multi_genre_content,
    migration_base_source,
)


ROOT = Path(__file__).resolve().parents[2]


def test_migration_preserves_shared_safety_ranking_and_stable_asset_owners():
    assert_migration_scope(ROOT)


def test_historical_approval_pins_still_describe_the_original_production_base():
    from p1_ranking_approval import DOCUMENT_RETRIEVER_PATH, P1_DOCUMENT_RETRIEVER_SHA256
    from p3b_boundary_scope import P3B_CONTENT_SHA256
    pins = {DOCUMENT_RETRIEVER_PATH: P1_DOCUMENT_RETRIEVER_SHA256, **P3B_CONTENT_SHA256}
    for path, expected in pins.items():
        assert sha256(migration_base_source(ROOT, path)).hexdigest() == expected, path


@pytest.mark.parametrize("path", tuple(MULTI_GENRE_CONTENT_SHA256))
def test_migration_allowance_rejects_future_single_byte_edits(monkeypatch, path):
    original = Path.read_bytes

    def changed(candidate):
        data = original(candidate)
        return data + b"\n" if candidate == ROOT / path else data

    monkeypatch.setattr(Path, "read_bytes", changed)
    with pytest.raises(AssertionError, match="differs from exact MULTI-GENRE-FOUNDATION migration content"):
        assert_multi_genre_content(ROOT, path)
