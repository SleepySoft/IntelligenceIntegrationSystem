"""归档后的实体别名自动发现与保守补录扩展。"""

from __future__ import annotations

import logging
import queue
import re
import threading
from typing import Any, Iterable

from EntityAlias.normalize import normalize_name
from ServiceComponent.pipeline import ARCHIVE_COMPLETED
from ServiceComponent.runtime import HubEvent, HubPlugin, HubRuntime


logger = logging.getLogger(__name__)


def _search_language(name: str) -> str:
    return "zh" if re.search(r"[\u3400-\u9fff]", name) else "en"


class EntityAliasAutoEnrichmentExtension(HubPlugin):
    """异步查询 Wikidata，仅自动登记唯一的精确同名候选。

    模糊候选和同名多义候选必须继续由人工/AI 工作清单确认，避免把同名不同实体
    错误合并。登记成功后，EntityResolution 会立即用全局实体的固定 ``type``
    投影历史及后续图数据，从而消除同一实体因 LLM 类型漂移产生的重复分类。
    """

    def __init__(self, service: Any, *, queue_size: int = 1000,
                 archive_collections: Iterable[Any] = (),
                 backfill_limit: int = 1000):
        if queue_size < 1:
            raise ValueError("queue_size 必须大于 0")
        if backfill_limit < 0:
            raise ValueError("backfill_limit 不能小于 0")
        self.service = service
        self.archive_collections = tuple(archive_collections)
        self.backfill_limit = backfill_limit
        self._pending: queue.Queue[tuple[str, str]] = queue.Queue(maxsize=queue_size)
        self._queued_names: set[str] = set()
        self._checked_names: set[str] = set()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        self._backfiller: threading.Thread | None = None
        self._stats = {
            "queued": 0,
            "registered": 0,
            "ambiguous": 0,
            "not_found": 0,
            "failed": 0,
            "dropped": 0,
        }

    def register(self, runtime: HubRuntime) -> None:
        runtime.subscribe(ARCHIVE_COMPLETED, self._on_archived)

    def start(self, runtime: HubRuntime) -> None:
        if self.service.suggester is None:
            logger.info("EntityAlias 在线建议源未配置，自动补录保持关闭。")
            return
        self._worker = threading.Thread(
            target=self._run, name="EntityAliasAutoEnrichment", daemon=True)
        self._worker.start()
        if self.archive_collections and self.backfill_limit:
            self._backfiller = threading.Thread(
                target=self._scan_archives,
                name="EntityAliasAutoEnrichmentBackfill", daemon=True)
            self._backfiller.start()

    def stop(self, runtime: HubRuntime) -> None:
        self._stop.set()
        for thread in (self._backfiller, self._worker):
            if thread and thread.is_alive():
                thread.join(timeout=2.0)

    @property
    def statistics(self) -> dict[str, int]:
        with self._lock:
            result = dict(self._stats)
        result["pending"] = self._pending.qsize()
        return result

    def wait_for_idle(self, timeout: float | None = None) -> bool:
        done = threading.Event()

        def wait() -> None:
            self._pending.join()
            done.set()

        threading.Thread(target=wait, daemon=True).start()
        return done.wait(timeout)

    def _on_archived(self, event: HubEvent, runtime: HubRuntime) -> None:
        del runtime
        if not isinstance(event.payload, dict):
            return
        extraction = event.payload.get("EVENT_EXTRACTION")
        entities = extraction.get("entities") if isinstance(extraction, dict) else None
        if not isinstance(entities, list):
            return
        for entity in entities:
            if not isinstance(entity, dict):
                continue
            name = str(entity.get("name") or "").strip()
            entity_type = str(entity.get("type") or "other").strip().lower() or "other"
            self._enqueue(name, entity_type)

    def _enqueue(self, name: str, entity_type: str) -> bool:
        key = normalize_name(name)
        if not key or self.service.resolve(name) is not None:
            return False
        with self._lock:
            if key in self._queued_names or key in self._checked_names:
                return False
            self._queued_names.add(key)
        try:
            self._pending.put_nowait((name, entity_type))
            with self._lock:
                self._stats["queued"] += 1
            return True
        except queue.Full:
            with self._lock:
                self._queued_names.discard(key)
                self._stats["dropped"] += 1
            logger.warning("EntityAlias 自动补录队列已满，跳过实体 %r。", name)
            return False

    def _scan_archives(self) -> None:
        """启动时限量扫描存量归档；失败不影响实时归档消费。"""
        queued = 0
        try:
            for collection in self.archive_collections:
                cursor = collection.find(
                    {}, {"_id": 0, "EVENT_EXTRACTION.entities.name": 1,
                         "EVENT_EXTRACTION.entities.type": 1})
                for document in cursor:
                    if self._stop.is_set() or queued >= self.backfill_limit:
                        return
                    extraction = document.get("EVENT_EXTRACTION") or {}
                    for entity in extraction.get("entities") or ():
                        if queued >= self.backfill_limit:
                            return
                        if not isinstance(entity, dict):
                            continue
                        name = str(entity.get("name") or "").strip()
                        entity_type = str(
                            entity.get("type") or "other").strip().lower() or "other"
                        if self._enqueue(name, entity_type):
                            queued += 1
        except Exception:
            logger.warning("EntityAlias 存量归档自动补录扫描失败。", exc_info=True)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                name, entity_type = self._pending.get(timeout=0.2)
            except queue.Empty:
                continue
            key = normalize_name(name)
            try:
                outcome = self.service.auto_register_exact(
                    name, entity_type=entity_type,
                    search_lang=_search_language(name),
                    primary_lang="zh", limit=50)
                status = str(outcome.get("status") or "failed")
                with self._lock:
                    if status == "registered":
                        self._stats["registered"] += 1
                    elif status == "ambiguous":
                        self._stats["ambiguous"] += 1
                    elif status == "not_found":
                        self._stats["not_found"] += 1
                    elif status != "existing":
                        self._stats["failed"] += 1
                    if status in {"registered", "ambiguous", "not_found", "existing"}:
                        self._checked_names.add(key)
            except Exception:
                with self._lock:
                    self._stats["failed"] += 1
                logger.warning("EntityAlias 自动补录失败：%r", name, exc_info=True)
            finally:
                with self._lock:
                    self._queued_names.discard(key)
                self._pending.task_done()
