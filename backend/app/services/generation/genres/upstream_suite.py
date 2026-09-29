"""Immutable text-only upstream suite; no upstream executable runs in production.

This owner authenticates provenance and composes existing genre capabilities.
The shared drafting loader still owns prompt byte checks on every invocation.
Evidence, production admission and delivery remain in their existing owners.
"""
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import tomllib

from app.models.full_article import FullArticleContractError
from .contracts import DraftingRules, GenreAdapter, GenreCapabilities


UPSTREAM_REPOSITORY = "https://github.com/zhaohui-yang/official-document-drafting"
UPSTREAM_COMMIT = "6b4166a50b96733684b36da6336ea85a847cf3be"
UPSTREAM_TREE = "b649efe9a575dcf86cd1428b18605216b7260a63"
UPSTREAM_LICENSE = "MIT"
SNAPSHOT_ROOT = Path(__file__).resolve().parents[4] / "vendor/official_document_drafting" / UPSTREAM_COMMIT
MANIFEST_PATH = SNAPSHOT_ROOT / "manifest.json"
# Filled from the deterministic acquisition/build manifest, never from runtime bytes.
MANIFEST_SHA256 = "69d3b0d1a830f070d97884a31fb620fe65b16f132906d8a8ffbeb308208272b9"
LICENSE_SHA256 = "2d8ba6968842d2d058a2f1f02f8a5252603e32124d79b684edf2ca119acd6a38"
# Derived from the pinned meta.toml catalog, with the existing host identities
# retained. In particular, historical `report` requests mean 汇报材料.
LOCAL_GENRE_IDS = {"summary": "work_summary", "presentation": "report", "report": "statutory_report"}
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_SHA1 = re.compile(r"[0-9a-f]{40}\Z")


def _require(condition):
    if not condition:
        raise FullArticleContractError("UPSTREAM_SUITE_PROVENANCE_INVALID")


def _path(value):
    _require(type(value) is str and bool(value) and "\\" not in value and ":" not in value)
    path = PurePosixPath(value)
    _require(not path.is_absolute() and ".." not in path.parts and str(path) == value)
    return value


def _read(root, name):
    name = _path(name)
    target = root / name
    _require(target.resolve().is_relative_to(root.resolve()))
    try:
        return target.read_bytes()
    except OSError as exc:
        raise FullArticleContractError("UPSTREAM_SUITE_UNAVAILABLE") from exc


def load_suite_manifest(root=None):
    """Authenticate the exact catalog, adopted source graph and artifact bytes.

    No cache: changing any source, license, builder, prompt or manifest closes
    admission. Expected digests are pinned in code/the authenticated manifest.
    """
    root = Path(root) if root is not None else SNAPSHOT_ROOT
    raw = _read(root, "manifest.json")
    if hashlib.sha256(raw).hexdigest() != MANIFEST_SHA256:
        raise FullArticleContractError("UPSTREAM_SUITE_MANIFEST_HASH_MISMATCH")
    try:
        manifest = json.loads(raw)
        _validate_manifest(root, manifest)
    except (KeyError, TypeError, ValueError, UnicodeError) as exc:
        if isinstance(exc, FullArticleContractError):
            raise
        raise FullArticleContractError("UPSTREAM_SUITE_PROVENANCE_INVALID") from exc
    return manifest


def _validate_manifest(root, manifest):
    _require(type(manifest) is dict and manifest["schema_version"] == 1)
    _require(manifest["upstream_repository"] == UPSTREAM_REPOSITORY
             and manifest["upstream_commit"] == UPSTREAM_COMMIT
             and manifest["upstream_tree"] == UPSTREAM_TREE
             and manifest["upstream_license"] == UPSTREAM_LICENSE
             and manifest["license_path"] == "LICENSE"
             and manifest["license_sha256"] == LICENSE_SHA256)
    build = manifest["build"]
    _require(build["profile"] == "default" and build["include_examples"] is False
             and build["runtime_execution"] is False)
    files = manifest["files"]
    _require(type(files) is list and bool(files))
    verified = {}
    for item in files:
        name = _path(item["path"])
        _require(name not in verified and type(item["sha256"]) is str
                 and _SHA256.fullmatch(item["sha256"]) is not None
                 and type(item["git_blob_sha1"]) is str
                 and _SHA1.fullmatch(item["git_blob_sha1"]) is not None)
        _require(name == "LICENSE" or PurePosixPath(name).suffix in {".md", ".py", ".toml"})
        data = _read(root, name)
        if hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise FullArticleContractError("UPSTREAM_SUITE_FILE_HASH_MISMATCH")
        _require(len(data) == item["size"] and hashlib.sha1(
            b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest() == item["git_blob_sha1"])
        data.decode("utf-8")
        verified[name] = data
    _require(hashlib.sha256(verified["LICENSE"]).hexdigest() == LICENSE_SHA256)
    _require(build["builder_path"] in verified and build["renderer_path"] in verified)
    _require(type(build["builder_source_paths"]) is list and bool(build["builder_source_paths"])
             and all(name in verified for name in build["builder_source_paths"]))
    genres = manifest["genres"]
    metas = {name for name in verified if name.startswith("prompts/doc-types/") and name.endswith("/meta.toml")}
    _require(type(genres) is list and len(genres) == len(metas) == 22)
    seen_ids, seen_metas, seen_prompts, seen_local = set(), set(), set(), set()
    for row in genres:
        upstream_id = row["upstream_genre_id"]
        _require(type(upstream_id) is str and upstream_id not in seen_ids)
        local_id = LOCAL_GENRE_IDS.get(upstream_id, upstream_id)
        _require(row["local_genre_id"] == local_id and local_id not in seen_local)
        meta_path = _path(row["meta_path"])
        _require(meta_path in metas and meta_path not in seen_metas)
        meta = tomllib.loads(verified[meta_path].decode("utf-8"))
        _require(meta["id"] == upstream_id and meta["title"] == row["title"]
                 and meta.get("display_name", meta["title"]) == row["display_name"]
                 and meta.get("aliases", []) == row["aliases"]
                 and meta["category"] == row["category"]
                 and meta["font_profile"] == row["font_profile"]
                 and meta["layout_profile"] == row["layout_profile"]
                 and meta["description"] == row["description"])
        _require(row["source_path"] == str(PurePosixPath(meta_path).with_name("spec.md"))
                 and row["source_path"] in verified and row["template_path"] in verified
                 and row["template_path"] == f"assets/templates/{upstream_id}.md")
        prompt = _path(row["prompt_path"])
        _require(prompt in verified and prompt not in seen_prompts
                 and prompt == f"dist/offline/default/doc-types/{upstream_id}-{meta.get('dir_label', row['display_name'])}/system_prompt.md")
        digest = hashlib.sha256(verified[prompt]).hexdigest()
        _require(row["ruleset_hash"] == row["local_sha256"] == digest
                 and row["ruleset_id"] == f"zhaohui-yang/official-document-drafting@{UPSTREAM_COMMIT}/offline/default/{upstream_id}")
        seen_ids.add(upstream_id)
        seen_local.add(local_id)
        seen_metas.add(meta_path)
        seen_prompts.add(prompt)
    _require(seen_metas == metas)


def build_suite_adapters():
    """Supply 21 fully configured, engineering-only genres to the registry.

    Summary remains the original reference adapter with unchanged bytes and
    behavior. Its suite artifact is present only for provenance/rebuild parity.
    """
    from .upstream_genre_contracts import (
        commercial_contract, retrieval_profile, structure_contract,
    )
    from .upstream_genre_quality import quality_evaluator
    from .commercial_gate import validate_commercial_contract

    manifest = load_suite_manifest()
    locks = (("manifest.json", MANIFEST_SHA256),) + tuple(
        (item["path"], item["sha256"]) for item in manifest["files"])
    adapters = []
    for row in manifest["genres"]:
        upstream_id, local_id = row["upstream_genre_id"], row["local_genre_id"]
        if upstream_id == "summary":
            continue
        # `report` is already owned by historical 汇报材料. Its statutory
        # counterpart resolves via statutory_report / 报告, never a collision.
        upstream_alias = () if upstream_id == "report" else (upstream_id,)
        aliases = tuple(dict.fromkeys((local_id, *upstream_alias, row["title"], *row["aliases"])))
        adapter = GenreAdapter(
            genre_id=local_id, document_type=row["display_name"], aliases=aliases,
            retrieval_profile=retrieval_profile(upstream_id),
            drafting_rules=DraftingRules(
                ruleset_id=row["ruleset_id"], ruleset_hash=row["ruleset_hash"], root=SNAPSHOT_ROOT,
                prompt_path=row["prompt_path"], integrity_locks=locks,
                profile_label="default", task_label=f"{row['display_name']}（{upstream_id}）",
                reference_policy="none", provenance="pinned_upstream"),
            structure_contract=structure_contract(upstream_id),
            quality_contract_id=f"upstream_{upstream_id}_quality_v1",
            quality_evaluator=quality_evaluator(upstream_id),
            capabilities=GenreCapabilities(status="VERIFIED_CORE_GENRE", production_ready=True,
                                           offline_architecture_proof=True),
            fact_policy_extensions=("PRESERVE_PLAN_MODALITY",),
            commercial=commercial_contract(local_id, upstream_id),
        )
        validate_commercial_contract(adapter)
        adapters.append(adapter)
    return tuple(adapters)
