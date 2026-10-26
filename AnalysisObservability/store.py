"""任务级 AI 分析观测存储。

该模块只理解“任务、尝试、终态、调用关联”，不依赖 IIS 领域模型，也不保存
Prompt 或回复正文。调用方可以把 request_id 与任意 Client 调用观测表关联。
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

OUTCOME_SUCCESS = "success"
OUTCOME_RETRY_SCHEDULED = "retry_scheduled"
OUTCOME_SENSITIVE = "sensitive"
OUTCOME_FAILED = "failed"


class AnalysisStatsRecorder:
    """按调用尝试和任务终态追加记录；写入失败永不影响业务主链。"""

    _SCHEMA = (
        "CREATE TABLE IF NOT EXISTS attempts ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " ts TEXT NOT NULL, uuid TEXT, domain_name TEXT, shadow INTEGER DEFAULT 0,"
        " prompt_version INTEGER, ai_service TEXT, ai_model TEXT,"
        " attempt_no INTEGER, latency_ms INTEGER, error_class TEXT,"
        " request_id TEXT, client_name TEXT);"
        "CREATE TABLE IF NOT EXISTS outcomes ("
        " uuid TEXT PRIMARY KEY, ts TEXT NOT NULL, domain_name TEXT, shadow INTEGER DEFAULT 0,"
        " prompt_version INTEGER, ai_service TEXT, ai_model TEXT,"
        " outcome TEXT NOT NULL, attempts_used INTEGER);"
        "CREATE TABLE IF NOT EXISTS outcome_events ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, uuid TEXT,"
        " domain_name TEXT, shadow INTEGER DEFAULT 0, prompt_version INTEGER,"
        " ai_service TEXT, ai_model TEXT, outcome TEXT NOT NULL,"
        " attempts_used INTEGER);"
        "CREATE INDEX IF NOT EXISTS idx_attempts_ts ON attempts(ts);"
        "CREATE INDEX IF NOT EXISTS idx_outcome_events_ts ON outcome_events(ts);"
        "CREATE INDEX IF NOT EXISTS idx_outcome_events_uuid ON outcome_events(uuid);"
    )

    def __init__(self, db_path: str | Path, *, enabled: bool = True):
        self.db_path = Path(db_path)
        self.enabled = enabled
        self._lock = threading.Lock()
        if enabled:
            with self._lock, self._connect() as conn:
                self._initialize(conn)

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 10000")
        return conn

    def _initialize(self, conn: sqlite3.Connection) -> None:
        conn.executescript(self._SCHEMA)
        for table in ("attempts", "outcomes", "outcome_events"):
            table_columns = {
                row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            if "domain_name" not in table_columns:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN domain_name TEXT")
            if "subsystem" in table_columns:
                conn.execute(
                    f"UPDATE {table} SET domain_name = subsystem "
                    "WHERE (domain_name IS NULL OR domain_name = '')")
        columns = {row[1] for row in conn.execute("PRAGMA table_info(attempts)")}
        for name, sql_type in (("request_id", "TEXT"), ("client_name", "TEXT")):
            if name not in columns:
                conn.execute(f"ALTER TABLE attempts ADD COLUMN {name} {sql_type}")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_attempts_request_id ON attempts(request_id)")
        # 旧 outcomes 是覆盖表；首次升级时复制为事件基线，之后新终态双写。
        if conn.execute("SELECT COUNT(*) FROM outcome_events").fetchone()[0] == 0:
            conn.execute(
                "INSERT INTO outcome_events (ts, uuid, domain_name, shadow,"
                " prompt_version, ai_service, ai_model, outcome, attempts_used)"
                " SELECT ts, uuid, domain_name, shadow, prompt_version, ai_service,"
                " ai_model, outcome, attempts_used FROM outcomes"
            )

    def record_attempt(
            self, *, uuid: str, domain_name: str = "",
            subsystem: str | None = None, shadow: bool,
            prompt_version: int | None, ai_service: str, ai_model: str,
            attempt_no: int, latency_ms: int, error_class: str | None,
            request_id: str | None = None, client_name: str | None = None,
    ) -> None:
        if not self.enabled:
            return
        try:
            domain = self._resolve_domain(domain_name, subsystem)
            with self._lock, self._connect() as conn:
                self._initialize(conn)
                conn.execute(
                    "INSERT INTO attempts (ts, uuid, domain_name, shadow,"
                    " prompt_version, ai_service, ai_model, attempt_no, latency_ms,"
                    " error_class, request_id, client_name)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        datetime.now(timezone.utc).isoformat(), uuid, domain,
                        int(shadow), prompt_version, ai_service, ai_model,
                        attempt_no, int(latency_ms), error_class,
                        request_id, client_name,
                    ),
                )
        except Exception:
            logger.warning("analysis stats attempt write failed", exc_info=True)

    def record_outcome(
            self, *, uuid: str, domain_name: str = "",
            subsystem: str | None = None, shadow: bool,
            prompt_version: int | None, ai_service: str, ai_model: str,
            outcome: str, attempts_used: int,
    ) -> None:
        if not self.enabled:
            return
        try:
            domain = self._resolve_domain(domain_name, subsystem)
            now = datetime.now(timezone.utc).isoformat()
            values = (
                uuid, now, domain, int(shadow), prompt_version,
                ai_service, ai_model, outcome, int(attempts_used),
            )
            with self._lock, self._connect() as conn:
                self._initialize(conn)
                conn.execute(
                    "INSERT OR REPLACE INTO outcomes (uuid, ts, domain_name, shadow,"
                    " prompt_version, ai_service, ai_model, outcome, attempts_used)"
                    " VALUES (?,?,?,?,?,?,?,?,?)",
                    values,
                )
                conn.execute(
                    "INSERT INTO outcome_events (uuid, ts, domain_name, shadow,"
                    " prompt_version, ai_service, ai_model, outcome, attempts_used)"
                    " VALUES (?,?,?,?,?,?,?,?,?)",
                    values,
                )
        except Exception:
            logger.warning("analysis stats outcome write failed", exc_info=True)

    def record_terminal_failure(
            self, *, uuid: str, domain_name: str = "",
            subsystem: str | None = None, shadow: bool = False,
    ) -> None:
        """把已耗尽持久重试的任务改为失败，并保留上次模型归因字段。"""
        if not self.enabled:
            return
        try:
            domain = self._resolve_domain(domain_name, subsystem)
            now = datetime.now(timezone.utc).isoformat()
            with self._lock, self._connect() as conn:
                self._initialize(conn)
                previous = conn.execute(
                    "SELECT prompt_version, ai_service, ai_model, attempts_used"
                    " FROM outcomes WHERE uuid = ?", (uuid,),
                ).fetchone()
                values = (
                    uuid, now, domain, int(shadow),
                    previous[0] if previous else None,
                    previous[1] if previous else "",
                    previous[2] if previous else "",
                    OUTCOME_FAILED,
                    previous[3] if previous else 0,
                )
                conn.execute(
                    "INSERT OR REPLACE INTO outcomes (uuid, ts, domain_name, shadow,"
                    " prompt_version, ai_service, ai_model, outcome, attempts_used)"
                    " VALUES (?,?,?,?,?,?,?,?,?)", values)
                conn.execute(
                    "INSERT INTO outcome_events (uuid, ts, domain_name, shadow,"
                    " prompt_version, ai_service, ai_model, outcome, attempts_used)"
                    " VALUES (?,?,?,?,?,?,?,?,?)", values)
        except Exception:
            logger.warning("analysis stats terminal outcome write failed", exc_info=True)

    @staticmethod
    def _resolve_domain(domain_name: str, subsystem: str | None) -> str:
        if domain_name and subsystem and domain_name != subsystem:
            raise ValueError("domain_name and legacy subsystem disagree")
        return str(domain_name or subsystem or "").strip()

    def aggregate_by_model(self, *, days: int = 7) -> list[dict[str, Any]]:
        """兼容原离线报告：按模型与 Prompt 聚合基础结果。"""
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self._lock, self._connect() as conn:
            self._initialize(conn)
            attempts = conn.execute(
                "SELECT ai_model, prompt_version, attempt_no, latency_ms, error_class"
                " FROM attempts WHERE ts >= ?", (since,),
            ).fetchall()
            outcomes = conn.execute(
                "SELECT ai_model, prompt_version, outcome, attempts_used"
                " FROM outcomes WHERE ts >= ?", (since,),
            ).fetchall()
        groups: dict[tuple, dict[str, Any]] = {}
        for model, version, attempt_no, latency, error_class in attempts:
            group = groups.setdefault((model or "", version), _empty_group(model, version))
            group["attempts"] += 1
            if latency is not None:
                group["latency_ms"].append(latency)
            if error_class:
                group["errors"][error_class] = group["errors"].get(error_class, 0) + 1
                if attempt_no == 1:
                    group["first_attempt_errors"] += 1
        for model, version, outcome, attempts_used in outcomes:
            group = groups.setdefault((model or "", version), _empty_group(model, version))
            group["outcomes"][outcome] = group["outcomes"].get(outcome, 0) + 1
            group["attempts_used_total"] += int(attempts_used or 0)
        result = [_finish_group(group) for group in groups.values()]
        result.sort(key=lambda item: (item["ai_model"] or "", item["prompt_version"] or 0))
        return result


def _empty_group(model: str | None, version: int | None) -> dict[str, Any]:
    return {
        "ai_model": model, "prompt_version": version, "attempts": 0,
        "first_attempt_errors": 0, "errors": {}, "latency_ms": [],
        "models_shadow": 0, "outcomes": {}, "attempts_used_total": 0,
    }


def _finish_group(group: dict[str, Any]) -> dict[str, Any]:
    outcomes = group.pop("outcomes")
    total = sum(outcomes.values())
    success = outcomes.get(OUTCOME_SUCCESS, 0)
    latencies = sorted(group.pop("latency_ms"))
    group["outcome_dist"] = outcomes
    group["items"] = total
    group["success_rate"] = round(success / total, 4) if total else None
    group["sensitive_rate"] = (
        round(outcomes.get(OUTCOME_SENSITIVE, 0) / total, 4) if total else None)
    group["first_pass_rate"] = (
        round(1 - group["first_attempt_errors"] / group["attempts"], 4)
        if group["attempts"] else None)
    group["attempts_per_item"] = (
        round(group.pop("attempts_used_total") / total, 2) if total else None)
    group["latency_p50_ms"] = latencies[len(latencies) // 2] if latencies else None
    group["latency_p95_ms"] = (
        latencies[min(int(len(latencies) * 0.95), len(latencies) - 1)]
        if latencies else None)
    group.pop("first_attempt_errors", None)
    return group
