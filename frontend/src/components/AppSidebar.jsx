import React from 'react'

const NAV_ICONS = {
  articleGen: '✎',
  library: '▤',
  history: '▱',
}

export default function AppSidebar({
  navigationItems,
  activePage,
  onNavigate,
  onOpenBackup,
  onOpenAccount,
}) {
  return (
    <aside className="app-sidebar" aria-label="应用导航">
      <div className="app-sidebar__brand">
        <span className="app-sidebar__logo" aria-hidden="true">文</span>
        <div className="app-sidebar__brand-copy">
          <div className="app-sidebar__brand-name">公文写作助手</div>
          <div className="app-sidebar__brand-subtitle">商业版 · 本地资料驱动</div>
        </div>
      </div>

      <div className="app-sidebar__section-label">工作空间</div>
      <nav className="app-sidebar__nav" aria-label="工作空间">
        {navigationItems.map((page) => (
          <button
            key={page.key}
            type="button"
            className={`app-sidebar__nav-item ${activePage === page.key ? 'is-active' : ''}`}
            onClick={() => onNavigate(page.key)}
            aria-current={activePage === page.key ? 'page' : undefined}
          >
            <span className="app-sidebar__nav-icon" aria-hidden="true">
              {NAV_ICONS[page.key] || '·'}
            </span>
            <span>{page.label}</span>
          </button>
        ))}
      </nav>

      <div className="app-sidebar__spacer" />

      <button type="button" className="app-sidebar__storage" onClick={onOpenBackup}>
        <span className="app-sidebar__storage-icon" aria-hidden="true">▤</span>
        <span className="app-sidebar__storage-copy">
          <strong>资料仅保存在本机</strong>
          <span>管理备份 →</span>
        </span>
      </button>

      <button type="button" className="app-sidebar__account" onClick={onOpenAccount}>
        <span className="app-sidebar__avatar" aria-hidden="true">占</span>
        <span className="app-sidebar__account-copy">
          <strong>本地用户</strong>
          <span>账户功能未接入</span>
        </span>
        <span className="app-sidebar__chevron" aria-hidden="true">›</span>
      </button>
    </aside>
  )
}
