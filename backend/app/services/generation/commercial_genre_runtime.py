"""Shared authority for admitted whole-article commercial runs.

Capabilities describe how a genre reads and writes; they never receive an
Evidence admission hook, Provider, mutable candidate or delivery verdict. The
formal route remains plan-free before its single Writer call. Structured/current
PlanningEngine and recovery are separate existing routes, not fallback choices.
"""
from dataclasses import dataclass, field
import json

from app.models.full_article import FullArticleContractError
from app.services.generation.genres.commercial_gate import validate_commercial_contract
from app.services.generation.genres.registry import resolve_genre


# Existing architecture-proof behavior for non-commercial low-level helpers.
# Production admission never uses these defaults to complete an adapter.
_OFFLINE_PLANNING_INSTRUCTION = "在此篇幅和事实边界内按上游规则自主安排繁简、主次和结构，不先输出提纲。"


def reading_group_key(document_type, actions):
    """A genre sees lexical navigation signals only, never Evidence or state.

    Task Assembly retains every admitted ID and creates/validates the brief.
    Group keys do not supply article sections or factual/event equivalence.
    """
    adapter = resolve_genre(document_type)
    original = tuple(actions)
    if not all(type(value) is str for value in original):
        raise FullArticleContractError("COMMERCIAL_READING_SIGNALS_INVALID")
    if adapter.commercial is None:
        return tuple(dict.fromkeys(original))
    contract = validate_commercial_contract(adapter)
    key = contract.evidence_organization.grouping_key(original)
    if (type(key) is not tuple or not all(type(value) is str for value in key)
            or len(set(key)) != len(key) or not set(key) <= set(original)):
        raise FullArticleContractError("COMMERCIAL_READING_KEY_INVALID")
    return key


def planning_instruction(document_type):
    adapter = resolve_genre(document_type)
    if adapter.commercial is None:
        return _OFFLINE_PLANNING_INSTRUCTION
    return validate_commercial_contract(adapter).planning.instruction


@dataclass(frozen=True)
class CommercialGenreRuntime:
    """Per-run invocation budget; construction is restricted to trusted code.

    Registry and contract validation happen before reference/material I/O.
    Diagnostics extend call_accounting.json, not a parallel status authority.
    """
    adapter: object
    contract: object
    _writer_calls: int = field(default=0, init=False)

    def __post_init__(self):
        registered = resolve_genre(self.adapter.genre_id, purpose="production")
        if registered is not self.adapter or self.contract is not registered.commercial:
            raise FullArticleContractError("COMMERCIAL_RUNTIME_ADMISSION_MISMATCH")
        # Direct trusted construction has exactly the same gate and byte locks
        # as admit(); it cannot manufacture an ADMITTED diagnostic or budget.
        from app.services.generation.official_document_drafting import load_official_drafting_rules
        load_official_drafting_rules(self.adapter)

    @classmethod
    def admit(cls, document_type, export_format="docx"):
        adapter = resolve_genre(document_type, purpose="production")
        contract = validate_commercial_contract(adapter)
        if export_format not in contract.delivery.formats:
            raise FullArticleContractError("GENRE_EXPORT_FORMAT_UNSUPPORTED")
        return cls(adapter, contract)

    def validate_packet(self, packet):
        control = self.contract.writer_control
        if control.source_scope_binding != "frozen_registry_scope" or control.body_policy != "immutable_raw":
            raise FullArticleContractError("COMMERCIAL_WRITER_CONTROL_UNSUPPORTED")
        if not packet.brief_json:
            raise FullArticleContractError("FULL_DRAFT_TASK_ASSEMBLY_REQUIRED")
        task = json.loads(packet.task_json)
        if resolve_genre(task.get("document_type")).genre_id != self.adapter.genre_id:
            raise FullArticleContractError("COMMERCIAL_PACKET_GENRE_MISMATCH")
        # Existing frozen registry and validated Brief remain the sole source
        # binding. There is no content-plan/section reassignment operation here.
        packet.bound_brief()
        self.require_no_recovery()

    def claim_writer_call(self):
        policy = self.contract.writer_calls
        if self._writer_calls >= policy.max_calls:
            raise FullArticleContractError("COMMERCIAL_WRITER_BUDGET_EXHAUSTED")
        object.__setattr__(self, "_writer_calls", self._writer_calls + 1)
        return {"retry_limit": policy.retry_limit,
                "preserve_whitespace": policy.preserve_whitespace}

    def require_no_recovery(self):
        policy = self.contract.recovery
        if policy.mode != "disabled" or policy.max_rounds != 0 or policy.writer_calls != 0:
            raise FullArticleContractError("COMMERCIAL_RECOVERY_NOT_SUPPORTED")

    def authorize_recovery(self):
        """No body transition is authorized by the current full-article route.

        A future recovery implementation must be admitted by a revised shared
        runtime with canonical FACT revalidation, not by a genre flag/callback.
        """
        self.require_no_recovery()
        raise FullArticleContractError("COMMERCIAL_RECOVERY_DISABLED")

    def require_delivery_body(self, raw, delivered):
        policy = self.contract.delivery
        if policy.flow4_mode != "shared_full_article" or policy.body_policy != "immutable_raw":
            raise FullArticleContractError("COMMERCIAL_DELIVERY_UNSUPPORTED")
        self.require_no_recovery()
        if raw != delivered:
            raise FullArticleContractError("FULL_DRAFT_BODY_CHANGED_DURING_DELIVERY")

    def diagnostics(self):
        adapter, contract = self.adapter, self.contract
        return {
            "runtime_id": "commercial_full_article_v1",
            "genre_id": adapter.genre_id,
            "capability_version": contract.version,
            "maturity": contract.maturity.value,
            "production_admission": "ADMITTED",
            "retrieval_profile_id": adapter.retrieval_profile.profile_id,
            "planning_capability_id": contract.planning.identity,
            "planning_mode": contract.planning.mode,
            "evidence_organization_id": contract.evidence_organization.identity,
            "drafting_ruleset_id": adapter.drafting_rules.ruleset_id,
            "drafting_ruleset_hash": adapter.drafting_rules.ruleset_hash,
            "structure_contract_id": adapter.structure_contract.contract_id,
            "writer_control_id": contract.writer_control.identity,
            "writer_policy_id": contract.writer_calls.identity,
            "quality_evaluator_id": adapter.quality_contract_id,
            "recovery_policy_id": contract.recovery.identity,
            "recovery_mode": contract.recovery.mode,
            "delivery_policy_id": contract.delivery.identity,
        }
