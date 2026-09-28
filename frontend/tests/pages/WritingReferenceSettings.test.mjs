import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import React from 'react'
import { build } from 'esbuild'

const bundle = await build({
  entryPoints: [fileURLToPath(new URL('../../src/components/WritingReferenceSettings.jsx', import.meta.url))],
  bundle: true, write: false, platform: 'node', format: 'cjs', external: ['react'],
  plugins: [{ name: 'local-api', setup(b) {
    b.onResolve({ filter: /\/api$/ }, () => ({ path: 'offline-api', external: true }))
  } }],
})
const nodes = node => Array.isArray(node) ? node.flatMap(nodes)
  : node && typeof node === 'object' ? [node, ...nodes(node.props?.children)] : []
const text = node => Array.isArray(node) ? node.map(text).join('')
  : node && typeof node === 'object' ? text(node.props?.children) : node == null ? '' : String(node)

function harness(api) {
  const states = [], effects = []
  let cursor = 0, tree
  const hooks = { ...React,
    useState(initial) {
      const index = cursor++
      if (!(index in states)) states[index] = initial
      return [states[index], value => { states[index] = value }]
    },
    useRef(initial) { return states[cursor++] ||= { current: initial } },
    useEffect(fn) { const index = cursor++; if (!(index in states)) { states[index] = true; effects.push(fn) } },
  }
  const module = { exports: {} }, require = createRequire(import.meta.url)
  new Function('require', 'module', 'exports', bundle.outputFiles[0].text)(
    name => name === 'react' ? hooks : name === 'offline-api' ? api : require(name), module, module.exports)
  const render = () => { cursor = 0; tree = module.exports.default(); return tree }
  render()
  effects.forEach(fn => fn())
  return { render, input: () => nodes(tree).find(n => n.type === 'input') }
}

test('shows existing configuration and replaces JSON through the same-origin API', async () => {
  const calls = []
  const h = harness({ get: async url => { calls.push(url); return { configured: true } },
    post: async (url, data) => {
      calls.push(url)
      assert.equal(await data.get('file').text(), '{}')
      return { configured: true }
    } })
  await new Promise(resolve => setImmediate(resolve))
  assert.match(text(h.render()), /已配置/)
  assert.match(text(h.render()), /更换文件/)
  const target = { files: [new File(['{}'], 'reference.json', { type: 'application/json' })], value: 'chosen' }
  await h.input().props.onChange({ target })
  assert.equal(target.value, '')
  assert.match(text(h.render()), /配置成功/)
  assert.deepEqual(calls, ['/settings/writing-reference', '/settings/writing-reference'])
})

test('invalid JSON response keeps the prior status and shows a public error', async () => {
  const h = harness({ get: async () => ({ configured: false }), post: async () => { throw Error('JSON 校验失败') } })
  await new Promise(resolve => setImmediate(resolve))
  h.render()
  await h.input().props.onChange({ target: { files: [new File(['bad'], 'bad.json')], value: '' } })
  assert.match(text(h.render()), /未配置/)
  assert.match(text(h.render()), /JSON 校验失败/)
})

test('oversized selection makes no upload request', async () => {
  let uploads = 0
  const h = harness({ get: async () => ({ configured: false }), post: async () => { uploads++ } })
  await new Promise(resolve => setImmediate(resolve))
  h.render()
  await h.input().props.onChange({ target: { files: [{ size: 5 * 1024 * 1024 + 1 }], value: '' } })
  assert.equal(uploads, 0)
  assert.match(text(h.render()), /5 MB/)
})

test('discloses local storage and task-related transfer to the configured model provider', async () => {
  for (const configured of [false, true]) {
    const h = harness({ get: async () => ({ configured }) })
    await new Promise(resolve => setImmediate(resolve))
    const rendered = text(h.render())
    assert.match(rendered, /参考材料保存在本机/)
    assert.match(rendered, /生成文章时.*相关的参考内容.*发送至.*当前配置的模型服务商/)
    assert.doesNotMatch(rendered, /完全不上传|内容始终不出本机|模型服务商无法接触/)
  }
})
