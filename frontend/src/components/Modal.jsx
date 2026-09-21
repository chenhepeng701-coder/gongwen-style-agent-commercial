import StateDialog from './StateDialog'
import { getSafeErrorMessage } from './ErrorState'

const TYPE_TO_TONE = {
  error: 'danger',
  danger: 'danger',
  warning: 'warning',
  success: 'success',
  info: 'info',
}

export default function Modal({
  visible,
  title,
  message,
  type,
  onClose,
  onConfirm,
  cancelText,
  confirmText = '确定',
  confirmDisabled = false,
  pending = false,
}) {
  const modalType = type || 'info'
  const handleCancel = onClose || (() => {})
  const handleConfirm = onConfirm || handleCancel
  const description = modalType === 'error'
    ? getSafeErrorMessage(message, '操作未完成，请稍后重试。')
    : message

  return (
    <StateDialog
      open={visible}
      tone={TYPE_TO_TONE[modalType] || 'info'}
      title={title}
      description={description}
      onCancel={handleCancel}
      onConfirm={handleConfirm}
      cancelText={cancelText}
      confirmText={confirmText}
      confirmDisabled={confirmDisabled}
      cancelDisabled={confirmDisabled}
      pending={pending}
    />
  )
}
