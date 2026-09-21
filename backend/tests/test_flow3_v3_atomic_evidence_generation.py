from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.models.generation import ArticleGenRequest
from app.services.generation.atomic_evidence import (
    InvalidEvidencePackError,
    build_atomic_evidence_pack,
    model_visible_evidence,
    validate_atomic_evidence_pack,
)
from app.services.generation.flow3_v3_trace import (
    UnsupportedFactError,
    check_source_paraphrase_risk,
    check_unsupported_facts,
)
from app.services.generation.generator import build_official_draft_task, generate_flow3_v3_raw_draft
from app.services.generation.new_draft_prompt_v3 import build_atomic_new_draft_prompt
from app.services.generation.official_draft_rules import build_official_draft_rule_prompt_block


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LIBRARY_ROOT = PROJECT_ROOT / "backend" / "data" / "library"


def _task(**overrides):
    values = {
        "title": "政法部门介绍大型音乐节安保联动经验",
        "document_type": "经验交流材料",
        "writing_purpose": "经验交流",
        "business_domain": "政法安保",
        "level": "县区",
        "org_type": "党政机关",
        "target_words": 2200,
        "reference_scope": "自动匹配资料库",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _chunk(document_id, chunk_id, text, role="primary", index=0):
    return {
        "document_id": document_id,
        "chunk_id": chunk_id,
        "chunk_index": index,
        "document_role": role,
        "text": text,
        "source_title": "禁止进入证据包的来源文章标题",
        "score": 0.91,
    }


def _retrieval(*, auxiliary=False):
    chunks = [
        _chunk("primary-doc", "primary-doc:c1", "县委政法委牵头成立安保工作专班，建立联动处置机制。", index=0),
        _chunk("primary-doc", "primary-doc:c2", "活动现场设置联合指挥部，开展风险研判。中国长安网", index=1),
        _chunk("primary-doc", "primary-doc:c3", "入口存在人流拥堵风险，实行分区放行。", index=2),
        _chunk("primary-doc", "primary-doc:c4", "县委专题听取安保工作汇报，县分管领导实地踏勘活动场地。", index=8),
        _chunk("primary-doc", "primary-doc:c5", "公安机关开展入口秩序排查，应急管理部门建立风险台账。", index=3),
        _chunk("primary-doc", "primary-doc:c6", "卫生健康部门设置现场救助点，交通运输部门维护周边道路秩序。", index=11),
        _chunk("primary-doc", "primary-doc:c7", "活动区域连接周边道路，现场人员较为集中。", index=5),
        _chunk("primary-doc", "primary-doc:c8", "舞台区域存在人员拥堵风险。", index=13),
        _chunk("primary-doc", "primary-doc:c9", "疏散通道存在通行风险。", index=7),
    ]
    auxiliary_ids = []
    if auxiliary:
        auxiliary_ids = ["aux-doc", "aux-doc-2"]
        chunks.extend([
            _chunk("aux-doc", "aux-doc:c1", "相关单位开展交通疏导。", "auxiliary", 0),
            _chunk("aux-doc-2", "aux-doc-2:c1", "现场存在极端天气风险。", "auxiliary", 0),
        ])
    return {
        "retrieval_status": "SUFFICIENT",
        "query_text": "大型音乐节安保联动",
        "query_fields": {},
        "primary_document_id": "primary-doc",
        "auxiliary_document_ids": auxiliary_ids,
        "ranked_documents": [
            {"document_id": "primary-doc", "source_title": "禁止进入证据包的来源文章标题", "document_score": 0.91},
            {"document_id": "aux-doc", "source_title": "辅助标题", "document_score": 0.81},
            {"document_id": "aux-doc-2", "source_title": "辅助标题二", "document_score": 0.72},
        ],
        "selected_chunks": chunks,
        "relevance_threshold": 0.50,
    }


def _pack_and_report(payload=None):
    payload = payload or _retrieval()
    pack = build_atomic_evidence_pack(_task(), payload)
    return pack, validate_atomic_evidence_pack(pack, payload)


def _checker_pack():
    return {
        "evidence": [{
            "evidence_id": "EV-1", "document_id": "doc", "chunk_id": "doc:c1",
            "document_role": "primary", "evidence_type": "action", "subject": "政法部门",
            "action": "建立", "object": "联动机制", "condition": "", "qualifier": "",
            "result": "", "fact_status": "explicit", "source_span_sha256": "0" * 64,
        }],
        "required_evidence": ["EV-1"], "optional_evidence": [], "prohibited_inference": [],
    }


class _Provider:
    def __init__(self, response):
        self.response = response
        self.calls = 0
        self.config = {"model": "offline-test-model"}

    def generate_text(self, **kwargs):
        self.calls += 1
        return self.response


def test_flow2_primary_auxiliary_identity_scores_and_ranking_are_immutable():
    payload = _retrieval(auxiliary=True)
    before = copy.deepcopy(payload)
    pack = build_atomic_evidence_pack(_task(), payload)
    assert pack["primary_document_id"] == before["primary_document_id"]
    assert pack["auxiliary_document_ids"] == before["auxiliary_document_ids"]
    assert payload == before
    assert [item["document_id"] for item in payload["ranked_documents"]] == ["primary-doc", "aux-doc", "aux-doc-2"]
    assert payload["relevance_threshold"] == 0.50


def test_insufficient_never_calls_model(tmp_path):
    provider = _Provider("不应调用")
    payload = _retrieval()
    payload.update({"retrieval_status": "INSUFFICIENT", "selected_chunks": [], "primary_document_id": None})
    request = ArticleGenRequest(title="无资料主题", article_mode="official_draft")
    with pytest.raises(Exception):
        generate_flow3_v3_raw_draft(request, artifact_root=tmp_path, provider=provider, retrieval_payload=payload)
    assert provider.calls == 0


def test_pack_excludes_full_source_chunk_title_heading_and_forbidden_fields():
    payload = _retrieval()
    pack, report = _pack_and_report(payload)
    visible = json.dumps(model_visible_evidence(pack), ensure_ascii=False)
    assert report["passed"]
    assert "禁止进入证据包的来源文章标题" not in visible
    assert all(chunk["text"] not in visible for chunk in payload["selected_chunks"])
    assert not any(key in visible for key in ("raw_text", "source_paragraph", "source_outline", "source_heading", "complete_chunk", "complete_document", "original_sentence"))


def test_atomic_records_are_single_fact_and_traceable():
    pack, report = _pack_and_report()
    assert report["passed"]
    assert pack["evidence"]
    for item in pack["evidence"]:
        assert item["document_id"] and item["chunk_id"]
        assert not any(mark in item["object"] for mark in "、，；")
        assert item["action"]


def test_real_ideology_report_objects_are_canonicalized_without_copying_source_clause():
    payload = {
        "retrieval_status": "SUFFICIENT",
        "primary_document_id": "real-doc",
        "auxiliary_document_ids": [],
        "ranked_documents": [{
            "document_id": "real-doc",
            "source_title": "县人民政府办公室2026年上半年意识形态工作责任制落实情况报告",
        }],
        "selected_chunks": [_chunk(
            "real-doc",
            "real-doc:c1",
            "上半年开展全县政府系统意识形态工作专项督查。"
            "党组会议专题研究意识形态工作*次。"
            "班子成员带头深入基层联系点开展意识形态工作调研。"
            "每次学习均结合办公室工作实际开展研讨交流。",
        )],
    }
    pack, report = _pack_and_report(payload)
    objects = {item["canonical_object"] for item in pack["evidence"]}

    assert report["passed"]
    assert report["contains_copied_20_han"] is False
    assert "意识形态工作专项督查" in objects
    assert "意识形态工作" in objects
    assert "意识形态工作调研" in objects
    assert "研讨交流" in objects
    assert "全县政府系统意识形态工作专项督查" not in objects
    assert "党组会议专题研究意识形态工作*次" not in objects
    assert all(item["evidence_id"].startswith("EV-") for item in pack["evidence"])


def test_anomaly_guard_blocks_oversized_single_field_source_copy():
    copied_object = "隐患排查整改闭环管理责任落实跟踪问效机制" * 6
    payload = {
        "selected_chunks": [
            _chunk(
                "primary-doc",
                "primary-doc:c1",
                f"\u5404\u5355\u4f4d\u5efa\u7acb{copied_object}\u3002",
            )
        ],
        "ranked_documents": [],
    }
    pack = {
        "evidence": [{
            "evidence_id": "EV-COPIED", "document_id": "primary-doc", "chunk_id": "primary-doc:c1",
            "document_role": "primary", "evidence_type": "action", "subject": "\u5404\u5355\u4f4d",
            "action": "\u5efa\u7acb", "object": copied_object, "condition": "", "qualifier": "",
            "result": "", "fact_status": "explicit", "source_span_sha256": "0" * 64,
        }],
        "required_evidence": ["EV-COPIED"], "optional_evidence": [], "prohibited_inference": [],
    }
    report = validate_atomic_evidence_pack(pack, payload)

    assert not report["passed"]
    assert any(item["rule"] == "oversized_source_copy" and item["field"] == "object" for item in report["hard_issues"])


def test_normal_length_atomic_fact_join_is_not_rejected_as_source_copy():
    payload = {
        "selected_chunks": [
            _chunk(
                "primary-doc",
                "primary-doc:c1",
                "\u4f1a\u8bae\u8981\u6c42\uff0c\u8981\u5c06\u5b89\u5168\u751f\u4ea7\u5de5\u4f5c"
                "\u7eb3\u5165\u7ecf\u6d4e\u793e\u4f1a\u53d1\u5c55\u603b\u4f53\u5e03\u5c40\uff0c\u7edf\u7b79\u63a8\u8fdb\u3002",
            )
        ],
        "ranked_documents": [],
    }
    pack = {
        "evidence": [{
            "evidence_id": "EV-LEGAL", "document_id": "primary-doc", "chunk_id": "primary-doc:c1",
            "document_role": "primary", "evidence_type": "action",
            "subject": "\u8981\u5c06\u5b89\u5168\u751f\u4ea7\u5de5\u4f5c",
            "action": "\u7eb3\u5165",
            "object": "\u7ecf\u6d4e\u793e\u4f1a\u53d1\u5c55\u603b\u4f53\u5e03\u5c40",
            "condition": "", "qualifier": "", "result": "",
            "fact_status": "explicit", "source_span_sha256": "0" * 64,
        }],
        "required_evidence": ["EV-LEGAL"], "optional_evidence": [], "prohibited_inference": [],
    }
    report = validate_atomic_evidence_pack(pack, payload)

    assert report["passed"]
    assert report["contains_copied_20_han"] is False
    assert not report["warnings"]


def test_web_tail_is_cleaned_and_action_is_not_misclassified_as_risk():
    pack, report = _pack_and_report()
    visible = json.dumps(model_visible_evidence(pack), ensure_ascii=False)
    assert "中国长安网" not in visible
    assert report["web_tail_clean"]
    established = [item for item in pack["evidence"] if item["action"] in {"成立", "建立", "设置", "开展", "实行"}]
    assert established and all(item["evidence_type"] != "risk" for item in established)


def test_unrelated_expression_features_are_not_emitted():
    payload = _retrieval()
    payload["selected_chunks"].append(_chunk("primary-doc", "primary-doc:c4", "廉政提醒应当警钟长鸣。", index=3))
    pack = build_atomic_evidence_pack(_task(), payload)
    assert not any(item["evidence_type"] == "expression" for item in pack["evidence"])


def test_auxiliary_is_limited_to_two_and_only_fills_missing_type():
    payload = _retrieval(auxiliary=True)
    payload["auxiliary_document_ids"].append("ignored-third")
    pack = build_atomic_evidence_pack(_task(), payload)
    assert pack["auxiliary_document_ids"] == ["aux-doc", "aux-doc-2"]
    primary_types = {item["evidence_type"] for item in pack["evidence"] if item["document_role"] == "primary"}
    assert all(item["evidence_type"] not in primary_types for item in pack["evidence"] if item["document_role"] == "auxiliary")


def test_prompt_is_new_draft_task_driven_and_contains_no_source_structure_or_long_text():
    payload = _retrieval()
    pack = build_atomic_evidence_pack(_task(), payload)
    prompt = build_atomic_new_draft_prompt(_task(), pack)
    assert "独立新文章" in prompt and "不是改写" in prompt
    assert "用户八项参数" in prompt
    assert "不得因采用FACT约束而退化为明显低于recommended_words的提纲式短稿" in prompt
    assert "不得新增证据未提供" in prompt
    assert "结构只能根据用户标题、文种、写作目的" in prompt
    assert "禁止进入证据包的来源文章标题" not in prompt
    assert not any(chunk["text"] in prompt for chunk in payload["selected_chunks"])
    for banned in ("第一相关文档决定文章结构", "保持原文信息密度", "优先吸收原文表述", "按照资料顺序展开", "尽量保留原文表达", "信息密度不得明显低于来源文章"):
        assert banned not in prompt


@pytest.mark.parametrize("text,claim_type", [
    ("累计完成18项检查。", "quantity_or_date"),
    ("先后召开三次联席会议。", "quantity_or_date"),
    ("现场新增X光机开展检查。", "equipment_or_system"),
    ("各项任务已经圆满完成。", "result_statement"),
])
def test_unsupported_concrete_claim_categories_are_detected(text, claim_type):
    report = check_unsupported_facts(text, _checker_pack(), _task())
    assert not report["passed"]
    assert any(item["claim_type"] == claim_type for item in report["unsupported_claims"])
    assert report["checker_modified_response"] is False


def test_continuous_24_character_copy_is_hard_blocked():
    copied = "活动现场建立统一指挥分工负责协同作战快速处置风险隐患工作机制"
    payload = _retrieval()
    payload["selected_chunks"] = [_chunk("primary-doc", "c1", copied)]
    report = check_source_paraphrase_risk(f"标题\n{copied}", payload)
    assert report["hard_block"]
    assert any(item["rule"] == "continuous_24_han" for item in report["hard_reasons"])


def test_four_heading_isomorphism_is_detected():
    headings = ["统筹部署", "风险排查", "应急处置", "服务保障"]
    payload = _retrieval()
    payload["selected_chunks"] = [
        _chunk("primary-doc", f"c{i}", f"{numeral}、聚力{heading}，构建专项工作体系\n围绕活动安全开展具体工作。", index=i)
        for i, (numeral, heading) in enumerate(zip("一二三四", headings))
    ]
    draft = "\n".join(f"{numeral}、{heading}" for numeral, heading in zip("一二三四", headings))
    report = check_source_paraphrase_risk(draft, payload)
    assert report["hard_block"]
    assert any(item["rule"] == "complete_heading_mapping" for item in report["hard_reasons"])


def test_overall_source_sequence_reproduction_is_detected():
    payload = _retrieval()
    payload["selected_chunks"] = [
        _chunk("primary-doc", "c1", "联合指挥统筹调度各方力量开展活动安保风险研判。", index=0),
        _chunk("primary-doc", "c2", "围绕入口通道排查隐患并组织人员维护现场秩序。", index=1),
        _chunk("primary-doc", "c3", "针对突发情况组织演练完善应急处置工作机制。", index=2),
    ]
    draft = "\n".join(item["text"] for item in payload["selected_chunks"])
    report = check_source_paraphrase_risk(draft, payload)
    assert report["hard_block"]
    assert any(item["rule"] == "source_sequence_overall_reproduction" for item in report["hard_reasons"])


def test_raw_response_is_saved_before_checks_and_checker_does_not_rewrite(tmp_path):
    response = "标题\n累计完成18项检查。"
    provider = _Provider(response)
    request = ArticleGenRequest(
        title=_task().title, article_mode="official_draft", document_type=_task().document_type,
        writing_purpose=_task().writing_purpose, business_domain=_task().business_domain,
        level=_task().level, org_type=_task().org_type, target_words=2200,
    )
    with pytest.raises(UnsupportedFactError):
        generate_flow3_v3_raw_draft(request, artifact_root=tmp_path, provider=provider, retrieval_payload=_retrieval())
    assert provider.calls == 1
    assert (tmp_path / "raw_response.txt").read_text(encoding="utf-8") == response
    assert json.loads((tmp_path / "unsupported_fact_report.json").read_text(encoding="utf-8"))["checker_modified_response"] is False


def test_invalid_evidence_pack_blocks_before_provider_call(tmp_path):
    provider = _Provider("不应调用")
    payload = _retrieval()
    payload["selected_chunks"] = [{"document_id": "primary-doc", "text": "缺少切片身份的事实。"}]
    request = ArticleGenRequest(title=_task().title, article_mode="official_draft")
    with pytest.raises(InvalidEvidencePackError):
        generate_flow3_v3_raw_draft(request, artifact_root=tmp_path, provider=provider, retrieval_payload=payload)
    assert provider.calls == 0
    assert (tmp_path / "evidence_quality_report.json").exists()


def test_success_path_calls_model_once_and_never_enters_flow4(tmp_path):
    provider = _Provider(
        "大型活动安保联动经验\n"
        "县委专题听取安保工作汇报。政法部门牵头成立工作专班。"
        "卫健部门设置现场救助点。"
    )
    request = ArticleGenRequest(
        title=_task().title, article_mode="official_draft", document_type=_task().document_type,
        writing_purpose=_task().writing_purpose, business_domain=_task().business_domain,
        level=_task().level, org_type=_task().org_type, target_words=2200,
    )
    result = generate_flow3_v3_raw_draft(request, artifact_root=tmp_path, provider=provider, retrieval_payload=_retrieval())
    assert result["flow3_v3_status"] == "READY_FOR_CASE1_USER_REVIEW"
    assert provider.calls == result["model_call_count"] == 1
    assert result["entered_postprocessing"] is False and result["flow4_calls"] == 0


def test_no_case_title_document_id_or_q_special_case_in_business_code():
    files = [
        PROJECT_ROOT / "backend/app/services/generation/generator.py",
        PROJECT_ROOT / "backend/app/services/generation/atomic_evidence.py",
        PROJECT_ROOT / "backend/app/services/generation/new_draft_prompt_v3.py",
        PROJECT_ROOT / "backend/app/services/generation/flow3_v3_trace.py",
    ]
    text = "\n".join(path.read_text(encoding="utf-8") for path in files)
    for marker in ("FLOW3-CASE-01", "7bda7787", "政法部门介绍大型音乐节安保联动经验", "Q13", "Q24", "Q33"):
        assert marker not in text


def _library_snapshot(root):
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*")) if path.is_file()
    }


def test_library_fixture_preserves_document_chunk_vector_identity_and_is_read_only(tmp_path, monkeypatch):
    from app.services.library import indexer
    from app.services.retrieval import retriever

    # Controlled inputs exercise the real read algorithms, independently of user data.
    documents = {"fixture-alpha": ["开展检查。", "建立台账。"], "fixture-beta": ["计划开展培训。"]}
    for folder in ("index", "embeddings"):
        (tmp_path / folder).mkdir()
    expected = {}
    for doc_id, texts in documents.items():
        chunks = [{"chunk_index": i, "text": text, "vector": [1.0, float(i + 1)],
                   "metadata": {"document_id": doc_id, "chunk_id": f"{doc_id}:c{i}"}}
                  for i, text in enumerate(texts)]
        expected[doc_id] = {"doc_id": doc_id, "chunks": chunks}
        (tmp_path / "index" / f"{doc_id}.json").write_text(json.dumps({
            "id": doc_id, "title": doc_id, "has_embedding": True,
            "embedding_chunks": len(chunks)}), encoding="utf-8")
        (tmp_path / "embeddings" / f"{doc_id}.json").write_text(json.dumps(expected[doc_id]), encoding="utf-8")
    monkeypatch.setattr(indexer, "get_library_index_dir", lambda: tmp_path / "index")
    monkeypatch.setattr(retriever, "get_library_embeddings_dir", lambda: tmp_path / "embeddings")
    before = _library_snapshot(tmp_path)
    loaded_documents = {doc.id: doc for doc in indexer.list_documents()["items"]}
    loaded_embeddings = retriever._load_all_embeddings()
    assert set(loaded_documents) == set(documents) == set(loaded_embeddings)
    assert loaded_embeddings == expected
    for doc_id, doc in loaded_documents.items():
        assert doc.has_embedding
        assert doc.embedding_chunks == len(documents[doc_id])
        assert [c["text"] for c in loaded_embeddings[doc_id]["chunks"]] == documents[doc_id]
    assert _library_snapshot(tmp_path) == before


def test_official_draft_task_eight_fields_are_preserved():
    request = ArticleGenRequest(article_mode="official_draft", export_format="txt", **vars(_task()))
    task = build_official_draft_task(request)
    assert task.title == _task().title
    assert task.document_type == _task().document_type
    assert task.writing_purpose == _task().writing_purpose
    assert task.business_domain == _task().business_domain
    assert task.level == _task().level
    assert task.org_type == _task().org_type
    assert task.target_words == 2200
    assert task.reference_scope == _task().reference_scope


@pytest.mark.parametrize("document_type", ["党课讲稿", "领导讲话", "工作汇报", "工作总结", "调研报告", "经验交流材料"])
def test_existing_document_type_rule_boundary_remains_available(document_type):
    block = build_official_draft_rule_prompt_block(document_type)
    assert isinstance(block, str) and len(block) > 100
