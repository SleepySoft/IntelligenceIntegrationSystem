"""IIS 应用门面：组合运行时、领域流程和查询端口。"""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from ServiceComponent.pipeline import (
    ANALYSIS_FAILED,
    ARCHIVE_COMPLETED,
    ARCHIVE_FAILED,
    ARCHIVE_REQUESTED,
    EventPipelinePlugin,
    INTAKE_ACCEPTED,
    INTAKE_RECEIVED,
    INTAKE_REJECTED,
)
from ServiceComponent.runtime import HubEvent, HubPlugin, HubRuntime


class HubApplication:
    """主工程访问 Hub 的门面，不构造业务基础设施。

    调用方在组合根创建 registry、AI manager、扩展和端口。门面只将它们安装到
    runtime，并提供 Web adapter 所需的查询/提交端口。
    """

    def __init__(
            self,
            *,
            subsystem_registry: Any,
            ai_client_manager: Any,
            worker_count: int = 1,
            pipeline_ports: Any = None,
            runtime: Optional[HubRuntime] = None,
            extensions: Iterable[HubPlugin] = (),
            vector_search: Optional[Callable[..., List[Tuple[str, float, dict]]]] = None,
            services: Optional[Dict[str, Any]] = None,
            max_inflight: int = 2000,
            submission_ack_timeout: float = 30.0,
    ):
        if max_inflight < 1:
            raise ValueError("max_inflight 必须大于 0。")
        if submission_ack_timeout <= 0:
            raise ValueError("submission_ack_timeout 必须大于 0。")
        self.subsystem_registry = subsystem_registry
        self.ai_client_manager = ai_client_manager
        self.default_subsystem = subsystem_registry.default()
        if self.default_subsystem is None:
            raise ValueError("subsystem_registry 必须提供默认子系统。")
        self.default_subsystem_name = self.default_subsystem.name
        self.runtime = runtime or HubRuntime(worker_count=worker_count)
        if pipeline_ports is None:
            raise ValueError("pipeline_ports 必须由组合根提供。")
        self._pipeline_ports = pipeline_ports
        self.runtime.install(EventPipelinePlugin(pipeline_ports, pipeline_ports, pipeline_ports))

        # 提交端需要知道 intake/archive 是否真正接收，避免把校验失败或重复数据
        # 回报为 queued。并发额度覆盖完整流水线，用于恢复旧队列的入口背压。
        self._submission_ack_timeout = submission_ack_timeout
        self._inflight_slots = threading.BoundedSemaphore(max_inflight)
        self._submission_lock = threading.RLock()
        self._started = threading.Event()
        self._submission_waiters: Dict[str, Tuple[threading.Event, Dict[str, Any]]] = {}
        self._active_submissions = set()
        self.runtime.subscribe(INTAKE_ACCEPTED, self._on_submission_accepted)
        self.runtime.subscribe(INTAKE_REJECTED, self._on_submission_rejected)
        self.runtime.subscribe(ARCHIVE_COMPLETED, self._on_submission_accepted)
        self.runtime.subscribe(ARCHIVE_FAILED, self._on_submission_rejected)
        for terminal_event in (INTAKE_REJECTED, ANALYSIS_FAILED, ARCHIVE_FAILED, ARCHIVE_COMPLETED):
            self.runtime.subscribe(terminal_event, self._on_submission_terminal)
        for extension in extensions:
            self.runtime.install(extension)

        self._vector_search = vector_search
        self.services = dict(services or {})

    def startup(self) -> None:
        self.runtime.start()
        self._started.set()

    def shutdown(self, timeout: float = 10.0) -> None:
        self._started.clear()
        self.runtime.stop(timeout=timeout, drain=True)

    @property
    def statistics(self) -> Dict[str, Any]:
        """运行时通用统计；领域/运维扩展可在 services 中提供额外统计。"""
        with self._submission_lock:
            in_flight = len(self._active_submissions)
        return {
            "runtime": {**self.runtime.stats, "in_flight_submissions": in_flight},
            "subsystems": self.subsystem_registry.describe(),
        }

    def submit_collected_data(self, data: dict) -> bool:
        subsystem = str(data.get("subsystem") or "").strip() or self.default_subsystem_name
        if self.subsystem_registry.resolve(subsystem) is None:
            return False
        return self._submit_and_wait(
            HubEvent(INTAKE_RECEIVED, dict(data), subsystem),
            accepted_event=INTAKE_ACCEPTED,
        )

    def submit_archived_data(self, data: dict) -> bool:
        subsystem = str(data.get("subsystem") or "").strip()
        subsystem = subsystem or self.default_subsystem_name
        if self.subsystem_registry.resolve(subsystem) is None:
            return False
        return self._submit_and_wait(
            HubEvent(ARCHIVE_REQUESTED, dict(data), subsystem),
            accepted_event=ARCHIVE_COMPLETED,
        )

    def get_intelligence(self, intelligence_uuid, light_weight: bool = False,
                         subsystem: Optional[str] = None):
        ctx = self._context(subsystem)
        return ctx.event_v4_query_engine.get_intelligence(
            intelligence_uuid, light_weight=light_weight)

    def query_intelligence(self, *, subsystem: Optional[str] = None, **kwargs):
        ctx = self._context(subsystem)
        return ctx.event_v4_query_engine.query_intelligence(**kwargs)

    def get_intelligence_summary(self, subsystem: Optional[str] = None):
        summary = self._context(subsystem).event_v4_query_engine.get_intelligence_summary()
        return summary["total_count"], summary["base_uuid"]

    def aggregate(self, pipeline: list, subsystem: Optional[str] = None):
        return self._context(subsystem).event_v4_query_engine.aggregate(pipeline)

    def count_documents(self, query: dict, subsystem: Optional[str] = None) -> int:
        return self._context(subsystem).event_v4_query_engine.count_documents(query)

    def get_statistics_engine(self, subsystem: Optional[str] = None):
        return self._context(subsystem).statistics_engine

    def get_query_engine(self, subsystem: Optional[str] = None):
        return self._context(subsystem).event_v4_query_engine

    def get_subsystems(self) -> List[Dict[str, Any]]:
        return self.subsystem_registry.describe()

    def get_prompt(self, subsystem: Optional[str] = None, version: Optional[int] = None) -> str:
        self._context(subsystem)
        from prompts_event_v4 import EVENT_ANALYSIS_PROMPT_TABLE
        if version is not None:
            return EVENT_ANALYSIS_PROMPT_TABLE.get(
                int(version), f"[Prompt v{version}] Not configured.")
        return EVENT_ANALYSIS_PROMPT_TABLE[max(EVENT_ANALYSIS_PROMPT_TABLE)]

    def submit_intelligence_manual_rating(self, intelligence_uuid: str, rating: dict,
                                          subsystem: Optional[str] = None) -> bool:
        if not isinstance(rating, dict):
            return False
        self._context(subsystem).event_v4_intelligence_collection.update_one(
            {"_id": intelligence_uuid}, {"$set": {"manual_rating": rating}})
        return True

    def vector_search_intelligence(self, **kwargs) -> List[Tuple[str, float, dict]]:
        return self._vector_search(**kwargs) if self._vector_search else []

    def get_service(self, name: str, default: Any = None) -> Any:
        return self.services.get(name, default)

    def _submit_and_wait(self, event: HubEvent, *, accepted_event: str) -> bool:
        if not self._started.is_set():
            return False
        if not self._inflight_slots.acquire(timeout=self._submission_ack_timeout):
            return False
        waiter = threading.Event()
        outcome: Dict[str, Any] = {}
        with self._submission_lock:
            self._submission_waiters[event.event_id] = (waiter, outcome)
            self._active_submissions.add(event.event_id)
        try:
            self.runtime.emit(event)
        except Exception:
            with self._submission_lock:
                self._submission_waiters.pop(event.event_id, None)
                self._active_submissions.discard(event.event_id)
            self._inflight_slots.release()
            return False

        acknowledged = waiter.wait(timeout=self._submission_ack_timeout)
        with self._submission_lock:
            self._submission_waiters.pop(event.event_id, None)
        return acknowledged and outcome.get("event_type") == accepted_event

    def _on_submission_accepted(self, event: HubEvent, runtime: HubRuntime) -> None:
        self._acknowledge_submission(event, True)

    def _on_submission_rejected(self, event: HubEvent, runtime: HubRuntime) -> None:
        self._acknowledge_submission(event, False)

    def _acknowledge_submission(self, event: HubEvent, accepted: bool) -> None:
        correlation_id = event.correlation_id
        if not correlation_id:
            return
        with self._submission_lock:
            entry = self._submission_waiters.get(correlation_id)
            if entry is None:
                return
            waiter, outcome = entry
            if waiter.is_set():
                return
            outcome["accepted"] = accepted
            outcome["event_type"] = event.event_type
            waiter.set()

    def _on_submission_terminal(self, event: HubEvent, runtime: HubRuntime) -> None:
        correlation_id = event.correlation_id
        if not correlation_id:
            return
        with self._submission_lock:
            if correlation_id not in self._active_submissions:
                return
            self._active_submissions.remove(correlation_id)
        self._inflight_slots.release()

    def _context(self, subsystem: Optional[str]) -> Any:
        ctx = self.subsystem_registry.resolve(subsystem) or self.default_subsystem
        return ctx
