import React from 'react'
import Button from '../components/Button'
import EmptyState from '../components/EmptyState'
import { getSafeErrorMessage } from '../components/ErrorState'
import FormControl from '../components/FormControl'
import StatusTag from '../components/StatusTag'
import { GenreSelect, SceneSelect } from '../components/WritingCatalogSelects'

const STATUS_PRESENTATION = {
  pending: { label: '未开始', tone: 'neutral' },
  generating: { label: '已提交', tone: 'info' },
  completed: { label: '成功', tone: 'success' },
  quality_test: { label: '质量测试稿', tone: 'warning' },
  failed: { label: '失败', tone: 'danger' },
  stopped: { label: '已停止', tone: 'neutral' },
}

export default function BatchTaskPage({
  rows,
  loading,
  catalogReady,
  catalogLoading,
  catalogError,
  onReloadCatalogs,
  taskValidationErrors = [],
  defaults,
  options,
  totalCount,
  fileInputRef,
  fileName,
  additionalRequirements,
  exportFormat,
  onNavigate,
  onDefaultChange,
  onTaskChange,
  onToggleTask,
  onAddTask,
  onRemoveTask,
  onApplyDefaults,
  onFileUpload,
  onImportExcel,
  onAdditionalRequirementsChange,
  onExportFormatChange,
  onGenerate,
  onOpenArticle,
}) {
  const readyCount = rows.filter(row => row.title.trim()).length

  return (
    <div className="core-page batch-flow-page" data-page-key="batchTask">
      <div className="core-mode-tabs" role="tablist" aria-label="生成方式">
        <button type="button" className="core-mode-tab" role="tab" aria-selected="false" onClick={() => onNavigate?.('articleGen')}>
          单篇生成
        </button>
        <button type="button" className="core-mode-tab is-active" role="tab" aria-selected="true">
          批量生成
        </button>
      </div>

      <section className="core-panel batch-defaults" aria-labelledby="batch-defaults-title">
        {catalogError && <div role="alert">{catalogError} <Button onClick={onReloadCatalogs} disabled={catalogLoading}>重新加载</Button></div>}
        <div className="core-panel-heading">
          <div>
            <h2 id="batch-defaults-title">批量默认参数</h2>
            <p>新增任务继承这些参数；每项任务仍可单独展开修改。</p>
          </div>
          <Button variant="secondary" size="sm" onClick={onApplyDefaults} disabled={loading || rows.length === 0}>
            应用到全部任务
          </Button>
        </div>
        <div className="batch-default-grid">
          <FormControl label="文种" htmlFor="batch-default-document-type">
            <GenreSelect id="batch-default-document-type" genres={options.genres} value={defaults.documentType} onChange={event => onDefaultChange('documentType', event.target.value)} disabled={loading || !catalogReady} />
          </FormControl>
          <FormControl label="写作场景" htmlFor="batch-default-scene">
            <SceneSelect id="batch-default-scene" scenes={options.scenes} value={defaults.scene} onChange={event => onDefaultChange('scene', event.target.value)} disabled={loading || !catalogReady} />
          </FormControl>
          <FormControl label="层级" htmlFor="batch-default-level">
            <select id="batch-default-level" value={defaults.level} onChange={event => onDefaultChange('level', event.target.value)} disabled={loading}>
              {options.level.map(option => <option key={option}>{option}</option>)}
            </select>
          </FormControl>
          <FormControl label="单位类型" htmlFor="batch-default-org-type">
            <select id="batch-default-org-type" value={defaults.orgType} onChange={event => onDefaultChange('orgType', event.target.value)} disabled={loading}>
              {options.orgType.map(option => <option key={option}>{option}</option>)}
            </select>
          </FormControl>
          <FormControl label="业务领域" htmlFor="batch-default-business-domain">
            <select id="batch-default-business-domain" value={defaults.businessDomain} onChange={event => onDefaultChange('businessDomain', event.target.value)} disabled={loading}>
              {options.businessDomain.map(option => <option key={option}>{option}</option>)}
            </select>
          </FormControl>
          <FormControl label="写作目的" htmlFor="batch-default-writing-purpose">
            <select id="batch-default-writing-purpose" value={defaults.writingPurpose} onChange={event => onDefaultChange('writingPurpose', event.target.value)} disabled={loading}>
              {options.writingPurpose.map(option => <option key={option}>{option}</option>)}
            </select>
          </FormControl>
          <FormControl label="目标字数" htmlFor="batch-default-word-count">
            <input id="batch-default-word-count" type="number" min="500" max="5000" step="100" value={defaults.wordCount} onChange={event => onDefaultChange('wordCount', event.target.value)} disabled={loading} />
          </FormControl>
          <FormControl label="参考资料范围" htmlFor="batch-default-reference-scope">
            <select id="batch-default-reference-scope" value={defaults.referenceScope} onChange={event => onDefaultChange('referenceScope', event.target.value)} disabled={loading}>
              {options.referenceScope.map(option => <option key={option}>{option}</option>)}
            </select>
          </FormControl>
          <FormControl label="导出格式" htmlFor="batch-export-format">
            <select id="batch-export-format" value={exportFormat} onChange={event => onExportFormatChange(event.target.value)} disabled={loading}>
              <option value="docx">Word</option>
              <option value="txt">TXT</option>
            </select>
          </FormControl>
        </div>
        <FormControl label="补充要求（可选）" htmlFor="batch-extra-requirements" className="batch-extra-requirements">
          <textarea id="batch-extra-requirements" rows="2" value={additionalRequirements} onChange={event => onAdditionalRequirementsChange(event.target.value)} placeholder="例如：问题分析要客观克制，下一步措施要可执行" disabled={loading} />
        </FormControl>
      </section>

      <section className="core-panel batch-task-panel" aria-labelledby="batch-task-title">
        <div className="batch-task-toolbar">
          <div>
            <h2 id="batch-task-title">任务列表</h2>
            <p>{rows.length} 项任务 · {readyCount} 项已填写标题</p>
          </div>
          <div className="batch-task-toolbar__actions">
            <input ref={fileInputRef} type="file" accept=".xlsx,.xls" className="visually-hidden" onChange={onFileUpload} />
            <Button variant="secondary" size="sm" onClick={onImportExcel} disabled={loading || !catalogReady}>导入 Excel</Button>
            <Button variant="secondary" size="sm" onClick={onAddTask} disabled={loading}>添加任务</Button>
          </div>
        </div>
        {fileName && <div className="batch-import-note">已导入：{fileName}</div>}

        {rows.length === 0 ? (
          <EmptyState
            icon="批"
            title="还没有批量任务"
            description="支持手动添加任务或从 Excel 批量导入。"
            secondaryActionLabel="导入 Excel"
            onSecondaryAction={onImportExcel}
            actionLabel="添加任务"
            onAction={onAddTask}
            className="batch-empty-state"
          />
        ) : (
          <div className="batch-task-list" role="table" aria-label="批量生成任务">
            <div className="batch-task-list__head" role="row">
              <span>标题</span><span>主要参数</span><span>篇数</span><span>状态</span><span>操作</span>
            </div>
            {rows.map((row, index) => {
              const status = STATUS_PRESENTATION[row.status] || STATUS_PRESENTATION.pending
              return (
                <article className={`batch-task-item ${row.expanded ? 'is-expanded' : ''}`} key={row.id} role="row">
                  <div className="batch-task-item__summary">
                    <input aria-label={`任务 ${index + 1} 标题`} value={row.title} onChange={event => onTaskChange(index, 'title', event.target.value)} placeholder="请输入公文标题" disabled={loading} />
                    <div className="batch-task-item__params">
                      <span>{row.documentType}</span><span>{row.scene || '自动 / 未指定'}</span><span>{row.level}</span><span>{row.orgType}</span><span>{row.businessDomain}</span><span>{row.wordCount} 字</span>
                    </div>
                    <span>{row.count || 1}</span>
                    <StatusTag tone={status.tone}>{status.label}</StatusTag>
                    <div className="batch-task-item__actions">
                      {row.articles?.length > 0 && <Button variant="ghost" size="sm" onClick={() => onOpenArticle(row.articles[0])}>查看</Button>}
                      <Button variant="ghost" size="sm" onClick={() => onToggleTask(index)} disabled={loading}>{row.expanded ? '收起' : '编辑'}</Button>
                      <Button variant="ghost" size="sm" onClick={() => onRemoveTask(index)} disabled={loading}>删除</Button>
                    </div>
                  </div>
                  {row.errorMessage && (
                    <div className="batch-task-item__error" role="alert">
                      {getSafeErrorMessage(row.errorMessage, '本项生成未完成，请检查资料或稍后重试。')}
                    </div>
                  )}
                  {taskValidationErrors[index] && <div className="batch-task-item__error" role="alert">{taskValidationErrors[index]}</div>}
                  {row.expanded && (
                    <div className="batch-task-editor-grid">
                      <FormControl label="文种"><GenreSelect genres={options.genres} value={row.documentType} onChange={event => onTaskChange(index, 'documentType', event.target.value)} disabled={loading || !catalogReady} /></FormControl>
                      <FormControl label="写作场景"><SceneSelect scenes={options.scenes} value={row.scene} onChange={event => onTaskChange(index, 'scene', event.target.value)} disabled={loading || !catalogReady} /></FormControl>
                      <FormControl label="层级"><select value={row.level} onChange={event => onTaskChange(index, 'level', event.target.value)} disabled={loading}>{options.level.map(option => <option key={option}>{option}</option>)}</select></FormControl>
                      <FormControl label="单位类型"><select value={row.orgType} onChange={event => onTaskChange(index, 'orgType', event.target.value)} disabled={loading}>{options.orgType.map(option => <option key={option}>{option}</option>)}</select></FormControl>
                      <FormControl label="业务领域"><select value={row.businessDomain} onChange={event => onTaskChange(index, 'businessDomain', event.target.value)} disabled={loading}>{options.businessDomain.map(option => <option key={option}>{option}</option>)}</select></FormControl>
                      <FormControl label="写作目的"><select value={row.writingPurpose} onChange={event => onTaskChange(index, 'writingPurpose', event.target.value)} disabled={loading}>{options.writingPurpose.map(option => <option key={option}>{option}</option>)}</select></FormControl>
                      <FormControl label="目标字数"><input type="number" min="500" max="5000" step="100" value={row.wordCount} onChange={event => onTaskChange(index, 'wordCount', event.target.value)} disabled={loading} /></FormControl>
                      <FormControl label="参考资料范围"><select value={row.referenceScope} onChange={event => onTaskChange(index, 'referenceScope', event.target.value)} disabled={loading}>{options.referenceScope.map(option => <option key={option}>{option}</option>)}</select></FormControl>
                      <FormControl label="生成篇数" description="每项任务支持 1 篇，多篇请拆分任务。"><input type="number" min="1" max="1" step="1" value={row.count || 1} onChange={event => onTaskChange(index, 'count', Math.max(1, parseInt(event.target.value, 10) || 1))} disabled={loading} /></FormControl>
                      {row.articles?.length > 1 && (
                        <div className="batch-task-results">
                          <strong>真实返回结果</strong>
                          {row.articles.map((article, articleIndex) => (
                            <button key={`${article.log_id || article.title}-${articleIndex}`} type="button" onClick={() => onOpenArticle(article)}>{article.title || `结果 ${articleIndex + 1}`}</button>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </article>
              )
            })}
          </div>
        )}
      </section>

      <section className="core-panel batch-action-bar">
        <div>
          <strong>计划生成 {totalCount} 篇</strong>
          <span>任务将按现有前端逻辑依次请求；页面不展示不存在的队列或实时进度。</span>
        </div>
        <Button variant="primary" size="lg" onClick={onGenerate} disabled={loading || !catalogReady || totalCount === 0 || taskValidationErrors.some(Boolean)}>
          开始批量生成
        </Button>
      </section>
    </div>
  )
}
