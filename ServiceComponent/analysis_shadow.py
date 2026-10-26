"""影子分析记录器：并行链路（prompt+后处理）的对比样本落盘。

影子分析用于保守灰度：生产归档仍由主链（抽样表中的 prompt 版本）产生，
影子版本对同一输入并行分析，结果只写入本目录、绝不进入归档与索引，
供离线对比两条链路的抽取质量。写入失败绝不影响分析主链。

每个样本写一个 JSON 文件，同时维护 SQLite 索引（index.sqlite）：
source_uuid → 影子记录列表，供 UI/API 快速回答“这条情报有没有影子数据”。
索引可从目录文件幂等回填（启动时调用 rebuild_index_if_stale）。
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4


logger = logging.getLogger(__name__)


class ShadowAnalysisRecorder:
    """将影子分析结果写入本地 JSON 文件（每个样本一个文件，超量淘汰最旧）。"""

    def __init__(self, directory: str | Path, *, enabled: bool = True,
                 max_files: int = 1000):
        if max_files < 1:
            raise ValueError("max_files 必须大于 0。")
        self.directory = Path(directory)
        self.enabled = enabled
        self.max_files = max_files
        self._lock = threading.Lock()
        self._index_path = self.directory / "index.sqlite"

    def record(self, *, original_data: Mapping[str, Any], domain_name: str = "",
               subsystem: str | None = None,
               primary_prompt_version: int, shadow_prompt_version: int,
               analysis: Any, ai_service: str, ai_model: str,
               extraction_quality: Any = None) -> Path | None:
        if not self.enabled:
            return None
        try:
            now = datetime.now(timezone.utc)
            source_uuid = str(original_data.get("UUID", "")).strip()
            if domain_name and subsystem and domain_name != subsystem:
                raise ValueError("domain_name and legacy subsystem disagree")
            resolved_domain = domain_name or subsystem or ""
            document = {
                "schema_version": 1,
                "captured_at": now.isoformat(),
                "source_uuid": source_uuid,
                "domain_name": str(resolved_domain),
                "primary_prompt_version": int(primary_prompt_version),
                "shadow_prompt_version": int(shadow_prompt_version),
                "ai_service": str(ai_service),
                "ai_model": str(ai_model),
                "input": {
                    "title": original_data.get("title"),
                    "informant": original_data.get("informant"),
                    "source": original_data.get("source"),
                },
                "analysis": analysis,
                "extraction_quality": extraction_quality,
            }
            with self._lock:
                self.directory.mkdir(parents=True, exist_ok=True)
                filename = (
                    f"{now.strftime('%Y%m%dT%H%M%S.%fZ')}_"
                    f"{self._safe_name(source_uuid)}_v{shadow_prompt_version}_"
                    f"{uuid4().hex[:8]}.json"
                )
                target = self.directory / filename
                temporary = target.with_suffix(".tmp")
                temporary.write_text(
                    json.dumps(document, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8",
                )
                os.replace(temporary, target)
                self._index_upsert_locked(filename, document)
                self._trim_locked()
                return target
        except Exception:
            logger.exception("Failed to persist shadow analysis record.")
            return None

    # ---------------- 索引（UI/API 查询层） ----------------

    _INDEX_SCHEMA = (
        "CREATE TABLE IF NOT EXISTS shadow_records ("
        " file_name TEXT PRIMARY KEY,"
        " source_uuid TEXT NOT NULL,"
        " shadow_prompt_version INTEGER NOT NULL,"
        " primary_prompt_version INTEGER,"
        " ai_service TEXT,"
        " ai_model TEXT,"
        " captured_at TEXT,"
        " quality_json TEXT)"
    )

    def _index_connect(self) -> sqlite3.Connection:
        self.directory.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._index_path, timeout=10)
        conn.execute(self._INDEX_SCHEMA)
        return conn

    def _index_upsert_locked(self, file_name: str, document: Mapping[str, Any]) -> None:
        """recorder 写文件成功后同步索引；失败只记日志，不影响落盘。"""
        try:
            quality = document.get("extraction_quality")
            quality_json = (
                json.dumps({
                    "unregistered_role_count": quality.get("unregistered_role_count", 0),
                    "role_type_conflict_count": quality.get("role_type_conflict_count", 0),
                    "predicate_grounding": (quality.get("surface_grounding") or {})
                                               .get("predicate"),
                }, ensure_ascii=False)
                if isinstance(quality, dict) else None
            )
            with self._index_connect() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO shadow_records VALUES (?,?,?,?,?,?,?,?)",
                    (file_name,
                     str(document.get("source_uuid") or ""),
                     int(document.get("shadow_prompt_version") or 0),
                     document.get("primary_prompt_version"),
                     str(document.get("ai_service") or ""),
                     str(document.get("ai_model") or ""),
                     str(document.get("captured_at") or ""),
                     quality_json),
                )
        except Exception:
            logger.warning("shadow index upsert failed", exc_info=True)

    def list_by_uuid(self, source_uuid: str) -> list[dict[str, Any]]:
        """按情报 UUID 列出影子记录（按时间新到旧）；索引不可用时回退目录扫描。"""
        source_uuid = str(source_uuid or "").strip()
        if not source_uuid:
            return []
        try:
            with self._index_connect() as conn:
                rows = conn.execute(
                    "SELECT file_name, source_uuid, shadow_prompt_version,"
                    " primary_prompt_version, ai_service, ai_model, captured_at, quality_json"
                    " FROM shadow_records WHERE source_uuid = ?"
                    " ORDER BY captured_at DESC",
                    (source_uuid,),
                ).fetchall()
            return [
                {
                    "file_name": row[0], "source_uuid": row[1],
                    "shadow_prompt_version": row[2], "primary_prompt_version": row[3],
                    "ai_service": row[4], "ai_model": row[5], "captured_at": row[6],
                    "quality": json.loads(row[7]) if row[7] else None,
                }
                for row in rows
            ]
        except Exception:
            logger.warning("shadow index query failed; falling back to directory scan",
                           exc_info=True)
            return self._scan_by_uuid(source_uuid)

    def _scan_by_uuid(self, source_uuid: str) -> list[dict[str, Any]]:
        results = []
        if not self.directory.is_dir():
            return results
        for file in sorted(self.directory.glob("*.json"),
                           key=lambda item: item.stat().st_mtime, reverse=True):
            try:
                document = json.loads(file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if str(document.get("source_uuid") or "") != source_uuid:
                continue
            results.append({
                "file_name": file.name,
                "source_uuid": source_uuid,
                "shadow_prompt_version": document.get("shadow_prompt_version"),
                "primary_prompt_version": document.get("primary_prompt_version"),
                "ai_service": document.get("ai_service"),
                "ai_model": document.get("ai_model"),
                "captured_at": document.get("captured_at"),
                "quality": None,
            })
        return results

    def read_record(self, file_name: str) -> dict[str, Any] | None:
        """读取单个影子记录全文；只允许索引中登记的文件名，防目录穿越。"""
        safe = Path(str(file_name or "")).name
        if not safe.endswith(".json"):
            return None
        target = self.directory / safe
        if not target.is_file():
            return None
        try:
            return json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def rebuild_index_if_stale(self) -> int:
        """启动时调用：把目录中未入索引的记录补进索引（幂等），返回补录条数。"""
        if not self.directory.is_dir():
            return 0
        added = 0
        with self._lock:
            try:
                with self._index_connect() as conn:
                    known = {row[0] for row in conn.execute(
                        "SELECT file_name FROM shadow_records")}
                for file in sorted(self.directory.glob("*.json")):
                    if file.name in known:
                        continue
                    try:
                        document = json.loads(file.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        continue
                    self._index_upsert_locked(file.name, document)
                    added += 1
            except Exception:
                logger.warning("shadow index rebuild failed", exc_info=True)
        return added

    @staticmethod
    def _safe_name(value: str) -> str:
        safe = "".join(char if char.isalnum() or char in "-_" else "_" for char in value)
        return safe[:80] or "unknown"

    def _trim_locked(self) -> None:
        files = sorted(self.directory.glob("*.json"), key=lambda item: item.stat().st_mtime)
        stale = files[:-self.max_files]
        for file in stale:
            try:
                file.unlink()
            except OSError:
                logger.warning("Failed to remove stale shadow analysis record: %s", file)
        if stale:
            try:
                with self._index_connect() as conn:
                    conn.executemany(
                        "DELETE FROM shadow_records WHERE file_name = ?",
                        [(f.name,) for f in stale])
            except Exception:
                logger.warning("shadow index trim failed", exc_info=True)
