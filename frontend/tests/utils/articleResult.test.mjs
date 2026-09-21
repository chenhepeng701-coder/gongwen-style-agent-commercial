import test from 'node:test'
import assert from 'node:assert/strict'
import {
  buildArticleBlocks,
  groupReferenceSources,
  serializeArticleBlocks,
} from '../../src/utils/articleResult.js'

test('merges sentence fragments into natural paragraphs and preserves headings', () => {
  const blocks = buildArticleBlocks([
    '一、坚持关口前移',
    '主要负责人亲自带队开展隐患',
    '排查整治，是压实责任的关键动作。',
    '从业人员安全意识得到有效',
    '提升，是安全基础扎实的体现。',
    '',
    '二、提升监管效能',
    '完整自然段正常显示。',
  ].join('\n'))

  assert.deepEqual(blocks.map(block => [block.type, block.text]), [
    ['heading', '一、坚持关口前移'],
    ['paragraph', '主要负责人亲自带队开展隐患排查整治，是压实责任的关键动作。'],
    ['paragraph', '从业人员安全意识得到有效提升，是安全基础扎实的体现。'],
    ['heading', '二、提升监管效能'],
    ['paragraph', '完整自然段正常显示。'],
  ])
  assert.equal(serializeArticleBlocks(blocks), '一、坚持关口前移\n主要负责人亲自带队开展隐患排查整治，是压实责任的关键动作。\n从业人员安全意识得到有效提升，是安全基础扎实的体现。\n\n二、提升监管效能\n完整自然段正常显示。')
})

test('deduplicates five chunk hits from one document and keeps citation count', () => {
  const sources = Array.from({ length: 5 }, (_, index) => ({
    document_id: 'doc-1',
    reference_title: '同一资料',
    reference_snippet: `片段 ${index + 1}`,
  }))

  const grouped = groupReferenceSources(sources)

  assert.equal(grouped.length, 1)
  assert.equal(grouped[0].citation_count, 5)
  assert.equal(grouped[0].snippets.length, 5)
})

test('keeps same-title documents separate when document ids differ', () => {
  const grouped = groupReferenceSources([
    { document_id: 'doc-1', reference_title: '同名资料' },
    { document_id: 'doc-2', reference_title: '同名资料' },
  ])

  assert.equal(grouped.length, 2)
})

test('uses normalized-title fallback and tolerates empty titles', () => {
  const grouped = groupReferenceSources([
    { reference_title: '  测试   资料  ', reference_filename: 'test.docx' },
    { reference_title: '测试 资料', reference_filename: 'test.docx' },
    {},
  ])

  assert.equal(grouped.length, 2)
  assert.equal(grouped[0].citation_count, 2)
  assert.equal(grouped[1].title, '未命名资料')
})
