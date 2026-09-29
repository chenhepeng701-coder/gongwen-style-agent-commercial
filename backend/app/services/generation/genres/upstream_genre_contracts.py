"""Minimum genre semantics derived from the pinned upstream doc-type specs.

Each entry follows that genre's ``spec.md`` writing rules and template. These
are lexical reading/quality observations, not evidence classification, mandatory
heading templates, or a claim of commercial calibration. Summary keeps its
existing owners and is deliberately absent from this extension table.
"""
from dataclasses import dataclass
from typing import Any, Mapping

from .commercial_contract import (
    CommercialGenreContract, CommercialMaturity, DeliveryPolicy,
    EvidenceOrganizationCapability, PlanningCapability, RecoveryPolicy,
    WriterCallPolicy, WriterControlPolicy,
)
from .contracts import StructureContract
from .retrieval import RetrievalProfile


@dataclass(frozen=True)
class FunctionalRole:
    role_id: str
    label: str
    cues: tuple[str, ...]
    required: bool = True


@dataclass(frozen=True)
class GenreSemanticSpec:
    upstream_id: str
    title: str
    roles: tuple[FunctionalRole, ...]
    identity_terms: tuple[str, ...]
    review_instruction: str
    order_pairs: tuple[tuple[str, str], ...] = ()
    closing_terms: tuple[str, ...] = ()
    closing_required: bool = False
    inappropriate_phrases: tuple[str, ...] = ()
    # Briefing titles can name the news item instead of repeating the masthead.
    title_identity_required: bool = True
    manual_elements: tuple[str, ...] = ()
    # A few established reading priorities can differ within a genre. Values
    # affect chunk reading only, never primary-document admission/ranking.
    reading_preferences: tuple[tuple[str, float], ...] = ()
    reading_source_roles: tuple[tuple[str, str], ...] = (("fact_source", "fact_source"),)

    @property
    def source_path(self):
        return f"prompts/doc-types/{self.upstream_id}-{self.title}/spec.md"


def _role(identity, label, *cues, required=True):
    return FunctionalRole(identity, label, (label, *cues), required)


_SPECS = (
    GenreSemanticSpec("announcement", "公告", (
        _role("announced_matter", "公告事项", "现公告", "现予公告", "决定", "宣布"),
        _role("implementation", "执行说明", "施行", "生效", "执行", "办理", "具体内容见附件")),
        ("公告",), "核对重要或法定公告事项、执行说明和落款；不设主送称谓，不把内部普通部署当公告。",
        (("announced_matter", "implementation"),), ("特此公告",),
        inappropriate_phrases=("妥否，请批示", "特此通知", "特此通告")),
    GenreSemanticSpec("approval", "批复", (
        _role("incoming_request", "来文依据", "请示》", "收悉", "现批复如下"),
        _role("approval_conclusion", "批复意见", "同意", "不同意", "暂不批准", "调整完善"),
        _role("execution", "执行要求", "组织实施", "报备", "遵照执行", required=False)),
        ("批复",), "核对原请示机关、来文与明确批复结论逐项对应；结论前置，短批复允许直接落款。",
        (("incoming_request", "approval_conclusion"),), ("特此批复", "此复", "请遵照执行"),
        inappropriate_phrases=("妥否，请批示", "特此函复")),
    GenreSemanticSpec("briefing", "简报", (
        _role("news_lead", "导语", "近日", "日前", "主要情况"),
        _role("key_information", "主要信息", "最新进展", "关键数据", "典型情况", "重要动向"),
        _role("followup_hint", "工作建议或提示", "后续关注", "工作提示", "风险提醒", required=False)),
        ("简报", "信息简报", "新闻简报"), "按一期一主题检查新闻导语与主要信息；版头期号、报送范围依用户模板，正文标题不必重复简报。",
        (("news_lead", "key_information"),),
        inappropriate_phrases=("妥否，请批示", "请予审批"), title_identity_required=False),
    GenreSemanticSpec("circular", "通报", (
        _role("verified_situation", "基本情况", "事实经过", "有关情况", "通报如下"),
        _role("evaluation", "评价分析", "原因分析", "教训", "表彰", "批评", "警示"),
        _role("work_requirements", "工作要求", "整改", "引以为戒", "学习", "防范")),
        ("通报",), "先区分表彰、批评或情况通报；核对事实、评价与要求，不把未经证实评价当作事实。",
        (("verified_situation", "evaluation"), ("evaluation", "work_requirements")), ("特此通报",),
        inappropriate_phrases=("妥否，请批示", "特此通知")),
    GenreSemanticSpec("communique", "公报", (
        _role("authoritative_context", "背景概况", "会议召开", "举行", "会议", "统计"),
        _role("published_content", "主要内容", "公布", "通过", "成果", "决定", "数据"),
        _role("publication_close", "结语", "后续安排", "发布说明", "号召", required=False)),
        ("公报",), "核对会议或重大事项背景、权威公布内容；会议公报和统计公报分别审阅，不强制套话。",
        (("authoritative_context", "published_content"),),
        inappropriate_phrases=("妥否，请批示", "请予审批")),
    GenreSemanticSpec("decision", "决定", (
        _role("decision_basis", "背景依据", "根据", "为了", "为进一步", "鉴于"),
        _role("decided_matters", "决定事项", "决定", "调整", "给予"),
        _role("execution_requirements", "执行要求", "贯彻执行", "施行", "负责", "落实")),
        ("决定",), "检查作出决定的背景依据、明确事项和执行要求；按公布、部署或奖惩类型复核落款。",
        (("decision_basis", "decided_matters"),), ("特此决定", "贯彻执行", "起施行"),
        inappropriate_phrases=("妥否，请批示", "以上汇报，请审示")),
    GenreSemanticSpec("letter", "函", (
        _role("correspondence_reason", "事项缘由", "根据", "为做好", "来函", "收悉"),
        _role("principal_opinion", "主要意见", "商洽", "协商", "函告", "请求", "同意", "函复"),
        _role("cooperation", "配合事项", "反馈", "协办", "支持", "材料", required=False)),
        ("函",), "核对平行或不相隶属对象，一函一事；商洽、询问、请批及答复各自闭合，不冒用下行批复口吻。",
        (("correspondence_reason", "principal_opinion"),), ("特此函达", "特此函商", "特此函复", "支持为盼", "感谢"),
        inappropriate_phrases=("特此批复", "现批复如下")),
    GenreSemanticSpec("minutes", "纪要", (
        _role("meeting_context", "会议基本信息", "会议概况", "召开", "主持", "议题"),
        _role("agreed_matters", "议定事项", "会议认为", "会议决定", "会议议定", "会议指出", "会议明确"),
        _role("responsibilities", "责任分工", "负责", "牵头", required=False),
        _role("followup", "后续要求", "督办", "反馈", "落实", required=False)),
        ("纪要",), "区分会议概况与议定事项；会议认为、会议决定是正文起句而非强制标题；不写逐字发言记录或寒暄结尾。",
        (("meeting_context", "agreed_matters"),),
        inappropriate_phrases=("特此通知", "特此报告", "妥否，请批示", "谢谢大家")),
    GenreSemanticSpec("motion", "议案", (
        _role("submitted_matter", "提请审议事项", "提请审议", "提请", "审议事项"),
        _role("statutory_reason", "依据与理由", "根据", "必要性", "可行性", "案由"),
        _role("proposed_content", "议案内容", "拟议", "方案", "条款", "主要内容"),
        _role("deliberation_request", "审议请求", "请予审议", "提请审议", "请审查批准")),
        ("议案",), "开门见山提请审议，说明法定依据、必要性与拟议内容；结尾须明确审议或审查批准请求。",
        (("submitted_matter", "statutory_reason"),), ("提请审议", "请予审议", "请审查批准"), True,
        ("妥否，请批示", "特此报告")),
    GenreSemanticSpec("notice", "通知", (
        _role("notice_purpose", "目的背景", "根据", "为做好", "为进一步", required=False),
        _role("notified_matters", "具体事项", "通知事项", "通知如下", "重点任务", "现将", "现印发"),
        _role("implementation_requirements", "工作要求", "有关要求", "报送", "时限", "负责", required=False)),
        ("通知",), "核对主送单位、通知事项和落款；部署、转发、知照类型分别处理，不把三段式或特此通知定为通用强制要素。",
        (("notice_purpose", "notified_matters"),), ("特此通知",),
        inappropriate_phrases=("妥否，请批示", "请予审批", "特此公告", "特此通告")),
    GenreSemanticSpec("opinion", "意见", (
        _role("overall_requirements", "背景与总体要求", "总体要求", "总体目标", "指导思想"),
        _role("principles", "基本原则", "工作原则", required=False),
        _role("policy_measures", "主要意见", "重点举措", "制度安排", "政策路径", "措施"),
        _role("organization", "组织实施", "推进机制", "监督评估", "保障措施")),
        ("意见",), "核对中长期指导与制度安排的总体要求、分领域意见和组织实施；上行报批与下行指导结语分别复核。",
        (("overall_requirements", "policy_measures"), ("policy_measures", "organization")),
        ("贯彻落实", "贯彻执行", "请批转执行"), inappropriate_phrases=("特此通知",)),
    GenreSemanticSpec("order", "命令（令）", (
        _role("order_number", "令号", "号令", "令第"),
        _role("issuing_authority", "发布依据", "根据", "通过", "批准", "签发"),
        _role("ordered_content", "命令内容", "现予公布", "命令", "通令嘉奖", "采取"),
        _role("effective_scope", "生效说明", "起施行", "施行范围", "执行要求", required=False),
        _role("signing", "签署与日期", "签署", "主席", "总理", "部长")),
        ("命令", "令"), "分别核对公布令、行政令和嘉奖令的法定签发权限、令号、发布依据、命令内容、签署日期；不得自动补全。",
        (("issuing_authority", "ordered_content"),), ("起施行", "特通令嘉奖"),
        inappropriate_phrases=("妥否，请批示", "特此通知"),
        manual_elements=("令号与签发权限", "签署人身份", "成文日期")),
    GenreSemanticSpec("presentation", "汇报材料", (
        _role("situation", "基本情况", "总体情况", required=False),
        _role("progress", "工作进展", "推进情况", "开展情况", "主要做法"),
        _role("achievement", "主要成效", "工作成效", "亮点", "经验"),
        _role("issues", "存在问题", "需要说明的问题", "困难", "不足", "风险"),
        _role("recommendations", "下一步措施", "下一步", "意见建议", "需协调", "请求")),
        ("汇报",), "按汇报对象与主题检查工作进展、成效、问题、下一步措施；可按板块组织，需领导关注事项不得冒作正式请示。",
        (("progress", "issues"), ("issues", "recommendations")), ("批评指正", "请审示"),
        inappropriate_phrases=("妥否，请批示", "特此通知"),
        # Retain the Foundation proof's issue-reading priority and mixed-source
        # applicability while expanding coverage from the pinned presentation.
        reading_preferences=(("situation", .04), ("progress", .04), ("achievement", .04),
                             ("issues", .08), ("recommendations", .0)),
        reading_source_roles=(("fact_source", "fact_source"), ("mixed", "fact_source"), ("mixed", "mixed"))),
    GenreSemanticSpec("public-notice", "通告", (
        _role("publication_reason", "发布缘由", "根据", "为了", "为维护"),
        _role("public_obligations", "通告事项", "通告如下", "禁止", "应当", "不得", "区域", "范围"),
        _role("execution", "执行要求", "施行", "执行", "处罚", "注意事项")),
        ("通告",), "核对一定范围公众应知应守事项、区域对象和执行时间；不与面向国内外重大法定事项的公告混用。",
        (("publication_reason", "public_obligations"),), ("特此通告",),
        inappropriate_phrases=("妥否，请批示", "特此公告", "特此通知")),
    GenreSemanticSpec("reply", "回复函", (
        _role("incoming_letter", "来文回顾", "来函", "收悉", "函悉"),
        _role("reply_opinions", "答复意见", "同意", "不同意", "答复如下", "函复如下", "回复如下"),
        _role("coordination", "执行要求", "后续对接", "办理时限", "责任分工", required=False)),
        ("回复函", "复函", "答复函"), "核对不相隶属来函、逐项答复与明确结论；对应清楚，结论前置，不用批复替代复函。",
        (("incoming_letter", "reply_opinions"),), ("特此函复", "特此复函", "特此回复"),
        inappropriate_phrases=("特此批复", "现批复如下", "妥否，请批示")),
    GenreSemanticSpec("report", "报告", (
        _role("report_context", "基本情况", "报告缘由", "总体情况", "报告如下"),
        _role("work_undertaken", "工作开展情况", "工作进展", "采取", "措施", "开展"),
        _role("problems", "存在问题", "困难", "风险", required=False),
        _role("next_actions", "下一步建议", "下一步打算", "下一步", required=False)),
        ("报告",), "核对主送、基本情况、工作开展与落款；一般报告和制式年报分别审阅，报告不得夹带请求批准事项。",
        (("report_context", "work_undertaken"),), ("特此报告", "请审议", "请予审查"),
        inappropriate_phrases=("请批示", "请批复", "请予批准", "请予审批", "请批转执行"),
        manual_elements=("主送单位", "落款和成文日期", "报告不得夹带请批事项")),
    GenreSemanticSpec("request", "请示", (
        _role("request_reason", "事项背景", "请示缘由", "根据", "必要性", "鉴于"),
        _role("requested_matter", "请示事项", "申请", "请求", "请予", "拟请"),
        _role("proposed_disposition", "请示意见", "倾向性方案", "办理思路", "拟", required=False),
        _role("approval_closing", "结尾请批语", "请批示", "请批复", "请予审批", "请批转", "请予批准")),
        ("请示",), "检查一文一事、一个直接上级、缘由和明确请批事项；结尾须请批语，联系人依已知材料核对，不补事实。",
        (("request_reason", "requested_matter"),), ("请批示", "请批复", "请予审批", "请批转", "请予批准"), True,
        ("特此报告", "特此通知", "特此函复"),
        manual_elements=("一文一事", "唯一直接上级主送单位", "落款和成文日期", "附注联系人和联系电话")),
    GenreSemanticSpec("resolution", "决议", (
        _role("meeting_background", "会议背景", "听取", "审议", "会议"),
        _role("formal_adoption", "通过说明", "审议通过", "表决通过", "通过了", "决定批准", "会议批准", "通过）"),
        _role("resolved_matters", "决议事项", "批准", "通过", "决定"),
        _role("implementation_call", "落实要求", "贯彻", "落实", "号召", "施行")),
        ("决议",), "核对正式议事程序及审议、表态、决议事项与落实要求；未经会议正式通过的事项不得写为决议。",
        (("meeting_background", "resolved_matters"), ("resolved_matters", "implementation_call")),
        ("会议号召", "起施行"), inappropriate_phrases=("妥否，请批示", "特此通知"),
        manual_elements=("通过说明行", "真实法定会议或相应议事程序")),
    GenreSemanticSpec("special-report", "情况专报", (
        _role("event_overview", "事项概况", "事件背景", "基本态势", "舆情综述"),
        _role("latest_progress", "最新进展", "新情况", "已采取措施", "最新动态"),
        _role("risk_assessment", "风险研判", "风险点", "走势研判", "趋势", "可能影响"),
        _role("action_advice", "工作建议", "处置建议", "报送建议", "建议")),
        ("专报",), "围绕单一专门事项检查概况、最新进展、风险研判、工作建议；事实和趋势判断分别呈现，建议须具体。",
        (("event_overview", "latest_progress"), ("latest_progress", "risk_assessment"), ("risk_assessment", "action_advice")),
        ("特此报告", "请审阅"), inappropriate_phrases=("妥否，请批示", "特此通知")),
    GenreSemanticSpec("speech", "讲话稿", (
        _role("opening", "开场说明", "同志们", "各位", "这次会议", "今天", "我讲"),
        _role("situational_judgment", "形势判断", "形势", "意义", "认识", required=False),
        _role("priority_tasks", "重点任务", "一要", "二要", "我们要", "重点工作", "部署"),
        _role("work_requirements", "工作要求", "责任", "纪律", "落实", required=False),
        _role("closing", "结束语", "谢谢大家", "让我们", "共同抓好", "努力奋斗")),
        ("讲话", "发言", "致辞", "主持词"), "按讲话、发言、致辞、主持场景复核；有开场、重点内容与结束语，保持现场感和可宣读性，不写成无人称方案。",
        (("opening", "priority_tasks"), ("priority_tasks", "closing")), ("谢谢大家", "让我们", "共同抓好", "努力奋斗"),
        inappropriate_phrases=("特此通知", "特此报告", "妥否，请批示")),
    GenreSemanticSpec("work-plan", "工作方案", (
        _role("formulation_basis", "制定背景", "根据", "为做好", "为进一步"),
        _role("general_requirements", "总体要求", "指导思想", "基本原则"),
        _role("objectives", "工作目标", "目标任务", "预期成效", "目标"),
        _role("tasks", "主要任务", "重点任务", "责任分工", "具体工作"),
        _role("schedule", "实施步骤", "阶段安排", "时间节点", "时间安排"),
        _role("safeguards", "保障措施", "组织实施", "组织领导", "督导考核")),
        ("工作方案", "实施方案", "方案"), "核对制定依据、总体要求、目标、任务、进度与保障；责任时限可嵌在任务或附件，允许板块平行式，不强求固定标题。",
        (("general_requirements", "tasks"), ("tasks", "safeguards")),
        inappropriate_phrases=("特此通知", "妥否，请批示", "特此报告")),
)

GENRE_SEMANTICS = {spec.upstream_id: spec for spec in _SPECS}


def genre_spec(upstream_id: str) -> GenreSemanticSpec:
    try:
        return GENRE_SEMANTICS[upstream_id]
    except KeyError:
        raise ValueError("UPSTREAM_GENRE_SEMANTICS_UNAVAILABLE") from None


def retrieval_profile(upstream_id: str) -> RetrievalProfile:
    spec = genre_spec(upstream_id)

    def classify(candidate: Mapping[str, Any]) -> str:
        metadata = candidate.get("metadata")
        metadata = metadata if isinstance(metadata, Mapping) else {}
        heading = str(candidate.get("section_heading") or metadata.get("section_heading") or "")
        path = metadata.get("section_path") or ()
        path = path if isinstance(path, (list, tuple)) else (path,)
        # Headings and existing chunk text are navigation signals only. No
        # evidence, semantic state or document-ranking object enters here.
        for context in (heading, " ".join(map(str, path)), str(candidate.get("chunk_text") or "")):
            for role in spec.roles:
                if any(cue in context for cue in role.cues):
                    return role.role_id
        return "context"

    def preference(candidate):
        role = classify(candidate)
        return dict(spec.reading_preferences).get(role, 0.04 if role != "context" else 0.0)

    return RetrievalProfile(
        profile_id=f"upstream_{upstream_id}_coverage_v1",
        # Unclassified material is an explicit safe reading category, never
        # converted into a required article function or authorized evidence.
        coverage_roles=tuple(role.role_id for role in spec.roles) + ("context",),
        classify_coverage=classify, chunk_preference=preference,
        chunk_preference_source_roles=spec.reading_source_roles,
    )


def structure_contract(upstream_id: str) -> StructureContract:
    spec = genre_spec(upstream_id)
    return StructureContract(
        contract_id=f"upstream_{upstream_id}_structure_v1",
        section_roles=tuple(role.role_id for role in spec.roles),
        maturity_review_instruction=(spec.review_instruction +
            "功能可以合并或嵌入正文；词面未匹配仅提示人工复核，不授权添加事实。"),
    )


def _lexical_grouping(actions):
    return tuple(dict.fromkeys(actions))


def commercial_contract(local_genre_id: str, upstream_id: str) -> CommercialGenreContract:
    spec = genre_spec(upstream_id)
    prefix = f"upstream_{upstream_id}"
    return CommercialGenreContract(
        genre_id=local_genre_id, version=f"{prefix}_commercial_v1",
        maturity=CommercialMaturity.PRODUCTION_ENABLED,
        planning=PlanningCapability(identity=f"{prefix}_internal_plan_v1", mode="writer_internal",
            instruction=f"按{spec.title}的上游规则自主组织完整正文，不先输出提纲。{spec.review_instruction}"),
        evidence_organization=EvidenceOrganizationCapability(
            identity=f"{prefix}_lexical_groups_v1", grouping_key=_lexical_grouping),
        writer_control=WriterControlPolicy(identity=f"{prefix}_writer_control_v1"),
        writer_calls=WriterCallPolicy(identity=f"{prefix}_single_writer_v1"),
        recovery=RecoveryPolicy(identity=f"{prefix}_no_rewrite_v1"),
        delivery=DeliveryPolicy(identity=f"{prefix}_delivery_v1", formats=("txt", "docx")),
    )
