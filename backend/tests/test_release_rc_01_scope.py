"""Exact Release frontend approvals preserve historical guard enforcement."""
import importlib
from pathlib import Path
import re
import subprocess

import pytest

import release_rc_01_scope as scope


ROOT = Path(__file__).resolve().parents[2]
MERGED_WAITING_UX_SHA = "e50981945773ac7263a5f90fb7f69569ff584098"
HISTORICAL_PATHS = (
    "frontend/src/App.css",
    "frontend/src/pages/GenerationPage.jsx",
    "frontend/src/components/GenerationWaitingIndicator.jsx",
    "frontend/tests/pages/GenerationPage.waiting-ux.test.mjs",
)
LOCKED_PATHS = (*HISTORICAL_PATHS,
    "frontend/tests/pages/App.generation-navigation-guard.test.mjs")
HISTORICAL_GUARDS = (
    ("test_material_ingestion", "test_protected_generation_retrieval_and_legacy_owners_unchanged"),
    ("test_production_task_assembly", "test_protected_owners_unchanged_from_task_base"),
)


def test_release_approval_is_limited_to_five_explicit_frontend_paths():
    assert set(scope.RELEASE_RC_01_FRONTEND_CONTENT_SHA256) == set(LOCKED_PATHS)


@pytest.mark.parametrize("path", LOCKED_PATHS)
def test_exact_release_frontend_content_is_accepted(path):
    scope.assert_release_rc_01_frontend_content(ROOT, path)


@pytest.mark.parametrize("path", LOCKED_PATHS)
def test_release_frontend_pin_rejects_single_byte_edits(monkeypatch, path):
    original = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda candidate: original(candidate) + (
        b"\n" if candidate == ROOT / path else b""))
    with pytest.raises(AssertionError, match=re.escape(
            path + " differs from exact RELEASE-RC-01 frontend content")):
        scope.assert_release_rc_01_frontend_content(ROOT, path)


def test_unapproved_path_is_rejected_before_file_read(monkeypatch):
    path = "backend/app/services/generation/atomic_evidence.py"
    reads = []

    def forbidden_read(candidate):
        reads.append(candidate)
        raise AssertionError("Unauthorized file read")

    monkeypatch.setattr(Path, "read_bytes", forbidden_read)
    with pytest.raises(AssertionError, match=re.escape(
            "Not an authorized RELEASE-RC-01 frontend path: " + path)):
        scope.assert_release_rc_01_frontend_content(ROOT, path)
    assert reads == []


@pytest.mark.parametrize("path", HISTORICAL_PATHS)
def test_waiting_ux_files_still_equal_the_merged_main_blobs(path):
    before = subprocess.check_output(
        ["git", "show", f"{MERGED_WAITING_UX_SHA}:{path}"], cwd=ROOT)
    assert (ROOT / path).read_bytes().replace(b"\r\n", b"\n") == before.replace(b"\r\n", b"\n")


def _include_future_staged_paths(monkeypatch):
    """Add only the approved candidate paths to read-only index observations."""
    original = subprocess.check_output

    def include_candidate_paths(command, *args, **kwargs):
        result = original(command, *args, **kwargs)
        if command[:2] == ["git", "ls-files"]:
            paths = set(result.decode("utf-8").splitlines())
            for path in LOCKED_PATHS:
                if path not in paths:
                    result += (path + "\n").encode("utf-8")
        return result

    monkeypatch.setattr(subprocess, "check_output", include_candidate_paths)


def _record_exact_checks(monkeypatch):
    original = scope.assert_release_rc_01_frontend_content
    seen = []

    def checked(root, path):
        seen.append(path)
        return original(root, path)

    monkeypatch.setattr(scope, "assert_release_rc_01_frontend_content", checked)
    return seen


@pytest.mark.parametrize("module_name,test_name", HISTORICAL_GUARDS)
@pytest.mark.parametrize("future_staging", [False, True])
def test_historical_guards_check_exact_release_paths_before_and_after_staging(
        monkeypatch, module_name, test_name, future_staging):
    tracked = subprocess.check_output(
        ["git", "ls-files", "--", *LOCKED_PATHS], cwd=ROOT).decode("utf-8").splitlines()
    expected = set(LOCKED_PATHS) if future_staging else set(tracked)
    assert set(HISTORICAL_PATHS) <= expected
    if future_staging:
        _include_future_staged_paths(monkeypatch)
    seen = _record_exact_checks(monkeypatch)
    getattr(importlib.import_module(module_name), test_name)()
    assert set(seen) == expected


@pytest.mark.parametrize("module_name,test_name", HISTORICAL_GUARDS)
@pytest.mark.parametrize("path", LOCKED_PATHS)
def test_historical_guards_reject_each_release_path_mutation_through_exact_helper(
        monkeypatch, module_name, test_name, path):
    _include_future_staged_paths(monkeypatch)
    seen = _record_exact_checks(monkeypatch)
    original = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda candidate: original(candidate) + (
        b"\n" if candidate == ROOT / path else b""))
    with pytest.raises(AssertionError, match=re.escape(
            path + " differs from exact RELEASE-RC-01 frontend content")):
        getattr(importlib.import_module(module_name), test_name)()
    assert path in seen
