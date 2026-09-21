import React from 'react'
import StateDialog from './StateDialog'

export default function ConfirmDialog({
  open,
  tone = 'danger',
  title,
  description,
  children,
  confirmText = '确认',
  cancelText = '取消',
  pending = false,
  confirmDisabled = false,
  cancelDisabled = false,
  onConfirm,
  onCancel,
}) {
  return (
    <StateDialog
      open={open}
      tone={tone}
      title={title}
      description={description}
      confirmText={confirmText}
      cancelText={cancelText}
      pending={pending}
      confirmDisabled={confirmDisabled}
      cancelDisabled={cancelDisabled}
      onConfirm={onConfirm}
      onCancel={onCancel}
    >
      {children}
    </StateDialog>
  )
}
