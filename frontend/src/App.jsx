import React, { useState, useRef } from 'react'
import Layout from './Layout'
import ConfirmDialog from './components/ConfirmDialog'
import LibraryPage from './pages/LibraryPage'
import ModelConfigPage from './pages/ModelConfigPage'
import ArticleGenPage from './pages/ArticleGenPage'
import HistoryPage from './pages/HistoryPage'
import PromptManagePage from './pages/PromptManagePage'
import OperationSuggestionsPage from './pages/OperationSuggestionsPage'
import AccountPage from './pages/AccountPage'
import CreditsPage from './pages/CreditsPage'
import BackupPage from './pages/BackupPage'

const PAGES = {
  operation: { key: 'operation', label: '写作场景建议', component: OperationSuggestionsPage, navHidden: true },
  library: {
    key: 'library',
    label: '资料库',
    description: '上传、查找和分类本地资料，在写文章时按需引用',
    component: LibraryPage,
  },
  model: { key: 'model', label: '模型配置', component: ModelConfigPage, navHidden: true },
  articleGen: {
    key: 'articleGen',
    label: '写文章',
    description: '从本地资料库中检索依据，生成可继续修改的公文初稿',
    component: ArticleGenPage,
  },
  batchTask: {
    key: 'batchTask',
    label: '批量生成',
    description: '添加任务或导入 Excel，使用现有生成能力依次提交并查看真实结果',
    component: ArticleGenPage,
    navHidden: true,
  },
  genResult: { key: 'genResult', label: '正在生成文章', component: ArticleGenPage, navHidden: true, hideTopBar: true },
  editor: { key: 'editor', label: '文稿编辑', component: ArticleGenPage, navHidden: true, hideTopBar: true },
  history: {
    key: 'history',
    label: '我的文稿',
    description: '集中查看已生成文章，复用写作参数或打开导出文件',
    component: HistoryPage,
  },
  prompt: { key: 'prompt', label: '高级设置 / 提示词管理', component: PromptManagePage, navHidden: true },
  account: {
    key: 'account',
    label: '账户中心',
    description: '查看当前本地使用状态和客户端设置入口',
    component: AccountPage,
    navHidden: true,
  },
  credits: {
    key: 'credits',
    label: '积分与额度',
    description: '商业能力入口预览，当前未接入积分与支付系统',
    component: CreditsPage,
    navHidden: true,
  },
  backup: {
    key: 'backup',
    label: '本地数据与备份',
    description: '了解本地数据范围和尚未开放的备份能力',
    component: BackupPage,
    navHidden: true,
  },
}

const ACTIVE_PAGE_STORAGE_KEY = 'gongwen_active_page'
const DEFAULT_ACTIVE_PAGE = 'articleGen'
const VALID_PAGE_KEYS = new Set(Object.keys(PAGES))
const TRANSIENT_PAGE_KEYS = new Set(['genResult', 'editor'])

function getInitialActivePage() {
  if (typeof window === 'undefined') return DEFAULT_ACTIVE_PAGE
  try {
    const savedPage = window.localStorage.getItem(ACTIVE_PAGE_STORAGE_KEY)
    return VALID_PAGE_KEYS.has(savedPage) && !TRANSIENT_PAGE_KEYS.has(savedPage) ? savedPage : DEFAULT_ACTIVE_PAGE
  } catch (err) {
    console.warn('Failed to read active page from localStorage:', err)
    return DEFAULT_ACTIVE_PAGE
  }
}

function saveActivePage(pageKey) {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.setItem(ACTIVE_PAGE_STORAGE_KEY, pageKey)
  } catch (err) {
    console.warn('Failed to save active page to localStorage:', err)
  }
}

export default function App() {
  const [activePage, setActivePage] = useState(getInitialActivePage)
  const [articleGenDraft, setArticleGenDraft] = useState(null)
  const [historyEditorArticle, setHistoryEditorArticle] = useState(null)
  const generationPending = useRef(false)
  const [navigationBlocked, setNavigationBlocked] = useState(false)

  const ActiveComponent = (PAGES[activePage] || PAGES[DEFAULT_ACTIVE_PAGE]).component

  const handleNavigate = (pageKey) => {
    const nextPage = VALID_PAGE_KEYS.has(pageKey) ? pageKey : DEFAULT_ACTIVE_PAGE
    if (generationPending.current && PAGES[nextPage].component !== ArticleGenPage) {
      setNavigationBlocked(true)
      return
    }
    setActivePage(nextPage)
    saveActivePage(nextPage)
  }

  const handleGenerationPendingChange = (pending) => {
    generationPending.current = pending
    if (!pending) setNavigationBlocked(false)
  }

  const handleReuseGenerationEntry = (entry) => {
    setArticleGenDraft({
      isHistoryReplay: true,
      title: entry.request_title || entry.title || '',
      count: entry.count || 1,
      scene: entry.scene || '',
      articleMode: entry.article_mode || 'draft_article',
      documentType: entry.document_type || '',
      level: entry.level || '',
      orgType: entry.org_type || '',
      businessDomain: entry.business_domain || '',
      writingPurpose: entry.writing_purpose || '',
      referenceScope: entry.reference_scope || '',
      targetWords: entry.target_words || 1200,
      exportFormat: entry.export_format || 'docx',
      style: entry.style ?? '',
    })
    handleNavigate('articleGen')
  }

  const handleUseTopicSuggestion = (draft) => {
    setArticleGenDraft(draft)
    handleNavigate('articleGen')
  }

  const handleOpenGenerationEntry = (entry) => {
    const article = entry?.articles?.[0]
    if (!article) return
    setHistoryEditorArticle({ ...article, log_id: entry.id })
    handleNavigate('editor')
  }

  return (
    <Layout pages={PAGES} activePage={activePage} onNavigate={handleNavigate}>
      <ActiveComponent
        activePage={activePage}
        onNavigate={handleNavigate}
        onGenerationPendingChange={handleGenerationPendingChange}
        articleGenDraft={articleGenDraft}
        onArticleGenDraftConsumed={() => setArticleGenDraft(null)}
        historyEditorArticle={historyEditorArticle}
        onHistoryEditorArticleConsumed={() => setHistoryEditorArticle(null)}
        onReuseGenerationEntry={handleReuseGenerationEntry}
        onOpenGenerationEntry={handleOpenGenerationEntry}
        onUseTopicSuggestion={handleUseTopicSuggestion}
      />
      <ConfirmDialog
        open={navigationBlocked}
        tone="warning"
        title="文章正在生成"
        description="文章正在生成。为避免生成结果丢失，请等待生成完成后再离开当前写作工作区。"
        cancelText="继续等待"
        confirmText="查看生成状态"
        onCancel={() => setNavigationBlocked(false)}
        onConfirm={() => {
          setNavigationBlocked(false)
          handleNavigate('genResult')
        }}
      />
    </Layout>
  )
}
