"""IIS 推荐的低成本多服务商 Client 池配置。

复制为 ``_config/ai_client_config.py`` 后，通过环境变量填写 Key。没有配置 Key 的服务不会
注册，因此本文件可以先复制、再逐个开通服务。模型列表使用逗号分隔的环境变量覆盖。

免费额度和模型名单会变化；上线前请结合
``doc/free_llm_api_and_client_benchmark.md`` 重新核对服务条款与模型广场。
"""

import os
from pathlib import Path
from typing import Callable

from GlobalConfig import DATA_PATH
from AIClientCenter.core.budget import BudgetMode, BudgetPolicy
from AIClientCenter.core.manager import (
    BaseAIClient,
    CLIENT_PRIORITY_CONSUMABLES,
    CLIENT_PRIORITY_FREEBIE,
)
from AIClientCenter.providers.openai_clients import SelfRotatingOpenAIClient
from AIClientCenter.providers.openai_compatible import (
    create_cloudflare_client,
    create_gemini_client,
    create_groq_client,
    create_mistral_client,
    create_modelscope_client,
    create_nvidia_client,
    create_openrouter_client,
    create_siliconflow_client,
)


STATE_DIR = Path(DATA_PATH) / "ai_client_usage"
STATE_DIR.mkdir(parents=True, exist_ok=True)


def _models(env_name: str, defaults: tuple[str, ...]) -> list[str]:
    raw = os.getenv(env_name, "")
    values = [item.strip() for item in raw.split(",") if item.strip()]
    return values or list(defaults)


def _positive_int(env_name: str, default: int) -> int:
    raw = os.getenv(env_name, "").strip()
    if not raw:
        return default
    value = int(raw)
    if value < 1:
        raise ValueError(f"{env_name} 必须为正整数")
    return value


def _rotating_client(
        *, name: str, group_id: str, api_factory: Callable[[], object],
        models: list[str], priority: int = CLIENT_PRIORITY_FREEBIE,
        daily_request_limit: int | None = None,
        daily_token_limit: int | None = None) -> SelfRotatingOpenAIClient:
    if not models:
        raise ValueError(f"{name} 至少需要配置一个模型")
    limits = {}
    if daily_request_limit is not None:
        limits["request_count"] = daily_request_limit
    if daily_token_limit is not None:
        limits["total_tokens"] = daily_token_limit
    client = SelfRotatingOpenAIClient(
        name=name,
        openai_api=api_factory(),
        priority=priority,
        group_id=group_id,
        default_available=True,
        state_file_path=str(STATE_DIR / f"{group_id.replace(' ', '_')}.json"),
        budget_policy=BudgetPolicy(
            BudgetMode.HARD_LIMIT if limits else BudgetMode.OBSERVED,
            limits=limits,
            period_days=1,
        ),
    )
    client.set_rotation_models(models, rotate_per_times=1)
    return client


def build_ai_clients() -> dict[str, BaseAIClient]:
    clients: dict[str, BaseAIClient] = {}

    # SiliconFlow 免费模型会变化。为避免误用收费模型，必须同时配置 Key 和模型列表。
    if os.getenv("SILICON_API_KEY") and os.getenv("SILICONFLOW_FREE_MODELS"):
        clients["siliconflow_free"] = _rotating_client(
            name="SiliconFlow Free Pool",
            group_id="siliconflow_free",
            api_factory=create_siliconflow_client,
            models=_models("SILICONFLOW_FREE_MODELS", ()),
        )

    if os.getenv("MODELSCOPE_API_KEY"):
        clients["modelscope_free"] = _rotating_client(
            name="ModelScope Free Pool",
            group_id="modelscope_free",
            api_factory=create_modelscope_client,
            models=_models("MODELSCOPE_MODELS", (
                "deepseek-ai/DeepSeek-V3.2-Exp",
                "Qwen/Qwen3-Coder-480B-A35B-Instruct",
            )),
            # 200 魔粒并不严格等于 200 次；默认保留余量，可按所选模型修改。
            daily_request_limit=_positive_int("MODELSCOPE_DAILY_REQUEST_LIMIT", 180),
        )

    if os.getenv("GEMINI_API_KEY"):
        clients["gemini_free"] = _rotating_client(
            name="Gemini Free Pool",
            group_id="gemini_free",
            api_factory=create_gemini_client,
            models=_models("GEMINI_MODELS", (
                "gemini-3.5-flash-lite",
                "gemini-3.5-flash",
            )),
        )

    if os.getenv("GROQ_API_KEY"):
        clients["groq_free"] = _rotating_client(
            name="Groq Free Pool",
            group_id="groq_free",
            api_factory=create_groq_client,
            models=_models("GROQ_MODELS", (
                "openai/gpt-oss-120b",
                "openai/gpt-oss-20b",
                "qwen/qwen3.8-27b",
            )),
            # 调查时三个模型各 200K TPD；本地总阈值用于防止无界重试。
            daily_token_limit=_positive_int("GROQ_DAILY_TOKEN_LIMIT", 580_000),
        )

    cloudflare_account = os.getenv("CLOUDFLARE_ACCOUNT_ID")
    if os.getenv("CLOUDFLARE_API_TOKEN") and cloudflare_account:
        clients["cloudflare_free"] = _rotating_client(
            name="Cloudflare Workers AI Free Pool",
            group_id="cloudflare_free",
            api_factory=create_cloudflare_client,
            models=_models("CLOUDFLARE_MODELS", (
                "@cf/ibm-granite/granite-4.0-h-micro",
                "@cf/zai-org/glm-4.7-flash",
            )),
        )

    if os.getenv("OPENROUTER_API_KEY"):
        clients["openrouter_free"] = _rotating_client(
            name="OpenRouter Free Router",
            group_id="openrouter_free",
            api_factory=create_openrouter_client,
            # openrouter/free 自己在当前可用免费模型之间路由；如需固定模型可覆盖此变量。
            models=_models("OPENROUTER_MODELS", ("openrouter/free",)),
            daily_request_limit=_positive_int("OPENROUTER_DAILY_REQUEST_LIMIT", 45),
        )

    if os.getenv("MISTRAL_API_KEY"):
        clients["mistral_free"] = _rotating_client(
            name="Mistral Free Mode",
            group_id="mistral_free",
            api_factory=create_mistral_client,
            models=_models("MISTRAL_MODELS", ("mistral-small-latest",)),
            priority=CLIENT_PRIORITY_CONSUMABLES,
        )

    # NVIDIA 免费端点仅限研发/测试。必须额外显式开启，防止进入正式归档主链。
    if os.getenv("NVIDIA_API_KEY") and os.getenv("ENABLE_NVIDIA_RESEARCH_CLIENT") == "1":
        clients["nvidia_research"] = _rotating_client(
            name="NVIDIA NIM Research Only",
            group_id="nvidia_research",
            api_factory=create_nvidia_client,
            models=_models("NVIDIA_MODELS", (
                "nvidia/nemotron-3-super-120b-a12b",
                "nvidia/nemotron-3-ultra-550b-a55b",
            )),
            priority=CLIENT_PRIORITY_CONSUMABLES,
        )

    return clients


AI_CLIENTS = build_ai_clients()

# 免费服务优先串行，避免单进程 worker 直接撞上 Provider 的并发/TPM 限制。
AI_CLIENT_LIMIT = {
    "siliconflow_free": 1,
    "modelscope_free": 1,
    "gemini_free": 1,
    "groq_free": 1,
    "cloudflare_free": 1,
    "openrouter_free": 1,
    "mistral_free": 1,
    "nvidia_research": 1,
}
