"""AI 分析错误的统一分类法与重试策略。

两端错误此前几乎不互通：Client 端（网络/限流/Provider 拒绝/内容安全）与
IIS 端（JSON 语法/结构校验/语义规则）。本模块把它们映射到同一枚举，
并给出每类的差异化重试策略，供分析主链与统计落库共用。

分类是确定性的纯函数，不做任何网络或数据库操作。
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError


class AnalysisErrorClass:
    """错误类别（字符串常量，便于直接落库与聚合）。"""

    NETWORK = "network"                 # 连接/超时/DNS/5xx
    RATE_LIMIT = "rate_limit"           # 429
    PROVIDER_FATAL = "provider_fatal"   # 4xx 非安全类：区域封锁/鉴权/参数错误
    CONTENT_SAFETY = "content_safety"   # 内容安全拒绝
    JSON_SYNTAX = "json_syntax"         # JSON 语法（本地修复失败后的残余）
    SCHEMA_SHAPE = "schema_shape"       # 结构校验：缺字段/类型/长度（Pydantic 层）
    SEMANTIC_RULE = "semantic_rule"     # 语义规则：Registry 生命周期/枚举/角色
    UNKNOWN = "unknown"


# 重试动作
RETRY_ANY = "retry_any"               # 换客户端重试（现状默认）
RETRY_SAME_MODEL = "retry_same_model"  # 同客户端带反馈重试一次（语义错误的纠错首选）
TERMINAL = "terminal"                 # 不再重试

# 差异化重试路由表：语义/结构错误同模型纠错一次优于跨模型乱跳；
# Provider 致命与限流必须换；内容安全由主链现有敏感终态处理。
RETRY_POLICY = {
    AnalysisErrorClass.NETWORK: RETRY_ANY,
    AnalysisErrorClass.RATE_LIMIT: RETRY_ANY,
    AnalysisErrorClass.PROVIDER_FATAL: RETRY_ANY,
    AnalysisErrorClass.CONTENT_SAFETY: TERMINAL,
    AnalysisErrorClass.JSON_SYNTAX: RETRY_ANY,
    AnalysisErrorClass.SCHEMA_SHAPE: RETRY_SAME_MODEL,
    AnalysisErrorClass.SEMANTIC_RULE: RETRY_SAME_MODEL,
    AnalysisErrorClass.UNKNOWN: RETRY_ANY,
}

# Registry/生命周期语义错误的特征文本（validate_event_extraction 抛 ValueError）
_SEMANTIC_HINTS = (
    "unsupported", "未登记", "not registered", "unknown predicate",
    "qualifier", "frame", "lifecycle",
)


def classify_analysis_error(category: str, error: Any,
                            raw_response: Any = None) -> str:
    """把一次分析失败分类到 AnalysisErrorClass。

    category 为主链的三类失败通道：call（调用异常）/ api（错误字典）/ validation（校验）。
    """
    if category == "call":
        name = type(error).__name__.lower()
        if any(k in name for k in ("timeout", "connection", "network", "readtimeout")):
            return AnalysisErrorClass.NETWORK
        return AnalysisErrorClass.UNKNOWN

    if category == "api":
        code = str((raw_response or {}).get("api_error_code") or "").upper()
        if code == "CONTENT_SAFETY":
            return AnalysisErrorClass.CONTENT_SAFETY
        if code == "HTTP_429":
            return AnalysisErrorClass.RATE_LIMIT
        if code.startswith("HTTP_5"):
            return AnalysisErrorClass.NETWORK
        if code.startswith("HTTP_4"):
            return AnalysisErrorClass.PROVIDER_FATAL
        return AnalysisErrorClass.UNKNOWN

    if category == "validation":
        if isinstance(error, ValidationError):
            return AnalysisErrorClass.SCHEMA_SHAPE
        text = str(error).lower()
        if isinstance(error, ValueError) and any(h in text for h in _SEMANTIC_HINTS):
            return AnalysisErrorClass.SEMANTIC_RULE
        if isinstance(error, ValueError):
            return AnalysisErrorClass.SEMANTIC_RULE
        return AnalysisErrorClass.UNKNOWN

    return AnalysisErrorClass.UNKNOWN


def retry_action_for(error_class: str) -> str:
    return RETRY_POLICY.get(error_class, RETRY_ANY)
