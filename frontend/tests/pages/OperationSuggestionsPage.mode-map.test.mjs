import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'

const pageSource = readFileSync(new URL('../../src/pages/OperationSuggestionsPage.jsx', import.meta.url), 'utf8')
const backendSource = readFileSync(new URL('../../../backend/app/services/operation/topic_suggestion_service.py', import.meta.url), 'utf8')
const generationPage = readFileSync(new URL('../../src/pages/ArticleGenPage.jsx', import.meta.url), 'utf8')
const requestContract = readFileSync(new URL('../../../backend/app/models/generation.py', import.meta.url), 'utf8')
const mapLiteral = pageSource.match(/const MODE_MAP = (\{[\s\S]*?\n\})/)[1]
const modeMap = runInNewContext(`(${mapLiteral})`)
const handlerBody = pageSource.match(/const handleBringToArticleGen = \(item\) => \{([\s\S]*?)\n  \}/)[1]

function bringToArticleGen(recommendedMode) {
  let result
  runInNewContext(handlerBody, {
    MODE_MAP: modeMap,
    item: { topic: '合成测试选题', recommended_mode: recommendedMode },
    onUseTopicSuggestion: value => { result = JSON.parse(JSON.stringify(value)) },
  })
  return result
}

test('公文初稿 has exactly one mapping, including quoted and unquoted keys', () => {
  const keys = [...mapLiteral.matchAll(/^\s*(?:'([^']+)'|"([^"]+)"|([^\s:]+))\s*:/gm)]
    .map(match => match[1] || match[2] || match[3])
  assert.deepEqual(keys, ['公文初稿'])
  assert.equal(new Set(keys).size, keys.length)
  assert.equal(modeMap['公文初稿'], 'official_draft')
})

test('backend recommended_mode contract reaches the current official draft mode', () => {
  // Read the actual backend response expression rather than inventing a label.
  const returnedDefault = backendSource.match(/"recommended_mode":\s*str\(rule\.get\("recommended_mode",\s*"([^"]+)"\)\)/)?.[1]
  assert.equal(returnedDefault, '公文初稿')
  assert.match(generationPage, /setArticleMode\('official_draft'\)/)
  assert.match(requestContract, /self\.article_mode != "official_draft"/)
  assert.deepEqual(bringToArticleGen(returnedDefault), {
    title: '合成测试选题', count: 1, scene: '', articleMode: 'official_draft',
    targetWords: 1800, exportFormat: 'docx',
  })
})

test('all other mode inputs retain the existing fallback and payload', () => {
  for (const mode of ['full_article', 'draft_article', 'official_draft', 'document_mode', 'appreciation', '未知模式', '', null, undefined]) {
    assert.deepEqual(bringToArticleGen(mode), {
      title: '合成测试选题', count: 1, scene: '', articleMode: 'draft_article',
      targetWords: 1200, exportFormat: 'docx',
    })
  }
})
