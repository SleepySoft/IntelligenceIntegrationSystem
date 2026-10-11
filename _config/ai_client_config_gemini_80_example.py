"""Gemini Flash 专用 AI Client 配置（按项目限额的 80% 主动控流）。

复制本文件为 ``_config/ai_client_config.py``，设置 ``GEMINI_API_KEY`` 后使用。
配额来自 2026-10-11 AI Studio Tier 1 项目页面；Google 调整额度后应同步修改
``MODEL_LIMITS``。多个 API Key 不会放大同一项目的额度。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from AIClientCenter.core.manager import (
    BaseAIClient,
    CLIENT_PRIORITY_CONSUMABLES,
    CLIENT_PRIORITY_FREEBIE,
)
from AIClientCenter.core.rate_limit import EstimatedRateLimiter
from AIClientCenter.providers.openai_clients import RateLimitedOpenAIClient
from AIClientCenter.providers.openai_compatible import create_gemini_client


GROUP_ID = "gemini_flash_80"
DEFAULT_SAFETY_RATIO = 0.8


@dataclass(frozen=True)
class GeminiModelLimit:
    rpm: int
    tpm: int
    rpd: int | None
    tier: str


# TPM 是输入 token/minute；RPD=None 表示项目页面显示“不限”。
MODEL_LIMITS: dict[str, GeminiModelLimit] = {
    "gemini-3.5-flash-lite": GeminiModelLimit(4000, 4_000_000, 150_000, "lite"),
    "gemini-3.1-flash-lite": GeminiModelLimit(4000, 4_000_000, 150_000, "lite"),
    "gemini-2.5-flash-lite": GeminiModelLimit(4000, 4_000_000, None, "lite"),
    "gemini-3.8-flash": GeminiModelLimit(1000, 2_000_000, 10_000, "quality"),
    "gemini-3.7-flash": GeminiModelLimit(1000, 2_000_000, 10_000, "quality"),
    "gemini-3.6-flash": GeminiModelLimit(1000, 2_000_000, 10_000, "quality"),
    "gemini-3.5-flash": GeminiModelLimit(1000, 2_000_000, 10_000, "quality"),
    "gemini-2.5-flash": GeminiModelLimit(1000, 1_000_000, 10_000, "quality"),
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


def build_ai_clients() -> dict[str, BaseAIClient]:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        return {}

    ratio = _ratio()
    slots = {
        "lite": _positive_int("GEMINI_FLASH_LITE_SLOTS", 2),
        "quality": _positive_int("GEMINI_FLASH_QUALITY_SLOTS", 1),
    }
    priorities = {
        "lite": CLIENT_PRIORITY_FREEBIE,
        # 普通 Flash 默认只在 Lite 无可用槽/额度时接管；人工测试仍可按名称固定选择。
        "quality": CLIENT_PRIORITY_CONSUMABLES,
    }
    limiters = {
        model: EstimatedRateLimiter(
            rpm=MODEL_LIMITS[model].rpm,
            tpm=MODEL_LIMITS[model].tpm,
            rpd=MODEL_LIMITS[model].rpd,
            utilization_ratio=ratio,
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

# 每个 Client 对象是一个并发槽；各模型同名槽共享一个限额器。
AI_CLIENT_LIMIT = {GROUP_ID: max(1, len(AI_CLIENTS))}
