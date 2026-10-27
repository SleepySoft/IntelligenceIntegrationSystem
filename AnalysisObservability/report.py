"""跨任务统计与通用 AI 调用事实表生成服务质量报告。"""

from __future__ import annotations

import sqlite3
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .store import (
    AnalysisStatsRecorder,
    OUTCOME_FAILED,
    OUTCOME_RETRY_SCHEDULED,
    OUTCOME_SENSITIVE,
    OUTCOME_SUCCESS,
)

RESULT_ERRORS = {"json_syntax", "schema_shape", "semantic_rule"}
SERVICE_ERRORS = {"network", "rate_limit", "provider_fatal", "unknown"}
DIMENSIONS = {
    "model", "client", "service", "domain", "prompt", "model_prompt",
    "service_model_prompt",
}


class AnalysisReportService:
    """只读聚合两个 SQLite 文件；不依赖 Flask、IIS 或 AIClientCenter 代码。"""

    def __init__(self, analysis_db_path: str | Path, call_db_path: str | Path):
        self.analysis_db_path = Path(analysis_db_path)
        self.call_db_path = Path(call_db_path)
        AnalysisStatsRecorder(self.analysis_db_path)

    def report(
        self, *, hours: int = 24, dimension: str = "service_model_prompt",
        include_shadow: bool = False,
    ) -> dict[str, Any]:
        if not 1 <= int(hours) <= 24 * 90:
            raise ValueError("hours 必须在 1..2160 范围内")
        if dimension not in DIMENSIONS:
            raise ValueError(f"不支持的维度: {dimension}")
        now = time.time()
        since = now - int(hours) * 3600
        attempts = self._load_attempts(since, include_shadow)
        request_ids = {row["request_id"] for row in attempts if row["request_id"]}
        calls = self._load_calls(request_ids)
        for row in attempts:
            row["call"] = calls.get(row["request_id"])
        outcomes = self._load_latest_outcomes(since, include_shadow)

        overview = _summarize(attempts, outcomes)
        overview["sample_level"] = _sample_level(overview["started_items"])
        overview["window_hours"] = int(hours)
        overview["linked_calls"] = len(calls)
        overview["link_coverage"] = _ratio(len(calls), len(attempts))

        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in attempts:
            grouped[_dimension_value(row, dimension)].append(row)
        final_dimension = {
            uuid: _dimension_value(max(rows, key=lambda row: row["ts"]), dimension)
            for uuid, rows in _group_by_uuid(attempts).items()
        }
        comparison = []
        for name, rows in grouped.items():
            uuids = {row["uuid"] for row in rows}
            item_outcomes = {
                key: value for key, value in outcomes.items()
                if key in uuids and final_dimension.get(key) == name
            }
            entry = _summarize(rows, item_outcomes)
            entry["name"] = name
            entry["sample_level"] = _sample_level(entry["started_items"])
            comparison.append(entry)
        comparison.sort(key=lambda row: (-row["started_items"], row["name"]))

        counts = Counter(row["error_class"] for row in attempts if row["error_class"])
        affected: dict[str, set[str]] = defaultdict(set)
        for row in attempts:
            if row["error_class"]:
                affected[row["error_class"]].add(row["uuid"])
        errors = [{
            "error_class": error_class,
            "kind": _error_kind(error_class),
            "calls": count,
            "affected_items": len(affected[error_class]),
            "call_rate": _ratio(count, len(attempts)),
        } for error_class, count in sorted(
            counts.items(), key=lambda item: (_error_kind(item[0]), -item[1], item[0]))]

        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "window": {"hours": int(hours), "since_epoch": since, "until_epoch": now},
            "dimension": dimension,
            "include_shadow": bool(include_shadow),
            "overview": overview,
            "comparison": comparison,
            "errors": errors,
            "series": _build_series(attempts, outcomes, hours=int(hours)),
            "recent_attempts": [_public_attempt(row) for row in attempts[-50:][::-1]],
        }

    def _load_attempts(self, since: float, include_shadow: bool) -> list[dict[str, Any]]:
        since_iso = datetime.fromtimestamp(since, timezone.utc).isoformat()
        query = "SELECT * FROM attempts WHERE ts >= ?"
        params: list[Any] = [since_iso]
        if not include_shadow:
            query += " AND shadow = 0"
        query += " ORDER BY ts, id"
        with sqlite3.connect(self.analysis_db_path) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(row) for row in conn.execute(query, params)]

    def _load_latest_outcomes(
        self, since: float, include_shadow: bool,
    ) -> dict[str, dict[str, Any]]:
        since_iso = datetime.fromtimestamp(since, timezone.utc).isoformat()
        query = "SELECT * FROM outcome_events WHERE ts >= ?"
        params: list[Any] = [since_iso]
        if not include_shadow:
            query += " AND shadow = 0"
        query += " ORDER BY ts, id"
        latest: dict[str, dict[str, Any]] = {}
        with sqlite3.connect(self.analysis_db_path) as conn:
            conn.row_factory = sqlite3.Row
            for row in conn.execute(query, params):
                latest[str(row["uuid"] or "")] = dict(row)
        return latest

    def _load_calls(self, request_ids: set[str]) -> dict[str, dict[str, Any]]:
        if not request_ids or not self.call_db_path.exists():
            return {}
        result: dict[str, dict[str, Any]] = {}
        try:
            with sqlite3.connect(self.call_db_path) as conn:
                conn.row_factory = sqlite3.Row
                for chunk in _chunks(sorted(request_ids), 500):
                    marks = ",".join("?" for _ in chunk)
                    query = f"SELECT * FROM ai_calls WHERE request_id IN ({marks})"
                    for row in conn.execute(query, chunk):
                        if not row["is_health_check"]:
                            result[row["request_id"]] = dict(row)
        except (sqlite3.Error, OSError):
            return {}
        return result


def _summarize(
    attempts: list[dict[str, Any]], outcomes: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    by_item: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in attempts:
        by_item[row["uuid"]].append(row)
    terminal = [row for row in outcomes.values() if row["outcome"] != OUTCOME_RETRY_SCHEDULED]
    successes = sum(row["outcome"] == OUTCOME_SUCCESS for row in terminal)
    sensitive = sum(row["outcome"] == OUTCOME_SENSITIVE for row in terminal)
    failed = sum(row["outcome"] == OUTCOME_FAILED for row in terminal)
    pending_retry = sum(row["outcome"] == OUTCOME_RETRY_SCHEDULED for row in outcomes.values())

    first_pass = 0
    recovered = 0
    for uuid, rows in by_item.items():
        outcome = outcomes.get(uuid, {}).get("outcome")
        first = min(rows, key=lambda row: (row.get("attempt_no") or 0, row["ts"]))
        if outcome == OUTCOME_SUCCESS and not first.get("error_class"):
            first_pass += 1
        elif outcome == OUTCOME_SUCCESS:
            recovered += 1

    result_error_items = {
        row["uuid"] for row in attempts if row.get("error_class") in RESULT_ERRORS}
    service_error_items = {
        row["uuid"] for row in attempts if row.get("error_class") in SERVICE_ERRORS}
    latencies = sorted(_latency(row) for row in attempts if _latency(row) is not None)
    input_tokens = sum(_input_tokens(row) for row in attempts)
    output_tokens = sum(_output_tokens(row) for row in attempts)
    cached_tokens = sum(_call_int(row, "cached_input_tokens") for row in attempts)
    uncached_tokens = sum(_call_int(row, "uncached_input_tokens") for row in attempts)
    reasoning_tokens = sum(_call_int(row, "reasoning_output_tokens") for row in attempts)
    linked_calls = sum(bool(row.get("call")) for row in attempts)
    cache_observed = [row for row in attempts if _cache_observed(row)]
    cold_starts = sum(_cache_status(row) == "cold" for row in cache_observed)
    cache_hits = sum(_cache_status(row) == "hit" for row in cache_observed)
    cache_observed_input = sum(_input_tokens(row) for row in cache_observed)
    reported_calls = sum(
        bool(row.get("call") and row["call"].get("token_source") == "reported")
        for row in attempts)
    busy_ms = sum(float(_latency(row) or 0) for row in attempts)
    busy_hours = busy_ms / 3_600_000
    retry_tokens = sum(
        _input_tokens(row) + _output_tokens(row)
        for row in attempts if int(row.get("attempt_no") or 1) > 1)
    total_tokens = input_tokens + output_tokens
    started = len(by_item)
    completed = len(terminal)
    return {
        "started_items": started, "completed_items": completed,
        "success_items": successes, "sensitive_items": sensitive,
        "failed_items": failed, "pending_retry_items": pending_retry,
        "first_pass_items": first_pass, "recovered_items": recovered,
        "first_pass_rate": _ratio(first_pass, started),
        "final_success_rate": _ratio(successes, completed),
        "sensitive_rate": _ratio(sensitive, completed),
        "result_error_items": len(result_error_items),
        "result_error_rate": _ratio(len(result_error_items), started),
        "service_error_items": len(service_error_items),
        "service_error_rate": _ratio(len(service_error_items), started),
        "calls": len(attempts),
        "attempts_per_success": _ratio(len(attempts), successes),
        "completions_per_100_calls": _ratio(successes * 100, len(attempts)),
        "busy_hours": round(busy_hours, 4),
        "busy_productivity": _ratio(successes, busy_hours),
        "latency_p50_ms": _percentile(latencies, 0.50),
        "latency_p95_ms": _percentile(latencies, 0.95),
        "input_tokens": input_tokens, "output_tokens": output_tokens,
        "total_tokens": total_tokens, "cached_input_tokens": cached_tokens,
        "uncached_input_tokens": uncached_tokens,
        "reasoning_output_tokens": reasoning_tokens,
        "cache_observed_calls": len(cache_observed),
        "cache_unreported_calls": linked_calls - len(cache_observed),
        "cache_observation_coverage": _ratio(len(cache_observed), linked_calls),
        "cold_start_calls": cold_starts,
        "cold_start_rate": _ratio(cold_starts, len(cache_observed)),
        "cache_hit_calls": cache_hits,
        "cache_hit_call_rate": _ratio(cache_hits, len(cache_observed)),
        # 只在供应商明确返回缓存字段的调用中计算，避免把“未报告”误当未命中。
        "cache_token_rate": _ratio(cached_tokens, cache_observed_input),
        "average_cached_tokens_per_hit": _ratio(cached_tokens, cache_hits),
        "tokens_per_success": _ratio(total_tokens, successes),
        "retry_token_rate": _ratio(retry_tokens, total_tokens),
        "reported_token_coverage": _ratio(reported_calls, len(attempts)),
    }


def _dimension_value(row: dict[str, Any], dimension: str) -> str:
    call = row.get("call") or {}
    model = str(call.get("model") or row.get("ai_model") or "未标记")
    if dimension == "model":
        return model
    if dimension == "client":
        return str(call.get("client_name") or row.get("client_name") or "未关联")
    if dimension == "service":
        return str(call.get("provider") or row.get("ai_service") or "未标记")
    if dimension == "domain":
        return str(row.get("domain_name") or "未标记")
    prompt = f"v{row.get('prompt_version')}" if row.get("prompt_version") is not None else "未标记"
    if dimension == "prompt":
        return prompt
    if dimension == "model_prompt":
        return f"{model} · {prompt}"
    service = str(call.get("provider") or row.get("ai_service") or "未标记")
    domain = str(row.get("domain_name") or "未标记")
    return f"{service} · {model} · {domain} · {prompt}"


def _build_series(
    attempts: list[dict[str, Any]], outcomes: dict[str, dict[str, Any]], *, hours: int,
) -> list[dict[str, Any]]:
    daily = hours > 72
    buckets: dict[str, dict[str, Any]] = {}
    for row in attempts:
        dt = _parse_iso(row["ts"])
        key = dt.strftime("%Y-%m-%d" if daily else "%m-%d %H:00")
        bucket = buckets.setdefault(key, {
            "bucket": key, "calls": 0, "success_items": 0, "tokens": 0,
            "cache_observed_calls": 0, "cold_start_calls": 0,
            "cache_hit_calls": 0,
        })
        bucket["calls"] += 1
        bucket["tokens"] += _input_tokens(row) + _output_tokens(row)
        cache_status = _cache_status(row)
        if cache_status != "unreported":
            bucket["cache_observed_calls"] += 1
            bucket[f"cache_{cache_status}_calls" if cache_status == "hit"
                   else "cold_start_calls"] += 1
    for row in outcomes.values():
        if row["outcome"] != OUTCOME_SUCCESS:
            continue
        dt = _parse_iso(row["ts"])
        key = dt.strftime("%Y-%m-%d" if daily else "%m-%d %H:00")
        bucket = buckets.setdefault(key, {
            "bucket": key, "calls": 0, "success_items": 0, "tokens": 0,
            "cache_observed_calls": 0, "cold_start_calls": 0,
            "cache_hit_calls": 0,
        })
        bucket["success_items"] += 1
    return [buckets[key] for key in sorted(buckets)]


def _public_attempt(row: dict[str, Any]) -> dict[str, Any]:
    call = row.get("call") or {}
    return {
        "ts": row["ts"], "uuid": row["uuid"],
        "domain_name": row["domain_name"],
        "prompt_version": row["prompt_version"], "attempt_no": row["attempt_no"],
        "model": call.get("model") or row.get("ai_model"),
        "client_name": call.get("client_name") or row.get("client_name"),
        "success": not bool(row.get("error_class")),
        "error_class": row.get("error_class"), "latency_ms": _latency(row),
        "tokens": _input_tokens(row) + _output_tokens(row), "linked": bool(call),
        "cached_input_tokens": call.get("cached_input_tokens"),
        "uncached_input_tokens": call.get("uncached_input_tokens"),
        "cache_status": _cache_status(row),
    }


def _input_tokens(row: dict[str, Any]) -> int:
    call = row.get("call") or {}
    value = call.get("reported_input_tokens")
    return int(value if value is not None else call.get("estimated_input_tokens") or 0)


def _output_tokens(row: dict[str, Any]) -> int:
    call = row.get("call") or {}
    value = call.get("reported_output_tokens")
    return int(value if value is not None else call.get("estimated_output_tokens") or 0)


def _call_int(row: dict[str, Any], field: str) -> int:
    return int((row.get("call") or {}).get(field) or 0)


def _cache_observed(row: dict[str, Any]) -> bool:
    """只有供应商明确返回缓存字段时，才进入冷启动分母。"""
    call = row.get("call") or {}
    return call.get("cached_input_tokens") is not None


def _cache_status(row: dict[str, Any]) -> str:
    if not _cache_observed(row):
        return "unreported"
    return "hit" if _call_int(row, "cached_input_tokens") > 0 else "cold"


def _latency(row: dict[str, Any]) -> float | None:
    value = (row.get("call") or {}).get("elapsed_ms")
    if value is None:
        value = row.get("latency_ms")
    return None if value is None else float(value)


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    index = max(0, min(len(values) - 1, int((len(values) - 1) * fraction + 0.5)))
    return round(values[index], 1)


def _ratio(numerator: float, denominator: float) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _sample_level(count: int) -> str:
    return "observe" if count < 20 else "preliminary" if count < 50 else "stable"


def _error_kind(error_class: str) -> str:
    if error_class in RESULT_ERRORS:
        return "result"
    return "safety" if error_class == "content_safety" else "service"


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _chunks(values: list[str], size: int) -> Iterable[list[str]]:
    for index in range(0, len(values), size):
        yield values[index:index + size]


def _group_by_uuid(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        result[row["uuid"]].append(row)
    return result
