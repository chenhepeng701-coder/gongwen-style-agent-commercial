import React, { useEffect, useState } from 'react'
import Button from './Button'
import StatusTag from './StatusTag'

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

// The request owner supplies the start time; mounting either view never resets it.
// Keep second-level updates local so the writing form does not re-render each tick.
export default function GenerationWaitingIndicator({ variant = 'full', startedAt, requestTitle, onViewStatus }) {
  const [now, setNow] = useState(Date.now)

  useEffect(() => {
    if (startedAt == null) return
    setNow(Date.now())
    const interval = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(interval)
  }, [startedAt])

  // Background tabs can throttle callbacks; always calculate the real time gap.
  const elapsedSeconds = startedAt == null ? 0 : Math.max(0, Math.floor((now - startedAt) / 1000))
  const waitingHint = elapsedSeconds <= 30
    ? '正在处理写作任务，请稍候。'
    : elapsedSeconds <= 90
      ? '完整文章生成可能需要一些时间，请保持当前页面打开。'
      : '复杂材料和较长文章处理时间可能更长，系统仍在等待生成结果。'
  const compact = variant === 'compact'
  const Heading = compact ? 'h3' : 'h2'

  return (
    <div className={`generation-wait-panel__content${compact ? ' generation-wait-panel__content--compact' : ''}`}>
      <div className="generation-wait-heading" role="status" aria-live="polite">
        {!compact && <StatusTag tone="info">请求中</StatusTag>}
        <Heading>正在生成文章</Heading>
        {!compact && <p className="generation-wait-title">{requestTitle || '正在处理当前写作请求'}</p>}
      </div>
      <div className="generation-elapsed" role="timer" aria-live="off" aria-label="已用时间">
        <span>已用时间</span>
        <strong>{formatElapsedTime(elapsedSeconds)}</strong>
      </div>
      <div className="generation-loading-dots" aria-hidden="true"><span /><span /><span /></div>
      {compact && <p>请求仍在处理中。<br />返回写作参数不会取消当前生成请求。</p>}
      <p className="generation-wait-hint" aria-live="polite">{waitingHint}</p>
      {compact ? (
        <Button variant="secondary" onClick={onViewStatus}>查看生成状态</Button>
      ) : (
        <>
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
        </>
      )}
    </div>
  )
}
