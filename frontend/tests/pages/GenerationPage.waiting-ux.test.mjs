import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import React from 'react'
import { build } from 'esbuild'
import { catalog, businessDomains } from '../fixtures/genreCatalog.mjs'

const read = name => readFileSync(new URL(`../../src/${name}`, import.meta.url), 'utf8')
const apiSource = read('api.js')
const pipeline = [
  '理解写作任务', '检索本地资料', '提取可靠事实', '匹配文种与写作要求',
  '生成完整初稿', '核查事实与内容', '整理可编辑文章',
]
const bundles = Object.fromEntries(await Promise.all(['GenerationPage', 'ArticleGenPage'].map(async name => {
  const result = await build({
    entryPoints: [fileURLToPath(new URL(`../../src/pages/${name}.jsx`, import.meta.url))],
    bundle: true, write: false, platform: 'node', format: 'cjs', external: ['react'],
    plugins: [{ name: 'offline-api', setup(builder) {
      builder.onResolve({ filter: /(^|\/)api(\.js)?$/ }, () => ({ path: 'offline-api', external: true }))
    } }],
  })
  return [name, result.outputFiles[0].text]
})))

const descendants = node => Array.isArray(node)
  ? node.flatMap(descendants)
  : !node || typeof node !== 'object'
    ? []
    : [node, ...descendants(node.rendered ?? node.props?.children)]
const textOf = node => Array.isArray(node)
  ? node.map(textOf).join('')
  : node == null || typeof node === 'boolean'
    ? ''
    : typeof node !== 'object'
      ? String(node)
      : textOf(node.rendered ?? node.props?.children)

// Extend the existing offline hook-harness pattern with keyed child instances
// and effect cleanup. Execute the real page, waiting component and handlers;
// unrelated editor/dialog components remain inspectable element boundaries.
function harness(pageName, initialProps = {}) {
  const instances = new Map(), sent = [], navigations = []
  let current, cursor = 0, dirty = true, tree, mounted = true
  let effects = []
  const props = { ...initialProps }
  props.onNavigate ||= target => { navigations.push(target); props.activePage = target; dirty = true }
  props.onArticleGenDraftConsumed ||= () => { props.articleGenDraft = null; dirty = true }
  const hooks = { ...React,
    useState(initial) {
      const instance = current, index = cursor++
      if (!instance.slots[index]) instance.slots[index] = { value: typeof initial === 'function' ? initial() : initial }
      return [instance.slots[index].value, update => {
        assert.equal(instance.mounted, true, 'unmounted components must not receive timer updates')
        const value = typeof update === 'function' ? update(instance.slots[index].value) : update
        if (!Object.is(value, instance.slots[index].value)) {
          instance.slots[index].value = value
          dirty = true
        }
      }]
    },
    useRef(initial) { return current.slots[cursor++] ||= { current: initial } },
    useEffect(callback, deps) {
      const instance = current, index = cursor++, old = instance.slots[index]
      if (!old || !deps || deps.length !== old.deps?.length || deps.some((value, i) => !Object.is(value, old.deps[i]))) {
        instance.slots[index] = { deps, callback, cleanup: old?.cleanup }
        effects.push(() => {
          instance.slots[index].cleanup?.()
          instance.slots[index].cleanup = callback()
        })
      }
    },
  }
  const fakeApi = Object.fromEntries([...apiSource.matchAll(/export function (\w+)/g)].map(([, name]) => [name, async () => ({})]))
  Object.assign(fakeApi, {
    getGenreCatalog: async () => catalog,
    getSceneTaxonomy: async () => ({ items: [{ name: '年度工作总结' }] }),
    getBusinessDomainCatalog: async () => ({ items: businessDomains }),
    getGenerationLog: async () => ({ entries: [] }),
    generateArticle: payload => new Promise((resolve, reject) => sent.push({ payload, resolve, reject })),
  })
  const loaded = { exports: {} }, require = createRequire(import.meta.url)
  new Function('require', 'module', 'exports', bundles[pageName])(
    name => name === 'react' ? hooks : name === 'offline-api' ? fakeApi : require(name), loaded, loaded.exports,
  )
  const Page = loaded.exports.default
  const executedComponents = new Set(['ArticleGenPage', 'GenerationPage', 'GenerationWaitingIndicator'])
  const cleanup = instance => {
    instance.slots.forEach(slot => slot?.cleanup?.())
    instance.mounted = false
  }
  function render(node, path, visited) {
    if (Array.isArray(node)) return node.map((child, index) => render(child, `${path}/${child?.key ?? index}`, visited))
    if (!node || typeof node !== 'object') return node
    if (typeof node.type === 'function' && executedComponents.has(node.type.name)) {
      const id = `${path}/${node.type.name}:${node.key ?? ''}`
      let instance = instances.get(id)
      if (!instance) { instance = { slots: [], mounted: true }; instances.set(id, instance) }
      visited.add(id)
      current = instance
      cursor = 0
      const output = node.type(node.props)
      return { ...node, rendered: render(output, `${id}/output`, visited) }
    }
    return { ...node, props: { ...node.props, children: render(node.props?.children, `${path}/children`, visited) } }
  }
  return {
    sent, navigations, props,
    find: predicate => descendants(tree).find(predicate),
    all: predicate => descendants(tree).filter(predicate),
    text: () => textOf(tree),
    update(next) { Object.assign(props, next); dirty = true },
    replayEffects() {
      // React StrictMode replays mount effects as setup -> cleanup -> setup.
      for (const instance of instances.values()) {
        for (const slot of instance.slots) {
          if (slot?.callback) { slot.cleanup?.(); slot.cleanup = slot.callback() }
        }
      }
    },
    unmount() { instances.forEach(cleanup); instances.clear(); mounted = false; tree = null },
    async flush() {
      for (let i = 0; i < 12; i++) {
        if (mounted && dirty) {
          dirty = false
          const visited = new Set()
          tree = render(React.createElement(Page, props), 'root', visited)
          for (const [id, instance] of instances) {
            if (!visited.has(id)) { cleanup(instance); instances.delete(id) }
          }
          const pending = effects
          effects = []
          pending.forEach(effect => effect())
        }
        await new Promise(resolve => setImmediate(resolve))
      }
      assert.equal(dirty, false, 'page state should settle')
    },
  }
}

function fakeClock(t) {
  t.mock.timers.enable({ apis: ['Date', 'setInterval'], now: 1_700_000_000_000 })
  const active = new Set()
  const set = globalThis.setInterval, clear = globalThis.clearInterval
  t.mock.method(globalThis, 'setInterval', (callback, delay, ...args) => {
    const id = set(callback, delay, ...args)
    active.add(id)
    return id
  })
  t.mock.method(globalThis, 'clearInterval', id => { active.delete(id); return clear(id) })
  return { active, tick: milliseconds => t.mock.timers.tick(milliseconds) }
}
const timer = page => textOf(page.find(node => node.props?.role === 'timer'))
const waiting = page => page.find(node => node.props?.className === 'generation-page')
const component = (page, name) => page.find(node => node.type?.name === name)
const beginSingle = page => page.find(node => node.props?.className === 'write-generate-button').props.onClick()
const draft = { title: '2026年安全生产工作总结', documentType: '工作总结', businessDomain: '安全生产' }

test('waiting timer starts at zero and formats real elapsed seconds including hours', async t => {
  const clock = fakeClock(t)
  const page = harness('GenerationPage', { loading: true, requestTitle: draft.title })
  t.after(() => page.unmount())
  await page.flush()
  assert.match(timer(page), /00:00/)
  assert.equal(clock.active.size, 1)
  clock.tick(1000)
  await page.flush()
  assert.match(timer(page), /00:01/)
  clock.tick(58_000)
  await page.flush()
  assert.match(timer(page), /00:59/)
  clock.tick(1000)
  await page.flush()
  assert.match(timer(page), /01:00/)
  clock.tick(3_675_000)
  await page.flush()
  assert.match(timer(page), /01:02:15/)
})

test('timer catches up to clock time after delayed interval delivery and cleans up on unmount', async t => {
  const clock = fakeClock(t)
  const page = harness('GenerationPage', { loading: true, requestTitle: draft.title })
  await page.flush()
  t.mock.timers.setTime(Date.now() + 74_000)
  clock.tick(1000)
  await page.flush()
  assert.match(timer(page), /01:15/)
  page.unmount()
  assert.equal(clock.active.size, 0)
  assert.equal(globalThis.clearInterval.mock.callCount(), 1)
  clock.tick(10_000)
})

test('StrictMode-equivalent effect replay leaves exactly one interval and clears it on exit', async t => {
  const clock = fakeClock(t)
  const page = harness('GenerationPage', { loading: true, requestTitle: draft.title })
  await page.flush()
  assert.equal(clock.active.size, 1)
  page.replayEffects()
  assert.equal(globalThis.setInterval.mock.callCount(), 2)
  assert.equal(globalThis.clearInterval.mock.callCount(), 1)
  assert.equal(clock.active.size, 1)
  clock.tick(3000)
  await page.flush()
  assert.match(timer(page), /00:03/)
  page.unmount()
  assert.equal(clock.active.size, 0)
  assert.equal(globalThis.clearInterval.mock.callCount(), 2)
})

test('waiting hints change at 31 and 91 seconds without claiming a backend stage', async t => {
  const clock = fakeClock(t)
  const page = harness('GenerationPage', { loading: true, requestTitle: draft.title })
  t.after(() => page.unmount())
  const hint = () => textOf(page.find(node => node.props?.className === 'generation-wait-hint'))
  await page.flush()
  assert.equal(hint(), '正在处理写作任务，请稍候。')
  clock.tick(30_000)
  await page.flush()
  assert.equal(hint(), '正在处理写作任务，请稍候。')
  clock.tick(1000)
  await page.flush()
  assert.equal(hint(), '完整文章生成可能需要一些时间，请保持当前页面打开。')
  clock.tick(59_000)
  await page.flush()
  assert.equal(hint(), '完整文章生成可能需要一些时间，请保持当前页面打开。')
  clock.tick(1000)
  await page.flush()
  assert.equal(hint(), '复杂材料和较长文章处理时间可能更长，系统仍在等待生成结果。')
})

test('waiting content preserves title and exposes static steps without live timer announcements', async t => {
  const clock = fakeClock(t)
  const title = '  2026年安全生产工作总结（原始标题）  '
  const page = harness('GenerationPage', { loading: true, requestTitle: title })
  t.after(() => page.unmount())
  await page.flush()
  const output = page.text()
  assert.match(output, /正在生成文章/)
  assert.ok(output.includes(title), 'the original title must remain unmodified')
  assert.match(output, /已用时间/)
  assert.match(output, /文章将按照以下流程完成处理/)
  for (const step of pipeline) assert.ok(output.includes(step))
  for (const label of ['请求状态', '任务类型', '结果入口', '返回写作参数']) assert.ok(output.includes(label))
  assert.doesNotMatch(output, /\d\s*[%％]|预计剩余|检索完成|事实提取完成|步骤已完成|当前正在执行/)
  const status = page.find(node => node.props?.role === 'status')
  assert.ok(status)
  assert.equal(status.props['aria-live'], 'polite')
  assert.equal(page.find(node => node.props?.role === 'timer').props['aria-live'], 'off')
  assert.equal(descendants(status).some(node => node.props?.role === 'timer'), false)
  const stepsBefore = page.all(node => node.type === 'li').map(node => [textOf(node), node.props.className])
  assert.equal(stepsBefore.length, 7)
  clock.tick(120_000)
  await page.flush()
  assert.deepEqual(page.all(node => node.type === 'li').map(node => [textOf(node), node.props.className]), stepsBefore)
  assert.doesNotMatch(page.text(), /\d\s*[%％]|预计剩余|检索完成|事实提取完成|步骤已完成|当前正在执行/)
})

test('loading false removes waiting UI and a later request starts a fresh timer', async t => {
  const clock = fakeClock(t)
  const page = harness('GenerationPage', { loading: true, requestTitle: draft.title })
  t.after(() => page.unmount())
  await page.flush()
  clock.tick(9000)
  await page.flush()
  assert.match(timer(page), /00:09/)
  page.update({ loading: false })
  await page.flush()
  assert.equal(waiting(page), undefined)
  assert.equal(clock.active.size, 0)
  page.update({ loading: true })
  await page.flush()
  assert.match(timer(page), /00:00/)
  assert.equal(clock.active.size, 1)
})

test('actual successful request leaves waiting UI and enters the existing article editor', async t => {
  const clock = fakeClock(t)
  const page = harness('ArticleGenPage', { activePage: 'articleGen', articleGenDraft: draft })
  t.after(() => page.unmount())
  await page.flush()
  beginSingle(page)
  await page.flush()
  assert.ok(waiting(page))
  assert.match(timer(page), /00:00/)
  clock.tick(4000)
  await page.flush()
  assert.match(timer(page), /00:04/)
  assert.equal(page.sent.length, 1)
  assert.equal(page.sent[0].payload.title, draft.title)
  assert.equal(Object.keys(page.sent[0].payload).some(key => /elapsed|progress|timer/i.test(key)), false)
  const article = { title: draft.title, content: '测试正文', id: 'offline-success' }
  page.sent[0].resolve({ articles: [article] })
  await page.flush()
  assert.equal(waiting(page), undefined)
  assert.equal(component(page, 'EditorPage').props.article, article)
  assert.deepEqual(page.navigations, ['genResult', 'editor'])
  assert.equal(clock.active.size, 0)
})

test('actual failed request removes waiting UI and preserves the existing error Modal', async t => {
  const clock = fakeClock(t)
  const page = harness('ArticleGenPage', { activePage: 'articleGen', articleGenDraft: draft })
  t.after(() => page.unmount())
  await page.flush()
  beginSingle(page)
  await page.flush()
  assert.ok(waiting(page))
  page.sent[0].reject(new Error('离线测试：生成失败'))
  await page.flush()
  assert.equal(waiting(page), undefined)
  const modal = component(page, 'Modal')
  assert.equal(modal.props.visible, true)
  assert.equal(modal.props.type, 'error')
  assert.equal(modal.props.title, `第 1 项生成失败: ${draft.title}`)
  assert.equal(modal.props.message, '离线测试：生成失败')
  assert.deepEqual(page.navigations, ['genResult', 'articleGen'])
  assert.equal(clock.active.size, 0)
})

test('return to writing parameters clears timer while keeping the same in-flight request', async t => {
  const clock = fakeClock(t)
  const page = harness('ArticleGenPage', { activePage: 'articleGen', articleGenDraft: draft })
  t.after(() => page.unmount())
  await page.flush()
  beginSingle(page)
  await page.flush()
  clock.tick(5000)
  await page.flush()
  const returnButton = page.find(node => node.props?.onClick && textOf(node) === '返回写作参数')
  assert.ok(returnButton)
  returnButton.props.onClick()
  await page.flush()
  assert.equal(waiting(page), undefined)
  assert.equal(clock.active.size, 0)
  clock.tick(10_000)
  await page.flush()
  assert.equal(page.sent.length, 1)
  const article = { title: draft.title, content: '测试正文', id: 'offline-return' }
  page.sent[0].resolve({ articles: [article] })
  await page.flush()
  assert.equal(component(page, 'EditorPage').props.article, article)
  assert.deepEqual(page.navigations, ['genResult', 'articleGen', 'editor'])
  assert.equal(clock.active.size, 0)
})

test('consecutive batch requests with identical titles reset by request identity', async t => {
  const clock = fakeClock(t)
  const page = harness('ArticleGenPage', { activePage: 'batchTask', articleGenDraft: draft })
  t.after(() => page.unmount())
  await page.flush()
  component(page, 'BatchTaskPage').props.onAddTask()
  await page.flush()
  component(page, 'BatchTaskPage').props.onTaskChange(1, 'title', draft.title)
  await page.flush()
  const rows = component(page, 'BatchTaskPage').props.rows
  assert.notEqual(rows[0].id, rows[1].id)
  const completion = component(page, 'BatchTaskPage').props.onGenerate()
  await page.flush()
  clock.tick(9000)
  await page.flush()
  assert.match(timer(page), /00:09/)
  page.sent[0].resolve({ articles: [] })
  await page.flush()
  assert.equal(page.sent.length, 2)
  assert.equal(page.sent[0].payload.title, page.sent[1].payload.title)
  assert.match(timer(page), /00:00/)
  assert.equal(clock.active.size, 1)
  page.sent[1].resolve({ articles: [] })
  await completion
  await page.flush()
  assert.equal(waiting(page), undefined)
  assert.equal(clock.active.size, 0)
  assert.deepEqual(page.navigations, ['genResult', 'batchTask'])
})
