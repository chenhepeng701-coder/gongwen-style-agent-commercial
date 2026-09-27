import test from 'node:test'
import assert from 'node:assert/strict'
import { parseBatchExcelRows } from '../../src/utils/batchExcelImport.js'
import { catalog, businessDomains } from '../fixtures/genreCatalog.mjs'
import { normalizeBusinessDomain } from '../../src/utils/genreCatalog.js'
import * as XLSX from 'xlsx'
import { buildTaskGenerationPayload } from '../../src/utils/batchTaskParams.js'

const defaults = { documentType: '党课讲稿', level: '乡镇 / 街道', orgType: '党政机关', businessDomain: '党建', writingPurpose: '学习研讨', wordCount: 1200, referenceScope: '自动匹配资料库' }
const options = {
  documentType: ['党课讲稿', '领导讲话稿', '工作汇报', '工作总结', '调研报告'],
  level: ['乡镇 / 街道', '县区'], orgType: ['党政机关'],
  businessDomain: businessDomains,
  writingPurpose: ['学习研讨', '工作部署', '贯彻落实', '问题整改', '总结复盘', '其他'],
  referenceScope: ['自动匹配资料库'],
}
const realStructureRows = [
  ['列1', '列2', '列3', '列4', '列5', '列6', '列7', '列8', '列9'],
  ['任务1', '党课讲稿', '乡镇 / 街道', '党政机关', '纪检/巡察', '学习研讨', 1200, '自动匹配资料库', 1],
  ['任务2', '党课讲稿', '乡镇 / 街道', '党政机关', '作风建设', '学习研讨', 1200, '自动匹配资料库', 1],
  ['任务3', '领导讲话稿', '乡镇 / 街道', '党政机关', '纪检/巡察', '工作部署', 1200, '自动匹配资料库', 1],
  ['任务4', '领导讲话稿', '乡镇 / 街道', '党政机关', '安全生产', '工作部署', 1500, '自动匹配资料库', 1],
  ['任务5', '工作汇报', '乡镇 / 街道', '党政机关', '基层治理', '贯彻落实', 1500, '自动匹配资料库', 1],
  ['任务6', '工作汇报', '县区', '党政机关', '纪检/巡察', '问题整改', 1500, '自动匹配资料库', 1],
  ['任务7', '工作总结', '乡镇 / 街道', '党政机关', '纪检/巡察', '总结复盘', 1500, '自动匹配资料库', 1],
  ['任务8', '工作总结', '乡镇 / 街道', '党政机关', '生态环保', '总结复盘', 1500, '自动匹配资料库', 1],
  ['任务9', '调研报告', '乡镇 / 街道', '党政机关', '纪检/巡察', '其他', 1800, '自动匹配资料库', 1],
  ['任务10', '调研报告', '乡镇 / 街道', '党政机关', '基层治理', '其他', 1800, '自动匹配资料库', 1],
]

test('Excel preserves ambiguous mixed legacy cells, separates canonical domains and rejects other unknown cells', () => {
  const rows = [['标题', '业务领域'], ['标准', '纪检 / 廉政'], ['旧混合值', '纪检/巡察'],
    ['排版', ' 纪检 ／ 巡察 '], ['未来别名', '合成旧域乙'], ['巡察', '巡察整改'], ['未知', '旧系统自定义值']]
  const snapshot = structuredClone(rows)
  const result = parseBatchExcelRows(rows, defaults, options)
  assert.deepEqual(result.tasks.map(task => task.businessDomain), ['纪检 / 廉政', '纪检/巡察', ' 纪检 ／ 巡察 ', '合成标准域', '巡察整改'])
  assert.deepEqual(result.errors, [{ row: 7, reason: '业务领域“旧系统自定义值”不是系统有效选项。' }])
  assert.deepEqual(rows, snapshot)
  assert.throws(() => parseBatchExcelRows(rows, defaults, { ...options, businessDomain: [] }), /业务领域目录暂不可用/)
})

test('Excel blank/missing business domain inherits normalized defaults, including unknown historic defaults', () => {
  for (const value of ['纪检/巡察', '纪检 / 廉政', '巡察整改', '历史默认域']) {
    const initial = Object.freeze({ ...defaults, businessDomain: value })
    for (const rows of [[['标题'], ['任务']], [['标题', '业务领域'], ['任务', '']]]) {
      const result = parseBatchExcelRows(rows, initial, options)
      assert.equal(result.errors.length, 0)
      assert.equal(result.tasks[0].businessDomain, normalizeBusinessDomain(value, businessDomains))
      assert.equal(initial.businessDomain, value)
    }
  }
})

test('real in-memory XLSX round trip preserves mixed legacy and distinct canonical payloads', () => {
  const rows = [['标题', '文种', '业务领域'], ['标准', '工作总结', '纪检 / 廉政'],
    ['旧模板', '工作总结', '纪检/巡察'], ['巡察', '工作总结', '巡察整改'], ['安全', '工作总结', '安全生产']]
  const book = XLSX.utils.book_new()
  XLSX.utils.book_append_sheet(book, XLSX.utils.aoa_to_sheet(rows), '任务')
  const restored = XLSX.read(XLSX.write(book, { type: 'buffer', bookType: 'xlsx' }), { type: 'buffer' })
  const result = parseBatchExcelRows(XLSX.utils.sheet_to_json(restored.Sheets['任务'], { header: 1 }), defaults, { ...options, genreCatalog: catalog })
  assert.equal(result.errors.length, 0)
  const payloads = result.tasks.map(task => buildTaskGenerationPayload({ title: task.title,
    document_type: task.documentType, business_domain: task.businessDomain }, {}, catalog, businessDomains))
  assert.deepEqual(payloads.map(task => task.business_domain), ['纪检 / 廉政', '纪检/巡察', '巡察整改', '安全生产'])
})

test('真实二维结构识别占位表头，导入10条且列1不入任务', () => {
  const result = parseBatchExcelRows(realStructureRows, defaults, options)
  assert.equal(result.tasks.length, 10)
  assert.equal(result.totalRows, 10)
  assert.equal(result.diagnostics.headerRowIndex, 0)
  assert.equal(result.tasks.some(task => task.title === '列1'), false)
})

for (const index of [0, 3, 6, 9]) {
  test(`真实结构第${index + 1}条映射全部独立参数`, () => {
    const task = parseBatchExcelRows(realStructureRows, defaults, options).tasks[index]
    const source = realStructureRows[index + 1]
    assert.deepEqual(task, { title: source[0], documentType: source[1], level: source[2], orgType: source[3], businessDomain: normalizeBusinessDomain(source[4], businessDomains), writingPurpose: source[5], wordCount: source[6], referenceScope: source[7], count: source[8] })
  })
}

test('非空Excel值覆盖defaults，空单元格才回退defaults', () => {
  const rows = [['标题', '文种类型', '字数', '业务领域'], ['覆盖', '调研报告', '1800', '基层治理'], ['回退', '', '', '']]
  const [filled, blank] = parseBatchExcelRows(rows, defaults, options).tasks
  assert.equal(filled.documentType, '调研报告'); assert.equal(filled.wordCount, 1800); assert.equal(filled.businessDomain, '基层治理')
  assert.equal(blank.documentType, defaults.documentType); assert.equal(blank.wordCount, defaults.wordCount); assert.equal(blank.businessDomain, defaults.businessDomain)
})

test('snake_case输入只在导入边界归一化为camelCase', () => {
  const legacyDefaults = { document_type: '工作总结', org_type: '党政机关', business_domain: '生态环保', writing_purpose: '总结复盘', target_length: 1500, reference_mode: '自动匹配资料库' }
  const task = parseBatchExcelRows([['标题'], ['任务']], legacyDefaults, options).tasks[0]
  assert.equal(task.documentType, '工作总结'); assert.equal(task.orgType, '党政机关'); assert.equal(task.businessDomain, '生态环保')
  assert.equal(task.wordCount, 1500); assert.equal('document_type' in task, false); assert.equal('target_length' in task, false)
})

test('wordCount为number，任务对象不保留snake_case', () => {
  const task = parseBatchExcelRows(realStructureRows, defaults, options).tasks[0]
  assert.equal(typeof task.wordCount, 'number')
  for (const key of ['document_type', 'org_type', 'business_domain', 'writing_purpose', 'target_length', 'reference_mode']) assert.equal(key in task, false)
})

test('标题行和说明行不被当成任务，缺失标题映射拒绝静默默认导入', () => {
  const rows = [['说明', '', ''], ['业务领域', '文种类型'], ['基层治理', '工作总结']]
  const result = parseBatchExcelRows(rows, defaults, options)
  assert.equal(result.tasks.length, 0)
  assert.equal(result.errors.length, 1)
})

test('标准业务表头仍映射且未识别列被诊断', () => {
  const result = parseBatchExcelRows([['标题', '文种', '未知列'], ['任务', '工作总结', 'x']], defaults, options)
  assert.equal(result.tasks[0].documentType, '工作总结')
  assert.deepEqual(result.diagnostics.unrecognizedHeaders, ['未知列'])
})

test('场景列独立保留每行值，自动场景清空默认值', () => {
  const rows = [['标题', '文种', '写作场景'], ['第一行', '工作总结', '年度工作总结'], ['第二行', '工作总结', '专题汇报'], ['自动', '工作总结', '自动 / 未指定']]
  const result = parseBatchExcelRows(rows, { ...defaults, scene: '专题汇报' }, { ...options, genreCatalog: catalog, scene: ['年度工作总结', '专题汇报'] })
  assert.equal(result.errors.length, 0)
  assert.deepEqual(result.tasks.map(task => task.scene), ['年度工作总结', '专题汇报', ''])
})

test('旧九列兼容导入使用后端安全映射并保留歧义值', () => {
  const result = parseBatchExcelRows(realStructureRows, defaults, { ...options, genreCatalog: catalog })
  assert.equal(result.tasks.length, 10)
  assert.equal(result.tasks[0].documentType, '讲话稿')
  assert.equal(result.tasks[0].scene, '党课讲稿')
  assert.equal(result.tasks[2].documentType, '讲话稿')
  assert.equal(result.tasks[4].documentType, '汇报材料')
  assert.equal(result.tasks[8].documentType, '调研报告')
  assert.equal(result.tasks[8].scene, '')
})

test('无场景列才继承默认场景，非法场景明确报错', () => {
  const inherited = parseBatchExcelRows([['标题'], ['任务']], { ...defaults, scene: '专题汇报' }, { ...options, genreCatalog: catalog })
  assert.equal(inherited.tasks[0].scene, '专题汇报')
  const rejected = parseBatchExcelRows([['标题', '场景'], ['任务', '不存在的场景']], defaults, { ...options, genreCatalog: catalog, scene: ['专题汇报'] })
  assert.equal(rejected.tasks.length, 0)
  assert.match(rejected.errors[0].reason, /不是系统有效选项/)
})

test('真实内存XLSX读写往返保留场景列及独立任务值', () => {
  const sheet = XLSX.utils.aoa_to_sheet([['标题', '文种', '写作场景'], ['任务甲', '工作总结', '专题汇报'], ['任务乙', '工作总结', '自动 / 未指定']])
  const workbook = XLSX.utils.book_new()
  XLSX.utils.book_append_sheet(workbook, sheet, '任务')
  const bytes = XLSX.write(workbook, { type: 'buffer', bookType: 'xlsx' })
  const restored = XLSX.read(bytes, { type: 'buffer' })
  const rows = XLSX.utils.sheet_to_json(restored.Sheets['任务'], { header: 1, defval: '' })
  const result = parseBatchExcelRows(rows, defaults, { ...options, genreCatalog: catalog, scene: ['专题汇报'] })
  assert.equal(result.errors.length, 0)
  assert.deepEqual(result.tasks.map(task => [task.title, task.scene]), [['任务甲', '专题汇报'], ['任务乙', '']])
})

test('Excel场景进入正式payload，空白和auto单元格均清空非空默认场景', () => {
  const rows = [['标题', '文种', '写作场景'], ['手选', '工作总结', '专题汇报'], ['空白', '工作总结', '  '], ['自动', '工作总结', 'auto']]
  const result = parseBatchExcelRows(rows, { ...defaults, scene: '年度工作总结' }, { ...options, genreCatalog: catalog, scene: ['专题汇报'] })
  const payloads = result.tasks.map(task => buildTaskGenerationPayload({ title: task.title, document_type: task.documentType, scene: task.scene }, {}, catalog, businessDomains))
  assert.deepEqual(payloads.map(task => task.scene), ['专题汇报', null, null])
  assert.ok(payloads.every(task => task.generation_route === 'full_article_drafting'))
})

test('Excel安全映射保留手选场景优先，歧义旧文种保留显示但正式请求拒绝', () => {
  const rows = [['标题', '文种', '场景'], ['旧党课', '党课讲稿', '动员讲话'], ['旧调研', '调研报告', '专题汇报']]
  const result = parseBatchExcelRows(rows, defaults, { ...options, genreCatalog: catalog, scene: ['动员讲话', '专题汇报'] })
  assert.equal(result.tasks[0].documentType, '讲话稿')
  assert.equal(result.tasks[0].scene, '动员讲话')
  assert.equal(result.tasks[1].documentType, '调研报告')
  assert.throws(() => buildTaskGenerationPayload({ document_type: result.tasks[1].documentType, scene: result.tasks[1].scene }, {}, catalog), /请选择支持的标准文种/)
})
