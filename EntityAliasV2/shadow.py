"""EntityAlias V2 的独立影子观察与保守候选增长。

影子扩展只订阅正式归档结果，不修改权威抽取、V1、EntityResolution、事件索引或图。V2
事实库、观察库、队列和 Wikidata 缓存均使用独立资源。
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import queue
import re
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable

from ServiceComponent.pipeline import ARCHIVE_COMPLETED
from ServiceComponent.runtime import HubEvent, HubPlugin, HubRuntime

from .model import AliasV2Error, ResolutionResult
from .normalize import normalize_name
from .service import EntityAliasV2Service
from .types import normalize_entity_type


logger = logging.getLogger(__name__)


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _search_language(name: str) -> str:
    return "zh" if re.search(r"[\u3400-\u9fff]", name) else "en"


class EntityAliasV2ShadowStore:
    """V2 影子观测和外部补录 ledger；与事实库物理分离。"""

    def __init__(self, path: str):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            self.path, check_same_thread=False, isolation_level=None)
        self._connection.row_factory = sqlite3.Row
        with self._lock:
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.execute("PRAGMA synchronous = NORMAL")
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS observations (
                    domain_name TEXT NOT NULL,
                    source_uuid TEXT NOT NULL,
                    local_entity_id TEXT NOT NULL,
                    surface TEXT NOT NULL,
                    normalized_surface TEXT NOT NULL,
                    mention_type TEXT NOT NULL,
                    canonical_type TEXT,
                    v1_found INTEGER NOT NULL,
                    v1_entity_id TEXT,
                    v1_type TEXT,
                    v2_status TEXT NOT NULL,
                    v2_candidate_ids TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(domain_name, source_uuid, local_entity_id)
                );
                CREATE INDEX IF NOT EXISTS observations_typed_surface
                    ON observations(normalized_surface, canonical_type);
                CREATE INDEX IF NOT EXISTS observations_status
                    ON observations(v2_status, updated_at);

                CREATE TABLE IF NOT EXISTS enrichment_ledger (
                    normalized_surface TEXT NOT NULL,
                    canonical_type TEXT NOT NULL,
                    surface TEXT NOT NULL,
                    status TEXT NOT NULL,
                    qid TEXT,
                    detail TEXT NOT NULL,
                    attempts INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(normalized_surface, canonical_type)
                );
                """
            )

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def record(
        self,
        *,
        domain_name: str,
        source_uuid: str,
        local_entity_id: str,
        surface: str,
        mention_type: str,
        v1_hit: dict[str, Any] | None,
        v2_result: ResolutionResult,
    ) -> None:
        now = _utc_now()
        candidate_ids = [item.entity_id for item in (
            *v2_result.candidates, *v2_result.other_type_candidates)]
        with self._lock:
            self._connection.execute(
                """INSERT INTO observations(
                       domain_name, source_uuid, local_entity_id, surface,
                       normalized_surface, mention_type, canonical_type,
                       v1_found, v1_entity_id, v1_type, v2_status,
                       v2_candidate_ids, reason, observed_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(domain_name, source_uuid, local_entity_id) DO UPDATE SET
                       surface = excluded.surface,
                       normalized_surface = excluded.normalized_surface,
                       mention_type = excluded.mention_type,
                       canonical_type = excluded.canonical_type,
                       v1_found = excluded.v1_found,
                       v1_entity_id = excluded.v1_entity_id,
                       v1_type = excluded.v1_type,
                       v2_status = excluded.v2_status,
                       v2_candidate_ids = excluded.v2_candidate_ids,
                       reason = excluded.reason,
                       updated_at = excluded.updated_at""",
                (
                    domain_name, source_uuid, local_entity_id, surface,
                    normalize_name(surface), mention_type,
                    v2_result.canonical_type,
                    int(v1_hit is not None),
                    (v1_hit or {}).get("entity_id"),
                    (v1_hit or {}).get("type"),
                    v2_result.status,
                    json.dumps(candidate_ids, ensure_ascii=False),
                    v2_result.reason,
                    now, now,
                ),
            )

    def should_enrich(self, surface: str, canonical_type: str) -> bool:
        """终态结果跨重启去重；只有 failed 可在下一次启动重试。"""

        key = normalize_name(surface)
        with self._lock:
            row = self._connection.execute(
                """SELECT status FROM enrichment_ledger
                   WHERE normalized_surface = ? AND canonical_type = ?""",
                (key, canonical_type),
            ).fetchone()
        return row is None or row["status"] == "failed"

    def record_enrichment(
        self,
        surface: str,
        canonical_type: str,
        *,
        status: str,
        qid: str = "",
        detail: str = "",
    ) -> None:
        key = normalize_name(surface)
        with self._lock:
            self._connection.execute(
                """INSERT INTO enrichment_ledger(
                       normalized_surface, canonical_type, surface, status,
                       qid, detail, attempts, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, 1, ?)
                   ON CONFLICT(normalized_surface, canonical_type) DO UPDATE SET
                       surface = excluded.surface,
                       status = excluded.status,
                       qid = excluded.qid,
                       detail = excluded.detail,
                       attempts = enrichment_ledger.attempts + 1,
                       updated_at = excluded.updated_at""",
                (key, canonical_type, surface, status, qid or None, detail, _utc_now()),
            )

    def refresh_resolution(
        self,
        surface: str,
        canonical_type: str,
        service: EntityAliasV2Service,
    ) -> ResolutionResult:
        """候选库变化后刷新同一类型化名称的全部历史影子结果。"""

        result = service.resolve(surface, mention_type=canonical_type)
        candidate_ids = [item.entity_id for item in (
            *result.candidates, *result.other_type_candidates)]
        with self._lock:
            self._connection.execute(
                """UPDATE observations
                   SET v2_status = ?, v2_candidate_ids = ?, reason = ?, updated_at = ?
                   WHERE normalized_surface = ? AND canonical_type = ?""",
                (
                    result.status,
                    json.dumps(candidate_ids, ensure_ascii=False),
                    result.reason,
                    _utc_now(),
                    normalize_name(surface),
                    canonical_type,
                ),
            )
        return result

    def statistics(self) -> dict[str, Any]:
        with self._lock:
            observation_rows = self._connection.execute(
                """SELECT v2_status, COUNT(*) AS count FROM observations
                   GROUP BY v2_status""").fetchall()
            enrichment_rows = self._connection.execute(
                """SELECT status, COUNT(*) AS count FROM enrichment_ledger
                   GROUP BY status""").fetchall()
            total = self._connection.execute(
                "SELECT COUNT(*) FROM observations").fetchone()[0]
        return {
            "observations": int(total),
            "resolution_statuses": {
                str(row["v2_status"]): int(row["count"])
                for row in observation_rows
            },
            "enrichment_statuses": {
                str(row["status"]): int(row["count"])
                for row in enrichment_rows
            },
        }


class EntityAliasV2ShadowExtension(HubPlugin):
    """归档后影子评估；V2 结果绝不进入正式下游。"""

    def __init__(
        self,
        service: EntityAliasV2Service,
        shadow_store: EntityAliasV2ShadowStore,
        *,
        v1_service: Any = None,
        suggester: Any = None,
        queue_size: int = 1000,
        archive_collections: Iterable[Any] = (),
        backfill_limit: int = 0,
    ):
        if queue_size < 1:
            raise ValueError("queue_size 必须大于 0")
        if backfill_limit < 0:
            raise ValueError("backfill_limit 不能小于 0")
        self.service = service
        self.shadow_store = shadow_store
        self.v1_service = v1_service
        self.suggester = suggester
        self.archive_collections = tuple(archive_collections)
        self.backfill_limit = backfill_limit
        self._pending: queue.Queue[tuple[str, str]] = queue.Queue(queue_size)
        self._queued: set[tuple[str, str]] = set()
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        self._backfiller: threading.Thread | None = None
        self._stats = {
            "queued": 0, "registered": 0, "existing": 0,
            "ambiguous": 0, "not_found": 0, "type_conflict": 0,
            "failed": 0, "dropped": 0,
        }

    def register(self, runtime: HubRuntime) -> None:
        runtime.subscribe(ARCHIVE_COMPLETED, self._on_archived)

    def start(self, runtime: HubRuntime) -> None:
        del runtime
        if self.suggester is not None:
            self._worker = threading.Thread(
                target=self._run, name="EntityAliasV2ShadowEnrichment", daemon=True)
            self._worker.start()
        if self.archive_collections and self.backfill_limit:
            self._backfiller = threading.Thread(
                target=self._scan_archives,
                name="EntityAliasV2ShadowBackfill", daemon=True)
            self._backfiller.start()

    def stop(self, runtime: HubRuntime) -> None:
        del runtime
        self._stop.set()
        for thread in (self._backfiller, self._worker):
            if thread and thread.is_alive():
                thread.join(timeout=2.0)

    @property
    def statistics(self) -> dict[str, Any]:
        with self._lock:
            result = dict(self._stats)
        result["pending"] = self._pending.qsize()
        result.update(self.shadow_store.statistics())
        result["v2"] = self.service.status()
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
        if isinstance(event.payload, dict):
            self.observe_archive(event.payload)

    def observe_archive(self, document: dict[str, Any], *, enqueue: bool = True) -> int:
        extraction = document.get("EVENT_EXTRACTION") or {}
        entities = extraction.get("entities") or ()
        source_uuid = str(document.get("UUID") or "").strip()
        appendix = document.get("APPENDIX") or {}
        domain_name = str(appendix.get("__DOMAIN_NAME__") or "").strip() or "unknown"
        observed = 0
        for entity in entities:
            if not isinstance(entity, dict):
                continue
            surface = str(entity.get("name") or "").strip()
            mention_type = str(entity.get("type") or "").strip()
            local_entity_id = str(entity.get("id") or "").strip()
            if not surface or not mention_type or not local_entity_id or not source_uuid:
                continue
            v1_hit = None
            if self.v1_service is not None:
                try:
                    v1_hit = self.v1_service.resolve(surface)
                except Exception:
                    logger.warning("V2 shadow 查询 V1 失败：%r", surface, exc_info=True)
            result = self.service.resolve(surface, mention_type=mention_type)
            self.shadow_store.record(
                domain_name=domain_name,
                source_uuid=source_uuid,
                local_entity_id=local_entity_id,
                surface=surface,
                mention_type=mention_type,
                v1_hit=v1_hit,
                v2_result=result,
            )
            observed += 1
            if enqueue and result.status in {"not_found", "type_conflict"}:
                self._enqueue(surface, mention_type)
        return observed

    def _enqueue(self, surface: str, mention_type: str) -> bool:
        canonical_type = normalize_entity_type(mention_type)
        key = (normalize_name(surface), canonical_type or "")
        if not key[0] or not canonical_type or self.suggester is None:
            return False
        if not self.shadow_store.should_enrich(surface, canonical_type):
            return False
        with self._lock:
            if key in self._queued:
                return False
            self._queued.add(key)
        try:
            self._pending.put_nowait((surface, canonical_type))
            with self._lock:
                self._stats["queued"] += 1
            return True
        except queue.Full:
            with self._lock:
                self._queued.discard(key)
                self._stats["dropped"] += 1
            return False

    def _scan_archives(self) -> None:
        queued = 0
        try:
            for collection in self.archive_collections:
                cursor = collection.find(
                    {}, {"_id": 0, "UUID": 1, "APPENDIX.__DOMAIN_NAME__": 1,
                         "EVENT_EXTRACTION.entities": 1}).sort("_id", -1)
                for document in cursor:
                    if self._stop.is_set() or queued >= self.backfill_limit:
                        return
                    before = self._stats["queued"]
                    self.observe_archive(document)
                    queued += self._stats["queued"] - before
                    if queued >= self.backfill_limit:
                        return
        except Exception:
            logger.warning("EntityAliasV2 shadow 存量扫描失败。", exc_info=True)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                surface, canonical_type = self._pending.get(timeout=0.2)
            except queue.Empty:
                continue
            key = (normalize_name(surface), canonical_type)
            status, qid, detail = "failed", "", ""
            try:
                status, qid, detail = self._enrich(surface, canonical_type)
            except Exception as exc:
                detail = str(exc)
                logger.warning(
                    "EntityAliasV2 shadow 补录失败：%r/%s",
                    surface, canonical_type, exc_info=True)
            finally:
                self.shadow_store.record_enrichment(
                    surface, canonical_type, status=status, qid=qid, detail=detail)
                self.shadow_store.refresh_resolution(
                    surface, canonical_type, self.service)
                with self._lock:
                    self._queued.discard(key)
                    self._stats[status if status in self._stats else "failed"] += 1
                self._pending.task_done()

    def _enrich(self, surface: str, canonical_type: str) -> tuple[str, str, str]:
        candidates = self.suggester.suggest(
            surface, lang=_search_language(surface), limit=50)
        key = normalize_name(surface)
        exact = {
            str(item.get("qid") or "").strip(): item
            for item in candidates
            if str(item.get("qid") or "").strip()
            and any(normalize_name(item.get(field, "")) == key
                    for field in ("matched", "label"))
        }
        if not exact:
            return "not_found", "", "没有唯一精确同名 Wikidata 候选"
        if len(exact) != 1:
            return "ambiguous", "", f"精确候选数：{len(exact)}"
        qid = next(iter(exact))
        existing = self.service.store.find_by_ref("wikidata", qid)
        if existing is not None:
            if existing.canonical_type != canonical_type:
                return (
                    "type_conflict", qid,
                    f"已登记类型 {existing.canonical_type}，本次为 {canonical_type}",
                )
            if all(item.normalized_name != key for item in existing.names):
                self.service.add_alias(
                    existing.id, surface,
                    lang=_search_language(surface),
                    resolution="context_required",
                )
            return "existing", qid, existing.id

        prefill = self.suggester.prefill(qid, primary_lang="zh")
        if not prefill:
            return "failed", qid, "Wikidata prefill 失败"
        aliases = []
        seen = {normalize_name(prefill.get("canonical", ""))}
        localized = {}
        for lang, value in (prefill.get("localized") or {}).items():
            localized_key = normalize_name(value)
            if localized_key and localized_key not in seen:
                localized[str(lang)] = value
                seen.add(localized_key)
        for item in prefill.get("aliases") or ():
            item_key = normalize_name(item.get("name", ""))
            if item_key and item_key not in seen:
                aliases.append({
                    "name": item.get("name"),
                    "lang": item.get("lang", ""),
                    "resolution": "context_required",
                })
                seen.add(item_key)
        if key not in seen:
            aliases.append({
                "name": surface,
                "lang": _search_language(surface),
                "resolution": "context_required",
            })
        created = self.service.create_entity(
            prefill["canonical"],
            canonical_type=canonical_type,
            canonical_lang=prefill.get("lang", ""),
            localized=localized,
            aliases=aliases,
            status="unverified",
            description=prefill.get("description", ""),
            refs={"wikidata": qid},
        )
        return "registered", qid, created["id"]
