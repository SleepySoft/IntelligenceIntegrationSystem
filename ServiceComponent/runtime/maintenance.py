"""不属于核心流程的定时维护插件。"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Optional

from ServiceComponent.runtime.runtime import HubPlugin, HubRuntime


logger = logging.getLogger(__name__)


class ScheduledMaintenancePlugin(HubPlugin):
    """由组合根提供调度器和任务清单的生命周期包装器。"""

    def __init__(self, scheduler_factory: Callable[[], Any],
                 configure: Callable[[Any], None],
                 bootstrap: Optional[Callable[[], None]] = None):
        self.scheduler_factory = scheduler_factory
        self.configure = configure
        self.bootstrap = bootstrap
        self.scheduler: Any = None
        self._bootstrap_thread: Optional[threading.Thread] = None

    def register(self, runtime: HubRuntime) -> None:
        return None

    def start(self, runtime: HubRuntime) -> None:
        self.scheduler = self.scheduler_factory()
        self.configure(self.scheduler)
        self.scheduler.start_scheduler()
        if self.bootstrap:
            self._bootstrap_thread = threading.Thread(
                target=self._run_bootstrap, name="HubMaintenanceBootstrap", daemon=True)
            self._bootstrap_thread.start()

    def stop(self, runtime: HubRuntime) -> None:
        if self.scheduler:
            self.scheduler.shutdown(wait=False)

    def _run_bootstrap(self) -> None:
        try:
            self.bootstrap()
        except Exception:
            logger.exception("Hub maintenance bootstrap failed.")
