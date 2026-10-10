"""可组合的 Hub 运行时：事件队列、插件生命周期与异常隔离。"""

from __future__ import annotations

import logging
import itertools
import queue
import threading
from abc import ABC, abstractmethod
from collections import defaultdict
from enum import IntEnum
from typing import Callable, DefaultDict, Dict, List, Optional

from ServiceComponent.runtime.events import HandlerFailure, HubEvent


logger = logging.getLogger(__name__)

EventHandler = Callable[[HubEvent, "HubRuntime"], None]
HANDLER_FAILED_EVENT = "runtime.handler_failed"
_STOP = object()


class EventPriority(IntEnum):
    """运行时调度车道；数值越小越先处理。"""

    CONTINUATION = 0
    LIVE = 10
    REPLAY = 20


_PRIORITY_LANES = {
    EventPriority.CONTINUATION: "continuation",
    EventPriority.LIVE: "live",
    EventPriority.REPLAY: "replay",
}


class HubPlugin(ABC):
    """运行时扩展的最小生命周期协议。"""

    @abstractmethod
    def register(self, runtime: "HubRuntime") -> None:
        """订阅事件；不得在此启动线程或执行耗时任务。"""

    def start(self, runtime: "HubRuntime") -> None:
        """运行时启动后调用。"""

    def stop(self, runtime: "HubRuntime") -> None:
        """运行时停止前调用。"""


class HubRuntime:
    """不感知业务数据、存储、AI 或 Web 框架的事件运行时。"""

    def __init__(self, worker_count: int = 1, queue_size: int = 0,
                 max_failures: int = 100):
        if worker_count < 1:
            raise ValueError("worker_count 必须大于 0。")
        if queue_size < 0:
            raise ValueError("queue_size 不能小于 0。")
        if max_failures < 1:
            raise ValueError("max_failures 必须大于 0。")

        self._worker_count = worker_count
        self._queue: queue.PriorityQueue[tuple[int, int, HubEvent | object, str]] = (
            queue.PriorityQueue(maxsize=queue_size)
        )
        self._queue_sequence = itertools.count()
        self._handlers: DefaultDict[str, List[EventHandler]] = defaultdict(list)
        self._plugins: List[HubPlugin] = []
        self._workers: List[threading.Thread] = []
        self._failures: List[HandlerFailure] = []
        self._max_failures = max_failures

        self._lock = threading.RLock()
        self._dispatch_context = threading.local()
        self._active_count = 0
        self._running = False
        self._stopping = False
        self._stopped = False
        self._idle = threading.Event()
        self._idle.set()
        self._stats: Dict[str, int] = {
            "emitted": 0,
            "processed": 0,
            "handler_calls": 0,
            "handler_failures": 0,
        }
        self._queued_by_type: DefaultDict[str, int] = defaultdict(int)
        self._queued_by_lane: DefaultDict[str, int] = defaultdict(int)
        self._active_by_type: DefaultDict[str, int] = defaultdict(int)
        self._processed_by_type: DefaultDict[str, int] = defaultdict(int)

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        """注册一个事件处理器；相同处理器不会重复注册。"""
        if not isinstance(event_type, str) or not event_type.strip():
            raise ValueError("event_type 必须是非空字符串。")
        if not callable(handler):
            raise TypeError("handler 必须可调用。")
        with self._lock:
            if handler not in self._handlers[event_type]:
                self._handlers[event_type].append(handler)

    def install(self, plugin: HubPlugin) -> None:
        """安装插件。运行时已启动时，插件立即进入 start 生命周期。"""
        if not isinstance(plugin, HubPlugin):
            raise TypeError("plugin 必须继承 HubPlugin。")
        with self._lock:
            if self._stopped or self._stopping:
                raise RuntimeError("HubRuntime 正在停止或已停止，不能安装插件。")
            if plugin in self._plugins:
                return
            plugin.register(self)
            self._plugins.append(plugin)
            running = self._running
        if running:
            plugin.start(self)

    def emit(self, event: HubEvent, *, block: bool = True,
             timeout: Optional[float] = None,
             priority: int | EventPriority | None = None) -> None:
        """投递事件；派生阶段、新数据和回放数据使用独立优先级车道。

        处理器内部产生的后续事件默认进入 ``continuation``，保证一条已开始的
        流程尽快走到终态；外部提交默认进入 ``live``。批量恢复必须显式指定
        ``EventPriority.REPLAY``，仅在前两条车道暂时为空时才会被消费。
        """
        if not isinstance(event, HubEvent):
            raise TypeError("event 必须是 HubEvent。")
        if priority is None:
            priority = (
                EventPriority.CONTINUATION
                if getattr(self._dispatch_context, "active", False)
                else EventPriority.LIVE
            )
        priority_value = int(priority)
        lane = _PRIORITY_LANES.get(priority_value, f"priority_{priority_value}")
        with self._lock:
            if self._stopped or (self._stopping and not getattr(
                    self._dispatch_context, "active", False)):
                raise RuntimeError("HubRuntime 已停止，不能再投递事件。")
            self._idle.clear()
            self._stats["emitted"] += 1
            self._queued_by_type[event.event_type] += 1
            self._queued_by_lane[lane] += 1
        try:
            self._queue.put(
                (priority_value, next(self._queue_sequence), event, lane),
                block=block,
                timeout=timeout,
            )
        except Exception:
            with self._lock:
                self._stats["emitted"] -= 1
                self._queued_by_type[event.event_type] -= 1
                self._queued_by_lane[lane] -= 1
                if not any(self._queued_by_type.values()) and self._active_count == 0:
                    self._idle.set()
            raise

    def start(self) -> None:
        """先启动已安装插件，再启动 worker 消费已排队事件。"""
        with self._lock:
            if self._running:
                return
            if self._stopped:
                raise RuntimeError("HubRuntime 已停止，不能重新启动。")
            self._running = True
            self._workers = [
                threading.Thread(
                    target=self._worker_loop,
                    name=f"HubRuntimeWorker-{index + 1}",
                    daemon=True,
                )
                for index in range(self._worker_count)
            ]
            plugins = list(self._plugins)
        for plugin in plugins:
            plugin.start(self)
        for worker in self._workers:
            worker.start()

    def stop(self, timeout: float = 5.0, *, drain: bool = True) -> None:
        """停止插件和 worker。默认先处理已入队事件，避免静默丢失。"""
        if timeout < 0:
            raise ValueError("timeout 不能小于 0。")
        with self._lock:
            if self._stopped:
                return
            if self._stopping:
                return
            self._stopping = True
            plugins = list(reversed(self._plugins))
            workers = list(self._workers)
            self._running = False
        if drain:
            self.wait_for_idle(timeout=timeout)
        for plugin in plugins:
            try:
                plugin.stop(self)
            except Exception:
                logger.exception("Hub plugin stop failed: %s", type(plugin).__name__)
        for _ in workers:
            self._queue.put((-1, next(self._queue_sequence), _STOP, "stop"))
        for worker in workers:
            worker.join(timeout=timeout)
        with self._lock:
            self._stopped = True

    def wait_for_idle(self, timeout: Optional[float] = None) -> bool:
        """等待队列和正在运行的处理器均清空。"""
        return self._idle.wait(timeout=timeout)

    @property
    def failures(self) -> List[HandlerFailure]:
        with self._lock:
            return list(self._failures)

    @property
    def stats(self) -> Dict[str, int]:
        with self._lock:
            return {
                **self._stats,
                "pending_events": self._queue.qsize(),
                "active_handlers": self._active_count,
                "queued_by_type": {
                    key: value for key, value in self._queued_by_type.items() if value
                },
                "queued_by_lane": {
                    key: value for key, value in self._queued_by_lane.items() if value
                },
                "active_by_type": {
                    key: value for key, value in self._active_by_type.items() if value
                },
                "processed_by_type": {
                    key: value for key, value in self._processed_by_type.items() if value
                },
            }

    def _worker_loop(self) -> None:
        while True:
            _, _, item, lane = self._queue.get()
            try:
                if item is _STOP:
                    return
                assert isinstance(item, HubEvent)
                with self._lock:
                    self._queued_by_type[item.event_type] -= 1
                    self._queued_by_lane[lane] -= 1
                    self._active_count += 1
                    self._active_by_type[item.event_type] += 1
                self._dispatch(item)
            finally:
                with self._lock:
                    if item is not _STOP:
                        self._active_count -= 1
                        self._active_by_type[item.event_type] -= 1
                        self._stats["processed"] += 1
                        self._processed_by_type[item.event_type] += 1
                    if self._queue.empty() and self._active_count == 0:
                        self._idle.set()
                self._queue.task_done()

    def _dispatch(self, event: HubEvent) -> None:
        with self._lock:
            handlers = list(self._handlers.get(event.event_type, ()))
        self._dispatch_context.active = True
        try:
            for handler in handlers:
                try:
                    handler(event, self)
                    with self._lock:
                        self._stats["handler_calls"] += 1
                except Exception as exc:
                    self._record_failure(event, handler, exc)
        finally:
            self._dispatch_context.active = False

    def _record_failure(self, event: HubEvent, handler: EventHandler, exc: Exception) -> None:
        failure = HandlerFailure(
            event_id=event.event_id,
            event_type=event.event_type,
            handler_name=getattr(handler, "__qualname__", repr(handler)),
            exception_type=type(exc).__name__,
            message=str(exc),
        )
        with self._lock:
            self._failures.append(failure)
            del self._failures[:-self._max_failures]
            self._stats["handler_failures"] += 1
        logger.exception("Hub event handler failed: event=%s handler=%s",
                         event.event_type, failure.handler_name)
        if event.event_type != HANDLER_FAILED_EVENT:
            self.emit(event.derive(HANDLER_FAILED_EVENT, payload=failure))
