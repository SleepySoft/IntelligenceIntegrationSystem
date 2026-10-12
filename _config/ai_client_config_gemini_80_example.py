"""Gemini Flash + Groq 大模型 AI Client 配置（按免费层限额主动控流）。

复制本文件为 ``_config/ai_client_config.py``，设置 ``GEMINI_API_KEY`` 和/或
``GROQ_API_KEY`` 后使用。
配额来自 2026-10-11 AI Studio 免费层项目页面；Google 调整额度后应同步修改
``MODEL_LIMITS``。多个 API Key 不会放大同一项目的额度。请求记录持久化到 SQLite，
程序重启后仍会继续遵守滚动 RPM/TPM/RPD/TPD 限额。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

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


def build_ai_clients() -> dict[str, BaseAIClient]:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
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
    limiters = {
        model: EstimatedRateLimiter(
            rpm=MODEL_LIMITS[model].rpm,
            tpm=MODEL_LIMITS[model].tpm,
            rpd=MODEL_LIMITS[model].rpd,
            utilization_ratio=ratio,
            storage_path=rate_limit_db,
            storage_namespace=f"google-generative-language:{model}",
        )
        for model in _selected_models()
    }
    clients: dict[str, BaseAIClient] = {}
    for model, limiter in limiters.items():
        spec = MODEL_LIMITS[model]
        for slot in range(1, slots[spec.tier] + 1):
            key = f"gemini_{model.removeprefix('gemini-').replace('.', '_').replace('-', '_')}_{slot}"
            clients[key] = RateLimitedOpenAIClient(
                name=f"Gemini {model} #{slot}",
                openai_api=create_gemini_client(token=api_key, model=model),
                rate_limiter=limiter,
                priority=priorities[spec.tier],
                group_id=GROUP_ID,
                default_available=True,
            )
    return clients


AI_CLIENTS = build_ai_clients()
AI_CLIENTS.update(build_groq_clients())

# 每个 Client 对象是一个并发槽；各模型同名槽共享一个限额器。
AI_CLIENT_LIMIT = {
    GROUP_ID: max(1, sum(
        client.group_id == GROUP_ID for client in AI_CLIENTS.values()
    )),
}
if any(client.group_id == GROQ_GROUP_ID for client in AI_CLIENTS.values()):
    # Groq 免费层的 120B 模型只有 8K TPM，固定单并发，避免并行请求争抢额度。
    AI_CLIENT_LIMIT[GROQ_GROUP_ID] = 1
