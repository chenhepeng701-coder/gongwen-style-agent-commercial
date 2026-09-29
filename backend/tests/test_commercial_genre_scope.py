"""Commercial dispatch has exact scope; prior safety approvals remain intact."""
import ast
from hashlib import sha256
from pathlib import Path

import pytest

from commercial_genre_scope import (
    COMMERCIAL_GENRE_BASE_SHA, COMMERCIAL_GENRE_CONTENT_SHA256,
    PROTECTED_OWNER_PATHS, assert_commercial_genre_content,
    assert_commercial_migration_scope, assert_commercial_protected_owners_unchanged,
    commercial_base_source,
)
from multi_genre_scope import MULTI_GENRE_CONTENT_SHA256


ROOT = Path(__file__).resolve().parents[2]


def _literal_assignments(source):
    values = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    try:
                        values[target.id] = ast.literal_eval(node.value)
                    except (ValueError, TypeError):
                        pass
    return values


def test_commercial_scope_and_protected_owner_bytes():
    assert_commercial_migration_scope(ROOT)


def test_foundation_hashes_still_describe_the_required_commercial_base():
    for path, expected in MULTI_GENRE_CONTENT_SHA256.items():
        assert sha256(commercial_base_source(ROOT, path)).hexdigest() == expected, path


@pytest.mark.parametrize("file,names", [
    ("multi_genre_scope.py", ("MULTI_GENRE_BASE_SHA", "MULTI_GENRE_CONTENT_SHA256",
                             "SHARED_CORE_UNCHANGED", "P1_UNCHANGED_FUNCTIONS", "P1_UNCHANGED_CONSTANTS")),
    ("p1_ranking_approval.py", ("P1_DOCUMENT_RETRIEVER_SHA256",)),
    ("p2_admission_approval.py", ("P2_ATOMIC_EVIDENCE_SHA256",)),
    ("p3a_semantic_approval.py", ("P3A_BASE_SHA", "P3A_ATOMIC_EVIDENCE_SHA256", "P3A_RECOGNITION_DELTA_SHA256")),
    ("p3b_boundary_scope.py", ("P3B_CONTENT_SHA256",)),
])
def test_all_historical_approval_values_are_immutable(file, names):
    path = "backend/tests/" + file
    before = _literal_assignments(commercial_base_source(ROOT, path))
    after = _literal_assignments((ROOT / path).read_bytes())
    for name in names:
        assert before[name] == after[name], file + ":" + name


@pytest.mark.parametrize("path", tuple(COMMERCIAL_GENRE_CONTENT_SHA256))
def test_commercial_pins_reject_future_single_byte_edits(monkeypatch, path):
    original = Path.read_bytes

    def changed(candidate):
        data = original(candidate)
        return data + b"\n" if candidate == ROOT / path else data

    monkeypatch.setattr(Path, "read_bytes", changed)
    with pytest.raises(AssertionError, match="differs from exact MULTI-GENRE-COMMERCIAL-INFRASTRUCTURE content"):
        assert_commercial_genre_content(ROOT, path)


@pytest.mark.parametrize("path", PROTECTED_OWNER_PATHS)
def test_safety_and_stable_asset_owner_mutations_cannot_gain_commercial_allowance(monkeypatch, path):
    original = Path.read_bytes

    def changed(candidate):
        data = original(candidate)
        return data + b"\n" if candidate == ROOT / path else data

    monkeypatch.setattr(Path, "read_bytes", changed)
    with pytest.raises(AssertionError, match="Protected owner changed"):
        assert_commercial_protected_owners_unchanged(ROOT)


def test_migration_baseline_is_the_user_required_merge():
    assert COMMERCIAL_GENRE_BASE_SHA == "7672e69ac60ea96b5610a59f77cc4c4c58894cfa"


def test_offline_runner_preserves_foundation_security_guards():
    path = "backend/tests/run_multi_genre_offline.py"
    from genre_ui_scene_scope import assert_integration_protected_owner
    assert_integration_protected_owner(ROOT, path)

    class ExpandDefaultTaskLabel(ast.NodeTransformer):
        def visit_Name(self, node):
            return ast.Constant("MULTI-GENRE-FOUNDATION") if node.id == "task_name" else node

        def visit_BinOp(self, node):
            node = self.generic_visit(node)
            if (isinstance(node.op, ast.Add) and isinstance(node.left, ast.Constant)
                    and isinstance(node.right, ast.Constant)
                    and isinstance(node.left.value, str) and isinstance(node.right.value, str)):
                return ast.Constant(node.left.value + node.right.value)
            return node

    trees = [ast.parse(commercial_base_source(ROOT, path)), ast.parse((ROOT / path).read_bytes())]
    # Runner closeout permits the exact network-policy delegation only. File
    # protection and real transport/Embedding sentinels stay Foundation-identical.
    for name in ("audit", "forbidden_model", "forbidden_embedding"):
        nodes = [next(node for node in ast.walk(tree)
                      if isinstance(node, ast.FunctionDef) and node.name == name) for tree in trees]
        if name == "audit":
            tail = ast.Module(body=nodes[1].body[1:], type_ignores=[])
            assert ast.dump(tail) == ast.dump(ast.parse("_audit_network(event, args, counts, task_name)"))
            nodes = [ast.Module(body=[node.body[0]], type_ignores=[]) for node in nodes]
        normalized = [ast.dump(ExpandDefaultTaskLabel().visit(node), include_attributes=False) for node in nodes]
        assert normalized[0] == normalized[1], "Offline safety guard changed: " + name
