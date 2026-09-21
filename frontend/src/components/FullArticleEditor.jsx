import React, { useLayoutEffect, useMemo, useRef, useState } from 'react'
import Button from './Button'
import { saveFullArticle } from '../api'
import { articleMarkdown, markdownDisplayBlocks, replaceArticleRange, editedFullArticle } from '../utils/fullArticleProjection'

function EditableArticleBlock({ block, index, saving, onChange }) {
  const node = useRef(null)
  const composing = useRef(false)
  const Tag = block.level ? `h${block.level}` : 'p'
  // React must not replace a live text node on every keystroke (or during IME).
  useLayoutEffect(() => {
    if (!composing.current && node.current.innerText !== block.text) node.current.textContent = block.text
  }, [block.text])
  const publish = () => {
    if (composing.current) return
    const selection = window.getSelection()
    let caret = null
    if (selection?.rangeCount && node.current.contains(selection.focusNode)) {
      const prefix = selection.getRangeAt(0).cloneRange()
      prefix.selectNodeContents(node.current)
      prefix.setEnd(selection.focusNode, selection.focusOffset)
      caret = Array.from(prefix.toString()).length
    }
    onChange(block, node.current.innerText, caret)
  }
  const insertText = text => {
    const selection = window.getSelection()
    if (!selection?.rangeCount) return
    const range = selection.getRangeAt(0)
    range.deleteContents()
    const inserted = document.createTextNode(text)
    range.insertNode(inserted)
    range.setStartAfter(inserted)
    range.collapse(true)
    selection.removeAllRanges()
    selection.addRange(range)
    publish()
  }
  return <Tag ref={node} id={`full-${block.block_id}`} contentEditable={!saving}
    suppressContentEditableWarning role="textbox" data-heading-level={block.level}
    aria-label={block.level === 1 ? '文稿标题' : block.level ? `${block.level - 1}级标题` : `正文段落 ${index + 1}`}
    className="editor-article-block__text" style={{ whiteSpace: 'pre-wrap' }}
    onInput={publish}
    onCompositionStart={() => { composing.current = true }}
    onCompositionEnd={() => { composing.current = false; publish() }}
    onKeyDown={event => {
      if (event.key === 'Enter' && !event.nativeEvent.isComposing) { event.preventDefault(); insertText('\n') }
    }}
    onPaste={event => { event.preventDefault(); insertText(event.clipboardData.getData('text/plain')) }} />
}

// The base is the previous saved revision; markdown is the sole current body.
export default function FullArticleEditor({ article, exporting, onBack, onCopy, onExport, onExportTxt, onSaved }) {
  const [base, setBase] = useState(article)
  const [markdown, setMarkdown] = useState(() => articleMarkdown(article))
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState('')
  const caretPosition = useRef(null)
  const blocks = useMemo(() => markdownDisplayBlocks(markdown), [markdown])
  const current = useMemo(() => editedFullArticle(base, markdown), [base, markdown])
  const title = blocks.find(b => b.level === 1)?.text || ''
  const changed = markdown !== articleMarkdown(base)

  useLayoutEffect(() => {
    const position = caretPosition.current
    caretPosition.current = null
    if (position === null) return
    const block = blocks.find(b => position >= b.text_start && position <= b.end)
    const node = block && document.getElementById(`full-${block.block_id}`)
    if (!node) return
    // Flatten only the DOM presentation, never reconstruct the canonical body.
    if (node.childNodes.length !== 1 || node.firstChild.nodeType !== Node.TEXT_NODE) node.textContent = block.text
    if (!node.firstChild) return
    const range = document.createRange()
    range.setStart(node.firstChild, Array.from(block.text).slice(0, position - block.text_start).join('').length)
    range.collapse(true)
    const selection = window.getSelection()
    selection.removeAllRanges()
    selection.addRange(range)
  }, [blocks])

  const updateText = (block, text, caret) => {
    if (text === block.text) return
    caretPosition.current = caret === null ? null : block.text_start + caret
    setMarkdown(body => replaceArticleRange(body, block, text))
  }

  const save = async () => {
    setSaving(true)
    setMessage('')
    try {
      const response = await saveFullArticle(base, markdown)
      setBase(response.article)
      onSaved?.(response.article)
      setMessage('已保存到我的文稿')
    } catch (error) { setMessage(error.message || '保存失败，正文仍保留在本页') }
    finally { setSaving(false) }
  }

  return <div className="editor-page" data-page-key="editor">
    <header className="editor-header">
      <Button variant="secondary" onClick={onBack} disabled={saving}>返回</Button>
      <div className="editor-header__title"><strong>{title || '未命名文稿'}</strong><span>全文编辑</span></div>
      <div className="editor-header__actions">
        <Button variant="secondary" onClick={save} loading={saving}>保存文稿</Button>
        <Button variant="secondary" onClick={() => onCopy(current)}>复制正文</Button>
        <Button variant="secondary" onClick={() => onExportTxt(current)}>导出 TXT</Button>
        <Button variant="primary" loading={exporting} onClick={() => onExport(current)}>导出 Word</Button>
      </div>
    </header>
    <div className="editor-toolbar" role="toolbar" aria-label="文稿编辑工具">
      <span>版本 {current.article_version || 1}{changed ? '（未保存）' : ''}</span>
      <span>{current.full_article_check_status?.fact === 'STALE' ? '正文已修改，旧检查已失效，需人工复核' : '待人工复核稿'}</span>
      <span className="editor-toolbar__count">{markdown.replace(/\s/gu, '').length} 字</span>
      <span role="status">{message}</span>
    </div>
    <main className="editor-workspace">
      <aside className="core-panel editor-outline"><h2>文章结构</h2>
        {blocks.filter(b => b.level).map(block => <button type="button" key={block.block_id}
          className="editor-outline__item" style={{ paddingLeft: `${block.level * 12}px` }}
          onClick={() => document.getElementById(`full-${block.block_id}`)?.scrollIntoView({ block: 'center' })}>
          {block.text}</button>)}
      </aside>
      <section className="editor-canvas" aria-label="文稿编辑区"><article className="editor-paper">
        <div className="editor-article-content" aria-label="正文">
          {blocks.map((block, index) => <section key={block.block_id} className={`editor-article-block editor-article-block--${block.level ? 'heading' : 'paragraph'}`}>
            <EditableArticleBlock block={block} index={index} saving={saving} onChange={updateText} />
          </section>)}
        </div>
      </article></section>
      <aside className="core-panel editor-inspector"><h2>当前文稿</h2>
        <p>标题和正文可直接修改。修改后请保存到“我的文稿”，以便下次继续打开。</p>
        <p>人工编辑后，上一次事实和质量检查不再适用于当前版本。</p>
      </aside>
    </main>
  </div>
}
