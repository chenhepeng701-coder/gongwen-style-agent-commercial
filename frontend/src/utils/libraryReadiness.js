const STATUS = {
  INDEXED: { label: '待处理', tone: 'neutral', action: '处理' },
  PROCESSING: { label: '正在处理', tone: 'info', action: '处理中' },
  READY: { label: '可用于写作', tone: 'success', action: null },
  FAILED: { label: '处理失败', tone: 'danger', action: '重试' },
}

export const PROCESSING_BUSY_MESSAGE = '资料正在处理中，请稍后查看。'
export const BACKFILL_CONFIRMATION = '将依次处理尚未完成材料处理的历史资料。\n处理过程会调用当前配置的 Embedding（向量化）服务。\n已经可用于写作的资料不会重复处理。'
export const READINESS_UNAVAILABLE_MESSAGE = '暂时无法确认资料处理状态，请稍后刷新查看；本次未继续启动后续资料。'

export function getDocumentStatus(document) {
  return Object.hasOwn(STATUS, document?.ingestion_status) ? STATUS[document.ingestion_status] : STATUS.INDEXED
}

export function getIngestionFailureMessage() {
  // Do not echo arbitrary provider messages or internal fields into the UI.
  return '资料处理未完成，可重试。'
}

export function formatBackfillResult({ success, failed, skipped }) {
  return `历史资料处理完成：成功 ${success}，失败 ${failed}，跳过 ${skipped}`
}

function checkActive(signal) {
  if (signal.aborted) throw new DOMException('已停止查看处理状态', 'AbortError')
}

function delay(ms, signal, timers) {
  checkActive(signal)
  return new Promise((resolve, reject) => {
    const abort = () => {
      timers.clearTimeout(timer)
      signal.removeEventListener('abort', abort)
      reject(new DOMException('已停止查看处理状态', 'AbortError'))
    }
    const timer = timers.setTimeout(() => {
      signal.removeEventListener('abort', abort)
      resolve()
    }, ms)
    signal.addEventListener('abort', abort, { once: true })
  })
}

// One page lifetime, one poll per active document. No persisted queue or POST retries.
export function createLibraryReadiness({
  getDocument, processDocument, listDocuments, onDocument = () => {},
  onNotice = () => {}, onPollingError = () => {}, interval = 2500,
  timers = globalThis,
}) {
  const lifetime = new AbortController()
  const { signal } = lifetime
  const polls = new Map()
  const requests = new Map()
  let backfilling = false

  function publish(document) {
    checkActive(signal)
    if (!document?.id || !Object.hasOwn(STATUS, document.ingestion_status)) {
      throw new Error(READINESS_UNAVAILABLE_MESSAGE)
    }
    onDocument(document)
    return document
  }

  async function read(id) {
    checkActive(signal)
    const document = await getDocument(id)
    checkActive(signal)
    if (document?.id !== id) throw new Error(READINESS_UNAVAILABLE_MESSAGE)
    return publish(document)
  }

  function watch(document) {
    if (polls.has(document.id)) return polls.get(document.id)
    if (document.ingestion_status !== 'PROCESSING') return Promise.resolve(document)
    const promise = (async () => {
      let current = document
      while (current.ingestion_status === 'PROCESSING') {
        await delay(interval, signal, timers)
        current = await read(document.id)
        if (current.ingestion_status === 'INDEXED') throw new Error(READINESS_UNAVAILABLE_MESSAGE)
      }
      return current
    })().finally(() => polls.delete(document.id))
    polls.set(document.id, promise)
    return promise
  }

  function observe(documents) {
    for (const document of documents) {
      if (document?.ingestion_status !== 'PROCESSING' || requests.has(document.id) || polls.has(document.id)) continue
      watch(document).catch((error) => {
        if (!signal.aborted) onPollingError(error)
      })
    }
  }

  function process(document) {
    checkActive(signal)
    if (requests.has(document.id)) return requests.get(document.id)
    if (document.ingestion_status === 'READY') return Promise.resolve({ document, skipped: true })
    if (document.ingestion_status === 'PROCESSING' || polls.has(document.id)) {
      return watch({ ...document, ingestion_status: 'PROCESSING' }).then((current) => ({ document: current, skipped: true }))
    }
    // Register before publishing the optimistic state so rapid clicks coalesce.
    const promise = Promise.resolve().then(async () => {
      checkActive(signal)
      publish({ ...document, ingestion_status: 'PROCESSING', ingestion_error: {} })
      let current
      let skipped = false
      try {
        current = await processDocument(document.id)
        checkActive(signal)
        if (current?.id !== document.id) throw new Error(READINESS_UNAVAILABLE_MESSAGE)
        publish(current)
      } catch (error) {
        checkActive(signal)
        skipped = error.errorCode === 'MATERIAL_PROCESSING_BUSY'
        if (skipped) onNotice(PROCESSING_BUSY_MESSAGE)
        // A lost response can still mean the server accepted the POST. Reconcile
        // with GET, never repeat POST or claim FAILED without server evidence.
        current = await read(document.id)
      }
      current = await watch(current)
      checkActive(signal)
      return { document: current, skipped }
    }).finally(() => requests.delete(document.id))
    requests.set(document.id, promise)
    return promise
  }

  async function backfill({ onProgress = () => {} } = {}) {
    checkActive(signal)
    if (backfilling || requests.size) throw new Error('请等待当前资料处理完成。')
    backfilling = true
    try {
      const response = await listDocuments(1, -1)
      checkActive(signal)
      if (!Array.isArray(response?.items)) throw new Error(READINESS_UNAVAILABLE_MESSAGE)
      const documents = [...new Map(response.items.map((document) => [document.id, document])).values()]
      const candidates = documents.filter((document) => document.ingestion_status === 'INDEXED')
      const result = { success: 0, failed: 0, skipped: documents.length - candidates.length }
      onProgress({ completed: 0, total: candidates.length, title: '' })
      for (const [index, candidate] of candidates.entries()) {
        checkActive(signal)
        onProgress({ completed: index, total: candidates.length, title: candidate.title || '未命名资料' })
        // Another tab may have processed this document since the full list was read.
        const current = await read(candidate.id)
        if (current.ingestion_status !== 'INDEXED') {
          result.skipped += 1
        } else {
          const outcome = await process(current)
          checkActive(signal)
          if (outcome.skipped) result.skipped += 1
          else if (outcome.document.ingestion_status === 'READY') result.success += 1
          else if (outcome.document.ingestion_status === 'FAILED' || outcome.document.ingestion_status === 'INDEXED') result.failed += 1
          else throw new Error(READINESS_UNAVAILABLE_MESSAGE)
        }
        onProgress({ completed: index + 1, total: candidates.length, title: candidate.title || '未命名资料' })
      }
      return result
    } finally {
      backfilling = false
    }
  }

  return { observe, process, backfill, dispose: () => lifetime.abort() }
}
