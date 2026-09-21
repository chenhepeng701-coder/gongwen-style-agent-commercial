from app.config import ProviderConfig, get_settings, get_provider_config
from app.services.llm.custom_provider import CustomProvider
from app.services.llm.openai_compatible_provider import OpenAICompatibleProvider


def get_llm_provider_from_config(config: ProviderConfig):
    if config.provider == "custom":
        return CustomProvider(config)
    return OpenAICompatibleProvider(config)


def get_llm_provider():
    settings = get_settings()
    key = settings.current_provider or 'deepseek'
    config = settings.providers.get(key)
    if config is None:
        raise ValueError(f'未找到服务商配置: {key}')
    return get_llm_provider_from_config(config)
