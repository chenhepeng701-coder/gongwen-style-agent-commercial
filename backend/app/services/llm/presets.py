MODEL_PRESETS = [
    {
        "label": "DeepSeek",
        "value": "deepseek",
        "provider": "openai_compatible",
        "base_url": "https://api.deepseek.com",
        "endpoint_path": "/v1/chat/completions",
        "model": "deepseek-chat",
    },
    {
        "label": "通义千问/Qwen",
        "value": "qwen",
        "provider": "openai_compatible",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode",
        "endpoint_path": "/v1/chat/completions",
        "model": "qwen-plus",
    },
    {
        "label": "智谱 GLM",
        "value": "zhipu_glm",
        "provider": "openai_compatible",
        "base_url": "https://open.bigmodel.cn/api/paas",
        "endpoint_path": "/v4/chat/completions",
        "model": "glm-4-flash",
    },
    {
        "label": "豆包/火山方舟",
        "value": "volcengine_ark",
        "provider": "openai_compatible",
        "base_url": "https://ark.cn-beijing.volces.com/api",
        "endpoint_path": "/v3/chat/completions",
        "model": "doubao-seed-1-6",
    },
    {
        "label": "Kimi/月之暗面",
        "value": "moonshot_kimi",
        "provider": "openai_compatible",
        "base_url": "https://api.moonshot.cn",
        "endpoint_path": "/v1/chat/completions",
        "model": "moonshot-v1-8k",
    },
    {
        "label": "MiniMax",
        "value": "minimax",
        "provider": "openai_compatible",
        "base_url": "https://api.minimax.chat",
        "endpoint_path": "/v1/chat/completions",
        "model": "MiniMax-Text-01",
    },
    {
        "label": "OpenRouter",
        "value": "openrouter",
        "provider": "openai_compatible",
        "base_url": "https://openrouter.ai/api",
        "endpoint_path": "/v1/chat/completions",
        "model": "openai/gpt-4o-mini",
    },
    {
        "label": "OpenAI",
        "value": "openai",
        "provider": "openai_compatible",
        "base_url": "https://api.openai.com",
        "endpoint_path": "/v1/chat/completions",
        "model": "gpt-4o-mini",
    },
    {
        "label": "自定义 OpenAI-Compatible",
        "value": "custom_openai_compatible",
        "provider": "openai_compatible",
        "base_url": "",
        "endpoint_path": "/v1/chat/completions",
        "model": "",
    },
]


def list_model_presets() -> list[dict]:
    return MODEL_PRESETS
