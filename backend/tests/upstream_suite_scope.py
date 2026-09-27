"""Exact UPSTREAM-22 migration allowance; historical approvals never move."""
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
import subprocess


UPSTREAM_SUITE_BASE_SHA = "ecfa1b9da98101fe74ecabc26b026ec8818a3488"
UPSTREAM_SUITE_MIGRATIONS = (
    "backend/app/services/generation/genres/registry.py",
    "backend/app/services/generation/genres/report.py",
    "backend/app/services/generation/genres/report_retrieval.py",
    "backend/app/services/generation/genres/report_quality.py",
)
# Final reviewed candidate digests are pinned after implementation stabilizes.
UPSTREAM_SUITE_CONTENT_SHA256 = {'backend/app/services/generation/genres/registry.py': '168fad31dec8203bbd537720e7c5b0bc5e77e9cd05f767c81c9cc1c3b0fe97b8', 'backend/app/services/generation/genres/report.py': 'b87b6c1e9d16b32b68da7105ee7469476965caca7d856c790be14cd115d430a2', 'backend/app/services/generation/genres/report_retrieval.py': 'b5d78c9f75c1202bb9dd04cd47af7864b6cce6d74d7892883af2dc035df8b259', 'backend/app/services/generation/genres/report_quality.py': '344c0bd3253dbc4b02328a2fcccb416099d1e0bb2b5cb478c77bf1a6ef4dbc23'}


@lru_cache(maxsize=None)
def upstream_base_source(root: Path, path: str) -> bytes:
    return subprocess.check_output(
        ["git", "show", f"{UPSTREAM_SUITE_BASE_SHA}:{path}"], cwd=root,
    ).replace(b"\r\n", b"\n")


def assert_upstream_suite_content(root: Path, path: str, *, message="") -> None:
    content = (root / path).read_bytes().replace(b"\r\n", b"\n")
    from genre_ui_scene_scope import GENRE_UI_SCENE_MIGRATIONS, assert_integration_content
    if path in GENRE_UI_SCENE_MIGRATIONS and sha256(content).hexdigest() != UPSTREAM_SUITE_CONTENT_SHA256.get(path):
        assert_integration_content(root, path,
            message=message or path + " differs from exact UPSTREAM-22-GENRE-MIGRATION content")
        return
    assert (path in UPSTREAM_SUITE_MIGRATIONS
            and sha256(content).hexdigest() == UPSTREAM_SUITE_CONTENT_SHA256.get(path)), (
        message or path + " differs from exact UPSTREAM-22-GENRE-MIGRATION content")


def assert_upstream_protected_owners(root: Path) -> None:
    from commercial_genre_scope import COMMERCIAL_GENRE_CONTENT_SHA256, PROTECTED_OWNER_PATHS
    paths = (set(COMMERCIAL_GENRE_CONTENT_SHA256) | set(PROTECTED_OWNER_PATHS)) - set(UPSTREAM_SUITE_MIGRATIONS)
    for path in sorted(paths):
        from genre_ui_scene_scope import GENRE_UI_SCENE_MIGRATIONS, assert_integration_content
        if path in GENRE_UI_SCENE_MIGRATIONS:
            assert_integration_content(root, path, message="UPSTREAM-22 protected owner changed: " + path)
            continue
        assert (root / path).read_bytes().replace(b"\r\n", b"\n") == upstream_base_source(root, path), (
            "UPSTREAM-22 protected owner changed: " + path)


def assert_upstream_suite_scope(root: Path) -> None:
    assert set(UPSTREAM_SUITE_CONTENT_SHA256) == set(UPSTREAM_SUITE_MIGRATIONS)
    for path in UPSTREAM_SUITE_MIGRATIONS:
        assert_upstream_suite_content(root, path)
    assert_upstream_protected_owners(root)
