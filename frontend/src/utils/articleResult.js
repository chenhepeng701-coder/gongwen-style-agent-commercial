const HEADING_PATTERN = /^(?:[一二三四五六七八九十]+、|\d+[.、])/u
const SENTENCE_TERMINAL_PATTERN = /[。！？；!?]$/u

export function isArticleHeading(text) {
  return HEADING_PATTERN.test(String(text || '').trim())
}

export function buildArticleBlocks(content) {
  const blocks = []
  let paragraphText = ''
  let paragraphIndex = 0
  let headingIndex = 0

  const flushParagraph = () => {
    const text = paragraphText.trim()
    if (!text) return
    blocks.push({
      id: `paragraph-${paragraphIndex}`,
      type: 'paragraph',
      paragraphIndex,
      text,
    })
    paragraphIndex += 1
    paragraphText = ''
  }

  String(content || '').split(/\r?\n/u).forEach((rawLine) => {
    const line = rawLine.trim()
    if (!line) {
      flushParagraph()
      return
    }
    if (isArticleHeading(line)) {
      flushParagraph()
      blocks.push({
        id: `heading-${headingIndex}`,
        type: 'heading',
        headingIndex,
        text: line,
      })
      headingIndex += 1
      return
    }
    if (paragraphText && SENTENCE_TERMINAL_PATTERN.test(paragraphText)) {
      flushParagraph()
    }
    paragraphText += line
    if (SENTENCE_TERMINAL_PATTERN.test(line)) flushParagraph()
  })
  flushParagraph()
  return blocks
}

export function serializeArticleBlocks(blocks) {
  const lines = []
  ;(blocks || []).forEach((block) => {
    const text = String(block?.text || '').trim()
    if (!text) return
    if (block.type === 'heading' && lines.length > 0) lines.push('')
    lines.push(text)
  })
  return lines.join('\n')
}

function normalizedTitle(value) {
  return String(value || '').trim().replace(/\s+/gu, ' ').toLocaleLowerCase('zh-CN')
}

function sourceTitle(source) {
  return String(source?.title || source?.reference_title || source?.filename || source?.reference_filename || '').trim() || '未命名资料'
}

function sourceFilename(source) {
  return String(source?.filename || source?.reference_filename || '').trim()
}

export function groupReferenceSources(referenceSources) {
  const groups = new Map()
  ;(Array.isArray(referenceSources) ? referenceSources : []).forEach((source, index) => {
    const documentId = String(source?.document_id || source?.doc_id || '').trim()
    const stableId = String(source?.source_id || source?.reference_id || '').trim()
    const title = sourceTitle(source)
    const filename = sourceFilename(source)
    const fallbackKey = `${normalizedTitle(title)}|${normalizedTitle(filename)}`
    const key = documentId ? `document:${documentId}` : stableId ? `source:${stableId}` : `title:${fallbackKey || index}`
    const existing = groups.get(key)
    const snippet = String(source?.reference_snippet || source?.snippet || '').trim()
    if (existing) {
      existing.citation_count += 1
      if (snippet && !existing.snippets.includes(snippet)) existing.snippets.push(snippet)
      return
    }
    groups.set(key, {
      key,
      document_id: documentId,
      title,
      filename,
      file_type: String(source?.file_type || '').trim(),
      citation_count: Number.isFinite(source?.citation_count) ? Number(source.citation_count) : 1,
      source_available: Boolean(documentId),
      snippets: snippet ? [snippet] : [],
      citations: Array.isArray(source?.citations) ? source.citations : [],
    })
  })
  return Array.from(groups.values())
}

export function getParagraphSourceMap(traceability) {
  const map = new Map()
  ;(traceability?.paragraph_sources || []).forEach((item) => {
    if (!Number.isInteger(item?.paragraph_index)) return
    map.set(item.paragraph_index, Array.isArray(item.sources) ? item.sources : [])
  })
  return map
}
