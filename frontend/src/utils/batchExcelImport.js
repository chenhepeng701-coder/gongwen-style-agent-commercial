import { normalizeUiTaskGenre, normalizeScene, normalizeBusinessDomain, normalizeUiTaskBusinessDomain, readBusinessDomainCatalog } from './genreCatalog.js'

const TASK_FIELDS = ['title', 'documentType', 'scene', 'level', 'orgType', 'businessDomain', 'writingPurpose', 'wordCount', 'referenceScope']

// Import compatibility only, never a catalog choice or an alias mapping.
// This mixed old cell cannot identify either canonical domain; preserve it.
const PRESERVED_LEGACY_DOMAIN_CELLS = new Set(['纪检/巡察'])

const HEADER_ALIASES = {
  title: ['标题', '主题', '标题/主题'],
  documentType: ['文种', '文种类型'],
  scene: ['写作场景', '场景', 'scene'],
  level: ['层级'],
  orgType: ['单位类型'],
  businessDomain: ['业务领域'],
  writingPurpose: ['写作目的'],
  wordCount: ['字数', '篇幅', '字数/篇幅'],
  referenceScope: ['参考范围', '参考资料', '参考资料范围'],
  count: ['篇数', '生成数量', '生成篇数'],
  sequence: ['序号'],
}

const LEGACY_FIELD_ALIASES = {
  document_type: 'documentType',
  org_type: 'orgType',
  business_domain: 'businessDomain',
  writing_purpose: 'writingPurpose',
  target_length: 'wordCount',
  word_count: 'wordCount',
  reference_mode: 'referenceScope',
  reference_scope: 'referenceScope',
  documentLevel: 'level',
  unitType: 'orgType',
  purpose: 'writingPurpose',
}

function cleanText(value) {
  if (value === undefined || value === null) return ''
  return String(value).trim().replace(/\s+/g, ' ').replace(/／/g, '/')
}

function compact(value) {
  return cleanText(value).replace(/\s*\/\s*/g, '/').replace(/\s/g, '')
}

const HEADER_LOOKUP = Object.fromEntries(
  Object.entries(HEADER_ALIASES).flatMap(([field, aliases]) => aliases.map(alias => [compact(alias), field])),
)

function optionMap(options = []) {
  return new Map(options.flatMap(option => {
    if (typeof option === 'object') {
      return [[compact(option.value), option.label ?? option.value], [compact(option.label), option.label ?? option.value]]
    }
    return [[compact(option), option]]
  }))
}

function positiveInteger(value, fieldLabel) {
  const text = cleanText(value)
  if (!/^\d+$/.test(text) || Number(text) <= 0) return { error: `${fieldLabel}“${text}”不是有效正整数。` }
  return { value: Number(text) }
}

function mapOption(rawValue, fieldLabel, options) {
  const text = cleanText(rawValue)
  const value = optionMap(options).get(compact(text))
  return value ? { value } : { error: `${fieldLabel}“${text}”不是系统有效选项。` }
}

function isPlaceholderHeader(row) {
  return row.length >= 9 && row.slice(0, 9).every((cell, index) => compact(cell) === `列${index + 1}`)
}

function buildColumnMap(row) {
  const columns = {}
  if (isPlaceholderHeader(row)) {
    ;['title', 'documentType', 'level', 'orgType', 'businessDomain', 'writingPurpose', 'wordCount', 'referenceScope', 'count']
      .forEach((field, index) => { columns[field] = index })
    return columns
  }
  row.forEach((cell, index) => {
    const field = HEADER_LOOKUP[compact(cell)]
    if (field && columns[field] === undefined) columns[field] = index
  })
  return columns
}

function detectHeader(row) {
  const columns = buildColumnMap(row)
  const fields = Object.keys(columns).filter(field => !['sequence', 'count'].includes(field))
  return { hasHeader: isPlaceholderHeader(row) || fields.includes('title') || fields.length >= 2, columns }
}

function normalizeImportedTask(task = {}) {
  const normalized = {}
  Object.entries(task).forEach(([key, value]) => {
    const normalizedKey = LEGACY_FIELD_ALIASES[key] || key
    if (TASK_FIELDS.includes(normalizedKey) || normalizedKey === 'count') normalized[normalizedKey] = value
  })
  return normalized
}

function nonEmptyTaskFields(task) {
  return Object.fromEntries(Object.entries(task).filter(([, value]) => value !== undefined && value !== null && value !== ''))
}

export function formatBatchImportSummary(result) {
  return [
    `Excel导入完成：文件总行数${result.totalRows}行，成功${result.tasks.length}条，跳过${result.skipped.length}条，失败${result.errors.length}条。`,
    ...result.skipped.map(item => `第${item.row}行：${item.reason}`),
    ...result.errors.map(item => `第${item.row}行：${item.reason}`),
  ].join('\n')
}

export function parseBatchExcelRows(rows, defaults, optionSets) {
  const businessDomains = readBusinessDomainCatalog({ items: optionSets.businessDomain })
  const normalizedRows = Array.isArray(rows) ? rows : []
  const firstNonBlankIndex = normalizedRows.findIndex(row => Array.isArray(row) && row.some(cell => cleanText(cell)))
  if (firstNonBlankIndex < 0) return { totalRows: 0, tasks: [], skipped: [], errors: [], mode: 'single', diagnostics: {} }

  const headerIndex = normalizedRows.findIndex((row, index) => (
    index >= firstNonBlankIndex && Array.isArray(row) && detectHeader(row).hasHeader
  ))
  const actualHeaderIndex = headerIndex >= 0 ? headerIndex : firstNonBlankIndex
  const header = headerIndex >= 0 ? detectHeader(normalizedRows[actualHeaderIndex]) : { hasHeader: false, columns: { title: 0 } }
  const columns = header.hasHeader ? header.columns : { title: 0 }
  const keyFields = ['title', 'documentType', 'level', 'orgType', 'businessDomain', 'writingPurpose', 'wordCount', 'referenceScope']
  const missingKeyFields = header.hasHeader ? keyFields.filter(field => columns[field] === undefined) : []
  const diagnostics = { headerRowIndex: header.hasHeader ? actualHeaderIndex : null, columns, missingKeyFields, unrecognizedHeaders: header.hasHeader ? normalizedRows[actualHeaderIndex].filter((cell, index) => !Object.values(columns).includes(index) && cleanText(cell)) : [] }
  if (header.hasHeader && columns.title === undefined) {
    return { totalRows: 0, tasks: [], skipped: [], errors: [{ row: actualHeaderIndex + 1, reason: '未识别标题列，已拒绝导入以避免生成默认任务。' }], mode: 'invalid', diagnostics }
  }

  const normalizedDefaults = nonEmptyTaskFields(normalizeImportedTask(defaults))
  const tasks = []
  const skipped = []
  const errors = []
  normalizedRows.forEach((row, index) => {
    const rowNumber = index + 1
    if (index < firstNonBlankIndex || (header.hasHeader && index <= actualHeaderIndex)) return
    const cells = Array.isArray(row) ? row : []
    if (!cells.some(cell => cleanText(cell))) {
      skipped.push({ row: rowNumber, reason: '空白行，已跳过。' })
      return
    }
    const title = cleanText(cells[columns.title ?? 0])
    if (!title) {
      skipped.push({ row: rowNumber, reason: '标题为空，已跳过。' })
      return
    }

    const importedTask = { title }
    const validations = [
      ['level', '层级', optionSets.level],
      ['orgType', '单位类型', optionSets.orgType],
      ['writingPurpose', '写作目的', optionSets.writingPurpose],
      ['referenceScope', '参考资料范围', optionSets.referenceScope],
    ]
    let error = ''
    if (columns.businessDomain !== undefined && cleanText(cells[columns.businessDomain])) {
      const rawDomain = String(cells[columns.businessDomain])
      const domain = normalizeBusinessDomain(rawDomain, businessDomains)
      if (businessDomains.some(item => item.value === domain)) importedTask.businessDomain = domain
      else if (PRESERVED_LEGACY_DOMAIN_CELLS.has(compact(rawDomain))) importedTask.businessDomain = rawDomain
      else error = `业务领域“${cleanText(rawDomain)}”不是系统有效选项。`
    }
    if (columns.documentType !== undefined && cleanText(cells[columns.documentType])) {
      // Preserve unresolved historical identities for display; request admission
      // later requires a canonical production genre from the backend catalog.
      importedTask.documentType = cleanText(cells[columns.documentType])
    }
    if (columns.scene !== undefined) {
      const explicitScene = normalizeScene(cells[columns.scene])
      if (explicitScene && !optionSets.scene?.includes(explicitScene)) {
        error = `写作场景“${explicitScene}”不是系统有效选项。`
      } else importedTask.scene = explicitScene || ''
    }
    for (const [field, label, options] of validations) {
      if (error) break
      if (columns[field] === undefined || !cleanText(cells[columns[field]])) continue
      const mapped = mapOption(cells[columns[field]], label, options)
      if (mapped.error) { error = mapped.error; break }
      importedTask[field] = mapped.value
    }
    if (!error && columns.wordCount !== undefined && cleanText(cells[columns.wordCount])) {
      const parsed = positiveInteger(cells[columns.wordCount], '字数')
      if (parsed.error) error = parsed.error
      else importedTask.wordCount = parsed.value
    }
    if (!error && columns.count !== undefined && cleanText(cells[columns.count])) {
      const parsed = positiveInteger(cells[columns.count], '生成篇数')
      if (parsed.error) error = parsed.error
      else importedTask.count = parsed.value
    }
    if (error) errors.push({ row: rowNumber, reason: error })
    else {
      const task = { ...normalizedDefaults, ...nonEmptyTaskFields(importedTask), count: importedTask.count ?? 1 }
      if (Object.prototype.hasOwnProperty.call(importedTask, 'scene')) task.scene = importedTask.scene
      const normalized = normalizeUiTaskBusinessDomain(task, businessDomains)
      tasks.push(optionSets.genreCatalog ? normalizeUiTaskGenre(normalized, optionSets.genreCatalog) : normalized)
    }
  })
  return { totalRows: Math.max(0, normalizedRows.length - (header.hasHeader ? actualHeaderIndex + 1 : firstNonBlankIndex)), tasks, skipped, errors, mode: header.hasHeader && Object.keys(columns).some(field => !['title', 'sequence'].includes(field)) ? 'multi' : 'single', diagnostics }
}

export { TASK_FIELDS, normalizeImportedTask }
