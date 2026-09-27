"""Actual read-only API metadata consumed by the existing frontend runtime.

No fixture copies the 22-genre catalog. FastAPI runs in-process; Node reads only
the supplied JSON and frontend source/dependencies, with no browser or network.
"""
import json
from pathlib import Path
import shutil
import subprocess

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import generation, retrieval


ROOT = Path(__file__).resolve().parents[2]


def test_actual_catalog_and_taxonomy_render_and_route_in_frontend():
    app = FastAPI()
    app.include_router(generation.router, prefix="/api/generation")
    app.include_router(retrieval.router, prefix="/api/retrieval")
    with TestClient(app) as client:
        catalog = client.get("/api/generation/genres")
        taxonomy = client.get("/api/retrieval/scene/taxonomy")
        domains = client.get("/api/generation/business-domains")
    assert catalog.status_code == taxonomy.status_code == domains.status_code == 200
    node = shutil.which("node")
    assert node, "Cross-stack acceptance requires the project's existing Node runtime"
    script = r"""
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import { readGenreCatalog, readSceneTaxonomy, normalizeTaskGenre, readBusinessDomainCatalog, normalizeBusinessDomain, normalizeUiTaskBusinessDomain } from './src/utils/genreCatalog.js';
import { parseBatchExcelRows } from './src/utils/batchExcelImport.js';
import { buildTaskGenerationPayload } from './src/utils/batchTaskParams.js';
const require = createRequire(process.cwd() + '/package.json');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { buildSync } = require('esbuild');
const input = JSON.parse(readFileSync(0, 'utf8'));
const catalog = readGenreCatalog(input.catalog);
const scenes = readSceneTaxonomy(input.taxonomy);
const domains = readBusinessDomainCatalog(input.domains);
assert.equal(catalog.genres.length, 22);
const compiled = buildSync({ entryPoints: ['src/components/WritingCatalogSelects.jsx'],
  bundle: true, write: false, platform: 'node', format: 'cjs', external: ['react'] });
const componentModule = { exports: {} };
new Function('module', 'exports', 'require', compiled.outputFiles[0].text)(componentModule, componentModule.exports, require);
const { GenreSelect, SceneSelect, BusinessDomainSelect } = componentModule.exports;
const genreHtml = renderToStaticMarkup(React.createElement(GenreSelect, { genres: catalog.genres, value: '', onChange() {} }));
assert.equal((genreHtml.match(/已接入，待商业验收/g) || []).length, 21);
assert.equal((genreHtml.match(/已可生产/g) || []).length, 1);
for (const genre of catalog.genres) {
  assert.ok(genreHtml.includes('value="' + genre.document_type + '"'), genre.document_type);
  const task = { title: '合成跨栈验收', document_type: genre.document_type, scene: scenes[0].name, count: 1 };
  if (genre.production_ready) {
    const payload = buildTaskGenerationPayload(task, { export_format: 'txt' }, catalog, domains);
    assert.equal(payload.generation_route, 'full_article_drafting');
    assert.equal(payload.document_type, genre.document_type);
    assert.equal(payload.scene, scenes[0].name);
    assert.throws(() => buildTaskGenerationPayload({ ...task, count: 2 }, {}, catalog));
  } else {
    assert.throws(() => buildTaskGenerationPayload(task, {}, catalog), /待商业验收/);
  }
}
const sceneHtml = renderToStaticMarkup(React.createElement(SceneSelect, { scenes, value: scenes[0].name, onChange() {} }));
for (const scene of scenes) assert.ok(sceneHtml.includes(scene.name));
for (const mapping of catalog.legacy_mappings) {
  const normalized = normalizeTaskGenre({ document_type: mapping.legacy_value }, catalog);
  assert.equal(normalized.document_type, mapping.document_type);
  assert.equal(normalized.scene, mapping.scene);
}
assert.throws(() => buildTaskGenerationPayload({ document_type: catalog.genres[0].document_type }, {}, null));
for (const item of domains) {
  const html = renderToStaticMarkup(React.createElement(BusinessDomainSelect, { items: domains, value: item.value, onChange() {} }));
  assert.ok(html.includes('value="' + item.value + '" selected="">' + item.display_name));
  for (const name of [item.value, ...item.aliases]) {
    assert.equal(normalizeBusinessDomain(name, domains), item.value);
    const history = Object.freeze({ businessDomain: name });
    const restored = normalizeUiTaskBusinessDomain(history, domains);
    assert.equal(restored.businessDomain, item.value);
    assert.equal(history.businessDomain, name);
    const imported = parseBatchExcelRows([['标题', '业务领域'], ['合成跨栈任务', name]],
      { documentType: '工作总结' }, { businessDomain: domains, genreCatalog: catalog });
    assert.equal(imported.errors.length, 0);
    const task = imported.tasks[0];
    const payload = buildTaskGenerationPayload({ title: task.title, document_type: task.documentType,
      business_domain: task.businessDomain }, {}, catalog, domains);
    assert.equal(payload.business_domain, item.value);
  }
}
assert.ok(domains.some(item => item.value === '纪检 / 廉政'));
assert.ok(domains.some(item => item.value === '巡察整改'));
assert.ok(domains.every(item => ![item.value, ...item.aliases].includes('纪检/巡察')));
const mixedHistory = Object.freeze({ business_domain: '纪检/巡察' });
assert.equal(normalizeUiTaskBusinessDomain(mixedHistory, domains).businessDomain, '纪检/巡察');
assert.equal(mixedHistory.business_domain, '纪检/巡察');
const legacyImport = parseBatchExcelRows([['标题', '业务领域'], ['旧混合领域', '纪检/巡察']],
  { documentType: '工作总结' }, { businessDomain: domains, genreCatalog: catalog });
assert.equal(legacyImport.errors.length, 0);
assert.equal(legacyImport.tasks[0].businessDomain, '纪检/巡察');
const legacyPayload = buildTaskGenerationPayload({ title: legacyImport.tasks[0].title,
  document_type: legacyImport.tasks[0].documentType, business_domain: legacyImport.tasks[0].businessDomain }, {}, catalog, domains);
assert.equal(legacyPayload.business_domain, '纪检/巡察');
process.stdout.write(JSON.stringify({ genres: catalog.genres.length, scenes: scenes.length,
  production: catalog.genres.filter(item => item.production_ready).length }));
"""
    result = subprocess.run(
        [node, "--input-type=module", "-e", script],
        cwd=ROOT / "frontend",
        input=json.dumps({"catalog": catalog.json(), "taxonomy": taxonomy.json(), "domains": domains.json()}, ensure_ascii=False),
        text=True, encoding="utf-8", capture_output=True, check=False, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed["genres"] == 22 and observed["production"] == 1
    assert observed["scenes"] == len(taxonomy.json()["items"])
