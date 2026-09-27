import test from 'node:test'
import assert from 'node:assert/strict'
import {
  applyDefaultsToTask, buildBatchTask, buildTaskGenerationPayload,
  referenceModeFromScope, referenceScopeFromMode,
} from '../../src/utils/batchTaskParams.js'
import { catalog, readyGenre } from '../fixtures/genreCatalog.mjs'

const defaults = {
  document_type: '工作总结', scene: '年度工作总结', level: '乡镇 / 街道', org_type: '党政机关',
  business_domain: '作风建设', writing_purpose: '学习研讨', target_length: 1200, reference_mode: 'auto',
}
const payload = (task, shared) => buildTaskGenerationPayload(task, shared, catalog)

test('new task inherits independent scene and task context snapshot', () => {
  const mutableDefaults = { ...defaults }
  const task = buildBatchTask(mutableDefaults, { title: '作风建设总结' })
  mutableDefaults.scene = '其他场景'
  mutableDefaults.business_domain = '安全生产'
  assert.equal(task.scene, '年度工作总结')
  assert.equal(task.business_domain, '作风建设')
  assert.equal(task.target_length, 1200)
  assert.equal(task.reference_mode, 'auto')
})

test('apply defaults preserves identity and title, deliberately replaces scene', () => {
  const task = { ...buildBatchTask(defaults, { title: '安全生产总结' }), id: 'task-2', expanded: true, status: '待生成' }
  const updated = applyDefaultsToTask(task, { ...defaults, scene: '专题汇报', target_length: 1500 })
  assert.equal(updated.id, 'task-2')
  assert.equal(updated.title, task.title)
  assert.equal(updated.scene, '专题汇报')
  assert.equal(updated.target_length, 1500)
})

test('each batch row supplies independent scene and context; shared scene cannot override it', () => {
  const task1 = buildBatchTask(defaults, { title: '作风建设总结', scene: '年度工作总结' })
  const task2 = buildBatchTask(defaults, { title: '安全生产总结', scene: '专题汇报', business_domain: '安全生产', writing_purpose: '工作部署', target_length: 1500 })
  const shared = { scene: 'ignored global scene', temperature: 0.7, export_format: 'docx' }
  assert.deepEqual([payload(task1, shared).scene, payload(task2, shared).scene], ['年度工作总结', '专题汇报'])
  assert.deepEqual([payload(task2, shared).business_domain, payload(task2, shared).writing_purpose, payload(task2, shared).target_length], ['安全生产', '工作部署', 1500])
})

test('explicit automatic row scene clears a nonempty default and retains null style', () => {
  const task = buildBatchTask(defaults, { scene: '', style: null })
  const result = payload(task, { scene: 'shared scene', export_format: 'txt' })
  assert.equal(result.scene, null)
  assert.equal(result.style, null)
})

test('ordinary task adds neither inferred scene nor implicit writing requirements', () => {
  const result = payload({ document_type: '工作总结' }, { scene: 'ignored global scene' })
  assert.equal(result.scene, null)
  assert.equal('style' in result, false)
})

test('explicit historical scene remains readable and is propagated unchanged', () => {
  assert.equal(payload({ ...defaults, scene: '历史场景值' }).scene, '历史场景值')
})

test('reference labels still map to existing stable API modes', () => {
  assert.equal(referenceModeFromScope('自动匹配资料库'), 'auto')
  assert.equal(referenceModeFromScope('优先参考搜索结果'), 'search')
  assert.equal(referenceScopeFromMode('recent'), '优先参考最近上传资料')
})

test('production capability selects formal route and empty scene keeps exact prior payload', () => {
  assert.deepEqual(payload({
    title: '  2026年安全生产工作总结  ', document_type: '工作总结', level: '县区', org_type: '党政机关',
    business_domain: '安全生产', writing_purpose: '总结复盘', target_length: 1800,
    reference_mode: 'recent', scene: '', style: '突出问题和下一步安排', count: 1,
  }, { temperature: 0, export_format: 'txt' }), {
    title: '2026年安全生产工作总结', document_type: '工作总结', level: '县区', org_type: '党政机关',
    business_domain: '安全生产', writing_purpose: '总结复盘', target_length: 1800,
    reference_mode: 'recent', scene: null, style: '突出问题和下一步安排', article_mode: 'official_draft',
    generation_route: 'full_article_drafting', temperature: 0, count: 1, export_format: 'txt',
  })
})

test('count > 1 fails clearly; there is no current route fallback', () => {
  for (const count of [2, 3, 10]) assert.throws(() => payload({ ...defaults, count }), /仅支持生成 1 篇/)
})

test('catalog absence, unknown genre and engineering-ready genres fail closed', () => {
  assert.throws(() => buildTaskGenerationPayload(defaults), /目录尚未加载/)
  for (const document_type of ['', '未知文种', '调研报告']) assert.throws(() => payload({ document_type }), /请选择支持的标准文种/)
  for (const document_type of ['讲话稿', '党课讲稿', '领导讲话稿', '工作汇报']) assert.throws(() => payload({ document_type }), /待商业验收/)
})

test('future promoted synthetic genre uses formal route with no document-name changes', () => {
  const futureCatalog = { genres: [readyGenre('future', '测试未来文种')], legacy_mappings: [] }
  const result = buildTaskGenerationPayload({ document_type: '测试未来文种', scene: '测试场景' }, {}, futureCatalog)
  assert.equal(result.generation_route, 'full_article_drafting')
  assert.equal(result.scene, '测试场景')
  assert.throws(() => buildTaskGenerationPayload({ document_type: '工作总结' }, {}, futureCatalog), /请选择支持/)
})

test('legacy aliases normalize from backend metadata before formal submission', () => {
  const enabledSpeech = { ...catalog, genres: catalog.genres.map(item => item.genre_id === 'speech' ? readyGenre('speech', '讲话稿') : item) }
  const result = buildTaskGenerationPayload({ document_type: '党课讲稿' }, {}, enabledSpeech)
  assert.equal(result.document_type, '讲话稿')
  assert.equal(result.scene, '党课讲稿')
})

test('route checks use same defaulted count as payload', () => {
  for (const task of [{}, { count: undefined }, { count: null }, { count: 0 }]) {
    const result = payload({ document_type: '工作总结', ...task })
    assert.equal(result.count, 1)
    assert.equal(result.generation_route, 'full_article_drafting')
  }
})
