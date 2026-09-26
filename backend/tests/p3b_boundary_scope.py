"""Exact P3-B delta scope for historical guards; no historical baseline moves.

These pins constrain this review candidate, not product/quality acceptance.
Every other historical owner and the P1/P2/P3-A protection remain unchanged.
"""
from hashlib import sha256
from pathlib import Path


P3B_CONTENT_SHA256 = {
    "backend/app/services/generation/flow3_v3_trace.py": "108a726256a6da873685a19a17260e1b18b000186689b8af359f04a38efe4939",
    "backend/app/services/generation/full_article_candidate_adapter.py": "7aa79cb19aef8c500c7effd00493487e9a1b5453cc04c86c04f27a159190e34e",
    "backend/app/services/generation/official_document_drafting.py": "d7df2132f1f2a04424bf22851ff0ca90761e9463c404c96497f6e3f1c456c782",
    "backend/app/services/generation/delivery_decision.py": "0e2f7ecae61585306fbe91e74a6b6b96ea1791cdf38ae92bd4a11706fdc7be0a",
    "backend/app/services/flow4/integrity_gate.py": "40323d8e1fee8c0e1f501238b8d5911b0811c0c4de0092e7c97656431e6e8c45",
}


def assert_p3b_content(root: Path, path: str) -> None:
    content = (root / path).read_bytes().replace(b"\r\n", b"\n")
    if sha256(content).hexdigest() == P3B_CONTENT_SHA256[path]:
        return
    # Keep every P3-B pin. Permit only explicitly pinned multi-genre wiring;
    # the canonical Fact authority and candidate adapter remain byte-identical.
    from multi_genre_scope import assert_multi_genre_content
    assert_multi_genre_content(root, path, message=path + " differs from exact P3-B scope content")
