from abc import ABC, abstractmethod

from app.config import ProviderConfig


class BaseLLMProvider(ABC):
    """Base interface for interchangeable LLM providers."""

    def __init__(self, config: ProviderConfig) -> None:
        self.config = config

    @abstractmethod
    def generate_text(self, prompt: str, system_prompt: str = "", temperature: float = 0.7) -> str:
        raise NotImplementedError
