"""Fail-closed admission for the capabilities consumed by the shared runtime.

Descriptor validation does not load or execute a genre's rules or callbacks.
The existing drafting-rules owner retains byte-lock verification. Neither this
gate nor a maturity label can grant evidence authority or article qualification.
"""
from pathlib import Path, PurePosixPath
from inspect import signature
import re

from .commercial_contract import (
    CommercialGenreContract, CommercialMaturity, DeliveryPolicy,
    EvidenceOrganizationCapability, PlanningCapability, RecoveryPolicy,
    WriterCallPolicy, WriterControlPolicy,
)
from .contracts import DraftingRules, GenreAdapter, GenreCapabilities, StructureContract
from .retrieval import RetrievalProfile


class CommercialAdmissionError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/@-]{0,199}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def _require(condition, code):
    if not condition:
        raise CommercialAdmissionError(code)


def _identity(value):
    return type(value) is str and _IDENTITY.fullmatch(value) is not None


def _nonempty(value):
    return type(value) is str and bool(value.strip()) and value == value.strip()


def _roles(value):
    return (type(value) is tuple and bool(value)
            and all(_identity(role) for role in value) and len(set(value)) == len(value))


def _accepts_positional(callback, count):
    """Validate the actual invocation shape without executing genre code."""
    if not callable(callback):
        return False
    try:
        signature(callback).bind(*(None for _ in range(count)))
    except (TypeError, ValueError):
        return False
    return True


def _validate_existing_capabilities(adapter):
    """Revalidate existing owners, including objects altered after construction."""
    profile = adapter.retrieval_profile
    _require(isinstance(profile, RetrievalProfile), "COMMERCIAL_RETRIEVAL_CAPABILITY_REQUIRED")
    _require(_identity(profile.profile_id) and _roles(profile.coverage_roles)
             and _accepts_positional(profile.classify_coverage, 1)
             and _accepts_positional(profile.chunk_preference, 1),
             "COMMERCIAL_RETRIEVAL_CAPABILITY_INVALID")
    try:
        profile.__post_init__()
    except (TypeError, ValueError):
        raise CommercialAdmissionError("COMMERCIAL_RETRIEVAL_CAPABILITY_INVALID") from None
    _require(type(profile.primary_coverage) is bool,
             "COMMERCIAL_RETRIEVAL_CAPABILITY_INVALID")

    structure = adapter.structure_contract
    _require(isinstance(structure, StructureContract)
             and _identity(structure.contract_id) and _roles(structure.section_roles)
             and _nonempty(structure.maturity_review_instruction),
             "COMMERCIAL_STRUCTURE_CAPABILITY_INVALID")
    _require(_identity(adapter.quality_contract_id) and _accepts_positional(adapter.quality_evaluator, 3),
             "COMMERCIAL_QUALITY_CAPABILITY_INVALID")

    rules = adapter.drafting_rules
    _require(isinstance(rules, DraftingRules), "COMMERCIAL_DRAFTING_CAPABILITY_REQUIRED")
    _require(_identity(rules.ruleset_id) and type(rules.ruleset_hash) is str
             and _SHA256.fullmatch(rules.ruleset_hash) is not None
             and isinstance(rules.root, Path) and rules.root.is_absolute()
             and _nonempty(rules.prompt_path) and _nonempty(rules.profile_label)
             and _nonempty(rules.task_label)
             and type(rules.reference_policy) is str and rules.reference_policy in {"case32", "none"}
             and type(rules.provenance) is str and rules.provenance in {"pinned_upstream", "host_authored"},
             "COMMERCIAL_DRAFTING_CAPABILITY_INVALID")
    locks = rules.integrity_locks
    _require(type(locks) is tuple and bool(locks), "COMMERCIAL_DRAFTING_LOCKS_INVALID")
    checked = {}
    for lock in locks:
        _require(type(lock) is tuple and len(lock) == 2, "COMMERCIAL_DRAFTING_LOCKS_INVALID")
        name, digest = lock
        _require(_nonempty(name) and "\\" not in name and ":" not in name
                 and not PurePosixPath(name).is_absolute() and ".." not in PurePosixPath(name).parts
                 and type(digest) is str and _SHA256.fullmatch(digest) is not None
                 and name not in checked, "COMMERCIAL_DRAFTING_LOCKS_INVALID")
        checked[name] = digest
    _require(checked.get(rules.prompt_path) == rules.ruleset_hash,
             "COMMERCIAL_DRAFTING_LOCKS_INCONSISTENT")

    capabilities = adapter.capabilities
    _require(isinstance(capabilities, GenreCapabilities), "COMMERCIAL_DECLARATION_INVALID")
    _require(type(capabilities.production_ready) is bool
             and type(capabilities.offline_architecture_proof) is bool
             and type(capabilities.status) is str
             and capabilities.status in {"VERIFIED_CORE_GENRE", "EXPERIMENTAL"}
             and type(capabilities.export_formats) is tuple
             and bool(capabilities.export_formats)
             and all(type(value) is str and value in {"txt", "docx"}
                     for value in capabilities.export_formats)
             and len(set(capabilities.export_formats)) == len(capabilities.export_formats),
             "COMMERCIAL_DECLARATION_INVALID")
    try:
        capabilities.__post_init__()
    except (TypeError, ValueError):
        raise CommercialAdmissionError("COMMERCIAL_DECLARATION_INCONSISTENT") from None
    _require(type(adapter.fact_policy_extensions) is tuple
             and all(value == "PRESERVE_PLAN_MODALITY" for value in adapter.fact_policy_extensions),
             "COMMERCIAL_FACT_POLICY_INVALID")


def validate_commercial_contract(adapter) -> CommercialGenreContract:
    """Validate a complete contract for trusted offline or production use.

No maturity stage grants production admission through this function. Offline
contract tests can compose capabilities without manufacturing a production flag.
"""
    _require(isinstance(adapter, GenreAdapter), "COMMERCIAL_ADAPTER_REQUIRED")
    contract = adapter.commercial
    _require(isinstance(contract, CommercialGenreContract), "COMMERCIAL_CONTRACT_REQUIRED")
    _require(_identity(adapter.genre_id) and _identity(contract.genre_id) and contract.genre_id == adapter.genre_id
             and _identity(contract.version), "COMMERCIAL_IDENTITY_INVALID")
    _require(isinstance(contract.maturity, CommercialMaturity), "COMMERCIAL_MATURITY_INVALID")
    for name, expected in (
        ("planning", PlanningCapability),
        ("evidence_organization", EvidenceOrganizationCapability),
        ("writer_control", WriterControlPolicy), ("writer_calls", WriterCallPolicy),
        ("recovery", RecoveryPolicy), ("delivery", DeliveryPolicy),
    ):
        capability = getattr(contract, name)
        _require(isinstance(capability, expected), "COMMERCIAL_CAPABILITY_REQUIRED")
        _require(_identity(capability.identity), "COMMERCIAL_CAPABILITY_IDENTITY_INVALID")
    _require(contract.planning.mode == "writer_internal" and _nonempty(contract.planning.instruction),
             "COMMERCIAL_PLANNING_UNSUPPORTED")
    _require(_accepts_positional(contract.evidence_organization.grouping_key, 1),
             "COMMERCIAL_EVIDENCE_ORGANIZATION_INVALID")
    control = contract.writer_control
    _require(control.source_scope_binding == "frozen_registry_scope" and control.body_policy == "immutable_raw",
             "COMMERCIAL_WRITER_CONTROL_UNSUPPORTED")
    calls = contract.writer_calls
    _require(all(type(value) is int for value in (calls.max_calls, calls.retry_limit, calls.material_calls))
             and (calls.max_calls, calls.retry_limit, calls.material_calls) == (1, 0, 0)
             and calls.preserve_whitespace is True, "COMMERCIAL_WRITER_CALL_POLICY_UNSUPPORTED")
    recovery = contract.recovery
    _require(recovery.mode == "disabled" and type(recovery.max_rounds) is int
             and type(recovery.writer_calls) is int and recovery.max_rounds == recovery.writer_calls == 0,
             "COMMERCIAL_RECOVERY_UNSUPPORTED")
    delivery = contract.delivery
    _require(delivery.flow4_mode == "shared_full_article" and delivery.body_policy == "immutable_raw"
             and type(delivery.formats) is tuple and bool(delivery.formats),
             "COMMERCIAL_DELIVERY_UNSUPPORTED")
    _validate_existing_capabilities(adapter)
    _require(delivery.formats == adapter.capabilities.export_formats,
             "COMMERCIAL_DELIVERY_FORMATS_INCONSISTENT")
    return contract


def require_commercial_production(adapter) -> CommercialGenreContract:
    """A production-like flag alone never satisfies commercial admission."""
    contract = validate_commercial_contract(adapter)
    _require(adapter.capabilities.production_ready is True,
             "COMMERCIAL_PRODUCTION_NOT_ENABLED")
    _require(contract.maturity in {
        CommercialMaturity.PRODUCTION_ENABLED, CommercialMaturity.COMMERCIALLY_VERIFIED,
    }, "COMMERCIAL_MATURITY_NOT_ADMITTED")
    return contract
