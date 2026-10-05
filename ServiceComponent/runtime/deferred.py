"""依赖其它可选服务的延迟初始化插件。"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Optional

from ServiceComponent.runtime.runtime import HubPlugin, HubRuntime


logger = logging.getLogger(__name__)


class DeferredServicePlugin(HubPlugin):
    """在前置条件满足后创建服务，避免 Hub 轮询基础设施状态。"""

    def __init__(self, available: Callable[[], bool], factory: Callable[[], Any],
                 *, retry_interval: float = 1.0, name: str = "deferred-service"):
        self.available = available
        self.factory = factory
        self.retry_interval = retry_interval
        self.name = name
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._service: Any = None
        self._lock = threading.RLock()

    def register(self, runtime: HubRuntime) -> None:
        return None

    def start(self, runtime: HubRuntime) -> None:
        self._thread = threading.Thread(
            target=self._initialize, name=f"Hub-{self.name}", daemon=True)
        self._thread.start()

    def stop(self, runtime: HubRuntime) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    @property
    def ready(self) -> bool:
        return self._ready.is_set()

    @property
    def service(self) -> Any:
        with self._lock:
            return self._service

    def __bool__(self) -> bool:
        return self.service is not None

    def __getattr__(self, name: str) -> Any:
        service = self.service
        if service is None:
            raise AttributeError(f"{self.name} 尚未就绪，不能访问 {name}。")
        return getattr(service, name)

    def _initialize(self) -> None:
        while not self._stop.is_set():
            try:
                if self.available():
                    service = self.factory()
                    with self._lock:
                        self._service = service
                    self._ready.set()
                    logger.info("Deferred service ready: %s", self.name)
                    return
            except Exception:
                logger.exception("Deferred service initialization failed: %s", self.name)
            self._stop.wait(self.retry_interval)
