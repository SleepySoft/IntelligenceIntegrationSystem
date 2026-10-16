"""将外围能力连接到 Hub 事件的通用插件。"""

from __future__ import annotations

from typing import Callable, Iterable

from ServiceComponent.runtime.events import HubEvent
from ServiceComponent.runtime.runtime import HubPlugin, HubRuntime


EventCallback = Callable[[HubEvent], None]


class EventCallbackPlugin(HubPlugin):
    """把一个或多个回调订阅到指定事件。

    回调只接收不可变事件，不接触运行时、数据库或其它领域组件。因此可作为
    组合根与现有服务之间的薄适配层，也可被未来子系统复用。
    """

    def __init__(self, event_type: str, callbacks: Iterable[EventCallback] = ()):
        if not isinstance(event_type, str) or not event_type.strip():
            raise ValueError("event_type 必须是非空字符串。")
        self.event_type = event_type
        self._callbacks = list(callbacks)
        if not all(callable(callback) for callback in self._callbacks):
            raise TypeError("callbacks 必须全部可调用。")

    def register(self, runtime: HubRuntime) -> None:
        runtime.subscribe(self.event_type, self._handle)

    def _handle(self, event: HubEvent, runtime: HubRuntime) -> None:
        for callback in self._callbacks:
            callback(event)
