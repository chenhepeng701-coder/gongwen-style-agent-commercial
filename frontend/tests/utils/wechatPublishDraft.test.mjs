import assert from 'node:assert/strict'
import {
  buildDocumentModeDraftText,
  getDocumentModeDraftFilename,
  getDocumentModeDraftSections,
} from '../../src/utils/documentModeDraft.js'

function testWrappedDraftIncludesAllNonEmptySections() {
  const article = {
    public_title: '公众号标题',
    analysis_wrapper_enabled: true,
    writing_tips: '先写背景\n再写措施',
    applicable_scenes: ['会议讲话', '工作汇报'],
    learning_points: ['开头承接', '结尾落点'],
    title: '正文标题',
    content: '第一段\n\n第二段',
    review_notes: ['点评一', '点评二'],
  }

  assert.equal(
    buildDocumentModeDraftText(article),
    [
      '标题：\n公众号标题',
      '写作提示：\n先写背景\n再写措施',
      '适用场景：\n1. 会议讲话\n2. 工作汇报',
      '重点学习位置：\n1. 开头承接\n2. 结尾落点',
      '正文标题：\n正文标题',
      '正文内容：\n第一段\n\n第二段',
      '写法点评：\n1. 点评一\n2. 点评二',
    ].join('\n\n')
  )
}

function testPlainDraftOnlyIncludesPublishTitleBodyTitleAndContent() {
  const article = {
    public_title: '公众号标题',
    analysis_wrapper_enabled: false,
    writing_tips: '不应出现',
    applicable_scenes: ['不应出现'],
    learning_points: ['不应出现'],
    title: '正文标题',
    content: '正文第一段\n正文第二段',
    review_notes: ['不应出现'],
  }

  const sections = getDocumentModeDraftSections(article)

  assert.deepEqual(sections.map(section => section.label), ['标题', '正文标题', '正文内容'])
  assert.equal(
    buildDocumentModeDraftText(article),
    '标题：\n公众号标题\n\n正文标题：\n正文标题\n\n正文内容：\n正文第一段\n正文第二段'
  )
}

function testAppreciationDraftUsesOnlyNewAppreciationSections() {
  const article = {
    article_mode: 'document_mode',
    public_title: '公众号标题',
    title: '完整范文标题',
    content: '完整范文正文第一段\n完整范文正文第二段',
    appreciation: {
      appreciation_title: '赏析标题',
      appreciation_intro: '这类材料最怕只列问题不见整改链条。',
      selected_segments: [
        {
          excerpt: '逐项认领反馈问题，建立整改台账。',
          key_sentence: '建立整改台账。',
          comment: '这句好在把问题认领落到台账抓手上，适合放在巡察整改报告的“问题认领”部分。',
          phrases: ['逐项认领', '整改台账'],
          rewrite: ['逐条建立问题台账，细化整改责任和完成节点。'],
          usage: '可放在巡察整改报告的“问题认领”部分。',
          copy_warning: '不要照抄台账句，换到本单位时要替换真实责任主体。',
        },
        {
          excerpt: '每周调度进展、每月复盘成效。',
          key_sentence: '每周调度进展、每月复盘成效。',
          comment: '这句好在把整改推进写成固定节点，适合放在“跟踪问效”部分。',
          phrases: ['调度进展', '复盘成效'],
          rewrite: ['定期调度整改进度，阶段性复盘整改成效。'],
          usage: '可放在整改报告的“跟踪问效”部分。',
          copy_warning: '不要照抄频次，按真实调度机制替换。',
        },
      ],
      writing_reminder: '写整改报告要把问题、责任、措施和结果接起来。',
      framework_analysis: '这类材料通常按“问题认领—责任分解—闭环整改”展开。',
      source_titles: ['巡察整改报告', '整改推进材料'],
    },
  }

  const sections = getDocumentModeDraftSections(article)

  assert.deepEqual(sections.map(section => section.label), [
    '标题',
    '写法参考导语',
    '材料框架解析',
    '精华片段一',
    '关键句',
    '拆句点评',
    '可借鉴词句',
    '可替换表达',
    '适用场景',
    '不要照抄提醒',
    '精华片段二',
    '关键句',
    '拆句点评',
    '可借鉴词句',
    '可替换表达',
    '适用场景',
    '不要照抄提醒',
    '写作提醒',
    '资料来源标题',
  ])
  assert.equal(
    buildDocumentModeDraftText(article),
    [
      '标题：\n公众号标题',
      '写法参考导语：\n这类材料最怕只列问题不见整改链条。',
      '材料框架解析：\n这类材料通常按“问题认领—责任分解—闭环整改”展开。',
      '精华片段一：\n逐项认领反馈问题，建立整改台账。',
      '关键句：\n建立整改台账。',
      '拆句点评：\n这句好在把问题认领落到台账抓手上，适合放在巡察整改报告的“问题认领”部分。',
      '可借鉴词句：\n1. 逐项认领\n2. 整改台账',
      '可替换表达：\n1. 逐条建立问题台账，细化整改责任和完成节点。',
      '适用场景：\n可放在巡察整改报告的“问题认领”部分。',
      '不要照抄提醒：\n不要照抄台账句，换到本单位时要替换真实责任主体。',
      '精华片段二：\n每周调度进展、每月复盘成效。',
      '关键句：\n每周调度进展、每月复盘成效。',
      '拆句点评：\n这句好在把整改推进写成固定节点，适合放在“跟踪问效”部分。',
      '可借鉴词句：\n1. 调度进展\n2. 复盘成效',
      '可替换表达：\n1. 定期调度整改进度，阶段性复盘整改成效。',
      '适用场景：\n可放在整改报告的“跟踪问效”部分。',
      '不要照抄提醒：\n不要照抄频次，按真实调度机制替换。',
      '写作提醒：\n写整改报告要把问题、责任、措施和结果接起来。',
      '资料来源标题：\n1. 巡察整改报告\n2. 整改推进材料',
    ].join('\n\n')
  )
  assert(!buildDocumentModeDraftText(article).includes('完整范文正文'))
}

function testHorizontalAppreciationDraftUsesComparisonSectionsOnly() {
  const article = {
    article_mode: 'document_mode',
    public_title: '几篇整改材料值得对照看',
    title: '完整范文标题',
    content: '完整范文正文不应出现',
    appreciation: {
      appreciation_scope: 'multi_horizontal',
      appreciation_intro: '同主题材料不要只看一篇。',
      horizontal_framework_analysis: '横向看，重点比较责任、台账和机制。',
      selected_segments: [
        {
          source_title: '材料一',
          best_learning_point: '责任拆得细。',
          excerpt: '明确责任领导、责任部门和完成节点。',
          key_sentence: '明确责任领导、责任部门和完成节点。',
          comment: '这句最值得学的是“责任领导、责任部门和完成节点”这组三连。',
          phrases: ['责任领导', '完成节点'],
          rewrite: ['逐项明确牵头领导、责任部门和完成节点。'],
          usage: '可放在巡察整改报告的“责任压实”部分。',
        },
        {
          source_title: '材料二',
          best_learning_point: '闭环写得实。',
          excerpt: '实行清单推进、定期调度、逐项销号。',
          key_sentence: '实行清单推进、定期调度、逐项销号。',
          comment: '这句最值得学的是“清单推进、定期调度、逐项销号”的推进链条。',
          phrases: ['清单推进', '逐项销号'],
          rewrite: ['围绕反馈事项实行清单推进、定期调度、逐项销号。'],
          usage: '可放在专项整治报告的“整改推进情况”部分。',
        },
      ],
      common_writing_summary: '共同点是都把任务拆成可跟踪、可复盘的动作链。',
      writing_reminder: '横向赏析要对照差别，不要混成一篇。',
      source_titles: ['材料一', '材料二'],
    },
  }

  const text = buildDocumentModeDraftText(article)

  assert.deepEqual(getDocumentModeDraftSections(article).map(section => section.label), [
    '标题',
    '写法参考导语',
    '横向框架解析',
    '材料一',
    '来源标题',
    '这篇最值得学的地方',
    '精华片段',
    '关键句',
    '拆句点评',
    '可借鉴词句',
    '可替换表达',
    '适用场景',
    '材料二',
    '来源标题',
    '这篇最值得学的地方',
    '精华片段',
    '关键句',
    '拆句点评',
    '可借鉴词句',
    '可替换表达',
    '适用场景',
    '共同写法总结',
    '写作提醒',
    '资料来源标题',
  ])
  assert(text.includes('共同写法总结：\n共同点是都把任务拆成可跟踪、可复盘的动作链。'))
  assert(!text.includes('完整范文正文'))
}

function testEmptyAndSentinelValuesAreSkipped() {
  const article = {
    public_title: '',
    analysis_wrapper_enabled: true,
    writing_tips: undefined,
    applicable_scenes: [],
    learning_points: [''],
    title: 'null',
    content: '正文',
    review_notes: {},
  }

  assert.equal(buildDocumentModeDraftText(article), '正文内容：\n正文')
}

function testFilenameSanitizesWindowsInvalidCharacters() {
  assert.equal(getDocumentModeDraftFilename('A/B:C*D?E"F<G>H|I'), 'Word导出_A_B_C_D_E_F_G_H_I.docx')
  assert.equal(getDocumentModeDraftFilename(''), 'Word导出_未命名.docx')
}

testWrappedDraftIncludesAllNonEmptySections()
testPlainDraftOnlyIncludesPublishTitleBodyTitleAndContent()
testAppreciationDraftUsesOnlyNewAppreciationSections()
testHorizontalAppreciationDraftUsesComparisonSectionsOnly()
testEmptyAndSentinelValuesAreSkipped()
testFilenameSanitizesWindowsInvalidCharacters()
console.log('wechatPublishDraft tests passed')
