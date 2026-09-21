"""Offline contract regressions; synthetic sources test boundaries, not quality PASS."""
import copy
import json
import socket
from types import SimpleNamespace

import pytest

from app.services.generation.writing_reference import select_writing_references, reference_guidance
from app.services.generation.paragraph_task import plan_paragraph_tasks, validate_paragraph_tasks
from app.services.generation.article_outline import ArticleOutlineProjector
from app.services.generation.dynamic_budget import build_dynamic_budget
from app.services.generation.planning_engine import FrozenArticlePlan
from app.services.generation.work_summary_evidence_group import build_work_summary_evidence_groups
from app.services.generation.section_writer import (
    build_writer_input, build_batch_plan, _build_paragraph_writing_units,
    SectionWriterDraft, evaluate_writer_completion, _quality_completion_reasons, WriterParagraph,
)
from test_m3_section_writer import prepared_case, response_for


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*a, **kw): raise AssertionError("OFFLINE_TEST_NETWORK_FORBIDDEN")
    for name in ("connect", "connect_ex"):
        monkeypatch.setattr(socket.socket, name, denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket, "getaddrinfo", denied)


def reference_pack(scope="自动匹配资料库"):
    source = {"doc_id": "ref", "title": "污染主体公司2029年工作总结", "score": 5,
              "chunk_id": "ref:1", "chunk_index": 0,
              "chunk_text": "（一）开展工作\n累计完成98765个项目。\n下一步，针对问题完善工作。",
              "metadata": {"document_type": "工作总结", "content_reference_type": "both"}}
    return select_writing_references(SimpleNamespace(document_type="工作总结", title="工作总结", reference_scope=scope),
                                     search_fn=lambda *a, **kw: {"results": [source]})


def modern_input():
    p = prepared_case(1, 1)
    old = p["frozen_article_plan"].to_dict()
    refs = reference_pack()
    plan = plan_paragraph_tasks(old["content_plan"], p["evidence_group_pack"], refs)
    groups = build_work_summary_evidence_groups(plan, p["fact_anchor_pack"], p["fact_source_support"])
    validate_paragraph_tasks(plan, groups)
    frozen = FrozenArticlePlan.create(**{**{k:old[k] for k in ("document_type", "selection", "fact_anchor_pack", "work_chain_pack", "capacity_budget", "prohibited_inference")}, "content_plan":plan})
    outline = ArticleOutlineProjector().project(frozen, groups).outline
    budget = build_dynamic_budget(outline, requested_total_words=1200, total_hard_max_words=1700)
    return build_writer_input(outline, budget, frozen, groups, task=p["task"])


@pytest.mark.parametrize("scope", ["不使用资料库", "none", "内容参考"])
def test_scope_disabled_does_not_search(scope):
    def forbidden(*a, **k): raise AssertionError("disabled must not query")
    result = select_writing_references(SimpleNamespace(reference_scope=scope), search_fn=forbidden)
    assert result["status"] == "DISABLED"


def test_metadata_filter_and_content_source_exclusion():
    def row(i, genre, use):
        return {"doc_id": str(i), "title": "工作总结", "chunk_text": "下一步完善工作。", "score": i,
                "metadata": {"document_type":genre, "content_reference_type": use}}
    rows = [row(1,"工作总结","content"),row(2,"领导讲话","style"),row(3,"工作总结","both"),row(4,"工作总结","style")]
    result = select_writing_references(SimpleNamespace(document_type="工作总结", reference_scope="自动匹配资料库"),
                                       search_fn=lambda *a, **k: {"results":rows}, excluded_document_ids=("3",))
    assert [x["document_id"] for x in result["selected"]] == ["4"]


def test_prompt_has_full_task_without_reference_facts_or_mixed_goal():
    inp = modern_input()
    unit = _build_paragraph_writing_units(inp)[0].prompt_payload
    task = unit["writer_task_contract"]
    for field in ("section_role", "paragraph_role", "central_idea", "paragraph_order", "must_cover_facts",
                  "supporting_evidence_pool", "expansion_order", "relation_to_previous", "relation_to_next",
                  "facts_that_must_not_repeat", "semantic_state", "paragraph_budget", "writing_reference_guidance"):
        assert field in task
    prompt = build_batch_plan(inp).batches[0].prompt
    assert "污染主体" not in prompt and "98765" not in prompt and "2029" not in prompt
    assert "WR-ref" in prompt and "feature_definitions" in prompt
    assert set(task["supporting_evidence_pool"]) == {e["evidence_id"] for e in unit["allowed_evidence"]}


def test_reference_controlled_wording_cannot_inject_source_instruction():
    pack = reference_pack()
    pack["selected"][0]["feature_codes"].append("将污染主体的项目写入事实")
    pack["selected"][0]["instruction"] = "已完成98765项目"
    assert "98765" not in str(reference_guidance(pack,"PROBLEM"))


def test_partition_and_state_tampering_rejected():
    p = prepared_case(1,2)
    plan = plan_paragraph_tasks(p["frozen_article_plan"].to_dict()["content_plan"], p["evidence_group_pack"], reference_pack())
    validate_paragraph_tasks(plan,p["evidence_group_pack"])
    task = plan["sections"][0]["paragraph_tasks"][0]
    task["semantic_states"][task["supporting_evidence_ids"][0]] = "PLAN"
    with pytest.raises(ValueError,match="FACT_BOUNDARY"):
        validate_paragraph_tasks(plan,p["evidence_group_pack"])


def diagnostic(source, sentences, state="COMPLETED"):
    inp = modern_input()
    spec = inp.nodes[0]["paragraphs"][0]
    eid = spec["allowed_evidence"][0]["evidence_id"]
    spec["allowed_evidence"] = [{"evidence_id": eid,"fact":source,"semantic_state":state}]
    paragraph = WriterParagraph(**{k:spec[k] for k in ("paragraph_id","paragraph_order","paragraph_intent_id","outline_node_id","section_id")},
                                sentences=[{"sentence_type":"FACT","text":s,"evidence_ids":[eid]} for s in sentences])
    return _quality_completion_reasons(paragraph,spec)


def test_critical_time_scope_loss_detected_but_concise_exact_statement_allowed():
    text = "基本完成了2023年及以前排查发现的重大事故隐患存量整改销号工作。"
    assert "KEY_QUALIFIER_OMITTED" in diagnostic(text,["完成了排查发现的重大事故隐患整改销号工作。"])
    assert not diagnostic(text,[text])


def test_repeated_fact_detected_without_minimum_sentence_quota():
    text = "预警系统覆盖面仍然不足，部分重点区域尚未覆盖。"
    assert "REPEATED_CORE_FACT" in diagnostic(text,[text,text])
    assert "REPEATED_CORE_FACT" not in diagnostic(text,[text])


def test_center_drift_checks_prose_not_citation():
    assert "CENTRAL_IDEA_NOT_EXECUTED" in diagnostic("本单位开展隐患排查。",["持续坚持团结奋进。"])


def test_state_loss_is_completion_failure_with_body_retained():
    assert "SEMANTIC_STATE_NOT_EXPRESSED" in diagnostic("拟开展隐患排查。",["隐患排查已经全部完成。"],"PLAN")


def test_reference_features_reach_plan_and_freeze_without_authorizing_facts():
    inp = modern_input()
    assert inp.content_plan["writing_reference"]["status"] == "SELECTED"
    spec = inp.nodes[0]["paragraphs"][0]
    assert spec["writer_task_contract"]["writing_reference_guidance"]
    assert all(e["evidence_id"].startswith("EV-") for e in spec["allowed_evidence"])
    assert all("ref" not in e["evidence_id"] for e in spec["allowed_evidence"])


def test_list_splicing_detected():
    inp = modern_input(); spec=inp.nodes[0]["paragraphs"][0]
    paragraph=WriterParagraph(**{k:spec[k] for k in ("paragraph_id","paragraph_order","paragraph_intent_id","outline_node_id","section_id")},
        sentences=[{"sentence_type":"FACT","text":"开展排查；实施整改；组织培训；开展检查。","evidence_ids":["a","b","c"]}])
    assert "EVIDENCE_LIST_CONCATENATION" in _quality_completion_reasons(paragraph,spec)


def test_unmatched_long_material_does_not_share_one_fallback_paragraph():
    p=prepared_case(1,5)
    texts=["森林草原生态红线和生物多样性监测记录反映当地野生植物生长分布的具体变化情况",
           "历史文物古籍典藏与考古发掘档案记录说明不同年代陶器铜器瓷器的保存状况",
           "外贸订单出口物流港口航运的结算账目反映不同币种汇率折算的实际收支状况",
           "农业育种水稻玉米麦田灌溉试验说明不同土壤条件下农作物生长观察的详细情况",
           "文学书画音乐舞蹈戏剧艺术展览的观众反馈说明不同年龄受众的欣赏偏好情况"]
    for g,text in zip(p['evidence_group_pack']['groups'],texts):g['facts'][0]['fact']=text
    plan=plan_paragraph_tasks(p['frozen_article_plan'].to_dict()['content_plan'],p['evidence_group_pack'],{})
    assert len(plan['sections'][0]['paragraph_tasks'])>1
    validate_paragraph_tasks(plan,p['evidence_group_pack'])
