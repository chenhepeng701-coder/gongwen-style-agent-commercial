"""Offline M3 tests. Synthetic drafts prove contracts, never real prose quality."""
from __future__ import annotations

import ast
import copy
import json
import socket
import time
from pathlib import Path

import pytest

from app.models.generation import ArticleGenRequest
from app.services.flow4.artifact_delivery import deliver_flow4_artifacts, read_artifact_metadata
from app.services.generation.article_outline import ArticleOutlineProjector, stable_sha
from app.services.generation.dynamic_budget import build_dynamic_budget
from app.services.generation.generator import build_official_draft_task, generate_flow3_v33_structured_draft
from app.services.generation.paragraph_compatibility import ParagraphCompatibilityAdapter
from app.services.generation.planning_engine import FrozenArticlePlan
from app.services.generation.section_writer import (
    LEGACY_WRITER, SECTION_WRITER_EXPERIMENTAL, SectionWriter, SectionWriterContractError,
    WriterContextLimits, build_batch_plan, build_writer_input, normalize_writer_text,
    paragraph_identity, select_writer_mode, validate_batch_draft,
)
from app.services.generation.section_writer_integration import generate_section_writer_experimental


@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    def denied(*args, **kwargs):
        pytest.fail("M3 local tests prohibit external calls")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket, "getaddrinfo", denied)


def prepared_case(nodes=1, intents=1, target=1200, state="COMPLETED"):
    """Create synthetic evidence in test scope using the production freeze owner."""
    sections, groups, anchors, support = [], [], [], {}
    for ni in range(nodes):
        sid = f"SEC-{ni+1:02d}"
        chains = []
        for pi in range(intents):
            eid, gid, cid = f"EV-{ni}-{pi}", f"EG-{ni}-{pi}", f"CHAIN-{ni}-{pi}"
            fact = "本单位开展隐患排查。" if state == "COMPLETED" else "拟开展隐患排查。"
            anchor = {"evidence_id": eid, "section_id": sid, "role": "core",
                      "canonical_subject": "本单位", "canonical_action": "开展",
                      "canonical_object": "隐患排查", "exact_values": {}, "forbidden_detail_types": [],
                      "semantic_state": state}
            anchors.append(anchor)
            support[eid] = {"evidence_id": eid, "section_id": sid, "source_span": fact,
                            "source_sentence": fact, "source_available": True,
                            "source_span_binding_valid": True, "low_quality_anchor_fields": []}
            groups.append({"group_id": gid, "section_id": sid, "semantic_state": state,
                           "evidence_ids": [eid], "facts": [{"evidence_id": eid, "fact": fact,
                           "fact_unit_id": gid+"-U1", "semantic_state": state, "exact_values": {}}]})
            chains.append({"chain_id": cid, "section_id": sid, "member_evidence_ids": [eid],
                           "relation_basis": [f"写清排查环节{pi+1}"], "chain_completeness": "MULTI_STEP"})
        sections.append({"section_id": sid, "section_role": "work_progress", "section_goal": "写清重点工作",
                         "section_purpose": "说明重点任务", "business_block": "重点工作",
                         "writing_focus": ["1. 写清排查任务", "goal: 写清排查任务"],
                         "expansion_path": ["交代任务重点"], "content_functions": ["major_work"],
                         "evidence_policy": "fact_required", "relative_weight": 1.0,
                         "work_chain_ids": [c["chain_id"] for c in chains], "work_chain_summaries": chains})
    plan = {"schema_version": "flow3-v3.3-content-plan-1", "plan_id": "PLAN-M3-FIXTURE",
            "planner_owner": "work_summary_planner_v1", "article_purpose": "年度工作总结",
            "allowed_general_analysis": ["允许无新增事实的一般综合"], "global_prohibited_claims": ["不得虚构成果"],
            "sections": sections, "target_total": target}
    pack = {"schema_version": "work-summary-evidence-group-1", "groups": groups, "external_model_calls": 0}
    anchor_pack = {"schema_version": "flow3-v3.3-fact-anchor-1", "fact_anchors": anchors,
                   "analysis_support_pack": [], "source_span_dedup_report": {}}
    frozen = FrozenArticlePlan.create(document_type="工作总结", content_plan=plan, selection={},
                                      fact_anchor_pack=anchor_pack, work_chain_pack={}, capacity_budget={})
    outline = ArticleOutlineProjector().project(frozen, pack).outline
    budget = build_dynamic_budget(outline, requested_total_words=target, total_hard_max_words=target+500)
    return {"article_outline": outline, "dynamic_budget": budget, "frozen_article_plan": frozen,
            "task": build_official_draft_task(ArticleGenRequest(title="年度工作总结", document_type="工作总结")),
            "evidence_group_pack": pack, "fact_anchor_pack": anchor_pack, "fact_source_support": support}


def inputs_for(prepared):
    return build_writer_input(prepared["article_outline"], prepared["dynamic_budget"],
                              prepared["frozen_article_plan"], prepared["evidence_group_pack"],
                              prepared["fact_anchor_pack"], task=prepared.get("task"))


def response_for(prompt, inputs=None):
    payload = json.loads(prompt.split("\n", 1)[1])
    response = {"outline_sha": payload["outline_sha"],
            "title": "年度工作总结" if payload["batch_id"] == "BATCH-01" else None,
            "sections": [{"outline_node_id": n["outline_node_id"], "section_id": n["section_id"],
                          "heading": n["heading_source"],
                          "paragraphs": [{**{k: p[k] for k in ("paragraph_id", "paragraph_order", "paragraph_intent_id", "outline_node_id", "section_id")},
                                          "sentences": [{"sentence_type": "FACT", "text": e["fact"], "evidence_ids": [e["evidence_id"]]}
                                                        for e in p["allowed_evidence"]]}
                                         for p in n["paragraphs"]]} for n in payload["nodes"]]}
    if inputs is not None:
        # Canonical validator/Adapter fixtures keep their global-ID contract.
        # The Provider below deliberately receives no inputs and returns wire
        # aliases, so end-to-end tests must exercise the production decoder.
        specs = {p["paragraph_id"]: p for n in inputs.nodes for p in n["paragraphs"]}
        wire = {p["paragraph_id"]: p for n in payload["nodes"] for p in n["paragraphs"]}
        for section in response["sections"]:
            for p in section["paragraphs"]:
                mapping = {alias["evidence_id"]: original["evidence_id"] for alias, original in
                           zip(wire[p["paragraph_id"]]["allowed_evidence"], specs[p["paragraph_id"]]["allowed_evidence"])}
                for sentence in p["sentences"]:
                    sentence["evidence_ids"] = [mapping[eid] for eid in sentence["evidence_ids"]]
    return response


class FixtureProvider:
    section_writer_local_fixture = True

    def __init__(self, transform=None):
        self.calls = []
        self.transform = transform

    def generate_text(self, *, prompt, system_prompt, temperature, retry_limit, on_http_request_attempt):
        assert retry_limit == 0
        self.calls.append({"prompt": prompt, "temperature": temperature})
        value = response_for(prompt)
        if self.transform:
            self.transform(value)
        return json.dumps(value, ensure_ascii=False)


@pytest.mark.parametrize("nodes,intents", [(1, 1), (1, 2), (2, 2), (6, 1)])
def test_paragraph_identity_and_adapter(nodes, intents):
    prepared = prepared_case(nodes, intents)
    inputs = inputs_for(prepared)
    execution = SectionWriter().write(inputs, provider=FixtureProvider(), local_fixture=True)
    structured, report = ParagraphCompatibilityAdapter().convert(execution.draft, inputs)
    assert len(report["paragraphs"]) == nodes * intents
    for node, section in zip(prepared["article_outline"].nodes, structured["sections"]):
        assert [s["paragraph_id"] for s in section["sentences"]] == [paragraph_identity(i.paragraph_intent_id) for i in node.paragraph_intents]
        assert [s["paragraph_order"] for s in section["sentences"]] == list(range(intents))
    assert report["sentence_text_changed"] is False
    assert report["evidence_identity_changed"] is False


@pytest.mark.parametrize("kind", ["add", "delete", "reorder", "id", "order", "intent", "node", "section",
                                  "illegal_evidence", "cross_paragraph", "empty_evidence", "analysis_evidence",
                                  "extra_field", "blank_text", "newline", "placeholder", "boolean_order",
                                  "duplicate_evidence", "node_reorder", "node_add", "node_delete", "outline_sha"])
def test_rejects_model_contract_mutations(kind):
    inputs = inputs_for(prepared_case(2, 2))
    batch = build_batch_plan(inputs).batches[0]
    response = response_for(batch.prompt, inputs)
    paragraphs = response["sections"][0]["paragraphs"]
    p, q = paragraphs
    sentence = p["sentences"][0]
    if kind == "add": paragraphs.append(copy.deepcopy(p))
    elif kind == "delete": paragraphs.pop()
    elif kind == "reorder": paragraphs.reverse()
    elif kind == "id": p["paragraph_id"] = "MODEL-ID"
    elif kind == "order": p["paragraph_order"] = 10
    elif kind == "intent": p["paragraph_intent_id"] = "MODEL-INTENT"
    elif kind == "node": p["outline_node_id"] = "MODEL-NODE"
    elif kind == "section": p["section_id"] = "MODEL-SECTION"
    elif kind == "illegal_evidence": sentence["evidence_ids"] = ["EV-NOT-FROZEN"]
    elif kind == "cross_paragraph": sentence["evidence_ids"] = q["sentences"][0]["evidence_ids"]
    elif kind == "empty_evidence": sentence["evidence_ids"] = []
    elif kind == "analysis_evidence": sentence["sentence_type"] = "ANALYSIS"
    elif kind == "extra_field": sentence["semantic_state"] = "COMPLETED"
    elif kind == "blank_text": sentence["text"] = " "
    elif kind == "newline": sentence["text"] += "\n新段"
    elif kind == "placeholder": sentence["text"] += "【待补充】"
    elif kind == "boolean_order": p["paragraph_order"] = True
    elif kind == "duplicate_evidence": sentence["evidence_ids"] *= 2
    elif kind == "node_reorder": response["sections"].reverse()
    elif kind == "node_add": response["sections"].append(response["sections"][0])
    elif kind == "node_delete": response["sections"].pop()
    elif kind == "outline_sha": response["outline_sha"] = "0" * 64
    with pytest.raises(SectionWriterContractError):
        validate_batch_draft(json.dumps(response), inputs, batch)


def test_repeated_legal_evidence_and_general_synthesis_allowed():
    inputs = inputs_for(prepared_case())
    batch = build_batch_plan(inputs).batches[0]
    response = response_for(batch.prompt, inputs)
    sentences = response["sections"][0]["paragraphs"][0]["sentences"]
    sentences.append(copy.deepcopy(sentences[0]))
    sentences.append({"sentence_type": "ANALYSIS", "text": "工作推进重在统筹衔接。", "evidence_ids": []})
    draft = validate_batch_draft(json.dumps(response), inputs, batch)
    assert len(draft.sections[0].paragraphs[0].sentences) == 3


@pytest.mark.parametrize("max_paragraphs,expected", [(16, 1), (3, 2), (2, 3)])
def test_deterministic_bounded_batching_context_isolation(max_paragraphs, expected):
    inputs = inputs_for(prepared_case(6))
    limits = WriterContextLimits(max_paragraphs=max_paragraphs)
    plan = build_batch_plan(inputs, limits)
    assert plan == build_batch_plan(inputs, limits)
    assert len(plan.batches) == expected
    all_evidence = {e["evidence_id"] for n in inputs.nodes for p in n["paragraphs"] for e in p["allowed_evidence"]}
    for i, batch in enumerate(plan.batches):
        data = json.loads(batch.prompt.split("\n", 1)[1])
        included = {e["evidence_id"] for n in data["nodes"] for p in n["paragraphs"] for e in p["allowed_evidence"]}
        assert included
        assert all(eid not in batch.prompt for eid in all_evidence-included)
        assert batch.evidence_count == len(included)
        assert batch.prompt_chars > len(batch.prompt)
        assert batch.estimated_prompt_tokens > 0
        assert data["global_context"]["write_article_opening_here"] == (i == 0)
        assert data["global_context"]["write_article_closing_here"] == (i == expected-1)
        assert data["global_context"]["total_target_words"] == 1200
    provider = FixtureProvider()
    result = SectionWriter(limits).write(inputs, provider=provider, local_fixture=True)
    assert len(provider.calls) == expected <= 3
    assert [s.outline_node_id for s in result.draft.sections] == [n["outline_node_id"] for n in inputs.nodes]


def test_no_fourth_batch_or_six_section_calls():
    inputs = inputs_for(prepared_case(6))
    provider = FixtureProvider()
    with pytest.raises(SectionWriterContractError, match="WRITER_CONTEXT_EXCEEDS_THREE_BATCHES"):
        SectionWriter(WriterContextLimits(max_paragraphs=1)).write(inputs, provider=provider, local_fixture=True)
    assert provider.calls == []


@pytest.mark.parametrize("limits", [WriterContextLimits(max_prompt_chars=500), WriterContextLimits(max_context_tokens=500), WriterContextLimits(max_output_tokens=10)])
def test_prompt_and_output_capacity_preflight_fails_without_call(limits):
    provider = FixtureProvider()
    with pytest.raises(SectionWriterContractError):
        SectionWriter(limits).write(inputs_for(prepared_case()), provider=provider, local_fixture=True)
    assert not provider.calls


def test_budget_prompt_and_source_trace_immutable():
    prepared = prepared_case(2, 2)
    before = stable_sha(prepared["frozen_article_plan"].to_dict())
    inputs = inputs_for(prepared)
    for node, budget in zip(inputs.nodes, prepared["dynamic_budget"].node_budgets):
        for paragraph, ib in zip(node["paragraphs"], budget.paragraph_intent_budgets):
            assert paragraph["budget"]["target_words"] == ib.target_words
            assert len(paragraph["must_cover"]) > 0
    trace = json.loads(inputs.normalization_trace_json)
    assert len(trace) == 4
    assert all(t["source_fields"] and t["must_cover_mapping"] for t in trace)
    assert stable_sha(prepared["frozen_article_plan"].to_dict()) == before
    assert normalize_writer_text("1. 写清任务；goal: 写清任务；focus: 落实安排") == "写清任务；落实安排"
    assert normalize_writer_text("1.5亿元") == "1.5亿元"


@pytest.mark.parametrize("nodes,intents", [(1, 1), (1, 2), (2, 2)], ids=["CASE_1", "CASE_2", "MULTI_SECTION"])
def test_flow4_v3_real_owner_roundtrip_and_readback(tmp_path, nodes, intents):
    prepared = prepared_case(nodes, intents)
    result = generate_section_writer_experimental(prepared=prepared, provider=FixtureProvider(),
                                                  artifact_root=tmp_path/"run", local_fixture=True)
    render = result["flow4_result"]
    assert result["flow4_input"].render_options.paragraph_mode == "paragraph_aware"
    assert result["flow4_input"].contract_version.endswith("/v3")
    assert [len(s.paragraphs) for s in render.sections] == [intents] * nodes
    assert result["paragraph_compatibility_report"]["flow4_round_trip"] == "ROUND_TRIP_PRESERVED"
    assert result["dynamic_budget_usage"].total_actual_words == len(render.final_text)
    assert result["performance"]["quality_rewrite_calls"] == 0
    assert result["performance"]["structured_model_repair_calls"] == 0
    assert result["performance"]["independent_title_calls"] == 0
    assert result["performance"]["performance_architecture_verdict"] == "NOT_REAL_VALIDATED"
    artifact = deliver_flow4_artifacts(render, tmp_path/"artifacts", formats=("txt", "docx"))
    for item in artifact.artifacts:
        metadata = read_artifact_metadata(artifact.manifest_path, artifact_id=item.artifact_id)
        assert [(p.section_id, p.paragraph_id, p.paragraph_order) for p in metadata.paragraphs] == [
            (s.section_id, p.paragraph_id, p.paragraph_order) for s in render.sections for p in s.paragraphs]


def test_actual_usage_redistributes_without_extra_writer(tmp_path):
    prepared = prepared_case(2, target=200)
    def enlarge(value):
        value["sections"][0]["paragraphs"][0]["sentences"].append(
            {"sentence_type": "ANALYSIS", "text": "工作重在统筹。"*20, "evidence_ids": []})
    provider = FixtureProvider(enlarge)
    result = generate_section_writer_experimental(prepared=prepared, provider=provider, artifact_root=tmp_path, local_fixture=True)
    assert result["dynamic_budget_usage"].usage_status == "REDISTRIBUTED"
    assert len(provider.calls) == 1
    usage = json.loads(Path(result["paths"]["budget_usage"]).read_text(encoding="utf-8"))
    assert len(usage["paragraph_usage"]) == 2
    assert usage["borrowed_words"] > 0


def test_total_hard_max_failure_persists_intact_article(tmp_path):
    prepared = prepared_case(target=100)
    def enlarge(value):
        value["sections"][0]["paragraphs"][0]["sentences"].append(
            {"sentence_type": "ANALYSIS", "text": "工作重在统筹。"*100, "evidence_ids": []})
    provider = FixtureProvider(enlarge)
    result = generate_section_writer_experimental(
        prepared=prepared, provider=provider, artifact_root=tmp_path,
        local_fixture=True,
    )
    assert result["issue_route"] == "QUALITY_REPAIR_REQUIRED"
    assert result["rendered_draft"].strip()
    assert len(provider.calls) == 1
    assert "工作重在统筹。"*100 in (tmp_path/"readable_article.txt").read_text(encoding="utf-8")


@pytest.mark.parametrize("state", ["PLAN", "PENDING"])
def test_semantic_state_is_preserved_not_a_model_editable_field(tmp_path, state):
    prepared = prepared_case(state=state)
    inputs = inputs_for(prepared)
    assert inputs.nodes[0]["paragraphs"][0]["allowed_evidence"][0]["semantic_state"] == state
    def unsupported(value):
        value["sections"][0]["paragraphs"][0]["sentences"][0]["text"] = "本单位已完成隐患排查。"
    result = generate_section_writer_experimental(prepared=prepared, provider=FixtureProvider(unsupported), artifact_root=tmp_path, local_fixture=True)
    assert prepared["fact_anchor_pack"]["fact_anchors"][0]["semantic_state"] == state
    assert result["rendered_draft"]
    # Existing Safety may repair or retain review; cannot silently endorse a
    # completed-state sentence as safe when the frozen source is a plan.
    assert "已完成" not in result["rendered_draft"] or not result["validation_reports"]["fact"]["passed"]


def test_feature_off_defaults_legacy_and_experimental_is_exclusive(tmp_path, monkeypatch):
    from app.services.generation import generator
    assert select_writer_mode() == LEGACY_WRITER
    assert select_writer_mode(SECTION_WRITER_EXPERIMENTAL) == SECTION_WRITER_EXPERIMENTAL
    with pytest.raises(SectionWriterContractError): select_writer_mode("UNKNOWN")
    def forbidden(*args, **kwargs):
        raise AssertionError("Legacy/preflight/repair must not run in experimental route")
    for name in ("prepare_flow3_v33_structured_generation", "get_llm_provider", "execute_one_shot_section_expansion",
                 "execute_one_shot_section_rewrite", "run_structured_execution_repair", "run_work_summary_quality_repair_loop"):
        if hasattr(generator, name): monkeypatch.setattr(generator, name, forbidden)
    prepared, provider = prepared_case(), FixtureProvider()
    request = ArticleGenRequest(title="工作总结", scene="work_summary", target_words=1200)
    result = generate_flow3_v33_structured_draft(request, artifact_root=tmp_path, prepared=prepared, provider=provider,
                                               writer_mode=SECTION_WRITER_EXPERIMENTAL, section_writer_local_fixture=True)
    assert result["writer_mode"] == SECTION_WRITER_EXPERIMENTAL
    assert len(provider.calls) == 1
    # Omitted/off switch reaches the original Legacy preflight, not the new path.
    with pytest.raises(AssertionError, match="Legacy/preflight"):
        generate_flow3_v33_structured_draft(request, artifact_root=tmp_path)


def test_external_authorization_and_no_automatic_second_writer():
    inputs, provider = inputs_for(prepared_case()), FixtureProvider()
    with pytest.raises(SectionWriterContractError, match="AUTHORIZATION_REQUIRED"):
        SectionWriter().write(inputs, provider=provider)
    assert not provider.calls
    provider = FixtureProvider(lambda v: v["sections"][0]["paragraphs"].clear())
    with pytest.raises(SectionWriterContractError):
        SectionWriter().write(inputs, provider=provider, local_fixture=True)
    assert len(provider.calls) == 1
    with pytest.raises(SectionWriterContractError, match="LOCAL_FIXTURE_PROVIDER_REQUIRED"):
        SectionWriter().write(inputs, provider=object(), local_fixture=True)


def test_provider_retry_guard_and_explicit_temperature():
    class Retrying(FixtureProvider):
        def generate_text(self, **kwargs):
            kwargs["on_http_request_attempt"](1)
            kwargs["on_http_request_attempt"](2)
    with pytest.raises(SectionWriterContractError, match="PROVIDER_RETRY_FORBIDDEN"):
        SectionWriter().write(inputs_for(prepared_case()), provider=Retrying(), local_fixture=True)
    provider = FixtureProvider()
    SectionWriter().write(inputs_for(prepared_case()), provider=provider, local_fixture=True, temperature=0.0)
    assert provider.calls[0]["temperature"] == 0.0


def test_syntax_only_fence_recovery_not_identity_repair():
    inputs = inputs_for(prepared_case())
    batch = build_batch_plan(inputs).batches[0]
    raw = "```json\n" + json.dumps(response_for(batch.prompt, inputs)) + "\n```"
    assert validate_batch_draft(raw, inputs, batch).sections


@pytest.mark.parametrize("placeholder", ["XX次", "TODO：补证据", "【待补充：请提供材料】", "××"])
def test_literal_placeholders_rejected_with_chinese_suffix(placeholder):
    inputs = inputs_for(prepared_case())
    batch = build_batch_plan(inputs).batches[0]
    value = response_for(batch.prompt, inputs)
    value["sections"][0]["paragraphs"][0]["sentences"][0]["text"] += placeholder
    with pytest.raises(SectionWriterContractError, match="NO_LITERAL_PLACEHOLDER"):
        validate_batch_draft(json.dumps(value), inputs, batch)


def test_failed_output_retains_call_metrics_without_retry(tmp_path):
    provider = FixtureProvider(lambda v: v["sections"][0]["paragraphs"].clear())
    with pytest.raises(SectionWriterContractError):
        generate_section_writer_experimental(prepared=prepared_case(), provider=provider,
                                              artifact_root=tmp_path, local_fixture=True, save_raw_responses=True)
    metrics = json.loads((tmp_path/"performance_report.json").read_text(encoding="utf-8"))
    assert metrics["status"] == "WRITER_FAILED" and metrics["batch_count"] == 1
    assert metrics["calls"][0]["latency_ms"] >= 0
    assert (tmp_path/"section_writer_raw_response_BATCH-01.txt").is_file()
    assert len(provider.calls) == 1


def test_wrong_source_budget_and_evidence_are_rejected():
    prepared = prepared_case()
    budget = build_dynamic_budget(prepared["article_outline"], requested_total_words=1000, total_hard_max_words=1700)
    with pytest.raises(SectionWriterContractError, match="BUDGET_TOTAL_SOURCE_MISMATCH"):
        build_writer_input(prepared["article_outline"], budget, prepared["frozen_article_plan"], prepared["evidence_group_pack"])
    pack = copy.deepcopy(prepared["evidence_group_pack"])
    pack["groups"][0]["facts"][0]["fact"] = "改变的内容"
    with pytest.raises(SectionWriterContractError, match="OUTLINE_SOURCE_MISMATCH"):
        build_writer_input(prepared["article_outline"], prepared["dynamic_budget"], prepared["frozen_article_plan"], pack)


def test_production_owner_no_experiment_or_second_quality_owner():
    root = Path(__file__).resolve().parents[1]/"app"
    found = []
    for path in root.rglob("*.py"):
        if "generation_core_v2" in path.parts: continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "SectionWriter": found.append(path)
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith(("experiments", "backend.experiments"))
    assert found == [root/"services/generation/section_writer.py"]
    for name in ("section_writer.py", "paragraph_compatibility.py", "section_writer_integration.py"):
        source = (root/"services/generation"/name).read_text(encoding="utf-8")
        tree = ast.parse(source)
        calls = [n.func.id if isinstance(n.func, ast.Name) else n.func.attr if isinstance(n.func, ast.Attribute) else ""
                 for n in ast.walk(tree) if isinstance(n, ast.Call)]
        assert not set(calls) & {"run_work_summary_quality_repair_loop", "execute_one_shot_section_expansion",
                                "execute_one_shot_section_rewrite", "search_detailed", "run_structured_execution_repair"}
