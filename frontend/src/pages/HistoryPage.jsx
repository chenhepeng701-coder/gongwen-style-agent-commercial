import React, { useEffect, useMemo, useState } from 'react'
import Button from '../components/Button'
import { FullArticleBlocks } from '../components/FullArticleContent'
import { isFullArticle, articleMarkdown } from '../utils/fullArticleProjection'
import EmptyState from '../components/EmptyState'
import ErrorState from '../components/ErrorState'
import FormControl from '../components/FormControl'
import Modal from '../components/Modal'
import StatusTag from '../components/StatusTag'
import {
  getGenerationLog,
  openGenerationFile,
  openGenerationFolder,
  openGenerationWord,
} from '../api'

const ARTICLE_MODE_LABELS = {
  draft_article: '公文初稿',
  full_article: '完整文章',
  document_mode: '文种模式',
  official_draft: '正式公文',
}

function getArticleModeLabel(mode) {
  return ARTICLE_MODE_LABELS[mode] || '公文初稿'
}

function formatTime(value) {
  if (!value) return '未记录'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN')
}

function getEntryTitle(entry) {
  return entry?.title || entry?.request_title || '未命名文稿'
}

function getEntryContent(entry) {
  const article = entry?.articles?.[0] || {}
  return isFullArticle(article) ? articleMarkdown(article) : article.content || article.text || entry?.content || ''
}

function getWordCount(entry) {
  if (Number.isFinite(entry?.word_count)) return entry.word_count
  const content = getEntryContent(entry)
  return content ? content.replace(/\s/g, '').length : null
}

function getEntryStatus(entry) {
  if (entry?.status === 'SUCCESS_WITH_REVIEW') return { label: '待人工复核', tone: 'warning' }
  if (entry?.status === 'failed') return { label: '生成失败', tone: 'danger' }
  if (entry?.status === 'running' || entry?.status === 'pending') return { label: '生成中', tone: 'warning' }
  if (entry?.status === 'success' || entry?.status === 'completed') return { label: '已生成', tone: 'success' }
  return { label: '状态待确认', tone: 'neutral' }
}

export default function HistoryPage({ onReuseGenerationEntry, onOpenGenerationEntry, onNavigate }) {
  const [entries, setEntries] = useState([])
  const [loading, setLoading] = useState(false)
  const [loadError, setLoadError] = useState('')
  const [feedbackModal, setFeedbackModal] = useState(null)
  const [selectedEntry, setSelectedEntry] = useState(null)
  const [keyword, setKeyword] = useState('')
  const [statusFilter, setStatusFilter] = useState('all')
  const [openingId, setOpeningId] = useState('')

  const loadEntries = async () => {
    setLoading(true)
    setLoadError('')
    try {
      const response = await getGenerationLog()
      const nextEntries = response?.entries || []
      setEntries(nextEntries)
      setSelectedEntry((current) => {
        if (current) return nextEntries.find((entry) => entry.id === current.id) || nextEntries[0] || null
        return nextEntries[0] || null
      })
    } catch (error) {
      setEntries([])
      setSelectedEntry(null)
      setLoadError(error.message || '生成历史暂时无法加载，请稍后重试。')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { loadEntries() }, [])

  const filteredEntries = useMemo(() => {
    const normalizedKeyword = keyword.trim().toLowerCase()
    return entries.filter((entry) => {
      const matchesStatus = statusFilter === 'all'
        || (statusFilter === 'success' && (entry.status === 'success' || entry.status === 'completed' || entry.status === 'SUCCESS_WITH_REVIEW'))
        || (statusFilter === 'failed' && entry.status === 'failed')
      if (!matchesStatus) return false
      if (!normalizedKeyword) return true
      return [getEntryTitle(entry), entry.document_type, entry.scene, entry.timestamp]
        .filter(Boolean)
        .join(' ')
        .toLowerCase()
        .includes(normalizedKeyword)
    })
  }, [entries, keyword, statusFilter])

  const runOpenAction = async (entry, type) => {
    const actionKey = `${type}-${entry.id}`
    setOpeningId(actionKey)
    try {
      let response
      if (type === 'word') response = await openGenerationWord(entry.id)
      if (type === 'file') response = await openGenerationFile(entry.id)
      if (type === 'folder') response = await openGenerationFolder(entry.id)
      if (!response?.success) throw new Error(response?.message || '打开请求未完成')
      const feedback = {
        word: { title: 'Word 已打开', message: '已使用系统默认应用打开真实导出文件。' },
        file: { title: '文件已打开', message: '已使用系统默认应用打开真实导出文件。' },
        folder: { title: '所在目录已打开', message: '已在文件管理器中定位真实导出文件。' },
      }[type]
      setFeedbackModal({ ...feedback, type: 'success' })
    } catch (error) {
      setFeedbackModal({ title: '打开失败', message: error.message || '暂时无法打开该文件。', type: 'error' })
    } finally {
      setOpeningId('')
    }
  }

  const selectedStatus = selectedEntry ? getEntryStatus(selectedEntry) : null
  const selectedContent = selectedEntry ? getEntryContent(selectedEntry) : ''

  return (
    <div className="core-page drafts-page" data-page-key="history">
      <Modal
        visible={!!feedbackModal}
        type={feedbackModal?.type || 'info'}
        title={feedbackModal?.title}
        message={feedbackModal?.message}
        onClose={() => setFeedbackModal(null)}
      />

      <div className="core-page-heading">
        <div>
          <h1>我的文稿</h1>
          <p>查看真实生成记录，继续复用写作参数或打开已导出的文件。</p>
        </div>
        <Button onClick={() => onNavigate?.('articleGen')}>新建文章</Button>
      </div>

      <div className="drafts-tools">
        <FormControl htmlFor="draft-search" label="搜索文稿" className="drafts-search">
          <input id="draft-search" value={keyword} onChange={(event) => setKeyword(event.target.value)} placeholder="搜索标题、文种或时间" />
        </FormControl>
        <FormControl htmlFor="draft-status" label="生成状态" className="drafts-filter">
          <select id="draft-status" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
            <option value="all">全部记录</option>
            <option value="success">已生成</option>
            <option value="failed">生成失败</option>
          </select>
        </FormControl>
      </div>

      <div className="drafts-workspace core-split-workspace">
        <section className="core-panel drafts-list-panel">
          <div className="core-panel-heading">
            <div><h2>生成记录</h2><p>{filteredEntries.length} 条可见记录</p></div>
            <Button variant="ghost" size="sm" onClick={loadEntries} disabled={loading}>刷新</Button>
          </div>

          {loading ? (
            <div className="core-state"><span className="core-spinner" /><strong>正在读取生成记录</strong></div>
          ) : loadError ? (
            <ErrorState title="文稿暂时无法加载" error={loadError} onRetry={loadEntries} />
          ) : filteredEntries.length === 0 ? (
            <EmptyState
              icon={entries.length ? '筛' : '稿'}
              title={entries.length ? '没有匹配的文稿' : '还没有生成记录'}
              description={entries.length ? '请调整搜索或筛选条件。' : '生成或创建的文章会保存在这里。'}
              actionLabel={entries.length ? '清除筛选' : '新建文章'}
              onAction={entries.length
                ? () => { setKeyword(''); setStatusFilter('all') }
                : () => onNavigate?.('articleGen')}
            />
          ) : (
            <div className="drafts-list">
              {filteredEntries.map((entry) => {
                const status = getEntryStatus(entry)
                const wordCount = getWordCount(entry)
                return (
                  <article key={entry.id} className={`draft-row ${selectedEntry?.id === entry.id ? 'is-selected' : ''}`} onClick={() => setSelectedEntry(entry)}>
                    <div className="draft-row-main">
                      <div className="draft-title-row"><strong>{getEntryTitle(entry)}</strong><StatusTag tone={status.tone}>{status.label}</StatusTag></div>
                      <div className="draft-meta">
                        <span>{entry.document_type || getArticleModeLabel(entry.article_mode)}</span>
                        <span>{formatTime(entry.timestamp)}</span>
                        {wordCount !== null && <span>{wordCount.toLocaleString()} 字</span>}
                      </div>
                    </div>
                    <span className="draft-row-arrow" aria-hidden="true">›</span>
                  </article>
                )
              })}
            </div>
          )}
        </section>

        <aside className="core-panel draft-detail-panel">
          <div className="core-panel-heading"><div><h2>文稿预览</h2><p>来自现有生成历史</p></div></div>
          {selectedEntry ? (
            <div className="draft-detail-content">
              <div className="draft-detail-header">
                <StatusTag tone={selectedStatus.tone}>{selectedStatus.label}</StatusTag>
                <h3>{getEntryTitle(selectedEntry)}</h3>
                <p>{selectedEntry.document_type || getArticleModeLabel(selectedEntry.article_mode)} · {formatTime(selectedEntry.timestamp)}</p>
              </div>
              <div className="draft-preview-paper">
                {selectedContent ? (isFullArticle(selectedEntry.articles?.[0]) ? <FullArticleBlocks article={selectedEntry.articles[0]} /> : <p>{selectedContent}</p>) : (
                  <EmptyState
                    icon="文"
                    title="暂无可预览正文"
                    description="当前历史接口未提供正文内容，可通过下方真实文件入口查看结果。"
                    className="core-state--compact"
                  />
                )}
              </div>
              <div className="draft-detail-actions">
                <Button disabled={!selectedContent} onClick={() => onOpenGenerationEntry?.(selectedEntry)}>阅读和编辑</Button>
                <Button variant="secondary" onClick={() => onReuseGenerationEntry?.(selectedEntry)}>复用写作参数</Button>
                {selectedEntry.export_format === 'txt' ? (
                  <Button onClick={() => runOpenAction(selectedEntry, 'file')} loading={openingId === `file-${selectedEntry.id}`}>打开文件</Button>
                ) : (
                  <Button disabled={isFullArticle(selectedEntry.articles?.[0]) && !selectedEntry.articles[0].docx_path} onClick={() => runOpenAction(selectedEntry, 'word')} loading={openingId === `word-${selectedEntry.id}`}>打开 Word</Button>
                )}
                <Button variant="ghost" disabled={isFullArticle(selectedEntry.articles?.[0]) && !selectedEntry.articles[0].save_path && !selectedEntry.articles[0].docx_path} onClick={() => runOpenAction(selectedEntry, 'folder')} loading={openingId === `folder-${selectedEntry.id}`}>打开所在目录</Button>
              </div>
            </div>
          ) : (
            <EmptyState icon="稿" title="选择一条生成记录" description="在左侧选择文稿后查看可验证信息。" className="core-state--compact" />
          )}
        </aside>
      </div>
    </div>
  )
}
