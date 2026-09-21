"""FLOW4 boundary services.

This package contains deterministic handoff, rendering, and artifact delivery.
It contains no retrieval or model call.
"""

from app.services.flow4.artifact_delivery import (
    ArtifactDeliveryError,
    calculate_render_result_sha256,
    deliver_flow4_artifacts,
    verify_artifact_manifest,
)
from app.services.flow4.renderer import render_flow4_input
from app.services.flow4.handoff import (
    build_flow4_input,
    calculate_core_payload_sha256,
    create_flow3_output_envelope,
    normalize_fact_anchor_pack,
)
from app.services.flow4.integrity_gate import (
    HandoffIntegrityError,
    HandoffIntegrityIssue,
    HandoffIntegrityReport,
    SentenceEvidenceTrace,
    assert_flow4_boundary_isolated,
    determine_flow4_risk_status,
    run_handoff_integrity_gate,
)

__all__ = [
    "ArtifactDeliveryError",
    "HandoffIntegrityError",
    "HandoffIntegrityIssue",
    "HandoffIntegrityReport",
    "SentenceEvidenceTrace",
    "assert_flow4_boundary_isolated",
    "build_flow4_input",
    "calculate_core_payload_sha256",
    "calculate_render_result_sha256",
    "create_flow3_output_envelope",
    "determine_flow4_risk_status",
    "deliver_flow4_artifacts",
    "normalize_fact_anchor_pack",
    "render_flow4_input",
    "run_handoff_integrity_gate",
    "verify_artifact_manifest",
]
