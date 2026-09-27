import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { transformWithEsbuild } from 'vite'
import { catalog, readyGenre } from '../fixtures/genreCatalog.mjs'

const read = name => readFileSync(new URL(`../../src/${name}`, import.meta.url), 'utf8')
const page = read('pages/ArticleGenPage.jsx')
const batch = read('pages/BatchTaskPage.jsx')
const api = read('api.js')
const component = read('components/WritingCatalogSelects.jsx')
const compiled = await transformWithEsbuild(component, 'WritingCatalogSelects.jsx', { format: 'cjs', jsx: 'transform' })
const module = { exports: {} }
new Function('require', 'module', 'exports', compiled.code)(createRequire(import.meta.url), module, module.exports)
const { GenreSelect, SceneSelect } = module.exports

test('actual React genre select renders supplied catalog status and no extra genres', () => {
  const html = renderToStaticMarkup(React.createElement(GenreSelect, { genres: catalog.genres, value: '工作总结', onChange() {} }))
  for (const genre of catalog.genres) assert.match(html, new RegExp(`value="${genre.document_type}"`))
  assert.equal((html.match(/<option/g) || []).length, catalog.genres.length + 1)
  assert.match(html, /工作总结 · 已可生产/)
  assert.match(html, /value="讲话稿" disabled="">讲话稿 · 已接入，待商业验收/)
  assert.doesNotMatch(html, /ENGINEERING_READY|CommercialGenreContract/)
})

test('future production metadata enables real rendered option without document-specific branch', () => {
  const html = renderToStaticMarkup(React.createElement(GenreSelect, { genres: [readyGenre('future', '未来文种')], value: '未来文种', onChange() {} }))
  assert.match(html, /value="未来文种" selected="">未来文种 · 已可生产/)
  const empty = renderToStaticMarkup(React.createElement(GenreSelect, { genres: [], value: '', disabled: true }))
  assert.match(empty, /<select disabled="">/)
  assert.doesNotMatch(empty, /工作总结/)
})

test('unknown historic genre remains visible as a disabled option', () => {
  const html = renderToStaticMarkup(React.createElement(GenreSelect, { genres: catalog.genres, value: '调研报告', onChange() {} }))
  assert.match(html, /value="调研报告" disabled="" selected="">调研报告（历史值，请重新选择）/)
})

test('actual Scene select renders live taxonomy, auto choice and existing historical values', () => {
  const html = renderToStaticMarkup(React.createElement(SceneSelect, { scenes: [{ name: '后端场景' }], value: '后端场景', onChange() {} }))
  assert.match(html, /value="">自动 \/ 未指定/)
  assert.match(html, /value="后端场景" selected="">后端场景/)
  assert.equal((html.match(/<option/g) || []).length, 2)
  const historical = renderToStaticMarkup(React.createElement(SceneSelect, { scenes: [], value: '旧场景', onChange() {} }))
  assert.match(historical, /旧场景（历史场景）/)
})

test('single and batch pages consume backend catalog and row-level scene', () => {
  assert.match(api, /api\.get\('\/generation\/genres'\)/)
  assert.match(api, /api\.get\('\/retrieval\/scene\/taxonomy'\)/)
  assert.match(page, /loadWritingCatalogs\(getGenreCatalog, getSceneTaxonomy\)/)
  assert.doesNotMatch(page, /DOCUMENT_TYPE_OPTIONS|WRITING_SCENE_OPTIONS|DOCUMENT_TYPE_PARTY_LECTURE/)
  assert.doesNotMatch(read('utils/batchTaskParams.js'), /=== '工作总结'|: 'current'/)
  assert.match(page, /document_type: row\.documentType,\s+scene: row\.scene,/)
  assert.match(page, /value=\{singleTask\.scene\}/)
  assert.match(batch, /value=\{row\.scene\}/)
  assert.match(batch, /value=\{defaults\.scene\}/)
  assert.match(page, /disabled=\{!catalogReady \|\| !!singleTaskError\}/)
  assert.match(batch, /taskValidationErrors\.some\(Boolean\)/)
  assert.doesNotMatch(page, /classifyScene\(|classifyDocumentScene\(/)
})

test('restore and initial defaults cannot erase explicit row scenes or reclassify history', () => {
  assert.match(page, /hasOwnProperty\.call\(overrides, 'scene'\)/)
  assert.match(page, /normalizeRestoredTaskGenre\(/)
  assert.match(page, /setScene\(normalizedDefaults\.scene\)/)
  assert.match(page, /if \(taskContextTouchedRef\.current\) return/)
  assert.match(read('App.jsx'), /scene: entry\.scene \|\| ''/)
  assert.match(read('App.jsx'), /isHistoryReplay: true/)
  assert.match(page, /row\.requiresGenreSelection \? '' : defaultDocumentType/)
  assert.match(page, /articleGenDraft\.isHistoryReplay \? '' : defaultDocumentType/)
})

test('single form keeps requested two-column conceptual order with no extra fact controls', () => {
  const ids = ['article-document-type', 'article-scene', 'article-level', 'article-org-type', 'article-business-domain', 'article-writing-purpose', 'article-target-words', 'article-reference-scope', 'article-extra-requirements']
  const positions = ids.map(id => page.indexOf(`htmlFor="${id}"`))
  assert.ok(positions.every((position, index) => position >= 0 && (!index || position > positions[index - 1])))
  assert.doesNotMatch(page, /补充事实|supplemental_fact|user_fact/)
})
