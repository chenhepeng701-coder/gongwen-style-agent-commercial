"""UI/Scene allowances preserve exact prior approvals and shared owners."""
import ast
from hashlib import sha256
import importlib
from pathlib import Path
import subprocess

import pytest

import genre_ui_scene_scope as scope


ROOT = Path(__file__).resolve().parents[2]


def test_ui_scene_scope_and_protected_owners_match_required_merge_base():
    assert scope.GENRE_UI_SCENE_BASE_SHA == "d9b6e68d698e9d7b97dc92f23f8feffd80360c5a"
    scope.assert_integration_scope(ROOT)


@pytest.mark.parametrize("path", scope.GENRE_UI_SCENE_MIGRATIONS)
def test_ui_scene_exact_pins_reject_future_single_byte_edits(monkeypatch, path):
    original = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda candidate: original(candidate) + (
        b"\n" if candidate == ROOT / path else b""))
    with pytest.raises(AssertionError, match="differs from exact GENRE-UI-SCENE"):
        scope.assert_integration_content(ROOT, path)


@pytest.mark.parametrize("path", scope.protected_owner_paths())
def test_ui_scene_cannot_authorize_protected_owner_single_byte_changes(monkeypatch, path):
    original = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda candidate: original(candidate) + (
        b"\n" if candidate == ROOT / path else b""))
    with pytest.raises(AssertionError, match="UI/Scene protected owner changed"):
        scope.assert_integration_protected_owners(ROOT)


@pytest.mark.parametrize("path,before,after", [
    (scope.RETRIEVER_PATH, b"RELEVANCE_THRESHOLD = 0.50", b"RELEVANCE_THRESHOLD = 0.51"),
    (scope.REGISTRY_PATH, b"self._identities.update(dict.fromkeys(identities, adapter))",
     b"self._identities.clear()"),
    (scope.ROUTER_PATH, b"_map_delivery_result_to_http(batch)", b"None"),
    (scope.SCENE_OWNER_PATH, '"name": "年度工作总结"'.encode(), '"name": "工作总结"'.encode()),
])
def test_even_resealed_candidate_cannot_change_ranking_or_registry_authority(monkeypatch, path, before, after):
    original = Path.read_bytes
    current = original(ROOT / path).replace(b"\r\n", b"\n")
    assert before in current
    changed = current.replace(before, after, 1)
    monkeypatch.setattr(Path, "read_bytes", lambda candidate: changed if candidate == ROOT / path else original(candidate))
    pins = scope.SCENE_CONTRACT_CONTENT_SHA256 if path in scope.SCENE_CONTRACT_CONTENT_SHA256 else scope.GENRE_UI_SCENE_CONTENT_SHA256
    monkeypatch.setitem(pins, path, sha256(changed).hexdigest())
    with pytest.raises(AssertionError, match="protected AST owner"):
        scope.assert_integration_content(ROOT, path)


def test_vendor_line_ending_mutation_remains_a_byte_change(monkeypatch):
    from app.services.generation.genres.upstream_suite import SNAPSHOT_ROOT
    target = SNAPSHOT_ROOT / "LICENSE"
    original = Path.read_bytes
    baseline = original(target)
    changed = baseline.replace(b"\n", b"\r\n", 1)
    assert changed != baseline
    monkeypatch.setattr(Path, "read_bytes", lambda candidate: changed if candidate == target else original(candidate))
    with pytest.raises(AssertionError, match="pinned upstream file changed"):
        scope.assert_vendor_bytes_unchanged(ROOT)


def _literal_assignments(source):
    result = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    try:
                        result[target.id] = ast.literal_eval(node.value)
                    except (ValueError, TypeError):
                        pass
    return result


@pytest.mark.parametrize("filename", [
    "multi_genre_scope.py", "commercial_genre_scope.py", "upstream_suite_scope.py",
    "p1_ranking_approval.py", "p2_admission_approval.py", "p3a_semantic_approval.py",
    "p3b_boundary_scope.py",
])
def test_all_historical_baselines_pins_and_protected_path_lists_remain_immutable(filename):
    path = "backend/tests/" + filename
    assert _literal_assignments(scope.integration_base_source(ROOT, path)) == _literal_assignments(
        (ROOT / path).read_bytes())


def test_offline_runner_inherits_pinned_foundation_guards_and_requires_user_manifest():
    path = "backend/tests/run_multi_genre_offline.py"
    scope.assert_integration_protected_owner(ROOT, path)
    source = (ROOT / "backend/tests/run_genre_ui_scene_offline.py").read_text("utf-8")
    tree = ast.parse(source)
    assert any(isinstance(node, ast.ImportFrom) and node.module == "run_multi_genre_offline"
               and [alias.name for alias in node.names] == ["main"] for node in tree.body)
    call = next(node for node in ast.walk(tree) if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name) and node.func.id == "main")
    options = {keyword.arg: ast.literal_eval(keyword.value) for keyword in call.keywords}
    assert options == {
        "workspace_relative": "tmp/genre-ui-scene-routing-integration",
        "task_name": "GENRE-UI-SCENE-ROUTING-INTEGRATION",
        "count_label": "GENRE_UI_SCENE_OFFLINE_COUNTS",
        "require_user_manifest": True,
    }


@pytest.mark.parametrize("module_name,test_name", [
    ("test_material_ingestion", "test_protected_generation_retrieval_and_legacy_owners_unchanged"),
    ("test_production_task_assembly", "test_protected_owners_unchanged_from_task_base"),
])
def test_historical_directory_guards_cover_new_files_before_and_after_future_staging(monkeypatch, module_name, test_name):
    original = subprocess.check_output

    def include_candidate_paths(command, *args, **kwargs):
        result = original(command, *args, **kwargs)
        if command[:2] == ["git", "ls-files"]:
            paths = set(result.decode("utf-8").splitlines())
            for path in scope.GENRE_UI_SCENE_MIGRATIONS:
                if path not in paths:
                    result += (path + "\n").encode("utf-8")
        return result

    monkeypatch.setattr(subprocess, "check_output", include_candidate_paths)
    getattr(importlib.import_module(module_name), test_name)()
