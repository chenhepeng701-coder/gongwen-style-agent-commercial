import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { transformWithEsbuild } from 'vite'
import { build } from 'esbuild'
import { catalog, readyGenre, businessDomains } from '../fixtures/genreCatalog.mjs'

const read = name => readFileSync(new URL(`../../src/${name}`, import.meta.url), 'utf8')
const page = read('pages/ArticleGenPage.jsx')
const batch = read('pages/BatchTaskPage.jsx')
const api = read('api.js')
const component = read('components/WritingCatalogSelects.jsx')
const compiled = await transformWithEsbuild(component, 'WritingCatalogSelects.jsx', { format: 'cjs', jsx: 'transform' })
const module = { exports: {} }
new Function('require', 'module', 'exports', compiled.code)(createRequire(import.meta.url), module, module.exports)
const { GenreSelect, SceneSelect, BusinessDomainSelect } = module.exports

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
  assert.match(page, /loadWritingCatalogs\(getGenreCatalog, getSceneTaxonomy, getBusinessDomainCatalog\)/)
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

test('domain select renders canonical values and display labels, never offers legacy aliases', () => {
  const html = renderToStaticMarkup(React.createElement(BusinessDomainSelect, { items: businessDomains, value: '纪检 / 廉政', onChange() {} }))
  assert.match(html, /value="纪检 \/ 廉政" selected="">纪检 \/ 廉政/)
  assert.match(html, /value="巡察整改">巡察整改/)
  assert.match(html, /value="合成标准域">合成展示名/)
  assert.doesNotMatch(html, /纪检\/巡察|合成旧域甲/)
  const historical = renderToStaticMarkup(React.createElement(BusinessDomainSelect, { items: businessDomains, value: '未知历史领域', onChange() {} }))
  assert.match(historical, /value="未知历史领域" disabled="" selected="">未知历史领域（历史值，已保留）/)
  const mixed = renderToStaticMarkup(React.createElement(BusinessDomainSelect, { items: businessDomains, value: '纪检/巡察', onChange() {} }))
  assert.match(mixed, /value="纪检\/巡察" disabled="" selected="">纪检\/巡察（历史值，已保留）/)
  const unavailable = renderToStaticMarkup(React.createElement(BusinessDomainSelect, { value: '安全生产' }))
  assert.match(unavailable, /<select disabled="">/)
  assert.match(unavailable, /业务领域目录加载中或不可用/)
  assert.doesNotMatch(unavailable, /安全生产|纪检/)
})

test('single/batch/defaults/Excel/history all wire domain metadata through shared catalog readiness', () => {
  assert.match(api, /api\.get\('\/generation\/business-domains'\)/)
  assert.doesNotMatch(page, /BUSINESS_DOMAIN_OPTIONS|纪检\/巡察|纪检 \/ 廉政/)
  assert.match(page, /businessDomainOptions\.length > 0 && !catalogLoading/)
  assert.match(page, /setBusinessDomainOptions\(\[\]\)/)
  assert.match(page, /业务领域目录加载失败/)
  assert.match(page, /<BusinessDomainSelect\s+id="article-business-domain"\s+items=\{businessDomainOptions\}/)
  assert.equal((batch.match(/<BusinessDomainSelect /g) || []).length, 2)
  assert.match(page, /businessDomain: businessDomainOptions/g)
  assert.match(page, /genreCatalog,\s+businessDomainOptions,/)
  assert.match(page, /normalizeUiTaskBusinessDomain\(normalizeRestoredTaskGenre/)
  assert.match(page, /setBusinessDomain\(draftDefaults\.businessDomain\)/)
  assert.match(page, /setBusinessDomain\(restoredDomain\)/)
})

// Execute the real page's hooks and event handlers with in-memory API I/O.
// This exercises delayed catalogs/history/batch wiring without a browser/network.
const pageBundle = await build({
  entryPoints: [fileURLToPath(new URL('../../src/pages/ArticleGenPage.jsx', import.meta.url))],
  bundle: true, write: false, platform: 'node', format: 'cjs', external: ['react'],
  plugins: [{ name: 'offline-api', setup(builder) {
    builder.onResolve({ filter: /(^|\/)api(\.js)?$/ }, () => ({ path: 'offline-api', external: true }))
  } }],
})

function pageHarness(getDomains, draft, activePage = 'articleGen') {
  const slots = [], effects = [], sent = []
  let cursor = 0, dirty = true, tree
  const props = { activePage, articleGenDraft: draft, onArticleGenDraftConsumed() { props.articleGenDraft = null; dirty = true } }
  const hooks = { ...React,
    useState(initial) {
      const index = cursor++
      if (!slots[index]) slots[index] = { value: typeof initial === 'function' ? initial() : initial }
      return [slots[index].value, update => {
        const value = typeof update === 'function' ? update(slots[index].value) : update
        if (!Object.is(value, slots[index].value)) { slots[index].value = value; dirty = true }
      }]
    },
    useRef(initial) { const index = cursor++; return slots[index] ||= { current: initial } },
    useEffect(callback, deps) {
      const index = cursor++, old = slots[index]
      if (!old || deps.some((value, i) => !Object.is(value, old.deps[i]))) {
        slots[index] = { deps }; effects.push(callback)
      }
    },
  }
  const fakeApi = Object.fromEntries([...api.matchAll(/export function (\w+)/g)].map(([, name]) => [name, async () => ({})]))
  Object.assign(fakeApi, {
    getGenreCatalog: async () => catalog,
    getSceneTaxonomy: async () => ({ items: [{ name: '年度工作总结' }] }),
    getBusinessDomainCatalog: getDomains,
    getGenerationLog: async () => ({ entries: [] }),
    generateArticle: async payload => { sent.push(payload); return { articles: [] } },
  })
  const loaded = { exports: {} }
  const require = createRequire(import.meta.url)
  new Function('require', 'module', 'exports', pageBundle.outputFiles[0].text)(
    name => name === 'react' ? hooks : name === 'offline-api' ? fakeApi : require(name), loaded, loaded.exports)
  const Page = loaded.exports.default
  const elements = node => !node || typeof node !== 'object' ? [] : [node, ...React.Children.toArray(node.props?.children).flatMap(elements)]
  return { sent,
    find: predicate => elements(tree).find(predicate),
    async flush() {
      for (let i = 0; i < 12; i++) {
        if (dirty) { dirty = false; cursor = 0; tree = Page(props); effects.splice(0).forEach(effect => effect()) }
        await new Promise(resolve => setImmediate(resolve))
      }
      assert.equal(dirty, false, 'page state should settle')
    },
  }
}

test('real single page waits for catalog then preserves ambiguous history in the submitted request', async () => {
  let resolve
  const pending = new Promise(done => { resolve = done })
  const history = Object.freeze({ title: '历史工作总结', documentType: '工作总结', business_domain: '纪检/巡察', isHistoryReplay: true })
  const page = pageHarness(() => pending, history)
  await page.flush()
  const generate = () => page.find(node => node.props?.className === 'write-generate-button')
  assert.equal(generate().props.disabled, true)
  assert.equal(page.sent.length, 0)
  resolve({ items: businessDomains })
  await page.flush()
  const select = page.find(node => node.props?.id === 'article-business-domain')
  assert.equal(select.props.value, '纪检/巡察')
  assert.equal(select.props.items, businessDomains)
  assert.equal(generate().props.disabled, false)
  await generate().props.onClick()
  assert.equal(page.sent.length, 1)
  assert.equal(page.sent[0].business_domain, '纪检/巡察')
  assert.equal(history.business_domain, '纪检/巡察')
})

test('real batch page preserves mixed history and supplies separate canonical options for new tasks', async () => {
  let fail = true
  const history = Object.freeze({ title: '历史任务', documentType: '工作总结', businessDomain: '纪检/巡察', isHistoryReplay: true })
  const page = pageHarness(async () => { if (fail) throw new Error('目录暂不可用'); return { items: businessDomains } }, history, 'batchTask')
  const batchPage = () => page.find(node => node.props?.onReloadCatalogs)
  await page.flush()
  assert.equal(batchPage().props.catalogReady, false)
  assert.match(batchPage().props.catalogError, /业务领域目录加载失败/)
  await batchPage().props.onGenerate()
  assert.equal(page.sent.length, 0)
  fail = false
  await batchPage().props.onReloadCatalogs()
  await page.flush()
  assert.equal(batchPage().props.rows[0].businessDomain, '纪检/巡察')
  batchPage().props.onDefaultChange('businessDomain', '巡察整改')
  await page.flush()
  batchPage().props.onAddTask()
  await page.flush()
  batchPage().props.onTaskChange(1, 'title', '新任务')
  await page.flush()
  assert.equal(batchPage().props.rows[1].businessDomain, '巡察整改')
  await batchPage().props.onGenerate()
  assert.deepEqual(page.sent.map(task => task.business_domain), ['纪检/巡察', '巡察整改'])
  assert.equal(history.businessDomain, '纪检/巡察')
})
