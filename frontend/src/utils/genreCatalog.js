// These helpers consume server metadata only. Registry and Commercial Gate own
// genre identity/admission; this UI check never authorizes backend production.
export function normalizeScene(value) {
  const text = String(value ?? '').trim()
  return ['', 'auto', '自动', '未指定', '自动识别场景', '自动/未指定', '自动 / 未指定'].includes(text) ? null : text
}

export function readGenreCatalog(response) {
  if (!Array.isArray(response?.genres) || response.genres.length === 0
    || !Array.isArray(response.legacy_mappings)
    || response.genres.some(item => !item?.genre_id || !item.document_type
      || !Array.isArray(item.aliases) || typeof item.production_ready !== 'boolean'
      || !Array.isArray(item.export_formats)
      || (item.production_ready && item.generation_route !== 'full_article_drafting'))) {
    throw new Error('文种目录暂不可用，请重新加载后再生成。')
  }
  return response
}

export function readSceneTaxonomy(response) {
  if (!Array.isArray(response?.items) || response.items.some(item => !item?.name)) {
    throw new Error('写作场景暂不可用，请重新加载后再生成。')
  }
  return response.items
}

export async function loadWritingCatalogs(getGenres, getScenes) {
  const results = await Promise.allSettled([getGenres(), getScenes()])
  const failed = results.find(result => result.status === 'rejected')
  if (failed) throw failed.reason
  return { catalog: readGenreCatalog(results[0].value), scenes: readSceneTaxonomy(results[1].value) }
}

export function normalizeTaskGenre(task, catalog) {
  const value = String(task?.document_type ?? '').trim()
  const mapping = catalog?.legacy_mappings?.find(item => item.legacy_value === value)
  const identity = mapping?.document_type || value
  const genre = catalog?.genres?.find(item => (
    item.document_type === identity || item.genre_id === identity || item.aliases.includes(identity)
  ))
  return {
    ...task,
    document_type: genre?.document_type || value,
    scene: normalizeScene(task?.scene) || normalizeScene(mapping?.scene),
  }
}

export function normalizeUiTaskGenre(task, catalog) {
  const normalized = normalizeTaskGenre({ document_type: task?.documentType, scene: task?.scene }, catalog)
  return { ...task, documentType: normalized.document_type, scene: normalized.scene || '' }
}

export function normalizeRestoredTaskGenre(task, catalog, scenes) {
  // Older logs stored genre identities in scene, sometimes without documentType.
  // Clear only catalog-recognized genres once the live taxonomy rules them out;
  // real scene selections take priority, and unknown historical values survive.
  const scene = normalizeScene(task?.scene)
  const sceneGenre = normalizeTaskGenre({ document_type: scene }, catalog).document_type
  const legacyGenreScene = Array.isArray(scenes)
    && !scenes.some(item => item.name === scene)
    && catalog?.genres?.some(item => item.document_type === sceneGenre)
  return normalizeUiTaskGenre({ ...task, scene: legacyGenreScene ? '' : scene }, catalog)
}

export function productionTaskError(task, catalog, exportFormat = 'docx') {
  if (!catalog?.genres?.length) return '文种目录尚未加载成功，请重新加载后再生成。'
  const normalized = normalizeTaskGenre(task, catalog)
  const genre = catalog.genres.find(item => item.document_type === normalized.document_type)
  if (!genre) return `文种“${normalized.document_type || '未选择'}”无法确定，请选择支持的标准文种。`
  if (!genre.production_ready || genre.generation_route !== 'full_article_drafting') {
    return `“${genre.display_name || genre.document_type}”已接入，待商业验收，暂不能生成。`
  }
  if (Number(task?.count ?? 1) !== 1) return '每项任务仅支持生成 1 篇，请拆分为多项任务。'
  if (!genre.export_formats.includes(exportFormat)) return '当前文种不支持所选导出格式。'
  return ''
}
