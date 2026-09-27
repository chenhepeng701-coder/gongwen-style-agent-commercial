import React, { useEffect, useState } from 'react'
import Button from '../components/Button'
import StatusTag from '../components/StatusTag'

const GENERATION_STEPS = [
  '理解写作任务',
  '检索本地资料',
  '提取可靠事实',
  '匹配文种与写作要求',
  '生成完整初稿',
  '核查事实与内容',
  '整理可编辑文章',
]

function formatElapsedTime(seconds) {
  const pad = value => String(value).padStart(2, '0')
  const minutesAndSeconds = `${pad(Math.floor(seconds / 60) % 60)}:${pad(seconds % 60)}`
  return seconds >= 3600 ? `${pad(Math.floor(seconds / 3600))}:${minutesAndSeconds}` : minutesAndSeconds
}

// Mounted only while the request's waiting page is visible. Keep second-level
// updates here so the writing page and its request state do not re-render.
function GenerationWaitingIndicator({ requestTitle }) {
  const [elapsedSeconds, setElapsedSeconds] = useState(0)

  useEffect(() => {
    const startedAt = Date.now()
    const interval = setInterval(() => {
      // Measure elapsed time instead of counting callbacks: background tabs can
      // throttle intervals, but must not under-report the actual wait.
      setElapsedSeconds(Math.max(0, Math.floor((Date.now() - startedAt) / 1000)))
    }, 1000)
    return () => clearInterval(interval)
  }, [])

  const waitingHint = elapsedSeconds <= 30
    ? '正在处理写作任务，请稍候。'
    : elapsedSeconds <= 90
      ? '完整文章生成可能需要一些时间，请保持当前页面打开。'
      : '复杂材料和较长文章处理时间可能更长，系统仍在等待生成结果。'

  return (
    <div className="generation-wait-panel__content">
      <div className="generation-wait-heading" role="status" aria-live="polite">
        <StatusTag tone="info">请求中</StatusTag>
        <h2>正在生成文章</h2>
        <p className="generation-wait-title">{requestTitle || '正在处理当前写作请求'}</p>
      </div>
      <div className="generation-elapsed" role="timer" aria-live="off" aria-label="已用时间">
        <span>已用时间</span>
        <strong>{formatElapsedTime(elapsedSeconds)}</strong>
      </div>
      <div className="generation-loading-dots" aria-hidden="true"><span /><span /><span /></div>
      <p className="generation-wait-hint" aria-live="polite">{waitingHint}</p>
      <div className="generation-pipeline">
        <h3 id="generation-pipeline-heading">文章将按照以下流程完成处理</h3>
        <p>以下为流程说明，不代表实时执行进度。</p>
        <ol aria-labelledby="generation-pipeline-heading">
          {GENERATION_STEPS.map((step, index) => (
            <li key={step}><span aria-hidden="true">{index + 1}</span>{step}</li>
          ))}
        </ol>
      </div>
      <p>结果返回后即可继续编辑。返回写作参数不会取消当前请求。</p>
    </div>
  )
}

export default function GenerationPage({ loading, requestTitle, isBatch, stopRequested, onReturn, onStop }) {
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
            <GenerationWaitingIndicator requestTitle={requestTitle} />
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
