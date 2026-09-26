"""Pydantic models for article generation."""

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, Field, ValidationInfo, field_validator, model_validator, model_serializer
from app.models.full_article import FullArticleProjection, CanonicalArticleBody


PublicDeliveryStatus = Literal[
    "SUCCESS",
    "SUCCESS_WITH_REPAIR",
    "SUCCESS_WITH_REVIEW",
    "FAILED",
]
FailureClass = Literal[
    "NONE",
    "REQUEST_FAILURE",
    "PRE_ARTICLE_FAILURE",
    "RECOVERABLE_CONTENT_FAILURE",
    "RECOVERABLE_POSTPROCESS_FAILURE",
    "FATAL_SYSTEM_FAILURE",
]


class DeliveryDecision(BaseModel):
    """Production-orchestrator decision about what the user receives."""

    delivery_status: PublicDeliveryStatus
    article_exists: bool
    article_recoverable: bool
    article_qualified: bool
    official_success: bool
    user_confirmation_required: bool
    issues: list[dict[str, Any]] = Field(default_factory=list)
    delivery_warnings: list[str] = Field(default_factory=list)
    failure_class: FailureClass = "NONE"
    failure_stage: str = ""
    reason: str = ""
    next_step: str = ""

    @model_validator(mode="after")
    def validate_delivery_invariants(self) -> "DeliveryDecision":
        if self.delivery_status == "SUCCESS_WITH_REVIEW":
            if not self.article_exists or not self.article_recoverable:
                raise ValueError("SUCCESS_WITH_REVIEW requires a recoverable article")
            if self.article_qualified or self.official_success:
                raise ValueError("review delivery cannot be marked qualified or official")
            if not self.user_confirmation_required:
                raise ValueError("review delivery requires user confirmation")
        if self.delivery_status in {"SUCCESS", "SUCCESS_WITH_REPAIR"}:
            if not self.article_exists or not self.article_recoverable:
                raise ValueError("successful delivery requires a recoverable article")
            if not self.article_qualified or not self.official_success:
                raise ValueError("official success requires a qualified article")
            if self.user_confirmation_required:
                raise ValueError("official success cannot require user confirmation")
        if self.delivery_status == "FAILED" and self.official_success:
            raise ValueError("FAILED cannot be official success")
        return self


@dataclass(frozen=True)
class OfficialDraftTask:
    """Normalized task fields for official_draft generation."""

    title: str
    topic: str
    document_type: str
    level: str
    org_type: str
    business_domain: str
    writing_purpose: str
    reference_scope: str
    target_words: int
    export_format: str
    scene_compat: str | None = None
    document_type_from_scene_compat: bool = False


class ArticleGenRequest(BaseModel):
    """Request to generate an article."""

    title: str = Field(..., min_length=1, description="文章标题/主题")
    generation_route: Literal["current", "full_article_drafting"] = Field(
        default="current", description="显式受控整篇起草；仅支持已注册且通过正式验收的文种")
    scene: str | None = Field(default=None, description="指定场景，不指定则自动识别")
    style: str | None = Field(default=None, description="指定写作风格")
    article_mode: str = Field(
        default="official_draft",
        description="official_draft | full_article | draft_article | document_mode | appreciation",
        validation_alias=AliasChoices("article_mode", "generation_mode", "mode"),
    )
    target_words: int = Field(
        default=1200,
        ge=1,
        description="目标字数",
        validation_alias=AliasChoices("target_words", "target_length"),
    )
    temperature: float = Field(default=0.7, ge=0.0, le=2.0, description="生成温度")
    count: int = Field(default=1, ge=1, le=10, description="生成篇数")
    export_format: str = Field(default="docx", description="txt | docx")
    analysis_wrapper: bool = Field(default=False, description="Enable full-article analysis wrapper")
    appreciation_scope: str = Field(
        default="single_deep",
        description="single_deep | multi_horizontal",
    )
    appreciation_count: int = Field(default=3, ge=2, le=4, description="多篇写法参考篇数")
    # v0.3 feedback fields, reserved for later RAG/prompt integration.
    document_type: str | None = Field(default=None, description="公文文种类型")
    level: str | None = Field(default=None, description="层级")
    org_type: str | None = Field(default=None, description="单位类型")
    business_domain: str | None = Field(default=None, description="业务领域")
    writing_purpose: str | None = Field(default=None, description="写作目的")
    reference_scope: str | None = Field(
        default=None,
        description="参考资料范围",
        validation_alias=AliasChoices("reference_scope", "reference_mode"),
    )

    @model_validator(mode="after")
    def validate_full_draft_route(self, info: ValidationInfo):
        if self.generation_route == "full_article_drafting":
            from app.services.generation.genres.registry import resolve_genre
            if self.article_mode != "official_draft":
                raise ValueError("full_article_drafting requires official_draft")
            # Only trusted in-process fixture validation can request offline
            # capability. JSON fields cannot enable it; production revalidates.
            purpose = "offline" if (info.context or {}).get("genre_purpose") == "offline" else "production"
            adapter = resolve_genre(self.document_type, purpose=purpose)
            if self.export_format.strip().lower() not in adapter.capabilities.export_formats:
                raise ValueError("GENRE_EXPORT_FORMAT_UNSUPPORTED")
            self.document_type = adapter.document_type
            if self.count != 1:
                raise ValueError("full_article_drafting currently requires count=1")
        return self

    @field_validator("reference_scope", mode="before")
    @classmethod
    def validate_reference_scope(cls, value):
        aliases = {
            "auto": "自动匹配资料库",
            "search": "优先参考搜索结果",
            "recent": "优先参考最近上传资料",
        }
        normalized = str(value or "").strip()
        return aliases.get(normalized, normalized or None)

    @field_validator("article_mode", mode="before")
    @classmethod
    def validate_article_mode(cls, value):
        normalized = str(value or "official_draft").strip().lower()
        if normalized not in {
            "official_draft",
            "full_article",
            "draft_article",
            "document_mode",
            "appreciation",
            "single_deep",
            "multi_horizontal",
        }:
            raise ValueError(
                "article_mode must be 'official_draft', 'full_article', 'draft_article', 'document_mode', or a legacy appreciation mode"
            )
        return normalized

    @field_validator("export_format", mode="before")
    @classmethod
    def validate_export_format(cls, value):
        normalized = str(value or "docx").strip().lower()
        if normalized not in {"txt", "docx"}:
            raise ValueError("export_format must be 'txt' or 'docx'")
        return normalized

    @field_validator("appreciation_scope", mode="before")
    @classmethod
    def validate_appreciation_scope(cls, value):
        normalized = str(value or "single_deep").strip().lower()
        aliases = {
            "single": "single_deep",
            "single_deep": "single_deep",
            "单篇写法参考": "single_deep",
            "multi": "multi_horizontal",
            "multi_horizontal": "multi_horizontal",
            "多篇写法参考": "multi_horizontal",
        }
        if normalized not in aliases:
            raise ValueError("appreciation_scope must be 'single_deep' or 'multi_horizontal'")
        return aliases[normalized]


class ArticleGenResult(CanonicalArticleBody):
    """Result of article generation."""

    article_mode: str = "official_draft"
    generation_route: Literal["current", "full_article_drafting"] = "current"
    generation_provenance: dict[str, Any] = Field(default_factory=dict)
    public_title: str = ""
    draft_full_article: dict[str, Any] = Field(default_factory=dict)
    appreciation: dict[str, Any] = Field(default_factory=dict)
    analysis_wrapper_enabled: bool = False
    writing_tips: str = ""
    applicable_scenes: list[str] = Field(default_factory=list)
    learning_points: list[str] = Field(default_factory=list)
    review_notes: list[str] = Field(default_factory=list)
    wrapped_preview: str = ""
    title: str = ""
    scene: str = ""
    retrieved_count: int = 0
    tokens_used: int = 0
    generation_time: float = 0.0
    save_path: str = ""
    docx_path: str = ""
    export_format: str = "docx"
    appreciation_scope: str = "single_deep"
    appreciation_count: int = 3
    reference_sources: list[dict[str, Any]] = Field(default_factory=list)
    log_id: str = ""
    word_count: int = 0
    fallback_used: bool = False
    fallback_reason: str | None = None
    selected_candidate_stage: str = "INITIAL_GENERATION"
    final_pipeline_stage_completed: bool = True
    fact_safety_rewrite_used: bool = False
    user_confirmation_required: bool = False
    unverified_claim_count: int = 0
    unverified_claim_types: list[str] = Field(default_factory=list)
    quality_test_mode: bool = False
    delivery_status: str = "OFFICIAL_SUCCESS"
    official_success: bool = True
    target_word_count: int = 0
    fact_risk_count: int = 0
    analysis_risk_count: int = 0
    fact_gap_count: int = 0
    length_gate_status: str = "PASS"
    structure_gate_status: str = "PASS"
    risk_items: list[dict[str, Any]] = Field(default_factory=list)
    article_exists: bool = False
    article_recoverable: bool = False
    article_qualified: bool = False
    delivery_warnings: list[str] = Field(default_factory=list)
    failure_class: FailureClass = "NONE"
    failure_stage: str = ""
    reason: str = ""
    next_step: str = ""
    review_suggestion: str = ""
    delivery_decision: DeliveryDecision | None = None

    @model_validator(mode="after")
    def close_public_delivery_contract(self) -> "ArticleGenResult":
        if self.content_projection is not None:
            self.content_projection.require_content(self.content)
            if self.delivery_status != "SUCCESS_WITH_REVIEW":
                raise ValueError("full-article/v1 requires review until mandatory checks are complete")
        if self.delivery_status not in {
            "SUCCESS",
            "SUCCESS_WITH_REPAIR",
            "SUCCESS_WITH_REVIEW",
            "FAILED",
        }:
            return self

        body_exists = bool(self.content.strip())
        self.article_exists = body_exists
        self.article_recoverable = body_exists
        if self.delivery_status == "SUCCESS_WITH_REVIEW":
            self.article_qualified = False
            self.official_success = False
            self.user_confirmation_required = True
            if not body_exists:
                raise ValueError("SUCCESS_WITH_REVIEW must include complete article content")
        elif self.delivery_status in {"SUCCESS", "SUCCESS_WITH_REPAIR"}:
            self.article_qualified = True
            self.official_success = True
            self.user_confirmation_required = False
        else:
            self.article_qualified = False
            self.official_success = False

        if self.delivery_decision is None:
            self.delivery_decision = DeliveryDecision(
                delivery_status=self.delivery_status,
                article_exists=self.article_exists,
                article_recoverable=self.article_recoverable,
                article_qualified=self.article_qualified,
                official_success=self.official_success,
                user_confirmation_required=self.user_confirmation_required,
                issues=list(self.risk_items),
                delivery_warnings=list(self.delivery_warnings),
                failure_class=self.failure_class,
                failure_stage=self.failure_stage,
                reason=self.reason,
                next_step=self.next_step,
            )
        return self

    @model_serializer(mode="wrap")
    def serialize_current_full_article(self, handler):
        self.require_current_body()
        if self.content_projection is not None:
            self.content_projection.require_content(self.content)
            if (self.delivery_status != "SUCCESS_WITH_REVIEW" or self.article_qualified
                    or self.official_success or not self.user_confirmation_required):
                raise ValueError("FULL_ARTICLE_REVIEW_STATUS_CHANGED")
        return handler(self)


class GenerationArticleSnapshot(CanonicalArticleBody):
    """Stored article snapshot for generation history."""

    article_mode: str = "official_draft"
    generation_route: Literal["current", "full_article_drafting"] = "current"
    generation_provenance: dict[str, Any] = Field(default_factory=dict)
    public_title: str = ""
    draft_full_article: dict[str, Any] = Field(default_factory=dict)
    appreciation: dict[str, Any] = Field(default_factory=dict)
    analysis_wrapper_enabled: bool = False
    writing_tips: str = ""
    applicable_scenes: list[str] = Field(default_factory=list)
    learning_points: list[str] = Field(default_factory=list)
    review_notes: list[str] = Field(default_factory=list)
    wrapped_preview: str = ""
    title: str = ""
    scene: str = ""
    retrieved_count: int = 0
    save_path: str = ""
    docx_path: str = ""
    export_format: str = "docx"
    appreciation_scope: str = "single_deep"
    appreciation_count: int = 3
    reference_sources: list[dict[str, Any]] = Field(default_factory=list)
    word_count: int = 0
    fallback_used: bool = False
    fallback_reason: str | None = None
    selected_candidate_stage: str = "INITIAL_GENERATION"
    final_pipeline_stage_completed: bool = True
    fact_safety_rewrite_used: bool = False
    user_confirmation_required: bool = False
    unverified_claim_count: int = 0
    unverified_claim_types: list[str] = Field(default_factory=list)
    quality_test_mode: bool = False
    delivery_status: str = "OFFICIAL_SUCCESS"
    official_success: bool = True
    target_word_count: int = 0
    fact_risk_count: int = 0
    analysis_risk_count: int = 0
    fact_gap_count: int = 0
    length_gate_status: str = "PASS"
    structure_gate_status: str = "PASS"
    risk_items: list[dict[str, Any]] = Field(default_factory=list)
    article_exists: bool = False
    article_recoverable: bool = False
    article_qualified: bool = False
    delivery_warnings: list[str] = Field(default_factory=list)
    failure_class: FailureClass = "NONE"
    failure_stage: str = ""
    reason: str = ""
    next_step: str = ""
    review_suggestion: str = ""
    delivery_decision: DeliveryDecision | None = None

    @model_serializer(mode="wrap")
    def serialize_history_body(self, handler):
        self.require_current_body()
        data = handler(self)
        if self.article_markdown is not None:
            data.pop("content", None)
        return data


class ArticleGenBatchResult(BaseModel):
    """Batch result of article generation."""

    articles: list[ArticleGenResult] = Field(default_factory=list)
    count: int = 0
    output_dir: str = ""
    delivery_status: PublicDeliveryStatus = "FAILED"
    official_success: bool = False
    user_confirmation_required: bool = False
    failure_class: FailureClass = "PRE_ARTICLE_FAILURE"
    delivery_warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def summarize_article_delivery(self) -> "ArticleGenBatchResult":
        if not self.articles:
            self.count = 0
            return self
        priority = {
            "SUCCESS": 0,
            "SUCCESS_WITH_REPAIR": 1,
            "SUCCESS_WITH_REVIEW": 2,
            "FAILED": 3,
        }
        normalized_status = {
            id(article): (
                article.delivery_status
                if article.delivery_status in priority
                else (
                    "SUCCESS"
                    if (
                        article.delivery_status == "OFFICIAL_SUCCESS"
                        and article.official_success
                        and article.content.strip()
                    )
                    else (
                        "SUCCESS_WITH_REVIEW"
                        if article.content.strip()
                        else "FAILED"
                    )
                )
            )
            for article in self.articles
        }
        selected = max(
            self.articles,
            key=lambda article: priority[normalized_status[id(article)]],
        )
        self.count = len(self.articles)
        self.delivery_status = normalized_status[id(selected)]  # type: ignore[assignment]
        self.official_success = all(article.official_success for article in self.articles)
        self.user_confirmation_required = any(
            article.user_confirmation_required for article in self.articles
        )
        self.failure_class = selected.failure_class
        self.delivery_warnings = [
            warning
            for article in self.articles
            for warning in article.delivery_warnings
        ]
        return self


class GenerationLogEntry(BaseModel):
    """A single generation log entry."""

    id: str = ""
    request_title: str = ""
    public_title: str = ""
    draft_full_article: dict[str, Any] = Field(default_factory=dict)
    appreciation: dict[str, Any] = Field(default_factory=dict)
    analysis_wrapper_enabled: bool = False
    writing_tips: str = ""
    applicable_scenes: list[str] = Field(default_factory=list)
    learning_points: list[str] = Field(default_factory=list)
    review_notes: list[str] = Field(default_factory=list)
    wrapped_preview: str = ""
    title: str = ""
    article_mode: str = "official_draft"
    target_words: int = 1200
    scene: str = ""
    word_count: int = 0
    status: str = "success"
    error_message: str = ""
    generation_time: float = 0.0
    count: int = 1
    export_format: str = "docx"
    appreciation_scope: str = "single_deep"
    appreciation_count: int = 3
    document_type: str = ""
    level: str = ""
    org_type: str = ""
    business_domain: str = ""
    writing_purpose: str = ""
    reference_scope: str = ""
    timestamp: str = ""
    docx_path: str = ""
    files: list[str] = Field(default_factory=list)
    articles: list[GenerationArticleSnapshot] = Field(default_factory=list)


class GenerationLogResponse(BaseModel):
    """Response for generation log."""

    entries: list[GenerationLogEntry] = Field(default_factory=list)
    total: int = 0
    output_dir: str = ""


class FullArticleSaveRequest(BaseModel):
    base_article: GenerationArticleSnapshot
    article_markdown: str
    entry_id: str = ""
    article_index: int = Field(default=0, ge=0)


class WordExportRequest(BaseModel):
    """Request payload for Word export."""

    title: str = ""
    content: str = ""
    output_path: str = ""
    content_format: Literal["plain", "full-article/v1"] = "plain"
    article_markdown: str | None = None

    @model_validator(mode="after")
    def canonical_export_body(self):
        if self.article_markdown is not None:
            if self.content and self.content != self.article_markdown:
                raise ValueError("ARTICLE_BODY_ALIAS_CONFLICT")
            self.content = self.article_markdown
            self.content_format = "full-article/v1"
        return self


class DocumentModeSection(BaseModel):
    """One section in a document mode draft export."""

    label: str = ""
    content: Any = None


class DocumentModeWordExportRequest(BaseModel):
    """Request payload for document mode draft Word export."""

    title: str = ""
    sections: list[DocumentModeSection] = Field(default_factory=list)
