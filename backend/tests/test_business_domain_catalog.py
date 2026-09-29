"""BUSINESS-DOMAIN-TAXONOMY-01: current BASE, synthetic I/O, real owners.

No test reads user materials or calls an external Provider. Full-route tests
reuse local synthetic ingestion and intercept Writer only at its I/O boundary.
"""
import asyncio
import ast
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys
import shutil
from types import ModuleType

import pytest

from app.models.generation import ArticleGenRequest, GenerationLogEntry
from app.routers import generation
from app.services import business_domain_catalog as domains
from app.services.generation import material_digest, task_assembly, writing_reference_pack
from app.services.generation.generator import build_official_draft_task
from app.services.generation.official_document_drafting import build_full_draft_prompt
from app.services.retrieval import document_retriever
from app.services.retrieval.official_document_classifier import classify_business_domain
from test_genre_ui_scene_integration import client as guarded_client
from test_material_digest_task_assembly import reference_pack
from test_production_task_assembly import ARTICLE, REQUEST, library, snapshots
from test_source_substrate import sources
from test_writing_reference_pack_02b1 import configured, api
import test_commercial_genre_parity as parity


BASE_SHA = "4333864dc24829a5853d783fc749c7ef9d8ada21"
ROOT = Path(__file__).resolve().parents[2]
ROUTER = "backend/app/routers/generation.py"


@pytest.fixture
def committed(monkeypatch):
    def load(name, path=None):
        path = path or "backend/app/services/generation/" + name + ".py"
        module = ModuleType("business_domain_base_" + name)
        module.__file__ = str(ROOT / path)
        monkeypatch.setitem(sys.modules, module.__name__, module)
        source = subprocess.check_output(["git", "show", f"{BASE_SHA}:{path}"], cwd=ROOT)
        exec(compile(source, BASE_SHA + "/" + path, "exec"), module.__dict__)
        return module
    return load


def test_catalog_is_read_only_and_replaces_mixed_ui_choice_with_existing_inspection_domain(guarded_client):
    response = guarded_client.get("/api/generation/business-domains")
    assert response.status_code == 200
    rows = response.json()["items"]
    assert rows == domains.business_domain_catalog()["items"]
    assert len(rows) == len({row["value"] for row in rows}) == 14
    assert "其他" in {row["value"] for row in rows}
    identities = {}
    for row in rows:
        assert set(row) == {"value", "display_name", "aliases"}
        assert row["display_name"].strip()
        for value in (row["value"], *row["aliases"]):
            key = domains._identity(value)
            assert key not in identities
            identities[key] = row["value"]
    # FIX-01 replaces the ambiguous UI choice with the classifier's existing
    # inspection domain; the old mixed name remains compatible outside options.
    before = subprocess.check_output(["git", "show", f"{BASE_SHA}:frontend/src/pages/ArticleGenPage.jsx"], cwd=ROOT).decode("utf-8")
    old = ast.literal_eval(before.split("const BUSINESS_DOMAIN_OPTIONS = ", 1)[1].split("\n]", 1)[0] + "]")
    assert (set(old) - {"纪检/巡察"}) | {"巡察整改"} == {row["value"] for row in rows}
    assert guarded_client.post("/api/generation/business-domains", json={"items": []}).status_code == 405
    rows[0]["aliases"].append("不可污染真源")
    rows.clear()
    assert len(domains.business_domain_catalog()["items"]) == 14
    assert domains.normalize_business_domain("不可污染真源") == "不可污染真源"


def test_catalog_validation_rejects_empty_and_ambiguous_identities():
    item = domains.BusinessDomain("甲", "展示甲", ("旧甲",))
    for items in [(), (domains.BusinessDomain("", "显示"),), (domains.BusinessDomain("甲", " "),),
                  (item, item), (item, domains.BusinessDomain("乙", "乙", ("旧甲",))),
                  (item, domains.BusinessDomain("旧甲", "旧甲")),
                  (domains.BusinessDomain("甲", "甲", (" 甲 ",)),),
                  (domains.BusinessDomain("甲", "甲", ("",)),)]:
        with pytest.raises(ValueError):
            domains._build_lookup(items)


def test_generic_alias_resolver_uses_catalog_and_preserves_unknown_and_empty(monkeypatch):
    for row in domains.BUSINESS_DOMAINS:
        for value in (row.value, *row.aliases):
            assert domains.normalize_business_domain(value) == row.value
            assert domains.normalize_business_domain(" " + value.replace("/", " ／ ") + " ") == row.value
    for value in (None, "", "  ", "历史特殊域", " 未知原值 ", "纪检/巡察", " 纪检 ／ 巡察 "):
        assert domains.normalize_business_domain(value) == value
    monkeypatch.setattr(domains, "_LOOKUP", domains._build_lookup((
        domains.BusinessDomain("合成标准域", "显示", ("合成旧甲", "合成旧乙")),)))
    for value in ("合成标准域", "合成旧甲", "合成旧乙"):
        assert domains.normalize_business_domain(value) == "合成标准域"


def test_canonical_separation_matches_unchanged_classifier_contract():
    values = {item.value for item in domains.BUSINESS_DOMAINS}
    for value in ("纪检 / 廉政", "巡察整改"):
        assert value in values
        assert domains.normalize_business_domain(value) == value
    assert domains.normalize_business_domain("纪检 / 廉政") != domains.normalize_business_domain("巡察整改")
    assert classify_business_domain(title="纪检工作总结") == "纪检 / 廉政"
    assert classify_business_domain(title="巡察工作总结") == "巡察整改"


def test_ambiguous_legacy_is_preserved_and_is_neither_canonical_nor_alias():
    assert domains.normalize_business_domain("纪检/巡察") == "纪检/巡察"
    for item in domains.BUSINESS_DOMAINS:
        assert "纪检/巡察" not in (item.value, *item.aliases)


@pytest.mark.parametrize("value,expected", [
    ("纪检/巡察", "纪检/巡察"), ("纪检 / 廉政", "纪检 / 廉政"), ("巡察整改", "巡察整改"),
    ("纪检 ／ 巡察", "纪检 ／ 巡察"), ("安全生产", "安全生产"),
    ("历史特殊域", "历史特殊域"), (None, ""), ("", ""),
])
def test_formal_http_retrieval_assembly_writer_and_history_share_normalized_domain(
        configured, api, library, tmp_path, committed, value, expected):
    client, route = api
    before = snapshots(tmp_path)
    response = client.post("/api/generation/generate", json={**REQUEST, "business_domain": value})
    assert response.status_code == 200, response.text
    _, calls, sent, assembled = library
    assert calls == {"search": 1, "assembly": 1, "writer": 1}
    assert snapshots(tmp_path) == before
    retrieval, assembly = assembled[0]
    fields = json.loads(assembly.packet.task_json)
    trusted, _ = json.JSONDecoder().raw_decode(sent[0][0].split("原任务记录保持不变：\n", 1)[1])
    assert retrieval["query_fields"]["business_domain"] == fields["business_domain"] == trusted["business_domain"] == expected
    assert trusted == fields
    if expected:
        assert expected in retrieval["query_text"]
    logs = client.get("/api/generation/log").json()["entries"]
    assert logs[0]["business_domain"] == expected
    article = response.json()["articles"][0]
    assert article["generation_route"] == "full_article_drafting"
    assert article["article_markdown"] == ARTICLE
    assert article["delivery_status"] == "SUCCESS_WITH_REVIEW"
    # Compare the full intermediate object against BASE with the SAME input.
    # The existing Atomic owner orders required_evidence via _task_tokens;
    # changing the domain may reorder that diagnostic list, never fact authority.
    assets = route.read_selected_material_assets(retrieval)
    baseline = committed("task_assembly").assemble_task_from_assets(
        ArticleGenRequest(**REQUEST, business_domain=expected or None), retrieval, assets)
    assert assembly.anchor_input_json == baseline.anchor_input_json
    assert asdict(assembly.packet) == asdict(baseline.packet)
    plain = task_assembly.assemble_task_from_assets(
        ArticleGenRequest(**REQUEST), retrieval, assets)
    actual_anchors, plain_anchors = json.loads(assembly.anchor_input_json), json.loads(plain.anchor_input_json)
    actual_pack, plain_pack = actual_anchors.pop("atomic_evidence"), plain_anchors.pop("atomic_evidence")
    assert actual_anchors == plain_anchors
    assert sorted(actual_pack.pop("required_evidence")) == sorted(plain_pack.pop("required_evidence"))
    assert actual_pack == plain_pack
    for name in ("registry_json", "restrictions_json", "unavailable_json"):
        assert getattr(assembly.packet, name) == getattr(plain.packet, name)
    assert assembly.packet.evidence_writing_view() == plain.packet.evidence_writing_view()
    assert assembly.packet.overlay() == plain.packet.overlay()


@pytest.mark.parametrize("value", ["纪检/巡察", "纪检 / 廉政", "巡察整改"])
def test_direct_formal_request_preserves_mixed_or_separate_canonical_domain(configured, library, value):
    request = ArticleGenRequest(**REQUEST, business_domain=value)
    result = asyncio.run(generation.generate_article(request))
    assert request.business_domain == value
    _, calls, sent, assembled = library
    retrieval, assembly = assembled[0]
    trusted, _ = json.JSONDecoder().raw_decode(sent[0][0].split("原任务记录保持不变：\n", 1)[1])
    assert retrieval["query_fields"]["business_domain"] == json.loads(assembly.packet.task_json)["business_domain"] == trusted["business_domain"] == request.business_domain
    assert calls["writer"] == 1 and result.articles[0].article_markdown == ARTICLE


@pytest.mark.parametrize("value,expected", [
    ("纪检/巡察", "纪检/巡察"), ("纪检 / 廉政", "纪检 / 廉政"),
    ("巡察整改", "巡察整改"), ("历史特殊域", "历史特殊域"),
])
def test_saved_history_replays_through_frontend_without_rewriting_old_record(configured, api, library, value, expected):
    client, _ = api
    generation._append_log(GenerationLogEntry(
        id="old-domain", request_title=REQUEST["title"], document_type="工作总结", business_domain=value))
    original = generation._log_path().read_bytes()
    old = client.get("/api/generation/log").json()["entries"][0]
    assert old["business_domain"] == value
    assert generation._log_path().read_bytes() == original
    script = """
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { normalizeUiTaskBusinessDomain } from './src/utils/genreCatalog.js';
import { buildTaskGenerationPayload } from './src/utils/batchTaskParams.js';
const { old, genres, domains, defaults } = JSON.parse(readFileSync(0, 'utf8'));
const snapshot = JSON.stringify(old);
const restored = normalizeUiTaskBusinessDomain(Object.freeze(old), domains);
const payload = buildTaskGenerationPayload({ title: old.request_title, document_type: old.document_type,
  business_domain: restored.businessDomain, count: 1 }, defaults, genres, domains);
assert.equal(JSON.stringify(old), snapshot);
process.stdout.write(JSON.stringify(payload));
"""
    node = shutil.which("node")
    assert node, "Existing frontend runtime is required"
    result = subprocess.run([node, "--input-type=module", "-e", script], cwd=ROOT / "frontend",
        input=json.dumps({"old": old, "genres": client.get("/api/generation/genres").json(),
                          "domains": client.get("/api/generation/business-domains").json()["items"],
                          "defaults": REQUEST}, ensure_ascii=False),
        text=True, encoding="utf-8", capture_output=True, check=True, timeout=30)
    payload = json.loads(result.stdout)
    assert payload["business_domain"] == expected
    assert client.post("/api/generation/generate", json=payload).status_code == 200
    assert next(entry for entry in generation._read_log() if entry["id"] == "old-domain") == json.loads(original)[0]
    assert library[1]["writer"] == 1


@pytest.mark.parametrize("unsafe", [False, True])
def test_safety_production_summary_full_route_matches_required_base(
        committed, reference_pack, monkeypatch, tmp_path, unsafe):
    original = parity.summary_request
    monkeypatch.setattr(parity, "summary_request", lambda **kw: original(**{"business_domain": "安全生产", **kw}))
    request = parity.summary_request()
    retrieval = sources()
    digest = material_digest.load_material_digest(retrieval, tmp_path / "digest").digest
    packet = task_assembly.assemble_task(request, retrieval, digest).packet
    baseline = committed("task_assembly").assemble_task(request, retrieval, digest).packet
    assert asdict(packet) == asdict(baseline)
    old_retrieval = committed("retrieval", "backend/app/services/retrieval/document_retriever.py")
    task = build_official_draft_task(request)
    assert document_retriever.prepare_retrieval_request(task) == old_retrieval.prepare_retrieval_request(task)
    with writing_reference_pack.use_writing_reference_pack(reference_pack):
        assert build_full_draft_prompt(packet) == committed("official_document_drafting").build_full_draft_prompt(baseline)
    # Same prompt, one Writer/no retry, body, Fact Safety, FLOW4 TXT/DOCX,
    # call policy and delivery outputs; exact Required BASE, both risk fixtures.
    parity.test_full_route_writer_body_facts_quality_and_delivery_match_base(
        committed, packet, reference_pack, monkeypatch, tmp_path, "qwen-synthetic", unsafe)


def test_all_preexisting_backend_owners_except_exact_router_seam_are_base_bytes():
    from release_rc_01_package_scope import PACKAGE_SEAMS, assert_package_seam
    from genre_ui_scene_scope import assert_integration_protected_owner
    paths = subprocess.check_output(["git", "ls-tree", "-r", "--name-only", BASE_SHA, "--", "backend/app"], cwd=ROOT).decode().splitlines()
    for path in paths:
        if path in PACKAGE_SEAMS:
            assert_package_seam(ROOT, path)
            continue
        if path == ROUTER:
            continue
        # MULTI-GENRE-PRODUCTION-GATE-01 reseals exactly these two declarations.
        # Keep them protected by fixed byte hashes, not a directory exemption.
        if path in ("backend/app/services/generation/genres/upstream_suite.py",
                    "backend/app/services/generation/genres/upstream_genre_contracts.py"):
            assert_integration_protected_owner(ROOT, path)
            continue
        before = subprocess.check_output(["git", "show", f"{BASE_SHA}:{path}"], cwd=ROOT)
        assert (ROOT / path).read_bytes().replace(b"\r\n", b"\n") == before.replace(b"\r\n", b"\n"), path


def test_scope_reseal_keeps_all_existing_path_lists_and_non_domain_router_ast():
    from genre_ui_scene_scope import _protected_ast
    before = subprocess.check_output(["git", "show", f"{BASE_SHA}:{ROUTER}"], cwd=ROOT)
    current = (ROOT / ROUTER).read_bytes()
    assert _protected_ast(before, ROUTER) == _protected_ast(current, ROUTER)
    # Preserve lists, keys and historical BASE; only exact content hashes may
    # change. The new shared catalog is not an exemption for any older owner.
    path = "backend/tests/genre_ui_scene_scope.py"
    old = ast.parse(subprocess.check_output(["git", "show", f"{BASE_SHA}:{path}"], cwd=ROOT))
    new = ast.parse((ROOT / path).read_bytes())
    def assignments(tree):
        return {node.targets[0].id: node.value for node in tree.body if isinstance(node, ast.Assign)}
    old, new = assignments(old), assignments(new)
    assert old.keys() == new.keys()
    for name, node in old.items():
        if name.endswith("CONTENT_SHA256"):
            assert [ast.dump(key) if key else None for key in node.keys] == [
                ast.dump(key) if key else None for key in new[name].keys]
        else:
            assert ast.dump(node) == ast.dump(new[name]), name
    for fragment, malicious in [
        (b"return business_domain_catalog()", b"return {}"),
        (b"req.business_domain = normalize_business_domain(req.business_domain)", b"req.business_domain = None"),
        (b"_map_delivery_result_to_http(batch)", b"None"),
    ]:
        assert fragment in current
        assert _protected_ast(current.replace(fragment, malicious, 1), ROUTER) != _protected_ast(before, ROUTER)
