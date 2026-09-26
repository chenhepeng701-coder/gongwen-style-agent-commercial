"""Host-authored reporting genre for offline architecture proof only."""
from pathlib import Path

from .contracts import DraftingRules, GenreAdapter, GenreCapabilities, StructureContract
from .report_retrieval import REPORT_RETRIEVAL_PROFILE


def _evaluate_quality(candidate, citations, facts):
    from .report_quality import evaluate_report_offline_quality
    return evaluate_report_offline_quality(candidate, citations, facts)


RULESET_HASH = "f74cc813fd49beea2d89b00c4cd41fd60d776ea2a9abde664c614d73061fd050"
REPORT_ADAPTER = GenreAdapter(
    genre_id="report", document_type="汇报材料", aliases=("report",),
    retrieval_profile=REPORT_RETRIEVAL_PROFILE,
    drafting_rules=DraftingRules(
        ruleset_id="host/report/offline-architecture-proof/v1", ruleset_hash=RULESET_HASH,
        root=Path(__file__).resolve().parent / "rules", prompt_path="report_offline.md",
        integrity_locks=(("report_offline.md", RULESET_HASH),),
        profile_label="offline-proof", task_label="汇报材料（report/offline-proof）",
        reference_policy="none", provenance="host_authored"),
    structure_contract=StructureContract(
        contract_id="report_offline_structure_v1",
        section_roles=("situation", "issues", "recommendations"),
        maturity_review_instruction="离线验证汇报事项、需要说明的问题与意见建议；不适用工作总结成绩和下一步质量合同，未取得商业成熟度验收。"),
    quality_contract_id="report_offline_quality_v1", quality_evaluator=_evaluate_quality,
    capabilities=GenreCapabilities(status="EXPERIMENTAL", production_ready=False,
                                   offline_architecture_proof=True),
    fact_policy_extensions=("PRESERVE_PLAN_MODALITY",),
)
