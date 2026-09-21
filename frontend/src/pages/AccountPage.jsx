import React, { useState } from 'react'
import Button from '../components/Button'
import FormControl from '../components/FormControl'
import StateDialog from '../components/StateDialog'
import StatusTag from '../components/StatusTag'

export default function AccountPage({ onNavigate }) {
  const [showBoundary, setShowBoundary] = useState(false)

  return (
    <div className="capability-page account-page">
      <h1>账户中心</h1>
      <p className="page-desc">查看当前本地使用状态和客户端设置入口</p>

      <section className="capability-card account-summary" aria-labelledby="account-summary-title">
        <div className="account-summary__identity" aria-hidden="true">本</div>
        <div className="account-summary__copy">
          <div className="capability-card__eyebrow">当前使用身份</div>
          <h2 id="account-summary-title">本地用户</h2>
          <p>当前客户端未接入注册、登录或云端账户体系。</p>
          <div className="capability-tags">
            <StatusTag tone="neutral">本地模式</StatusTag>
            <StatusTag tone="warning">账户功能未接入</StatusTag>
          </div>
        </div>
        <Button variant="secondary" onClick={() => setShowBoundary(true)}>账户说明</Button>
      </section>

      <div className="capability-grid capability-grid--two">
        <section className="capability-card">
          <div className="capability-card__header">
            <div>
              <h2>账户状态</h2>
              <p>仅展示当前产品边界，不生成或保存模拟账户。</p>
            </div>
            <StatusTag tone="warning">未接入</StatusTag>
          </div>
          <div className="capability-fields">
            <FormControl label="用户类型" description="本地占位身份，不代表已注册账号">
              <div className="capability-field">本地用户</div>
            </FormControl>
            <FormControl label="登录状态" description="当前没有可验证的云端登录会话">
              <div className="capability-field">账户功能未接入</div>
            </FormControl>
          </div>
        </section>

        <section className="capability-card">
          <div className="capability-card__header">
            <div>
              <h2>本地运行说明</h2>
              <p>资料处理和写作工作区继续在当前电脑中运行。</p>
            </div>
            <StatusTag tone="success">本地运行</StatusTag>
          </div>
          <ul className="capability-list">
            <li><span>本地资料</span><strong>保存在当前电脑</strong></li>
            <li><span>账户数据</span><strong>未创建</strong></li>
            <li><span>云端会员状态</span><strong>未接入</strong></li>
          </ul>
        </section>
      </div>

      <section className="capability-card">
        <div className="capability-card__header capability-card__header--settings">
          <div>
            <h2>设置入口</h2>
            <p>继续使用项目中已经存在的设置页面。</p>
          </div>
        </div>
        <div className="settings-entry-list">
          <div className="settings-entry">
            <div><strong>模型配置</strong><span>查看当前已有的模型服务配置</span></div>
            <Button variant="secondary" onClick={() => onNavigate('model')}>打开设置</Button>
          </div>
          <div className="settings-entry">
            <div><strong>高级写作设置</strong><span>进入已有的提示词管理页面</span></div>
            <Button variant="secondary" onClick={() => onNavigate('prompt')}>打开设置</Button>
          </div>
        </div>
      </section>

      <StateDialog
        open={showBoundary}
        tone="info"
        title="账户能力边界"
        description="当前页面不包含注册、登录、会员等级或云端账户能力，也不会在浏览器本地存储中模拟这些状态。"
        confirmText="知道了"
        onConfirm={() => setShowBoundary(false)}
        onCancel={() => setShowBoundary(false)}
      />
    </div>
  )
}
