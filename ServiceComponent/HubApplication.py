"""IIS 应用门面：组合运行时、领域流程和查询端口。"""

from __future__ import annotations

import threading
from datetime import datetime
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
from ServiceComponent.intelligence_contracts import (
    APPENDIX_MANUAL_RATING,
    APPENDIX_RETRY_LEVEL,
    APPENDIX_TIME_ARCHIVED,
    CACHE_ARCHIVED_FLAG_FIELD,
    CACHE_LEGACY_ARCHIVED_FLAG_FIELD,
    CacheArchiveState,
    EventV4CollectionSuffix,
    appendix_path,
)


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
        self.runtime = runtime or HubRuntime(
            worker_count=1,
            worker_groups={
                "analysis": worker_count,
                "postprocess": 1,
            },
        )
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
            "analysis": dict(getattr(self._pipeline_ports, "statistics", {}) or {}),
            "shadow": dict(getattr(self._pipeline_ports, "shadow_statistics", {}) or {}),
            "subsystems": self.subsystem_registry.describe(),
            "workflow_outcomes": self._workflow_outcomes(),
            "today_flow": self._today_flow(),
        }

    def _today_flow(self) -> Dict[str, Any]:
        """当日流量漏斗（本地日界）：提交 → 入口拒绝 / 归档 / 低价值。

        事件计数（emitted/processed）是一条情报生命周期内多个阶段事件的总和，
        不能直接反映提交量；该块给出可与人理解对齐的按天口径。
        """
        day_start = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
        flow: Dict[str, Any] = {
            "day_start": day_start.isoformat(),
            "intake": 0, "intake_rejected": 0, "archived": 0, "low_value": 0,
        }
        enabled = getattr(self.subsystem_registry, "enabled_subsystems", None)
        if not callable(enabled):
            return flow
        for ctx in enabled():
            try:
                cache = getattr(ctx, "mongo_db_cache", None)
                if cache is None:
                    continue
                flow["intake"] += int(cache.collection.count_documents(
                    {"collect_time": {"$gte": day_start}}))
                archived_coll = getattr(ctx, "event_v4_intelligence_collection", None)
                if archived_coll is not None:
                    flow["archived"] += int(archived_coll.count_documents(
                        {appendix_path(APPENDIX_TIME_ARCHIVED): {"$gte": day_start}}))
                low_coll = getattr(ctx, "event_v4_low_value_collection", None)
                if low_coll is not None:
                    flow["low_value"] += int(low_coll.count_documents(
                        {appendix_path(APPENDIX_TIME_ARCHIVED): {"$gte": day_start}}))
                try:
                    rejected_coll = cache.db[
                        ctx.collection_name(EventV4CollectionSuffix.INTAKE_REJECTED)]
                    flow["intake_rejected"] += int(rejected_coll.count_documents(
                        {"rejected_at": {"$gte": day_start}}))
                except Exception:
                    pass  # 审计集合未创建时按 0 计
            except Exception:
                continue  # 单个子系统统计失败不影响整体
        return flow

    def _workflow_outcomes(self) -> List[Dict[str, Any]]:
        """从持久集合汇总整个工作流结果，不受进程重启影响。"""
        enabled_subsystems = getattr(self.subsystem_registry, "enabled_subsystems", None)
        if not callable(enabled_subsystems):
            return []

        outcomes = []
        for ctx in enabled_subsystems():
            try:
                cache_collection = ctx.mongo_db_cache.collection
                state_rows = cache_collection.aggregate([{
                    "$group": {
                        "_id": {
                            "state": {"$ifNull": [
                                f"${CACHE_ARCHIVED_FLAG_FIELD}",
                                {"$ifNull": [
                                    f"${CACHE_LEGACY_ARCHIVED_FLAG_FIELD}", "__PENDING__"
                                ]},
                            ]},
                            "retry_level": {"$ifNull": [
                                f"${appendix_path(APPENDIX_RETRY_LEVEL)}", 0]},
                        },
                        "count": {"$sum": 1},
                    }
                }])
                states = {}
                retry_levels = {1: 0, 2: 0, 3: 0}
                for item in state_rows:
                    identity = item.get("_id")
                    if isinstance(identity, dict):
                        state = str(identity.get("state"))
                        retry_level = int(identity.get("retry_level") or 0)
                    else:  # 兼容测试桩和迁移期间的旧聚合结果。
                        state = str(identity)
                        retry_level = 0
                    count = int(item.get("count", 0))
                    states[state] = states.get(state, 0) + count
                    if state == CacheArchiveState.RETRY.value and retry_level in retry_levels:
                        retry_levels[retry_level] += count
                outcomes.append({
                    "name": ctx.name,
                    "display_name": ctx.display_name or ctx.name,
                    "received": int(cache_collection.count_documents({})),
                    "pending": states.get("__PENDING__", 0),
                    "archived": int(ctx.event_v4_intelligence_collection.count_documents({})),
                    "low_value": int(ctx.event_v4_low_value_collection.count_documents({})),
                    "failed": states.get(CacheArchiveState.ERROR.value, 0),
                    "sensitive": states.get(CacheArchiveState.SENSITIVE.value, 0),
                    "duplicated": states.get(CacheArchiveState.DUPLICATED.value, 0),
                    "retry": states.get(CacheArchiveState.RETRY.value, 0),
                    "retry_r1": retry_levels[1],
                    "retry_r2": retry_levels[2],
                    "retry_r3": retry_levels[3],
                    "deprecated": states.get(CacheArchiveState.DEPRECATED.value, 0),
                })
            except Exception as exc:
                outcomes.append({
                    "name": getattr(ctx, "name", "unknown"),
                    "display_name": getattr(ctx, "display_name", None)
                                    or getattr(ctx, "name", "unknown"),
                    "error": f"{type(exc).__name__}: {exc}",
                })
        return outcomes

    def submit_collected_data(self, data: dict) -> bool:
        accepted, _reason = self.submit_collected_data_result(data)
        return accepted

    def submit_collected_data_result(self, data: dict) -> Tuple[bool, str]:
        """提交并等待 intake 回执；返回 (是否接收, 拒绝原因)。接受时原因为空串。"""
        subsystem = str(data.get("subsystem") or "").strip() or self.default_subsystem_name
        if self.subsystem_registry.resolve(subsystem) is None:
            return False, "unknown_subsystem"
        return self._submit_and_wait_result(
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
        from prompts_event_v4 import EVENT_V4_PROMPT_CATALOG
        if version is not None:
            try:
                return EVENT_V4_PROMPT_CATALOG.text_for(int(version))
            except ValueError:
                return f"[Prompt v{version}] Not configured."
        return EVENT_V4_PROMPT_CATALOG.sampling_table()[
            max(EVENT_V4_PROMPT_CATALOG.sampling_versions())]

    def submit_intelligence_manual_rating(self, intelligence_uuid: str, rating: dict,
                                          subsystem: Optional[str] = None) -> bool:
        if not isinstance(rating, dict):
            return False
        self._context(subsystem).event_v4_intelligence_collection.update_one(
            {"UUID": intelligence_uuid},
            {"$set": {appendix_path(APPENDIX_MANUAL_RATING): rating}})
        return True

    @property
    def vector_search_available(self) -> bool:
        """向量搜索是否已配置（门面已注入）。供 Web 层控制相似情报入口显隐。"""
        return self._vector_search is not None

    def vector_search_intelligence(self, **kwargs) -> List[Tuple[str, float, dict]]:
        return self._vector_search(**kwargs) if self._vector_search else []

    def get_service(self, name: str, default: Any = None) -> Any:
        return self.services.get(name, default)

    def _submit_and_wait(self, event: HubEvent, *, accepted_event: str) -> bool:
        accepted, _reason = self._submit_and_wait_result(event, accepted_event=accepted_event)
        return accepted

    def _submit_and_wait_result(self, event: HubEvent, *, accepted_event: str) -> Tuple[bool, str]:
        if not self._started.is_set():
            return False, "hub_not_started"
        if not self._inflight_slots.acquire(timeout=self._submission_ack_timeout):
            return False, "inflight_limit"
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
            return False, "emit_failed"

        acknowledged = waiter.wait(timeout=self._submission_ack_timeout)
        with self._submission_lock:
            self._submission_waiters.pop(event.event_id, None)
        if not acknowledged:
            return False, "ack_timeout"
        if outcome.get("event_type") == accepted_event:
            return True, ""
        return False, str(outcome.get("reason") or "rejected")

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
            # 拒绝事件 payload 是 PipelineStageFailure；把 reason 透传给提交方。
            outcome["reason"] = str(getattr(event.payload, "reason", "") or "")
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
