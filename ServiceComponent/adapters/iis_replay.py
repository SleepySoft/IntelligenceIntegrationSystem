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

    def __init__(self, domain_resources: Any, *, poll_interval: float = 30.0,
                 batch_size: int = 100, lease_duration: timedelta = timedelta(hours=6),
                 auto_replay: bool = True, replay_newest_first: bool = True,
                 max_analysis_age_hours: float = 0.0):
        if poll_interval <= 0 or batch_size < 1 or lease_duration.total_seconds() <= 0:
            raise ValueError("Retry poll/batch/lease 参数必须大于 0。")
        self.domain_resources = domain_resources
        self.poll_interval = poll_interval
        self.batch_size = batch_size
        self.lease_duration = lease_duration
        # auto_replay=False 时启动只做遗留迁移与重试轮询，未处理记录等待手工触发
        # （POST /api/control/replay_unarchived）。
        self.auto_replay = auto_replay
        # replay_newest_first=True 时未处理记录按 collect_time 新到旧回放：
        # 冷启动回补时先处理最新情报，旧存货最后消化。
        self.replay_newest_first = replay_newest_first
        # 分析年龄硬上限（小时）：回放/重试时超过该年龄的记录直接标弃用（X）不再分析。
        # 0 = 不启用。
        self.max_analysis_age_hours = max(0.0, float(max_analysis_age_hours))
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._runtime: HubRuntime | None = None
        self._manual_lock = threading.Lock()
        self._manual_running = False
        self.replayed = 0
        self.retried = 0
        self.migrated_legacy_errors = 0

    def register(self, runtime: HubRuntime) -> None:
        return None

    def start(self, runtime: HubRuntime) -> None:
        self._runtime = runtime
        self._thread = threading.Thread(
            target=self._replay, args=(runtime,), name="IISUnarchivedReplay", daemon=True)
        self._thread.start()

    def stop(self, runtime: HubRuntime) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def trigger_replay(self) -> dict:
        """手工触发一次未处理记录回放（运行控制端点调用）；幂等，并发调用只跑一次。"""
        runtime = self._runtime
        if runtime is None:
            return {"started": False, "reason": "extension_not_started"}
        with self._manual_lock:
            if self._manual_running:
                return {"started": False, "reason": "already_running"}
            self._manual_running = True
        before = self.replayed

        def _run():
            try:
                self._replay_unprocessed(runtime)
            finally:
                with self._manual_lock:
                    self._manual_running = False

        threading.Thread(target=_run, name="IISUnarchivedReplayManual", daemon=True).start()
        return {"started": True, "replayed_before": before}

    def _replay(self, runtime: HubRuntime) -> None:
        self._migrate_legacy_errors()
        if self.auto_replay:
            self._replay_unprocessed(runtime)
        self._replay_due_retries(runtime)
        while not self._stop.wait(self.poll_interval):
            self._replay_due_retries(runtime)

    def _migrate_legacy_errors(self) -> None:
        """旧 E 没有重试轮次；一次性迁为立即到期的 R1。"""
        now = datetime.now(timezone.utc)
        for context in self._enabled_domains():
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
                logger.exception("Failed to migrate legacy E records for domain %s", context.name)

    def _deprecate_stale(self, context: Any, records: list) -> set:
        """把超过分析年龄硬上限的记录批量标为弃用（X），返回被弃用的 _id 集合。"""
        if self.max_analysis_age_hours <= 0 or not records:
            return set()
        cutoff = datetime.now(timezone.utc) - timedelta(hours=self.max_analysis_age_hours)
        stale_ids = []
        for record in records:
            collected = record.get("collect_time")
            if isinstance(collected, datetime):
                if collected.tzinfo is None:
                    collected = collected.replace(tzinfo=timezone.utc)
                if collected < cutoff:
                    stale_ids.append(record.get("_id"))
        if not stale_ids:
            return set()
        try:
            result = context.mongo_db_cache.collection.update_many(
                {"_id": {"$in": stale_ids}},
                {"$set": {CACHE_ARCHIVED_FLAG_FIELD: CacheArchiveState.DEPRECATED.value}},
            )
            count = int(getattr(result, "modified_count", len(stale_ids)))
        except Exception:
            logger.exception("Failed to deprecate stale records for domain %s", context.name)
            return set()
        logger.info("Deprecated %d stale records (older than %.1fh) for domain %s",
                    count, self.max_analysis_age_hours, context.name)
        return set(stale_ids)

    def _replay_unprocessed(self, runtime: HubRuntime) -> None:
        query = unarchived_cache_query()
        for context in self._enabled_domains():
            if self._stop.is_set() or not context.mongo_db_cache:
                continue
            try:
                cursor = context.mongo_db_cache.collection.find(query)
                if not isinstance(cursor, list) and self.replay_newest_first:
                    cursor = cursor.sort("collect_time", -1)
                records = list(cursor)
                deprecated = self._deprecate_stale(context, records)
                for record in records:
                    if record.get("_id") in deprecated:
                        continue  # 超旧记录已弃用，不再发射
                    if not self._emit_record(runtime, context, record, EventPriority.REPLAY):
                        return
                    self.replayed += 1
            except Exception:
                logger.exception("Failed to replay unarchived cache for domain %s", context.name)

    def _replay_due_retries(self, runtime: HubRuntime) -> None:
        now = datetime.now(timezone.utc)
        query = retry_cache_query(now)
        lease_until = now + self.lease_duration
        for context in self._enabled_domains():
            if self._stop.is_set() or not context.mongo_db_cache:
                continue
            collection = context.mongo_db_cache.collection
            try:
                cursor = collection.find(query)
                if not isinstance(cursor, list):
                    cursor = cursor.sort(
                        appendix_path(APPENDIX_RETRY_NEXT_AT), 1).limit(self.batch_size)
                records = list(cursor)[:self.batch_size]
                deprecated = self._deprecate_stale(context, records)
                for record in records:
                    if self._stop.is_set():
                        return
                    if record.get("_id") in deprecated:
                        continue  # 超旧记录已弃用，不再重试
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
                logger.exception("Failed to replay retry cache for domain %s", context.name)

    def _enabled_domains(self):
        enabled = getattr(self.domain_resources, "enabled_domains", None)
        if not callable(enabled):
            enabled = self.domain_resources.enabled_subsystems
        return enabled()

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
