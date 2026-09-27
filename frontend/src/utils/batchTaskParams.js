import { normalizeTaskGenre, productionTaskError, normalizeBusinessDomain, readBusinessDomainCatalog } from './genreCatalog.js'

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

export function buildBatchTask(defaults, overrides = {}, businessDomains = []) {
  const safeDefaults = defaults || {}
  return {
    title: '',
    document_type: safeDefaults.document_type || '',
    scene: safeDefaults.scene ?? null,
    level: safeDefaults.level || '',
    org_type: safeDefaults.org_type || '',
    writing_purpose: safeDefaults.writing_purpose || '',
    target_length: safeDefaults.target_length || 1200,
    reference_mode: safeDefaults.reference_mode || 'auto',
    count: safeDefaults.count || 1,
    ...overrides,
    business_domain: normalizeBusinessDomain(overrides.business_domain ?? (safeDefaults.business_domain || ''), businessDomains),
  }
}

export function applyDefaultsToTask(task, defaults, businessDomains) {
  const safeTask = task || {}
  return buildBatchTask(defaults, {
    title: safeTask.title || '',
    id: safeTask.id,
    expanded: safeTask.expanded,
    status: safeTask.status,
    count: safeTask.count || 1,
  }, businessDomains)
}

export function buildTaskGenerationPayload(task, shared, catalog, businessDomains) {
  const safeTask = normalizeTaskGenre(buildBatchTask({}, task || {}), catalog)
  const safeShared = shared || {}
  const hasExplicitStyle = Object.prototype.hasOwnProperty.call(safeTask, 'style')
  const count = safeTask.count || 1
  const error = productionTaskError({ ...safeTask, count }, catalog, safeShared.export_format || 'docx')
  if (error) throw new Error(error)
  const domainItems = readBusinessDomainCatalog({ items: businessDomains })
  const genre = catalog.genres.find(item => item.document_type === safeTask.document_type)
  return {
    title: String(safeTask.title || '').trim(),
    document_type: safeTask.document_type,
    level: safeTask.level,
    org_type: safeTask.org_type,
    business_domain: normalizeBusinessDomain(safeTask.business_domain, domainItems),
    writing_purpose: safeTask.writing_purpose,
    target_length: safeTask.target_length,
    reference_mode: safeTask.reference_mode,
    scene: safeTask.scene,
    ...(hasExplicitStyle ? { style: safeTask.style } : {}),
    article_mode: 'official_draft',
    generation_route: genre.generation_route,
    temperature: safeShared.temperature ?? 0.7,
    count,
    export_format: safeShared.export_format || 'docx',
  }
}
