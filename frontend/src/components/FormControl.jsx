import React from 'react'

export default function FormControl({
  label,
  description,
  error,
  required = false,
  htmlFor,
  className = '',
  children,
}) {
  const classes = ['form-control', error ? 'has-error' : '', className].filter(Boolean).join(' ')

  return (
    <div className={classes}>
      {label && (
        <label className="form-control__label" htmlFor={htmlFor}>
          {label}
          {required && <span className="form-control__required" aria-hidden="true">*</span>}
        </label>
      )}
      {description && <div className="form-control__description">{description}</div>}
      <div className="form-control__field">{children}</div>
      {error && <div className="form-control__error" role="alert">{error}</div>}
    </div>
  )
}
