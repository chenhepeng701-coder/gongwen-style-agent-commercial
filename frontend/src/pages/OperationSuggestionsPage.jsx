import React, { useEffect, useMemo, useState } from 'react'
import { getTopicSuggestions } from '../api'
import ErrorState from '../components/ErrorState'

const MODE_MAP = {
  '公文初稿': 'full_article',
  公文初稿: 'draft_article',
}

function MatchBadge({ level }) {
  const safeLevel = level || '无'
  return <span className={`operation-match operation-match-${safeLevel}`}>{safeLevel}</span>
}

function TopicCard({ item, onBringToArticleGen }) {
  return (
    <div className="operation-topic-card">
      <div className="operation-card-head">
        <div>
          <div className="operation-topic-title">{item.topic}</div>
          <div className="operation-topic-scene">{item.scene}</div>
        </div>
        <span className={`operation-priority operation-priority-${item.priority}`}>{item.priority}</span>
      </div>

      <p className="operation-reason">{item.timing_reason}</p>

      <div className="operation-card-row">
        <span className="operation-label">标题痛点方向</span>
        <div className="operation-tags">
          {(item.pain_points || []).map(point => <span key={point} className="tag">{point}</span>)}
        </div>
      </div>

      <div className="operation-card-row">
        <span className="operation-label">资料库关键词</span>
        <div className="operation-tags">
          {(item.keywords || []).map(keyword => <span key={keyword} className="scene-tag">{keyword}</span>)}
        </div>
      </div>

      <div className="operation-metrics">
        <div>
          <span className="operation-label">资料匹配度</span>
          <MatchBadge level={item.match_level} />
        </div>
        <div>
          <span className="operation-label">命中资料数量</span>
          <strong>{item.material_count}</strong>
        </div>
        <div>
          <span className="operation-label">推荐模式</span>
          <strong>{item.recommended_mode}</strong>
        </div>
      </div>

      <div className="operation-card-row">
        <span className="operation-label">建议补充资料</span>
        <div className="operation-tags">
          {(item.material_suggestions || []).map(suggestion => (
            <span key={suggestion} className="tag tag-green">{suggestion}</span>
          ))}
        </div>
      </div>

      <div className="operation-card-actions">
        <button className="btn btn-primary btn-sm" onClick={() => onBringToArticleGen(item)}>
          带入文章生成
        </button>
      </div>
    </div>
  )
}

export default function OperationSuggestionsPage({ onUseTopicSuggestion }) {
  const [suggestions, setSuggestions] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const loadSuggestions = async () => {
    setLoading(true)
    setError('')
    try {
      const res = await getTopicSuggestions()
      setSuggestions(Array.isArray(res) ? res : [])
    } catch (err) {
      setError(err.message || '写作场景建议加载失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadSuggestions()
  }, [])

  const highPriority = useMemo(
    () => suggestions.filter(item => item.priority === '高'),
    [suggestions],
  )

  const materialGaps = useMemo(
    () => suggestions.filter(item => item.match_level === '低' || item.match_level === '无'),
    [suggestions],
  )

  const handleBringToArticleGen = (item) => {
    onUseTopicSuggestion?.({
      title: item.topic,
      count: 1,
      scene: '',
      articleMode: MODE_MAP[item.recommended_mode] || 'draft_article',
      targetWords: item.recommended_mode === '公文初稿' ? 1800 : 1200,
      exportFormat: 'docx',
    })
  }

  return (
    <div>
      <h1>写作场景建议</h1>
      <p className="page-desc">根据当前月份、时效节点和资料库素材情况，推荐适合导出的公文选题。</p>

      <div className="operation-toolbar">
        <div>
          <strong>今日推荐选题</strong>
          <span className="text-muted"> 高优先级 {highPriority.length} 项 / 全部 {suggestions.length} 项</span>
        </div>
        <button className="btn btn-outline btn-sm" onClick={loadSuggestions} disabled={loading}>
          刷新
        </button>
      </div>

      {loading && (
        <div className="form-section">
          <p className="text-muted">正在加载写作场景建议...</p>
        </div>
      )}

      {error && !loading && (
        <div className="form-section">
          <ErrorState title="写作建议暂时无法加载" error={error} onRetry={loadData} />
        </div>
      )}

      {!loading && !error && (
        <>
          <section className="form-section">
            <h3>今日推荐选题</h3>
            <div className="operation-card-grid">
              {(highPriority.length ? highPriority : suggestions).map(item => (
                <TopicCard
                  key={item.topic}
                  item={item}
                  onBringToArticleGen={handleBringToArticleGen}
                />
              ))}
            </div>
          </section>

          <section className="form-section">
            <h3>资料补充建议</h3>
            {materialGaps.length > 0 ? (
              <div className="operation-gap-list">
                {materialGaps.map(item => (
                  <div key={item.topic} className="operation-gap-item">
                    <div>
                      <strong>{item.topic}</strong>
                      <span className="text-muted"> 命中 {item.material_count} 篇</span>
                    </div>
                    <MatchBadge level={item.match_level} />
                    <div className="operation-tags">
                      {(item.material_suggestions || []).map(suggestion => (
                        <span key={suggestion} className="tag tag-green">{suggestion}</span>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="empty-state">
                <p className="text-muted">当前推荐主题资料匹配度较好，暂无低匹配补充项。</p>
              </div>
            )}
          </section>

          <section className="form-section">
            <h3>高优先级选题</h3>
            <div className="operation-table-wrap">
              <table className="preset-table">
                <thead>
                  <tr>
                    <th>主题</th>
                    <th>文种/场景</th>
                    <th>匹配度</th>
                    <th>命中数</th>
                    <th>推荐模式</th>
                    <th>标题痛点方向</th>
                  </tr>
                </thead>
                <tbody>
                  {highPriority.map(item => (
                    <tr key={item.topic}>
                      <td><strong>{item.topic}</strong></td>
                      <td>{item.scene}</td>
                      <td><MatchBadge level={item.match_level} /></td>
                      <td>{item.material_count}</td>
                      <td>{item.recommended_mode}</td>
                      <td>{(item.pain_points || []).join(' / ')}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}
    </div>
  )
}
