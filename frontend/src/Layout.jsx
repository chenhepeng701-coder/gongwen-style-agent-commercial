import React from 'react'
import AppSidebar from './components/AppSidebar'
import AppTopBar from './components/AppTopBar'
import Button from './components/Button'

const PRIMARY_NAV_ORDER = ['articleGen', 'library', 'history']
const COMMERCIAL_NAV_ITEMS = [
  { key: 'account', label: '账户中心' },
  { key: 'credits', label: '积分与额度' },
  { key: 'backup', label: '本地数据与备份' },
]
const COMMERCIAL_PAGE_KEYS = new Set(COMMERCIAL_NAV_ITEMS.map(item => item.key))

export default function Layout({ pages, activePage, onNavigate, topBarActions, children }) {
  const pageEntries = PRIMARY_NAV_ORDER
    .map(pageKey => pages[pageKey])
    .filter(page => page && !page.navHidden)
  const activePageConfig = pages[activePage] || pages.articleGen || pageEntries[0]
  const hideTopBar = activePageConfig?.hideTopBar === true

  const accountEntry = activePage === 'articleGen' ? (
    <Button
      variant="secondary"
      size="sm"
      onClick={() => onNavigate('account')}
    >
      账户中心
    </Button>
  ) : null

  const commercialTabs = COMMERCIAL_PAGE_KEYS.has(activePage) ? (
    <div className="commercial-tabs" role="navigation" aria-label="商业能力页面">
      {COMMERCIAL_NAV_ITEMS.map(item => (
        <button
          key={item.key}
          type="button"
          className={`commercial-tabs__item ${activePage === item.key ? 'is-active' : ''}`}
          onClick={() => onNavigate(item.key)}
          aria-current={activePage === item.key ? 'page' : undefined}
        >
          {item.label}
        </button>
      ))}
    </div>
  ) : null

  return (
    <div className="app-shell">
      <AppSidebar
        navigationItems={pageEntries}
        activePage={activePage}
        onNavigate={onNavigate}
        onOpenBackup={() => onNavigate('backup')}
        onOpenAccount={() => onNavigate('account')}
      />

      <div className={`app-shell__body ${hideTopBar ? 'app-shell__body--workflow' : ''}`}>
        {!hideTopBar && (
          <AppTopBar
            title={activePageConfig?.label || '公文写作助手'}
            description={activePageConfig?.description}
            tabs={commercialTabs}
            actions={topBarActions}
            accountEntry={accountEntry}
          />
        )}
        <main className={`main-content ${hideTopBar ? 'main-content--workflow' : ''}`}>
          <div className="app-page-content">{children}</div>
        </main>
      </div>

    </div>
  )
}
