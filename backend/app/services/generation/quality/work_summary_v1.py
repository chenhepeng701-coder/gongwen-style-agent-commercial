"""Work Summary Quality Contract V1.0.

All product semantics in this module are derived from the frozen V1.0 quality
documents.  Dataclass/enum shapes and stable identifiers are engineering
representations.  This module does not inspect, score, plan, or generate a draft.
"""

from __future__ import annotations

from .models import (
    AISmell,
    AutomationBoundary,
    AutomationLevel,
    CommercialQualityLevel,
    ContentFunction,
    CurrentSystemCoverage,
    GoldenRegistryMetadata,
    GoldenSample,
    HardGate,
    HeadingPolicy,
    NextStepPolicy,
    ObservableIndicator,
    ProblemPolicy,
    ProblemPolicyCase,
    QualityDimension,
    SampleTier,
    ScenarioProfile,
    SourceReference,
    SparseMaterialPolicy,
    UniversalRule,
    WorkChainDefinition,
    WorkSummaryQualityContract,
)


QUALITY_BASELINE = "docs/quality/工作总结质量基准_V1.0.md"
GOLDEN_CALIBRATION = "docs/quality/工作总结黄金样本标定_V1.0.md"
QUALITY_GAP_AUDIT = "docs/audit/COMMERCIAL_WORK_SUMMARY_QUALITY_GAP_AUDIT_01.md"


def _q(section: str, lines: str) -> SourceReference:
    return SourceReference(QUALITY_BASELINE, section, lines)


def _g(section: str, lines: str) -> SourceReference:
    return SourceReference(GOLDEN_CALIBRATION, section, lines)


UNIVERSAL_RULES = (
    UniversalRule(
        "dynamic_structure",
        "固定文种底线与动态结构",
        "先判断内容职能，再由任务和材料决定章节数量、合并方式与篇幅分配。",
        AutomationLevel.ASSISTED,
        _q("§3；§4", "28；35-63"),
    ),
    UniversalRule(
        "fact_first",
        "真实工作事实为主体",
        "工作总结以真实工作事实为主体，概括用于组织，必要分析仅作辅助。",
        AutomationLevel.ASSISTED,
        _q("§2；§3", "20-24；29"),
    ),
    UniversalRule(
        "work_chain_first",
        "工作链优先",
        "优先组织事实之间的连续工作链，不在单条事实之后扩写大量理论。",
        AutomationLevel.ASSISTED,
        _q("§3；§7.4", "30；138-152"),
    ),
    UniversalRule(
        "supported_outcome",
        "成效必须有支撑",
        "结果性评价必须有工作过程、事实、数据或明确证据支撑。",
        AutomationLevel.ASSISTED,
        _q("§3；§5 G6；§7.5", "31；84-85；154-162"),
    ),
    UniversalRule(
        "specific_problem",
        "问题具体且单位化",
        "问题应像本单位自己的问题，指向具体对象、表现、约束和现实影响。",
        AutomationLevel.ASSISTED,
        _q("§3；§7.6", "32；164-172"),
    ),
    UniversalRule(
        "carry_over_next_step",
        "下一步承接前文",
        "下一步优先承接未完成任务和前述问题，并形成具体可执行安排。",
        AutomationLevel.ASSISTED,
        _q("§3；§7.7", "32；174-183"),
    ),
    UniversalRule(
        "restrained_heading",
        "标题服务内容",
        "标题首先负责组织真实材料、区分业务板块，修辞不得压过信息。",
        AutomationLevel.ASSISTED,
        _q("§7.8", "185-187"),
    ),
    UniversalRule(
        "restrained_language",
        "语言克制且由信息承担篇幅",
        "语言应保持高信息承载率，少重复、少意义扩写，不以同义词替换冒充去 AI 味。",
        AutomationLevel.ASSISTED,
        _q("§7.9", "189-193"),
    ),
    UniversalRule(
        "sparse_material_policy",
        "稀疏材料宁可少写",
        "资料不足时应缩短、合并或明确缺口，不用套话、分析或虚构撑满篇幅。",
        AutomationLevel.ASSISTED,
        _q("§3；§5 G5", "33；81-82"),
    ),
    UniversalRule(
        "no_theoretical_padding",
        "禁止理论填充",
        "不得把少量事实扩成大量意义、机制、规律或正确但空泛的理论解释。",
        AutomationLevel.ASSISTED,
        _q("§2；§5 G5", "24；81-82"),
    ),
)


HARD_GATES = (
    HardGate(
        "G1",
        "文种错位",
        "工作总结不得写成领导讲话、理论评论、工作部署、经验交流或政策解读。",
        "BLOCKING",
        AutomationLevel.ASSISTED,
        "需要正文文体、任务场景及总结主体占比等文本证据。",
        CurrentSystemCoverage.FUTURE_QUALITY_GATE,
        _q("§5 G1", "69-70"),
    ),
    HardGate(
        "G2",
        "无依据创造现实事实",
        "不得自行创造或补全时间、数字、机构、文件、会议、项目、人员、动作、结果、荣誉或现实因果。",
        "BLOCKING",
        AutomationLevel.AUTOMATIC,
        "现实 FACT 必须追溯至合法 evidence_id / Fact Anchor。",
        CurrentSystemCoverage.CURRENT,
        _q("§5 G2", "72-73"),
    ),
    HardGate(
        "G3",
        "阶段状态失真",
        "不得把计划、拟开展、推进中或部分完成改写成已完成、全面完成或显著成效。",
        "BLOCKING",
        AutomationLevel.AUTOMATIC,
        "需要材料状态与正文完成态逐项对照。",
        CurrentSystemCoverage.CURRENT,
        _q("§5 G3", "75-76"),
    ),
    HardGate(
        "G4",
        "核心工作严重遗漏",
        "不得因结构简化整体丢弃资料中的重要业务板块、关键项目、关键指标或重点任务。",
        "BLOCKING",
        AutomationLevel.ASSISTED,
        "需要资料中的核心业务清单与成稿覆盖关系。",
        CurrentSystemCoverage.FUTURE_QUALITY_GATE,
        _q("§5 G4", "78-79"),
    ),
    HardGate(
        "G5",
        "用理论和分析填补事实缺口",
        "材料不足时必须缩短、合并或明确缺口，不得自动扩写意义、机制、治理转型或能力提升。",
        "BLOCKING",
        AutomationLevel.ASSISTED,
        "需要事实单元与分析性篇幅的对应关系及人工语义复核。",
        CurrentSystemCoverage.FUTURE_QUALITY_GATE,
        _q("§5 G5", "81-82"),
    ),
    HardGate(
        "G6",
        "无支撑成效",
        "显著提升、形成良好局面等结果性评价必须有前文事实、数据或明确证据支撑。",
        "BLOCKING",
        AutomationLevel.ASSISTED,
        "需要成效表达与前置事实、过程或结果证据的关联。",
        CurrentSystemCoverage.PARTIAL,
        _q("§5 G6", "84-85"),
    ),
    HardGate(
        "G7",
        "结构为通过当前案例而硬编码",
        "不得把单一样本的一级标题数量或结构固化为产品通用规则。",
        "BLOCKING",
        AutomationLevel.ASSISTED,
        "需要对合同、Planner、Prompt 与回归断言进行静态审计和场景复核。",
        CurrentSystemCoverage.FUTURE_QUALITY_GATE,
        _q("§5 G7", "87-88"),
    ),
)


QUALITY_DIMENSIONS = (
    QualityDimension(
        "document_scenario_fit", "文种与场景适配", 10,
        "成稿应自然体现当前工作总结子场景与单位职能，而非万能稿。",
        AutomationLevel.ASSISTED,
        ("文种分类", "场景特征覆盖", "单位可替换程度"),
        _q("§6；§7.1", "96；110-114"),
    ),
    QualityDimension(
        "structure_organization", "结构组织能力", 12,
        "材料按实际逻辑归类、取舍、排序，板块边界清楚且主次分明。",
        AutomationLevel.ASSISTED,
        ("内容职能覆盖", "板块边界", "重点与篇幅匹配", "机械均分迹象"),
        _q("§6；§7.2", "97；116-124"),
    ),
    QualityDimension(
        "material_information_density", "材料与信息密度", 18,
        "单位篇幅承载具体对象、动作、数字、项目、状态和节点的程度。",
        AutomationLevel.ASSISTED,
        ("事实单元", "具体对象", "具体动作", "项目与状态", "时间节点"),
        _q("§6；§7.3", "98；126-136"),
    ),
    QualityDimension(
        "work_chain_completeness", "工作链完整度", 16,
        "事实能否组成任务、对象、动作、执行及进度/结果的连续工作单元。",
        AutomationLevel.ASSISTED,
        ("标准工作链覆盖", "治理闭环覆盖", "孤立事实后的分析扩写"),
        _q("§6；§7.4", "99；138-152"),
    ),
    QualityDimension(
        "outcome_support", "成效与事实支撑", 10,
        "成效来自可观察工作结果，不以主观宣布替代证据。",
        AutomationLevel.ASSISTED,
        ("结果证据", "前文过程支撑", "弱成效篇幅"),
        _q("§6；§7.5", "100；154-162"),
    ),
    QualityDimension(
        "problem_quality", "问题分析质量", 8,
        "问题具体、单位化并指向真实矛盾，而非通用短板模板。",
        AutomationLevel.ASSISTED,
        ("问题对象", "具体表现", "原因或约束", "现实影响", "材料对应"),
        _q("§6；§7.6", "101；164-172"),
    ),
    QualityDimension(
        "next_step_quality", "下一步安排质量", 8,
        "下一步承接未完成任务和前述问题，并体现具体行动性。",
        AutomationLevel.ASSISTED,
        ("前文承接", "未完成任务关联", "问题关联", "行动与节点"),
        _q("§6；§7.7", "102；174-183"),
    ),
    QualityDimension(
        "heading_quality", "标题质量", 6,
        "标题准确组织材料、区分板块，修辞不压过信息。",
        AutomationLevel.ASSISTED,
        ("标题正文对应", "板块区分度", "机械排比迹象", "跨单位可替换性"),
        _q("§6；§7.8", "103；185-187"),
    ),
    QualityDimension(
        "language_paragraph_maturity", "语言与段落成熟度", 7,
        "语言克制、信息承载率高，减少重复与意义扩写，具备成熟机关语感。",
        AutomationLevel.MANUAL,
        ("重复表达", "意义扩写", "段落主题集中度", "成熟机关语感"),
        _q("§6；§7.9", "104；189-193"),
    ),
    QualityDimension(
        "user_editing_cost", "用户修改成本", 5,
        "判断用户只需核对微调，还是需要重搭结构、补事实或大幅重写。",
        AutomationLevel.MANUAL,
        ("事实核对范围", "标题段落调整范围", "结构重做范围", "补事实范围"),
        _q("§6；§7.10", "105；195-199"),
    ),
)


COMMERCIAL_QUALITY_LEVELS = (
    CommercialQualityLevel("A", "90-100", "商业优秀稿：结构适配、材料密、工作链完整、修改成本低", "核心目标", _q("§10", "225-232")),
    CommercialQualityLevel("B", "80-89", "商业可用稿：主体成立，需少量到中等调整", "可接受但需继续优化", _q("§10", "225-232")),
    CommercialQualityLevel("C", "70-79", "普通参考稿：有可用部分，但修改成本明显", "不是最终目标", _q("§10", "225-232")),
    CommercialQualityLevel("D", "<70", "反面/不合格：事实稀薄、模板化、错位或需大改", "否", _q("§10", "225-232")),
)


OBSERVABLE_INDICATORS = tuple(
    ObservableIndicator(indicator_id, name, description, level, _q("§11", "236-256"))
    for indicator_id, name, description, level in (
        ("document_scenario_match", "文种/场景匹配", "观测成稿与目标文种及子场景是否一致。", AutomationLevel.ASSISTED),
        ("core_business_coverage", "核心业务覆盖", "对照材料观测重要业务板块是否进入成稿。", AutomationLevel.ASSISTED),
        ("fact_unit_count", "事实单元数量", "识别可追溯的事实单元数量；不在本合同中设置阈值。", AutomationLevel.AUTOMATIC),
        ("concrete_entity_action_status", "对象/动作/项目/状态/节点", "识别具体对象、动作、项目、状态及节点。", AutomationLevel.AUTOMATIC),
        ("work_chain_completeness", "工作链/整改闭环完整度", "辅助判断事实是否形成标准工作链或治理闭环。", AutomationLevel.ASSISTED),
        ("summary_fact_balance", "概括句与事实句关系", "观测概括是否压过事实主体。", AutomationLevel.ASSISTED),
        ("unsupported_outcome", "无支撑成效表达", "识别结果性评价并核对前置支撑。", AutomationLevel.ASSISTED),
        ("problem_specificity", "问题具体程度", "辅助判断问题的对象、表现、约束和影响。", AutomationLevel.ASSISTED),
        ("problem_material_alignment", "问题与资料对应", "核对具体问题是否有材料依据。", AutomationLevel.ASSISTED),
        ("next_step_carry_over", "下一步与前文承接", "核对下一步是否承接问题、未完成任务、项目或节点。", AutomationLevel.ASSISTED),
        ("heading_body_alignment", "标题与正文对应", "观测标题和正文材料的一致性。", AutomationLevel.ASSISTED),
        ("paragraph_topic_focus", "段落主题集中度", "辅助判断段落是否围绕明确工作单元展开。", AutomationLevel.ASSISTED),
        ("abstract_padding", "空泛解释比例", "辅助识别意义、理论和抽象解释是否替代信息。", AutomationLevel.ASSISTED),
        ("unit_replaceability", "跨单位可替换程度", "评估更换单位名称后文本是否仍几乎成立。", AutomationLevel.MANUAL),
        ("estimated_edit_scope", "预计人工修改范围", "评估用户为交付所需的事实、结构和表达修改范围。", AutomationLevel.MANUAL),
    )
)


COMMON_PROHIBITED_STRUCTURE_ASSUMPTIONS = (
    "固定一级章节数量",
    "固定角色序列",
    "问题固定为某一章节位置",
    "下一步固定为末章",
    "平均分配各板块篇幅",
)


SCENARIO_PROFILES = (
    ScenarioProfile(
        "annual_summary", "年度工作总结", "全周期回顾年度任务完成、重大成果、问题和后续。",
        (ContentFunction.PERIOD_TASK_PROGRESS, ContentFunction.MAJOR_WORK, ContentFunction.VERIFIABLE_RESULT),
        (ContentFunction.PROBLEM_ANALYSIS, ContentFunction.NEXT_STEP_ARRANGEMENT, ContentFunction.DISTINCTIVE_PRACTICE),
        ("可按职责、重点任务或指标组织。", "问题与下一步按任务和材料决定是否独立呈现。"),
        "问题是否出现及放置方式取决于可靠材料与任务需要。",
        "后续安排按年度剩余任务、问题、项目、节点和既有计划决定。",
        "优先覆盖年度重点任务、指标、项目状态和可验证结果。",
        COMMON_PROHIBITED_STRUCTURE_ASSUMPTIONS + ("固定套用工作—问题—计划三段",),
        (_q("§4.2", "52-63"),),
    ),
    ScenarioProfile(
        "half_year_summary", "半年工作总结", "呈现阶段进度、重点任务完成度、未完成事项和后续安排。",
        (ContentFunction.PERIOD_TASK_PROGRESS, ContentFunction.MAJOR_WORK, ContentFunction.UNFINISHED_TASK),
        (ContentFunction.PROBLEM_ANALYSIS, ContentFunction.NEXT_STEP_ARRANGEMENT, ContentFunction.INDICATOR_STATUS),
        ("常见工作、问题、下步组合，也可按职责板块直接展开。", "是否分章按材料决定。"),
        "可独立、融入业务板块或在无可靠问题时省略具体问题。",
        "优先承接上半年未完成事项、问题、节点和下半年既定目标。",
        "关注阶段完成状态、未完成事项及后续节点。",
        COMMON_PROHIBITED_STRUCTURE_ASSUMPTIONS + ("因半年场景固定为四部分",),
        (_q("§4.2", "52-63"),),
    ),
    ScenarioProfile(
        "stage_summary", "阶段性总结", "围绕某项任务的阶段推进和节点结果。",
        (ContentFunction.PERIOD_TASK_PROGRESS, ContentFunction.CONCRETE_OBJECT_PROGRESS, ContentFunction.VERIFIABLE_RESULT),
        (ContentFunction.UNFINISHED_TASK, ContentFunction.NEXT_STEP_ARRANGEMENT, ContentFunction.PROBLEM_ANALYSIS),
        ("任务链或专项链优先。", "围绕当前任务范围组织，不扩展为综合总结。"),
        "仅在材料揭示阶段性约束时表达，位置按任务链决定。",
        "优先承接未完成节点和已有计划，可无独立章节。",
        "要求任务、对象、动作、执行状态和节点结果相互对应。",
        COMMON_PROHIBITED_STRUCTURE_ASSUMPTIONS + ("塞入无关综合工作",),
        (_q("§4.2", "52-63"),),
    ),
    ScenarioProfile(
        "special_summary", "专项工作总结", "呈现单一专项从部署、执行、整改到结果及后续的工作过程。",
        (ContentFunction.MAJOR_WORK, ContentFunction.GOVERNANCE_CHAIN, ContentFunction.VERIFIABLE_RESULT),
        (ContentFunction.PROBLEM_ANALYSIS, ContentFunction.UNFINISHED_TASK, ContentFunction.NEXT_STEP_ARRANGEMENT),
        ("可按行业领域或治理链展开。", "专项工作链和整改闭环优先。"),
        "问题优先来自排查、整改或复核环节的可靠材料。",
        "承接未销号事项、整改节点、专项计划及遗留问题。",
        "优先要求部署、对象、执行、整改、复核和结果等可观察事实。",
        COMMON_PROHIBITED_STRUCTURE_ASSUMPTIONS + ("写成综合年度总结",),
        (_q("§4.2", "52-63"), _g("§6 P2", "140-142")),
    ),
    ScenarioProfile(
        "long_period_summary", "五年/任期/换届等长周期总结", "呈现长期变化、重大工作、累积成果、机制沉淀和阶段演进。",
        (ContentFunction.LONG_PERIOD_CHANGE, ContentFunction.MAJOR_WORK, ContentFunction.VERIFIABLE_RESULT),
        (ContentFunction.PROBLEM_ANALYSIS, ContentFunction.NEXT_STEP_ARRANGEMENT, ContentFunction.REFORM_INNOVATION),
        ("可按领域和阶段组织。", "突出长期变化和阶段演进，不机械堆叠年度数据。"),
        "按材料表达长期约束或遗留问题，不为结构完整强加。",
        "承接长期未完成任务、机制建设和下一阶段目标，是否独立成章按场景决定。",
        "应能支持累计成果、阶段变化与机制沉淀的区分。",
        COMMON_PROHIBITED_STRUCTURE_ASSUMPTIONS + ("只堆多年度数据",),
        (_q("§4.2", "52-63"), _g("§6 P6", "156-158")),
    ),
    ScenarioProfile(
        "department_comprehensive_summary", "部门综合总结", "整合多职责、多专业条线和重点任务，体现部门业务全貌与重点。",
        (ContentFunction.MAJOR_WORK, ContentFunction.INDICATOR_STATUS, ContentFunction.CONCRETE_OBJECT_PROGRESS),
        (ContentFunction.PROBLEM_ANALYSIS, ContentFunction.NEXT_STEP_ARRANGEMENT, ContentFunction.DISTINCTIVE_PRACTICE),
        ("业务条线多、事实量大时优先按职责或重点任务分组。", "事实多的板块可细分，事实少的板块可合并。"),
        "通常可独立呈现，但仍以材料和任务需要为准。",
        "通常可独立呈现，也可按未完成任务与业务板块组织。",
        "覆盖核心职责、重点任务、项目/对象、指标状态与验证结果。",
        COMMON_PROHIBITED_STRUCTURE_ASSUMPTIONS + ("各业务条线平均分段",),
        (_g("§6 P1", "136-138"), _q("§7.2", "116-124")),
    ),
    ScenarioProfile(
        "party_building_summary", "党建/全面从严治党总结", "呈现政治建设、组织建设、责任链、纪律作风及业务融合。",
        (ContentFunction.MAJOR_WORK, ContentFunction.GOVERNANCE_CHAIN, ContentFunction.REFORM_INNOVATION),
        (ContentFunction.PROBLEM_ANALYSIS, ContentFunction.NEXT_STEP_ARRANGEMENT, ContentFunction.VERIFIABLE_RESULT),
        ("可按责任链和业务融合组织。", "数字不是唯一质量指标，具体任务、对象、动作和结果优先。"),
        "问题须来自具体任务、整改或业务融合材料，不使用通用党建短板凑章。",
        "承接整改、业务融合任务、未完成事项和明确计划，可融入业务板块。",
        "关注具体任务、对象、动作、整改/结果及业务融合。",
        COMMON_PROHIBITED_STRUCTURE_ASSUMPTIONS + ("只有政治概念而无具体动作",),
        (_q("§4.2", "52-63"), _g("§6 P5", "152-154")),
    ),
    ScenarioProfile(
        "learning_education_summary", "学习教育总结", "呈现学习、查摆、整改、建章立制等任务闭环及业务结合。",
        (ContentFunction.PERIOD_TASK_PROGRESS, ContentFunction.GOVERNANCE_CHAIN, ContentFunction.VERIFIABLE_RESULT),
        (ContentFunction.PROBLEM_ANALYSIS, ContentFunction.NEXT_STEP_ARRANGEMENT, ContentFunction.REFORM_INNOVATION),
        ("可按学习、查摆、整改、建章立制或实际任务组织。", "实际材料优先于固定套路。"),
        "问题应来自查摆和整改材料，可融入任务闭环。",
        "承接未整改事项、后续节点和既定教育计划，可不单设章节。",
        "重点观察具体任务、对象、动作、整改、机制与业务融合。",
        COMMON_PROHIBITED_STRUCTURE_ASSUMPTIONS + ("把学查改建同一套路复制到所有单位",),
        (_q("§4.2", "52-63"), _g("§5.2；§6 P5", "130-154")),
    ),
    ScenarioProfile(
        "township_comprehensive_summary", "乡镇综合总结", "按镇域实际权重整合经济、农业、民生、治理、安全等多业务。",
        (ContentFunction.MAJOR_WORK, ContentFunction.INDICATOR_STATUS, ContentFunction.CONCRETE_OBJECT_PROGRESS),
        (ContentFunction.PROBLEM_ANALYSIS, ContentFunction.NEXT_STEP_ARRANGEMENT, ContentFunction.DISTINCTIVE_PRACTICE),
        ("按镇域核心任务和实际权重组织。", "多线并行时动态分配板块和篇幅。"),
        "问题可独立或进入相关业务板块，以镇域材料为准。",
        "承接镇域项目、民生任务、治理问题、安全事项和时间节点。",
        "覆盖真实重点领域，避免以形式上的齐全替代实际权重。",
        COMMON_PROHIBITED_STRUCTURE_ASSUMPTIONS + ("各业务平均分配篇幅",),
        (_q("§4.2", "52-63"), _g("§6 P4", "148-150")),
    ),
)


WORK_CHAIN_DEFINITION = WorkChainDefinition(
    definition="成熟工作单元优先呈现任务到进度/结果的连续链；治理整改类可呈现发现、建档、整改、复核、销号闭环。",
    standard_elements=("任务", "对象", "采取动作", "执行过程", "进度/结果"),
    governance_elements=("发现/排查问题", "建档", "整改", "复核", "销号"),
    semantic_constraints=(
        "只描述质量语义，不在 V1.0 合同中实现自动 grouping。",
        "合同不得创建事实关系或成为事实来源。",
        "后续实现仍须保留每个 evidence_id 的独立可验证性。",
    ),
    source_reference=_q("§7.4", "138-152"),
)


SPARSE_MATERIAL_POLICY = SparseMaterialPolicy(
    trigger="有效材料不足以支撑目标篇幅或完整展开。",
    allowed_responses=("减少展开", "合并内容", "缩短建议篇幅", "降低质量等级", "提示材料不足或明确缺口"),
    prohibited_responses=("理论填充", "意义填充", "以常识补现实事实", "无材料生成具体问题", "无材料生成具体成效"),
    source_reference=(_q("§3", "33"), _q("§5 G5", "81-82")),
)


PROBLEM_POLICY = ProblemPolicy(
    universal_section_required=False,
    cases=(
        ProblemPolicyCase("材料有明确问题", "可形成独立问题部分，也可融入对应业务部分。"),
        ProblemPolicyCase("材料只有局部短板", "可在相关业务板块内表达。"),
        ProblemPolicyCase("材料没有可靠问题", "不得为结构完整强行生成具体问题。"),
    ),
    high_quality_components=("具体对象", "具体表现", "形成原因/约束", "现实影响"),
    prohibited_default_templates=("创新不足", "机制不足", "队伍不足", "数字化不足"),
    source_reference=(_q("§4.1", "39-50"), _q("§7.6", "164-172")),
)


NEXT_STEP_POLICY = NextStepPolicy(
    universal_section_required=False,
    preferred_sources=("未完成任务", "当前问题", "已有项目", "时间节点", "已明确计划", "年度剩余目标"),
    allowed_placements=("独立下一步章节", "与问题合并", "融入业务板块", "按场景不设置独立下一步章节"),
    prohibited_default_templates=("强化学习", "完善机制", "加强队伍", "创新方式"),
    source_reference=(_q("§4.1", "39-50"), _q("§7.7", "174-183")),
)


HEADING_POLICY = HeadingPolicy(
    desirable_properties=("对应正文真实内容", "区分不同业务板块", "组织材料", "允许朴素职责型或适度提炼"),
    prohibited_assumptions=("标题必须统一字数", "标题必须同句式", "为形式追求机械对仗", "标题必须使用特定动词"),
    source_reference=_q("§7.8", "185-187"),
)


AI_SMELLS = tuple(
    AISmell(smell_id, name, definition, level, signals, _q("§8", "201-213"))
    for smell_id, name, definition, level, signals in (
        ("low_fact_density", "事实稀薄", "少量事实之后出现大量解释。", AutomationLevel.ASSISTED, ("事实单元稀少", "解释篇幅明显扩张")),
        ("evaluation_before_evidence", "评价先行", "没有过程支撑就先宣布显著提升等成效。", AutomationLevel.ASSISTED, ("结果性评价先于过程", "成效缺少前置支撑")),
        ("unsupported_causality", "过度因果", "由单一动作自动推出治理能力、满意度或机制完善等结果。", AutomationLevel.ASSISTED, ("因果跨度过大", "结果缺少中间过程")),
        ("generic_problem", "万能问题", "使用创新、机制、队伍、数字化等概括替代真实问题。", AutomationLevel.ASSISTED, ("问题缺少对象", "问题缺少具体表现", "跨单位可替换")),
        ("generic_next_step", "万能下步", "无论前文内容都输出学习、机制、人才、创新等通用安排。", AutomationLevel.ASSISTED, ("与前文缺少承接", "安排缺少对象或节点")),
        ("mechanical_parallelism", "机械排比", "所有标题的字数、句式和节奏高度一致。", AutomationLevel.AUTOMATIC, ("标题长度高度一致", "句式结构高度一致")),
        ("significance_padding", "意义扩写", "反复解释工作重要性，以修辞承担篇幅。", AutomationLevel.ASSISTED, ("重要性解释重复", "未新增事实信息")),
        ("pseudo_depth", "假深度", "抽象程度高但没有新增事实。", AutomationLevel.ASSISTED, ("抽象概念密集", "事实增量低")),
        ("high_unit_replaceability", "单位可替换性高", "更换单位名称后文章仍几乎成立。", AutomationLevel.MANUAL, ("单位职能不明显", "业务对象不具体", "表达普适化")),
    )
)


def _sample(
    sample_id: str,
    title: str,
    tier: SampleTier,
    scenario: str,
    values: tuple[int, int, int],
    use: str,
    lines: str,
) -> GoldenSample:
    material, structure, expression = values
    return GoldenSample(sample_id, title, tier, scenario, structure, material, expression, use, _g("§4；§5", lines))


# Registry membership was frozen by the user-confirmed product decision in
# COMMERCIAL-WORK-SUMMARY-24-SAMPLE-REGISTRY-FREEZE-01.  Per-sample descriptive
# metadata remains traceable to the frozen V1.0 calibration document.
GOLDEN_SAMPLE_REGISTRY = (
    _sample("S01", "XX 区安全生产治本攻坚三年行动 2026 年阶段性工作总结", SampleTier.CORE, "专项工作", (5, 5, 4), "专项治理型核心锚点", "35-50；60"),
    _sample("S02", "XX县民政局2026年上半年工作总结及下半年工作安排", SampleTier.CORE, "部门综合", (5, 5, 4), "民生项目型核心锚点", "35-50；61"),
    _sample("S03", "XX县工信局2026年重点工作总结", SampleTier.CORE, "部门综合", (5, 5, 4), "产业经济类核心对标", "35-50；62"),
    _sample("S04", "卫健委2026年上半年工作总结", SampleTier.CORE, "部门综合", (5, 5, 4), "多专业条线高信息密度对标", "35-50；63"),
    _sample("S05", "县人社局2026年上半年工作总结及下一步工作打算", SampleTier.CORE, "部门综合", (5, 5, 5), "民生服务型核心锚点", "35-50；64"),
    _sample("S06", "2026年上半年XX区生态环境工作总结", SampleTier.CORE, "部门综合", (5, 5, 4), "指标—工作—问题—下步成熟结构对标", "35-50；65"),
    _sample("S07", "XX市生态环境局XX分局2026年半年工作总结及下一步计划", SampleTier.CORE, "部门综合", (5, 5, 4), "专业监管型核心参考", "35-50；66"),
    _sample("S09", "党委办2026年上半年工作总结及下半年工作思路", SampleTier.CORE, "部门综合", (5, 4, 5), "短篇高密度职责型锚点", "35-50；68"),
    _sample("S10", "司法局上半年工作总结及下半年工作打算", SampleTier.CORE, "部门综合", (5, 5, 4), "司法业务链与案件服务数据对标", "35-50；69"),
    _sample("S13", "市自然资源和规划局（市林业局）2026年上半年工作总结及下半年工作计划", SampleTier.CORE, "部门综合", (5, 5, 4), "任务清单型结构锚点", "35-50；72"),
    _sample("S24", "区科技和工业信息化局2026年上半年工作总结", SampleTier.BOUNDARY, "部门综合", (5, 4, 4), "无独立问题/下步板块与高业务事实密度边界样本", "83；160-168"),
    _sample("S30", "XX镇委员会关于2021-2025年及近期工作情况的总结报告(换届总结)", SampleTier.BOUNDARY, "换届/长周期", (4, 4, 4), "长周期叙事与概括评价偏多边界样本", "89；160-168"),
    _sample("S31", "市经信局党组2026年上半年抓基层党建工作总结", SampleTier.BOUNDARY, "党建/业务融合", (4, 4, 4), "党建业务融合正向边界样本", "90；160-168"),
    _sample("S39", "党建办 2026 年上半工作总结", SampleTier.BOUNDARY, "党建", (3, 4, 4), "基层党建短稿边界样本", "98；160-168"),
    _sample("S41", "局机关关于树立和践行正确zjg学习教育阶段性工作总结（交通运输）（汇编3）", SampleTier.BOUNDARY, "学习教育", (3, 4, 4), "学习教育业务融合正向边界样本", "100；160-168"),
    _sample("S50", "关于树立和践行正确zjg学习教育阶段性工作总结（镇乡）（汇编2）", SampleTier.BOUNDARY, "学习教育", (3, 3, 3), "学习教育套路感边界样本", "109；121-132；160-168"),
    _sample("S52", "县委开展树立和践行正确政绩观学习教育阶段工作总结（专题汇编2）", SampleTier.BOUNDARY, "学习教育", (3, 3, 3), "专题教育场景边界样本", "111；121-132；160-168"),
    _sample("S54", "局树立和践行正确政绩观学习教育工作总结报告（专题汇编3）", SampleTier.BOUNDARY, "学习教育", (2, 3, 2), "反面边界样本：占位符与模板化", "113；121-132；160-168"),
    _sample("S55", "关于树立和践行正确zjg学习教育阶段性工作总结（区委）（汇编1）", SampleTier.NEGATIVE, "学习教育", (2, 3, 2), "检测具体事实不足、意义与评价性扩写", "114；160-168"),
    _sample("S56", "医院退休党支部2026年上半年抓基层党建工作总结", SampleTier.NEGATIVE, "党建", (2, 3, 2), "检测活动/机制概括替代真实工作", "115；123-128；160-168"),
    _sample("S57", "XX单位树立和践行正确政绩观学习教育工作总结报告（专题汇编1）", SampleTier.NEGATIVE, "学习教育", (2, 3, 2), "检测长篇占位符和模板化", "116；160-168"),
    _sample("S58", "XX公司基层党支部2026年上半年工作总结", SampleTier.NEGATIVE, "党建", (2, 3, 2), "检测模板化和单位可替换性", "117；123-128；160-168"),
    _sample("S59", "2026年党建引领基层治理工作总结", SampleTier.NEGATIVE, "党建/基层治理", (1, 3, 2), "检测理论化和抽象化", "118；123-128；160-168"),
    _sample("S60", "XX 单位 2026 年上半年工作总结2", SampleTier.NEGATIVE, "通用总结", (2, 2, 2), "检测顺滑但空、意义扩写和万能下步", "119；123-128；160-168"),
)


GOLDEN_REGISTRY_METADATA = GoldenRegistryMetadata(
    source_stated_regression_sizes=(
        (SampleTier.CORE, 10),
        (SampleTier.BOUNDARY, 8),
        (SampleTier.NEGATIVE, 6),
    ),
    membership_status=(
        (SampleTier.CORE, "COMPLETE"),
        (SampleTier.BOUNDARY, "COMPLETE"),
        (SampleTier.NEGATIVE, "COMPLETE"),
    ),
    note=(
        "正式回归集规模为 Core-10、Boundary-8、Negative-6；"
        "24 个 sample_id 成员资格已由用户产品决策完整冻结。"
    ),
    source_reference=_g("§3；§5；§7", "35-50；121-132；160-166"),
)


AUTOMATION_BOUNDARIES = (
    AutomationBoundary(
        AutomationLevel.AUTOMATIC,
        "可由确定性 schema、计数、引用关系或静态结构检查稳定观测；自动可观测不等于自动判定商业质量。",
        ("合同 schema", "事实单元计数", "section/内容职能覆盖", "部分标题—正文相似度", "下一步链接存在性"),
        (_q("§11", "236-256"), _g("§8", "170-182")),
    ),
    AutomationBoundary(
        AutomationLevel.ASSISTED,
        "机器提供信号和候选判断，但须结合材料、场景或人工复核才能形成质量结论。",
        ("工作链完整度", "通用问题识别", "抽象填充识别", "成效支撑", "下一步承接"),
        (_q("§7；§8；§11", "108-213；236-256"), _g("§8", "170-182")),
    ),
    AutomationBoundary(
        AutomationLevel.MANUAL,
        "主要依赖熟悉场景和单位材料的人工评审，机器信号仅供参考。",
        ("成熟机关语感", "用户修改成本", "复杂场景的高级结构判断", "跨单位可替换程度"),
        (_q("§7.9；§7.10；§11", "189-199；236-256"), _g("§8", "170-182")),
    ),
)


WORK_SUMMARY_QUALITY_CONTRACT_V1 = WorkSummaryQualityContract(
    contract_id="work_summary_quality_contract",
    contract_version="1.0",
    document_type="工作总结",
    source_documents=(QUALITY_BASELINE, GOLDEN_CALIBRATION, QUALITY_GAP_AUDIT),
    universal_rules=UNIVERSAL_RULES,
    hard_gates=HARD_GATES,
    quality_dimensions=QUALITY_DIMENSIONS,
    commercial_quality_levels=COMMERCIAL_QUALITY_LEVELS,
    observable_indicators=OBSERVABLE_INDICATORS,
    scenario_profiles=SCENARIO_PROFILES,
    content_functions=tuple(ContentFunction),
    work_chain_definition=WORK_CHAIN_DEFINITION,
    sparse_material_policy=SPARSE_MATERIAL_POLICY,
    problem_policy=PROBLEM_POLICY,
    next_step_policy=NEXT_STEP_POLICY,
    heading_policy=HEADING_POLICY,
    ai_smells=AI_SMELLS,
    golden_sample_registry=GOLDEN_SAMPLE_REGISTRY,
    golden_registry_metadata=GOLDEN_REGISTRY_METADATA,
    automation_boundaries=AUTOMATION_BOUNDARIES,
)
