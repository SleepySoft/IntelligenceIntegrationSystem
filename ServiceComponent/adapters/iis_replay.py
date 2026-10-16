"""IIS cache 未归档记录的启动回放扩展。"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from ServiceComponent.intelligence_contracts import (
    APPENDIX_RETRY_FIRST_FAILED_AT,
    APPENDIX_RETRY_LAST_ERROR,
    APPENDIX_RETRY_LEASE_UNTIL,
    APPENDIX_RETRY_LEVEL,
    APPENDIX_RETRY_NEXT_AT,
    CACHE_ARCHIVED_FLAG_FIELD,
    CacheArchiveState,
    appendix_path,
    retry_cache_query,
    unarchived_cache_query,
)
from ServiceComponent.intelligence_identity import normalize_or_assign_intelligence_uuid
from ServiceComponent.pipeline import ANALYSIS_REQUESTED
from ServiceComponent.runtime import EventPriority, HubEvent, HubPlugin, HubRuntime


logger = logging.getLogger(__name__)


class IISUnarchivedReplayExtension(HubPlugin):
    """恢复未处理记录，并把到期 R1/R2/R3 投入独立低优先级车道。

    已经存在于 cache 的恢复数据不能再经过 intake 去重；否则它会被自身挡掉。
    因此这个适配器明确从 ``analysis.requested`` 续接，保持旧队列恢复语义。
    """

    def __init__(self, subsystem_registry: Any, *, poll_interval: float = 30.0,
                 batch_size: int = 100, lease_duration: timedelta = timedelta(hours=6)):
        if poll_interval <= 0 or batch_size < 1 or lease_duration.total_seconds() <= 0:
            raise ValueError("Retry poll/batch/lease 参数必须大于 0。")
        self.subsystem_registry = subsystem_registry
        self.poll_interval = poll_interval
        self.batch_size = batch_size
        self.lease_duration = lease_duration
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.replayed = 0
        self.retried = 0
        self.migrated_legacy_errors = 0

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
        self._migrate_legacy_errors()
        self._replay_unprocessed(runtime)
        self._replay_due_retries(runtime)
        while not self._stop.wait(self.poll_interval):
            self._replay_due_retries(runtime)

    def _migrate_legacy_errors(self) -> None:
        """旧 E 没有重试轮次；一次性迁为立即到期的 R1。"""
        now = datetime.now(timezone.utc)
        for context in self.subsystem_registry.enabled_subsystems():
            if self._stop.is_set() or not context.mongo_db_cache:
                continue
            try:
                result = context.mongo_db_cache.collection.update_many(
                    {
                        CACHE_ARCHIVED_FLAG_FIELD: CacheArchiveState.ERROR.value,
                        appendix_path(APPENDIX_RETRY_LEVEL): {"$exists": False},
                    },
                    {"$set": {
                        CACHE_ARCHIVED_FLAG_FIELD: CacheArchiveState.RETRY.value,
                        appendix_path(APPENDIX_RETRY_LEVEL): 1,
                        appendix_path(APPENDIX_RETRY_FIRST_FAILED_AT): now,
                        appendix_path(APPENDIX_RETRY_NEXT_AT): now,
                        appendix_path(APPENDIX_RETRY_LAST_ERROR):
                            "legacy E migrated to persistent retry",
                    }},
                )
                self.migrated_legacy_errors += int(getattr(result, "modified_count", 0))
            except Exception:
                logger.exception("Failed to migrate legacy E records for subsystem %s", context.name)

    def _replay_unprocessed(self, runtime: HubRuntime) -> None:
        query = unarchived_cache_query()
        for context in self.subsystem_registry.enabled_subsystems():
            if self._stop.is_set() or not context.mongo_db_cache:
                continue
            try:
                for record in context.mongo_db_cache.collection.find(query):
                    if not self._emit_record(runtime, context, record, EventPriority.REPLAY):
                        return
                    self.replayed += 1
            except Exception:
                logger.exception("Failed to replay unarchived cache for subsystem %s", context.name)

    def _replay_due_retries(self, runtime: HubRuntime) -> None:
        now = datetime.now(timezone.utc)
        query = retry_cache_query(now)
        lease_until = now + self.lease_duration
        for context in self.subsystem_registry.enabled_subsystems():
            if self._stop.is_set() or not context.mongo_db_cache:
                continue
            collection = context.mongo_db_cache.collection
            try:
                cursor = collection.find(query)
                if not isinstance(cursor, list):
                    cursor = cursor.sort(
                        appendix_path(APPENDIX_RETRY_NEXT_AT), 1).limit(self.batch_size)
                for record in list(cursor)[:self.batch_size]:
                    if self._stop.is_set():
                        return
                    claim_query = {"_id": record.get("_id"), **retry_cache_query(now)}
                    claimed = collection.update_one(
                        claim_query,
                        {"$set": {
                            appendix_path(APPENDIX_RETRY_LEASE_UNTIL): lease_until,
                        }},
                    )
                    if getattr(claimed, "modified_count", 1) != 1:
                        continue
                    if not self._emit_record(runtime, context, record, EventPriority.RETRY):
                        return
                    self.retried += 1
            except Exception:
                logger.exception("Failed to replay retry cache for subsystem %s", context.name)

    @staticmethod
    def _emit_record(runtime: HubRuntime, context: Any, record: dict,
                     priority: EventPriority) -> bool:
        try:
            payload = dict(record)
            stored_id = payload.get("_id")
            original_uuid = str(payload.get("UUID", "")).strip()
            canonical_uuid = str(normalize_or_assign_intelligence_uuid(original_uuid))
            if canonical_uuid != original_uuid:
                context.mongo_db_cache.collection.update_one(
                    {"_id": stored_id}, {"$set": {"UUID": canonical_uuid}})
                payload["UUID"] = canonical_uuid
            payload.pop("_id", None)
            runtime.emit(
                HubEvent(ANALYSIS_REQUESTED, payload, context.name), priority=priority)
            return True
        except RuntimeError:
            return False
