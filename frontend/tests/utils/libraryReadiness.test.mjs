import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import api, { processLibraryDocument } from '../../src/api.js'
import {
  BACKFILL_CONFIRMATION, PROCESSING_BUSY_MESSAGE, createLibraryReadiness,
  formatBackfillResult, getDocumentStatus, getIngestionFailureMessage,
} from '../../src/utils/libraryReadiness.js'

const doc = (id, ingestion_status) => ({ id, title: `合成资料 ${id}`, ingestion_status })
const flush = async () => { for (let i = 0; i < 30; i++) await Promise.resolve() }

function fakeTimers() {
  let id = 0
  const pending = new Map()
  return {
    pending,
    setTimeout(fn, ms) { assert.equal(ms, 2500); pending.set(++id, fn); return id },
    clearTimeout(key) { pending.delete(key) },
    async tick() {
      const tasks = [...pending.values()]
      pending.clear()
      tasks.forEach((fn) => fn())
      await flush()
    },
  }
}

function setup(t, documents, overrides = {}) {
  const stored = new Map(documents.map((item) => [item.id, { ...item }]))
  const calls = [], updates = [], notices = [], errors = []
  const timers = fakeTimers()
  const controller = createLibraryReadiness({
    timers,
    listDocuments: async (...args) => { calls.push(['list', ...args]); return { items: [...stored.values()] } },
    getDocument: async (id) => { calls.push(['get', id]); return { ...stored.get(id) } },
    processDocument: async (id) => {
      calls.push(['post', id])
      const current = { ...stored.get(id), ingestion_status: 'PROCESSING' }
      stored.set(id, current)
      return current
    },
    onDocument: (value) => updates.push(value),
    onNotice: (value) => notices.push(value),
    onPollingError: (value) => errors.push(value),
    ...overrides,
  })
  t.after(() => controller.dispose())
  return { controller, stored, calls, updates, notices, errors, timers,
    terminal(id, status = 'READY') { stored.set(id, doc(id, status)) },
    posts: () => calls.filter(([method]) => method === 'post').map(([, id]) => id),
  }
}

test('readiness labels, tones and actions use only ingestion_status, including old/unknown values', () => {
  for (const [status, label, tone, action] of [
    ['READY', '可用于写作', 'success', null], ['PROCESSING', '正在处理', 'info', '处理中'],
    ['FAILED', '处理失败', 'danger', '重试'], ['INDEXED', '待处理', 'neutral', '处理'],
    [undefined, '待处理', 'neutral', '处理'], ['toString', '待处理', 'neutral', '处理'],
    ['LEGACY', '待处理', 'neutral', '处理'],
  ]) assert.deepEqual(getDocumentStatus({ ingestion_status: status, has_embedding: true }), { label, tone, action })
})

for (const state of ['INDEXED', 'FAILED']) {
  test(`${state}: process/retry publishes PROCESSING immediately, coalesces clicks, reaches READY and stops polling`, async (t) => {
    const h = setup(t, [doc('A', state)])
    const first = h.controller.process(doc('A', state))
    const second = h.controller.process(doc('A', state))
    assert.equal(first, second)
    await flush()
    assert.equal(h.updates[0].ingestion_status, 'PROCESSING')
    assert.deepEqual(h.posts(), ['A'])
    assert.equal(h.timers.pending.size, 1)
    await h.timers.tick()
    assert.equal(h.timers.pending.size, 1)
    h.terminal('A')
    await h.timers.tick()
    assert.equal((await first).document.ingestion_status, 'READY')
    assert.equal(h.timers.pending.size, 0)
    const count = h.calls.length
    await h.timers.tick()
    assert.equal(h.calls.length, count)
  })
}

test('PROCESSING is observed once; READY/FAILED/INDEXED are never periodically queried', async (t) => {
  const documents = [doc('P', 'PROCESSING'), doc('R', 'READY'), doc('F', 'FAILED'), doc('I', 'INDEXED')]
  const h = setup(t, documents)
  h.controller.observe(documents)
  h.controller.observe(documents)
  const clicked = h.controller.process(documents[0])
  assert.equal(h.timers.pending.size, 1)
  h.terminal('P', 'FAILED')
  await h.timers.tick()
  await clicked
  assert.deepEqual(h.calls, [['get', 'P']])
  assert.equal(h.timers.pending.size, 0)
})

for (const firstStatus of ['READY', 'FAILED']) {
  test(`backfill waits for A ${firstStatus} before starting B; skips READY/FAILED/PROCESSING`, async (t) => {
    const h = setup(t, [doc('R', 'READY'), doc('A', 'INDEXED'), doc('B', 'INDEXED'), doc('F', 'FAILED'), doc('P', 'PROCESSING')])
    const progress = []
    const finished = h.controller.backfill({ onProgress: (value) => progress.push(value) })
    await flush()
    assert.deepEqual(h.calls[0], ['list', 1, -1])
    assert.deepEqual(h.posts(), ['A'])
    await h.timers.tick()
    assert.deepEqual(h.posts(), ['A'])
    h.terminal('A', firstStatus)
    await h.timers.tick()
    assert.deepEqual(h.posts(), ['A', 'B'])
    h.terminal('B')
    await h.timers.tick()
    assert.deepEqual(await finished, { success: firstStatus === 'READY' ? 2 : 1, failed: firstStatus === 'FAILED' ? 1 : 0, skipped: 3 })
    assert.equal(progress.at(-1).completed, 2)
    assert.equal(progress.at(-1).total, 2)
    assert.match(progress.at(-1).title, /B/)
    assert.equal(h.timers.pending.size, 0)
    assert.deepEqual(await h.controller.backfill(), { success: 0, failed: 0, skipped: 5 })
    assert.deepEqual(h.posts(), ['A', 'B'])
  })
}

test('backfill rechecks an INDEXED snapshot before POST and skips a newly READY document', async (t) => {
  const h = setup(t, [doc('A', 'READY')], { listDocuments: async () => ({ items: [doc('A', 'INDEXED')] }) })
  assert.deepEqual(await h.controller.backfill(), { success: 0, failed: 0, skipped: 1 })
  assert.deepEqual(h.posts(), [])
})

test('busy response is an informational notice with GET reconciliation and no second POST', async (t) => {
  let posts = 0
  const h = setup(t, [doc('A', 'INDEXED')], {
    processDocument: async () => { posts++; throw Object.assign(new Error('unsafe provider detail'), { errorCode: 'MATERIAL_PROCESSING_BUSY' }) },
  })
  assert.equal((await h.controller.process(doc('A', 'INDEXED'))).skipped, true)
  assert.equal(posts, 1)
  assert.deepEqual(h.notices, [PROCESSING_BUSY_MESSAGE])
  assert.equal(h.updates.at(-1).ingestion_status, 'INDEXED')
  assert.equal(h.timers.pending.size, 0)
})

test('busy reconciliation also follows an already PROCESSING document to terminal', async (t) => {
  const h = setup(t, [doc('A', 'PROCESSING')], {
    processDocument: async () => { throw Object.assign(new Error(), { errorCode: 'MATERIAL_PROCESSING_BUSY' }) },
  })
  const finished = h.controller.process(doc('A', 'INDEXED'))
  await flush()
  assert.equal(h.timers.pending.size, 1)
  h.terminal('A')
  await h.timers.tick()
  assert.equal((await finished).document.ingestion_status, 'READY')
})

test('lost POST response reconciles accepted work without automatically retrying POST', async (t) => {
  let count = 0
  const h = setup(t, [doc('A', 'PROCESSING')], { processDocument: async () => { count++; throw new Error('lost response') } })
  const finished = h.controller.process(doc('A', 'INDEXED'))
  await flush()
  h.terminal('A')
  await h.timers.tick()
  assert.equal((await finished).document.ingestion_status, 'READY')
  assert.equal(count, 1)
})

test('unconfirmed processing state stops backfill and never starts B', async (t) => {
  let reads = 0
  const h = setup(t, [doc('A', 'INDEXED'), doc('B', 'INDEXED')], {
    getDocument: async () => { if (reads++) throw new Error('offline'); return doc('A', 'INDEXED') },
  })
  const finished = h.controller.backfill()
  const rejected = assert.rejects(finished, /offline/)
  await flush()
  await h.timers.tick()
  await rejected
  assert.deepEqual(h.posts(), ['A'])
  assert.equal(h.timers.pending.size, 0)
})

test('dispose clears timers, prevents B, and a new page continues remaining INDEXED documents', async (t) => {
  const h = setup(t, [doc('A', 'INDEXED'), doc('B', 'INDEXED')])
  const finished = h.controller.backfill()
  const rejected = assert.rejects(finished, { name: 'AbortError' })
  await flush()
  h.controller.dispose()
  await rejected
  assert.equal(h.timers.pending.size, 0)
  assert.deepEqual(h.posts(), ['A'])
  assert.equal(h.stored.get('B').ingestion_status, 'INDEXED')
  h.terminal('A') // already accepted server work completes independently
  const next = setup(t, [...h.stored.values()])
  const resumed = next.controller.backfill()
  await flush()
  assert.deepEqual(next.posts(), ['B'])
  next.terminal('B')
  await next.timers.tick()
  assert.deepEqual(await resumed, { success: 1, failed: 0, skipped: 1 })
})

test('dispose while GET is in flight suppresses state publication and later timers', async (t) => {
  let resolveRead
  const h = setup(t, [], { getDocument: () => new Promise((resolve) => { resolveRead = resolve }) })
  h.controller.observe([doc('P', 'PROCESSING')])
  await h.timers.tick()
  h.controller.dispose()
  resolveRead(doc('P', 'READY'))
  await flush()
  assert.deepEqual(h.updates, [])
  assert.deepEqual(h.errors, [])
  assert.equal(h.timers.pending.size, 0)
})

test('dispose while full list is loading starts no work after the response arrives', async (t) => {
  let resolveList
  const h = setup(t, [], { listDocuments: () => new Promise((resolve) => { resolveList = resolve }) })
  const finished = h.controller.backfill()
  const rejected = assert.rejects(finished, { name: 'AbortError' })
  h.controller.dispose()
  resolveList({ items: [doc('A', 'INDEXED')] })
  await rejected
  assert.deepEqual(h.posts(), [])
  assert.equal(h.timers.pending.size, 0)
})

test('dispose while POST is in flight never starts a poll or the next document', async (t) => {
  let resolvePost
  const posted = []
  const h = setup(t, [doc('A', 'INDEXED'), doc('B', 'INDEXED')], {
    processDocument: (id) => { posted.push(id); return new Promise((resolve) => { resolvePost = resolve }) },
  })
  const finished = h.controller.backfill()
  const rejected = assert.rejects(finished, { name: 'AbortError' })
  await flush()
  h.controller.dispose()
  resolvePost(doc('A', 'PROCESSING'))
  await rejected
  assert.deepEqual(posted, ['A'])
  assert.equal(h.timers.pending.size, 0)
})

test('a failed scheduling request stays INDEXED and does not prevent later candidates', async (t) => {
  const posted = []
  const h = setup(t, [doc('A', 'INDEXED'), doc('B', 'INDEXED')], {
    processDocument: async (id) => {
      posted.push(id)
      if (id === 'A') throw new Error('request rejected')
      return doc(id, 'READY')
    },
  })
  assert.deepEqual(await h.controller.backfill(), { success: 1, failed: 1, skipped: 0 })
  assert.deepEqual(posted, ['A', 'B'])
  assert.equal(h.stored.get('A').ingestion_status, 'INDEXED')
})

test('PROCESSING unexpectedly reverting to INDEXED pauses the backfill instead of starting B', async (t) => {
  const h = setup(t, [doc('A', 'INDEXED'), doc('B', 'INDEXED')])
  const finished = h.controller.backfill()
  const rejected = assert.rejects(finished, /无法确认/)
  await flush()
  h.terminal('A', 'INDEXED')
  await h.timers.tick()
  await rejected
  assert.deepEqual(h.posts(), ['A'])
})

test('empty backfill, unknown status and repeated invocation never enqueue extra work', async (t) => {
  let resolveList
  const h = setup(t, [], { listDocuments: () => new Promise((resolve) => { resolveList = resolve }) })
  const first = h.controller.backfill()
  await assert.rejects(h.controller.backfill(), /等待/)
  resolveList({ items: [doc('old', 'UNKNOWN')] })
  assert.deepEqual(await first, { success: 0, failed: 0, skipped: 1 })
  assert.deepEqual(h.posts(), [])
})

test('API uses official process route and maps 409 to safe notice without network', async (t) => {
  const original = api.defaults.adapter
  t.after(() => { api.defaults.adapter = original })
  api.defaults.adapter = async (config) => {
    assert.equal(config.method, 'post')
    assert.equal(config.url, '/library/documents/fake%2Fid/process')
    return { data: doc('fake/id', 'PROCESSING'), status: 202, config }
  }
  assert.equal((await processLibraryDocument('fake/id')).ingestion_status, 'PROCESSING')
  api.defaults.adapter = async () => { throw { response: { status: 409, data: { detail: {
    error_type: 'MATERIAL_PROCESSING_BUSY', message: 'raw provider secret',
  } } } } }
  await assert.rejects(processLibraryDocument('A'), (error) => error.errorCode === 'MATERIAL_PROCESSING_BUSY' && error.message === PROCESSING_BUSY_MESSAGE)
})

test('failure copy never includes backend secrets and result counts are dynamic', () => {
  assert.equal(getIngestionFailureMessage({ ingestion_error: { message: 'key path stack vector chunk' } }), '资料处理未完成，可重试。')
  assert.equal(formatBackfillResult({ success: 7, failed: 2, skipped: 4 }), '历史资料处理完成：成功 7，失败 2，跳过 4')
  assert.match(BACKFILL_CONFIRMATION, /Embedding（向量化）服务/)
  assert.match(BACKFILL_CONFIRMATION, /不会重复处理/)
})

test('page wires upload observation, per-document actions, confirmation and lifecycle cleanup', async () => {
  const page = await readFile(new URL('../../src/pages/LibraryPage.jsx', import.meta.url), 'utf8')
  assert.doesNotMatch(page, /has_embedding|processLibraryAll|ingestion_stage|embedding_chunks|file_path|ingestion_error\.message/)
  assert.match(page, /processButton\(document\)/)
  assert.match(page, /processButton\(selectedDocument\)/)
  assert.match(page, /observe\(\[response\.document\]\)/)
  assert.match(page, /controller\.dispose\(\)/)
  assert.match(page, /message: BACKFILL_CONFIRMATION/)
  assert.match(page, /onConfirm: runBackfill/)
})
