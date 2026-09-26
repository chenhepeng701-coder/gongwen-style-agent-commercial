"""Commercial full-article capabilities, without evidence or safety authority.

These are trusted application configuration. Request payloads cannot construct
or replace them. The shared runtime owns invocation, validation and delivery;
genre configuration describes only behavior that the current route consumes.
"""
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Literal


class CommercialMaturity(str, Enum):
    REGISTERED = "REGISTERED"
    OFFLINE_PROOF = "OFFLINE_PROOF"
    ENGINEERING_READY = "ENGINEERING_READY"
    PRODUCTION_ENABLED = "PRODUCTION_ENABLED"
    COMMERCIALLY_VERIFIED = "COMMERCIALLY_VERIFIED"


@dataclass(frozen=True)
class PlanningCapability:
    identity: str
    mode: Literal["writer_internal"]
    instruction: str


@dataclass(frozen=True)
class EvidenceOrganizationCapability:
    identity: str
    # Lexical navigation only: no Evidence, source scope or admission objects.
    grouping_key: Callable[[tuple[str, ...]], tuple[str, ...]]


@dataclass(frozen=True)
class WriterControlPolicy:
    identity: str
    source_scope_binding: Literal["frozen_registry_scope"] = "frozen_registry_scope"
    body_policy: Literal["immutable_raw"] = "immutable_raw"


@dataclass(frozen=True)
class WriterCallPolicy:
    identity: str
    max_calls: int = 1
    retry_limit: int = 0
    material_calls: int = 0
    preserve_whitespace: bool = True


@dataclass(frozen=True)
class RecoveryPolicy:
    identity: str
    mode: Literal["disabled"] = "disabled"
    max_rounds: int = 0
    writer_calls: int = 0


@dataclass(frozen=True)
class DeliveryPolicy:
    identity: str
    formats: tuple[str, ...]
    flow4_mode: Literal["shared_full_article"] = "shared_full_article"
    body_policy: Literal["immutable_raw"] = "immutable_raw"


@dataclass(frozen=True)
class CommercialGenreContract:
    genre_id: str
    version: str
    maturity: CommercialMaturity
    planning: PlanningCapability
    evidence_organization: EvidenceOrganizationCapability
    writer_control: WriterControlPolicy
    writer_calls: WriterCallPolicy
    recovery: RecoveryPolicy
    delivery: DeliveryPolicy
