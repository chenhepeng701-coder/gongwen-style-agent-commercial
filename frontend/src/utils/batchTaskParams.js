export const REFERENCE_MODE_OPTIONS = [
  { value: 'auto', label: '自动匹配资料库' },
  { value: 'search', label: '优先参考搜索结果' },
  { value: 'recent', label: '优先参考最近上传资料' },
]

const REFERENCE_MODE_BY_LABEL = Object.fromEntries(
  REFERENCE_MODE_OPTIONS.map(option => [option.label, option.value]),
)

export function referenceModeFromScope(value) {
  if (value === undefined || value === null) return 'auto'
  if (REFERENCE_MODE_OPTIONS.some(option => option.value === value)) return value
  return REFERENCE_MODE_BY_LABEL[value] || 'auto'
}

export function referenceScopeFromMode(value) {
  return REFERENCE_MODE_OPTIONS.find(option => option.value === value)?.label
    || REFERENCE_MODE_OPTIONS[0].label
}

export function buildBatchTask(defaults, overrides = {}) {
  const safeDefaults = defaults || {}
  return {
    title: '',
    document_type: safeDefaults.document_type || '',
    level: safeDefaults.level || '',
    org_type: safeDefaults.org_type || '',
    business_domain: safeDefaults.business_domain || '',
    writing_purpose: safeDefaults.writing_purpose || '',
    target_length: safeDefaults.target_length || 1200,
    reference_mode: safeDefaults.reference_mode || 'auto',
    count: safeDefaults.count || 1,
    ...overrides,
  }
}

export function applyDefaultsToTask(task, defaults) {
  const safeTask = task || {}
  return buildBatchTask(defaults, {
    title: safeTask.title || '',
    id: safeTask.id,
    expanded: safeTask.expanded,
    status: safeTask.status,
    count: safeTask.count || 1,
  })
}

export function buildTaskGenerationPayload(task, shared) {
  const safeTask = buildBatchTask({}, task || {})
  const safeShared = shared || {}
  const hasExplicitScene = Object.prototype.hasOwnProperty.call(safeTask, 'scene')
  const hasExplicitStyle = Object.prototype.hasOwnProperty.call(safeTask, 'style')
  const count = safeTask.count || 1
  const generationRoute = safeTask.document_type === '工作总结' && count === 1
    ? 'full_article_drafting'
    : 'current'
  return {
    title: String(safeTask.title || '').trim(),
    document_type: safeTask.document_type,
    level: safeTask.level,
    org_type: safeTask.org_type,
    business_domain: safeTask.business_domain,
    writing_purpose: safeTask.writing_purpose,
    target_length: safeTask.target_length,
    reference_mode: safeTask.reference_mode,
    scene: hasExplicitScene ? safeTask.scene : null,
    ...(hasExplicitStyle ? { style: safeTask.style } : {}),
    article_mode: 'official_draft',
    generation_route: generationRoute,
    temperature: safeShared.temperature ?? 0.7,
    count,
    export_format: safeShared.export_format || 'docx',
  }
}
