import React from 'react'

const BUTTON_VARIANTS = new Set(['primary', 'secondary', 'ghost', 'danger'])
const BUTTON_SIZES = new Set(['sm', 'md', 'lg'])

export default function Button({
  variant = 'secondary',
  size = 'md',
  loading = false,
  disabled = false,
  className = '',
  type = 'button',
  children,
  ...buttonProps
}) {
  const resolvedVariant = BUTTON_VARIANTS.has(variant) ? variant : 'secondary'
  const resolvedSize = BUTTON_SIZES.has(size) ? size : 'md'
  const classes = [
    'ui-button',
    `ui-button--${resolvedVariant}`,
    `ui-button--${resolvedSize}`,
    loading ? 'is-loading' : '',
    className,
  ].filter(Boolean).join(' ')

  return (
    <button
      {...buttonProps}
      type={type}
      className={classes}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
    >
      {loading && <span className="ui-button__spinner" aria-hidden="true" />}
      <span className="ui-button__label">{children}</span>
    </button>
  )
}
