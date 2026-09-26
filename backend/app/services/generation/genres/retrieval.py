"""Genre-owned reading preferences consumed by the shared retrieval algorithm.

Profiles classify already retrieved source chunks; they never admit Evidence,
change document ranking, or receive an Evidence registry/authority.
"""
from dataclasses import dataclass
from typing import Any, Callable, Mapping


@dataclass(frozen=True)
class RetrievalProfile:
    profile_id: str
    coverage_roles: tuple[str, ...]
    classify_coverage: Callable[[Mapping[str, Any]], str]
    chunk_preference: Callable[[Mapping[str, Any]], float]
    # Only chunk reading is genre-owned. Document source-role classification,
    # compatibility score and Topic-first document ranking remain shared.
    chunk_preference_source_roles: tuple[tuple[str, str], ...] = (("fact_source", "fact_source"),)
    primary_coverage: bool = True
    primary_relevance_seed: int = 6
    primary_min_coverage_chunks: int = 8
    primary_max_coverage_chunks: int = 24
    per_role_limit: int = 2
    auxiliary_chunk_budget: int = 2

    def __post_init__(self):
        if not self.profile_id or not callable(self.classify_coverage) or not callable(self.chunk_preference):
            raise ValueError("Retrieval profiles require an identity and reading policies")
        if len(set(self.coverage_roles)) != len(self.coverage_roles):
            raise ValueError("Retrieval coverage roles must be unique")
        source_roles = {"fact_source", "expression_source", "mixed"}
        if any(len(pair) != 2 or any(role not in source_roles for role in pair)
               for pair in self.chunk_preference_source_roles):
            raise ValueError("Retrieval chunk preferences require recognized source-role pairs")
        if not all(type(value) is int and value > 0 for value in (
            self.primary_relevance_seed, self.primary_min_coverage_chunks,
            self.primary_max_coverage_chunks, self.per_role_limit, self.auxiliary_chunk_budget,
        )):
            raise ValueError("Retrieval coverage budgets must be positive integers")
        if self.primary_min_coverage_chunks > self.primary_max_coverage_chunks:
            raise ValueError("Retrieval minimum coverage exceeds maximum coverage")
