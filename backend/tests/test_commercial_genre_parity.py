"""Commercial infrastructure parity against the exact merged Foundation base.

All materials/articles are synthetic fixtures. BASE owners execute independently;
these comparisons prove orchestration parity, not live prose acceptance.
"""
import ast
import asyncio
import copy
from dataclasses import asdict
from functools import lru_cache
import json
from pathlib import Path
import subprocess
import sys
from types import ModuleType, SimpleNamespace

from docx import Document
import pytest

from app import config
from app.models.generation import ArticleGenRequest
from app.services.generation import full_draft_generation as route
from app.services.generation import material_digest, task_assembly, writing_reference_pack
from app.services.generation.full_article_candidate_adapter import import_full_article, propose_exact_citations
from app.services.generation.flow3_v3_trace import evaluate_full_article_facts
from app.services.generation.full_article_quality import evaluate_full_article_quality
from app.services.generation.official_document_drafting import build_full_draft_prompt
from app.services.llm.openai_compatible_provider import OpenAICompatibleProvider
from app.services.retrieval import document_retriever
from test_material_digest_task_assembly import reference_pack
from test_multi_genre_retrieval import candidates, run as retrieve_fixture, task as retrieval_task
from test_source_substrate import sources


BASE_SHA = "7672e69ac60ea96b5610a59f77cc4c4c58894cfa"
ROOT = Path(__file__).resolve().parents[2]
GENERATION = "backend/app/services/generation/"


@lru_cache(maxsize=None)
def base_source(path):
    return subprocess.check_output(["git", "show", f"{BASE_SHA}:{path}"], cwd=ROOT)


@pytest.fixture
def committed(monkeypatch):
    modules = {}

    def load(name, path=None):
        if name not in modules:
            path = path or GENERATION + name + ".py"
            module = ModuleType("commercial_parity_base_" + name)
            module.__file__ = str(ROOT / path)
            monkeypatch.setitem(sys.modules, module.__name__, module)
            exec(compile(base_source(path), BASE_SHA + "/" + path, "exec"), module.__dict__)
            modules[name] = module
        return modules[name]

    return load


def summary_request(**overrides):
    return ArticleGenRequest(**{
        "title": "2026年安全检查工作总结", "document_type": "工作总结",
        "generation_route": "full_article_drafting", "target_words": 1200,
        "temperature": 0.45, **overrides})


@pytest.fixture
def summary_packet(tmp_path):
    retrieval = sources()
    digest = material_digest.load_material_digest(retrieval, tmp_path / "synthetic-digest").digest
    return task_assembly.assemble_task(summary_request(), retrieval, digest).packet


@pytest.mark.parametrize("count", [0, 1, 6, 8, 12, 24, 40])
def test_commercial_retrieval_complete_result_matches_foundation_base(committed, count):
    baseline = committed("retrieval", "backend/app/services/retrieval/document_retriever.py")
    from app.services.generation.genres.registry import resolve_genre
    selected = resolve_genre("工作总结", purpose="production")
    rows = candidates(count)
    before = retrieve_fixture(baseline, rows, retrieval_task())
    after = retrieve_fixture(document_retriever, rows, retrieval_task(), selected.retrieval_profile)
    assert after == before  # Entire trace, ranking, selection, text, limits and search arguments.


@pytest.mark.parametrize("variant", ["normal", "new_task", "restrictions", "new_source"])
def test_task_planning_and_evidence_organization_match_base(committed, tmp_path, variant):
    retrieval = sources()
    request = summary_request()
    if variant == "new_task":
        request = summary_request(title="2028年宣传培训工作总结", style="简洁", scene="年度汇报")
    elif variant == "restrictions":
        retrieval.update(limitations=["仅供当前合成任务，不得补全结果"],
                         time_risk_overlay={"findings": [], "synthetic_marker": "retain"})
    elif variant == "new_source":
        retrieval["selected_chunks"][0]["text"] += "测试街道正在开展应急演练。"
    snapshot = copy.deepcopy(retrieval)
    digest = material_digest.load_material_digest(retrieval, tmp_path / "synthetic-digest").digest
    before = committed("task_assembly").assemble_task(request, retrieval, digest)
    after = task_assembly.assemble_task(request, retrieval, digest)
    assert after.anchor_input_json == before.anchor_input_json
    assert asdict(after.packet) == asdict(before.packet)
    assert after.packet.brief_json == before.packet.brief_json
    assert after.packet.evidence_writing_view() == before.packet.evidence_writing_view()
    assert after.packet.candidate_input() == before.packet.candidate_input()
    assert retrieval == snapshot
    registry = json.loads(after.packet.registry_json)
    reading = json.loads(after.packet.brief_json)
    assert {eid for row in reading["matters"] for eid in row["evidence_ids"]} == set(registry)
    assert "同组不表示同一事件、因果关系或写作章节" in reading["overview"]


def test_full_prompt_and_locked_rules_remain_exactly_base(committed, summary_packet, reference_pack):
    baseline = committed("official_document_drafting")
    with writing_reference_pack.use_writing_reference_pack(reference_pack):
        assert build_full_draft_prompt(summary_packet) == baseline.build_full_draft_prompt(summary_packet)
    from app.services.generation.genres.registry import resolve_genre
    rules = resolve_genre("工作总结").drafting_rules
    assert rules.ruleset_id == baseline.OFFICIAL_DRAFTING_RULESET_ID
    assert rules.ruleset_hash == baseline.OFFICIAL_DRAFTING_RULESET_HASH
    assert dict(rules.integrity_locks) == baseline._LOCK


@pytest.mark.parametrize("body", [
    "# 合成工作总结\n\n测试街道组织安全检查。\n",
    "# 合成风险稿\n\n测试街道已完成999项重大工程，取得全面成效。\n",
])
def test_complete_quality_report_matches_base(committed, summary_packet, body):
    candidate = import_full_article(body, summary_packet.candidate_input(), {"kind": "OFFLINE_PARITY"})
    citations = propose_exact_citations(candidate)
    facts = evaluate_full_article_facts(candidate, citations)
    actual = evaluate_full_article_quality(candidate, citations, facts)
    assert actual == committed("full_article_quality").evaluate_full_article_quality(candidate, citations, facts)
    assert actual == committed("work_summary_quality_gate").evaluate_full_article_quality(candidate, citations, facts)


@pytest.mark.parametrize("model", ["qwen3.8-flash", "qwen3.8-max", "qwen-synthetic"])
@pytest.mark.parametrize("unsafe", [False, True])
def test_full_route_writer_body_facts_quality_and_delivery_match_base(
        committed, summary_packet, reference_pack, monkeypatch, tmp_path, model, unsafe):
    baseline = committed("full_draft_generation")
    baseline.build_full_draft_prompt = committed("official_document_drafting").build_full_draft_prompt
    baseline.evaluate_full_article_quality = committed("full_article_quality").evaluate_full_article_quality
    body = ("# 合成工作总结\n\n测试街道组织安全检查。\n\n## 问题与后续安排\n\n"
            + ("测试街道已完成999项重大工程，取得全面成效。" if unsafe else "相关情况仍需核实。") + "\n")
    before_packet = asdict(summary_packet)
    results, calls, roots = [], [], []
    for name, owner in (("base", baseline), ("current", route)):
        output = tmp_path / name
        active = OpenAICompatibleProvider(config.ProviderConfig(
            model=model, api_key="synthetic-only", base_url="https://offline.invalid"))
        sent = []

        def writer(prompt, **kwargs):
            sent.append((prompt, {key: value for key, value in kwargs.items()
                                  if key != "on_http_request_attempt"}))
            kwargs["on_http_request_attempt"](1)
            return body

        monkeypatch.setattr(active, "generate_text", writer)
        monkeypatch.setattr(owner, "get_llm_provider", lambda: active)
        monkeypatch.setattr(owner, "get_output_dir", lambda: output)
        monkeypatch.setattr(owner, "prepare_full_draft_material", lambda request: summary_packet)
        monkeypatch.setattr(owner, "load_writing_reference_pack", lambda: reference_pack)
        monkeypatch.setattr(owner, "uuid", SimpleNamespace(uuid4=lambda: SimpleNamespace(hex="a" * 32)))
        monkeypatch.setattr(owner, "time", SimpleNamespace(perf_counter=lambda: 10.0))
        result = asyncio.run(owner.generate_full_draft(summary_request())).articles[0]
        assert len(sent) == 1
        root = output / "full-draft" / ("a" * 32)
        assert not (root / "checks_unavailable.json").exists()
        assert (root / "RAW_WRITER_ARTICLE.md").read_bytes() == body.encode("utf-8")
        assert (root / "FINAL_DELIVERED_ARTICLE.md").read_bytes() == body.encode("utf-8")
        assert result.article_markdown == body and result.delivery_status == "SUCCESS_WITH_REVIEW"
        assert result.docx_path and result.save_path
        assert Path(result.save_path).read_bytes() == body.encode("utf-8")
        values = result.model_dump()
        values.pop("save_path")
        values.pop("docx_path")
        results.append(values)
        calls.append(sent)
        roots.append(root)
    assert calls[0] == calls[1]
    assert results[0] == results[1]
    assert asdict(summary_packet) == before_packet
    for artifact in ("material_packet.json", "TASK_ASSEMBLY.json", "candidate.json", "citations.json",
                     "fact_report.json", "quality_report.json", "article_identity.json"):
        assert (roots[0] / artifact).read_bytes() == (roots[1] / artifact).read_bytes(), artifact
    manifests = [json.loads(next(root.glob("FULL-*/artifact_manifest.json")).read_bytes()) for root in roots]
    for manifest in manifests:
        for artifact in manifest.pop("artifacts"):
            if artifact["path"].endswith(".docx"):
                manifest["docx_paragraphs"] = [(p.text, p.style.name) for p in Document(artifact["path"]).paragraphs]
    assert manifests[0] == manifests[1]
    accounting = [json.loads((root / "call_accounting.json").read_bytes()) for root in roots]
    assert {key: accounting[1][key] for key in accounting[0]} == accounting[0]


@pytest.mark.parametrize("owner", [
    "planning_engine", "work_summary_planner", "work_summary_evidence_group",
    "work_summary_execution_policy", "work_summary_capacity_budget", "work_summary_evidence_chain",
    "new_draft_prompt_v3", "work_summary_quality_recovery", "article_outline", "section_writer",
])
def test_existing_structured_capability_owners_are_unchanged(owner):
    path = GENERATION + owner + ".py"
    assert (ROOT / path).read_bytes().replace(b"\r\n", b"\n") == base_source(path).replace(b"\r\n", b"\n")


def test_existing_frozen_plan_evidence_and_writer_control_behavior_matches_base(committed):
    from app.services.generation import new_draft_prompt_v3, work_summary_evidence_group, work_summary_execution_policy
    from test_m3_section_writer import prepared_case
    prepared = prepared_case(2, 2)
    frozen = prepared["frozen_article_plan"]
    payload = frozen.to_dict()
    kwargs = {key: payload[key] for key in ("document_type", "content_plan", "selection", "fact_anchor_pack",
                                           "work_chain_pack", "capacity_budget", "prohibited_inference")}
    old_frozen = committed("planning_engine").FrozenArticlePlan.create(**kwargs)
    assert old_frozen.to_dict() == frozen.to_dict()
    assert old_frozen.frozen_plan_sha256 == frozen.frozen_plan_sha256
    plan = payload["content_plan"]
    group_args = (plan, prepared["fact_anchor_pack"], prepared["fact_source_support"])
    groups = work_summary_evidence_group.build_work_summary_evidence_groups(*group_args)
    assert groups == committed("work_summary_evidence_group").build_work_summary_evidence_groups(*group_args)
    policy = work_summary_execution_policy.build_work_summary_execution_policy(plan)
    assert policy == committed("work_summary_execution_policy").build_work_summary_execution_policy(plan)
    control_args = dict(content_plan=plan, work_summary_execution_policy=policy,
                        evidence_group_pack=groups, frozen_article_plan=frozen)
    assert new_draft_prompt_v3.build_writer_plan_control_view(**control_args) == committed(
        "new_draft_prompt_v3").build_writer_plan_control_view(**control_args)


def test_formal_full_article_has_no_frozen_planning_or_recovery_regression():
    forbidden = {"PlanningEngine", "FrozenArticlePlan", "build_work_summary_evidence_groups",
                 "build_writer_plan_control_view", "run_work_summary_commercial_quality_loop",
                 "execute_work_summary_quality_repair", "prepare_flow3_v33_structured_generation"}
    for source in (base_source(GENERATION + "full_draft_generation.py"),
                   (ROOT / GENERATION / "full_draft_generation.py").read_bytes()):
        tree = ast.parse(source)
        invoked = {node.func.id for node in ast.walk(tree)
                   if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
        assert not invoked & forbidden
    base_prompt = base_source(GENERATION + "official_document_drafting.py").decode("utf-8")
    assert "不实施第二轮自修" in base_prompt
