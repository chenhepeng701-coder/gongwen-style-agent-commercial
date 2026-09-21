const PUBLISH_SECTION_ORDER = [
  ['public_title', '标题'],
  ['writing_tips', '写作提示'],
  ['applicable_scenes', '适用场景'],
  ['learning_points', '重点学习位置'],
  ['title', '正文标题'],
  ['content', '正文内容'],
  ['review_notes', '写法点评'],
]

const PLAIN_SECTION_ORDER = [
  ['public_title', '标题'],
  ['title', '正文标题'],
  ['content', '正文内容'],
]

const CHINESE_NUMBERS = ['一', '二', '三', '四', '五']
const EMPTY_SENTINELS = new Set(['undefined', 'null', '[]', '{}'])

function isBlankValue(value) {
  if (value === undefined || value === null) return true
  if (Array.isArray(value)) return value.filter(item => !isBlankValue(item)).length === 0
  if (typeof value === 'object') return true
  return EMPTY_SENTINELS.has(String(value).trim()) || String(value).trim() === ''
}

function normalizeValue(value) {
  if (Array.isArray(value)) {
    const items = value
      .filter(item => !isBlankValue(item))
      .map(item => String(item))
    return items.length > 0 ? items : null
  }
  if (isBlankValue(value)) return null
  return String(value)
}

export function formatDocumentModeDraftValue(value) {
  if (Array.isArray(value)) {
    return value.map((item, index) => `${index + 1}. ${item}`).join('\n')
  }
  return value
}

export function getDocumentModeDraftSections(article = {}) {
  if (article.article_mode === 'document_mode') {
    const appreciation = article.appreciation || {}
    const segments = Array.isArray(appreciation.selected_segments) ? appreciation.selected_segments : []
    if (appreciation.appreciation_scope === 'multi_horizontal') {
      const sections = [
        { label: '标题', content: normalizeValue(article.public_title || appreciation.public_title) },
        { label: '写法参考导语', content: normalizeValue(appreciation.appreciation_intro) },
        { label: '横向框架解析', content: normalizeValue(appreciation.horizontal_framework_analysis || appreciation.framework_analysis) },
      ]

      segments.forEach((segment, index) => {
        const number = CHINESE_NUMBERS[index] || String(index + 1)
        sections.push(
          { label: `材料${number}`, content: normalizeValue(segment?.material_label || `材料${number}`) },
          { label: '来源标题', content: normalizeValue(segment?.source_title) },
          { label: '这篇最值得学的地方', content: normalizeValue(segment?.best_learning_point) },
          { label: '精华片段', content: normalizeValue(segment?.excerpt) },
          { label: '关键句', content: normalizeValue(segment?.key_sentence) },
          { label: '拆句点评', content: normalizeValue(segment?.comment) },
          { label: '可借鉴词句', content: normalizeValue(segment?.phrases) },
          { label: '可替换表达', content: normalizeValue(segment?.rewrite) },
          { label: '适用场景', content: normalizeValue(segment?.usage || segment?.usage_tip) },
        )
      })

      sections.push(
        { label: '共同写法总结', content: normalizeValue(appreciation.common_writing_summary) },
        { label: '写作提醒', content: normalizeValue(appreciation.writing_reminder) },
        { label: '资料来源标题', content: normalizeValue(appreciation.source_titles) },
      )

      return sections.filter(section => section.content !== null)
    }

    const sections = [
      { label: '标题', content: normalizeValue(article.public_title || appreciation.public_title) },
      { label: '写法参考导语', content: normalizeValue(appreciation.appreciation_intro) },
      { label: '材料框架解析', content: normalizeValue(appreciation.framework_analysis) },
    ]

    segments.forEach((segment, index) => {
      const number = CHINESE_NUMBERS[index] || String(index + 1)
      sections.push(
        { label: `精华片段${number}`, content: normalizeValue(segment?.excerpt) },
        { label: '关键句', content: normalizeValue(segment?.key_sentence) },
        { label: '拆句点评', content: normalizeValue(segment?.comment) },
        { label: '可借鉴词句', content: normalizeValue(segment?.phrases) },
        { label: '可替换表达', content: normalizeValue(segment?.rewrite) },
        { label: '适用场景', content: normalizeValue(segment?.usage || segment?.usage_tip) },
        { label: '不要照抄提醒', content: normalizeValue(segment?.copy_warning) },
      )
    })

    sections.push(
      { label: '写作提醒', content: normalizeValue(appreciation.writing_reminder) },
      { label: '资料来源标题', content: normalizeValue(appreciation.source_titles) },
    )

    return sections.filter(section => section.content !== null)
  }

  const order = article.analysis_wrapper_enabled ? PUBLISH_SECTION_ORDER : PLAIN_SECTION_ORDER
  return order
    .map(([field, label]) => ({ label, content: normalizeValue(article[field]) }))
    .filter(section => section.content !== null)
}

export function buildDocumentModeDraftText(article = {}) {
  return getDocumentModeDraftSections(article)
    .map(section => `${section.label}：\n${formatDocumentModeDraftValue(section.content)}`)
    .join('\n\n')
}

export function getDocumentModeDraftFilename(title) {
  const safeTitle = String(title || '').trim().replace(/[\\/:*?"<>|]/g, '_') || '未命名'
  return `Word导出_${safeTitle}.docx`
}
