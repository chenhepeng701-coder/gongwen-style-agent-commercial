"""Read-only genre configuration, never a capability to authorize facts.

Adapters receive frozen article snapshots for quality observations. They cannot
replace retrieval, Atomic admission, canonical FACT checks or delivery policy.
"""
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

from .retrieval import RetrievalProfile


@dataclass(frozen=True)
class DraftingRules:
    ruleset_id: str
    ruleset_hash: str
    root: Path
    prompt_path: str
    integrity_locks: tuple[tuple[str, str], ...]
    profile_label: str
    task_label: str
    reference_policy: Literal["case32", "none"]
    provenance: Literal["pinned_upstream", "host_authored"]


@dataclass(frozen=True)
class StructureContract:
    contract_id: str
    section_roles: tuple[str, ...]
    maturity_review_instruction: str


@dataclass(frozen=True)
class GenreCapabilities:
    status: Literal["VERIFIED_CORE_GENRE", "EXPERIMENTAL"]
    production_ready: bool
    offline_architecture_proof: bool
    export_formats: tuple[str, ...] = ("txt", "docx")

    def __post_init__(self):
        if self.production_ready != (self.status == "VERIFIED_CORE_GENRE"):
            raise ValueError("GENRE_CAPABILITY_STATUS_CONFLICT")
        if not self.export_formats or not set(self.export_formats) <= {"txt", "docx"}:
            raise ValueError("GENRE_EXPORT_FORMAT_UNSUPPORTED")


@dataclass(frozen=True)
class GenreAdapter:
    genre_id: str
    document_type: str
    aliases: tuple[str, ...]
    retrieval_profile: RetrievalProfile
    drafting_rules: DraftingRules
    structure_contract: StructureContract
    quality_contract_id: str
    quality_evaluator: Callable[[Any, Any, Any], Any]
    capabilities: GenreCapabilities
    # Declarative restrictions only. Shared authority is mandatory and is not
    # an adapter field/callback. No UNKNOWN/scope/admission override exists.
    fact_policy_extensions: tuple[Literal["PRESERVE_PLAN_MODALITY"], ...] = ()

    def __post_init__(self):
        if not all((self.genre_id, self.document_type, self.quality_contract_id,
                    self.structure_contract.contract_id, self.structure_contract.section_roles)):
            raise ValueError("GENRE_CONTRACT_INCOMPLETE")
        if not callable(self.quality_evaluator):
            raise ValueError("GENRE_QUALITY_EVALUATOR_REQUIRED")
        if set(self.fact_policy_extensions) - {"PRESERVE_PLAN_MODALITY"}:
            raise ValueError("GENRE_FACT_POLICY_EXTENSION_UNSUPPORTED")
