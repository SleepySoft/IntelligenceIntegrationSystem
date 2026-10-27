"""使用生产 Event V4 Prompt 和校验契约执行独立 Client 基准。"""

from __future__ import annotations

import hashlib
import json
import math
import time
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import json_repair

from AIClientCenter.core.manager import BaseAIClient
from ServiceComponent.analysis_messages import (
    build_analyze_message,
    extract_pure_json_text,
    extract_pure_response,
)
from ServiceComponent.IntelligenceHubDefines_v4 import (
    CollectedData,
    validate_analysis_result_v4,
)


@dataclass(frozen=True)
class BenchmarkCase:
    case_id: str
    tags: tuple[str, ...]
    collected_data: dict[str, Any]
    expected: dict[str, Any]


def load_benchmark_suite(path: Path) -> tuple[str, list[BenchmarkCase]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("cases"), list):
        raise ValueError("基准文件必须是包含 cases 数组的 JSON 对象")
    suite_name = str(payload.get("name") or path.stem).strip()
    cases: list[BenchmarkCase] = []
    seen_ids = set()
    for index, raw_case in enumerate(payload["cases"], start=1):
        if not isinstance(raw_case, dict):
            raise ValueError(f"cases[{index}] 必须是对象")
        case_id = str(raw_case.get("id") or "").strip()
        if not case_id or case_id in seen_ids:
            raise ValueError(f"cases[{index}] 的 id 为空或重复: {case_id!r}")
        seen_ids.add(case_id)
        collected = CollectedData.model_validate(
            raw_case.get("collected_data") or {}
        ).model_dump(mode="json")
        expected = raw_case.get("expected") or {}
        if not isinstance(expected, dict):
            raise ValueError(f"cases[{index}].expected 必须是对象")
        route = expected.get("route")
        if route not in (None, "valuable", "non_intelligence"):
            raise ValueError(f"cases[{index}].expected.route 无效: {route!r}")
        cases.append(BenchmarkCase(
            case_id=case_id,
            tags=tuple(str(item) for item in raw_case.get("tags", [])),
            collected_data=collected,
            expected=deepcopy(expected),
        ))
    if not cases:
        raise ValueError("基准文件至少需要一个 case")
    return suite_name, cases


class AIClientBenchmarkRunner:
    """顺序执行真实请求，并保存不含凭据的逐次记录与聚合指标。"""

    def __init__(self, clients: Mapping[str, BaseAIClient], prompt_table: Mapping[int, str],
                 *, raw_output_limit: int = 50_000):
        self.clients = dict(clients)
        self.prompt_table = dict(prompt_table)
        self.raw_output_limit = max(0, int(raw_output_limit))
        if not self.clients:
            raise ValueError("没有选中可测试的 Client")
        if not self.prompt_table:
            raise ValueError("Prompt 表不能为空")

    def run(self, *, suite_name: str, cases: Iterable[BenchmarkCase], repeat: int,
            prompt_version: int, output_dir: Path) -> dict[str, Any]:
        if repeat < 1:
            raise ValueError("repeat 必须大于 0")
        if prompt_version not in self.prompt_table:
            raise ValueError(f"Prompt v{prompt_version} 未配置")
        cases = list(cases)
        output_dir.mkdir(parents=True, exist_ok=False)
        results_path = output_dir / "results.jsonl"
        prompt = self.prompt_table[prompt_version]
        prompt_sha256 = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        started_at = datetime.now(timezone.utc)
        records: list[dict[str, Any]] = []
        with results_path.open("w", encoding="utf-8") as stream:
            for client_key, client in self.clients.items():
                for case in cases:
                    for repetition in range(1, repeat + 1):
                        record = self._run_one(
                            client_key=client_key,
                            client=client,
                            case=case,
                            repetition=repetition,
                            prompt_version=prompt_version,
                            prompt=prompt,
                            prompt_sha256=prompt_sha256,
                        )
                        records.append(record)
                        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                        stream.flush()

        finished_at = datetime.now(timezone.utc)
        manifest = {
            "schema_version": "iis-ai-client-benchmark/1.0",
            "suite": suite_name,
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "prompt_version": prompt_version,
            "prompt_sha256": prompt_sha256,
            "repeat": repeat,
            "case_ids": [case.case_id for case in cases],
            "clients": [
                {
                    "key": key,
                    "name": client.name,
                    "api_base_url": client.get_api_base_url(),
                }
                for key, client in self.clients.items()
            ],
        }
        summary = summarize_benchmark(records, manifest=manifest)
        _write_json(output_dir / "manifest.json", manifest)
        _write_json(output_dir / "summary.json", summary)
        (output_dir / "summary.md").write_text(
            render_summary_markdown(summary), encoding="utf-8",
        )
        return summary

    def _run_one(self, *, client_key: str, client: BaseAIClient, case: BenchmarkCase,
                 repetition: int, prompt_version: int, prompt: str,
                 prompt_sha256: str) -> dict[str, Any]:
        messages = build_analyze_message(prompt, case.collected_data)
        started_at = datetime.now(timezone.utc)
        started = time.perf_counter()
        response: Any = None
        raised: Exception | None = None
        try:
            response = client.chat(messages=messages, temperature=0, max_tokens=8192)
        except Exception as exc:  # BaseAIClient 通常会转成 error dict，仍防御第三方实现。
            raised = exc
        elapsed = time.perf_counter() - started

        model = _safe_call(client, "get_current_model")
        service = _safe_call(client, "get_api_base_url")
        usage = dict(response.get("usage") or {}) if isinstance(response, dict) else {}
        record: dict[str, Any] = {
            "schema_version": "iis-ai-client-benchmark-result/1.0",
            "timestamp": started_at.isoformat(),
            "client_key": client_key,
            "client_name": client.name,
            "api_base_url": service,
            "model": model,
            "response_model": response.get("model") if isinstance(response, dict) else None,
            "effective_model": (
                response.get("model") if isinstance(response, dict) and response.get("model")
                else model
            ),
            "case_id": case.case_id,
            "tags": list(case.tags),
            "repetition": repetition,
            "prompt_version": prompt_version,
            "prompt_sha256": prompt_sha256,
            "elapsed_seconds": round(elapsed, 6),
            "usage": usage,
            "api_success": False,
            "json_parsed": False,
            "json_repaired": False,
            "v4_valid": False,
            "correctness_pass": False,
            "assertions": [],
            "finish_reason": None,
            "output_chars": 0,
            "raw_output": "",
            "raw_output_truncated": False,
            "raw_output_sha256": None,
            "error_stage": None,
            "error_type": None,
            "error": None,
        }
        if raised is not None:
            _set_error(record, "call", raised)
            return record
        if not isinstance(response, dict):
            _set_error(record, "call", TypeError("Client 未返回字典"))
            return record
        if response.get("error"):
            record["error_stage"] = "api"
            record["error_type"] = str(response.get("error_type") or response.get("error"))
            record["error"] = str(response.get("message") or response.get("error"))
            return record

        choices = response.get("choices") or []
        if not choices:
            _set_error(record, "protocol", ValueError("响应不包含 choices"))
            return record
        choice = choices[0]
        raw_output = str((choice.get("message") or {}).get("content") or "")
        record["api_success"] = True
        record["finish_reason"] = choice.get("finish_reason")
        record["output_chars"] = len(raw_output)
        record["raw_output_sha256"] = hashlib.sha256(raw_output.encode("utf-8")).hexdigest()
        record["raw_output"] = raw_output[:self.raw_output_limit]
        record["raw_output_truncated"] = len(raw_output) > self.raw_output_limit
        if not raw_output:
            _set_error(record, "protocol", ValueError("响应正文为空"))
            return record

        try:
            pure_json = extract_pure_json_text(extract_pure_response(raw_output))
            try:
                parsed = json.loads(pure_json)
            except json.JSONDecodeError:
                parsed = json_repair.loads(pure_json)
                record["json_repaired"] = True
            if not isinstance(parsed, dict):
                raise TypeError("JSON 顶层必须是对象")
            record["json_parsed"] = True
        except Exception as exc:
            _set_error(record, "json", exc)
            return record

        try:
            validated = validate_analysis_result_v4(parsed)
            record["v4_valid"] = True
        except Exception as exc:
            _set_error(record, "v4_validation", exc)
            return record

        assertions = evaluate_expected(validated, case.expected)
        record["assertions"] = assertions
        record["correctness_pass"] = all(item["passed"] for item in assertions)
        if not record["correctness_pass"]:
            record["error_stage"] = "expected"
            record["error_type"] = "assertion_failed"
            record["error"] = "; ".join(
                item["message"] for item in assertions if not item["passed"]
            )
        return record


def evaluate_expected(validated: Any, expected: Mapping[str, Any]) -> list[dict[str, Any]]:
    payload = validated.model_dump(mode="json", by_alias=True)
    assertions: list[dict[str, Any]] = []
    expected_route = expected.get("route")
    if expected_route:
        actual = "non_intelligence" if validated.is_non_intelligence else "valuable"
        assertions.append({
            "name": "route",
            "passed": actual == expected_route,
            "message": f"route expected={expected_route} actual={actual}",
        })

    required = {str(item) for item in expected.get("required_event_predicates", [])}
    if required:
        actual_predicates = set()
        extraction = getattr(validated, "EVENT_EXTRACTION", None)
        if extraction:
            for event in extraction.events:
                predicate_id = event.core.predicate.id
                if predicate_id:
                    actual_predicates.add(str(predicate_id))
        missing = sorted(required - actual_predicates)
        assertions.append({
            "name": "required_event_predicates",
            "passed": not missing,
            "message": f"missing predicates={missing}; actual={sorted(actual_predicates)}",
        })

    serialized = json.dumps(payload, ensure_ascii=False)
    for forbidden in expected.get("forbidden_claims", []):
        forbidden = str(forbidden)
        assertions.append({
            "name": "forbidden_claim",
            "passed": forbidden not in serialized,
            "message": f"forbidden claim present={forbidden!r}",
        })

    # 没有业务断言时，V4 契约通过本身就是该样本唯一断言。
    if not assertions:
        assertions.append({"name": "v4_contract", "passed": True, "message": "V4 valid"})
    return assertions


def summarize_benchmark(records: list[dict[str, Any]], *, manifest: dict[str, Any]) -> dict[str, Any]:
    clients = []
    keys = list(dict.fromkeys(record["client_key"] for record in records))
    for key in keys:
        items = [record for record in records if record["client_key"] == key]
        clients.append({
            "client_key": key,
            "client_name": items[0]["client_name"],
            "models": sorted({str(item["model"]) for item in items if item.get("model")}),
            **_summarize_items(items),
        })
    client_models = []
    pairs = list(dict.fromkeys(
        (record["client_key"], str(record.get("effective_model") or "<unknown>"))
        for record in records
    ))
    for client_key, model in pairs:
        items = [
            record for record in records
            if record["client_key"] == client_key
            and str(record.get("effective_model") or "<unknown>") == model
        ]
        client_models.append({
            "client_key": client_key,
            "client_name": items[0]["client_name"],
            "model": model,
            **_summarize_items(items),
        })
    return {
        "schema_version": "iis-ai-client-benchmark-summary/1.0",
        "manifest": manifest,
        "clients": clients,
        "client_models": client_models,
    }


def render_summary_markdown(summary: Mapping[str, Any]) -> str:
    manifest = summary["manifest"]
    lines = [
        "# IIS AI Client Benchmark",
        "",
        f"- Suite: `{manifest['suite']}`",
        f"- Prompt: `v{manifest['prompt_version']}` / `{manifest['prompt_sha256'][:12]}`",
        f"- Repeat: {manifest['repeat']}",
        "",
        "| Client / model | Runs | API | V4 valid | Correct | P50 | P95 | Tokens/V4 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in summary["client_models"]:
        tokens_per_valid = item["tokens_per_valid_result"]
        lines.append(
            f"| {item['client_name']} / `{item['model']}` | {item['runs']} | "
            f"{item['api_success_rate']:.1%} | {item['v4_valid_rate']:.1%} | "
            f"{item['correctness_rate']:.1%} | {item['latency_p50_seconds']:.3f}s | "
            f"{item['latency_p95_seconds']:.3f}s | "
            f"{tokens_per_valid if tokens_per_valid is not None else '-'} |"
        )
    lines.extend(["", "详细逐次结果见 `results.jsonl`。", ""])
    return "\n".join(lines)


def _safe_call(obj: Any, method_name: str) -> Any:
    try:
        return getattr(obj, method_name)()
    except Exception:
        return None


def _summarize_items(items: list[dict[str, Any]]) -> dict[str, Any]:
    latencies = [float(item["elapsed_seconds"]) for item in items]
    total_tokens = sum(_number(item["usage"].get("total_tokens")) for item in items)
    valid_count = sum(bool(item["v4_valid"]) for item in items)
    return {
        "runs": len(items),
        "api_success": sum(bool(item["api_success"]) for item in items),
        "json_parsed": sum(bool(item["json_parsed"]) for item in items),
        "json_repaired": sum(bool(item["json_repaired"]) for item in items),
        "v4_valid": valid_count,
        "correctness_pass": sum(bool(item["correctness_pass"]) for item in items),
        "api_success_rate": _rate(items, "api_success"),
        "v4_valid_rate": _rate(items, "v4_valid"),
        "correctness_rate": _rate(items, "correctness_pass"),
        "latency_p50_seconds": round(_percentile(latencies, 0.50), 6),
        "latency_p95_seconds": round(_percentile(latencies, 0.95), 6),
        "total_tokens": total_tokens,
        "tokens_per_valid_result": (
            round(total_tokens / valid_count, 2) if valid_count else None
        ),
        "errors_by_stage": _count_values(items, "error_stage"),
    }


def _set_error(record: dict[str, Any], stage: str, exc: Exception) -> None:
    record["error_stage"] = stage
    record["error_type"] = type(exc).__name__
    record["error"] = str(exc)[:4_000]


def _number(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def _rate(items: list[dict[str, Any]], key: str) -> float:
    return sum(bool(item[key]) for item in items) / len(items) if items else 0.0


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _count_values(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = item.get(key)
        if value:
            counts[str(value)] = counts.get(str(value), 0) + 1
    return counts


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
