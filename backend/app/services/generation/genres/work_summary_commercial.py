"""Reference commercial capabilities for the existing full-article route.

Planning occurs within its one Writer call. Reading groups are lexical indexes,
not Frozen Plan sections or factual relationships. This route has never run the
legacy structured-draft recovery loop; automatic rewriting stays disabled.
"""
from .commercial_contract import (
    CommercialGenreContract, CommercialMaturity, DeliveryPolicy,
    EvidenceOrganizationCapability, PlanningCapability, RecoveryPolicy,
    WriterCallPolicy, WriterControlPolicy,
)


def lexical_action_grouping(actions: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(actions))


WORK_SUMMARY_COMMERCIAL_CONTRACT = CommercialGenreContract(
    genre_id="work_summary", version="work_summary_commercial_v1",
    # Production admission does not certify prose or calibrated quality scores.
    maturity=CommercialMaturity.PRODUCTION_ENABLED,
    planning=PlanningCapability(
        identity="work_summary_writer_internal_plan_v1", mode="writer_internal",
        instruction="在此篇幅和事实边界内按上游规则自主安排繁简、主次和结构，不先输出提纲。"),
    evidence_organization=EvidenceOrganizationCapability(
        identity="work_summary_lexical_reading_groups_v1", grouping_key=lexical_action_grouping),
    writer_control=WriterControlPolicy(identity="work_summary_full_article_control_v1"),
    writer_calls=WriterCallPolicy(identity="work_summary_single_writer_v1"),
    recovery=RecoveryPolicy(identity="work_summary_full_article_no_rewrite_v1"),
    delivery=DeliveryPolicy(identity="work_summary_full_article_delivery_v1", formats=("txt", "docx")),
)
