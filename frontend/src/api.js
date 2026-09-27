import axios from 'axios'

const api = axios.create({
  baseURL: '/api',
  timeout: 300000,
  headers: { 'Content-Type': 'application/json' },
})

const ERROR_CODE_MESSAGES = {
  MODEL_TIMEOUT: 'AI模型响应超时，请稍后重试。',
  MODEL_CONNECTION_ERROR: 'AI模型连接失败，请检查模型配置。',
  MATERIAL_INSUFFICIENT: '当前资料的真实事实容量不足，无法支持本次文章生成。\n请补充相关参考材料后重新生成。',
  INSUFFICIENT_EVIDENCE: '当前资料不足以支撑安全生成，请补充资料或调整资料范围。',
  FACT_SAFETY_BLOCKED: '模型生成内容包含无法由资料支持的事实，系统已停止交付。',
  FACT_VALIDATION_FAILED: '模型生成内容未通过事实安全校验，系统已停止交付。',
  INTERNAL_ERROR: '服务器内部错误，请稍后重试。',
  MATERIAL_PROCESSING_BUSY: '资料正在处理中，请稍后查看。',
}

export function publicErrorMessage(error) {
  const status = error.response?.status
  const data = error.response?.data || {}
  const detail = data.detail
  const errorCode = typeof detail === 'object' && detail !== null
    ? detail.error_code || detail.error_type
    : data.error_code || data.error_type
  if (errorCode && ERROR_CODE_MESSAGES[errorCode]) return ERROR_CODE_MESSAGES[errorCode]

  const candidate = typeof detail === 'object' && detail !== null
    ? detail.message
    : data.message

  if (typeof candidate === 'string' && candidate.trim()) return candidate
  if (status >= 500) return ERROR_CODE_MESSAGES.INTERNAL_ERROR
  if (typeof detail === 'string' && status < 500) return detail
  return error.response?.statusText || '请求失败'
}

// ── Response interceptor: extract response.data ──
api.interceptors.response.use(
  (response) => response.data,
  (error) => {
    let message = '未知错误'
    if (error.code === 'ECONNABORTED') {
      message = ERROR_CODE_MESSAGES.MODEL_TIMEOUT
    } else if (error.response) {
      // HTTP 4xx / 5xx
      message = publicErrorMessage(error)
    } else if (error.request) {
      // Network error — no response received
      message = '无法连接服务器，请检查网络或确认后端服务已启动'
    } else {
      message = error.message || '请求配置错误'
    }
    const publicError = new Error(message)
    const detail = error.response?.data?.detail
    if (typeof detail === 'object' && detail !== null) {
      publicError.errorCode = detail.error_code || detail.error_type || ''
      publicError.stage = detail.stage || ''
      publicError.retryable = detail.retryable === true
      publicError.preserveForm = detail.preserve_form === true
    }
    return Promise.reject(publicError)
  }
)

// Health
export function getHealth() {
  return api.get('/health')
}

// Settings
export function getSettings(providerKey) {
  return api.get('/settings', { params: providerKey ? { provider_key: providerKey } : {} })
}

export function saveSettings(payload) {
  return api.post('/settings', payload)
}

export function testConnection(payload) {
  return api.post('/settings/test', payload)
}

export function getModelPresets() {
  return api.get('/settings/model-presets')
}

// Embedding Settings
export function getEmbeddingSettings() {
  return api.get('/settings/embedding')
}

export function saveEmbeddingSettings(payload) {
  return api.post('/settings/embedding', payload)
}

export function testEmbeddingConnection(payload) {
  return api.post('/settings/embedding/test', payload)
}

// Library
export function uploadDocument(file) {
  const formData = new FormData()
  formData.append('file', file)
  return api.post('/library/upload', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 60000,
  })
}

export function listDocuments(page, pageSize) {
  const params = {}
  if (page !== undefined && page !== null) params.page = page
  if (pageSize !== undefined && pageSize !== null) params.page_size = pageSize
  return api.get('/library/documents', { params })
}

export function getDocument(id) {
  return api.get(`/library/documents/${id}`)
}

export function processLibraryDocument(id) {
  return api.post(`/library/documents/${encodeURIComponent(id)}/process`)
}

export function getSourceDocumentContent(id) {
  return api.get(`/library/documents/${encodeURIComponent(id)}/source-content`)
}

export function deleteDocument(id) {
  return api.delete(`/library/documents/${id}`)
}

export function updateDocumentTags(id, tags) {
  return api.put(`/library/documents/${id}/tags`, tags)
}

export function generateEmbedding(id) {
  return api.post(`/library/documents/${id}/embed`, {}, { timeout: 120000 })
}

export function getEmbeddingStatus(id) {
  return api.get(`/library/documents/${id}/embed`)
}

export function extractCards(id, cardCount = undefined) {
  const params = cardCount ? { card_count: cardCount } : {}
  return api.post(`/library/documents/${id}/cards`, null, { params, timeout: 120000 })
}

export function getCards(id) {
  return api.get(`/library/documents/${id}/cards`)
}

// Style
export function extractStyle(id) {
  return api.post(`/library/documents/${id}/style`, {}, { timeout: 120000 })
}

export function getStyle(id) {
  return api.get(`/library/documents/${id}/style`)
}

// Retrieval
export function searchDocuments(query, topK = 20) {
  return api.post('/retrieval/search', { query, top_k: topK }, { timeout: 60000 })
}

export function getRetrievalStats() {
  return api.get('/retrieval/stats')
}

// Scene Classification
export function classifyScene(text) {
  return api.post('/retrieval/scene/classify', { text })
}

export function classifyDocumentScene(docId) {
  return api.post(`/retrieval/scene/classify/${docId}`)
}

export function batchClassifyAll() {
  return api.post('/retrieval/scene/classify-all', {}, { timeout: 300000 })
}

export function getSceneTaxonomy() {
  return api.get('/retrieval/scene/taxonomy')
}

export function getSceneStats() {
  return api.get('/retrieval/scene/stats')
}

export function batchEmbedAll() {
  return api.post('/library/embed-all', {}, { timeout: 600000 })
}

export function batchExtractCardsAll() {
  return api.post('/library/extract-cards-all', {}, { timeout: 600000 })
}

export function batchExtractStyleAll() {
  return api.post('/library/extract-style-all', {}, { timeout: 600000 })
}

export const libraryProcessingSteps = [
  { name: '场景分类', progressText: '正在进行：场景分类', run: batchClassifyAll },
  { name: '向量化', progressText: '正在进行：向量化', run: batchEmbedAll },
  { name: '素材卡片', progressText: '正在进行：素材卡片生成', run: batchExtractCardsAll },
  { name: '风格卡片', progressText: '正在进行：风格卡片生成', run: batchExtractStyleAll },
]

export async function processLibraryAll({ steps = libraryProcessingSteps, onProgress } = {}) {
  for (const step of steps) {
    onProgress?.(step)
    try {
      await step.run()
    } catch (error) {
      error.step = step
      throw error
    }
  }
}

export function getLibraryStats() {
  return api.get('/library/stats')
}

export function getSceneStyleProfile(sceneName) {
  return api.get(`/retrieval/scene/style/${encodeURIComponent(sceneName)}`)
}

export function getSceneStyleSummary(sceneName) {
  return api.get(`/retrieval/scene/style/${encodeURIComponent(sceneName)}/summary`)
}

export function getAllSceneProfiles() {
  return api.get('/retrieval/scene/styles')
}

// Operation
export function getTopicSuggestions() {
  return api.get('/operation/topic-suggestions')
}

// Generation
export function getGenreCatalog() {
  return api.get('/generation/genres')
}

export function generateArticle(payload) {
  return api.post('/generation/generate', payload, { timeout: 600000 })
}

export function generateArticleBatch(payload) {
  return api.post('/generation/generate', payload, { timeout: 600000 })
}

export function getGenerationLog() {
  return api.get('/generation/log')
}

export function getGenerationSourceTraceability(entryId) {
  return api.get(`/generation/log/${encodeURIComponent(entryId)}/source-traceability`)
}

export function getGenerationOutputDir() {
  return api.get('/generation/output-dir')
}

export function saveGenerationOutputDir(outputDir) {
  return api.post('/generation/output-dir', { output_dir: outputDir })
}

export function openGenerationOutputDir(outputDir) {
  return api.post('/generation/open-output-dir', { output_dir: outputDir })
}

export function getGenerationDefaultSettings() {
  return api.get('/generation/default-settings')
}

export function saveGenerationDefaultSettings(payload) {
  return api.post('/generation/default-settings', payload)
}

export function deleteGenerationLog(entryId) {
  return api.delete(`/generation/log/${entryId}`)
}

export function openGenerationWord(entryId) {
  return api.post(`/generation/log/${entryId}/open-word`)
}

export function openGenerationFile(entryId) {
  return api.post(`/generation/log/${entryId}/open-file`)
}

export function openGenerationFolder(entryId) {
  return api.post(`/generation/log/${entryId}/open-folder`)
}

export function exportWord(title, content, contentFormat = 'plain') {
  return api.post('/generation/export/word', { title, ...(contentFormat === 'full-article/v1' ? { article_markdown: content } : { content }), content_format: contentFormat }, {
    responseType: 'blob',
    timeout: 120000,
  })
}

export function saveFullArticle(baseArticle, articleMarkdown) {
  return api.post('/generation/log/article', { base_article: baseArticle, article_markdown: articleMarkdown,
    entry_id: baseArticle.log_id || '', article_index: baseArticle.article_index || 0 })
}

export function exportFullArticleTxt(articleMarkdown) {
  return api.post('/generation/export/txt', { article_markdown: articleMarkdown }, { responseType: 'blob' })
}

export function exportDocumentModeWord(payload) {
  return api.post('/generation/export/wechat-word', payload, {
    responseType: 'blob',
    timeout: 120000,
  })
}

export default api
