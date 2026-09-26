"""Build/check the pinned text-only genre snapshot, entirely offline.

Acquisition is deliberately separate: --source-root must already contain the
authorized pinned GitHub commit.json, commit-tree.json and raw text files.
Production loads immutable system_prompt.md files; it never imports this builder.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
from pathlib import Path, PurePosixPath
import re
import sys
import tomllib


REPOSITORY = "https://github.com/zhaohui-yang/official-document-drafting"
COMMIT = "6b4166a50b96733684b36da6336ea85a847cf3be"
TREE = "b649efe9a575dcf86cd1428b18605216b7260a63"
LICENSE_SHA256 = "2d8ba6968842d2d058a2f1f02f8a5252603e32124d79b684edf2ca119acd6a38"
SUMMARY_SHA256 = "80c99367127e6eabca3a5329ec745bcd5bad46734b10aa577a478f4627f858b1"
BACKEND = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = BACKEND / "vendor" / "official_document_drafting" / COMMIT
GENRE_ID_OVERRIDES = {
    "summary": "work_summary", "presentation": "report", "report": "statutory_report",
}
BUILDER_PATHS = (
    "src/adapters/doc_types.py", "src/adapters/offline/build.py",
    "src/adapters/paths.py", "src/adapters/profiles.py",
    "src/adapters/rendering.py", "src/adapters/shared.py",
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git_blob_sha1(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()


def is_adopted(path: str) -> bool:
    """Whitelist text only; no upstream executable lifecycle/exporter/networking."""
    return bool(
        path == "LICENSE" or path in BUILDER_PATHS
        or path in {"assets/fonts/catalog.toml", "assets/fonts/README.md", "tests/test_offline_build.py"}
        or re.fullmatch(r"prompts/(?:core|doc-types|profiles|font-profiles|layout-profiles)/.+\.(?:md|toml)", path)
        or re.fullmatch(r"assets/templates/[^/]+\.md", path)
        or re.fullmatch(r"dist/offline/default/doc-types/[^/]+/system_prompt\.md", path)
    )


def safe_path(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or "\\" in relative:
        raise ValueError("Snapshot path must use relative POSIX notation")
    parts = PurePosixPath(relative)
    if parts.is_absolute() or not parts.parts or any(p in {".", ".."} for p in parts.parts):
        raise ValueError(f"Unsafe snapshot path: {relative!r}")
    result = root.joinpath(*parts.parts)
    if not result.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"Snapshot path escaped root: {relative!r}")
    return result


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def verify_git_tree(tree: dict) -> None:
    """Authenticate every recursive path/blob mapping against the pinned root tree."""
    children: dict[str, list[dict]] = {"": []}
    expected = {"": TREE}
    for entry in tree["tree"]:
        path = PurePosixPath(entry["path"])
        parent = str(path.parent)
        if parent == ".":
            parent = ""
        children.setdefault(parent, []).append({**entry, "name": path.name})
        if entry["type"] == "tree":
            expected[entry["path"]] = entry["sha"]
            children.setdefault(entry["path"], [])
    if set(children) != set(expected):
        raise ValueError("Recursive Git tree has missing or unparented tree objects")
    for directory, entries in children.items():
        ordered = sorted(entries, key=lambda e: (e["name"] + ("/" if e["type"] == "tree" else "")).encode("utf-8"))
        payload = b"".join(
            str(int(entry["mode"])).encode("ascii") + b" "
            + entry["name"].encode("utf-8") + b"\0" + bytes.fromhex(entry["sha"])
            for entry in ordered
        )
        actual = hashlib.sha1(b"tree " + str(len(payload)).encode("ascii") + b"\0" + payload).hexdigest()
        if actual != expected[directory]:
            raise ValueError(f"Recursive Git tree path/blob mapping is not authentic: {directory or '/'}")


def write_immutable(path: Path, data: bytes) -> None:
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError(f"Refusing to overwrite a differing snapshot file: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def file_role(path: str) -> str:
    if path == "LICENSE":
        return "license"
    if path.startswith("dist/"):
        return "generated_system_prompt"
    if path in BUILDER_PATHS:
        return "offline_builder"
    if path.startswith("tests/"):
        return "upstream_catalog_test"
    if path.startswith("assets/fonts/"):
        return "font_metadata_only"
    return "upstream_source"


def catalog(root: Path, hashes: dict[str, str]) -> list[dict]:
    result = []
    for path in sorted((root / "prompts/doc-types").glob("*/meta.toml")):
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        genre_id = raw["id"]
        label = raw["dir_label"]
        source = f"prompts/doc-types/{genre_id}-{label}"
        prompt = f"dist/offline/default/doc-types/{genre_id}-{label}/system_prompt.md"
        if path.parent.name != f"{genre_id}-{label}":
            raise ValueError(f"Upstream directory does not match canonical metadata: {path}")
        result.append({
            "upstream_genre_id": genre_id,
            "local_genre_id": GENRE_ID_OVERRIDES.get(genre_id, genre_id),
            "display_name": raw["display_name"], "title": raw["title"],
            "aliases": raw["aliases"], "category": raw["category"],
            "description": raw["description"],
            "source_path": source + "/spec.md", "meta_path": source + "/meta.toml",
            "template_path": f"assets/templates/{genre_id}.md", "prompt_path": prompt,
            "ruleset_id": f"zhaohui-yang/official-document-drafting@{COMMIT}/offline/default/{genre_id}",
            "ruleset_hash": hashes[prompt], "local_sha256": hashes[prompt],
            "font_profile": raw["font_profile"], "layout_profile": raw["layout_profile"],
        })
    if len(result) != 22 or len({g["upstream_genre_id"] for g in result}) != 22:
        raise ValueError("Pinned upstream catalog must contain exactly 22 distinct genres")
    categories = [g["category"] for g in result]
    if categories.count("法定公文") != 15 or categories.count("常见正式材料") != 7:
        raise ValueError("Pinned upstream category counts drifted")
    lookup: dict[str, str] = {}
    for genre in result:
        for key in (genre["upstream_genre_id"], genre["display_name"], *genre["aliases"]):
            normalized = key.strip().lower().replace("_", "-")
            if normalized in lookup and lookup[normalized] != genre["upstream_genre_id"]:
                raise ValueError(f"Ambiguous upstream alias: {key}")
            lookup[normalized] = genre["upstream_genre_id"]
    return result


def build_snapshot(source: Path, root: Path) -> dict:
    commit = read_json(source / "commit.json")
    tree = read_json(source / "commit-tree.json")
    if commit["sha"] != COMMIT or commit["tree"]["sha"] != TREE:
        raise ValueError("Acquired commit does not match the exact pinned commit/tree")
    if tree["sha"] != TREE or tree.get("truncated") is not False:
        raise ValueError("Acquired tree is not the full pinned commit tree")
    verify_git_tree(tree)
    entries = sorted((e for e in tree["tree"] if e["type"] == "blob" and is_adopted(e["path"])), key=lambda e: e["path"])
    files = []
    for entry in entries:
        relative = entry["path"]
        data = safe_path(source, relative).read_bytes()
        data.decode("utf-8")
        if b"\r" in data or data.startswith(b"\xef\xbb\xbf"):
            raise ValueError(f"Upstream text encoding/newlines changed: {relative}")
        if len(data) != entry["size"] or git_blob_sha1(data) != entry["sha"]:
            raise ValueError(f"Acquired bytes do not match pinned Git blob: {relative}")
        write_immutable(safe_path(root, relative), data)
        files.append({"path": relative, "sha256": sha256(data), "git_blob_sha1": entry["sha"], "size": len(data), "role": file_role(relative)})
    hashes = {f["path"]: f["sha256"] for f in files}
    if hashes.get("LICENSE") != LICENSE_SHA256:
        raise ValueError("Exact pinned MIT license was not preserved")
    manifest = {
        "schema_version": 1, "upstream_repository": REPOSITORY,
        "upstream_commit": COMMIT, "upstream_tree": TREE,
        "upstream_license": "MIT", "license_path": "LICENSE", "license_sha256": LICENSE_SHA256,
        "copyright_notice": "Copyright (c) 2026 official-document-drafting contributors",
        # Logical project path stays deterministic even when verification uses
        # a temporary --snapshot-root on another machine.
        "local_snapshot_path": DEFAULT_ROOT.relative_to(BACKEND.parent).as_posix(),
        "build": {
            "profile": "default", "builder_path": "src/adapters/offline/build.py",
            "renderer_path": "src/adapters/rendering.py", "builder_source_paths": list(BUILDER_PATHS),
            "function": "build_doc_type_prompt_bundle", "artifact_key": "system_prompt.md",
            "include_examples": False, "runtime_execution": False,
            "artifact_origin": "Exact pinned dist/offline/default/doc-types/*/system_prompt.md bytes; checked against pinned offline builder",
        },
        "files": files, "genres": catalog(root, hashes),
        "catalog_evidence_paths": ["prompts/doc-types/*/meta.toml", "tests/test_offline_build.py", "dist/offline/default/doc-types/*/system_prompt.md"],
        "excluded_font_binary_paths": sorted(e["path"] for e in tree["tree"] if e["type"] == "blob" and Path(e["path"]).suffix.lower() in {".ttf", ".otf", ".woff", ".woff2", ".ttc"}),
        "exclusions": ["All binary assets", "Upstream runtime/model/network/agent execution", "Upstream standalone DOCX exporter", "Upstream skill runtime", "Upstream user_prompt_template and combined prompt artifacts"],
    }
    verify_rendering(root, manifest)
    write_immutable(root / "manifest.json", (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    return manifest


def verify_rendering(root: Path, manifest: dict) -> None:
    """Import only the pinned stdlib renderer in this offline maintenance process."""
    old_bytecode = sys.dont_write_bytecode
    old_path = list(sys.path)
    saved_modules = {k: v for k, v in sys.modules.items() if k == "adapters" or k.startswith("adapters.")}
    try:
        sys.dont_write_bytecode = True
        for name in saved_modules:
            del sys.modules[name]
        sys.path.insert(0, str(root / "src"))
        shared = importlib.import_module("adapters.shared")
        builder = importlib.import_module("adapters.offline.build")
        profile = shared.load_profile("default")
        doc_types = shared.sort_doc_types(shared.load_doc_types(), profile.category_order)
        entries = {g["upstream_genre_id"]: g for g in manifest["genres"]}
        if {doc.id for doc in doc_types} != set(entries):
            raise ValueError("Upstream builder catalog does not match the manifest")
        for doc_type in doc_types:
            prompt = builder.build_doc_type_prompt_bundle(profile, doc_types, doc_type)["system_prompt.md"].encode("utf-8")
            stored = safe_path(root, entries[doc_type.id]["prompt_path"]).read_bytes()
            if prompt != stored:
                raise ValueError(f"Pinned generated prompt is not source/build equivalent: {doc_type.id}")
        for path, expected in shared.build_template_outputs(doc_types, profile.default_template).items():
            if path.read_bytes() != expected.encode("utf-8"):
                raise ValueError(f"Pinned generated template is not source/build equivalent: {path}")
    finally:
        sys.path[:] = old_path
        sys.dont_write_bytecode = old_bytecode
        for name in list(sys.modules):
            if name == "adapters" or name.startswith("adapters."):
                del sys.modules[name]
        sys.modules.update(saved_modules)
    summary = next(g for g in manifest["genres"] if g["upstream_genre_id"] == "summary")
    summary_bytes = safe_path(root, summary["prompt_path"]).read_bytes()
    original = BACKEND / "experiments/case_a_upstream_drafting_validation_32/upstream" / summary["prompt_path"]
    if sha256(summary_bytes) != SUMMARY_SHA256 or summary_bytes != original.read_bytes():
        raise ValueError("Work-summary reference prompt byte parity failed")


def check_snapshot(root: Path) -> dict:
    manifest = read_json(root / "manifest.json")
    if (manifest.get("upstream_repository"), manifest.get("upstream_commit"), manifest.get("upstream_tree"), manifest.get("upstream_license"), manifest.get("license_sha256")) != (REPOSITORY, COMMIT, TREE, "MIT", LICENSE_SHA256):
        raise ValueError("Snapshot provenance does not match the pinned upstream")
    paths = [item["path"] for item in manifest["files"]]
    if len(paths) != len(set(paths)):
        raise ValueError("Duplicate adopted file path")
    actual = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts}
    if actual != set(paths) | {"manifest.json"}:
        raise ValueError(f"Snapshot file inventory changed: {sorted(actual ^ (set(paths) | {'manifest.json'}))}")
    hashes = {}
    for entry in manifest["files"]:
        relative = entry["path"]
        if not is_adopted(relative):
            raise ValueError(f"Non-whitelisted snapshot asset: {relative}")
        data = safe_path(root, relative).read_bytes()
        data.decode("utf-8")
        if b"\r" in data or b"\0" in data:
            raise ValueError(f"Unexpected binary/line ending in snapshot: {relative}")
        if (sha256(data), git_blob_sha1(data), len(data)) != (entry["sha256"], entry["git_blob_sha1"], entry["size"]):
            raise ValueError(f"Snapshot integrity mismatch: {relative}")
        hashes[relative] = entry["sha256"]
    if hashes.get("LICENSE") != LICENSE_SHA256 or catalog(root, hashes) != manifest["genres"]:
        raise ValueError("Snapshot license/catalog identity drifted")
    verify_rendering(root, manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify bytes, provenance, source rebuild, catalog and summary parity")
    parser.add_argument("--source-root", type=Path, help="Previously acquired exact pinned source; never downloads anything")
    parser.add_argument("--snapshot-root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    root = args.snapshot_root.resolve()
    if args.check:
        manifest = check_snapshot(root)
    elif args.source_root:
        manifest = build_snapshot(args.source_root.resolve(), root)
        check_snapshot(root)
    else:
        parser.error("Choose --check or --source-root; no network acquisition is performed by this tool")
    print(json.dumps({"status": "PASS", "genres": len(manifest["genres"]), "files": len(manifest["files"]), "manifest_sha256": sha256((root / "manifest.json").read_bytes()), "work_summary_prompt_parity": True, "network_calls": 0}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
