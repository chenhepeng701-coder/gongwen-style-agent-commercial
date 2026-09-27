"""SCENE-CONTRACT-01: public HTTP -> real owners -> synthetic Writer -> history.

Only local synthetic library fixtures and in-memory Provider I/O are used.
These are contract/BASE parity checks, not real prose quality acceptance.
"""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from app.models.generation import ArticleGenRequest, GenerationLogEntry
from app.routers import generation
from app.services.generation import task_assembly
from app.services.generation.generator import build_official_draft_task
from app.services.retrieval import document_retriever, scene_classifier
from test_genre_ui_scene_integration import client as guarded_client, committed
from test_production_task_assembly import ARTICLE, REQUEST, library
from test_writing_reference_pack_02b1 import api, configured


ROOT = Path(__file__).resolve().parents[2]
AUTO_SCENES = [None, "", "auto", "自动", "未指定", "自动/未指定", "自动识别场景",
               "自动 / 未指定", " AUTO ", "  "]


def assert_scene_chain(client, library, response, expected):
    assert response.status_code == 200, response.text
    _, calls, sent, assembled = library
    assert calls == {"search": 1, "assembly": 1, "writer": 1}
    retrieval, assembly = assembled[0]
    assert retrieval["query_fields"].get("scene") == expected
    fields = json.loads(assembly.packet.task_json)
    assert fields["scene"] == fields["scene_compat"] == expected
    assert fields["document_type"] == "工作总结"
    assert fields["document_type_from_scene_compat"] is False
    # Decode the actual trusted task record received at the Provider boundary.
    prompt = sent[0][0]
    trusted, _ = json.JSONDecoder().raw_decode(prompt.split("原任务记录保持不变：\n", 1)[1])
    assert trusted == fields
    article = response.json()["articles"][0]
    logs = client.get("/api/generation/log").json()["entries"]
    new_entry = next(entry for entry in logs if entry["id"] != "legacy-scene")
    assert article["scene"] == new_entry["scene"] == new_entry["articles"][0]["scene"] == (expected or "")
    assert article["article_markdown"] == ARTICLE
    assert new_entry["document_type"] == "工作总结"
    return retrieval, assembly, sent[0]


@pytest.mark.parametrize("scene", AUTO_SCENES)
def test_auto_scene_is_empty_through_public_production_chain(
        configured, api, library, committed, scene):
    client, route = api
    response = client.post("/api/generation/generate", json={**REQUEST, "scene": scene})
    retrieval, assembly, sent = assert_scene_chain(client, library, response, None)
    assert "scene" not in retrieval["query_fields"]
    # Exact required BASE uses an absent Scene; normalized HTTP inputs must
    # produce the same query, Task Assembly and entire Writer prompt/body.
    request = ArticleGenRequest(**REQUEST)
    base_retriever = committed("backend/app/services/retrieval/document_retriever.py")
    prepared = base_retriever.prepare_retrieval_request(build_official_draft_task(request))
    assert retrieval["query_text"] == prepared["query_text"]
    assert retrieval["query_fields"] == prepared["query_fields"]
    plain = task_assembly.assemble_task_from_assets(
        request, retrieval, route.read_selected_material_assets(retrieval)).packet
    assert assembly.packet == plain
    from app.services.generation.writing_reference_pack import load_writing_reference_pack, use_writing_reference_pack
    base_prompt = committed("backend/app/services/generation/official_document_drafting.py")
    with use_writing_reference_pack(load_writing_reference_pack()):
        expected = base_prompt.build_full_draft_prompt(plain)
    assert sent[0] == expected["user"]
    assert sent[1]["system_prompt"] == expected["system"]


def test_explicit_scene_reaches_writer_without_changing_evidence_authority(configured, api, library):
    client, route = api
    scene = "民主生活会对照检查"
    response = client.post("/api/generation/generate", json={**REQUEST, "scene": scene})
    retrieval, assembly, _ = assert_scene_chain(client, library, response, scene)
    assert scene in retrieval["query_text"].split("辅助限定：")[1]
    assert retrieval["query_fields"]["scene"] == scene
    plain = task_assembly.assemble_task_from_assets(
        ArticleGenRequest(**REQUEST), retrieval, route.read_selected_material_assets(retrieval))
    assert assembly.packet.registry_json == plain.packet.registry_json
    assert assembly.packet.evidence_writing_view() == plain.packet.evidence_writing_view()
    assert assembly.packet.restrictions_json == plain.packet.restrictions_json
    assert assembly.packet.unavailable_json == plain.packet.unavailable_json
    assert assembly.packet.overlay() == plain.packet.overlay()
    assert json.loads(assembly.anchor_input_json) == json.loads(plain.anchor_input_json)


@pytest.mark.parametrize("scene", ["随便编一个场景", "工作总结", "work_summary"])
@pytest.mark.parametrize("route", ["full_article_drafting", "current", None])
def test_invalid_new_request_fails_before_generator_or_writer(guarded_client, monkeypatch, scene, route):
    def no_log(*args, **kwargs):
        pytest.fail("Invalid Scene must not create a generation history record")
    monkeypatch.setattr(generation, "_append_log", no_log)
    payload = {**REQUEST, "scene": scene}
    if route is None:
        payload.pop("generation_route")
    else:
        payload["generation_route"] = route
    response = guarded_client.post("/api/generation/generate", json=payload)
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["error_code"] == "INVALID_SCENE"
    assert detail["field"] == "scene"
    assert detail["success"] is False


def test_legacy_history_reads_and_restores_empty_before_new_generation(configured, api, library):
    client, _ = api
    generation._append_log(GenerationLogEntry(
        id="legacy-scene", request_title=REQUEST["title"], document_type="工作总结", scene="工作总结"))
    original = generation._log_path().read_bytes()
    old = client.get("/api/generation/log").json()["entries"][0]
    assert old["scene"] == "工作总结"
    assert generation._log_path().read_bytes() == original
    # Execute the existing UI recovery owner with actual server metadata; do
    # not grant new requests a legacy bypass or rewrite persisted old records.
    node = shutil.which("node")
    assert node, "Existing frontend runtime is required for history restoration parity"
    script = """
import { readFileSync } from 'node:fs';
import { normalizeRestoredTaskGenre } from './src/utils/genreCatalog.js';
import { buildTaskGenerationPayload } from './src/utils/batchTaskParams.js';
const { old, catalog, scenes, defaults } = JSON.parse(readFileSync(0, 'utf8'));
const restored = normalizeRestoredTaskGenre({ documentType: old.document_type, scene: old.scene }, catalog, scenes);
process.stdout.write(JSON.stringify(buildTaskGenerationPayload({ title: old.request_title,
  document_type: restored.documentType, scene: restored.scene, count: 1 }, defaults, catalog)));
"""
    result = subprocess.run([node, "--input-type=module", "-e", script], cwd=ROOT / "frontend",
        input=json.dumps({"old": old, "catalog": client.get("/api/generation/genres").json(),
                          "scenes": scene_classifier.get_scene_taxonomy(), "defaults": REQUEST}, ensure_ascii=False),
        text=True, encoding="utf-8", capture_output=True, check=True, timeout=30)
    restored = json.loads(result.stdout)
    assert restored["scene"] is None
    assert restored["generation_route"] == "full_article_drafting"
    response = client.post("/api/generation/generate", json={**REQUEST, **restored})
    assert_scene_chain(client, library, response, None)
    assert next(e for e in generation._read_log() if e["id"] == "legacy-scene") == json.loads(original)[0]


def test_scene_validation_reads_the_existing_taxonomy_owner(monkeypatch):
    for item in scene_classifier.get_scene_taxonomy():
        assert scene_classifier.normalize_generation_scene(item["name"]) == item["name"]
    monkeypatch.setattr(scene_classifier, "SCENE_TAXONOMY", [{"name": "合成测试场景", "keywords": []}])
    assert scene_classifier.normalize_generation_scene("合成测试场景") == "合成测试场景"
    with pytest.raises(ValueError):
        scene_classifier.normalize_generation_scene("民主生活会对照检查")
