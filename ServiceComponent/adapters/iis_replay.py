"""IIS cache 未归档记录的启动回放扩展。"""

from __future__ import annotations

import logging
import threading
from typing import Any

from ServiceComponent.pipeline import ANALYSIS_REQUESTED
from ServiceComponent.runtime import EventPriority, HubEvent, HubPlugin, HubRuntime


logger = logging.getLogger(__name__)


class IISUnarchivedReplayExtension(HubPlugin):
    """启动时将 cache 中未处理记录直接投回分析阶段。

    已经存在于 cache 的恢复数据不能再经过 intake 去重；否则它会被自身挡掉。
    因此这个适配器明确从 ``analysis.requested`` 续接，保持旧队列恢复语义。
    """

    def __init__(self, subsystem_registry: Any):
        self.subsystem_registry = subsystem_registry
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.replayed = 0

    def register(self, runtime: HubRuntime) -> None:
        return None

    def start(self, runtime: HubRuntime) -> None:
        self._thread = threading.Thread(
            target=self._replay, args=(runtime,), name="IISUnarchivedReplay", daemon=True)
        self._thread.start()

    def stop(self, runtime: HubRuntime) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def _replay(self, runtime: HubRuntime) -> None:
        query = {
            "$and": [
                {"__ARCHIVED__": {"$exists": False}},
                {"APPENDIX.__ARCHIVED__": {"$exists": False}},
            ]
        }
        for context in self.subsystem_registry.enabled_subsystems():
            if self._stop.is_set() or not context.mongo_db_cache:
                continue
            try:
                for record in context.mongo_db_cache.collection.find(query):
                    if self._stop.is_set():
                        return
                    try:
                        runtime.emit(
                            HubEvent(ANALYSIS_REQUESTED, dict(record), context.name),
                            priority=EventPriority.REPLAY,
                        )
                        self.replayed += 1
                    except RuntimeError:
                        return
            except Exception:
                logger.exception("Failed to replay unarchived cache for subsystem %s", context.name)
