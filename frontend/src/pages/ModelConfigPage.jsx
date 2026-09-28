import React, { useEffect, useState, useCallback } from 'react'
import {
  getSettings, saveSettings, testConnection, getModelPresets,
  getEmbeddingSettings, saveEmbeddingSettings, testEmbeddingConnection,
} from '../api'
import Modal from '../components/Modal'
import WritingReferenceSettings from '../components/WritingReferenceSettings'

const INITIAL_FORM = {
  provider_key: 'deepseek',
  provider: 'openai_compatible',
  base_url: '',
  model: '',
  endpoint_path: '/v1/chat/completions',
  temperature: 0.7,
  timeout: 120,
  api_key: '',
  api_key_configured: false,
}

export default function ModelConfigPage() {
  const [form, setForm] = useState({ ...INITIAL_FORM })
  const [presets, setPresets] = useState([])
  const [settingsLoading, setSettingsLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [showKey, setShowKey] = useState(false)
  const [testResult, setTestResult] = useState(null)

  const [modal, setModal] = useState(null)

  const [embForm, setEmbForm] = useState({
    base_url: '',
    model: '',
    endpoint_path: '/v1/embeddings',
    api_key: '',
    timeout: 120,
    api_key_configured: false,
  })
  const [embLoading, setEmbLoading] = useState(true)
  const [embSaving, setEmbSaving] = useState(false)
  const [embTesting, setEmbTesting] = useState(false)
  const [embTestResult, setEmbTestResult] = useState(null)
  const [showEmbKey, setShowEmbKey] = useState(false)

  useEffect(() => {
    let cancelled = false
    Promise.all([getSettings(), getModelPresets()])
      .then(([settingsRes, presetsRes]) => {
        if (cancelled) return
        const s = settingsRes
        setForm({
          provider_key: s.provider_key || 'deepseek',
          provider: s.provider || 'openai_compatible',
          base_url: s.base_url || '',
          model: s.model || '',
          endpoint_path: s.endpoint_path || '/v1/chat/completions',
          temperature: s.temperature ?? 0.7,
          timeout: s.timeout ?? 120,
          api_key: '',
          api_key_configured: s.api_key_configured || false,
        })
        setPresets(presetsRes.items || [])
      })
      .catch((err) => {
        if (!cancelled) setModal({ type: 'error', title: '加载失败', message: `加载基础模型配置失败: ${err.message}` })
      })
      .finally(() => {
        if (!cancelled) setSettingsLoading(false)
      })
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    let cancelled = false
    getEmbeddingSettings()
      .then((e) => {
        if (cancelled) return
        setEmbForm({
          base_url: e.base_url || '',
          model: e.model || '',
          endpoint_path: e.endpoint_path || '/v1/embeddings',
          api_key: '',
          timeout: e.timeout ?? 120,
          api_key_configured: e.api_key_configured || false,
        })
      })
      .catch((err) => {
        if (!cancelled) setModal({ type: 'error', title: '加载失败', message: `加载向量模型配置失败: ${err.message}` })
      })
      .finally(() => {
        if (!cancelled) setEmbLoading(false)
      })
    return () => { cancelled = true }
  }, [])

  const switchProvider = useCallback(async (key) => {
    setSettingsLoading(true)
    setTestResult(null)
    try {
      const res = await getSettings(key)
      const s = res
      setForm({
        provider_key: key,
        provider: s.provider || 'openai_compatible',
        base_url: s.base_url || '',
        model: s.model || '',
        endpoint_path: s.endpoint_path || '/v1/chat/completions',
        temperature: s.temperature ?? 0.7,
        timeout: s.timeout ?? 120,
        api_key: '',
        api_key_configured: s.api_key_configured || false,
      })
    } catch (err) {
      setModal({ type: 'error', title: '切换失败', message: `切换服务商失败: ${err.message}` })
    } finally {
      setSettingsLoading(false)
    }
  }, [])

  const applyPreset = useCallback((preset) => {
    setForm((prev) => ({
      ...prev,
      provider_key: preset.value,
      provider: preset.provider,
      base_url: preset.base_url,
      model: preset.model,
      endpoint_path: preset.endpoint_path,
      api_key: '',
      api_key_configured: false,
    }))
    setTestResult(null)
    switchProvider(preset.value)
  }, [switchProvider])

  const updateField = useCallback((field, value) => {
    setForm((prev) => ({ ...prev, [field]: value }))
  }, [])

  const handleSave = async () => {
    setSaving(true)
    try {
      const res = await saveSettings({
        provider_key: form.provider_key,
        provider: form.provider,
        base_url: form.base_url,
        endpoint_path: form.endpoint_path,
        api_key: form.api_key,
        model: form.model,
        temperature: Number(form.temperature),
        timeout: Number(form.timeout),
      })
      setModal({ type: 'success', title: '保存成功', message: res.message || '配置已保存' })
      const reload = await getSettings(form.provider_key)
      setForm((prev) => ({
        ...prev,
        api_key: '',
        api_key_configured: reload.api_key_configured,
      }))
    } catch (err) {
      setModal({ type: 'error', title: '保存失败', message: err.message })
    } finally {
      setSaving(false)
    }
  }

  const handleTest = async () => {
    setTesting(true)
    setTestResult(null)
    try {
      const res = await testConnection({
        provider_key: form.provider_key,
        provider: form.provider,
        base_url: form.base_url,
        endpoint_path: form.endpoint_path,
        api_key: form.api_key,
        model: form.model,
        temperature: Number(form.temperature),
        timeout: Number(form.timeout),
      })
      setTestResult(res)
    } catch (err) {
      setTestResult({
        success: false,
        message: `请求失败: ${err.message}`,
        response: '',
        timestamp: new Date().toISOString(),
      })
    } finally {
      setTesting(false)
    }
  }

  const updateEmbField = useCallback((field, value) => {
    setEmbForm((prev) => ({ ...prev, [field]: value }))
  }, [])

  const handleEmbSave = async () => {
    setEmbSaving(true)
    try {
      const res = await saveEmbeddingSettings({
        base_url: embForm.base_url,
        model: embForm.model,
        endpoint_path: embForm.endpoint_path,
        api_key: embForm.api_key,
        timeout: Number(embForm.timeout),
      })
      setModal({ type: 'success', title: '保存成功', message: res.message || '向量配置已保存' })
      const reload = await getEmbeddingSettings()
      setEmbForm((prev) => ({
        ...prev,
        api_key: '',
        api_key_configured: reload.api_key_configured,
      }))
    } catch (err) {
      setModal({ type: 'error', title: '保存失败', message: `向量配置保存失败: ${err.message}` })
    } finally {
      setEmbSaving(false)
    }
  }

  const handleEmbTest = async () => {
    setEmbTesting(true)
    setEmbTestResult(null)
    try {
      const res = await testEmbeddingConnection({
        base_url: embForm.base_url,
        model: embForm.model,
        endpoint_path: embForm.endpoint_path,
        api_key: embForm.api_key,
        timeout: Number(embForm.timeout),
      })
      setEmbTestResult(res)
    } catch (err) {
      setEmbTestResult({
        success: false,
        message: `请求失败: ${err.message}`,
        response: '',
        timestamp: new Date().toISOString(),
      })
    } finally {
      setEmbTesting(false)
    }
  }

  if (settingsLoading && !form.provider_key) {
    return (
      <div>
        <h1>模型配置</h1>
        <p className="text-muted">加载中...</p>
      </div>
    )
  }

  return (
    <div>
      <h1>模型配置</h1>

      <Modal
        visible={!!modal}
        type={modal?.type}
        title={modal?.title}
        message={modal?.message}
        onClose={() => setModal(null)}
      />

      {/* ── Provider Selector ── */}
      <div className="form-section">
        <h3>服务商选择</h3>
        <div className="provider-tabs">
          <button
            className={`provider-tab ${form.provider_key === 'deepseek' ? 'active' : ''}`}
            onClick={() => switchProvider('deepseek')}
          >
            DeepSeek
          </button>
          <button
            className={`provider-tab ${form.provider_key === 'volcengine_ark' ? 'active' : ''}`}
            onClick={() => switchProvider('volcengine_ark')}
          >
            豆包 / 火山方舟
          </button>
        </div>
      </div>

      {/* ── Dual Panel: Base Model + Vector Model ── */}
      <div className="config-panels">

        {/* ── Basic Model Config ── */}
        <div className="config-panel">
          <h3>基础模型配置</h3>
          <div className="form-grid">
          <FormField label="Base URL" required>
            <input
              type="text"
              value={form.base_url}
              onChange={(e) => updateField('base_url', e.target.value)}
              placeholder="https://api.deepseek.com"
            />
          </FormField>

          <FormField label="Model" required>
            <input
              type="text"
              value={form.model}
              onChange={(e) => updateField('model', e.target.value)}
              placeholder="deepseek-chat"
            />
          </FormField>

          <FormField label="Endpoint Path">
            <input
              type="text"
              value={form.endpoint_path}
              onChange={(e) => updateField('endpoint_path', e.target.value)}
              placeholder="/v1/chat/completions"
            />
          </FormField>

          <FormField label="Temperature">
            <input
              type="number"
              min="0"
              max="2"
              step="0.1"
              value={form.temperature}
              onChange={(e) => updateField('temperature', e.target.value)}
            />
          </FormField>

          <FormField label="Timeout (秒)">
            <input
              type="number"
              min="10"
              max="600"
              step="10"
              value={form.timeout}
              onChange={(e) => updateField('timeout', e.target.value)}
            />
          </FormField>

          <FormField label="API Key" required hint={form.api_key_configured && !form.api_key ? '（已有已保存的 Key，留空则不覆盖）' : ''}>
            <div className="api-key-row">
              <input
                type={showKey ? 'text' : 'password'}
                value={form.api_key}
                onChange={(e) => updateField('api_key', e.target.value)}
                placeholder={form.api_key_configured ? '已配置 · 留空不覆盖' : '请输入 API Key'}
              />
              <button
                type="button"
                className="btn-icon"
                onClick={() => setShowKey(!showKey)}
                title={showKey ? '隐藏' : '显示'}
              >
                {showKey ? '***' : '***'}
              </button>
            </div>
          </FormField>
        </div>

          <div className="form-actions">
            <button className="btn btn-primary" onClick={handleSave} disabled={saving}>
              {saving ? '保存中...' : '保存配置'}
            </button>
            <button className="btn btn-secondary" onClick={handleTest} disabled={testing}>
              {testing ? '测试中...' : '测试连接'}
            </button>
          </div>

          {testResult && (
            <div className={`test-result ${testResult.success ? 'success' : 'error'}`}>
              <div className="test-result-header">
                <span className={`test-result-badge ${testResult.success ? 'success' : 'error'}`}>
                  {testResult.success ? '连接成功' : '连接失败'}
                </span>
                {testResult.timestamp && (
                  <span className="test-result-time">{testResult.timestamp}</span>
                )}
              </div>
              <div className="test-result-msg">{testResult.message}</div>
              {testResult.response && (
                <div className="test-result-response">
                  <strong>响应内容:</strong>
                  <pre>{testResult.response}</pre>
                </div>
              )}
            </div>
          )}
        </div>

        {/* ── Vector Model Config ── */}
        <div className="config-panel">
          <h3>向量模型配置</h3>
        <div className="form-grid">
          <FormField label="Base URL">
            <input
              type="text"
              value={embForm.base_url}
              onChange={(e) => updateEmbField('base_url', e.target.value)}
              placeholder="https://api.deepseek.com"
            />
          </FormField>

          <FormField label="Model">
            <input
              type="text"
              value={embForm.model}
              onChange={(e) => updateEmbField('model', e.target.value)}
              placeholder="text-embedding-3-small"
            />
          </FormField>

          <FormField label="Endpoint Path">
            <input
              type="text"
              value={embForm.endpoint_path}
              onChange={(e) => updateEmbField('endpoint_path', e.target.value)}
              placeholder="/v1/embeddings"
            />
          </FormField>

          <FormField label="Timeout (秒)">
            <input
              type="number"
              min="10"
              max="600"
              step="10"
              value={embForm.timeout}
              onChange={(e) => updateEmbField('timeout', e.target.value)}
            />
          </FormField>

          <FormField
            label="API Key"
            hint={embForm.api_key_configured && !embForm.api_key ? '（已有已保存的 Key，留空则不覆盖）' : ''}
          >
            <div className="api-key-row">
              <input
                type={showEmbKey ? 'text' : 'password'}
                value={embForm.api_key}
                onChange={(e) => updateEmbField('api_key', e.target.value)}
                placeholder={embForm.api_key_configured ? '已配置 · 留空不覆盖' : '请输入 API Key'}
              />
              <button
                type="button"
                className="btn-icon"
                onClick={() => setShowEmbKey(!showEmbKey)}
                title={showEmbKey ? '隐藏' : '显示'}
              >
                {showEmbKey ? '***' : '***'}
              </button>
            </div>
          </FormField>
        </div>

        <div className="form-actions">
          <button className="btn btn-primary" onClick={handleEmbSave} disabled={embSaving}>
            {embSaving ? '保存中...' : '保存向量配置'}
          </button>
          <button className="btn btn-secondary" onClick={handleEmbTest} disabled={embTesting}>
            {embTesting ? '测试中...' : '测试连接'}
          </button>
        </div>

        {embTestResult && (
          <div className={`test-result ${embTestResult.success ? 'success' : 'error'}`}>
            <div className="test-result-header">
              <span className={`test-result-badge ${embTestResult.success ? 'success' : 'error'}`}>
                {embTestResult.success ? '连接成功' : '连接失败'}
              </span>
              {embTestResult.timestamp && (
                <span className="test-result-time">{embTestResult.timestamp}</span>
              )}
            </div>
            <div className="test-result-msg">{embTestResult.message}</div>
            {embTestResult.response && (
              <div className="test-result-response">
                <strong>响应内容:</strong>
                <pre>{embTestResult.response}</pre>
              </div>
            )}
          </div>
        )}
        </div>
      </div>

      {/* ── Model Presets ── */}
      <WritingReferenceSettings />
      <div className="form-section mt-4">
        <h3>模型预设列表</h3>
        <table className="preset-table">
          <thead>
            <tr>
              <th>预设名称</th>
              <th>Base URL</th>
              <th>Model</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {presets.map((p) => (
              <tr key={p.value} className={form.provider_key === p.value ? 'active-row' : ''}>
                <td>{p.label}</td>
                <td className="mono">{p.base_url || '(自定义)'}</td>
                <td className="mono">{p.model || '(自定义)'}</td>
                <td>
                  <button
                    className="btn btn-sm"
                    onClick={() => applyPreset(p)}
                    disabled={settingsLoading}
                  >
                    应用
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function FormField({ label, required, hint, children }) {
  return (
    <div className="form-field">
      <label>
        {label}
        {required && <span className="required"> *</span>}
      </label>
      {hint && <span className="field-hint">{hint}</span>}
      {children}
    </div>
  )
}
