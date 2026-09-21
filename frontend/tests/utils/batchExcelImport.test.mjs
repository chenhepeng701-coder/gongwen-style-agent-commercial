import test from 'node:test'
import assert from 'node:assert/strict'
import { parseBatchExcelRows } from '../../src/utils/batchExcelImport.js'

const defaults = { documentType: '党课讲稿', level: '乡镇 / 街道', orgType: '党政机关', businessDomain: '党建', writingPurpose: '学习研讨', wordCount: 1200, referenceScope: '自动匹配资料库' }
const options = {
  documentType: ['党课讲稿', '领导讲话稿', '工作汇报', '工作总结', '调研报告'],
  level: ['乡镇 / 街道', '县区'], orgType: ['党政机关'],
  businessDomain: ['党建', '纪检/巡察', '作风建设', '安全生产', '基层治理', '生态环保'],
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
    assert.deepEqual(task, { title: source[0], documentType: source[1], level: source[2], orgType: source[3], businessDomain: source[4], writingPurpose: source[5], wordCount: source[6], referenceScope: source[7], count: source[8] })
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
