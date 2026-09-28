"""Exact UI/Scene integration seams; historical approvals remain immutable.

The reviewed candidate hashes permit Registry inspection, query preparation
and catalog/scene metadata at the API edge. Independent AST checks preserve
all other owner nodes, including P1 weights and retrieval orchestration.
"""
import ast
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
import subprocess


GENRE_UI_SCENE_BASE_SHA = "d9b6e68d698e9d7b97dc92f23f8feffd80360c5a"
REGISTRY_PATH = "backend/app/services/generation/genres/registry.py"
RETRIEVER_PATH = "backend/app/services/retrieval/document_retriever.py"
ROUTER_PATH = "backend/app/routers/generation.py"
SCENE_OWNER_PATH = "backend/app/services/retrieval/scene_classifier.py"
CATALOG_PATH = "backend/app/services/generation/genres/catalog.py"
NEW_BACKEND_PATHS = (CATALOG_PATH,)
FRONTEND_CONTENT_SHA256 = {
    # RELEASE-RC-01 Phase 2 Repair: exact accepted Phase 1 navigation/lifecycle
    # bytes, based on main e50981945773ac7263a5f90fb7f69569ff584098.
    # Historical BASEs, protected backend owners and guard algorithms stay fixed.
    "frontend/src/App.jsx": "e27f337faa06031a762a1516b39bf45d272ed0e10ca5150595ef706c86cb58f1",
    "frontend/src/api.js": "083238e2dcb5410781e4cab6fd7dd5065c159b10d947ba9c01303b1a93fb39d1",
    "frontend/src/pages/ArticleGenPage.jsx": "f809320467dd6badd318ec13617c70569c30bd80f4d17ffa7a0f3c325a398b58",
    "frontend/src/pages/BatchTaskPage.jsx": "60cd69f4f16b16b771ff4d9e0af417a06489f1b0e678f438a5f093332201b224",
    "frontend/src/utils/batchExcelImport.js": "332dcfc2bc0d129cbe7e850a52792d66fcabf9304de6dbf8fde6c15318e28e49",
    "frontend/src/utils/batchTaskParams.js": "c1497b9a17a555c1aa21d5bf3bcee9409daf628e902b2c3b682ec2e759352a85",
    "frontend/tests/utils/batchExcelImport.test.mjs": "74f4515c7576019bbaa797766ee2cc22474347d57a228fa1b97879c7de2c20a5",
    "frontend/tests/utils/batchTaskParams.test.mjs": "00b02c2dde863193f59266bc2dedaffa3c33b3e009ddbce39b585b872ba07d7f",
    "frontend/src/components/WritingCatalogSelects.jsx": "3defcb035b1291c2e24ebffa40409cfb39b759f9d16d061cdc855f17e3b7090f",
    "frontend/src/utils/genreCatalog.js": "72de34ed88b776ef53eb0179a53be3086cfe10f636d6789508b3d7a7949c4a58",
    "frontend/tests/fixtures/genreCatalog.mjs": "fe4eba2a45961b4169cc7576fe083621cad6791d4194ecf00e029811f4f0f9d0",
    "frontend/tests/pages/ArticleGenPage.genre-scene-routing.test.mjs": "6d74fc9449104f5a333dde5a898afe31eb523a253e4710b9dc01e320612b094f",
    "frontend/tests/utils/genreCatalog.test.mjs": "e25c0bb5809843b104e6216dfbef868d6abcef17f1681fb97d38e8209bad425a",
}
GENRE_UI_SCENE_MIGRATIONS = (
    REGISTRY_PATH, RETRIEVER_PATH, ROUTER_PATH, SCENE_OWNER_PATH, *NEW_BACKEND_PATHS, *FRONTEND_CONTENT_SHA256,
)
GENRE_UI_SCENE_CONTENT_SHA256 = {
    REGISTRY_PATH: "9dba888afc964973b2e3d1f91dda3b4862feeda35ce914e3034b9246647e42cc",
    RETRIEVER_PATH: "0e9c1d0101b0c0a42633f94c1a98132b40867d482dbabc948a4905af80cb74dd",
    ROUTER_PATH: "fc928e35f0a1541d5d704d4e86f522047b901c0fd2cadff1d80afa89f5bd1a30",
    CATALOG_PATH: "28929f0591d3badd3dbf4ab2e223db545b1c7155be8be26b4475f1a479cefdf2",
    **FRONTEND_CONTENT_SHA256,
}
QUERY_PREPARATION_SEAMS = ("_field_payload", "prepare_retrieval_request")
# SCENE-CONTRACT-01 supersedes only the request/metadata boundary.
# BUSINESS-DOMAIN-TAXONOMY-01 reseals exact frontend/Router bytes only;
# path lists and other owners stay fixed, with exact Router AST checks below.
SCENE_CONTRACT_CONTENT_SHA256 = {
    ROUTER_PATH: "e1f182b044b57d02b82e42fd5bcb9a1c12b34aa5814a88b4438532eb133d4be3",
    SCENE_OWNER_PATH: "45fdef0ec6250f480e1f0a328421bea2114be47b39b0d2078f07ceb95ec02de1",
}
# PR #18 introduced these files after the historical directory guards' BASEs.
# They receive no migration allowance: current bytes must equal the merge BASE.
MERGED_UPSTREAM_OWNER_PATHS = (
    "backend/app/services/generation/genres/upstream_adapters.py",
    "backend/app/services/generation/genres/upstream_genre_contracts.py",
    "backend/app/services/generation/genres/upstream_genre_quality.py",
    "backend/app/services/generation/genres/upstream_suite.py",
)


@lru_cache(maxsize=None)
def integration_base_source(root: Path, path: str) -> bytes:
    return subprocess.check_output(
        ["git", "show", f"{GENRE_UI_SCENE_BASE_SHA}:{path}"], cwd=root,
    ).replace(b"\r\n", b"\n")


def _protected_ast(source: bytes, path: str) -> str:
    tree = ast.parse(source)
    if path == RETRIEVER_PATH:
        tree.body = [node for node in tree.body
                     if not isinstance(node, ast.FunctionDef) or node.name not in QUERY_PREPARATION_SEAMS]
    elif path == REGISTRY_PATH:
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name == "GenreRegistry":
                node.body = [item for item in node.body
                             if not isinstance(item, ast.FunctionDef) or item.name != "all"]
    elif path == SCENE_OWNER_PATH:
        tree.body = [node for node in tree.body if not (
            isinstance(node, ast.FunctionDef) and node.name == "normalize_generation_scene")]
    elif path == ROUTER_PATH:
        tree.body = [node for node in tree.body if not isinstance(node, ast.AsyncFunctionDef)
                     or node.name != "get_genre_catalog"]
        # BUSINESS-DOMAIN-TAXONOMY-01: remove only the exact read-only endpoint,
        # never exclude another handler or enlarge the protected path lists.
        domain_endpoint = ast.parse('''
@router.get("/business-domains")
async def get_business_domain_catalog():
    """Read-only task context names; no evidence or production authority."""
    from app.services.business_domain_catalog import business_domain_catalog

    return business_domain_catalog()
''').body[0]
        tree.body = [node for node in tree.body
                     if ast.dump(node) != ast.dump(domain_endpoint)]
        # Exact statements only; no blanket exclusion of the HTTP handler or
        # its delivery/error/logging authority is permitted.
        allowed_source = '''
if req.scene:
    for article in batch.articles:
        article.scene = req.scene
from app.services.retrieval.scene_classifier import normalize_generation_scene
from app.services.business_domain_catalog import normalize_business_domain
req.business_domain = normalize_business_domain(req.business_domain)
try:
    req.scene = normalize_generation_scene(req.scene)
except ValueError as exc:
    raise HTTPException(
        status_code=422,
        detail=_public_error_detail("INVALID_SCENE", str(exc), field="scene"),
    ) from exc
for article in batch.articles:
    article.scene = req.scene or ""
'''
        allowed = {ast.dump(node, include_attributes=False) for node in ast.parse(allowed_source).body}
        for node in tree.body:
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "generate_article":
                node.body = [item for item in node.body
                             if ast.dump(item, include_attributes=False) not in allowed]
                # Result metadata lives inside the existing generation try.
                for item in node.body:
                    if isinstance(item, ast.Try):
                        item.body = [statement for statement in item.body
                                     if ast.dump(statement, include_attributes=False) not in allowed]
    return ast.dump(tree, include_attributes=False)


def assert_integration_content(root: Path, path: str, *, message="") -> None:
    assert path in GENRE_UI_SCENE_MIGRATIONS, message or "Not an authorized UI/Scene seam: " + path
    current = (root / path).read_bytes().replace(b"\r\n", b"\n")
    pins = SCENE_CONTRACT_CONTENT_SHA256 if path in SCENE_CONTRACT_CONTENT_SHA256 else GENRE_UI_SCENE_CONTENT_SHA256
    assert sha256(current).hexdigest() == pins[path], (
        message or path + " differs from exact GENRE-UI-SCENE-ROUTING-INTEGRATION content")
    if path in NEW_BACKEND_PATHS or path in FRONTEND_CONTENT_SHA256:
        return
    assert _protected_ast(current, path) == _protected_ast(integration_base_source(root, path), path), (
        message or "UI/Scene migration changed a protected AST owner: " + path)


def assert_query_preparation_migration(root: Path) -> None:
    assert_integration_content(root, RETRIEVER_PATH)


def protected_owner_paths() -> tuple[str, ...]:
    from commercial_genre_scope import COMMERCIAL_GENRE_CONTENT_SHA256, PROTECTED_OWNER_PATHS
    from multi_genre_scope import MULTI_GENRE_CONTENT_SHA256, SHARED_CORE_UNCHANGED
    from upstream_suite_scope import UPSTREAM_SUITE_MIGRATIONS
    paths = (set(COMMERCIAL_GENRE_CONTENT_SHA256) | set(PROTECTED_OWNER_PATHS)
             | set(MULTI_GENRE_CONTENT_SHA256) | set(SHARED_CORE_UNCHANGED)
             | set(UPSTREAM_SUITE_MIGRATIONS)) - set(GENRE_UI_SCENE_MIGRATIONS)
    paths.update((*MERGED_UPSTREAM_OWNER_PATHS, "backend/tests/run_multi_genre_offline.py"))
    return tuple(sorted(paths))


def assert_integration_protected_owner(root: Path, path: str) -> None:
    assert path in protected_owner_paths()
    assert (root / path).read_bytes().replace(b"\r\n", b"\n") == integration_base_source(root, path), (
        "UI/Scene protected owner changed: " + path)


def assert_integration_protected_owners(root: Path) -> None:
    for path in protected_owner_paths():
        assert_integration_protected_owner(root, path)


def assert_vendor_bytes_unchanged(root: Path) -> None:
    paths = subprocess.check_output(
        ["git", "ls-tree", "-r", "-z", "--name-only", GENRE_UI_SCENE_BASE_SHA,
         "--", "backend/vendor/official_document_drafting"], cwd=root,
    ).decode("utf-8").rstrip("\0").split("\0")
    assert paths, "Pinned upstream files are absent from the required BASE"
    for path in paths:
        baseline = subprocess.check_output(
            ["git", "show", f"{GENRE_UI_SCENE_BASE_SHA}:{path}"], cwd=root,
        )
        assert (root / path).read_bytes() == baseline, (
            "UI/Scene pinned upstream file changed: " + path)


def assert_integration_scope(root: Path) -> None:
    assert set(GENRE_UI_SCENE_CONTENT_SHA256) | set(SCENE_CONTRACT_CONTENT_SHA256) == set(GENRE_UI_SCENE_MIGRATIONS)
    for path in GENRE_UI_SCENE_MIGRATIONS:
        assert_integration_content(root, path)
    assert_integration_protected_owners(root)
    assert_vendor_bytes_unchanged(root)
