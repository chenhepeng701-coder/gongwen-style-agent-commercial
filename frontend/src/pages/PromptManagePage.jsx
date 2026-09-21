import React from 'react'

export default function PromptManagePage() {
  return (
    <div>
      <h1>高级设置 / 提示词管理</h1>
      <p className="page-desc">管理系统提示词模板、生成参数与高级配置。</p>

      <div className="form-section">
        <h3>提示词模板</h3>
        <p className="text-muted">提示词管理功能将在后续 Phase 开发，届时支持：</p>
        <ul className="feature-list">
          <li><strong>场景提示词</strong> — 不同公文场景使用不同的 System Prompt 模板</li>
          <li><strong>风格提示词</strong> — 注入风格卡片特征到生成提示词中</li>
          <li><strong>素材提示词</strong> — 素材卡片到 Prompt 的转换模板</li>
          <li><strong>质检提示词</strong> — 风格贴近度评估的 Prompt 模板</li>
          <li><strong>提示词变量</strong> — 支持 &#123;&#123;title&#125;&#125;、&#123;&#123;scene&#125;&#125; 等动态变量替换</li>
        </ul>
      </div>

      <div className="form-section">
        <h3>生成参数默认值</h3>
        <div className="form-grid">
          <div className="form-field">
            <label>最小字数</label>
            <input type="number" value={800} disabled />
          </div>
          <div className="form-field">
            <label>最大字数</label>
            <input type="number" value={1200} disabled />
          </div>
          <div className="form-field">
            <label>小标题数量</label>
            <input type="number" value={3} disabled />
          </div>
          <div className="form-field">
            <label>重试次数</label>
            <input type="number" value={1} disabled />
          </div>
        </div>
        <p className="gen-hint">生成参数当前为默认值，将在高级设置功能开发后开放编辑。</p>
      </div>
    </div>
  )
}
