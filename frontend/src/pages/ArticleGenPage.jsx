import React, { useState, useEffect, useRef } from 'react'
import FullArticleContent from '../components/FullArticleContent'
import { fullArticleCopyText, verifyFullArticleContent, isFullArticle, articleMarkdown } from '../utils/fullArticleProjection'
import * as XLSX from 'xlsx'
import {
  generateArticle,
  getGenreCatalog,
  getSceneTaxonomy,
  getBusinessDomainCatalog,
  getLibraryStats,
  getGenerationLog,
  getGenerationOutputDir,
  getGenerationDefaultSettings,
  saveGenerationOutputDir,
  saveGenerationDefaultSettings,
  openGenerationOutputDir,
  openGenerationFile,
  exportDocumentModeWord,
  exportWord,
  exportFullArticleTxt,
} from '../api'
import ConfirmDialog from '../components/ConfirmDialog'
import Modal from '../components/Modal'
import Button from '../components/Button'
import FormControl from '../components/FormControl'
import BatchTaskPage from './BatchTaskPage'
import GenerationPage from './GenerationPage'
import GenerationWaitingIndicator from '../components/GenerationWaitingIndicator'
import EditorPage from './EditorPage'
import {
  buildDocumentModeDraftText,
  getDocumentModeDraftFilename,
  getDocumentModeDraftSections,
} from '../utils/documentModeDraft'
import { buildTaskGenerationPayload, referenceModeFromScope } from '../utils/batchTaskParams'
import { formatBatchImportSummary, parseBatchExcelRows } from '../utils/batchExcelImport'
import { loadWritingCatalogs, normalizeScene, normalizeUiTaskGenre, normalizeRestoredTaskGenre, productionTaskError, normalizeBusinessDomain, normalizeUiTaskBusinessDomain } from '../utils/genreCatalog'
import { GenreSelect, SceneSelect, BusinessDomainSelect } from '../components/WritingCatalogSelects'

const WORD_EXPORT_RESPONSIBILITY_NOTICE = '导出内容仅为辅助写作初稿，不代表最终可提交版本。请结合本单位要求、领导意见和最新政策口径进行修改审核。'

const ARTICLE_MODE_LABELS = {
  official_draft: '公文初稿',
  draft_article: '公文初稿',
  full_article: '公文初稿',
}

const LEVEL_OPTIONS = ['乡镇 / 街道', '县区', '县级', '市级', '省直 / 厅局', '基层单位', '其他']

const ORG_TYPE_OPTIONS = [
  '党政机关',
  '乡镇街道',
  '事业单位',
  '财政部门',
  '学校',
  '医院',
  '国企',
  '村 / 社区',
  '园区 / 开发区',
  '其他',
]

const WRITING_PURPOSE_OPTIONS = [
  '情况汇报',
  '贯彻落实',
  '汇报总结',
  '总结复盘',
  '工作部署',
  '学习研讨',
  '述职表态',
  '问题整改',
  '宣传动员',
  '其他',
]

const DEFAULT_BUSINESS_DOMAIN = '安全生产'
const DEFAULT_WRITING_PURPOSE = '汇报总结'

const REFERENCE_SCOPE_OPTIONS = [
  '自动匹配资料库',
  '优先参考搜索结果',
  '优先参考最近上传资料',
]

const INITIAL_TASK_DEFAULTS = {
  documentType: '',
  scene: '',
  level: LEVEL_OPTIONS[0],
  orgType: ORG_TYPE_OPTIONS[0],
  businessDomain: DEFAULT_BUSINESS_DOMAIN,
  writingPurpose: DEFAULT_WRITING_PURPOSE,
  wordCount: 1200,
  referenceScope: REFERENCE_SCOPE_OPTIONS[0],
}

let batchTaskSequence = 0

function createUiBatchTask(defaults, overrides = {}) {
  batchTaskSequence += 1
  const imported = Object.fromEntries(Object.entries(overrides).filter(([, value]) => value !== undefined && value !== null && value !== ''))
  return {
    ...defaults,
    ...imported,
    scene: Object.prototype.hasOwnProperty.call(overrides, 'scene') ? (normalizeScene(overrides.scene) || '') : (defaults.scene || ''),
    title: imported.title ?? '',
    count: imported.count ?? 1,
    id: overrides.id || `batch-task-${batchTaskSequence}`,
    expanded: overrides.expanded ?? false,
    status: overrides.status || 'pending',
  }
}

function getArticleModeLabel(mode) {
  return ARTICLE_MODE_LABELS[mode] || '公文初稿'
}

function getErrorMessage(error) {
  return String(error?.message || error || '')
}

function isMaterialMissingError(error) {
  const message = getErrorMessage(error)
  return error?.errorCode === 'INSUFFICIENT_EVIDENCE'
    || message.includes('当前资料不足以支撑安全生成')
    || message.includes('资料库素材不足')
    || message.includes('未检索到相关资料')
    || message.includes('NoRelevantMaterialError')
    || message.includes('No relevant material')
    || message.includes('没有合格写法片段')
    || message.includes('素材不足，无法生成')
}

function isPaymentRequiredError(error) {
  const message = getErrorMessage(error)
  return message.includes('402 Payment Required')
    || message.includes('Payment Required')
    || message.includes('余额不足')
    || message.includes('额度不足')
}

function getReferenceSources(article) {
  if (Array.isArray(article?.reference_sources)) {
    return article.reference_sources.slice(0, 5)
  }
  return []
}

function formatReferenceScore(score) {
  const numeric = Number(score)
  if (!Number.isFinite(numeric) || numeric <= 0) return ''
  return `相关度：${numeric.toFixed(2)}`
}

function isQualityTestDraft(article) {
  return article?.quality_test_mode === true
    && article?.delivery_status === 'QUALITY_TEST_DRAFT'
    && article?.official_success === false
}

function isReviewRequiredDraft(article) {
  return article?.delivery_status === 'REVIEW_REQUIRED_DRAFT'
    && article?.official_success === false
    && article?.user_confirmation_required === true
}

function qualityTestIssueCount(article) {
  const gapCount = Number(article?.fact_gap_count || 0)
  const analysisCount = Number(article?.analysis_risk_count || 0)
  return gapCount + analysisCount
}

function lengthGateLabel(status) {
  if (status === 'FAIL_UNDER_LENGTH') return '不足'
  if (status === 'FAIL_OVER_LENGTH') return '超出'
  return status === 'PASS' ? '通过' : '未通过'
}

function structureGateLabel(status) {
  return status === 'PASS' ? '完整' : '不完整'
}

function generationLogStatusLabel(entry) {
  return entry.status === 'failed'
    ? '失败'
    : entry.status === 'QUALITY_TEST_DRAFT'
      ? '质量测试稿'
      : entry.status === 'REVIEW_REQUIRED_DRAFT'
        ? '待核对草稿'
        : '成功'
}

function QualityTestDraftDetails({ art }) {
  if (!isQualityTestDraft(art)) return null
  return (
    <div className="write-quality-notice" data-testid="quality-test-draft-status">
      <div>
        <strong>质量测试稿｜存在 {qualityTestIssueCount(art)} 处事实缺口或风险｜当前不作为正式交付稿</strong>
        <div className="gen-result-meta">
          <span className="gen-meta-tag">目标字数：{art.target_word_count}</span>
          <span className="gen-meta-tag">实际字数：{art.word_count}</span>
          <span className="gen-meta-tag">篇幅状态：{lengthGateLabel(art.length_gate_status)}</span>
          <span className="gen-meta-tag">结构状态：{structureGateLabel(art.structure_gate_status)}</span>
          <span className="gen-meta-tag">事实风险：{art.fact_risk_count}</span>
        </div>
        {art.risk_items?.length > 0 && (
          <details data-testid="quality-test-risk-details">
            <summary>查看风险详情</summary>
            {art.risk_items.map((item, index) => (
              <p key={`${item.section_id}-${item.paragraph_index}-${index}`}>
                {item.section_heading || item.section_id || '未定位章节'} · 第 {Number(item.paragraph_index || 0) + 1} 段
              </p>
            ))}
          </details>
        )}
      </div>
      <div className="gen-meta-tag">正文内容</div>
    </div>
  )
}

function ReviewRequiredDraftDetails({ art }) {
  if (!isReviewRequiredDraft(art)) return null
  return (
    <div className="write-quality-notice" data-testid="review-required-draft-status">
      <div>
        <strong>草稿已生成，其中部分内容建议结合原始资料人工核对。</strong>
        <div className="gen-result-meta">
          <span className="gen-meta-tag">事实风险：{Number(art.fact_risk_count || 0)}</span>
          <span className="gen-meta-tag">分析风险：{Number(art.analysis_risk_count || 0)}</span>
        </div>
      </div>
      <div className="gen-meta-tag">正文可复制、编辑和导出</div>
    </div>
  )
}

export default function ArticleGenPage({
  activePage = 'articleGen',
  articleGenDraft,
  onArticleGenDraftConsumed,
  historyEditorArticle,
  onHistoryEditorArticleConsumed,
  onNavigate,
  onGenerationPendingChange,
}) {
  const [rows, setRows] = useState(() => [createUiBatchTask(INITIAL_TASK_DEFAULTS)])
  const [scene, setScene] = useState('')
  const [genreCatalog, setGenreCatalog] = useState(null)
  const [sceneOptions, setSceneOptions] = useState([])
  const [businessDomainOptions, setBusinessDomainOptions] = useState([])
  const [catalogLoading, setCatalogLoading] = useState(true)
  const [catalogError, setCatalogError] = useState('')
  const [articleMode, setArticleMode] = useState('official_draft')
  const [analysisWrapperEnabled, setAnalysisWrapperEnabled] = useState(false)
  const [appreciationScope, setAppreciationScope] = useState('single_deep')
  const [appreciationCount, setAppreciationCount] = useState(3)
  const [documentType, setDocumentType] = useState('')
  const [level, setLevel] = useState(LEVEL_OPTIONS[0])
  const [orgType, setOrgType] = useState(ORG_TYPE_OPTIONS[0])
  const [businessDomain, setBusinessDomain] = useState(DEFAULT_BUSINESS_DOMAIN)
  const [writingPurpose, setWritingPurpose] = useState(DEFAULT_WRITING_PURPOSE)
  const [referenceScope, setReferenceScope] = useState(REFERENCE_SCOPE_OPTIONS[0])
  const [targetWords, setTargetWords] = useState(1200)
  const [temperature, setTemperature] = useState(0.7)
  const [exportFormat, setExportFormat] = useState('docx')
  const [additionalRequirements, setAdditionalRequirements] = useState('')
  const [validationErrors, setValidationErrors] = useState({})

  const [loading, setLoading] = useState(false)
  const [requestStartedAt, setRequestStartedAt] = useState(null)
  const [stopRequested, setStopRequested] = useState(false)
  const [errorModal, setErrorModal] = useState(null)
  const [pendingWordExportArticle, setPendingWordExportArticle] = useState(null)

  const [result, setResult] = useState(null)
  const [generationOrigin, setGenerationOrigin] = useState('articleGen')
  const [editorArticle, setEditorArticle] = useState(null)
  const [editorReturnPage, setEditorReturnPage] = useState('articleGen')
  const [exporting, setExporting] = useState(false)
  const [outputDir, setOutputDir] = useState('')
  const [outputDirDraft, setOutputDirDraft] = useState('')
  const [outputDirSaving, setOutputDirSaving] = useState(false)
  const [outputDirOpening, setOutputDirOpening] = useState(false)
  const [defaultSettingsSaving, setDefaultSettingsSaving] = useState(false)

  const [logEntries, setLogEntries] = useState([])
  const [logLoading, setLogLoading] = useState(false)
  const [showLog, setShowLog] = useState(false)

  const [libraryStats, setLibraryStats] = useState(null)
  const [showLibDetail, setShowLibDetail] = useState(false)

  const fileInputRef = useRef(null)
  const stopRequestedRef = useRef(false)
  const taskContextTouchedRef = useRef(false)
  const [fileName, setFileName] = useState('')
  const genreOptions = genreCatalog?.genres || []
  const catalogReady = !!genreCatalog && businessDomainOptions.length > 0 && !catalogLoading && !catalogError
  const defaultDocumentType = genreOptions.find(genre => genre.production_ready)?.document_type || ''

  const getDefaultTaskParams = (overrides = {}) => ({
    documentType,
    scene,
    level,
    orgType,
    writingPurpose,
    wordCount: normalizeTargetWords(overrides.wordCount ?? targetWords),
    referenceScope,
    ...overrides,
    businessDomain: normalizeBusinessDomain(overrides.businessDomain ?? businessDomain, businessDomainOptions),
  })

  const handleFileUpload = (e) => {
    if (!catalogReady) return
    const file = e.target.files?.[0]
    if (!file) return
    setFileName(file.name)
    const reader = new FileReader()
    reader.onload = (evt) => {
      try {
        const data = new Uint8Array(evt.target.result)
        const workbook = XLSX.read(data, { type: 'array' })
        const sheet = workbook.Sheets[workbook.SheetNames[0]]
        const json = XLSX.utils.sheet_to_json(sheet, { header: 1, defval: '' })
        const defaults = getDefaultTaskParams()
        const result = parseBatchExcelRows(json, defaults, {
          genreCatalog,
          scene: sceneOptions.map(option => option.name),
          level: LEVEL_OPTIONS,
          orgType: ORG_TYPE_OPTIONS,
          businessDomain: businessDomainOptions,
          writingPurpose: WRITING_PURPOSE_OPTIONS,
          referenceScope: REFERENCE_SCOPE_OPTIONS,
        })
        const parsed = result.tasks.map(task => createUiBatchTask(defaults, normalizeUiTaskGenre(task, genreCatalog)))
        if (parsed.length > 0) {
          taskContextTouchedRef.current = true
          setRows(parsed)
        }
        setErrorModal({
          type: result.errors.length ? 'error' : 'success',
          title: 'Excel导入完成',
          message: formatBatchImportSummary(result),
        })
      } catch (err) {
        setErrorModal({ type: 'error', title: 'Excel解析失败', message: `解析文件失败：${err.message}` })
      }
    }
    reader.readAsArrayBuffer(file)
    // Reset input so same file can be re-selected
    e.target.value = ''
  }

  const triggerFileInput = () => {
    if (!catalogReady) return
    fileInputRef.current?.click()
  }

  // Batch progress tracking
  const [batchProgress, setBatchProgress] = useState({ current: 0, total: 0 })

  const loadCatalogs = async () => {
    setCatalogLoading(true)
    setCatalogError('')
    setGenreCatalog(null)
    setSceneOptions([])
    setBusinessDomainOptions([])
    try {
      const { catalog, scenes, businessDomains } = await loadWritingCatalogs(getGenreCatalog, getSceneTaxonomy, getBusinessDomainCatalog)
      setGenreCatalog(catalog)
      setSceneOptions(scenes)
      setBusinessDomainOptions(businessDomains)
    } catch {
      setCatalogError('文种、写作场景或业务领域目录加载失败，请重新加载后再生成。')
    } finally {
      setCatalogLoading(false)
    }
  }

  useEffect(() => {
    if (!loading || typeof window === 'undefined') return
    const handleBeforeUnload = (event) => {
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', handleBeforeUnload)
    return () => window.removeEventListener('beforeunload', handleBeforeUnload)
  }, [loading])

  useEffect(() => {
    if (!genreCatalog || !businessDomainOptions.length) return
    setRows(currentRows => currentRows.map(row => normalizeUiTaskBusinessDomain(normalizeRestoredTaskGenre({
      ...row, documentType: row.documentType || (row.requiresGenreSelection ? '' : defaultDocumentType),
    }, genreCatalog, sceneOptions), businessDomainOptions)))
    const normalizedDefaults = normalizeRestoredTaskGenre({ documentType: documentType || defaultDocumentType, scene }, genreCatalog, sceneOptions)
    setDocumentType(normalizedDefaults.documentType)
    setScene(normalizedDefaults.scene)
    setBusinessDomain(current => normalizeBusinessDomain(current, businessDomainOptions))
  }, [genreCatalog, businessDomainOptions])

  useEffect(() => {
    loadCatalogs()
    getLibraryStats()
      .then(res => setLibraryStats(res))
      .catch(() => {})
    loadGenerationDefaults()
    loadOutputDir()
    loadLog()
  }, [])

  useEffect(() => {
    if (!articleGenDraft) return
    taskContextTouchedRef.current = true
    const restoredGenre = normalizeRestoredTaskGenre({
      documentType: articleGenDraft.documentType || articleGenDraft.document_type || (articleGenDraft.isHistoryReplay ? '' : defaultDocumentType),
      requiresGenreSelection: articleGenDraft.isHistoryReplay && !articleGenDraft.documentType && !articleGenDraft.document_type,
      scene: articleGenDraft.scene,
    }, genreCatalog, catalogReady ? sceneOptions : undefined)
    const draftDefaults = getDefaultTaskParams({
      ...restoredGenre,
      level: articleGenDraft.level || LEVEL_OPTIONS[0],
      orgType: articleGenDraft.orgType || articleGenDraft.org_type || ORG_TYPE_OPTIONS[0],
      businessDomain: articleGenDraft.businessDomain || articleGenDraft.business_domain || DEFAULT_BUSINESS_DOMAIN,
      writingPurpose: articleGenDraft.writingPurpose || articleGenDraft.writing_purpose || DEFAULT_WRITING_PURPOSE,
      wordCount: articleGenDraft.targetWords || articleGenDraft.target_length || 1200,
      referenceScope: articleGenDraft.referenceScope || articleGenDraft.reference_scope || REFERENCE_SCOPE_OPTIONS[0],
    })
    setRows([createUiBatchTask(draftDefaults, { title: articleGenDraft.title || '', count: articleGenDraft.count || 1 })])
    setScene(restoredGenre.scene)
    setArticleMode('official_draft')
    setAnalysisWrapperEnabled(!!articleGenDraft.analysisWrapper)
    setAppreciationScope(articleGenDraft.appreciationScope || 'single_deep')
    setAppreciationCount(articleGenDraft.appreciationCount || 3)
    setDocumentType(restoredGenre.documentType)
    setLevel(articleGenDraft.level || LEVEL_OPTIONS[0])
    setOrgType(articleGenDraft.orgType || articleGenDraft.org_type || ORG_TYPE_OPTIONS[0])
    setBusinessDomain(draftDefaults.businessDomain)
    setWritingPurpose(articleGenDraft.writingPurpose || articleGenDraft.writing_purpose || DEFAULT_WRITING_PURPOSE)
    setReferenceScope(articleGenDraft.referenceScope || articleGenDraft.reference_scope || REFERENCE_SCOPE_OPTIONS[0])
    setTargetWords(articleGenDraft.targetWords || 1200)
    setExportFormat(articleGenDraft.exportFormat || 'docx')
    setAdditionalRequirements(articleGenDraft.style || '')
    setValidationErrors({})
    setResult(null)
    setErrorModal(null)
    onArticleGenDraftConsumed?.()
  }, [articleGenDraft, onArticleGenDraftConsumed])

  useEffect(() => {
    if (!historyEditorArticle) return
    setEditorArticle(historyEditorArticle)
    setEditorReturnPage('history')
    onHistoryEditorArticleConsumed?.()
  }, [historyEditorArticle, onHistoryEditorArticleConsumed])

  const loadOutputDir = async () => {
    try {
      const res = await getGenerationOutputDir()
      if (res.output_dir) {
        setOutputDir(res.output_dir)
        setOutputDirDraft(res.output_dir)
      }
    } catch {
      // ignore
    }
  }

  const normalizeDefaultSceneForUi = (value) => {
    return normalizeScene(value) || ''
  }

  const normalizeDefaultExportFormatForUi = (value) => {
    return value === 'TXT' ? 'txt' : 'docx'
  }

  const buildDefaultSettingsPayload = () => ({
    scene: scene || '自动识别场景',
    article_mode: 'official_draft',
    target_words: parseInt(targetWords, 10),
    temperature,
    export_format: exportFormat === 'txt' ? 'TXT' : 'Word',
  })

  const loadGenerationDefaults = async () => {
    try {
      const settings = await getGenerationDefaultSettings()
      if (taskContextTouchedRef.current) return
      setScene(normalizeDefaultSceneForUi(settings.scene))
      setArticleMode('official_draft')
      setTargetWords(settings.target_words || 1200)
      setTemperature(settings.temperature ?? 0.7)
      setExportFormat(normalizeDefaultExportFormatForUi(settings.export_format))
      setRows(currentRows => {
        if (currentRows.length !== 1 || currentRows[0].title.trim()) return currentRows
        return [{ ...currentRows[0], scene: normalizeDefaultSceneForUi(settings.scene), wordCount: normalizeTargetWords(settings.target_words || 1200) }]
      })
    } catch {
      // Keep page defaults when saved defaults cannot be read.
    }
  }

  const handleSaveDefaultSettings = async () => {
    setDefaultSettingsSaving(true)
    try {
      await saveGenerationDefaultSettings(buildDefaultSettingsPayload())
      setErrorModal({ type: 'success', title: '保存成功', message: '全局参数配置已保存' })
    } catch (err) {
      setErrorModal({
        type: 'error',
        title: '保存失败',
        message: `全局参数配置保存失败：${err.message || '未知原因'}`,
      })
    } finally {
      setDefaultSettingsSaving(false)
    }
  }

  const loadLog = async () => {
    setLogLoading(true)
    try {
      const res = await getGenerationLog()
      setLogEntries(res.entries || [])
      if (res.output_dir) {
        setOutputDir(res.output_dir)
        setOutputDirDraft(current => current || res.output_dir)
      }
    } catch {
      // ignore
    } finally {
      setLogLoading(false)
    }
  }

  const addRow = () => {
    taskContextTouchedRef.current = true
    setRows(currentRows => [...currentRows, createUiBatchTask(getDefaultTaskParams())])
  }

  const removeRow = (index) => {
    setRows(rows.filter((_, i) => i !== index))
  }

  const updateRow = (index, field, value) => {
    taskContextTouchedRef.current = true
    setRows(currentRows => currentRows.map((row, i) => {
      if (i !== index) return row
      return { ...row, [field]: value }
    }))
  }

  const toggleRowExpanded = (index) => {
    updateRow(index, 'expanded', !rows[index].expanded)
  }

  const applyDefaultsToAllRows = () => {
    const defaults = getDefaultTaskParams()
    setRows(currentRows => currentRows.map(row => ({ ...row, ...defaults })))
    setErrorModal({ type: 'success', title: '已应用默认参数', message: '当前默认参数已应用到全部批量任务。' })
  }

  const updateTaskStatus = (taskId, status, details = {}) => {
    setRows(currentRows => currentRows.map(row => (
      row.id === taskId ? { ...row, status, ...details } : row
    )))
  }

  const normalizeTargetWords = (value) => {
    const parsed = parseInt(value, 10)
    if (Number.isNaN(parsed)) return 1200
    return Math.max(500, Math.min(5000, parsed))
  }

  const handleGenerate = async () => {
    if (!catalogReady) {
      setErrorModal({ type: 'error', title: '目录暂不可用', message: catalogError || '文种、写作场景或业务领域目录加载中，请稍后再生成。' })
      return
    }
    const requestRows = activePage === 'batchTask' ? rows : rows.slice(0, 1)
    const validRows = requestRows.filter(r => r.title.trim())
    if (validRows.length === 0) return

    const invalid = validRows.map(row => ({ row, message: productionTaskError({
      document_type: row.documentType, scene: row.scene, count: row.count,
    }, catalogReady ? genreCatalog : null, exportFormat) })).find(item => item.message)
    if (invalid) {
      setErrorModal({ type: 'error', title: '请检查写作参数', message: `${invalid.row.title}：${invalid.message}` })
      return
    }

    const requestOrigin = activePage === 'batchTask' ? 'batchTask' : 'articleGen'
    setGenerationOrigin(requestOrigin)
    onNavigate?.('genResult')

    stopRequestedRef.current = false
    setStopRequested(false)
    setErrorModal(null)
    setResult(null)
    setLoading(true)
    onGenerationPendingChange?.(true)
    setBatchProgress({ current: 0, total: validRows.length })
    const validTaskIds = new Set(validRows.map(row => row.id))
    setRows(currentRows => currentRows.map(row => (
      validTaskIds.has(row.id) ? { ...row, status: 'pending', errorMessage: '', articles: [] } : row
    )))

    const allArticles = []
    let current = 0
    let hasError = false
    let consecutiveMaterialMissingCount = 0
    let stoppedAfterCurrent = false

    try {
      for (const row of validRows) {
        if (stopRequestedRef.current) {
          stoppedAfterCurrent = true
          setRows(currentRows => currentRows.map(task => (
            validTaskIds.has(task.id) && task.status === 'pending'
              ? { ...task, status: 'stopped' }
              : task
          )))
          setErrorModal({
            type: 'success',
            title: '已停止生成',
            message: '已在当前文章完成后停止本轮批量生成。',
          })
          break
        }

        current++
        setBatchProgress({ current, total: validRows.length })
        updateTaskStatus(row.id, 'generating')

        try {
          const payload = buildTaskGenerationPayload(
            {
              title: row.title,
              document_type: row.documentType,
              scene: row.scene,
              level: row.level,
              org_type: row.orgType,
              business_domain: row.businessDomain,
              writing_purpose: row.writingPurpose,
              target_length: normalizeTargetWords(row.wordCount),
              reference_mode: referenceModeFromScope(row.referenceScope),
              style: additionalRequirements.trim() || undefined,
              count: row.count,
            },
            {
              temperature,
              export_format: exportFormat,
            },
            genreCatalog,
            businessDomainOptions,
          )
          setRequestStartedAt(Date.now())
          const res = await generateArticle(payload)
          const returnedQualityTestDraft = res?.articles?.some(isQualityTestDraft)
          updateTaskStatus(row.id, returnedQualityTestDraft ? 'quality_test' : 'completed', {
            articles: Array.isArray(res?.articles) ? res.articles : [],
            errorMessage: '',
          })
          consecutiveMaterialMissingCount = 0
          if (res && res.articles) {
            allArticles.push(...res.articles)
          }
          if (res && res.output_dir) {
            setOutputDir(res.output_dir)
            setOutputDirDraft(res.output_dir)
          }
        } catch (err) {
          const message = getErrorMessage(err) || '生成失败'
          if (isPaymentRequiredError(err)) {
            hasError = true
            updateTaskStatus(row.id, 'failed', { errorMessage: message })
            setErrorModal({
              type: 'error',
              title: '模型接口余额不足',
              message: '模型接口余额不足或计费不可用，本轮批量任务已停止。请检查 API 余额或密钥。',
            })
            break
          }

          if (isMaterialMissingError(err)) {
            updateTaskStatus(row.id, 'failed', { errorMessage: message })
            consecutiveMaterialMissingCount += 1
            const reachedMaterialMissingLimit = consecutiveMaterialMissingCount >= 3
            setErrorModal({
              type: 'error',
              title: reachedMaterialMissingLimit ? '批量任务已停止' : `第 ${current} 项资料库素材不足，已跳过`,
              message: reachedMaterialMissingLimit
                ? '已连续 3 个标题因资料库素材不足无法生成，本轮批量任务已停止。请补充相关资料后再继续。'
                : articleMode === 'document_mode'
                  ? '资料库素材不足，无法按文种生成。已跳过当前标题并继续后续任务。'
                  : '资料库素材不足，无法生成。已跳过当前标题并继续后续任务。',
              hasMaterialHint: true,
            })
            if (reachedMaterialMissingLimit) {
              hasError = true
              break
            }
            continue
          }

          hasError = true
          updateTaskStatus(row.id, 'failed', { errorMessage: message })
          setErrorModal({
            type: 'error',
            title: `第 ${current} 项生成失败: ${row.title}`,
            message,
          })
          break
        } finally {
          setRequestStartedAt(null)
        }
      }

      if (!hasError || stoppedAfterCurrent || allArticles.length > 0) {
        setResult({ articles: allArticles })
        loadLog()
        setShowLog(true)
      }

      if (requestOrigin === 'batchTask') {
        onNavigate?.('batchTask')
      } else if (allArticles[0]) {
        setEditorArticle(allArticles[0])
        setEditorReturnPage('articleGen')
        onNavigate?.('editor')
      } else {
        onNavigate?.(requestOrigin)
      }
    } finally {
      if (hasError || stoppedAfterCurrent) {
        setRows(currentRows => currentRows.map(task => (
          validTaskIds.has(task.id) && task.status === 'pending'
            ? { ...task, status: 'stopped' }
            : task
        )))
      }
      stopRequestedRef.current = false
      setStopRequested(false)
      setLoading(false)
      onGenerationPendingChange?.(false)
    }
  }

  const handleStopGenerate = () => {
    if (!loading || stopRequestedRef.current) return
    stopRequestedRef.current = true
    setStopRequested(true)
    setErrorModal({
      type: 'success',
      title: '停止请求已收到',
      message: '已收到停止请求，当前文章生成完成后将停止后续任务。',
    })
  }

  const handleSingleGenerate = () => {
    const title = rows[0]?.title?.trim() || ''
    if (!title) {
      setValidationErrors({ title: '请填写文章标题' })
      return
    }
    setValidationErrors({})
    handleGenerate()
  }

  const handleSaveOutputDir = async () => {
    setOutputDirSaving(true)
    try {
      const res = await saveGenerationOutputDir(outputDirDraft)
      setOutputDir(res.output_dir || outputDirDraft)
      setOutputDirDraft(res.output_dir || outputDirDraft)
      setErrorModal({ type: 'success', title: '保存成功', message: '输出目录已保存' })
    } catch (err) {
      setErrorModal({
        type: 'error',
        title: '保存失败',
        message: `输出目录保存失败：${err.message || '未知原因'}`,
      })
    } finally {
      setOutputDirSaving(false)
    }
  }

  const handleOpenOutputDir = async () => {
    setOutputDirOpening(true)
    try {
      const res = await openGenerationOutputDir(outputDirDraft)
      if (!res?.success) {
        throw new Error(res?.message || '未知原因')
      }
      setErrorModal({ type: 'success', title: '打开成功', message: '已打开输出目录' })
    } catch (err) {
      setErrorModal({
        type: 'error',
        title: '打开失败',
        message: `打开输出目录失败：${err.message || '未知原因'}`,
      })
    } finally {
      setOutputDirOpening(false)
    }
  }

  const handleOpenGeneratedFile = async (entryId) => {
    setExporting(true)
    try {
      const response = await openGenerationFile(entryId)
      if (!response?.success) throw new Error(response?.message || '打开请求未完成')
      setErrorModal({ type: 'success', title: '文件已打开', message: '已使用系统默认应用打开真实导出文件。' })
    } catch (err) {
      setErrorModal({ type: 'error', title: '打开失败', message: err.message || '暂时无法打开该文件。' })
    } finally {
      setExporting(false)
    }
  }

  const copyTextWithFallback = async (text) => {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
      return
    }
    const textarea = document.createElement('textarea')
    textarea.value = text
    textarea.setAttribute('readonly', '')
    textarea.style.position = 'fixed'
    textarea.style.left = '-9999px'
    document.body.appendChild(textarea)
    textarea.select()
    const copied = document.execCommand('copy')
    document.body.removeChild(textarea)
    if (!copied) throw new Error('copy failed')
  }

  const handleCopyDocumentModeDraft = async (article) => {
    try {
      if (!await verifyFullArticleContent(article)) throw new Error('正文已变化，请重新核验')
      const text = fullArticleCopyText(article) ?? buildDocumentModeDraftText(article)
      if (!text.trim()) throw new Error('empty draft')
      await copyTextWithFallback(text)
      setErrorModal({ type: 'success', title: '复制成功', message: '文稿正文已复制到剪贴板。' })
    } catch {
      setErrorModal({ type: 'error', title: '复制失败', message: '复制失败，请手动复制' })
    }
  }

  const handleExportFullArticleTxt = async (article) => {
    try {
      const blob = await exportFullArticleTxt(articleMarkdown(article))
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `${article.title || '文章'}.txt`
      document.body.appendChild(link)
      link.click()
      link.remove()
      URL.revokeObjectURL(url)
    } catch (error) {
      setErrorModal({ type: 'error', title: 'TXT 导出失败', message: error.message })
    }
  }

  const handleRequestExportDocumentModeWord = (article) => {
    setPendingWordExportArticle(article)
  }

  const handleCancelExportDocumentModeWord = () => {
    setPendingWordExportArticle(null)
  }

  const handleConfirmExportDocumentModeWord = async () => {
    const article = pendingWordExportArticle
    if (!article) return
    setExporting(true)
    try {
      if (!await verifyFullArticleContent(article)) throw new Error('正文已变化，请重新核验')
      const blob = isFullArticle(article)
        ? await exportWord(article.title || '', articleMarkdown(article), 'full-article/v1')
        : await exportDocumentModeWord({
        title: article.title || '',
        sections: getDocumentModeDraftSections(article),
      })
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = getDocumentModeDraftFilename(article.title)
      document.body.appendChild(link)
      link.click()
      document.body.removeChild(link)
      URL.revokeObjectURL(url)
      setPendingWordExportArticle(null)
      setErrorModal({
        type: 'success',
        title: 'Word 导出完成',
        message: `${getDocumentModeDraftFilename(article.title)} 已生成并开始下载。`,
      })
    } catch (err) {
      setPendingWordExportArticle(null)
      setErrorModal({ type: 'error', title: '导出失败', message: err.message || 'Word导出失败' })
    } finally {
      setExporting(false)
    }
  }

  const formatTime = (iso) => {
    try { return new Date(iso).toLocaleString() } catch { return iso }
  }

  const handleRerunFromLog = (entry) => {
    taskContextTouchedRef.current = true
    const restoredDomain = normalizeBusinessDomain(entry.business_domain || DEFAULT_BUSINESS_DOMAIN, businessDomainOptions)
    const restoredGenre = normalizeRestoredTaskGenre({ documentType: entry.document_type || '', requiresGenreSelection: !entry.document_type, scene: entry.scene }, genreCatalog, catalogReady ? sceneOptions : undefined)
    setRows([createUiBatchTask({
      ...restoredGenre,
      level: entry.level || LEVEL_OPTIONS[0],
      orgType: entry.org_type || ORG_TYPE_OPTIONS[0],
      businessDomain: restoredDomain,
      writingPurpose: entry.writing_purpose || DEFAULT_WRITING_PURPOSE,
      wordCount: entry.target_words || 1200,
      referenceScope: entry.reference_scope || REFERENCE_SCOPE_OPTIONS[0],
    }, { title: entry.request_title || entry.title || '', count: entry.count || 1 })])
    setScene(restoredGenre.scene)
    setArticleMode('official_draft')
    setAnalysisWrapperEnabled(!!entry.analysis_wrapper_enabled)
    setAppreciationScope(entry.appreciation_scope || entry.appreciation?.appreciation_scope || 'single_deep')
    setAppreciationCount(entry.appreciation_count || entry.appreciation?.appreciation_count || 3)
    setDocumentType(restoredGenre.documentType)
    setLevel(entry.level || LEVEL_OPTIONS[0])
    setOrgType(entry.org_type || ORG_TYPE_OPTIONS[0])
    setBusinessDomain(restoredDomain)
    setWritingPurpose(entry.writing_purpose || DEFAULT_WRITING_PURPOSE)
    setReferenceScope(entry.reference_scope || REFERENCE_SCOPE_OPTIONS[0])
    setTargetWords(entry.target_words || 1200)
    setExportFormat(entry.export_format || 'docx')
    setAdditionalRequirements(entry.style || '')
    setResult(null)
    setErrorModal(null)
  }

  const handleOpenEditor = (article, returnPage = activePage === 'batchTask' ? 'batchTask' : 'articleGen') => {
    if (!article) return
    setEditorArticle(article)
    setEditorReturnPage(returnPage)
    onNavigate?.('editor')
  }

  const handleBatchDefaultChange = (field, value) => {
    taskContextTouchedRef.current = true
    const setters = {
      documentType: setDocumentType,
      scene: setScene,
      level: setLevel,
      orgType: setOrgType,
      businessDomain: setBusinessDomain,
      writingPurpose: setWritingPurpose,
      wordCount: setTargetWords,
      referenceScope: setReferenceScope,
    }
    setters[field]?.(value)
  }

  const totalCount = rows
    .filter(row => row.title.trim())
    .reduce((sum, row) => sum + (Number.isInteger(row.count) && row.count > 0 ? row.count : 1), 0)

  const singleTask = rows[0] || createUiBatchTask(INITIAL_TASK_DEFAULTS)
  const singleTaskError = productionTaskError({ document_type: singleTask.documentType, scene: singleTask.scene, count: singleTask.count }, genreCatalog, exportFormat)
  const taskValidationErrors = rows.map(row => row.title.trim() && catalogReady
    ? productionTaskError({ document_type: row.documentType, scene: row.scene, count: row.count }, genreCatalog, exportFormat) : '')
  const primaryArticle = result?.articles?.[0] || null
  const art = primaryArticle
  const primaryArticleText = primaryArticle ? buildDocumentModeDraftText(primaryArticle) : ''
  const primaryReferences = getReferenceSources(primaryArticle)
  const currentRequestTask = rows[Math.max(0, batchProgress.current - 1)] || rows.find(row => row.title.trim())
  const workflowDialogs = (
    <>
      <Modal
        visible={!!errorModal}
        type={errorModal?.type}
        title={errorModal?.title}
        message={errorModal?.hasMaterialHint ? `${errorModal.message}\n\n提示：请前往「资料库」页面，上传当前主题的相关资料后再试。` : errorModal?.message}
        onClose={() => setErrorModal(null)}
      />
      <ConfirmDialog
        open={!!pendingWordExportArticle}
        tone="warning"
        title="导出前提示"
        description={WORD_EXPORT_RESPONSIBILITY_NOTICE}
        cancelText="取消"
        confirmText="我已知晓，继续导出"
        pending={exporting}
        onCancel={handleCancelExportDocumentModeWord}
        onConfirm={handleConfirmExportDocumentModeWord}
      />
    </>
  )

  if (activePage === 'genResult') {
    return (
      <div className="workflow-route">
        {workflowDialogs}
        <GenerationPage
          key={currentRequestTask?.id}
          loading={loading}
          requestTitle={currentRequestTask?.title}
          requestStartedAt={requestStartedAt}
          isBatch={generationOrigin === 'batchTask'}
          stopRequested={stopRequested}
          onReturn={() => onNavigate?.(generationOrigin)}
          onStop={generationOrigin === 'batchTask' ? handleStopGenerate : undefined}
        />
      </div>
    )
  }

  if (activePage === 'editor') {
    return (
      <div className="workflow-route">
        {workflowDialogs}
        <EditorPage
          key={editorArticle?.log_id || editorArticle?.id || editorArticle?.title || 'empty-editor'}
          article={editorArticle}
          exporting={exporting}
          onBack={() => onNavigate?.(editorReturnPage)}
          onCopy={handleCopyDocumentModeDraft}
          onExport={handleRequestExportDocumentModeWord}
          onExportTxt={handleExportFullArticleTxt}
          onSaved={setEditorArticle}
        />
      </div>
    )
  }

  if (activePage === 'batchTask') {
    return (
      <div className="workflow-route">
        {workflowDialogs}
        <BatchTaskPage
          rows={rows}
          loading={loading}
          defaults={{ documentType, scene, level, orgType, businessDomain, writingPurpose, wordCount: targetWords, referenceScope }}
          catalogReady={catalogReady}
          catalogLoading={catalogLoading}
          catalogError={catalogError}
          onReloadCatalogs={loadCatalogs}
          taskValidationErrors={taskValidationErrors}
          options={{
            genres: genreOptions,
            scenes: sceneOptions,
            level: LEVEL_OPTIONS,
            orgType: ORG_TYPE_OPTIONS,
            businessDomain: businessDomainOptions,
            writingPurpose: WRITING_PURPOSE_OPTIONS,
            referenceScope: REFERENCE_SCOPE_OPTIONS,
          }}
          totalCount={totalCount}
          fileInputRef={fileInputRef}
          fileName={fileName}
          additionalRequirements={additionalRequirements}
          exportFormat={exportFormat}
          onNavigate={(pageKey) => {
            if (pageKey === 'articleGen' && rows.length === 0) addRow()
            onNavigate?.(pageKey)
          }}
          onDefaultChange={handleBatchDefaultChange}
          onTaskChange={updateRow}
          onToggleTask={toggleRowExpanded}
          onAddTask={addRow}
          onRemoveTask={removeRow}
          onApplyDefaults={applyDefaultsToAllRows}
          onFileUpload={handleFileUpload}
          onImportExcel={triggerFileInput}
          onAdditionalRequirementsChange={setAdditionalRequirements}
          onExportFormatChange={setExportFormat}
          onGenerate={handleGenerate}
          onOpenArticle={article => handleOpenEditor(article, 'batchTask')}
        />
      </div>
    )
  }

  return (
    <div className="core-page write-article-page" data-page-key="articleGen">
      <Modal
        visible={!!errorModal}
        type={errorModal?.type}
        title={errorModal?.title}
        message={
          errorModal?.hasMaterialHint
            ? `${errorModal.message}\n\n提示：请前往「资料库」页面，上传当前主题的相关资料后再试。`
            : errorModal?.message
        }
        onClose={() => setErrorModal(null)}
      />

      <ConfirmDialog
        open={!!pendingWordExportArticle}
        tone="warning"
        title="导出前提示"
        description={WORD_EXPORT_RESPONSIBILITY_NOTICE}
        cancelText="取消"
        confirmText="我已知晓，继续导出"
        pending={exporting}
        onCancel={handleCancelExportDocumentModeWord}
        onConfirm={handleConfirmExportDocumentModeWord}
      />

      <div className="core-mode-tabs" role="tablist" aria-label="生成方式">
        <button type="button" className="core-mode-tab is-active" role="tab" aria-selected="true">
          单篇生成
        </button>
        <button
          type="button"
          className="core-mode-tab"
          role="tab"
          aria-selected="false"
          onClick={() => onNavigate?.('batchTask')}
        >
          批量生成
        </button>
      </div>

      <div className="write-workspace">
        <section className="core-panel write-form-panel" aria-labelledby="write-form-title">
          <div className="core-panel__heading core-panel__heading--muted">
            <div>
              <h2 id="write-form-title">写作要求</h2>
              <p>填写核心参数，系统将从本地资料库检索依据后生成文章。</p>
            </div>
          </div>

          <div className="write-form-fields">
            <FormControl
              label="文章标题"
              htmlFor="article-title"
              required
              error={validationErrors.title}
              className="write-field--full"
            >
              <input
                id="article-title"
                type="text"
                value={singleTask.title}
                onChange={(event) => {
                  updateRow(0, 'title', event.target.value)
                  if (validationErrors.title) setValidationErrors({})
                }}
                placeholder="请输入要写作的文章标题"
                disabled={loading}
              />
            </FormControl>

            <FormControl label="文种" htmlFor="article-document-type">
              <GenreSelect
                id="article-document-type"
                genres={genreOptions}
                value={singleTask.documentType}
                onChange={event => updateRow(0, 'documentType', event.target.value)}
                disabled={loading || !catalogReady}
              />
            </FormControl>

            <FormControl label="写作场景" htmlFor="article-scene">
              <SceneSelect id="article-scene" scenes={sceneOptions} value={singleTask.scene}
                onChange={event => updateRow(0, 'scene', event.target.value)} disabled={loading || !catalogReady} />
            </FormControl>

            <FormControl label="层级" htmlFor="article-level">
              <select
                id="article-level"
                value={singleTask.level}
                onChange={event => updateRow(0, 'level', event.target.value)}
                disabled={loading}
              >
                {LEVEL_OPTIONS.map(option => <option key={option} value={option}>{option}</option>)}
              </select>
            </FormControl>

            <FormControl label="单位类型" htmlFor="article-org-type">
              <select
                id="article-org-type"
                value={singleTask.orgType}
                onChange={event => updateRow(0, 'orgType', event.target.value)}
                disabled={loading}
              >
                {ORG_TYPE_OPTIONS.map(option => <option key={option} value={option}>{option}</option>)}
              </select>
            </FormControl>

            <FormControl label="业务领域" htmlFor="article-business-domain">
              <BusinessDomainSelect
                id="article-business-domain"
                items={businessDomainOptions}
                value={singleTask.businessDomain}
                onChange={event => updateRow(0, 'businessDomain', event.target.value)}
                disabled={loading || !catalogReady}
              />
            </FormControl>

            <FormControl label="写作目的" htmlFor="article-writing-purpose">
              <select
                id="article-writing-purpose"
                value={singleTask.writingPurpose}
                onChange={event => updateRow(0, 'writingPurpose', event.target.value)}
                disabled={loading}
              >
                {WRITING_PURPOSE_OPTIONS.map(option => <option key={option} value={option}>{option}</option>)}
              </select>
            </FormControl>

            <FormControl label="目标字数" htmlFor="article-target-words" className="write-field--full">
              <input
                id="article-target-words"
                type="number"
                min="500"
                max="5000"
                step="100"
                value={singleTask.wordCount}
                onChange={event => updateRow(0, 'wordCount', event.target.value)}
                onBlur={event => updateRow(0, 'wordCount', normalizeTargetWords(event.target.value))}
                disabled={loading}
              />
            </FormControl>

            <FormControl label="参考资料范围" htmlFor="article-reference-scope" className="write-field--full">
              <select
                id="article-reference-scope"
                value={singleTask.referenceScope}
                onChange={event => updateRow(0, 'referenceScope', event.target.value)}
                disabled={loading}
              >
                {REFERENCE_SCOPE_OPTIONS.map(option => <option key={option} value={option}>{option}</option>)}
              </select>
            </FormControl>

            <FormControl label="补充要求（可选）" htmlFor="article-extra-requirements" className="write-field--full">
              <textarea
                id="article-extra-requirements"
                value={additionalRequirements}
                onChange={event => setAdditionalRequirements(event.target.value)}
                placeholder="例如：突出责任落实和阵地管理，问题分析要客观克制"
                rows="3"
                disabled={loading}
              />
            </FormControl>
          </div>

          <div className="write-form-actions">
            {catalogError && <div role="alert">{catalogError} <Button onClick={loadCatalogs} disabled={catalogLoading}>重新加载</Button></div>}
            {catalogReady && singleTaskError && <p role="alert">{singleTaskError}</p>}
            <Button
              variant="primary"
              size="lg"
              loading={loading}
              disabled={!catalogReady || !!singleTaskError}
              onClick={handleSingleGenerate}
              className="write-generate-button"
            >
              {loading ? '正在生成文章' : '生成文章'}
            </Button>
            <p>生成会使用当前资料库中的真实材料；资料不足时系统将明确提示。</p>
          </div>
        </section>

        <section className="core-panel write-result-panel" aria-labelledby="write-result-title">
          <div className="core-panel__heading core-panel__heading--muted write-preview-heading">
            <div>
              <h2 id="write-result-title">文稿预览</h2>
              <p>文章生成后将在这里显示，可复制正文或导出 Word。</p>
            </div>
            {art && !isQualityTestDraft(art) && (
              <div className="gen-result-actions">
              <div className="core-panel__actions">
                <Button variant="secondary" size="sm" onClick={() => handleCopyDocumentModeDraft(art)}>
                  复制正文
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  loading={exporting}
                  onClick={() => handleRequestExportDocumentModeWord(art)}
                >
                  导出 Word
                </Button>
              </div>
              </div>
            )}
          </div>

          <div className="write-preview-body">
            {loading ? (
              <GenerationWaitingIndicator
                variant="compact"
                startedAt={requestStartedAt}
                onViewStatus={() => onNavigate?.('genResult')}
              />
            ) : !primaryArticle ? (
              <div className="core-state core-state--empty">
                <div className="core-state__mark" aria-hidden="true">文</div>
                <h3>文章生成后将在这里显示</h3>
                <p>填写左侧写作要求后开始生成。</p>
              </div>
            ) : (
              <div className="write-document-preview">
                <QualityTestDraftDetails art={primaryArticle} />
                <ReviewRequiredDraftDetails art={primaryArticle} />
                <article className="write-paper">
                  {primaryArticle.content_projection?.contract === 'full-article/v1' ? (
                    <FullArticleContent article={primaryArticle} />
                  ) : <>
                  <h3>{primaryArticle.title || primaryArticle.public_title || singleTask.title}</h3>
                  <div className="write-paper__content">
                    {primaryArticleText.split('\n').map((paragraph, index) => (
                      paragraph.trim()
                        ? <p key={`${paragraph.slice(0, 24)}-${index}`}>{paragraph.trim()}</p>
                        : null
                    ))}
                  </div>
                  </>}
                </article>

                <div className="write-reference-summary">
                  <h3>本次参考资料</h3>
                  {primaryReferences.length > 0 ? (
                    primaryReferences.map((source, index) => (
                      <div className="write-reference-item" key={`${source.reference_title || source.reference_filename || 'reference'}-${index}`}>
                        <strong>{source.reference_title || source.reference_filename || `参考资料 ${index + 1}`}</strong>
                        {source.reference_reason && <span>{source.reference_reason}</span>}
                      </div>
                    ))
                  ) : (
                    <p>本次结果未返回可展示的参考资料信息。</p>
                  )}
                </div>
              </div>
            )}
          </div>
        </section>
      </div>
    </div>
  )

}
