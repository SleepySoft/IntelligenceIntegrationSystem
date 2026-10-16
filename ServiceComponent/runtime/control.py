"""运行时控制开关：暂停采集/分析等处理环节，用于维护与发布测试场景。

设计约定：
- 进程内存态，不持久化——重启即恢复全运行，避免"忘了关"造成停产；
- ``maintenance`` 是总开关，开启后所有 scope 视为暂停；
- 暂停语义是"不再消费/接收新工作"，队列积压由 max_inflight 等既有机制兜底；
- 每次切换写审计日志（操作人、scope、动作、时间）。
"""

from __future__ import annotations

import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

# 可暂停的处理环节。translation/vector 本期为预留位（扩展插件尚未接入检查）。
CONTROL_SCOPES = ("intake", "analysis", "shadow", "translation", "vector")
SCOPE_MAINTENANCE = "maintenance"
ALL_SCOPES = frozenset((*CONTROL_SCOPES, SCOPE_MAINTENANCE))


class RuntimeControlState:
    """线程安全的运行控制开关集合。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._paused: set[str] = set()

    def is_paused(self, scope: str) -> bool:
        with self._lock:
            return SCOPE_MAINTENANCE in self._paused or scope in self._paused

    def pause(self, scope: str, operator: str = "") -> None:
        self._set(scope, True, operator)

    def resume(self, scope: str, operator: str = "") -> None:
        self._set(scope, False, operator)

    def status(self) -> dict[str, Any]:
        with self._lock:
            paused = set(self._paused)
        return {
            "maintenance": SCOPE_MAINTENANCE in paused,
            "scopes": {
                scope: (SCOPE_MAINTENANCE in paused) or scope in paused
                for scope in CONTROL_SCOPES
            },
            "explicitly_paused": sorted(paused),
        }

    def _set(self, scope: str, paused: bool, operator: str) -> None:
        if scope not in ALL_SCOPES:
            raise ValueError(f"未知控制范围: {scope!r}（可选: {sorted(ALL_SCOPES)}）")
        with self._lock:
            if paused:
                self._paused.add(scope)
            else:
                self._paused.discard(scope)
        logger.info(
            "运行控制变更: %s %s（操作人: %s）",
            "暂停" if paused else "恢复", scope, operator or "unknown",
        )
