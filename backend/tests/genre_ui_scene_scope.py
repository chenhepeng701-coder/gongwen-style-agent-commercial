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
    "frontend/src/App.jsx": "c3ace3f5a94c441ac0c3db483cd0a9b170fe218e992e5b45c1997cbb86bfb32d",
    "frontend/src/api.js": "9ee4e92b81e61ce9f7991d7c37a8ab39487bd78cd27fdf89be86ad45a9201ab3",
    "frontend/src/pages/ArticleGenPage.jsx": "e04a9e5ffdd8584e221843206d208a78859b86129d8c2a3b58f5c5b40fff6c4f",
    "frontend/src/pages/BatchTaskPage.jsx": "795296e25f298b4d65de068e728b4881c5e38456e29f448696ef79303d767dbf",
    "frontend/src/utils/batchExcelImport.js": "21c04ccf4605fcaf816f2a690eee9f3b1cfbf2c43274769ffd169e7a1a732f8a",
    "frontend/src/utils/batchTaskParams.js": "9095acbe5b3393fe21302166231422e8a55c2dd9b013e7b6515e4b3738043246",
    "frontend/tests/utils/batchExcelImport.test.mjs": "902fc2ab9522e257bb8505c098b1cd57951d2e7eafbb1c5c0fc6abea00d59ff9",
    "frontend/tests/utils/batchTaskParams.test.mjs": "dda652817e127c3648c110b70ccc58253f55b4f69a023ac590eae4cf2496c842",
    "frontend/src/components/WritingCatalogSelects.jsx": "92b3d4a73b1abc2a8f2122acbc84e486617a598a62118da2bea40dfba4c0df0f",
    "frontend/src/utils/genreCatalog.js": "3f2298793fdb1a7a6aa778cdbcf6413435d8871e1683a8058e306948e0ec2681",
    "frontend/tests/fixtures/genreCatalog.mjs": "3e867337bcabab874e87f65fbb8623990a28684b42eb3c318e50d49612f3be90",
    "frontend/tests/pages/ArticleGenPage.genre-scene-routing.test.mjs": "5884b1c86818bef1a571df7102cdd7ddcbb4f32ad33b63b4a2ddc92c57f55d31",
    "frontend/tests/utils/genreCatalog.test.mjs": "a1719f95387949d33bea9548f150d708fc5dd40672dafa274731d44284618ebd",
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
# SCENE-CONTRACT-01 supersedes only the request/metadata boundary. Original
# integration pins above remain intact; all other AST owners still equal BASE.
SCENE_CONTRACT_CONTENT_SHA256 = {
    ROUTER_PATH: "9802cb64447adaeb125ce8752e86db33dbd089cc2b5bbe0123669f35cd322bf8",
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
        # Exact statements only; no blanket exclusion of the HTTP handler or
        # its delivery/error/logging authority is permitted.
        allowed_source = '''
if req.scene:
    for article in batch.articles:
        article.scene = req.scene
from app.services.retrieval.scene_classifier import normalize_generation_scene
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
