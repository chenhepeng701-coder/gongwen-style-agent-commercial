import React, { useState } from 'react'
import Button from '../components/Button'
import FormControl from '../components/FormControl'
import StateDialog from '../components/StateDialog'
import StatusTag from '../components/StatusTag'

const FUTURE_CAPABILITIES = [
  ['额度展示', '未来用于展示经过真实账户服务返回的可用额度。'],
  ['使用明细', '未来用于展示真实模型调用产生的计量记录。'],
  ['充值与套餐', '未来需要支付、订单与计费能力接入后开放。'],
]

export default function CreditsPage() {
  const [showNotice, setShowNotice] = useState(false)

  return (
    <div className="capability-page credits-page">
      <h1>积分与额度</h1>
      <p className="page-desc">商业能力入口预览，当前未接入积分与支付系统</p>

      <section className="capability-card credits-hero">
        <div>
          <div className="capability-card__eyebrow">商业能力入口</div>
          <h2>积分系统尚未开放</h2>
          <p>当前没有可查询的积分余额、额度、套餐、订单或消费记录。</p>
        </div>
        <StatusTag tone="warning">未开放</StatusTag>
      </section>

      <div className="capability-grid capability-grid--two">
        <section className="capability-card credits-balance-placeholder">
          <div className="capability-card__header">
            <div>
              <h2>当前额度</h2>
              <p>余额必须来自未来的真实积分服务。</p>
            </div>
          </div>
          <FormControl label="可用积分" description="未接入积分系统，因此不展示数字">
            <div className="capability-field capability-field--unavailable">暂不可用</div>
          </FormControl>
          <div className="capability-actions">
            <Button variant="primary" disabled>充值入口未开放</Button>
            <Button variant="secondary" onClick={() => setShowNotice(true)}>查看说明</Button>
          </div>
        </section>

        <section className="capability-card">
          <div className="capability-card__header">
            <div>
              <h2>计费状态</h2>
              <p>本页面不会模拟扣减或支付结果。</p>
            </div>
            <StatusTag tone="neutral">无数据</StatusTag>
          </div>
          <ul className="capability-list">
            <li><span>计费规则</span><strong>尚未确定</strong></li>
            <li><span>消费记录</span><strong>尚未接入</strong></li>
            <li><span>支付与订单</span><strong>尚未接入</strong></li>
          </ul>
        </section>
      </div>

      <section className="capability-card">
        <div className="capability-card__header">
          <div>
            <h2>未来能力</h2>
            <p>以下仅说明规划中的页面入口，不代表功能已经可用。</p>
          </div>
        </div>
        <div className="future-capability-list">
          {FUTURE_CAPABILITIES.map(([title, description]) => (
            <div className="future-capability" key={title}>
              <div><strong>{title}</strong><span>{description}</span></div>
              <StatusTag tone="warning">未开放</StatusTag>
            </div>
          ))}
        </div>
      </section>

      <StateDialog
        open={showNotice}
        tone="info"
        title="积分功能未接入"
        description="只有在真实账户、计费、支付与订单能力完成后，本页才会展示余额和记录。当前不会写死积分，也不会模拟充值或扣费。"
        confirmText="知道了"
        onConfirm={() => setShowNotice(false)}
        onCancel={() => setShowNotice(false)}
      />
    </div>
  )
}
