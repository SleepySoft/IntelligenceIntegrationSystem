"""Version-neutral infrastructure shared by Hub pipeline adapters."""

from __future__ import annotations

import logging
import threading
import time
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
        client_wait_timeout: float = 60.0,
        analysis_worker_count: int = 1,
    ):
        if client_wait_timeout <= 0:
            raise ValueError("client_wait_timeout 必须大于 0。")
        if analysis_worker_count < 1:
            raise ValueError("analysis_worker_count 必须大于 0。")
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
        self._client_wait_timeout = client_wait_timeout
        self._analysis_worker_count = analysis_worker_count
        self._wait_warning_lock = threading.Lock()
        self._last_wait_warning = 0.0
        self._runtime_stats_lock = threading.RLock()
        self._runtime_stats = {
            "waiting_client": 0,
            "ai_running": 0,
            "validation_running": 0,
            "attempts": 0,
            "responses": 0,
            "retries": 0,
            "validated": 0,
            "failed": 0,
            "call_errors": 0,
            "api_errors": 0,
            "validation_errors": 0,
            "last_call_ms": 0,
            "call_ms_total": 0,
        }

    @property
    def statistics(self) -> dict[str, int]:
        with self._runtime_stats_lock:
            return dict(self._runtime_stats)

    def _change_runtime_stat(self, name: str, amount: int = 1) -> None:
        with self._runtime_stats_lock:
            self._runtime_stats[name] += amount

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
        notify_waiters = getattr(self._ai_client_manager, "notify_waiters", None)
        if callable(notify_waiters):
            notify_waiters()

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
        self._change_runtime_stat("waiting_client", 1)
        try:
            while not self._stop_event.is_set():
                kwargs = {}
                if getattr(ctx, "ai_client_group", None):
                    kwargs["target_group_id"] = ctx.ai_client_group
                client = self._ai_client_manager.wait_for_available_client(
                    user_name,
                    timeout=self._client_wait_timeout,
                    cancel_event=self._stop_event,
                    **kwargs,
                )
                if client is not None:
                    return client
                if self._stop_event.is_set():
                    break

                capacity = self._ai_client_manager.get_scheduling_capacity(
                    target_group_id=kwargs.get("target_group_id"),
                )
                self._warn_client_wait_timeout(capacity, kwargs.get("target_group_id"))
            raise RuntimeError("hub_stopping_while_waiting_for_ai_client")
        finally:
            self._change_runtime_stat("waiting_client", -1)

    def _warn_client_wait_timeout(self, capacity: int, target_group_id: str | None) -> None:
        """多个 worker 共用限频窗口，避免同一轮超时同时刷出多条告警。"""
        now = time.monotonic()
        with self._wait_warning_lock:
            if now - self._last_wait_warning < self._client_wait_timeout:
                return
            self._last_wait_warning = now

            if self._analysis_worker_count > capacity:
                logger.warning(
                    "AI client wait timed out after %.0fs: analysis workers=%d, "
                    "schedulable client capacity=%d%s. 建议将 ai_analysis_thread 调整为不大于 %d，"
                    "或增加客户端/分组并发上限。",
                    self._client_wait_timeout,
                    self._analysis_worker_count,
                    capacity,
                    f", target_group={target_group_id!r}" if target_group_id else "",
                    capacity,
                )
            else:
                logger.warning(
                    "AI client wait timed out after %.0fs; all %d schedulable slots are busy%s.",
                    self._client_wait_timeout,
                    capacity,
                    f" in group {target_group_id!r}" if target_group_id else "",
                )

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
