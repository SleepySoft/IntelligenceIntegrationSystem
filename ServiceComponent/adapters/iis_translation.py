"""将 IIS 异步翻译补丁适配为 Hub 外围扩展。"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Dict, Optional

from ServiceComponent.extensions import TRANSLATION_COMPLETED
from ServiceComponent.pipeline import ARCHIVE_COMPLETED
from ServiceComponent.runtime import HubEvent, HubPlugin, HubRuntime


logger = logging.getLogger(__name__)


class IISAsyncTranslationExtension(HubPlugin):
    """归档后异步翻译，并把补丁结果发布为独立扩展事件。"""

    def __init__(
            self,
            *,
            default_subsystem: str,
            translator_factory: Callable[[threading.Event, Callable[[Dict[str, Any]], None]], Any],
            needs_translation: Callable[[Dict[str, Any]], bool],
    ):
        self.default_subsystem = default_subsystem
        self.translator_factory = translator_factory
        self.needs_translation = needs_translation
        self._shutdown = threading.Event()
        self._runtime: Optional[HubRuntime] = None
        self.translator: Any = None

    def register(self, runtime: HubRuntime) -> None:
        runtime.subscribe(ARCHIVE_COMPLETED, self._on_archived)

    def start(self, runtime: HubRuntime) -> None:
        self._runtime = runtime
        self.translator = self.translator_factory(self._shutdown, self._on_patched)
        self.translator.start()

    def stop(self, runtime: HubRuntime) -> None:
        self._shutdown.set()

    def should_defer_index(self, event: HubEvent) -> bool:
        """供索引扩展判断：待翻译记录不应先按原文入索引。"""
        return (event.subsystem == self.default_subsystem and
                isinstance(event.payload, dict) and self.needs_translation(event.payload))

    def _on_archived(self, event: HubEvent, runtime: HubRuntime) -> None:
        if not self.should_defer_index(event):
            return
        identifier = str(
            event.payload.get("intelligence_uuid")
            or event.payload.get("_id")
            or ""
        ).strip()
        if identifier:
            self.translator.enqueue_new(identifier, reason="new_archived")

    def _on_patched(self, payload: Dict[str, Any]) -> None:
        runtime = self._runtime
        if runtime is None:
            return
        try:
            runtime.emit(HubEvent(
                TRANSLATION_COMPLETED, dict(payload), subsystem=self.default_subsystem))
        except RuntimeError:
            logger.debug("Translation completed during Hub shutdown; index event ignored.")
