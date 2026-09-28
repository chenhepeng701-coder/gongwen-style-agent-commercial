import React, { useEffect, useRef, useState } from 'react'
import api from '../api'

export default function WritingReferenceSettings() {
  const [configured, setConfigured] = useState(false)
  const [busy, setBusy] = useState(true)
  const [message, setMessage] = useState('')
  const picker = useRef(null)

  useEffect(() => {
    let active = true
    api.get('/settings/writing-reference')
      .then(result => { if (active) setConfigured(result.configured) })
      .catch(error => { if (active) setMessage(error.message) })
      .finally(() => { if (active) setBusy(false) })
    return () => { active = false }
  }, [])

  async function selectFile(event) {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    if (file.size > 5 * 1024 * 1024) {
      setMessage('请选择不超过 5 MB 的 Writing Reference JSON。')
      return
    }
    setBusy(true)
    setMessage('')
    try {
      const data = new FormData()
      data.append('file', file)
      const result = await api.post('/settings/writing-reference', data, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      setConfigured(result.configured)
      setMessage('配置成功')
    } catch (error) {
      setMessage(error.message)
    } finally {
      setBusy(false)
    }
  }

  return <section className="form-section mt-4" aria-label="Writing Reference 设置">
    <h3>Writing Reference</h3>
    <p>{configured ? '已配置' : '未配置'}</p>
    <p className="field-hint">选择您的 Writing Reference JSON，文件仅发送到本机校验并保存。</p>
    <p className="field-hint">参考材料保存在本机。生成文章时，与本次任务相关的参考内容会发送至您当前配置的模型服务商，用于生成处理。</p>
    <input ref={picker} type="file" accept=".json,application/json" hidden onChange={selectFile} />
    <button className="btn btn-secondary" disabled={busy} onClick={() => picker.current?.click()}>
      {busy ? '校验中...' : configured ? '更换文件' : '选择 Writing Reference JSON'}
    </button>
    {message && <p role="status">{message}</p>}
  </section>
}
