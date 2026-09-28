"""Exact frontend file approvals for RELEASE-RC-01 Phase 2 Repair only.

No historical baseline, existing path list or backend authority is replaced.
The four Waiting UX files equal merged PR #22's main tree
e50981945773ac7263a5f90fb7f69569ff584098 (following merged PR #21).
The navigation test is the accepted Phase 1 regression coverage.
"""
from hashlib import sha256
from pathlib import Path


RELEASE_RC_01_FRONTEND_CONTENT_SHA256 = {
    "frontend/src/App.css": "bcc9a4f055ee4fb440910950c0f36b0ef7d707798dd260b8f771db34ac6a5ee8",
    "frontend/src/pages/GenerationPage.jsx": "dcc83278ffe8c7d0f86ffeb42e3e92f7737382d4ba14f341cc023d7c98a8b42a",
    "frontend/src/components/GenerationWaitingIndicator.jsx": "72c16d54c540c71e695a37d405afb83c7354461decb6bc4125af1f5f3739ddb7",
    "frontend/tests/pages/GenerationPage.waiting-ux.test.mjs": "14fbfc1f766bbcac158540c7600ffe3bd7e30efc4a210c5af00837fe4a369c1d",
    "frontend/tests/pages/App.generation-navigation-guard.test.mjs": "17eeea5a10fb91c1d470d00ca4444df10f54f6001828475cb00477ae5cd0e152",
}


def assert_release_rc_01_frontend_content(root: Path, path: str) -> None:
    assert path in RELEASE_RC_01_FRONTEND_CONTENT_SHA256, (
        "Not an authorized RELEASE-RC-01 frontend path: " + path)
    current = (root / path).read_bytes().replace(b"\r\n", b"\n")
    assert sha256(current).hexdigest() == RELEASE_RC_01_FRONTEND_CONTENT_SHA256[path], (
        path + " differs from exact RELEASE-RC-01 frontend content")
