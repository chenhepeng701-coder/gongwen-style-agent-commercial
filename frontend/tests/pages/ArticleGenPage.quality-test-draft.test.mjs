import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

const __dirname = dirname(fileURLToPath(import.meta.url))
const pageSource = readFileSync(
  resolve(__dirname, '../../src/pages/ArticleGenPage.jsx'),
  'utf8',
)

test('quality test draft is rendered as a non-success preview with external risk UI', () => {
  assert.match(pageSource, /delivery_status === 'QUALITY_TEST_DRAFT'/)
  assert.match(pageSource, /official_success === false/)
  assert.match(
    pageSource,
    /质量测试稿｜存在 \{qualityTestIssueCount\(art\)\} 处事实缺口或风险｜当前不作为正式交付稿/,
  )
  assert.match(pageSource, /data-testid="quality-test-risk-details"/)
  assert.match(pageSource, /目标字数：\{art\.target_word_count\}/)
  assert.match(pageSource, /实际字数：\{art\.word_count\}/)
  assert.match(pageSource, /结构状态：\{structureGateLabel\(art\.structure_gate_status\)\}/)
  assert.match(pageSource, /事实风险：\{art\.fact_risk_count\}/)
})

test('quality test draft keeps body separate and disables formal export actions', () => {
  const statusIndex = pageSource.indexOf('data-testid="quality-test-draft-status"')
  const bodyIndex = pageSource.indexOf('<div className="gen-meta-tag">正文内容</div>')
  assert.ok(statusIndex >= 0)
  assert.ok(bodyIndex > statusIndex)
  assert.match(pageSource, /!isQualityTestDraft\(art\) && \(\s*<div className="gen-result-actions">/)
  assert.match(pageSource, /entry\.status === 'QUALITY_TEST_DRAFT'\s*\? '质量测试稿'/)
  assert.doesNotMatch(
    pageSource.slice(
      pageSource.indexOf('data-testid="quality-test-draft-status"'),
      pageSource.indexOf('<div className="gen-result-header">'),
    ),
    /art\.content/,
  )
})
