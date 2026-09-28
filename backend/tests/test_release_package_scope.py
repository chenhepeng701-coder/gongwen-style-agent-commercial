from pathlib import Path
import subprocess

import pytest

from release_rc_01_package_scope import APPROVED_PACKAGE_SEAMS, PACKAGE_SEAMS, assert_package_seam

ROOT = Path(__file__).resolve().parents[2]


def test_package_approval_is_limited_to_nine_explicit_paths():
    assert len(APPROVED_PACKAGE_SEAMS) == 9
    assert set(PACKAGE_SEAMS) == APPROVED_PACKAGE_SEAMS


def test_extra_package_seam_is_rejected_before_file_read(monkeypatch):
    monkeypatch.setitem(PACKAGE_SEAMS, "backend/not-authorized.py", "0" * 64)
    monkeypatch.setattr(Path, "read_bytes", lambda path: pytest.fail("Must reject changed path set before reading"))
    with pytest.raises(AssertionError, match="Phase 3 approved packaging seam set changed"):
        assert_package_seam(ROOT, "backend/not-authorized.py")


@pytest.mark.parametrize("path", sorted(APPROVED_PACKAGE_SEAMS))
def test_missing_package_seam_is_rejected_before_file_read(monkeypatch, path):
    monkeypatch.delitem(PACKAGE_SEAMS, path)
    monkeypatch.setattr(Path, "read_bytes", lambda path: pytest.fail("Must reject changed path set before reading"))
    with pytest.raises(AssertionError, match="Phase 3 approved packaging seam set changed"):
        assert_package_seam(ROOT, "backend/app/config.py")


@pytest.mark.parametrize("path", sorted(APPROVED_PACKAGE_SEAMS))
def test_package_seam_hash_tamper_is_rejected(monkeypatch, path):
    monkeypatch.setitem(PACKAGE_SEAMS, path, "0" * 64)
    with pytest.raises(AssertionError, match="Phase 3 exact content changed"):
        assert_package_seam(ROOT, path)


@pytest.mark.parametrize("path", PACKAGE_SEAMS)
def test_exact_package_seams_and_unchanged_reference_schema(path):
    assert_package_seam(ROOT, path)


@pytest.mark.parametrize("path", PACKAGE_SEAMS)
def test_every_package_seam_rejects_mutation(monkeypatch, path):
    original = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda item: original(item) + (b"\n" if item == ROOT / path else b""))
    with pytest.raises(AssertionError, match="Phase 3 exact content changed"):
        assert_package_seam(ROOT, path)


def test_unapproved_production_file_is_rejected_without_read(monkeypatch):
    monkeypatch.setattr(Path, "read_bytes", lambda path: pytest.fail("Must not read an unapproved path"))
    with pytest.raises(AssertionError, match="Not a Phase 3 packaging seam"):
        assert_package_seam(ROOT, "backend/app/services/generation/generator.py")


@pytest.mark.parametrize("module,function", [
    ("test_material_ingestion", "test_protected_generation_retrieval_and_legacy_owners_unchanged"),
    ("test_production_task_assembly", "test_protected_owners_unchanged_from_task_base"),
])
def test_historical_guards_cover_phase3_new_paths_before_staging(monkeypatch, module, function):
    import importlib
    original = subprocess.check_output
    def with_new_paths(command, *args, **kwargs):
        raw = original(command, *args, **kwargs)
        if command[:2] == ["git", "ls-files"]:
            paths = set(raw.decode().splitlines())
            raw += ''.join(path + '\n' for path in PACKAGE_SEAMS if path not in paths).encode()
        return raw
    monkeypatch.setattr(subprocess, "check_output", with_new_paths)
    getattr(importlib.import_module(module), function)()
