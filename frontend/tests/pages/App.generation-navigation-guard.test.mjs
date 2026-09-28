import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import React from 'react'
import { build } from 'esbuild'
import { catalog, businessDomains } from '../fixtures/genreCatalog.mjs'

const apiSource = readFileSync(new URL('../../src/api.js', import.meta.url), 'utf8')
const bundle = await build({
  entryPoints: [fileURLToPath(new URL('../../src/App.jsx', import.meta.url))],
  bundle: true, write: false, platform: 'node', format: 'cjs', external: ['react'],
  plugins: [{ name: 'offline-api', setup(builder) {
    builder.onResolve({ filter: /(^|\/)api(\.js)?$/ }, () => ({ path: 'offline-api', external: true }))
  } }],
})
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
      : node.props?.['aria-hidden'] === 'true' ? '' : textOf(node.rendered ?? node.props?.children)

// Extend the existing offline hook harness through App and its navigation UI.
// Keyed component instances and effect cleanup exercise the real pending owner,
// request handlers and navigation callbacks without a browser or model service.
function harness(t) {
  const originalWindow = globalThis.window
  const storage = new Map(), listeners = new Map(), registrations = [], removals = []
  globalThis.window = {
    localStorage: {
      getItem: key => storage.get(key) ?? null,
      setItem: (key, value) => storage.set(key, String(value)),
    },
    addEventListener(type, handler) {
      if (!listeners.has(type)) listeners.set(type, new Set())
      listeners.get(type).add(handler)
      registrations.push({ type, handler })
    },
    removeEventListener(type, handler) {
      listeners.get(type)?.delete(handler)
      removals.push({ type, handler })
    },
  }
  const instances = new Map(), sent = [], unmounted = []
  let current, cursor = 0, dirty = true, tree, mounted = true, effects = []
  const hooks = { ...React,
    useState(initial) {
      const instance = current, index = cursor++
      if (!instance.slots[index]) instance.slots[index] = { value: typeof initial === 'function' ? initial() : initial }
      return [instance.slots[index].value, update => {
        assert.equal(instance.mounted, true, 'request must not update an unmounted generation owner')
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
  const fakeApi = Object.fromEntries([...apiSource.matchAll(/export (?:async )?function (\w+)/g)].map(([, name]) => [name, async () => ({})]))
  Object.assign(fakeApi, {
    getGenreCatalog: async () => catalog,
    getSceneTaxonomy: async () => ({ items: [{ name: '年度工作总结' }] }),
    getBusinessDomainCatalog: async () => ({ items: businessDomains }),
    getGenerationLog: async () => ({ entries: [] }),
    generateArticle: payload => new Promise((resolve, reject) => sent.push({ payload, resolve, reject })),
  })
  const loaded = { exports: {} }, require = createRequire(import.meta.url)
  new Function('require', 'module', 'exports', bundle.outputFiles[0].text)(
    name => name === 'react' ? hooks : name === 'offline-api' ? fakeApi : require(name), loaded, loaded.exports,
  )
  const App = loaded.exports.default
  const executed = new Set(['App', 'Layout', 'AppSidebar', 'AppTopBar', 'ArticleGenPage', 'GenerationPage', 'GenerationWaitingIndicator', 'ConfirmDialog'])
  const cleanup = instance => {
    instance.slots.forEach(slot => slot?.cleanup?.())
    instance.mounted = false
    unmounted.push(instance)
  }
  function render(node, path, visited) {
    if (Array.isArray(node)) return node.map((child, index) => render(child, `${path}/${child?.key ?? index}`, visited))
    if (!node || typeof node !== 'object') return node
    if (typeof node.type === 'function' && executed.has(node.type.name)) {
      const id = `${path}/${node.type.name}:${node.key ?? ''}`
      let instance = instances.get(id)
      if (!instance) { instance = { name: node.type.name, slots: [], mounted: true }; instances.set(id, instance) }
      visited.add(id)
      current = instance
      cursor = 0
      const output = node.type(node.props)
      return { ...node, rendered: render(output, `${id}/output`, visited) }
    }
    return { ...node, props: { ...node.props, children: render(node.props?.children, `${path}/children`, visited) } }
  }
  const page = {
    sent, storage, registrations, removals, unmounted,
    find: predicate => descendants(tree).find(predicate),
    instance: name => [...instances.values()].find(instance => instance.name === name),
    listenerCount: () => listeners.get('beforeunload')?.size || 0,
    beforeUnload() {
      const event = { returnValue: undefined, prevented: false, preventDefault() { this.prevented = true } }
      for (const handler of listeners.get('beforeunload') || []) handler(event)
      return event
    },
    replayEffects() {
      // StrictMode-equivalent setup -> cleanup -> setup must retain one listener.
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
          tree = render(React.createElement(App), 'root', visited)
          for (const [id, instance] of instances) {
            if (!visited.has(id)) { cleanup(instance); instances.delete(id) }
          }
          const pending = effects
          effects = []
          pending.forEach(effect => effect())
        }
        await new Promise(resolve => setImmediate(resolve))
      }
      assert.equal(dirty, false, 'App state should settle')
    },
  }
  t.after(() => {
    page.unmount()
    if (originalWindow === undefined) delete globalThis.window
    else globalThis.window = originalWindow
  })
  return page
}

const component = (page, name) => page.find(node => node.type?.name === name)
const activePage = page => component(page, 'Layout').props.activePage
const dialog = page => page.find(node => node.type?.name === 'ConfirmDialog' && node.props.title === '文章正在生成')
const navigate = (page, target) => component(page, 'Layout').props.onNavigate(target)
const click = (page, label) => {
  const button = page.find(node => node.props?.onClick && textOf(node) === label)
  assert.ok(button, `visible navigation control must exist: ${label}`)
  return button.props.onClick()
}
const draft = { title: '2026年安全生产工作总结', document_type: '工作总结', business_domain: '安全生产' }
async function prepare(page) {
  await page.flush()
  component(page, 'ArticleGenPage').props.onReuseGenerationEntry(draft)
  await page.flush()
}
async function begin(page) {
  const button = page.find(node => node.props?.className === 'write-generate-button')
  assert.ok(button)
  button.props.onClick()
  await page.flush()
  assert.equal(activePage(page), 'genResult')
  assert.equal(page.sent.length, 1)
}

for (const [label, target] of [['资料库', 'library'], ['我的文稿', 'history']]) {
  test(`pending generation blocks real sidebar ${label} click and preserves its single request`, async t => {
    const page = harness(t)
    await prepare(page)
    const owner = page.instance('ArticleGenPage')
    await begin(page)
    const request = page.sent[0], payload = structuredClone(request.payload)
    click(page, label)
    await page.flush()
    assert.equal(activePage(page), 'genResult')
    assert.equal(page.storage.get('gongwen_active_page'), 'genResult')
    assert.equal(dialog(page).props.open, true)
    assert.equal(dialog(page).props.description, '文章正在生成。为避免生成结果丢失，请等待生成完成后再离开当前写作工作区。')
    assert.equal(dialog(page).props.cancelText, '继续等待')
    assert.equal(dialog(page).props.confirmText, '查看生成状态')
    dialog(page).props.onCancel()
    await page.flush()
    assert.equal(dialog(page).props.open, false)
    assert.equal(activePage(page), 'genResult')
    assert.equal(page.instance('ArticleGenPage'), owner)
    assert.equal(page.unmounted.includes(owner), false)
    assert.equal(page.sent.length, 1)
    assert.equal(page.sent[0], request)
    assert.deepEqual(request.payload, payload)
    const article = { title: draft.title, content: '离线测试正文', id: `guard-${target}` }
    request.resolve({ articles: [article] })
    await page.flush()
    assert.equal(component(page, 'EditorPage').props.article, article)
    click(page, label)
    await page.flush()
    assert.equal(activePage(page), target)
    assert.equal(page.listenerCount(), 0)
    assert.equal(page.sent.length, 1)
  })
}

test('every top-level destination is guarded while workspace routes preserve the mounted owner', async t => {
  const page = harness(t)
  await prepare(page)
  await begin(page)
  const owner = page.instance('ArticleGenPage')
  for (const target of ['library', 'history', 'account', 'model', 'operation', 'prompt', 'credits', 'backup']) {
    navigate(page, target)
    await page.flush()
    assert.equal(activePage(page), 'genResult', target)
    assert.equal(dialog(page).props.open, true, target)
    dialog(page).props.onCancel()
    await page.flush()
  }
  for (const target of ['articleGen', 'batchTask', 'editor', 'genResult']) {
    navigate(page, target)
    await page.flush()
    assert.equal(activePage(page), target)
    assert.equal(dialog(page).props.open, false)
    assert.equal(page.instance('ArticleGenPage'), owner)
  }
  assert.equal(page.unmounted.includes(owner), false)
  assert.equal(page.sent.length, 1)
  page.sent[0].resolve({ articles: [] })
  await page.flush()
  for (const target of ['library', 'history', 'account', 'model', 'operation', 'prompt', 'credits', 'backup']) {
    navigate(page, target)
    await page.flush()
    assert.equal(activePage(page), target)
    assert.equal(dialog(page).props.open, false)
  }
})

test('continue waiting stays in parameters and view generation status restores genResult without resubmission', async t => {
  const page = harness(t)
  await prepare(page)
  await begin(page)
  const request = page.sent[0], payload = structuredClone(request.payload), owner = page.instance('ArticleGenPage')
  click(page, '返回写作参数')
  await page.flush()
  assert.equal(activePage(page), 'articleGen')
  click(page, '账户中心')
  await page.flush()
  assert.equal(dialog(page).props.open, true)
  dialog(page).props.onCancel()
  await page.flush()
  assert.equal(activePage(page), 'articleGen')
  component(page, 'AppSidebar').props.onOpenBackup()
  await page.flush()
  assert.equal(activePage(page), 'articleGen')
  dialog(page).props.onConfirm()
  await page.flush()
  assert.equal(activePage(page), 'genResult')
  assert.equal(dialog(page).props.open, false)
  assert.equal(page.instance('ArticleGenPage'), owner)
  assert.equal(page.sent.length, 1)
  assert.equal(page.sent[0], request)
  assert.deepEqual(request.payload, payload)
})

for (const outcome of ['success', 'failure']) {
  test(`${outcome} clears beforeunload and blocked dialog while retaining existing result handling`, async t => {
    const page = harness(t)
    await prepare(page)
    assert.equal(page.listenerCount(), 0)
    assert.equal(page.registrations.length, 0)
    await begin(page)
    assert.equal(page.listenerCount(), 1)
    const unload = page.beforeUnload()
    assert.equal(unload.prevented, true)
    assert.equal(unload.returnValue, '', 'use browser-native protection without custom warning text')
    click(page, '资料库')
    await page.flush()
    assert.equal(dialog(page).props.open, true)
    const article = { title: draft.title, content: '离线测试完整正文', id: 'offline-guard' }
    if (outcome === 'success') page.sent[0].resolve({ articles: [article] })
    else page.sent[0].reject(new Error('离线测试：生成失败'))
    await page.flush()
    assert.equal(dialog(page).props.open, false)
    assert.equal(page.listenerCount(), 0)
    assert.equal(page.beforeUnload().prevented, false)
    assert.equal(page.registrations.length, 1)
    assert.equal(page.removals.length, 1)
    assert.equal(page.removals[0].handler, page.registrations[0].handler)
    if (outcome === 'success') {
      assert.equal(activePage(page), 'editor')
      assert.equal(component(page, 'EditorPage').props.article, article)
    } else {
      assert.equal(activePage(page), 'articleGen')
      const modal = component(page, 'Modal')
      assert.equal(modal.props.visible, true)
      assert.equal(modal.props.type, 'error')
      assert.equal(modal.props.title, `第 1 项生成失败: ${draft.title}`)
      assert.equal(modal.props.message, '离线测试：生成失败')
    }
    click(page, '我的文稿')
    await page.flush()
    assert.equal(activePage(page), 'history')
    assert.equal(page.sent.length, 1)
  })
}

test('StrictMode-equivalent effect replay retains one beforeunload listener and unmount removes it', async t => {
  const page = harness(t)
  await prepare(page)
  page.replayEffects()
  await page.flush()
  assert.equal(page.listenerCount(), 0)
  await begin(page)
  page.replayEffects()
  await page.flush()
  assert.equal(page.listenerCount(), 1)
  assert.equal(page.registrations.length, 2)
  assert.equal(page.removals.length, 1)
  assert.equal(page.sent.length, 1)
  click(page, '返回写作参数')
  await page.flush()
  assert.equal(page.listenerCount(), 1)
  assert.equal(page.registrations.length, 2)
  page.unmount()
  assert.equal(page.listenerCount(), 0)
  assert.equal(page.removals.length, 2)
})

test('batch protection lasts across sequential requests and ends after the final response', async t => {
  const page = harness(t)
  await prepare(page)
  navigate(page, 'batchTask')
  await page.flush()
  component(page, 'BatchTaskPage').props.onAddTask()
  await page.flush()
  component(page, 'BatchTaskPage').props.onTaskChange(1, 'title', '第二项安全生产工作总结')
  await page.flush()
  const completion = component(page, 'BatchTaskPage').props.onGenerate()
  await page.flush()
  assert.equal(page.sent.length, 1)
  assert.equal(page.listenerCount(), 1)
  click(page, '资料库')
  await page.flush()
  page.sent[0].resolve({ articles: [] })
  await page.flush()
  assert.equal(page.sent.length, 2, 'only the planned second batch task may add a request')
  assert.equal(dialog(page).props.open, true)
  assert.equal(page.listenerCount(), 1)
  assert.equal(page.registrations.length, 1)
  click(page, '我的文稿')
  await page.flush()
  assert.equal(activePage(page), 'genResult')
  page.sent[1].resolve({ articles: [] })
  await completion
  await page.flush()
  assert.equal(activePage(page), 'batchTask')
  assert.equal(dialog(page).props.open, false)
  assert.equal(page.listenerCount(), 0)
  assert.equal(page.sent.length, 2)
  click(page, '资料库')
  await page.flush()
  assert.equal(activePage(page), 'library')
})

test('a request that never starts installs no unload or navigation protection', async t => {
  const page = harness(t)
  await page.flush()
  page.find(node => node.props?.className === 'write-generate-button').props.onClick()
  await page.flush()
  assert.equal(page.sent.length, 0)
  assert.equal(page.listenerCount(), 0)
  click(page, '资料库')
  await page.flush()
  assert.equal(activePage(page), 'library')
  assert.equal(dialog(page).props.open, false)
})
