"""Data models for the library (资料库) system."""

from datetime import datetime
from pydantic import BaseModel, Field


class EmbeddingChunk(BaseModel):
    """A single text chunk with its embedding vector."""
    chunk_index: int = 0
    text: str = ""
    vector: list[float] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


class LibraryDocument(BaseModel):
    """An indexed document entry in the library."""
    id: str = ""
    title: str = ""
    original_filename: str = ""
    file_path: str = ""         # 原始文件路径（不复制到项目内）
    file_type: str = ""         # .docx, .txt, .md
    file_size: int = 0          # 字节
    word_count: int = 0
    content_preview: str = ""   # 前 200 字预览
    scene_tags: list[str] = Field(default_factory=list)
    indexed_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    has_embedding: bool = False
    embedding_chunks: int = 0
    embedding_model: str = ""
    embedded_at: str = ""


class SourceDocumentInfo(BaseModel):
    """Public, path-free metadata for reading one indexed source document."""

    document_id: str = ""
    title: str = ""
    filename: str = ""
    file_type: str = ""
    word_count: int = 0


class SourceDocumentContentResponse(BaseModel):
    """Full locally parsed source text returned without filesystem details."""

    document: SourceDocumentInfo
    content: str = ""


class EmbedResponse(BaseModel):
    success: bool = True
    message: str = ""
    chunks: int = 0
    model: str = ""


class DocumentListResponse(BaseModel):
    items: list[LibraryDocument] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 0
    total_pages: int = 1


class UploadResponse(BaseModel):
    success: bool = True
    message: str = ""
    document: LibraryDocument | None = None


class MaterialCard(BaseModel):
    """A structured material card extracted from a document."""
    card_index: int = 0
    core_viewpoint: str = ""        # 核心观点
    argument_support: str = ""      # 论据支撑
    typical_expression: str = ""    # 典型表述
    structure_pattern: str = ""     # 结构范式
    scene_tags: list[str] = Field(default_factory=list)  # 场景标签


class CardExtractResponse(BaseModel):
    """Response from card extraction."""
    success: bool = True
    message: str = ""
    cards: list[MaterialCard] = Field(default_factory=list)
    card_count: int = 0
    model: str = ""


class StyleCard(BaseModel):
    """A structured style card describing writing style features."""
    sentence_pattern: dict = Field(default_factory=dict)    # 句式偏好
    word_habit: dict = Field(default_factory=dict)          # 用词习惯
    opening_paradigm: dict = Field(default_factory=dict)    # 开头范式
    closing_paradigm: dict = Field(default_factory=dict)    # 结尾范式
    argument_rhythm: dict = Field(default_factory=dict)     # 论证节奏
    tone_register: dict = Field(default_factory=dict)       # 语气语调


class StyleExtractResponse(BaseModel):
    """Response from style extraction."""
    success: bool = True
    message: str = ""
    style_card: StyleCard | None = None
    model: str = ""


class DeleteResponse(BaseModel):
    success: bool = True
    message: str = ""


class RetrievalRequest(BaseModel):
    """Search request for semantic retrieval."""
    query: str = ""
    top_k: int = Field(default=20, ge=1, le=100)


class RetrievalResult(BaseModel):
    """A single retrieval result (matching chunk)."""
    doc_id: str = ""
    title: str = ""
    file_path: str = ""
    file_type: str = ""
    word_count: int = 0
    scene_tags: list[str] = Field(default_factory=list)
    chunk_index: int = 0
    chunk_text: str = ""
    score: float = 0.0
    match_type: str = "语义相关"
    match_reason: str = "语义相关"
    metadata: dict = Field(default_factory=dict)
    chunk_id: str = ""
    source_file: str = ""
    document_type: str = "未知"
    level: str = "未知"
    org_type: str = "未知"
    business_domain: str = "未知"
    writing_purpose: str = "未知"
    paragraph_type: str = "unknown"
    section_heading: str = ""
    section_index: int = 0
    schema_version: str = "legacy"


class RetrievalResponse(BaseModel):
    """Response from semantic search."""
    query: str = ""
    results: list[RetrievalResult] = Field(default_factory=list)
    total: int = 0
    total_documents: int = 0
    library_empty: bool = False
    semantic_error: str = ""


class RetrievalStatsResponse(BaseModel):
    """Retrieval system readiness stats."""
    documents_with_embeddings: int = 0
    total_chunks: int = 0


class SceneClassifyRequest(BaseModel):
    """Request to classify a title/query into scene categories."""
    text: str = ""


class SceneClassifyResponse(BaseModel):
    """Response from scene classification."""
    primary_scene: str = ""
    secondary_scenes: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    reasoning: str = ""
    applied_tags: list[str] | None = None  # only populated for document classification


class SceneTaxonomyResponse(BaseModel):
    """List of scene taxonomy entries."""
    items: list[dict] = Field(default_factory=list)


class SceneStatsResponse(BaseModel):
    """Scene distribution stats."""
    total_documents: int = 0
    tagged_documents: int = 0
    scene_dist: dict[str, int] = Field(default_factory=dict)


class LibraryStatsResponse(BaseModel):
    """Overall library stats."""
    total_docs: int = 0
    scene_distribution: dict[str, int] = Field(default_factory=dict)


class BatchEmbedResponse(BaseModel):
    """Response from batch embedding."""
    success: bool = True
    message: str = ""
    embedded: int = 0
    failed: int = 0
    errors: list[str] = Field(default_factory=list)


class BatchCardsResponse(BaseModel):
    """Response from batch card extraction."""
    success: bool = True
    message: str = ""
    extracted: int = 0
    failed: int = 0
    errors: list[str] = Field(default_factory=list)


class BatchStyleResponse(BaseModel):
    """Response from batch style extraction."""
    success: bool = True
    message: str = ""
    extracted: int = 0
    failed: int = 0
    errors: list[str] = Field(default_factory=list)


class BatchClassifyResponse(BaseModel):
    """Response from batch scene classification."""
    total: int = 0
    classified: int = 0
    failed: int = 0
    details: list[dict] = Field(default_factory=list)


class SceneStyleProfile(BaseModel):
    """Aggregated style profile for a scene."""
    scene_name: str = ""
    document_count: int = 0
    aggregated_style: dict = Field(default_factory=dict)
    source_docs: list[dict] = Field(default_factory=list)


class SceneStyleSummary(BaseModel):
    """Brief summary of a scene's style profile."""
    scene_name: str = ""
    document_count: int = 0
    tone: str = ""
    opening_type: str = ""
    closing_type: str = ""
    argument_logic: str = ""
    top_terms: list[str] = Field(default_factory=list)
    sentence_length: str = ""


class SceneStyleListResponse(BaseModel):
    """List of scene style profiles."""
    items: list[SceneStyleProfile] = Field(default_factory=list)
    total: int = 0
