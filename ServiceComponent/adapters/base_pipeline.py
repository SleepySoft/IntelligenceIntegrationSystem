"""Version-neutral infrastructure shared by Hub pipeline adapters."""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Optional

from tenacity import wait_exponential


logger = logging.getLogger(__name__)


class BasePipelinePorts:
    """AI client, prompt-debug and subsystem plumbing without a data schema."""

    def __init__(
        self,
        subsystem_registry: Any,
        ai_client_manager: Any,
        *,
        analyzer: Optional[Callable[[Any, str, dict], dict]] = None,
        transient_analyzer: Optional[Callable[[Any, str, dict], dict]] = None,
        scorer_factory: Callable[[Optional[dict]], Any] | None = None,
        retry_wait: Any = None,
        client_wait_interval: float = 1.0,
    ):
        if client_wait_interval <= 0:
            raise ValueError("client_wait_interval 必须大于 0。")
        self._registry = subsystem_registry
        self._ai_client_manager = ai_client_manager
        self._analyzer = analyzer or self._load_default_analyzer()
        self._transient_analyzer = (
            transient_analyzer
            or (analyzer if analyzer is not None else self._load_default_transient_analyzer())
        )
        self._scorer_factory = scorer_factory or self._load_default_scorer_factory()
        self._stop_event = threading.Event()
        self._dedupe_lock = threading.RLock()
        self._retry_wait = retry_wait or wait_exponential(multiplier=1, min=1, max=30)
        self._client_wait_interval = client_wait_interval

    @staticmethod
    def _load_default_scorer_factory() -> Callable[[Optional[dict]], Any]:
        from ServiceComponent.IntelligenceScoringEngine import IntelligenceScoringEngine

        def build(config: Optional[dict]) -> IntelligenceScoringEngine:
            return IntelligenceScoringEngine(config=config) if config else IntelligenceScoringEngine()

        return build

    @staticmethod
    def _load_default_analyzer() -> Callable[[Any, str, dict], dict]:
        from ServiceComponent.IntelligenceAnalyzerProxy import analyze_with_ai
        return analyze_with_ai

    @staticmethod
    def _load_default_transient_analyzer() -> Callable[[Any, str, dict], dict]:
        from ServiceComponent.IntelligenceAnalyzerProxy import analyze_with_ai_transient
        return analyze_with_ai_transient

    def stop(self) -> None:
        self._stop_event.set()

    @staticmethod
    def _transient_prompt_options(original_data: dict) -> tuple[int | None, str | None]:
        settings = original_data.get("temp_data", {}).get("manual_debug", {})
        version = settings.get("prompt_version")
        version = int(version) if version not in (None, "") else None
        override = original_data.get("prompt")
        if override is not None:
            override = str(override)
            if not override.strip():
                raise ValueError("prompt_override 不能为空")
        return version, override

    def _wait_for_ai_client(self, ctx: Any, user_name: str) -> Any:
        attempts = 0
        while not self._stop_event.is_set():
            kwargs = {}
            if getattr(ctx, "ai_client_group", None):
                kwargs["target_group_id"] = ctx.ai_client_group
            client = self._ai_client_manager.get_available_client(user_name, **kwargs)
            if client is not None:
                return client
            attempts += 1
            if attempts % 10 == 0:
                logger.warning(
                    "Hub analysis is waiting for an available AI client (%ss).", attempts)
            self._stop_event.wait(self._client_wait_interval)
        raise RuntimeError("hub_stopping_while_waiting_for_ai_client")

    def _resolve_context(self, event: Any) -> Any:
        ctx = self._registry.resolve(event.subsystem)
        if ctx is None:
            raise ValueError(f"unknown_subsystem:{event.subsystem!r}")
        return ctx

    @staticmethod
    def _increment_stat(ctx: Any, name: str) -> None:
        stats = getattr(ctx, "stats", None)
        if isinstance(stats, dict):
            stats[name] = stats.get(name, 0) + 1
