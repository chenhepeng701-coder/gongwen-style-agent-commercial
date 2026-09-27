import React from 'react'

export function GenreSelect({ genres = [], value, ...props }) {
  const hasValue = genres.some(genre => genre.document_type === value)
  return (
    <select value={value || ''} {...props}>
      <option value="" disabled>{genres.length ? '请选择文种' : '文种目录加载中或不可用'}</option>
      {value && !hasValue && <option value={value} disabled>{value}（历史值，请重新选择）</option>}
      {genres.map(genre => (
        <option key={genre.genre_id} value={genre.document_type}
          disabled={!genre.production_ready || genre.generation_route !== 'full_article_drafting'}>
          {genre.display_name || genre.document_type} · {genre.production_ready && genre.generation_route === 'full_article_drafting' ? '已可生产' : '已接入，待商业验收'}
        </option>
      ))}
    </select>
  )
}

export function SceneSelect({ scenes = [], value, ...props }) {
  return (
    <select value={value || ''} {...props}>
      <option value="">自动 / 未指定</option>
      {value && !scenes.some(scene => scene.name === value)
        && <option value={value}>{value}（历史场景）</option>}
      {scenes.map(scene => <option key={scene.name} value={scene.name}>{scene.name}</option>)}
    </select>
  )
}

export function BusinessDomainSelect({ items = [], value, disabled, ...props }) {
  const ready = items.length > 0
  return (
    <select {...props} value={ready ? (value || '') : ''} disabled={disabled || !ready}>
      <option value="" disabled>{ready ? '请选择业务领域' : '业务领域目录加载中或不可用'}</option>
      {ready && value && !items.some(item => item.value === value)
        && <option value={value} disabled>{value}（历史值，已保留）</option>}
      {items.map(item => <option key={item.value} value={item.value}>{item.display_name}</option>)}
    </select>
  )
}
