import React from 'react'

const STATUS_TONES = new Set(['neutral', 'success', 'warning', 'danger', 'info'])

export default function StatusTag({ tone = 'neutral', className = '', children, ...props }) {
  const resolvedTone = STATUS_TONES.has(tone) ? tone : 'neutral'
  const classes = ['status-tag', `status-tag--${resolvedTone}`, className].filter(Boolean).join(' ')

  return (
    <span {...props} className={classes}>
      {children}
    </span>
  )
}
