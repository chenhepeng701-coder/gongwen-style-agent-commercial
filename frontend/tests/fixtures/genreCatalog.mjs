// Small test-only response: deliberately not a mirror of the 22-genre Registry.
export const readyGenre = (genre_id, document_type) => ({
  genre_id, document_type, display_name: document_type, aliases: [],
  production_ready: true, generation_route: 'full_article_drafting', export_formats: ['docx', 'txt'],
})

// Test response only. Production choices are loaded from /business-domains.
export const businessDomains = [
  { value: '纪检 / 廉政', display_name: '纪检 / 廉政', aliases: [] },
  { value: '巡察整改', display_name: '巡察整改', aliases: [] },
  ...['党建', '作风建设', '安全生产', '基层治理', '生态环保'].map(value => ({ value, display_name: value, aliases: [] })),
  { value: '合成标准域', display_name: '合成展示名', aliases: ['合成旧域甲', '合成旧域乙'] },
]

export const catalog = {
  genres: [
    readyGenre('work_summary', '工作总结'),
    readyGenre('speech', '讲话稿'),
    readyGenre('report', '汇报材料'),
    { ...readyGenre('plan', '工作方案'), aliases: ['实施方案'] },
  ],
  legacy_mappings: [
    { legacy_value: '领导讲话稿', document_type: '讲话稿', scene: null },
    { legacy_value: '工作汇报', document_type: '汇报材料', scene: null },
    { legacy_value: '实施方案', document_type: '工作方案', scene: null },
    { legacy_value: '党课讲稿', document_type: '讲话稿', scene: '党课讲稿' },
    { legacy_value: '组织生活会个人发言材料', document_type: '讲话稿', scene: '组织生活会' },
    { legacy_value: '民主生活会对照检查材料', document_type: '讲话稿', scene: '民主生活会对照检查' },
  ],
}
