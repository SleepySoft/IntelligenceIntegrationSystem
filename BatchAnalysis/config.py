"""批量分析子系统配置：读取 optional_subsystems.batch_analysis 的运行时投影。

与其他子系统（vector/situation/entity_alias）同层：配置声明在 IISConfig 的
OptionalSubsystemSettings，运行时投影为顶层 batch_analysis.* 键。
"""

from __future__ import annotations

DEFAULT_BASE_URL = "https://batch-api-cn.xiaomimimo.com/v1"
DEFAULT_MODEL = "mimo-v2.6-flash"  # batch 要求小写模型名


class BatchAnalysisConfig:
    """从组合根投影读取子系统配置（带默认值）。"""

    def __init__(self, raw: dict | None):
        values = dict(raw or {}) if isinstance(raw, dict) else {}
        self.enabled = bool(values.get("enabled", False))
        self.base_url = str(values.get("base_url") or DEFAULT_BASE_URL).rstrip("/")
        self.model = str(values.get("model") or DEFAULT_MODEL)
        self.lookback_days = _bounded_int(values, "lookback_days", 7, 1, 30)
        self.max_items = _bounded_int(values, "max_items", 500, 1, 5000)
        self.poll_interval_sec = _bounded_float(values, "poll_interval_sec", 300.0, 30, 86400)


def _bounded_int(values, name, default, minimum, maximum):
    try:
        value = int(values.get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _bounded_float(values, name, default, minimum, maximum):
    try:
        value = float(values.get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))
