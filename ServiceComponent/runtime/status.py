"""Hub 运行状态的单行汇总与打印节流。"""

from __future__ import annotations

from typing import Any, Mapping


_STATUS_STAGE_GROUPS = {
    "intake": {"intake.received", "intake.accepted", "intake.rejected"},
    "analysis": {"analysis.requested"},
    "result": {"analysis.completed", "analysis.failed"},
    "archive": {"archive.requested"},
    "terminal": {"archive.completed", "archive.failed"},
}
_GROUP_ORDER = ("intake", "analysis", "result", "archive", "terminal", "other")


def _group_event_counts(counts: Mapping[str, int] | None) -> dict[str, int]:
    grouped = {name: 0 for name in _GROUP_ORDER}
    for event_type, count in (counts or {}).items():
        group = next((name for name, types in _STATUS_STAGE_GROUPS.items()
                      if event_type in types), "other")
        grouped[group] += int(count)
    return grouped


def _numeric_snapshot(value: Any):
    """只提取数值状态；配置描述变化不触发运行状态日志。"""
    if isinstance(value, dict):
        return tuple(sorted(
            (str(key), snapshot)
            for key, item in value.items()
            if (snapshot := _numeric_snapshot(item)) is not None
        ))
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    return None


def format_hub_status(statistics: Mapping[str, Any]) -> str:
    runtime = statistics.get("runtime", {})
    analysis = statistics.get("analysis", {})
    queued = _group_event_counts(runtime.get("queued_by_type"))
    active = _group_event_counts(runtime.get("active_by_type"))
    done = _group_event_counts(runtime.get("processed_by_type"))

    def group_text(values: Mapping[str, int]) -> str:
        return "/".join(f"{key}={values[key]}" for key in _GROUP_ORDER)

    return (
        "HUB STATUS | "
        f"events emitted={runtime.get('emitted', 0)} processed={runtime.get('processed', 0)} "
        f"handler_failed={runtime.get('handler_failures', 0)} | "
        f"queued total={runtime.get('pending_events', 0)} [{group_text(queued)}] | "
        f"active total={runtime.get('active_handlers', 0)} [{group_text(active)}] | "
        "ai "
        f"wait={analysis.get('waiting_client', 0)}/run={analysis.get('ai_running', 0)}/"
        f"validate={analysis.get('validation_running', 0)} "
        f"attempt={analysis.get('attempts', 0)}/response={analysis.get('responses', 0)}/"
        f"retry={analysis.get('retries', 0)}/valid={analysis.get('validated', 0)}/"
        f"fail={analysis.get('failed', 0)} "
        f"errors(call={analysis.get('call_errors', 0)}/api={analysis.get('api_errors', 0)}/"
        f"validation={analysis.get('validation_errors', 0)}) | "
        f"done [{group_text(done)}] | "
        f"submissions={runtime.get('in_flight_submissions', 0)}"
    )


class HubStatusLogGate:
    """数值变化时放行；无变化时按固定周期放行一次心跳。"""

    def __init__(self, unchanged_log_interval: float = 60.0):
        if unchanged_log_interval <= 0:
            raise ValueError("unchanged_log_interval 必须大于 0。")
        self._unchanged_log_interval = unchanged_log_interval
        self._previous_snapshot = None
        self._last_logged_at = 0.0

    def should_log(self, statistics: Mapping[str, Any], now: float) -> bool:
        snapshot = _numeric_snapshot(statistics)
        if (snapshot != self._previous_snapshot
                or now - self._last_logged_at >= self._unchanged_log_interval):
            self._previous_snapshot = snapshot
            self._last_logged_at = now
            return True
        return False
