"""Exact P2 approval and explicit historical-oracle delta; no baseline moves."""
from hashlib import sha256
from pathlib import Path


ATOMIC_EVIDENCE_PATH = "backend/app/services/generation/atomic_evidence.py"
# P2-AUXILIARY-EVIDENCE-ADMISSION only. Normalize CRLF, nothing else.
P2_ATOMIC_EVIDENCE_SHA256 = "16efd41b4688f987f1c6be391f7ccc1e2bf79386f06d0143cc242a9d7a6865ee"


def assert_p2_atomic_evidence(root: Path) -> None:
    content = (root / ATOMIC_EVIDENCE_PATH).read_bytes().replace(b"\r\n", b"\n")
    assert sha256(content).hexdigest() == P2_ATOMIC_EVIDENCE_SHA256, (
        ATOMIC_EVIDENCE_PATH + " differs from the exact P2-approved content"
    )


def historical_atomic_with_p2_admission(source: bytes) -> bytes:
    """Apply only the approved 16-line deletion to a frozen historical oracle.

    Cache/assembly parity tests still execute their original Git baseline, with
    this explicit contract correction. Every other byte and full output-field
    comparison stays intact. Missing/repeated blocks fail, never silently skip.
    The P2 regression separately compares against the *unmodified* BASE_SHA.
    """
    text = source.decode("utf-8").replace("\r\n", "\n")
    removed = (
        '    primary_types: set[str] = set()\n',
        '                if role == "auxiliary" and atom["evidence_type"] in primary_types:\n'
        '                    discarded.append({\n'
        '                        "document_id": document_id, "chunk_id": chunk_id,\n'
        '                        "source_span_sha256": atom["source_span_sha256"],\n'
        '                        "reason": "auxiliary_does_not_fill_primary_type_gap",\n'
        '                    })\n'
        '                    continue\n',
        '                if role == "primary":\n'
        '                    primary_types.add(atom["evidence_type"])\n',
        '    roles_by_type: dict[str, set[str]] = {}\n'
        '    for item in evidence:\n'
        '        roles_by_type.setdefault(item["evidence_type"], set()).add(item["document_role"])\n'
        '    for evidence_type, roles in roles_by_type.items():\n'
        '        if roles == {"primary", "auxiliary"}:\n'
        '            hard_issues.append({"rule": "auxiliary_overlap", "detail": evidence_type})\n',
    )
    for block in removed:
        assert text.count(block) == 1, "Historical Atomic owner does not match the exact P2 delta"
        text = text.replace(block, "", 1)
    return text.encode("utf-8")
