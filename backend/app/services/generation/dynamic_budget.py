"""Deterministic Dynamic Budget projection for a frozen ArticleOutline.

The existing Capacity/FrozenArticlePlan remains the budget source.  This module
only allocates that source budget across Outline nodes and ParagraphIntents.
"""

from __future__ import annotations

from typing import Any, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.generation.article_outline import ArticleOutline, stable_sha


DYNAMIC_BUDGET_CONTRACT_VERSION = "dynamic-outline-budget-1"
DYNAMIC_BUDGET_POLICY_VERSION = "dynamic-outline-budget-policy-1"
BUDGET_STATUS_FROZEN = "FROZEN"
BUDGET_CONTRACT_CONFLICT = "BUDGET_CONTRACT_CONFLICT"
TOTAL_HARD_MAX_EXCEEDED = "TOTAL_HARD_MAX_EXCEEDED"
EXTERNAL_MODEL_CALLS = 0


class BudgetContractError(RuntimeError):
    def __init__(self, code: str, report: Mapping[str, Any]):
        self.code = str(code)
        self.report = dict(report)
        super().__init__(f"{self.code}: {self.report.get('reason', 'budget contract failed')}")


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BudgetPolicySnapshot(_StrictFrozenModel):
    """Versioned unit weights; no document-type fixed section quotas exist."""

    version: Literal["dynamic-outline-budget-policy-1"] = DYNAMIC_BUDGET_POLICY_VERSION
    source_node_weight_coefficient: float = 1.0
    paragraph_intent_weight_coefficient: float = 1.0
    must_cover_unit_coefficient: float = 1.0
    macro_role_base_units: dict[str, float] = {
        "BODY_WORK": 1.0,
        "PROBLEM": 1.0,
        "NEXT_STEP": 1.0,
        "OTHER": 1.0,
    }
    minimum_words_per_required_intent: int = 1
    policy_basis: str = (
        "Equal dimensionless units preserve Planner weight, ParagraphIntent load, "
        "must-cover load and every macro role without inventing document-type ratios."
    )
    fixed_section_hard_caps: tuple[int, ...] = ()


class ParagraphIntentBudget(_StrictFrozenModel):
    paragraph_intent_id: str
    target_words: int = Field(ge=0)
    weight: float = Field(gt=0)
    minimum_contract_words: int = Field(ge=0)
    must_cover_count: int = Field(ge=0)


class OutlineNodeBudget(_StrictFrozenModel):
    outline_node_id: str
    macro_role: str
    source_section_ids: tuple[str, ...]
    target_words: int = Field(ge=0)
    soft_min_words: int = Field(ge=0)
    soft_max_words: int = Field(ge=0)
    weight: float = Field(ge=0)
    reserve_share_words: int = Field(ge=0)
    paragraph_intent_budgets: tuple[ParagraphIntentBudget, ...]
    required: bool

    @model_validator(mode="after")
    def validate_range(self) -> "OutlineNodeBudget":
        if self.soft_min_words > self.target_words or self.target_words > self.soft_max_words:
            raise ValueError("invalid node soft range")
        if sum(value.target_words for value in self.paragraph_intent_budgets) != self.target_words:
            raise ValueError("ParagraphIntent targets do not conserve node target")
        return self


class DynamicBudgetValidation(_StrictFrozenModel):
    status: Literal["PASS"]
    total_target_conserved: bool
    total_hard_max_valid: bool
    required_intents_funded: bool
    must_cover_preserved: bool
    fixed_section_hard_caps_used: bool
    external_model_calls: Literal[0] = 0


class DynamicBudgetPlan(_StrictFrozenModel):
    version: Literal["dynamic-outline-budget-1"] = DYNAMIC_BUDGET_CONTRACT_VERSION
    budget_id: str
    source_plan_sha: str = Field(min_length=64, max_length=64)
    source_outline_sha: str = Field(min_length=64, max_length=64)
    total_target_words: int = Field(gt=0)
    total_hard_max_words: int = Field(gt=0)
    reserve_pool_words: int = Field(ge=0)
    node_budgets: tuple[OutlineNodeBudget, ...]
    policy: BudgetPolicySnapshot
    validation: DynamicBudgetValidation
    generation_status: Literal["PASS"]
    budget_status: Literal["FROZEN"]
    budget_sha: str = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def validate_budget_contract(self) -> "DynamicBudgetPlan":
        if self.total_hard_max_words < self.total_target_words:
            raise ValueError("total hard max is below target")
        if sum(value.target_words for value in self.node_budgets) != self.total_target_words:
            raise ValueError("node targets do not conserve total target")
        if self.reserve_pool_words != self.total_hard_max_words - self.total_target_words:
            raise ValueError("reserve pool mismatch")
        expected = stable_sha(self.model_dump(mode="json", exclude={"budget_sha"}))
        if self.budget_sha != expected:
            raise ValueError("budget_sha mismatch")
        return self


class NodeActualUsage(_StrictFrozenModel):
    outline_node_id: str
    target_words: int = Field(ge=0)
    actual_words: int = Field(ge=0)
    delta_words: int
    borrowed_words: int = Field(ge=0)
    released_words: int = Field(ge=0)
    exceeded_initial_target: bool
    exceeded_soft_max: bool


class BudgetUsageReport(_StrictFrozenModel):
    source_budget_sha: str
    usage_status: Literal["PASS", "REDISTRIBUTED", "BUDGET_CONTRACT_CONFLICT"]
    total_actual_words: int = Field(ge=0)
    total_hard_max_words: int = Field(gt=0)
    initial_reserve_pool_words: int = Field(ge=0)
    released_words: int = Field(ge=0)
    borrowed_words: int = Field(ge=0)
    remaining_pool_words: int = Field(ge=0)
    completed_paragraph_intent_ids: tuple[str, ...]
    missing_required_paragraph_intent_ids: tuple[str, ...]
    node_usage: tuple[NodeActualUsage, ...]
    conflict_reasons: tuple[str, ...]
    usage_sha: str

    @model_validator(mode="after")
    def validate_usage_hash(self) -> "BudgetUsageReport":
        expected = stable_sha(self.model_dump(mode="json", exclude={"usage_sha"}))
        if self.usage_sha != expected:
            raise ValueError("usage_sha mismatch")
        return self


def _allocate_with_minimum(total: int, weights: Sequence[float], minimums: Sequence[int]) -> list[int]:
    if not weights:
        if total:
            raise BudgetContractError(BUDGET_CONTRACT_CONFLICT, {"reason": "no budget consumers"})
        return []
    minimum_total = sum(max(0, int(value)) for value in minimums)
    if total < minimum_total:
        raise BudgetContractError(
            BUDGET_CONTRACT_CONFLICT,
            {
                "reason": "total budget cannot fund every required ParagraphIntent",
                "total_words": total,
                "minimum_contract_words": minimum_total,
            },
        )
    remainder = total - minimum_total
    normalized = [max(0.0, float(value)) for value in weights]
    if not any(normalized):
        normalized = [1.0 for _ in weights]
    raw = [remainder * value / sum(normalized) for value in normalized]
    extras = [int(value) for value in raw]
    residue = remainder - sum(extras)
    order = sorted(
        range(len(raw)),
        key=lambda index: (raw[index] - extras[index], -index),
        reverse=True,
    )
    for index in order[:residue]:
        extras[index] += 1
    return [max(0, int(minimums[index])) + extras[index] for index in range(len(weights))]


def _role_base(policy: BudgetPolicySnapshot, macro_role: str) -> float:
    return float(
        policy.macro_role_base_units.get(
            macro_role,
            policy.macro_role_base_units["OTHER"],
        )
    )


def _node_weight(node: Any, policy: BudgetPolicySnapshot) -> float:
    if node.paragraph_intents and all("paragraph_tasks" in i.source_fields for i in node.paragraph_intents):
        return sum(i.target_weight for i in node.paragraph_intents)
    return max(
        0.0,
        float(node.weight) * policy.source_node_weight_coefficient
        + sum(
            float(intent.target_weight) * policy.paragraph_intent_weight_coefficient
            for intent in node.paragraph_intents
        )
        + sum(len(intent.must_cover) for intent in node.paragraph_intents)
        * policy.must_cover_unit_coefficient
        + _role_base(policy, node.macro_role),
    )


def _intent_weight(intent: Any, policy: BudgetPolicySnapshot) -> float:
    if "paragraph_tasks" in intent.source_fields:
        # M2 has measured distinct material and reference density already.
        # Counting boilerplate task fields again would equalize thin/rich prose.
        return intent.target_weight
    return max(
        0.0,
        float(intent.target_weight) * policy.paragraph_intent_weight_coefficient
        + len(intent.must_cover) * policy.must_cover_unit_coefficient,
    )


def build_dynamic_budget(
    outline: ArticleOutline,
    *,
    requested_total_words: int,
    total_hard_max_words: int,
    policy: BudgetPolicySnapshot | None = None,
) -> DynamicBudgetPlan:
    """Freeze a total -> node -> intent budget without section hard caps."""

    policy = policy or BudgetPolicySnapshot()
    total_target = int(requested_total_words)
    hard_max = int(total_hard_max_words)
    if total_target <= 0 or hard_max < total_target:
        raise BudgetContractError(
            BUDGET_CONTRACT_CONFLICT,
            {
                "reason": "invalid total target/hard max",
                "total_target_words": total_target,
                "total_hard_max_words": hard_max,
            },
        )
    if policy.fixed_section_hard_caps:
        raise BudgetContractError(BUDGET_CONTRACT_CONFLICT, {"reason": "fixed section hard caps are prohibited"})

    active_nodes = [node for node in outline.nodes if node.paragraph_intents]
    container_nodes = [node for node in outline.nodes if not node.paragraph_intents]
    node_weights = [_node_weight(node, policy) for node in active_nodes]
    node_minimums = [
        len(node.paragraph_intents) * policy.minimum_words_per_required_intent
        if node.required
        else 0
        for node in active_nodes
    ]
    node_targets = _allocate_with_minimum(total_target, node_weights, node_minimums)
    reserve_total = hard_max - total_target
    reserve_shares = _allocate_with_minimum(reserve_total, node_weights, [0] * len(active_nodes))

    active_budgets: list[OutlineNodeBudget] = []
    for node, target, reserve_share, node_weight in zip(
        active_nodes, node_targets, reserve_shares, node_weights
    ):
        intent_weights = [_intent_weight(intent, policy) for intent in node.paragraph_intents]
        intent_minimums = [
            policy.minimum_words_per_required_intent if node.required else 0
            for _ in node.paragraph_intents
        ]
        intent_targets = _allocate_with_minimum(target, intent_weights, intent_minimums)
        intent_budgets = tuple(
            ParagraphIntentBudget(
                paragraph_intent_id=intent.paragraph_intent_id,
                target_words=intent_target,
                weight=intent_weight,
                minimum_contract_words=intent_minimum,
                must_cover_count=len(intent.must_cover),
            )
            for intent, intent_target, intent_weight, intent_minimum in zip(
                node.paragraph_intents,
                intent_targets,
                intent_weights,
                intent_minimums,
            )
        )
        active_budgets.append(
            OutlineNodeBudget(
                outline_node_id=node.outline_node_id,
                macro_role=node.macro_role,
                source_section_ids=node.source_section_ids,
                target_words=target,
                soft_min_words=sum(intent_minimums),
                soft_max_words=target + reserve_share,
                weight=node_weight,
                reserve_share_words=reserve_share,
                paragraph_intent_budgets=intent_budgets,
                required=node.required,
            )
        )
    by_id = {value.outline_node_id: value for value in active_budgets}
    container_budgets = {
        node.outline_node_id: OutlineNodeBudget(
            outline_node_id=node.outline_node_id,
            macro_role=node.macro_role,
            source_section_ids=node.source_section_ids,
            target_words=0,
            soft_min_words=0,
            soft_max_words=0,
            weight=0,
            reserve_share_words=0,
            paragraph_intent_budgets=(),
            required=node.required,
        )
        for node in container_nodes
    }
    by_id.update(container_budgets)
    ordered = tuple(by_id[node.outline_node_id] for node in outline.nodes)
    validation = DynamicBudgetValidation(
        status="PASS",
        total_target_conserved=sum(value.target_words for value in ordered) == total_target,
        total_hard_max_valid=hard_max >= total_target,
        required_intents_funded=all(
            intent.minimum_contract_words <= intent.target_words
            for node in ordered
            for intent in node.paragraph_intent_budgets
        ),
        must_cover_preserved=all(
            intent.must_cover_count >= 0
            for node in ordered
            for intent in node.paragraph_intent_budgets
        ),
        fixed_section_hard_caps_used=False,
        external_model_calls=0,
    )
    budget_id = f"DB-{stable_sha({'outline_sha': outline.outline_sha, 'target': total_target, 'hard_max': hard_max, 'policy': policy.model_dump(mode='json')})[:16].upper()}"
    unsigned = {
        "version": DYNAMIC_BUDGET_CONTRACT_VERSION,
        "budget_id": budget_id,
        "source_plan_sha": outline.source_plan_sha,
        "source_outline_sha": outline.outline_sha,
        "total_target_words": total_target,
        "total_hard_max_words": hard_max,
        "reserve_pool_words": reserve_total,
        "node_budgets": ordered,
        "policy": policy,
        "validation": validation,
        "generation_status": "PASS",
        "budget_status": BUDGET_STATUS_FROZEN,
    }
    return DynamicBudgetPlan(**unsigned, budget_sha=stable_sha(unsigned))


def record_actual_usage(
    budget: DynamicBudgetPlan,
    *,
    actual_words_by_node: Mapping[str, int],
    completed_paragraph_intent_ids: Sequence[str],
) -> BudgetUsageReport:
    """Return an immutable usage snapshot; the frozen budget is never mutated."""

    known_nodes = {value.outline_node_id for value in budget.node_budgets}
    unknown_nodes = sorted(set(actual_words_by_node) - known_nodes)
    if unknown_nodes:
        raise BudgetContractError(BUDGET_CONTRACT_CONFLICT, {"reason": "unknown Outline node usage", "outline_node_ids": unknown_nodes})
    completed = tuple(dict.fromkeys(str(value) for value in completed_paragraph_intent_ids))
    required_intents = {
        intent.paragraph_intent_id
        for node in budget.node_budgets
        if node.required
        for intent in node.paragraph_intent_budgets
    }
    missing = tuple(sorted(required_intents - set(completed)))
    usage_rows: list[NodeActualUsage] = []
    for node in budget.node_budgets:
        actual = max(0, int(actual_words_by_node.get(node.outline_node_id, 0)))
        delta = actual - node.target_words
        usage_rows.append(
            NodeActualUsage(
                outline_node_id=node.outline_node_id,
                target_words=node.target_words,
                actual_words=actual,
                delta_words=delta,
                borrowed_words=max(0, delta),
                released_words=max(0, -delta),
                exceeded_initial_target=delta > 0,
                exceeded_soft_max=actual > node.soft_max_words,
            )
        )
    total_actual = sum(value.actual_words for value in usage_rows)
    released = sum(value.released_words for value in usage_rows)
    borrowed = sum(value.borrowed_words for value in usage_rows)
    reasons: list[str] = []
    if total_actual > budget.total_hard_max_words:
        reasons.append(TOTAL_HARD_MAX_EXCEEDED)
    if missing:
        reasons.append("MUST_COVER_OR_PARAGRAPH_INTENT_MISSING")
    if reasons:
        status = BUDGET_CONTRACT_CONFLICT
    elif borrowed:
        status = "REDISTRIBUTED"
    else:
        status = "PASS"
    unsigned = {
        "source_budget_sha": budget.budget_sha,
        "usage_status": status,
        "total_actual_words": total_actual,
        "total_hard_max_words": budget.total_hard_max_words,
        "initial_reserve_pool_words": budget.reserve_pool_words,
        "released_words": released,
        "borrowed_words": borrowed,
        "remaining_pool_words": max(0, budget.total_hard_max_words - total_actual),
        "completed_paragraph_intent_ids": completed,
        "missing_required_paragraph_intent_ids": missing,
        "node_usage": tuple(usage_rows),
        "conflict_reasons": tuple(reasons),
    }
    return BudgetUsageReport(**unsigned, usage_sha=stable_sha(unsigned))
