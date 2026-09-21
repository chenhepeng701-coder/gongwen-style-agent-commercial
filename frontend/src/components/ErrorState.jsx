import React from 'react'
import Button from './Button'

const INTERNAL_DETAIL_PATTERN = /(?:traceback|api[_ -]?key|authorization|bearer|[a-z]:\\|\/users\/|\/home\/|\.py\b|chunk|vector|embedding|provider|model[_ -]?id|debug|stack trace)/i

export function getSafeErrorMessage(errorOrMessage, fallback = '请求未完成，请稍后重试。') {
  const rawMessage = typeof errorOrMessage === 'string'
    ? errorOrMessage
    : errorOrMessage?.message
  if (!rawMessage) return fallback

  const normalized = String(rawMessage)
    .replace(/^error:\s*/i, '')
    .replace(/\s+/g, ' ')
    .trim()

  if (!normalized || INTERNAL_DETAIL_PATTERN.test(normalized)) return fallback
  if (normalized.length > 160) return `${normalized.slice(0, 157)}…`
  return normalized
}

export default function ErrorState({
  title = '暂时无法加载',
  error,
  description,
  fallback,
  retryLabel = '重新加载',
  onRetry,
  secondaryActionLabel,
  onSecondaryAction,
  className = '',
}) {
  const message = getSafeErrorMessage(description || error, fallback)
  const classes = ['core-state', 'core-state--error', 'ui-error-state', className]
    .filter(Boolean)
    .join(' ')

  return (
    <div className={classes} role="alert">
      <div className="core-state__mark ui-error-state__mark" aria-hidden="true">×</div>
      <h3>{title}</h3>
      <p>{message}</p>
      {(onRetry || secondaryActionLabel) && (
        <div className="core-state__actions">
          {secondaryActionLabel && (
            <Button variant="secondary" onClick={onSecondaryAction}>{secondaryActionLabel}</Button>
          )}
          {onRetry && <Button variant="secondary" onClick={onRetry}>{retryLabel}</Button>}
        </div>
      )}
    </div>
  )
}
