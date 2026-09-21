import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'

const pageSource = fs.readFileSync(
  new URL('../../src/pages/ArticleGenPage.jsx', import.meta.url),
  'utf8',
)

test('review-required draft remains visible with copy and export actions', () => {
  assert.match(pageSource, /delivery_status === 'REVIEW_REQUIRED_DRAFT'/)
  assert.match(pageSource, /草稿已生成，其中部分内容建议结合原始资料人工核对。/)
  assert.match(pageSource, /正文可复制、编辑和导出/)
  assert.match(pageSource, /复制正文/)
  assert.match(pageSource, /导出 Word/)
  assert.match(pageSource, /<ReviewRequiredDraftDetails art=\{primaryArticle\} \/>/)
  assert.match(pageSource, /entry\.status === 'REVIEW_REQUIRED_DRAFT'/)
  assert.match(pageSource, /待核对草稿/)
})
