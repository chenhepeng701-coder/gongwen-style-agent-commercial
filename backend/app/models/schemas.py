"""Base Pydantic schemas for API responses."""

from datetime import datetime
from pydantic import BaseModel, Field


class MessageResponse(BaseModel):
    """Standard API response wrapper."""
    success: bool = True
    message: str = ""


class SettingsResponse(BaseModel):
    provider_key: str = ""
    provider: str = 'openai_compatible'
    base_url: str = ''
    model: str = ''
    embedding_model: str = ''
    endpoint_path: str = '/v1/chat/completions'
    temperature: float = 0.7
    timeout: int = 120
    api_key_configured: bool = False


class SettingsRequest(BaseModel):
    provider_key: str = ''
    provider: str = 'openai_compatible'
    base_url: str = ''
    endpoint_path: str = '/v1/chat/completions'
    api_key: str = ''
    model: str = ''
    embedding_model: str = ''
    temperature: float = 0.7
    timeout: int = 120


class TestConnectionResponse(BaseModel):
    success: bool
    message: str
    response: str = ''
    vector_dim: int = 0
    error_type: str = ''
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())


class EmbeddingResponse(BaseModel):
    provider: str = 'openai_compatible'
    base_url: str = ''
    model: str = ''
    endpoint_path: str = '/v1/embeddings'
    timeout: int = 120
    api_key_configured: bool = False


class EmbeddingRequest(BaseModel):
    provider: str = 'openai_compatible'
    base_url: str = ''
    api_key: str = ''
    model: str = ''
    endpoint_path: str = '/v1/embeddings'
    timeout: int = 120


class ModelPreset(BaseModel):
    label: str
    value: str
    provider: str = 'openai_compatible'
    base_url: str
    endpoint_path: str = '/v1/chat/completions'
    model: str


class ModelPresetListResponse(BaseModel):
    items: list[ModelPreset] = Field(default_factory=list)
