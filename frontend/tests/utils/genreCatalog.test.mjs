import test from 'node:test'
import assert from 'node:assert/strict'
import {
  loadWritingCatalogs, normalizeScene, normalizeTaskGenre, normalizeUiTaskGenre,
  normalizeRestoredTaskGenre, productionTaskError, readGenreCatalog, readSceneTaxonomy,
} from '../../src/utils/genreCatalog.js'
import { catalog, readyGenre } from '../fixtures/genreCatalog.mjs'
import { buildTaskGenerationPayload } from '../../src/utils/batchTaskParams.js'

test('loads backend response without replacing it with frontend catalog or classification', async () => {
  const scenes = { items: [{ name: '服务端场景', keywords: ['服务端'] }] }
  const result = await loadWritingCatalogs(async () => catalog, async () => scenes)
  assert.equal(result.catalog, catalog)
  assert.equal(result.scenes, scenes.items)
})

test('failed or malformed catalog/taxonomy load fails closed with no fallback', async () => {
  await assert.rejects(loadWritingCatalogs(async () => { throw new Error('offline') }, async () => ({ items: [] })), /offline/)
  await assert.rejects(loadWritingCatalogs(async () => catalog, async () => { throw new Error('taxonomy offline') }), /taxonomy offline/)
  for (const response of [null, {}, { genres: [] }, { genres: [readyGenre('x', 'x')] }]) {
    assert.throws(() => readGenreCatalog(response), /目录暂不可用/)
  }
  assert.throws(() => readGenreCatalog({ ...catalog, genres: [{ ...readyGenre('x', 'x'), generation_route: 'current' }] }), /目录暂不可用/)
  assert.throws(() => readSceneTaxonomy({ items: [{}] }), /场景暂不可用/)
  assert.match(productionTaskError({ document_type: '工作总结' }, null), /尚未加载成功/)
})

test('legacy mappings are consumed from backend data, preserving explicit scene priority', () => {
  for (const mapping of catalog.legacy_mappings) {
    const result = normalizeTaskGenre({ document_type: mapping.legacy_value }, catalog)
    assert.equal(result.document_type, mapping.document_type)
    assert.equal(result.scene, mapping.scene)
  }
  assert.equal(normalizeTaskGenre({ document_type: '党课讲稿', scene: '动员讲话' }, catalog).scene, '动员讲话')
  assert.equal(normalizeTaskGenre({ document_type: 'work_summary' }, catalog).document_type, '工作总结')
})

test('ambiguous historical types remain readable but cannot create production requests', () => {
  for (const document_type of ['述职报告', '调研报告', '预案', '心得体会 / 研讨发言', '其他']) {
    const result = normalizeTaskGenre({ document_type, scene: '历史场景' }, catalog)
    assert.equal(result.document_type, document_type)
    assert.equal(result.scene, '历史场景')
    assert.match(productionTaskError(result, catalog), /请选择支持的标准文种/)
  }
})

test('auto sentinels remain null without inferring a scene from title', () => {
  for (const scene of [undefined, null, '', 'auto', '自动', '未指定', '自动识别场景', '自动/未指定', '自动 / 未指定']) {
    assert.equal(normalizeScene(scene), null)
    assert.equal(normalizeTaskGenre({ document_type: '工作总结', title: '民主生活会对照检查', scene }, catalog).scene, null)
  }
})

test('delayed catalog migrates both restored row and batch defaults without mutating history', () => {
  const historical = { documentType: '党课讲稿', scene: '' }
  const pending = normalizeUiTaskGenre(historical, null)
  assert.equal(pending.documentType, '党课讲稿')
  const defaults = normalizeUiTaskGenre(pending, catalog)
  const row = normalizeUiTaskGenre({ ...pending, title: '历史标题' }, catalog)
  assert.equal(defaults.documentType, '讲话稿')
  assert.equal(defaults.scene, '党课讲稿')
  assert.equal(row.scene, defaults.scene)
  assert.deepEqual(historical, { documentType: '党课讲稿', scene: '' })
  assert.equal(normalizeUiTaskGenre({ ...pending, scene: '明确手选场景' }, catalog).scene, '明确手选场景')
})

test('restored genre-as-scene logs stay automatic while real taxonomy scenes survive', () => {
  const scenes = [{ name: '党课讲稿' }, { name: '工作计划' }, { name: '年度工作总结' }]
  const oldSummary = { documentType: '工作总结', scene: '工作总结' }
  assert.equal(normalizeRestoredTaskGenre(oldSummary, catalog, scenes).scene, '')
  const waiting = normalizeRestoredTaskGenre(oldSummary, null, undefined)
  assert.equal(normalizeRestoredTaskGenre(waiting, catalog, scenes).scene, '')
  assert.equal(normalizeRestoredTaskGenre({ documentType: '党课讲稿', scene: '党课讲稿' }, catalog, scenes).scene, '党课讲稿')
  assert.equal(normalizeRestoredTaskGenre({ documentType: '工作计划', scene: '工作计划' }, catalog, scenes).scene, '工作计划')
  assert.equal(normalizeRestoredTaskGenre({ documentType: '工作总结', scene: '历史手选场景' }, catalog, scenes).scene, '历史手选场景')
  assert.equal(oldSummary.scene, '工作总结')
})

test('scene-only summary history restores auto without mutation and can rerun after genre selection', () => {
  const historical = Object.freeze({ documentType: '', scene: '工作总结', requiresGenreSelection: true })
  const restored = normalizeRestoredTaskGenre(historical, catalog, [{ name: '年度工作总结' }])
  assert.equal(restored.scene, '')
  assert.equal(restored.documentType, '')
  assert.equal(restored.requiresGenreSelection, true)
  assert.deepEqual(historical, { documentType: '', scene: '工作总结', requiresGenreSelection: true })
  const payload = buildTaskGenerationPayload({
    document_type: '工作总结', scene: restored.scene, title: '历史任务重跑', count: 1,
  }, {}, catalog)
  assert.equal(payload.scene, null)
  assert.equal(payload.document_type, '工作总结')
})

test('scene-only legacy genres and catalog identities restore auto without a matching document type', () => {
  const scenes = [{ name: '党课讲稿' }, { name: '组织生活会' }, { name: '民主生活会对照检查' }]
  const identities = [
    ...catalog.genres.flatMap(item => [item.document_type, item.genre_id, ...item.aliases]),
    ...catalog.legacy_mappings.map(item => item.legacy_value),
  ].filter(value => !scenes.some(item => item.name === value))
  assert.ok(identities.includes('领导讲话稿'))
  for (const scene of identities) {
    for (const documentType of ['', '工作总结']) {
      const historical = Object.freeze({ documentType, scene })
      assert.equal(normalizeRestoredTaskGenre(historical, catalog, scenes).scene, '', scene)
      assert.deepEqual(historical, { documentType, scene })
    }
  }
  // Exercise aliases independently of legacy_mappings, using the same catalog metadata.
  const withoutLegacy = { ...catalog, legacy_mappings: [] }
  for (const scene of catalog.genres.flatMap(item => item.aliases)) {
    assert.equal(normalizeRestoredTaskGenre({ documentType: '', scene }, withoutLegacy, scenes).scene, '')
  }
})

test('live taxonomy takes priority over genre cleanup for scene-only history', () => {
  const scenes = ['党课讲稿', '工作计划', '工作总结', '领导讲话稿', '实施方案'].map(name => ({ name }))
  for (const { name: scene } of scenes) {
    const historical = Object.freeze({ documentType: '', scene })
    assert.equal(normalizeRestoredTaskGenre(historical, catalog, scenes).scene, scene)
    assert.deepEqual(historical, { documentType: '', scene })
  }
})

test('genre-as-scene cleanup waits for both catalog and taxonomy without mutating delayed history', () => {
  const scenes = [{ name: '党课讲稿' }, { name: '工作计划' }]
  for (const scene of ['工作总结', '领导讲话稿']) {
    for (const documentType of ['', scene]) {
      const historical = Object.freeze({ documentType, scene })
      for (const [pendingCatalog, pendingScenes] of [[null, undefined], [catalog, undefined], [null, scenes]]) {
        const pending = normalizeRestoredTaskGenre(historical, pendingCatalog, pendingScenes)
        assert.equal(pending.scene, scene)
        assert.equal(normalizeRestoredTaskGenre(pending, catalog, scenes).scene, '')
        assert.deepEqual(historical, { documentType, scene })
      }
    }
  }
})

test('unknown historical scenes remain unchanged even when matching an unknown document type', () => {
  for (const documentType of ['', '工作总结', '历史手选场景']) {
    const historical = Object.freeze({ documentType, scene: '历史手选场景' })
    const restored = normalizeRestoredTaskGenre(historical, catalog, [{ name: '党课讲稿' }])
    assert.equal(restored.scene, '历史手选场景')
    assert.deepEqual(historical, { documentType, scene: '历史手选场景' })
  }
})

test('restored scene auto sentinels are normalized before catalog cleanup', () => {
  for (const scene of [undefined, null, '', 'auto', '自动', '未指定', '自动识别场景', '自动/未指定', '自动 / 未指定']) {
    assert.equal(normalizeRestoredTaskGenre({ documentType: '', scene }, catalog, []).scene, '')
  }
})
