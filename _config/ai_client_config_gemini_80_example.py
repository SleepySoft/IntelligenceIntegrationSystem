"""Gemini Flash + Groq 大模型 AI Client 配置（按免费层限额主动控流）。

复制本文件为 ``_config/ai_client_config.py``，直接填写 ``GEMINI_API_KEYS`` 和/或
``GROQ_API_KEYS`` 后使用。未填写映射时仍兼容 ``GEMINI_API_KEY``、
``GROQ_API_KEY`` 环境变量。
配额来自 2026-10-11 AI Studio 免费层项目页面；Google 调整额度后应同步修改
``MODEL_LIMITS``。每个映射项必须代表一份独立额度；同一个 Key 不得重复填写。
请求记录按 Key 的不可逆指纹隔离并持久化到 SQLite，程序重启后仍会继续遵守滚动
RPM/TPM/RPD/TPD 限额。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from AIClientCenter.core.credentials import APIKeyConfig, resolve_api_keys
from AIClientCenter.core.manager import (
    BaseAIClient,
    CLIENT_PRIORITY_CONSUMABLES,
    CLIENT_PRIORITY_FREEBIE,
)
from AIClientCenter.core.rate_limit import EstimatedRateLimiter
from AIClientCenter.providers.openai_clients import RateLimitedOpenAIClient
from AIClientCenter.providers.openai_compatible import create_gemini_client
from AIClientCenter.providers.groq import GROQ_GROUP_ID, build_groq_clients


GROUP_ID = "gemini_flash_80"
DEFAULT_SAFETY_RATIO = 0.8

# 别名只用于客户端显示，Key 不会出现在日志、客户端名或 SQLite namespace 中。
# 同一 Key、同一模型的多个并发槽共享一个限额器；不同 Key 的账本相互独立。
GEMINI_API_KEYS: dict[str, str] = {
    # "google_project_a": "AIza...",
    # "google_project_b": "AIza...",
}
GROQ_API_KEYS: dict[str, str] = {
    # "groq_account_a": "gsk_...",
}


@dataclass(frozen=True)
class GeminiModelLimit:
    rpm: int
    tpm: int
    rpd: int | None
    tier: str


# TPM 是输入 token/minute。这里登记服务商原始额度，由限额器统一使用 80%。
MODEL_LIMITS: dict[str, GeminiModelLimit] = {
    "gemini-3.5-flash-lite": GeminiModelLimit(15, 250_000, 500, "primary"),
    "gemini-3.1-flash-lite": GeminiModelLimit(15, 250_000, 500, "primary"),
    "gemini-2.5-flash-lite": GeminiModelLimit(10, 250_000, 20, "reserve"),
    "gemini-3.8-flash": GeminiModelLimit(5, 250_000, 20, "reserve"),
    "gemini-3.7-flash": GeminiModelLimit(5, 250_000, 20, "reserve"),
    "gemini-3.6-flash": GeminiModelLimit(5, 250_000, 20, "reserve"),
    "gemini-3.5-flash": GeminiModelLimit(5, 250_000, 20, "reserve"),
    "gemini-3-flash": GeminiModelLimit(5, 250_000, 20, "reserve"),
    "gemini-2.5-flash": GeminiModelLimit(5, 250_000, 20, "reserve"),
}


def _positive_int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value < 1:
        raise ValueError(f"{name} 必须为正整数")
    return value


def _ratio() -> float:
    value = float(os.getenv("GEMINI_QUOTA_SAFETY_RATIO", str(DEFAULT_SAFETY_RATIO)))
    if not 0 < value <= 1:
        raise ValueError("GEMINI_QUOTA_SAFETY_RATIO 必须在 (0, 1] 范围内")
    return value


def _selected_models() -> list[str]:
    raw = os.getenv("GEMINI_MODELS", "").strip()
    models = [item.strip() for item in raw.split(",") if item.strip()] \
        if raw else list(MODEL_LIMITS)
    unknown = [model for model in models if model not in MODEL_LIMITS]
    if unknown:
        raise ValueError(f"GEMINI_MODELS 缺少限额定义: {', '.join(unknown)}")
    return models


def _rate_limit_db_path() -> Path:
    configured = os.getenv("GEMINI_RATE_LIMIT_DB", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[1] / "_data" / "ai_rate_limits.sqlite3"


def build_ai_clients(api_keys: APIKeyConfig = None) -> dict[str, BaseAIClient]:
    credentials = resolve_api_keys(api_keys, env_name="GEMINI_API_KEY")
    if not credentials:
        return {}

    ratio = _ratio()
    slots = {
        "primary": _positive_int("GEMINI_PRIMARY_SLOTS", 2),
        "reserve": _positive_int("GEMINI_RESERVE_SLOTS", 1),
    }
    priorities = {
        "primary": CLIENT_PRIORITY_FREEBIE,
        # 低 RPD 模型只在两个主力 Lite 暂时不可用或额度耗尽后接管。
        "reserve": CLIENT_PRIORITY_CONSUMABLES,
    }
    rate_limit_db = _rate_limit_db_path()
    clients: dict[str, BaseAIClient] = {}
    for credential in credentials:
        for model in _selected_models():
            spec = MODEL_LIMITS[model]
            limiter = EstimatedRateLimiter(
                rpm=spec.rpm,
                tpm=spec.tpm,
                rpd=spec.rpd,
                utilization_ratio=ratio,
                storage_path=rate_limit_db,
                storage_namespace=(
                    f"google-generative-language:{credential.quota_id}:{model}"
                ),
            )
            model_key = model.removeprefix("gemini-").replace(".", "_").replace("-", "_")
            for slot in range(1, slots[spec.tier] + 1):
                key = f"gemini_{credential.alias}_{model_key}_{slot}"
                clients[key] = RateLimitedOpenAIClient(
                    name=f"Gemini [{credential.alias}] {model} #{slot}",
                    openai_api=create_gemini_client(
                        token=credential.api_key,
                        model=model,
                    ),
                    rate_limiter=limiter,
                    priority=priorities[spec.tier],
                    group_id=GROUP_ID,
                    default_available=True,
                )
    return clients


AI_CLIENTS = build_ai_clients(GEMINI_API_KEYS)
AI_CLIENTS.update(build_groq_clients(api_keys=GROQ_API_KEYS))

# 每个 Client 对象是一个并发槽；各模型同名槽共享一个限额器。
AI_CLIENT_LIMIT = {
    GROUP_ID: max(1, sum(
        client.group_id == GROUP_ID for client in AI_CLIENTS.values()
    )),
}
if any(client.group_id == GROQ_GROUP_ID for client in AI_CLIENTS.values()):
    # 每个 Key 对应一个单并发 Client；多个独立额度可以并行使用。
    AI_CLIENT_LIMIT[GROQ_GROUP_ID] = sum(
        client.group_id == GROQ_GROUP_ID for client in AI_CLIENTS.values()
    )
