import React from 'react'
import Button from '../components/Button'
import StatusTag from '../components/StatusTag'

export default function GenerationPage({ requestTitle, isBatch, stopRequested, onReturn, onStop }) {
  return (
    <div className="generation-page" data-page-key="genResult">
      <header className="workflow-page-header">
        <div>
          <h1>正在生成文章</h1>
          <p>请求已提交，正在等待服务返回真实结果。</p>
        </div>
        <Button variant="secondary" onClick={onReturn}>返回写作参数</Button>
      </header>
      <main className="generation-main">
        <section className="core-panel generation-status-card" aria-live="polite">
          <div className="generation-status-card__heading">
            <div>
              <StatusTag tone="info">请求中</StatusTag>
              <h2>{requestTitle || '正在处理当前写作请求'}</h2>
            </div>
            <span className="generation-request-indicator" aria-hidden="true"><span /></span>
          </div>
          <p>当前系统没有实时进度接口，因此这里仅展示真实的请求中状态，不显示百分比、剩余时间或内部阶段。</p>
        </section>

        <div className="generation-workspace">
          <section className="core-panel generation-wait-panel">
            <div className="generation-wait-panel__content" role="status">
              <span className="core-spinner" aria-hidden="true" />
              <h2>请稍候，结果返回后即可继续编辑</h2>
              <p>生成时间会受到文章长度、资料数量和模型响应速度影响。可以返回参数页查看当前任务，生成请求不会因此被伪装为后台队列。</p>
            </div>
          </section>
          <aside className="core-panel generation-notes">
            <h2>当前可确认的信息</h2>
            <div className="generation-note-item"><strong>请求状态</strong><span>已提交，等待返回</span></div>
            <div className="generation-note-item"><strong>任务类型</strong><span>{isBatch ? '批量任务中的当前请求' : '单篇生成请求'}</span></div>
            <div className="generation-note-item"><strong>结果入口</strong><span>请求成功后自动进入文稿编辑或返回任务列表</span></div>
            {isBatch && onStop && (
              <Button variant="secondary" onClick={onStop} disabled={stopRequested}>
                {stopRequested ? '已请求停止后续任务' : '停止后续任务'}
              </Button>
            )}
          </aside>
        </div>
      </main>
    </div>
  )
}
