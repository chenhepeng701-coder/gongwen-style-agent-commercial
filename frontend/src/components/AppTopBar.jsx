import React from 'react'

export default function AppTopBar({ title, description, tabs, actions, accountEntry }) {
  return (
    <header className="app-topbar">
      <div className="app-topbar__heading">
        <h1 className="app-topbar__title">{title}</h1>
        {description && <p className="app-topbar__description">{description}</p>}
      </div>
      {tabs && <div className="app-topbar__tabs">{tabs}</div>}
      <div className="app-topbar__spacer" />
      {actions && <div className="app-topbar__actions">{actions}</div>}
      {accountEntry && <div className="app-topbar__account">{accountEntry}</div>}
    </header>
  )
}
