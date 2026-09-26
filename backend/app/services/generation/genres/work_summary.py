"""First verified full-draft genre; preserve the pinned summary behavior."""
from pathlib import Path

from .contracts import DraftingRules, GenreAdapter, GenreCapabilities, StructureContract
from .work_summary_retrieval import WORK_SUMMARY_RETRIEVAL_PROFILE
from .work_summary_commercial import WORK_SUMMARY_COMMERCIAL_CONTRACT


def _evaluate_quality(candidate, citations, facts):
    from app.services.generation.work_summary_quality_gate import evaluate_full_article_quality
    return evaluate_full_article_quality(candidate, citations, facts)


RULESET_ROOT = Path(__file__).resolve().parents[4] / "experiments/case_a_upstream_drafting_validation_32/upstream"
RULESET_HASH = "80c99367127e6eabca3a5329ec745bcd5bad46734b10aa577a478f4627f858b1"
PROMPT_PATH = "dist/offline/default/doc-types/summary-工作总结/system_prompt.md"

WORK_SUMMARY_ADAPTER = GenreAdapter(
    genre_id="work_summary", document_type="工作总结", aliases=("work_summary",),
    retrieval_profile=WORK_SUMMARY_RETRIEVAL_PROFILE,
    drafting_rules=DraftingRules(
        ruleset_id="zhaohui-yang/official-document-drafting@6b4166a50b96733684b36da6336ea85a847cf3be/offline/default/summary",
        ruleset_hash=RULESET_HASH, root=RULESET_ROOT, prompt_path=PROMPT_PATH,
        integrity_locks=((PROMPT_PATH, RULESET_HASH),
            ("src/adapters/offline/build.py", "8e57d70b0467a18e05379e668a7345b2d2c7c9c7c59a24cfc9e0c3c237fbe72c"),
            ("LICENSE", "2d8ba6968842d2d058a2f1f02f8a5252603e32124d79b684edf2ca119acd6a38")),
        profile_label="default", task_label="工作总结（summary）", reference_policy="case32",
        provenance="pinned_upstream"),
    structure_contract=StructureContract(
        contract_id="work_summary_full_article_structure_v1",
        section_roles=("achievement_status", "major_work", "problem", "next_step", "context"),
        maturity_review_instruction="按真实任务及证据检查全文的结构、工作链、问题和下一步；未分类句子不能伪装 FACT/ANALYSIS。"),
    quality_contract_id="work_summary_quality_contract", quality_evaluator=_evaluate_quality,
    capabilities=GenreCapabilities(status="VERIFIED_CORE_GENRE", production_ready=True,
                                   offline_architecture_proof=True),
    commercial=WORK_SUMMARY_COMMERCIAL_CONTRACT,
)
