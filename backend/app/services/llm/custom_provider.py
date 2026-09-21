from app.config import ProviderConfig
from app.services.llm.openai_compatible_provider import OpenAICompatibleProvider


class CustomProvider(OpenAICompatibleProvider):
    """Placeholder custom provider that reuses the OpenAI-compatible contract."""
