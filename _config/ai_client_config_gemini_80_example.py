"""推荐免费 AI 服务组合。

复制为 ``_config/ai_client_config.py``，只需填写需要启用的服务商 Key。
模型、优先级、限额、并发和恢复策略由各服务商预置维护；空映射仍兼容对应的单 Key
环境变量。付费服务无需使用这些免费预置，可直接构造普通 Client 后加入结果。
"""

from AIClientCenter.presets import (
    compose_presets,
    gemini_free,
    groq_free,
    nvidia_free,
)


GEMINI_API_KEYS: dict[str, str] = {
    # "google_project_a": "AIza...",
    # "google_project_b": "AIza...",
}

GROQ_API_KEYS: dict[str, str] = {
    # "groq_account_a": "gsk_...",
}

NVIDIA_API_KEYS: dict[str, str] = {
    # "nvidia_account_a": "nvapi-...",
    # "nvidia_account_b": "nvapi-...",
}


AI_CLIENTS, AI_CLIENT_LIMIT = compose_presets(
    gemini_free(GEMINI_API_KEYS),
    groq_free(GROQ_API_KEYS),
    nvidia_free(NVIDIA_API_KEYS),
)
