import test from 'node:test'
import assert from 'node:assert/strict'

import {
  applyDefaultsToTask,
  buildBatchTask,
  buildTaskGenerationPayload,
  referenceModeFromScope,
  referenceScopeFromMode,
} from '../../src/utils/batchTaskParams.js'

const defaults = {
  document_type: '党课讲稿',
  level: '乡镇 / 街道',
  org_type: '党政机关',
  business_domain: '作风建设',
  writing_purpose: '学习研讨',
  target_length: 1200,
  reference_mode: 'auto',
}

test('new task inherits a snapshot of current defaults', () => {
  const mutableDefaults = { ...defaults }
  const task = buildBatchTask(mutableDefaults, { title: '基层干部作风建设专题党课' })
  mutableDefaults.business_domain = '安全生产'

  assert.equal(task.business_domain, '作风建设')
  assert.equal(task.target_length, 1200)
  assert.equal(task.reference_mode, 'auto')
})

test('apply defaults preserves task identity and title', () => {
  const task = {
    ...buildBatchTask(defaults, { title: '安全生产工作部署会议讲话' }),
    id: 'task-2',
    expanded: true,
    status: '待生成',
  }
  const updated = applyDefaultsToTask(task, {
    ...defaults,
    document_type: '领导讲话稿',
    writing_purpose: '工作部署',
    target_length: 1500,
  })

  assert.equal(updated.id, 'task-2')
  assert.equal(updated.title, '安全生产工作部署会议讲话')
  assert.equal(updated.document_type, '领导讲话稿')
  assert.equal(updated.target_length, 1500)
})

test('generation payload uses each task parameters instead of shared defaults', () => {
  const task1 = buildBatchTask(defaults, { title: '基层干部作风建设专题党课' })
  const task2 = buildBatchTask(defaults, {
    title: '安全生产工作部署会议讲话',
    document_type: '领导讲话稿',
    business_domain: '安全生产',
    writing_purpose: '工作部署',
    target_length: 1500,
    count: 3,
  })
  const shared = { scene: '', temperature: 0.7, export_format: 'docx' }

  const payload1 = buildTaskGenerationPayload(task1, shared)
  const payload2 = buildTaskGenerationPayload(task2, shared)

  assert.deepEqual(
    [payload1.document_type, payload1.business_domain, payload1.writing_purpose, payload1.target_length],
    ['党课讲稿', '作风建设', '学习研讨', 1200],
  )
  assert.deepEqual(
    [payload2.document_type, payload2.business_domain, payload2.writing_purpose, payload2.target_length],
    ['领导讲话稿', '安全生产', '工作部署', 1500],
  )
  assert.equal(payload1.count, 1)
  assert.equal(payload2.count, 3)
})

test('generation payload preserves explicit replay scene and style values', () => {
  const task = buildBatchTask(defaults, {
    title: 'frozen replay case',
    scene: null,
    style: null,
  })
  const shared = { scene: 'shared scene', temperature: 0.7, export_format: 'txt' }

  const payload = buildTaskGenerationPayload(task, shared)

  assert.equal(payload.scene, null)
  assert.equal(payload.style, null)
  assert.equal(payload.document_type, defaults.document_type)
})

test('generation payload does not inject document type into scene for ordinary tasks', () => {
  const task = buildBatchTask(defaults, { title: 'ordinary task' })
  const shared = { scene: '', temperature: 0.7, export_format: 'txt' }

  const payload = buildTaskGenerationPayload(task, shared)

  assert.equal(payload.document_type, defaults.document_type)
  assert.equal(payload.scene, null)
  assert.equal('style' in payload, false)
})

test('generation payload preserves explicit historical scene value', () => {
  const task = buildBatchTask(defaults, {
    title: 'legacy scene request',
    scene: '历史场景值',
  })
  const shared = { scene: '', temperature: 0.7, export_format: 'txt' }

  const payload = buildTaskGenerationPayload(task, shared)

  assert.equal(payload.scene, '历史场景值')
  assert.equal(payload.document_type, defaults.document_type)
})

test('generation payload ignores shared scene unless task scene is explicit', () => {
  const task = buildBatchTask(defaults, { title: 'ordinary task with shared scene' })
  const shared = { scene: 'shared scene', temperature: 0.7, export_format: 'txt' }

  const payload = buildTaskGenerationPayload(task, shared)

  assert.equal(payload.scene, null)
  assert.equal(payload.document_type, defaults.document_type)
  assert.equal('style' in payload, false)
})

test('reference scope labels map to stable API modes', () => {
  assert.equal(referenceModeFromScope('自动匹配资料库'), 'auto')
  assert.equal(referenceModeFromScope('优先参考搜索结果'), 'search')
  assert.equal(referenceScopeFromMode('recent'), '优先参考最近上传资料')
})

test('single work summary explicitly selects the full article drafting route', () => {
  const payload = buildTaskGenerationPayload({
    title: '2026年安全生产工作总结',
    document_type: '工作总结',
    count: 1,
  })

  assert.equal(payload.article_mode, 'official_draft')
  assert.equal(payload.generation_route, 'full_article_drafting')
  assert.equal(payload.count, 1)
})

test('multiple work summaries explicitly keep the current route', () => {
  for (const count of [2, 3, 10]) {
    const payload = buildTaskGenerationPayload({ document_type: '工作总结', count })

    assert.equal(payload.generation_route, 'current')
    assert.equal(payload.count, count)
  }
})

test('other document types explicitly keep the current route for one article', () => {
  for (const document_type of ['党课讲稿', '领导讲话稿', '工作汇报', '调研报告', '']) {
    const payload = buildTaskGenerationPayload({ document_type, count: 1 })

    assert.equal(payload.generation_route, 'current')
    assert.equal(payload.document_type, document_type)
    assert.equal(payload.count, 1)
  }
})

test('work summary routing uses the same defaulted count as the payload', () => {
  for (const task of [{}, { count: undefined }, { count: null }, { count: 0 }]) {
    const payload = buildTaskGenerationPayload({ document_type: '工作总结', ...task })

    assert.equal(payload.count, 1)
    assert.equal(payload.generation_route, 'full_article_drafting')
  }
})

test('explicit routing preserves all existing generation parameters', () => {
  const payload = buildTaskGenerationPayload({
    title: '  2026年安全生产工作总结  ',
    document_type: '工作总结',
    level: '县区',
    org_type: '党政机关',
    business_domain: '安全生产',
    writing_purpose: '总结复盘',
    target_length: 1800,
    reference_mode: 'recent',
    scene: '历史场景值',
    style: '突出问题和下一步安排',
    count: 1,
  }, { temperature: 0, export_format: 'txt' })

  assert.deepEqual(payload, {
    title: '2026年安全生产工作总结',
    document_type: '工作总结',
    level: '县区',
    org_type: '党政机关',
    business_domain: '安全生产',
    writing_purpose: '总结复盘',
    target_length: 1800,
    reference_mode: 'recent',
    scene: '历史场景值',
    style: '突出问题和下一步安排',
    article_mode: 'official_draft',
    generation_route: 'full_article_drafting',
    temperature: 0,
    count: 1,
    export_format: 'txt',
  })
})
