import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from app.utils.runtime_paths import BACKEND_DIR, get_config_path, initialize_runtime_environment
from app.utils.storage import atomic_write_text

BASE_DIR = BACKEND_DIR
CONFIG_PATH = get_config_path()
DOTENV_API_KEY_NAMES = {
    "GONGWEN_LLM_API_KEY",
    "GONGWEN_EMBEDDING_API_KEY",
    "DASHSCOPE_API_KEY",
}
DOTENV_LOCAL_RESOURCE_NAMES = {"WRITING_REFERENCE_PACK_PATH"}


def _env_flag(name: str, default: bool = False) -> bool:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def _load_dotenv_api_keys() -> None:
    for env_path in (BACKEND_DIR.parent / ".env", BACKEND_DIR / ".env"):
        if not env_path.exists():
            continue
        try:
            lines = env_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for raw_line in lines:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key not in DOTENV_API_KEY_NAMES | DOTENV_LOCAL_RESOURCE_NAMES or os.environ.get(key):
                continue
            os.environ[key] = value.strip().strip('"').strip("'")


def _resolve_env_placeholder(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if stripped.startswith("${") and stripped.endswith("}") and len(stripped) > 3:
        env_name = stripped[2:-1].strip()
        return os.environ.get(env_name, "").strip()
    return value


def _resolve_api_key_placeholders(data: dict[str, Any]) -> None:
    for provider in (data.get("providers") or {}).values():
        if isinstance(provider, dict) and "api_key" in provider:
            provider["api_key"] = _resolve_env_placeholder(provider.get("api_key"))

    embedding = data.get("embedding")
    if isinstance(embedding, dict) and "api_key" in embedding:
        embedding["api_key"] = _resolve_env_placeholder(embedding.get("api_key"))


class ProviderConfig(BaseModel):
    """Configuration for a single LLM provider (豆包, DeepSeek, etc.)."""
    model_config = ConfigDict(extra="ignore")

    provider: str = "openai_compatible"
    api_key: str = ""
    base_url: str = ""
    model: str = ""
    embedding_model: str = ""
    temperature: float = 0.7
    timeout: int = 120
    # Receive SSE internally; generate_text still returns one complete string.
    stream: bool = False
    endpoint_path: str = "/v1/chat/completions"
    headers: dict[str, Any] = Field(default_factory=dict)


class EmbeddingConfig(BaseModel):
    """Configuration for embedding/vector model, independent from chat model."""
    model_config = ConfigDict(extra="ignore")

    provider: str = "openai_compatible"
    api_key: str = ""
    base_url: str = ""
    model: str = ""
    endpoint_path: str = "/v1/embeddings"
    timeout: int = 120
    headers: dict[str, Any] = Field(default_factory=dict)


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    upload_dir: str = "uploads"
    output_dir: str = "output"
    max_file_size_mb: int = 20
    allowed_extensions: list[str] = Field(default_factory=lambda: [".docx", ".txt", ".md"])
    host: str = "127.0.0.1"
    port: int = 8000


class GenerationConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    min_length: int = 800
    max_length: int = 1200
    subtitle_count: int = 3
    retry_times: int = 1
    rag_top_k: int = 5
    rag_min_score: float = 0.35
    rag_min_results: int = 1


class RetrievalConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    mode: str = "vector_only"
    vector_candidate_k: int = 30
    bm25_candidate_k: int = 30
    rrf_k: int = 60
    vector_weight: float = 1.0
    bm25_weight: float = 1.0
    bm25_k1: float = 1.5
    bm25_b: float = 0.75
    bm25_title_weight: float = 2.0


class Settings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    current_provider: str = ""
    quality_test_mode: bool = Field(default=False, exclude=True)
    writing_style_library_enabled: bool = Field(default=False, exclude=True)
    writing_reference_pack_path: str = Field(default="", exclude=True)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    app: AppConfig = Field(default_factory=AppConfig)
    generation: GenerationConfig = Field(default_factory=GenerationConfig)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)


def load_settings() -> Settings:
    initialize_runtime_environment()
    _load_dotenv_api_keys()
    if not CONFIG_PATH.exists():
        return Settings(quality_test_mode=_env_flag("QUALITY_TEST_MODE", False),
                        writing_reference_pack_path=os.environ.get("WRITING_REFERENCE_PACK_PATH", "").strip())
    with CONFIG_PATH.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}

    # ── Migration: old single-llm config → multi-provider ─────────────────
    if "providers" not in data and "llm" in data:
        old_llm = data.pop("llm")
        provider_key = old_llm.get("provider", "openai_compatible")
        if provider_key == "openai_compatible":
            # Try to guess from base_url
            bu = old_llm.get("base_url", "")
            if "deepseek" in bu:
                provider_key = "deepseek"
            elif "volces" in bu:
                provider_key = "volcengine_ark"
        data["providers"] = {provider_key: old_llm}
        data["current_provider"] = provider_key

    # ── Environment variable overrides for API keys ───────────────────────
    _llm_api_key_env = os.environ.get("GONGWEN_LLM_API_KEY", "").strip()
    _embed_api_key_env = os.environ.get("GONGWEN_EMBEDDING_API_KEY", "").strip()
    if _llm_api_key_env:
        providers = data.setdefault("providers", {})
        current = data.get("current_provider", "")
        if current and current in providers:
            providers[current]["api_key"] = _llm_api_key_env
        else:
            for _key, _cfg in providers.items():
                _cfg["api_key"] = _llm_api_key_env
                break
    if _embed_api_key_env:
        data.setdefault("embedding", {})["api_key"] = _embed_api_key_env

    _resolve_api_key_placeholders(data)
    data["writing_reference_pack_path"] = os.environ.get("WRITING_REFERENCE_PACK_PATH", "").strip()
    data["quality_test_mode"] = _env_flag("QUALITY_TEST_MODE", False)
    data["writing_style_library_enabled"] = _env_flag(
        "ENABLE_WRITING_STYLE_LIBRARY",
        False,
    )

    return Settings(**data)


@lru_cache
def get_settings() -> Settings:
    return load_settings()


def reload_settings() -> Settings:
    if hasattr(get_settings, "cache_clear"):
        get_settings.cache_clear()
    return get_settings()


def save_settings(settings: Settings) -> Settings:
    data = settings.model_dump()
    atomic_write_text(
        CONFIG_PATH,
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return reload_settings()


def get_provider_config(provider_key: str) -> ProviderConfig | None:
    """Get config for a specific provider, or None if not configured."""
    settings = get_settings()
    return settings.providers.get(provider_key)


def update_provider_config(provider_key: str, config: ProviderConfig) -> Settings:
    """Save config for a specific provider. If api_key is empty, keep existing key."""
    current_settings = load_settings()
    existing = current_settings.providers.get(provider_key)

    if not config.api_key and existing and existing.api_key:
        config = config.model_copy(update={"api_key": existing.api_key})

    current_settings.providers[provider_key] = config

    # Auto-set current_provider if not set
    if not current_settings.current_provider:
        current_settings.current_provider = provider_key

    return save_settings(current_settings)


def set_current_provider(provider_key: str) -> Settings:
    """Switch the active provider."""
    current_settings = load_settings()
    current_settings.current_provider = provider_key
    return save_settings(current_settings)


def get_embedding_config() -> EmbeddingConfig:
    return get_settings().embedding


def update_embedding_config(config: EmbeddingConfig) -> Settings:
    settings = load_settings()
    if not config.api_key and settings.embedding.api_key:
        config = config.model_copy(update={"api_key": settings.embedding.api_key})
    settings.embedding = config
    return save_settings(settings)
