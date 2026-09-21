// Offsets are Unicode code points from the backend, never evidence scope IDs.
export function isFullArticle(article) {
  return typeof article?.article_markdown === 'string' || article?.content_projection?.contract === 'full-article/v1'
}

export function articleMarkdown(article) {
  return typeof article?.article_markdown === 'string' ? article.article_markdown : article?.content || ''
}

// Read-only positions derived from the current body. Never serialize these back.
export function markdownDisplayBlocks(markdown) {
  let start = 0
  const blocks = []
  for (const line of markdown.match(/[^\r\n]*(?:\r\n|\r|\n|$)/gu) || []) {
    if (!line) continue
    const raw = line.replace(/[\r\n]+$/u, '')
    const prefix = raw.match(/^(#{1,6})[ \t]+/u)
    if (raw.trim()) blocks.push({ block_id: `B${String(blocks.length + 1).padStart(4, '0')}`,
      start, end: start + Array.from(raw).length,
      text_start: start + (prefix ? Array.from(prefix[0]).length : 0),
      level: prefix ? prefix[1].length : 0, text: prefix ? raw.slice(prefix[0].length) : raw })
    start += Array.from(line).length
  }
  return blocks
}

export function replaceArticleRange(markdown, block, text) {
  const chars = Array.from(markdown)
  return chars.slice(0, block.text_start).join('') + text + chars.slice(block.end).join('')
}

export function editedFullArticle(base, markdown) {
  if (markdown === articleMarkdown(base)) return base
  return { ...base, article_markdown: markdown, content: markdown,
    title: markdownDisplayBlocks(markdown).find(b => b.level === 1)?.text || '',
    article_version: (base.article_version || 1) + 1,
    parent_revision_id: base.article_revision_id || '', article_revision_id: '', article_sha256: '',
    content_projection: null, save_path: '', docx_path: '', risk_items: [], delivery_decision: null,
    full_article_check_status: { fact: 'STALE', quality: 'STALE', review: 'REVIEW_REQUIRED', required_checks_complete: false },
    length_gate_status: 'STALE', structure_gate_status: 'STALE',
    delivery_status: 'SUCCESS_WITH_REVIEW', official_success: false,
    article_qualified: false, user_confirmation_required: true }
}

export function fullArticleBlocks(article) {
  if (typeof article?.article_markdown === 'string') return markdownDisplayBlocks(article.article_markdown)
  const projection = article?.content_projection
  if (projection?.contract !== 'full-article/v1') return null
  const chars = Array.from(article.content || '')
  const rows = projection.blocks
  if (!Array.isArray(rows) || !rows.length) throw new Error('全文展示索引不可用')
  let end = 0
  const blocks = rows.map(block => {
    if (!Number.isInteger(block.start) || !Number.isInteger(block.end)
      || !Number.isInteger(block.text_start) || !Number.isInteger(block.level)
      || block.start < end || block.text_start < block.start || block.end > chars.length
      || block.text_start > block.end || block.level < 0 || block.level > 6
      || chars.slice(end, block.start).join('').trim()) throw new Error('全文展示索引与正文不一致')
    end = block.end
    const prefix = chars.slice(block.start, block.text_start).join('')
    if (block.level ? !new RegExp(`^#{${block.level}}[ \\t]+$`).test(prefix) : prefix !== '') {
      throw new Error('全文标题索引与正文不一致')
    }
    return { ...block, text: chars.slice(block.text_start, block.end).join('') }
  })
  if (chars.slice(end).join('').trim()) throw new Error('全文展示索引不完整')
  return blocks
}

export function fullArticleCopyText(article) {
  if (typeof article?.article_markdown === 'string') return article.article_markdown
  if (!fullArticleBlocks(article)) return null
  return article.content
}

export async function verifyFullArticleContent(article) {
  if (typeof article?.article_markdown === 'string') {
    if (!article.article_sha256) return article.full_article_check_status?.fact === 'STALE'
    const digest = await globalThis.crypto.subtle.digest('SHA-256', new TextEncoder().encode(article.article_markdown))
    return Array.from(new Uint8Array(digest), n => n.toString(16).padStart(2, '0')).join('') === article.article_sha256
  }
  if (article?.content_projection?.contract !== 'full-article/v1') return true
  fullArticleBlocks(article)
  const bytes = new TextEncoder().encode(article.content)
  const digest = await globalThis.crypto.subtle.digest('SHA-256', bytes)
  const hash = Array.from(new Uint8Array(digest), n => n.toString(16).padStart(2, '0')).join('')
  return hash === article.content_projection.content_sha256
}
