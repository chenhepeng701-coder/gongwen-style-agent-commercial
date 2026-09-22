import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  deleteDocument,
  getDocument,
  listDocuments,
  processLibraryDocument,
  searchDocuments,
  updateDocumentTags,
  uploadDocument,
} from '../api'
import Button from '../components/Button'
import ConfirmDialog from '../components/ConfirmDialog'
import EmptyState from '../components/EmptyState'
import ErrorState from '../components/ErrorState'
import FormControl from '../components/FormControl'
import Modal from '../components/Modal'
import StatusTag from '../components/StatusTag'
import { collectDroppedFiles, collectLibraryFiles } from '../utils/dropFiles'
import { batchUploadLibraryFiles, formatImportResultMessage } from '../utils/libraryUpload'
import {
  BACKFILL_CONFIRMATION, READINESS_UNAVAILABLE_MESSAGE, createLibraryReadiness,
  formatBackfillResult, getDocumentStatus, getIngestionFailureMessage,
} from '../utils/libraryReadiness'

const PAGE_SIZE = 20
const SUPPORTED_TYPES = '.docx,.txt,.md'

function formatDate(value) {
  if (!value) return '未记录'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN')
}

function formatFileType(value) {
  if (!value) return '文件'
  return String(value).replace(/^\./, '').toUpperCase()
}

export default function LibraryPage() {
  const [documents, setDocuments] = useState([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [uploading, setUploading] = useState(false)
  const [dragOver, setDragOver] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  const [filterKeyword, setFilterKeyword] = useState('')
  const [searchResults, setSearchResults] = useState(null)
  const [searching, setSearching] = useState(false)
  const [selectedDocument, setSelectedDocument] = useState(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [tagDraft, setTagDraft] = useState('')
  const [savingTags, setSavingTags] = useState(false)
  const [pendingDelete, setPendingDelete] = useState(null)
  const [deleting, setDeleting] = useState(false)
  const [modal, setModal] = useState(null)
  const [currentPage, setCurrentPage] = useState(1)
  const [totalPages, setTotalPages] = useState(1)
  const [totalDocs, setTotalDocs] = useState(0)
  const [backfillProgress, setBackfillProgress] = useState(null)
  const [processingIds, setProcessingIds] = useState(new Set())
  const [readinessError, setReadinessError] = useState('')
  const readinessRef = useRef(null)
  const backfillRunningRef = useRef(false)
  const fileInputRef = useRef(null)
  const folderInputRef = useRef(null)

  const updateReadiness = useCallback((document) => {
    setDocuments((current) => current.map((item) => item.id === document.id ? { ...item, ...document } : item))
    setSelectedDocument((current) => current?.id === document.id ? { ...current, ...document } : current)
  }, [])

  useEffect(() => {
    const controller = createLibraryReadiness({
      getDocument, processDocument: processLibraryDocument, listDocuments,
      onDocument: updateReadiness,
      onNotice: (message) => setModal({ type: 'info', title: '资料正在处理中', message }),
      onPollingError: () => setReadinessError(READINESS_UNAVAILABLE_MESSAGE),
    })
    readinessRef.current = controller
    return () => {
      controller.dispose()
      readinessRef.current = null
    }
  }, [updateReadiness])

  useEffect(() => {
    readinessRef.current?.observe([...documents, selectedDocument].filter(Boolean))
  }, [documents, selectedDocument])

  const fetchDocuments = useCallback(async () => {
    setLoading(true)
    setLoadError('')
    try {
      const response = await listDocuments(currentPage, PAGE_SIZE)
      const items = response?.items || []
      setDocuments(items)
      setTotalPages(response?.total_pages || 1)
      setTotalDocs(response?.total || 0)
      setSelectedDocument((current) => {
        if (current) return items.find((item) => item.id === current.id) || items[0] || null
        return items[0] || null
      })
    } catch (error) {
      setDocuments([])
      setSelectedDocument(null)
      setLoadError(error.message || '资料列表暂时无法加载，请稍后重试。')
    } finally {
      setLoading(false)
    }
  }, [currentPage])

  useEffect(() => { fetchDocuments() }, [fetchDocuments])

  const selectedTags = (selectedDocument?.scene_tags || []).join('、')
  useEffect(() => {
    setTagDraft(selectedTags)
  }, [selectedDocument?.id, selectedTags])

  const visibleDocuments = useMemo(() => {
    if (!filterKeyword.trim()) return documents
    const keyword = filterKeyword.trim().toLowerCase()
    return documents.filter((document) => {
      const searchable = [document.title, document.file_type, ...(document.scene_tags || [])]
        .filter(Boolean)
        .join(' ')
        .toLowerCase()
      return searchable.includes(keyword)
    })
  }, [documents, filterKeyword])

  const showModal = (type, title, message) => setModal({ type, title, message })

  const confirmBeforeUpload = (onConfirm) => {
    setModal({
      type: 'info',
      title: '上传资料提醒',
      message: '请上传已脱敏、可用于个人写作参考的资料。请勿上传涉密、未脱敏或不宜外传资料。',
      cancelText: '取消',
      confirmText: '我已确认，继续上传',
      onConfirm: () => {
        setModal(null)
        onConfirm?.()
      },
    })
  }

  const runImport = async ({ files, ignoredUnsupported = 0, readFailed = false }, input) => {
    if (!files.length) {
      showModal('error', '导入失败', '未找到可导入的 .docx、.txt 或 .md 文件。')
      if (input) input.value = ''
      return
    }
    setUploading(true)
    try {
      const result = await batchUploadLibraryFiles(files, { uploadFile: async (file) => {
        const response = await uploadDocument(file)
        if (response?.document) readinessRef.current?.observe([response.document])
        return response
      } })
      const notes = []
      if (ignoredUnsupported) notes.push('已忽略不支持的文件。')
      if (readFailed) notes.push('部分文件读取失败，请检查文件权限。')
      if (result.errors?.length) notes.push(`失败原因：${result.errors.slice(0, 3).join('；')}`)
      showModal(
        result.fail > 0 || readFailed ? 'error' : 'success',
        result.fail > 0 ? '导入完成，部分失败' : '导入完成',
        `${formatImportResultMessage({ ok: result.ok, fail: result.fail, ignored: ignoredUnsupported })}${notes.length ? `\n${notes.join('\n')}` : ''}`,
      )
      await fetchDocuments()
    } catch (error) {
      showModal('error', '导入失败', error.message || '资料导入失败，请稍后重试。')
    } finally {
      setUploading(false)
      if (input) input.value = ''
    }
  }

  const handleFileSelect = (event) => runImport(collectLibraryFiles(event.target.files), event.target)
  const handleFolderSelect = (event) => runImport(collectLibraryFiles(event.target.files), event.target)

  const handleDrop = async (event) => {
    event.preventDefault()
    setDragOver(false)
    const result = await collectDroppedFiles(event.dataTransfer.items, event.dataTransfer.files)
    confirmBeforeUpload(() => runImport(result))
  }

  const handleSearch = async (event) => {
    event?.preventDefault?.()
    const query = searchQuery.trim()
    if (!query) {
      setSearchResults(null)
      return
    }
    setSearching(true)
    try {
      const response = await searchDocuments(query)
      setSearchResults(response?.results || [])
    } catch (error) {
      setSearchResults([])
      showModal('error', '搜索失败', error.message || '暂时无法搜索资料，请稍后重试。')
    } finally {
      setSearching(false)
    }
  }

  const openDocumentDetail = async (document) => {
    setSelectedDocument(document)
    setDetailLoading(true)
    try {
      const detail = await getDocument(document.id)
      setSelectedDocument(detail?.document || detail || document)
    } catch (error) {
      showModal('error', '详情加载失败', error.message || '暂时无法读取资料详情。')
    } finally {
      setDetailLoading(false)
    }
  }

  const saveTags = async () => {
    if (!selectedDocument) return
    const tags = tagDraft.split(/[、,，]/).map((tag) => tag.trim()).filter(Boolean)
    setSavingTags(true)
    try {
      await updateDocumentTags(selectedDocument.id, tags)
      setDocuments((current) => current.map((item) => (
        item.id === selectedDocument.id ? { ...item, scene_tags: tags } : item
      )))
      setSelectedDocument((current) => ({ ...current, scene_tags: tags }))
      showModal('success', '标签已更新', '资料分类和标签已保存。')
    } catch (error) {
      showModal('error', '保存失败', error.message || '标签暂时无法保存。')
    } finally {
      setSavingTags(false)
    }
  }

  const confirmDelete = async () => {
    if (!pendingDelete) return
    setDeleting(true)
    try {
      await deleteDocument(pendingDelete.id)
      setPendingDelete(null)
      showModal('success', '已移除资料', '资料已从资料库中移除。')
      await fetchDocuments()
    } catch (error) {
      showModal('error', '删除失败', error.message || '资料暂时无法删除。')
    } finally {
      setDeleting(false)
    }
  }

  const handleProcess = async (document) => {
    const controller = readinessRef.current
    if (!controller || backfillRunningRef.current) return
    setReadinessError('')
    setProcessingIds((current) => new Set(current).add(document.id))
    try {
      const outcome = await controller.process(document)
      if (readinessRef.current !== controller) return
      if (!outcome.skipped && outcome.document.ingestion_status !== 'READY') {
        showModal('info', '资料处理未完成', getIngestionFailureMessage())
      }
    } catch (error) {
      if (readinessRef.current === controller) setReadinessError(READINESS_UNAVAILABLE_MESSAGE)
    } finally {
      if (readinessRef.current === controller) setProcessingIds((current) => {
        const next = new Set(current)
        next.delete(document.id)
        return next
      })
    }
  }

  const runBackfill = async () => {
    const controller = readinessRef.current
    if (!controller || backfillRunningRef.current) return
    backfillRunningRef.current = true
    setModal(null)
    setReadinessError('')
    setBackfillProgress({ completed: 0, total: 0, title: '正在读取待处理资料' })
    try {
      const result = await controller.backfill({ onProgress: setBackfillProgress })
      if (readinessRef.current === controller) showModal('info', '历史资料处理完成', formatBackfillResult(result))
    } catch (error) {
      if (readinessRef.current === controller) setReadinessError(READINESS_UNAVAILABLE_MESSAGE)
    } finally {
      if (readinessRef.current === controller) {
        backfillRunningRef.current = false
        setBackfillProgress(null)
      }
    }
  }

  const confirmBackfill = () => setModal({
    type: 'info', title: '处理已有资料', message: BACKFILL_CONFIRMATION,
    cancelText: '取消', confirmText: '确认处理', onConfirm: runBackfill,
  })

  const processButton = (document) => {
    const status = getDocumentStatus(document)
    if (!status.action) return null
    return <Button variant="secondary" size="sm"
      disabled={!!backfillProgress || processingIds.has(document.id) || document.ingestion_status === 'PROCESSING'}
      onClick={(event) => { event.stopPropagation(); handleProcess(document) }}
    >{processingIds.has(document.id) ? '处理中' : status.action}</Button>
  }

  return (
    <div className="core-page library-page" data-page-key="library">
      <Modal
        visible={!!modal}
        type={modal?.type}
        title={modal?.title}
        message={modal?.message}
        onClose={() => setModal(null)}
        onConfirm={modal?.onConfirm}
        cancelText={modal?.cancelText}
        confirmText={modal?.confirmText}
      />
      <ConfirmDialog
        open={!!pendingDelete}
        tone="danger"
        title="移除这份资料？"
        description={pendingDelete ? `“${pendingDelete.title}”将从资料库中移除，原始文件不会被删除。` : ''}
        confirmText="确认移除"
        cancelText="取消"
        pending={deleting}
        onConfirm={confirmDelete}
        onCancel={() => setPendingDelete(null)}
      />

      <input ref={fileInputRef} className="visually-hidden" type="file" accept={SUPPORTED_TYPES} multiple onChange={handleFileSelect} />
      <input ref={folderInputRef} className="visually-hidden" type="file" accept={SUPPORTED_TYPES} multiple webkitdirectory="" directory="" onChange={handleFolderSelect} />

      <div className="core-page-heading">
        <div>
          <h1>资料库</h1>
          <p>集中管理本地写作资料，上传前请确认内容已经脱敏。</p>
        </div>
        <div className="core-heading-actions">
          <Button variant="secondary" onClick={confirmBackfill} disabled={!!backfillProgress || uploading || processingIds.size > 0}>处理已有资料</Button>
          <Button variant="secondary" onClick={() => confirmBeforeUpload(() => folderInputRef.current?.click())} disabled={uploading}>导入文件夹</Button>
          <Button onClick={() => confirmBeforeUpload(() => fileInputRef.current?.click())} loading={uploading}>上传资料</Button>
        </div>
      </div>

      {backfillProgress && <section className="core-panel library-detail-content" role="status" aria-live="polite">
        <strong>正在处理已有资料 {backfillProgress.completed} / {backfillProgress.total}</strong>
        <p>{backfillProgress.title}</p>
        <p>离开页面后将停止安排后续资料，已开始的资料会继续处理。下次可继续处理剩余待处理资料。</p>
      </section>}
      {readinessError && <ErrorState title="资料状态暂时无法更新" error={readinessError} onRetry={() => {
        setReadinessError('')
        fetchDocuments()
      }} />}

      <section
        className={`library-drop-zone ${dragOver ? 'is-drag-over' : ''}`}
        onDragOver={(event) => { event.preventDefault(); setDragOver(true) }}
        onDragLeave={() => setDragOver(false)}
        onDrop={handleDrop}
      >
        <div className="library-drop-mark" aria-hidden="true">＋</div>
        <div>
          <strong>{uploading ? '资料正在导入' : '拖放资料到这里'}</strong>
          <span>支持 DOCX、TXT、MD，可一次选择多个文件</span>
        </div>
      </section>

      <div className="library-tools">
        <form className="core-search" onSubmit={handleSearch}>
          <FormControl htmlFor="library-search" label="搜索资料" className="core-search-field">
            <input id="library-search" value={searchQuery} onChange={(event) => setSearchQuery(event.target.value)} placeholder="搜索标题或正文内容" />
          </FormControl>
          <Button type="submit" variant="secondary" loading={searching}>搜索</Button>
          {searchResults !== null && <Button type="button" variant="ghost" onClick={() => { setSearchQuery(''); setSearchResults(null) }}>清除</Button>}
        </form>
        <FormControl htmlFor="library-filter" label="筛选当前列表" className="library-filter">
          <input id="library-filter" value={filterKeyword} onChange={(event) => setFilterKeyword(event.target.value)} placeholder="标题、类型或标签" />
        </FormControl>
      </div>

      {searchResults !== null && (
        <section className="library-search-results" aria-live="polite">
          <div><strong>搜索结果</strong><span>{searchResults.length} 条匹配内容</span></div>
          {searchResults.length > 0 ? (
            <div className="library-search-result-list">
              {searchResults.slice(0, 6).map((result, index) => (
                <div key={result.id || result.document_id || `${result.title || 'result'}-${index}`}>
                  <strong>{result.title || result.document_title || '未命名资料'}</strong>
                  <p>{result.content || result.content_preview || result.text || '该结果没有可展示的摘要。'}</p>
                </div>
              ))}
            </div>
          ) : (
            <EmptyState
              icon="搜"
              title="没有找到匹配资料"
              description="请更换关键词后重新搜索。"
              actionLabel="清除搜索"
              onAction={() => { setSearchQuery(''); setSearchResults(null) }}
              className="core-state--compact"
            />
          )}
        </section>
      )}

      <div className="library-workspace core-split-workspace">
        <section className="core-panel library-list-panel">
          <div className="core-panel-heading">
            <div><h2>全部资料</h2><p>共 {totalDocs} 份</p></div>
            <Button variant="ghost" size="sm" onClick={fetchDocuments} disabled={loading}>刷新</Button>
          </div>

          {loading ? (
            <div className="core-state"><span className="core-spinner" /><strong>正在读取资料库</strong></div>
          ) : loadError ? (
            <ErrorState title="资料暂时无法加载" error={loadError} onRetry={fetchDocuments} />
          ) : visibleDocuments.length === 0 ? (
            <EmptyState
              icon={documents.length ? '筛' : '库'}
              title={documents.length ? '没有匹配的资料' : '资料库还是空的'}
              description={documents.length ? '请调整当前列表筛选条件。' : '上传资料后，系统会自动处理并用于写作。'}
              actionLabel={documents.length ? '清除筛选' : '上传资料'}
              onAction={documents.length
                ? () => setFilterKeyword('')
                : () => confirmBeforeUpload(() => fileInputRef.current?.click())}
            />
          ) : (
            <div className="library-document-list">
              {visibleDocuments.map((document) => {
                const status = getDocumentStatus(document)
                return (
                  <article key={document.id} className={`library-document-row ${selectedDocument?.id === document.id ? 'is-selected' : ''}`} onClick={() => openDocumentDetail(document)}>
                    <div className="library-file-icon">{formatFileType(document.file_type).slice(0, 3)}</div>
                    <div className="library-document-main">
                      <div className="library-document-title-row"><strong>{document.title || '未命名资料'}</strong><StatusTag tone={status.tone}>{status.label}</StatusTag></div>
                      <div className="library-document-meta"><span>{formatFileType(document.file_type)}</span><span>{formatDate(document.indexed_at || document.created_at)}</span>{Number.isFinite(document.word_count) && <span>{document.word_count.toLocaleString()} 字</span>}</div>
                      {!!document.scene_tags?.length && <div className="library-tags">{document.scene_tags.slice(0, 4).map((tag) => <span key={`${document.id}-${tag}`}>{tag}</span>)}</div>}
                    </div>
                    {processButton(document)}
                    <Button variant="ghost" size="sm" onClick={(event) => { event.stopPropagation(); setPendingDelete(document) }}>移除</Button>
                  </article>
                )
              })}
            </div>
          )}

          {totalPages > 1 && (
            <div className="core-pagination">
              <Button variant="secondary" size="sm" disabled={currentPage <= 1} onClick={() => setCurrentPage((page) => page - 1)}>上一页</Button>
              <span>第 {currentPage} / {totalPages} 页</span>
              <Button variant="secondary" size="sm" disabled={currentPage >= totalPages} onClick={() => setCurrentPage((page) => page + 1)}>下一页</Button>
            </div>
          )}
        </section>

        <aside className="core-panel library-detail-panel">
          <div className="core-panel-heading"><div><h2>资料详情</h2><p>查看基础信息和标签</p></div></div>
          {detailLoading ? (
            <div className="core-state"><span className="core-spinner" /><strong>正在读取详情</strong></div>
          ) : selectedDocument ? (
            <div className="library-detail-content">
              <div className="library-detail-title"><div className="library-file-icon">{formatFileType(selectedDocument.file_type).slice(0, 3)}</div><div><strong>{selectedDocument.title || '未命名资料'}</strong><span>{formatFileType(selectedDocument.file_type)}</span></div></div>
              <dl className="core-detail-list">
                <div><dt>写作状态</dt><dd><StatusTag tone={getDocumentStatus(selectedDocument).tone}>{getDocumentStatus(selectedDocument).label}</StatusTag></dd></div>
                <div><dt>入库时间</dt><dd>{formatDate(selectedDocument.indexed_at || selectedDocument.created_at)}</dd></div>
                {Number.isFinite(selectedDocument.word_count) && <div><dt>字数</dt><dd>{selectedDocument.word_count.toLocaleString()} 字</dd></div>}
              </dl>
              {selectedDocument.ingestion_status === 'FAILED' && <p role="status">{getIngestionFailureMessage(selectedDocument)}</p>}
              {processButton(selectedDocument)}
              <FormControl htmlFor="document-tags" label="分类与标签" description="使用顿号或逗号分隔">
                <input id="document-tags" value={tagDraft} onChange={(event) => setTagDraft(event.target.value)} placeholder="例如：工作总结、基层治理" />
              </FormControl>
              <Button variant="secondary" onClick={saveTags} loading={savingTags}>保存标签</Button>
              <div className="library-preview">
                <strong>内容预览</strong>
                <p>{selectedDocument.content_preview || selectedDocument.content || '当前接口未提供可展示的正文预览。'}</p>
              </div>
            </div>
          ) : (
            <EmptyState icon="资" title="选择一份资料" description="点击左侧资料查看基础信息。" className="core-state--compact" />
          )}
        </aside>
      </div>
    </div>
  )
}
