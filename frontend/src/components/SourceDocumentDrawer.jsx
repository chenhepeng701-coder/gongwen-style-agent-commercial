import React, { useEffect, useMemo, useRef, useState } from 'react'
import { getSourceDocumentContent } from '../api'
import Button from './Button'

function uniqueTerms(values) {
  return Array.from(new Set((values || []).map(value => String(value || '').trim()).filter(value => value.length > 1)))
    .sort((left, right) => right.length - left.length)
}

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/gu, '\\$&')
}

function HighlightedText({ text, terms }) {
  const cleanTerms = uniqueTerms(terms)
  if (!cleanTerms.length) return text
  const pattern = new RegExp(`(${cleanTerms.map(escapeRegExp).join('|')})`, 'giu')
  return String(text || '').split(pattern).map((part, index) => (
    cleanTerms.some(term => term.toLocaleLowerCase('zh-CN') === part.toLocaleLowerCase('zh-CN'))
      ? <mark className="source-document-highlight" data-source-match key={`${part}-${index}`}>{part}</mark>
      : <React.Fragment key={`${part}-${index}`}>{part}</React.Fragment>
  ))
}

function fileTypeLabel(value) {
  const normalized = String(value || '').replace(/^\./u, '').toUpperCase()
  return normalized || '本地资料'
}

export default function SourceDocumentDrawer({ open, source, activeCitationId, onClose, onLocateParagraph }) {
  const [tab, setTab] = useState('hits')
  const [contentResponse, setContentResponse] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [search, setSearch] = useState('')
  const [matchIndex, setMatchIndex] = useState(0)
  const contentRef = useRef(null)

  useEffect(() => {
    if (!open || !source?.document_id) return undefined
    let cancelled = false
    setLoading(true)
    setError('')
    setContentResponse(null)
    setSearch('')
    setMatchIndex(0)
    getSourceDocumentContent(source.document_id)
      .then((response) => {
        if (!cancelled) setContentResponse(response)
      })
      .catch((requestError) => {
        if (!cancelled) setError(requestError.message || '该资料原文暂时无法读取。')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => { cancelled = true }
  }, [open, source?.document_id])

  useEffect(() => {
    if (open) setTab('hits')
  }, [open, activeCitationId, source?.document_id])

  const citations = Array.isArray(source?.citations) ? source.citations : []
  const activeCitation = citations.find(item => item.citation_id === activeCitationId) || citations[0] || null
  const hitTerms = useMemo(() => uniqueTerms(citations.flatMap(citation => citation.hit_texts || [])), [citations])
  const fullTextTerms = search.trim() ? [search.trim()] : hitTerms
  const matchCount = useMemo(() => {
    const text = String(contentResponse?.content || '')
    if (!text || !fullTextTerms.length) return 0
    return fullTextTerms.reduce((count, term) => {
      let next = 0
      let offset = 0
      const lowerText = text.toLocaleLowerCase('zh-CN')
      const lowerTerm = term.toLocaleLowerCase('zh-CN')
      while ((offset = lowerText.indexOf(lowerTerm, offset)) >= 0) {
        next += 1
        offset += Math.max(lowerTerm.length, 1)
      }
      return count + next
    }, 0)
  }, [contentResponse?.content, fullTextTerms])

  useEffect(() => {
    if (tab !== 'full' || !contentRef.current) return
    const marks = contentRef.current.querySelectorAll('[data-source-match]')
    if (!marks.length) return
    const normalizedIndex = ((matchIndex % marks.length) + marks.length) % marks.length
    marks[normalizedIndex].scrollIntoView({ block: 'center', behavior: 'smooth' })
  }, [tab, matchIndex, search, source?.document_id, contentResponse?.content])

  if (!open || !source) return null

  return (
    <div className="source-drawer-layer" role="presentation">
      <button type="button" className="source-drawer-backdrop" aria-label="关闭原文查看" onClick={onClose} />
      <aside className="source-drawer" role="dialog" aria-modal="true" aria-labelledby="source-drawer-title">
        <header className="source-drawer__header">
          <div>
            <span>{fileTypeLabel(source.file_type)}</span>
            <h2 id="source-drawer-title">{source.title || source.filename || '未命名资料'}</h2>
            {source.filename && source.filename !== source.title && <p>{source.filename}</p>}
          </div>
          <Button variant="ghost" size="sm" onClick={onClose}>关闭</Button>
        </header>

        <div className="source-drawer__tabs" role="tablist" aria-label="原文查看方式">
          <button type="button" role="tab" aria-selected={tab === 'hits'} className={tab === 'hits' ? 'is-active' : ''} onClick={() => setTab('hits')}>本文命中片段</button>
          <button type="button" role="tab" aria-selected={tab === 'full'} className={tab === 'full' ? 'is-active' : ''} onClick={() => setTab('full')}>完整原文</button>
        </div>

        {loading ? (
          <div className="source-drawer__state" role="status"><span className="core-spinner" /><strong>正在读取本地原文</strong></div>
        ) : error ? (
          <div className="source-drawer__state source-drawer__state--error"><strong>原文暂时无法读取</strong><p>{error}</p></div>
        ) : tab === 'hits' ? (
          <div className="source-drawer__body source-hit-list">
            {citations.length > 0 ? citations.map((citation, index) => (
              <article className={`source-hit-card ${citation.citation_id === activeCitation?.citation_id ? 'is-active' : ''}`} key={citation.citation_id || index}>
                <div className="source-hit-card__meta">
                  <strong>命中片段 {index + 1}</strong>
                  <span>{(citation.section_headings || []).join('、') || '对应正文段落'}</span>
                </div>
                <p><HighlightedText text={citation.excerpt || '该片段暂不可显示。'} terms={citation.hit_texts || []} /></p>
                <div className="source-hit-card__actions">
                  {(citation.paragraph_indexes || []).map((paragraphIndex) => (
                    <button type="button" key={paragraphIndex} onClick={() => onLocateParagraph?.(paragraphIndex)}>定位到正文</button>
                  ))}
                </div>
              </article>
            )) : (
              <div className="source-drawer__state"><strong>暂未记录具体命中片段</strong><p>可以切换到“完整原文”查看资料内容。</p></div>
            )}
          </div>
        ) : (
          <div className="source-drawer__body source-full-document">
            <div className="source-full-document__search">
              <label htmlFor="source-document-search">在原文中查找</label>
              <div>
                <input id="source-document-search" value={search} onChange={event => { setSearch(event.target.value); setMatchIndex(0) }} placeholder="输入关键词；留空时高亮本次命中内容" />
                <span>{matchCount ? `${Math.min(matchIndex + 1, matchCount)} / ${matchCount}` : '0 处'}</span>
                <button type="button" disabled={!matchCount} onClick={() => setMatchIndex(index => index - 1)}>上一处</button>
                <button type="button" disabled={!matchCount} onClick={() => setMatchIndex(index => index + 1)}>下一处</button>
              </div>
            </div>
            <div className="source-full-document__content" ref={contentRef} tabIndex="0">
              <HighlightedText text={contentResponse?.content || ''} terms={fullTextTerms} />
            </div>
          </div>
        )}
      </aside>
    </div>
  )
}
