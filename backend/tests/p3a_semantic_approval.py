"""Exact P3-A content approval; preserve every historical baseline and P2 hash."""
from hashlib import sha256
import json
from pathlib import Path

from p2_admission_approval import ATOMIC_EVIDENCE_PATH


P3A_BASE_SHA = "72af33cf4e6283c04c18ab87a30e2b506471feaa"
P3A_ATOMIC_EVIDENCE_SHA256 = "9936252cb6db55d8f1c1b0072348852f9a2ce520a84ecf13a8dc235fa1ebe8a6"
P3A_RECOGNITION_DELTA_SHA256 = "e1f0c5d9f43c79e4964896dd7b5d2799b65a2a6079e178e9fd918771cb9d6974"


def assert_p3a_atomic_evidence(root: Path) -> None:
    content = (root / ATOMIC_EVIDENCE_PATH).read_bytes().replace(b"\r\n", b"\n")
    assert sha256(content).hexdigest() == P3A_ATOMIC_EVIDENCE_SHA256, (
        ATOMIC_EVIDENCE_PATH + " differs from the exact P3-A-approved content"
    )


def historical_atomic_with_p3a_recognition(source: bytes) -> bytes:
    """Apply only the frozen recognition delta to a historical test oracle.

    This leaves admission, identity, cache, conflict and safety implementations
    at their original baseline. The delta itself is pinned; it never reads the
    working production owner or HEAD. Full-field parity assertions stay intact.
    P3-A tests separately execute the completely unmodified P3-A BASE_SHA.
    """
    raw = (Path(__file__).with_name("fixtures") / "p3a_atomic_recognition_delta.json").read_bytes()
    raw = raw.replace(b"\r\n", b"\n")
    assert sha256(raw).hexdigest() == P3A_RECOGNITION_DELTA_SHA256, "P3-A recognition delta changed"
    text = source.decode("utf-8").replace("\r\n", "\n")
    for region in json.loads(raw):
        assert text.count(region["before"]) == 1, "Historical Atomic does not match exact P3-A delta"
        text = text.replace(region["before"], region["after"], 1)
    return text.encode("utf-8")
