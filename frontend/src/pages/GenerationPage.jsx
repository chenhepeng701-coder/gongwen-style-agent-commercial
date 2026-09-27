import React from 'react'
import Button from '../components/Button'
import GenerationWaitingIndicator from '../components/GenerationWaitingIndicator'

export default function GenerationPage({ loading, requestTitle, requestStartedAt, isBatch, stopRequested, onReturn, onStop }) {
  if (!loading) return null

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
        <div className="generation-workspace">
          <section className="core-panel generation-wait-panel">
            <GenerationWaitingIndicator variant="full" startedAt={requestStartedAt} requestTitle={requestTitle} />
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
