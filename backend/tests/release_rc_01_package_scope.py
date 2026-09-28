"""Exact Phase 3 resource/UI seams, independent of accepted Phase 1/2 locks."""
import ast
import hashlib
import subprocess

BASE_SHA = "e50981945773ac7263a5f90fb7f69569ff584098"
# Independently approved paths; never derive this set from the hash mapping.
APPROVED_PACKAGE_SEAMS = frozenset({
    "backend/app/config.py",
    "backend/app/main.py",
    "backend/app/routers/settings.py",
    "backend/app/services/app_settings.py",
    "backend/app/utils/runtime_paths.py",
    "backend/app/services/generation/writing_reference_pack.py",
    "frontend/src/pages/ModelConfigPage.jsx",
    "frontend/src/components/WritingReferenceSettings.jsx",
    "frontend/tests/pages/WritingReferenceSettings.test.mjs",
})
PACKAGE_SEAMS = {
    "backend/app/config.py": "262d8fda03234db340c7f211ab2eb00f415de35b853fa216e3994c83942c9745",
    "backend/app/main.py": "eb20c3c72e628c04009806f972f8b3dc235254764e245e7f6a96916bd60785e3",
    "backend/app/routers/settings.py": "4ec8c2ecdd0077a22a66bf5dc77cd39d06cac663679871eaa10f8642c7924120",
    "backend/app/services/app_settings.py": "d66e9452996725cda026a0901a1123e0546dc6f5e166536f94f99b99261ab29d",
    "backend/app/utils/runtime_paths.py": "169c00cf8ffa26601fc788f0afd8c318e4f279318aa55e245fe1f90cc2b75581",
    "backend/app/services/generation/writing_reference_pack.py": "aa55e86e6235ae65cdd3e9a9f100d06da29f6fba95e553cf85f523dc24d00258",
    "frontend/src/pages/ModelConfigPage.jsx": "c3cf24e289c4674ac9b3cd03db0ee0cb2ad5756f8b3d28faed2577de12eba6b7",
    "frontend/src/components/WritingReferenceSettings.jsx": "631fe54fabd248993fa2ae8fc5253237c35d43656136a10e0d40d6c0d406a85a",
    "frontend/tests/pages/WritingReferenceSettings.test.mjs": "72a804c32e33ac268367917cf09a4eeb04cad87d4bb3b1e6abb4577a63081596",
}


def assert_package_seam(root, path):
    assert set(PACKAGE_SEAMS) == APPROVED_PACKAGE_SEAMS, "Phase 3 approved packaging seam set changed"
    assert path in PACKAGE_SEAMS, "Not a Phase 3 packaging seam: " + path
    raw = (root / path).read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(raw).hexdigest() == PACKAGE_SEAMS[path], "Phase 3 exact content changed: " + path
    if path.endswith("writing_reference_pack.py"):
        before = ast.parse(subprocess.check_output(["git", "show", f"{BASE_SHA}:{path}"], cwd=root))
        after = ast.parse(raw)
        original = before.body[-1]
        loader, validator = after.body[-2:]
        assert original.name == loader.name == "load_writing_reference_pack"
        assert validator.name == "validate_writing_reference_bytes"
        dump = lambda nodes: [ast.dump(node, include_attributes=False) for node in nodes]
        assert dump(before.body[:-1]) == dump(after.body[:-2]), "Schema/owner/context changes are forbidden"
        assert dump(original.body[:-2]) == dump(loader.body[:-1]), "Existing read/error behavior must stay identical"
        assert dump(original.body[-2:]) == dump(validator.body[1:]), "Validation must be the original implementation"
        assert ast.dump(loader.body[-1]) == ast.dump(ast.parse("return validate_writing_reference_bytes(raw)").body[0])
