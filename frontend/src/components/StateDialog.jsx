import React, { useEffect, useId } from 'react'
import Button from './Button'

const DIALOG_TONES = new Set(['info', 'success', 'warning', 'danger'])
const DIALOG_MARKS = {
  info: 'i',
  success: '✓',
  warning: '!',
  danger: '!',
}

export default function StateDialog({
  open,
  tone = 'info',
  title,
  description,
  children,
  confirmText = '确定',
  cancelText,
  onConfirm,
  onCancel,
  pending = false,
  confirmDisabled = false,
  cancelDisabled = false,
}) {
  const resolvedTone = DIALOG_TONES.has(tone) ? tone : 'info'
  const titleId = useId()
  const descriptionId = useId()
  const handleCancel = onCancel || (() => {})
  const handleConfirm = onConfirm || handleCancel

  useEffect(() => {
    if (!open) return undefined
    const handleKeyDown = (event) => {
      if (event.key === 'Escape' && !pending) handleCancel()
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [open, pending, handleCancel])

  if (!open) return null

  return (
    <div
      className="state-dialog-overlay"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !pending) handleCancel()
      }}
    >
      <section
        className={`state-dialog state-dialog--${resolvedTone}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description || children ? descriptionId : undefined}
      >
        <div className="state-dialog__marker" aria-hidden="true">{DIALOG_MARKS[resolvedTone]}</div>
        <div className="state-dialog__content">
          <h2 className="state-dialog__title" id={titleId}>{title}</h2>
          <div id={descriptionId}>
            {description && <div className="state-dialog__description">{description}</div>}
            {children && <div className="state-dialog__body">{children}</div>}
          </div>
        </div>
        <div className="state-dialog__actions">
          {cancelText && (
            <Button variant="secondary" onClick={handleCancel} disabled={pending || cancelDisabled}>
              {cancelText}
            </Button>
          )}
          <Button
            variant={resolvedTone === 'danger' ? 'danger' : 'primary'}
            onClick={handleConfirm}
            loading={pending}
            disabled={confirmDisabled}
          >
            {confirmText}
          </Button>
        </div>
      </section>
    </div>
  )
}
