"""Pinned source/artifact provenance and fail-closed offline suite loading."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import tomllib

import pytest

from app.models.full_article import FullArticleContractError
from app.services.generation.genres import upstream_suite as suite
from app.services.generation.genres import registry
from app.services.generation.genres.work_summary import WORK_SUMMARY_ADAPTER
from commercial_genre_scope import COMMERCIAL_GENRE_CONTENT_SHA256, PROTECTED_OWNER_PATHS
from upstream_suite_scope import (
    UPSTREAM_SUITE_CONTENT_SHA256, UPSTREAM_SUITE_MIGRATIONS,
    assert_upstream_protected_owners, assert_upstream_suite_content, assert_upstream_suite_scope,
    upstream_base_source,
)


ROOT = Path(__file__).resolve().parents[2]
BASE_SHA = "ecfa1b9da98101fe74ecabc26b026ec8818a3488"


def test_exact_pinned_commit_license_and_entire_catalog_agree():
    manifest = suite.load_suite_manifest()
    assert suite.UPSTREAM_COMMIT == manifest["upstream_commit"] == "6b4166a50b96733684b36da6336ea85a847cf3be"
    assert suite.UPSTREAM_REPOSITORY == manifest["upstream_repository"] == "https://github.com/zhaohui-yang/official-document-drafting"
    assert manifest["upstream_license"] == "MIT"
    license_bytes = (suite.SNAPSHOT_ROOT / "LICENSE").read_bytes()
    assert hashlib.sha256(license_bytes).hexdigest() == "2d8ba6968842d2d058a2f1f02f8a5252603e32124d79b684edf2ca119acd6a38"
    assert b"Copyright (c) 2026 official-document-drafting contributors" in license_bytes
    assert b"Permission is hereby granted, free of charge" in license_bytes
    assert b"The above copyright notice and this permission notice" in license_bytes
    metas = [tomllib.loads(path.read_text("utf-8"))
             for path in (suite.SNAPSHOT_ROOT / "prompts/doc-types").glob("*/meta.toml")]
    assert len(metas) == len(manifest["genres"]) == 22
    assert {meta["id"] for meta in metas} == {row["upstream_genre_id"] for row in manifest["genres"]}
    assert len({row["local_genre_id"] for row in manifest["genres"]}) == 22
    assert len({row["prompt_path"] for row in manifest["genres"]}) == 22
    for row in manifest["genres"]:
        adapter = registry.resolve_genre(row["local_genre_id"])
        assert adapter.document_type == row["display_name"]
        assert adapter.drafting_rules.ruleset_id == row["ruleset_id"]
        assert adapter.drafting_rules.ruleset_hash == row["ruleset_hash"] == row["local_sha256"]
        assert hashlib.sha256((suite.SNAPSHOT_ROOT / row["prompt_path"]).read_bytes()).hexdigest() == row["ruleset_hash"]
    assert manifest["build"]["runtime_execution"] is False
    assert manifest["build"]["include_examples"] is False


def test_snapshot_is_text_only_complete_and_deterministic():
    first, second = suite.load_suite_manifest(), suite.load_suite_manifest()
    assert first == second
    entries = {entry["path"]: entry for entry in first["files"]}
    files = {path.relative_to(suite.SNAPSHOT_ROOT).as_posix()
             for path in suite.SNAPSHOT_ROOT.rglob("*") if path.is_file()}
    assert files == set(entries) | {"manifest.json"}
    for name in files:
        path = suite.SNAPSHOT_ROOT / name
        assert path.suffix.lower() not in {".ttf", ".otf", ".woff", ".woff2", ".exe", ".dll", ".zip"}
        assert name == "LICENSE" or path.suffix in {".md", ".py", ".toml", ".json"}
        path.read_bytes().decode("utf-8")
    for name, entry in entries.items():
        raw = (suite.SNAPSHOT_ROOT / name).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == entry["sha256"]
        assert hashlib.sha1(b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw).hexdigest() == entry["git_blob_sha1"]


def test_pinned_builder_reproduces_all_twenty_two_artifacts_without_snapshot_writes():
    from scripts.build_upstream_genre_snapshot import check_snapshot
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest()
              for path in suite.SNAPSHOT_ROOT.rglob("*") if path.is_file()}
    assert check_snapshot(suite.SNAPSHOT_ROOT) == suite.load_suite_manifest()
    after = {path: hashlib.sha256(path.read_bytes()).hexdigest()
             for path in suite.SNAPSHOT_ROOT.rglob("*") if path.is_file()}
    assert before == after


def test_migration_has_an_exact_scope_and_protected_owners_match_required_base():
    assert_upstream_suite_scope(ROOT)
    assert set(UPSTREAM_SUITE_CONTENT_SHA256) == set(UPSTREAM_SUITE_MIGRATIONS)
    for path, expected in COMMERCIAL_GENRE_CONTENT_SHA256.items():
        assert hashlib.sha256(upstream_base_source(ROOT, path)).hexdigest() == expected


@pytest.mark.parametrize("path", UPSTREAM_SUITE_MIGRATIONS)
def test_exact_migration_allowance_rejects_single_byte_future_edits(monkeypatch, path):
    original = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda p: original(p) + (b"\n" if p == ROOT / path else b""))
    with pytest.raises(AssertionError, match="differs from exact UPSTREAM-22-GENRE-MIGRATION content"):
        assert_upstream_suite_content(ROOT, path)


@pytest.mark.parametrize("path", sorted((set(PROTECTED_OWNER_PATHS) | set(COMMERCIAL_GENRE_CONTENT_SHA256)) - set(UPSTREAM_SUITE_MIGRATIONS)))
def test_upstream_migration_never_authorizes_protected_owner_edits(monkeypatch, path):
    original = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda p: original(p) + (b"\n" if p == ROOT / path else b""))
    with pytest.raises(AssertionError, match="UPSTREAM-22 protected owner changed"):
        assert_upstream_protected_owners(ROOT)


@pytest.mark.parametrize("kind", ["manifest", "license", "builder", "renderer", "genre_meta", "genre_spec", "prompt"])
def test_manifest_and_adopted_source_graph_tampering_fails_closed(monkeypatch, kind):
    manifest = suite.load_suite_manifest()
    genre = manifest["genres"][0]
    targets = {"manifest": "manifest.json", "license": "LICENSE",
        "builder": manifest["build"]["builder_path"], "renderer": manifest["build"]["renderer_path"],
        "genre_meta": genre["meta_path"], "genre_spec": genre["source_path"], "prompt": genre["prompt_path"]}
    target = suite.SNAPSHOT_ROOT / targets[kind]
    original = Path.read_bytes

    def corrupt(path):
        raw = original(path)
        return raw + b"\nchanged" if path == target else raw

    monkeypatch.setattr(Path, "read_bytes", corrupt)
    with pytest.raises(FullArticleContractError, match="UPSTREAM_SUITE_.*HASH_MISMATCH"):
        suite.load_suite_manifest()


@pytest.mark.parametrize("mutation", [
    "repository", "commit", "license", "license_hash", "runtime", "examples", "builder",
    "duplicate_genre", "missing_genre", "wrong_local_id", "wrong_alias", "wrong_source",
    "wrong_rules_hash", "traversal", "absolute_path", "binary", "duplicate_file", "git_blob",
])
def test_malformed_even_reauthenticated_provenance_fails_closed(monkeypatch, mutation):
    manifest = copy.deepcopy(suite.load_suite_manifest())
    if mutation in {"repository", "commit", "license", "license_hash"}:
        key = {"repository": "upstream_repository", "commit": "upstream_commit",
               "license": "upstream_license", "license_hash": "license_sha256"}[mutation]
        manifest[key] = "invalid"
    elif mutation in {"runtime", "examples", "builder"}:
        key = {"runtime": "runtime_execution", "examples": "include_examples", "builder": "builder_path"}[mutation]
        manifest["build"][key] = "invalid" if mutation == "builder" else True
    elif mutation == "duplicate_genre":
        manifest["genres"][1] = copy.deepcopy(manifest["genres"][0])
    elif mutation == "missing_genre":
        manifest["genres"].pop()
    elif mutation.startswith("wrong_"):
        key, value = {"wrong_local_id": ("local_genre_id", "forged"),
            "wrong_alias": ("aliases", ["工作总结"]), "wrong_source": ("source_path", "LICENSE"),
            "wrong_rules_hash": ("ruleset_hash", "0" * 64)}[mutation]
        manifest["genres"][0][key] = value
    elif mutation in {"traversal", "absolute_path", "binary"}:
        manifest["files"][0]["path"] = {"traversal": "../outside.md", "absolute_path": "/outside.md", "binary": "font.ttf"}[mutation]
    elif mutation == "duplicate_file":
        manifest["files"].append(copy.deepcopy(manifest["files"][0]))
    elif mutation == "git_blob":
        manifest["files"][0]["git_blob_sha1"] = "0" * 40
    raw = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
    original = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda path: raw if path == suite.MANIFEST_PATH else original(path))
    # Deliberately bypass just the immutable manifest hash to exercise the
    # structural validator; production never derives its pin from input.
    monkeypatch.setattr(suite, "MANIFEST_SHA256", hashlib.sha256(raw).hexdigest())
    with pytest.raises(FullArticleContractError, match="UPSTREAM_SUITE_PROVENANCE_INVALID"):
        suite.load_suite_manifest()


def test_summary_snapshot_bytes_and_contract_owners_match_exact_base():
    manifest = suite.load_suite_manifest()
    row, = [row for row in manifest["genres"] if row["upstream_genre_id"] == "summary"]
    rules = WORK_SUMMARY_ADAPTER.drafting_rules
    assert (suite.SNAPSHOT_ROOT / row["prompt_path"]).read_bytes() == (rules.root / rules.prompt_path).read_bytes()
    for path in ("backend/app/services/generation/genres/work_summary.py",
                 "backend/app/services/generation/genres/work_summary_commercial.py",
                 "backend/app/services/generation/genres/work_summary_retrieval.py",
                 "backend/app/services/generation/work_summary_quality_gate.py",
                 "backend/app/services/generation/official_document_drafting.py",
                 "backend/app/services/generation/commercial_genre_runtime.py",
                 "backend/app/services/generation/genres/commercial_gate.py",
                 "backend/app/services/generation/full_draft_generation.py"):
        baseline = subprocess.check_output(["git", "show", BASE_SHA + ":" + path], cwd=ROOT)
        assert (ROOT / path).read_bytes().replace(b"\r\n", b"\n") == baseline.replace(b"\r\n", b"\n"), path
