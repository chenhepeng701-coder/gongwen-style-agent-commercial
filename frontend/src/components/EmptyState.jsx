import React from 'react'
import Button from './Button'

export default function EmptyState({
  icon = '空',
  title,
  description,
  actionLabel,
  onAction,
  secondaryActionLabel,
  onSecondaryAction,
  className = '',
}) {
  const classes = ['core-state', 'core-state--empty', 'ui-empty-state', className]
    .filter(Boolean)
    .join(' ')

  return (
    <div className={classes} role="status">
      <div className="core-state__mark ui-empty-state__mark" aria-hidden="true">{icon}</div>
      <h3>{title}</h3>
      {description && <p>{description}</p>}
      {(actionLabel || secondaryActionLabel) && (
        <div className="core-state__actions">
          {secondaryActionLabel && (
            <Button variant="secondary" onClick={onSecondaryAction}>{secondaryActionLabel}</Button>
          )}
          {actionLabel && <Button onClick={onAction}>{actionLabel}</Button>}
        </div>
      )}
    </div>
  )
}
