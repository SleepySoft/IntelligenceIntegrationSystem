"""IIS 侧 AI 分析效果统计（持久化，供离线评估与出报告）。

与 AIClientCenter 的观测库分工：ClientCenter 管客户端健康/额度；本模块以
**情报分析任务**为单位评估"哪个模型 + 哪个 prompt 版本"的实际产出质量
（成功率、首过率、敏感率、错误分类分布、延迟），跨重启持久保留。

写库失败绝不阻塞分析主链。
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 终态
OUTCOME_SUCCESS = "success"
OUTCOME_RETRY_SCHEDULED = "retry_scheduled"   # 进入 R1-R3
OUTCOME_SENSITIVE = "sensitive"
OUTCOME_FAILED = "failed"                     # 终态失败


class AnalysisStatsRecorder:
    """按次（attempt）与按件（uuid 终态）记录分析统计。"""

    _SCHEMA = (
        "CREATE TABLE IF NOT EXISTS attempts ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " ts TEXT NOT NULL, uuid TEXT, subsystem TEXT, shadow INTEGER DEFAULT 0,"
        " prompt_version INTEGER, ai_service TEXT, ai_model TEXT,"
        " attempt_no INTEGER, latency_ms INTEGER, error_class TEXT);"
        "CREATE TABLE IF NOT EXISTS outcomes ("
        " uuid TEXT PRIMARY KEY, ts TEXT NOT NULL, subsystem TEXT, shadow INTEGER DEFAULT 0,"
        " prompt_version INTEGER, ai_service TEXT, ai_model TEXT,"
        " outcome TEXT NOT NULL, attempts_used INTEGER);"
    )

    def __init__(self, db_path: str | Path, *, enabled: bool = True):
        self.db_path = Path(db_path)
        self.enabled = enabled
        self._lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.executescript(self._SCHEMA)
        return conn

    def record_attempt(self, *, uuid: str, subsystem: str, shadow: bool,
                       prompt_version: int | None, ai_service: str, ai_model: str,
                       attempt_no: int, latency_ms: int,
                       error_class: str | None) -> None:
        """每次 AI 调用结束记录一行；error_class=None 表示该次成功。"""
        if not self.enabled:
            return
        try:
            with self._lock, self._connect() as conn:
                conn.execute(
                    "INSERT INTO attempts (ts, uuid, subsystem, shadow,"
                    " prompt_version, ai_service, ai_model, attempt_no,"
                    " latency_ms, error_class) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (datetime.now(timezone.utc).isoformat(), uuid, subsystem,
                     int(shadow), prompt_version, ai_service, ai_model,
                     attempt_no, int(latency_ms), error_class),
                )
        except Exception:
            logger.warning("analysis stats attempt write failed", exc_info=True)

    def record_outcome(self, *, uuid: str, subsystem: str, shadow: bool,
                       prompt_version: int | None, ai_service: str, ai_model: str,
                       outcome: str, attempts_used: int) -> None:
        """一件情报的终态（成功/敏感/进重试/终态失败）。"""
        if not self.enabled:
            return
        try:
            with self._lock, self._connect() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO outcomes (uuid, ts, subsystem, shadow,"
                    " prompt_version, ai_service, ai_model, outcome, attempts_used)"
                    " VALUES (?,?,?,?,?,?,?,?,?)",
                    (uuid, datetime.now(timezone.utc).isoformat(), subsystem,
                     int(shadow), prompt_version, ai_service, ai_model,
                     outcome, int(attempts_used)),
                )
        except Exception:
            logger.warning("analysis stats outcome write failed", exc_info=True)

    # ---------------- 聚合（离线报告用） ----------------

    def aggregate_by_model(self, *, days: int = 7) -> list[dict[str, Any]]:
        """按 (模型, prompt版本) 聚合近 N 天：成功率/首过率/敏感率/错误分布/延迟。"""
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self._lock, self._connect() as conn:
            attempt_rows = conn.execute(
                "SELECT ai_model, prompt_version, attempt_no, latency_ms, error_class"
                " FROM attempts WHERE ts >= ?", (since,)).fetchall()
            outcome_rows = conn.execute(
                "SELECT ai_model, prompt_version, outcome, attempts_used"
                " FROM outcomes WHERE ts >= ?", (since,)).fetchall()

        groups: dict[tuple, dict[str, Any]] = {}
        for model, version, attempt_no, latency, error_class in attempt_rows:
            key = (model or "", version)
            g = groups.setdefault(key, {
                "ai_model": model, "prompt_version": version,
                "attempts": 0, "first_attempt_errors": 0, "errors": {},
                "latency_ms": [], "models_shadow": 0,
            })
            g["attempts"] += 1
            if latency is not None:
                g["latency_ms"].append(latency)
            if error_class:
                g["errors"][error_class] = g["errors"].get(error_class, 0) + 1
                if attempt_no == 1:
                    g["first_attempt_errors"] += 1

        out = []
        for model, version, outcome, attempts_used in outcome_rows:
            key = (model or "", version)
            g = groups.setdefault(key, {
                "ai_model": model, "prompt_version": version,
                "attempts": 0, "first_attempt_errors": 0, "errors": {},
                "latency_ms": [], "models_shadow": 0,
            })
            g.setdefault("outcomes", {}).setdefault(outcome, 0)
            g["outcomes"][outcome] += 1
            g.setdefault("attempts_used_total", 0)
            g["attempts_used_total"] += int(attempts_used or 0)

        for g in groups.values():
            outcomes = g.pop("outcomes", {})
            total = sum(outcomes.values())
            success = outcomes.get(OUTCOME_SUCCESS, 0)
            lat = sorted(g.pop("latency_ms"))
            g["outcome_dist"] = outcomes
            g["items"] = total
            g["success_rate"] = round(success / total, 4) if total else None
            g["sensitive_rate"] = (
                round(outcomes.get(OUTCOME_SENSITIVE, 0) / total, 4) if total else None)
            g["first_pass_rate"] = (
                round(1 - g["first_attempt_errors"] / g["attempts"], 4)
                if g["attempts"] else None)
            g["attempts_per_item"] = (
                round(g.pop("attempts_used_total", 0) / total, 2) if total else None)
            g["latency_p50_ms"] = lat[len(lat) // 2] if lat else None
            g["latency_p95_ms"] = lat[int(len(lat) * 0.95)] if lat else None
            g.pop("first_attempt_errors", None)
            out.append(g)
        out.sort(key=lambda x: (x["ai_model"], x["prompt_version"] or 0))
        return out
