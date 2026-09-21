import React, { useEffect, useMemo, useRef, useState } from 'react'
import FullArticleEditor from '../components/FullArticleEditor'
import { isFullArticle } from '../utils/fullArticleProjection'
import { getGenerationSourceTraceability } from '../api'
import Button from '../components/Button'
import EmptyState from '../components/EmptyState'
import SourceDocumentDrawer from '../components/SourceDocumentDrawer'
import StatusTag from '../components/StatusTag'
import {
  buildArticleBlocks,
  getParagraphSourceMap,
  groupReferenceSources,
  serializeArticleBlocks,
} from '../utils/articleResult'

function isQualityTestDraft(article) {
  return article?.quality_test_mode === true && article?.delivery_status === 'QUALITY_TEST_DRAFT' && article?.official_success === false
}

function sourceTypeLabel(source) {
  const value = String(source?.file_type || '').replace(/^\./u, '').toUpperCase()
  return value || '本地资料'
}

export default function EditorPage(props) {
  return isFullArticle(props.article) ? <FullArticleEditor {...props} /> : <LegacyEditorPage {...props} />
}

function LegacyEditorPage({ article, exporting, onBack, onCopy, onExport }) {
  const [title, setTitle] = useState('')
  const [blocks, setBlocks] = useState([])
  const [contentEdited, setContentEdited] = useState(false)
  const [traceability, setTraceability] = useState(null)
  const [traceLoading, setTraceLoading] = useState(false)
  const [traceError, setTraceError] = useState('')
  const [activeOutlineId, setActiveOutlineId] = useState('editor-title')
  const [expandedSourceId, setExpandedSourceId] = useState('')
  const [drawerState, setDrawerState] = useState(null)
  const [highlightedParagraph, setHighlightedParagraph] = useState(null)
  const highlightTimerRef = useRef(null)

  useEffect(() => {
    setTitle(article?.title || article?.public_title || '')
    setBlocks(buildArticleBlocks(article?.content || article?.text || ''))
    setContentEdited(false)
    setTraceability(null)
    setTraceError('')
    setDrawerState(null)
  }, [article])

  useEffect(() => {
    const logId = String(article?.log_id || '').trim()
    if (!logId) {
      setTraceLoading(false)
      setTraceability(null)
      return undefined
    }
    let cancelled = false
    setTraceLoading(true)
    setTraceError('')
    getGenerationSourceTraceability(logId)
      .then((response) => {
        if (!cancelled) setTraceability(response)
      })
      .catch((error) => {
        if (!cancelled) {
          setTraceability(null)
          setTraceError(error.message || '来源对应关系暂时无法读取。')
        }
      })
      .finally(() => {
        if (!cancelled) setTraceLoading(false)
      })
    return () => { cancelled = true }
  }, [article?.log_id])

  useEffect(() => () => {
    if (highlightTimerRef.current) window.clearTimeout(highlightTimerRef.current)
  }, [])

  const content = useMemo(() => serializeArticleBlocks(blocks), [blocks])
  const wordCount = content.replace(/\s/gu, '').length
  const headings = useMemo(() => blocks.filter(block => block.type === 'heading'), [blocks])
  const paragraphSourceMap = useMemo(() => getParagraphSourceMap(traceability), [traceability])
  const tracedSources = traceability?.status === 'AVAILABLE' ? traceability.sources || [] : []
  const references = useMemo(
    () => tracedSources.length ? tracedSources : groupReferenceSources(article?.reference_sources),
    [article?.reference_sources, tracedSources],
  )
  const sourceById = useMemo(() => new Map(references.map(source => [source.document_id, source])), [references])
  const qualityTestDraft = isQualityTestDraft(article)
  const hasResult = Boolean(article)
  const exportedArticle = { ...article, title, public_title: '', content: contentEdited ? content : (article?.content || article?.text || '') }
  const copiedArticle = { ...exportedArticle, content }

  useEffect(() => {
    const canvas = document.querySelector('.editor-canvas')
    if (!canvas) return undefined
    const updateActiveOutline = () => {
      const canvasTop = canvas.getBoundingClientRect().top
      let active = 'editor-title'
      headings.forEach((heading) => {
        const node = document.getElementById(`article-${heading.id}`)
        if (node && node.getBoundingClientRect().top <= canvasTop + 120) active = heading.id
      })
      setActiveOutlineId(active)
    }
    updateActiveOutline()
    canvas.addEventListener('scroll', updateActiveOutline, { passive: true })
    return () => canvas.removeEventListener('scroll', updateActiveOutline)
  }, [headings])

  const scrollToBlock = (blockId) => {
    const targetId = blockId === 'editor-title' ? 'editor-title' : `article-${blockId}`
    const target = document.getElementById(targetId)
    target?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    if (blockId === 'editor-title') target?.focus()
    setActiveOutlineId(blockId)
  }

  const updateBlockText = (blockId, text) => {
    setBlocks(current => current.map(block => block.id === blockId ? { ...block, text } : block))
    setContentEdited(true)
  }

  const openSource = (documentId, citationId = '') => {
    const source = sourceById.get(documentId)
    if (!source) return
    setDrawerState({ source, citationId })
  }

  const locateParagraph = (paragraphIndex) => {
    const target = document.querySelector(`[data-paragraph-index="${paragraphIndex}"]`)
    if (!target) return
    target.scrollIntoView({ behavior: 'smooth', block: 'center' })
    setHighlightedParagraph(paragraphIndex)
    setDrawerState(null)
    if (highlightTimerRef.current) window.clearTimeout(highlightTimerRef.current)
    highlightTimerRef.current = window.setTimeout(() => setHighlightedParagraph(null), 2200)
  }

  if (!hasResult) {
    return (
      <div className="editor-page" data-page-key="editor">
        <header className="editor-header">
          <Button variant="secondary" onClick={onBack}>返回</Button>
          <div className="editor-header__title"><strong>文稿编辑</strong><span>当前没有可编辑的真实生成结果</span></div>
          <div className="editor-header__actions"><Button variant="secondary" disabled>复制正文</Button><Button variant="primary" disabled>导出 Word</Button></div>
        </header>
        <main className="editor-empty-result"><section className="core-panel"><EmptyState icon="稿" title="暂无生成结果" description="请先完成一次真实生成，结果返回后即可继续编辑、复制和导出。" actionLabel="返回写文章" onAction={onBack} /></section></main>
      </div>
    )
  }

  return (
    <div className="editor-page" data-page-key="editor">
      <header className="editor-header">
        <Button variant="secondary" onClick={onBack}>返回</Button>
        <div className="editor-header__title">
          <strong>{title || '未命名文稿'}</strong>
          <span>正文可直接阅读、选中和修改；来源核对不会改动资料或生成内容。</span>
        </div>
        <div className="editor-header__actions">
          <Button variant="secondary" onClick={() => onCopy(copiedArticle)}>复制正文</Button>
          <Button variant="primary" loading={exporting} disabled={qualityTestDraft} onClick={() => onExport(exportedArticle)}>导出 Word</Button>
        </div>
      </header>
      <div className="editor-toolbar" role="toolbar" aria-label="文稿编辑工具">
        <StatusTag tone="neutral">正文编辑</StatusTag>
        <span>点击正文即可修改；来源标记仅用于核对，不会进入复制内容或 Word。</span>
        <span className="editor-toolbar__count">{wordCount.toLocaleString()} 字</span>
      </div>

      <main className="editor-workspace">
        <aside className="core-panel editor-outline">
          <h2>文章结构</h2>
          <p>点击标题可定位到正文，滚动时自动高亮当前位置</p>
          <button type="button" title={title || '文章标题'} className={`editor-outline__item is-title ${activeOutlineId === 'editor-title' ? 'is-active' : ''}`} onClick={() => scrollToBlock('editor-title')}>文章标题</button>
          {headings.length > 0 ? headings.map(heading => (
            <button type="button" title={heading.text} className={`editor-outline__item ${activeOutlineId === heading.id ? 'is-active' : ''}`} key={heading.id} onClick={() => scrollToBlock(heading.id)}>{heading.text}</button>
          )) : <div className="editor-outline__empty">正文中暂未识别到一级标题</div>}
        </aside>

        <section className="editor-canvas" aria-label="文稿编辑区">
          <article className="editor-paper">
            <textarea id="editor-title" aria-label="文稿标题" className="editor-title-input" rows="2" value={title} onChange={event => setTitle(event.target.value)} placeholder="当前结果未返回文稿标题" />
            <div className="editor-article-content" aria-label="正文">
              {blocks.map((block) => {
                const sourceRefs = block.type === 'paragraph' ? paragraphSourceMap.get(block.paragraphIndex) || [] : []
                const Tag = block.type === 'heading' ? 'h2' : 'div'
                return (
                  <section
                    id={`article-${block.id}`}
                    className={`editor-article-block editor-article-block--${block.type} ${highlightedParagraph === block.paragraphIndex ? 'is-source-target' : ''}`}
                    data-paragraph-index={block.type === 'paragraph' ? block.paragraphIndex : undefined}
                    key={block.id}
                  >
                    <Tag
                      className="editor-article-block__text"
                      contentEditable
                      suppressContentEditableWarning
                      role="textbox"
                      aria-label={block.type === 'heading' ? '一级标题' : `正文第 ${block.paragraphIndex + 1} 段`}
                      onInput={event => updateBlockText(block.id, event.currentTarget.innerText)}
                    >{block.text}</Tag>
                    {sourceRefs.length > 0 && (
                      <div className="article-source-links" aria-label={`正文第 ${block.paragraphIndex + 1} 段来源`}>
                        {sourceRefs.map((reference, index) => {
                          const source = sourceById.get(reference.document_id)
                          return (
                            <button type="button" key={reference.citation_id || `${reference.document_id}-${index}`} title={source?.title || '查看来源'} onClick={() => openSource(reference.document_id, reference.citation_id)}>
                              {sourceRefs.length > 1 ? `来源 ${index + 1}` : '查看来源'}
                            </button>
                          )
                        })}
                      </div>
                    )}
                  </section>
                )
              })}
            </div>
          </article>
        </section>

        <aside className="core-panel editor-inspector">
          <h2>当前文稿</h2>
          <div className="editor-inspector__tags">
            <StatusTag tone={qualityTestDraft ? 'warning' : 'success'}>{qualityTestDraft ? '质量测试稿' : '真实生成结果'}</StatusTag>
            <StatusTag tone="neutral">{contentEdited ? '已在本页修改' : '原始正文'}</StatusTag>
          </div>
          {qualityTestDraft && <div className="editor-inspector__warning">当前结果存在事实缺口或风险，不提供 Word 导出，请先人工核验。</div>}
          <div className="editor-inspector__section"><strong>结果信息</strong><span>当前字数：{wordCount.toLocaleString()} 字</span>{Number.isFinite(article?.generation_time) && <span>真实生成耗时：{article.generation_time} 秒</span>}</div>
          <div className="editor-inspector__section editor-source-section">
            <div className="editor-source-section__heading"><strong>本次参考资料</strong>{traceLoading && <span>正在核对来源…</span>}</div>
            {references.length > 0 ? references.map((source, index) => (
              <article className="editor-source-card" key={source.document_id || source.key || index}>
                <button type="button" className="editor-source-card__title" title={source.title || source.filename} disabled={!source.document_id} onClick={() => source.document_id && openSource(source.document_id)}>{source.title || source.filename || '未命名资料'}</button>
                <div className="editor-source-card__meta"><span>{sourceTypeLabel(source)}</span><strong>引用 {source.citation_count || 1} 处</strong></div>
                <div className="editor-source-card__actions">
                  <button type="button" disabled={!source.document_id} onClick={() => source.document_id && openSource(source.document_id)}>查看原文</button>
                  <button type="button" disabled={!source.citations?.length} onClick={() => setExpandedSourceId(current => current === source.document_id ? '' : source.document_id)}>{expandedSourceId === source.document_id ? '收起引用位置' : '展开引用位置'}</button>
                </div>
                {expandedSourceId === source.document_id && (
                  <div className="editor-source-card__citations">
                    {(source.citations || []).map((citation, citationIndex) => (
                      <button type="button" key={citation.citation_id || citationIndex} onClick={() => openSource(source.document_id, citation.citation_id)}>
                        <span>引用位置 {citationIndex + 1}</span>
                        <small>{(citation.section_headings || []).join('、') || '对应正文段落'}</small>
                      </button>
                    ))}
                  </div>
                )}
              </article>
            )) : <span>{traceError || traceability?.message || '当前结果未返回可展示的参考资料信息'}</span>}
            {!traceLoading && references.length > 0 && traceability?.status !== 'AVAILABLE' && <p className="editor-source-section__notice">{traceError || traceability?.message || '这篇历史文章没有保存具体引用位置。'}</p>}
          </div>
        </aside>
      </main>

      <SourceDocumentDrawer
        open={Boolean(drawerState)}
        source={drawerState?.source}
        activeCitationId={drawerState?.citationId}
        onClose={() => setDrawerState(null)}
        onLocateParagraph={locateParagraph}
      />
    </div>
  )
}
