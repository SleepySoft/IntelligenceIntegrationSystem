"""人工输入情报的无持久化调试服务。"""

from __future__ import annotations

import re
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import uuid4

from ServiceComponent.IntelligenceHubDefines_v2 import CollectedData
from ServiceComponent.pipeline.contracts import StageResult
from ServiceComponent.runtime import HubEvent, HubPlugin, HubRuntime


MANUAL_TEST_SOURCE = "__IIS_MANUAL_TEST__"
MANUAL_TEST_TOKEN = "manual-debug-internal"
DEFAULT_ANALYSIS_MECHANISM = "v2"
ANALYSIS_MECHANISM_LABELS = {
    "v2": "V2（当前生产机制）",
    "event_v4": "Event V4（最新机制）",
}


class ManualDebugAnalysisService(HubPlugin):
    """异步执行人工调试分析，并在进程内保留有限数量的结果。

    服务不会发出 Hub 主链事件，也不会调用 intake/archive 端口，因此不会写入 cache、
    archive、low_value，亦不会触发翻译、向量等归档订阅者。
    """

    def __init__(self, analysis_port: Any, subsystem_registry: Any, *,
                 analysis_ports: Mapping[str, Any] | None = None,
                 max_results: int = 50, worker_count: int = 1):
        if max_results < 1:
            raise ValueError("max_results 必须大于 0")
        if worker_count < 1:
            raise ValueError("worker_count 必须大于 0")
        ports = {DEFAULT_ANALYSIS_MECHANISM: analysis_port}
        ports.update(dict(analysis_ports or {}))
        for mechanism, port in ports.items():
            if not callable(getattr(port, "analyze_transient", None)):
                raise TypeError(f"分析机制 {mechanism} 必须实现 analyze_transient()")
            if not callable(getattr(port, "get_transient_prompt_table", None)):
                raise TypeError(f"分析机制 {mechanism} 必须实现 get_transient_prompt_table()")
        self._analysis_ports = ports
        self._registry = subsystem_registry
        self._max_results = max_results
        self._worker_count = worker_count
        self._records: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._lock = threading.RLock()
        self._executor: ThreadPoolExecutor | None = None

    def register(self, runtime: HubRuntime) -> None:
        del runtime

    def start(self, runtime: HubRuntime) -> None:
        del runtime
        with self._lock:
            if self._executor is None:
                self._executor = ThreadPoolExecutor(
                    max_workers=self._worker_count,
                    thread_name_prefix="ManualDebugAnalysis",
                )

    def stop(self, runtime: HubRuntime) -> None:
        del runtime
        with self._lock:
            executor = self._executor
            self._executor = None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)

    def mechanism_catalog(self) -> dict[str, Any]:
        return {
            "default": DEFAULT_ANALYSIS_MECHANISM,
            "items": [
                {
                    "name": name,
                    "display_name": ANALYSIS_MECHANISM_LABELS.get(name, name),
                }
                for name in self._analysis_ports
            ],
        }

    def prompt_catalog(self, subsystem: str = "", mechanism: str = "") -> dict[str, Any]:
        subsystem = str(subsystem or "").strip() or self._registry.default_name
        if self._registry.resolve(subsystem) is None:
            raise ValueError(f"未知子系统: {subsystem}")
        mechanism, analysis_port = self._resolve_analysis_port(mechanism)
        table = analysis_port.get_transient_prompt_table(subsystem)
        if not table:
            raise ValueError(f"子系统 {subsystem} 没有可用 Prompt")
        versions = sorted(int(version) for version in table)
        return {
            "subsystem": subsystem,
            "mechanism": mechanism,
            "versions": versions,
            "default_version": versions[-1],
        }

    def get_prompt(self, *, subsystem: str = "", mechanism: str = "",
                   version: int | str) -> dict[str, Any]:
        catalog = self.prompt_catalog(subsystem, mechanism)
        version = int(version)
        _, analysis_port = self._resolve_analysis_port(catalog["mechanism"])
        table = analysis_port.get_transient_prompt_table(catalog["subsystem"])
        if version not in table:
            raise ValueError(f"Prompt v{version} 未配置")
        return {
            "subsystem": catalog["subsystem"],
            "mechanism": catalog["mechanism"],
            "version": version,
            "content": table[version],
        }

    def submit(self, *, content: str, title: str = "", subsystem: str = "",
               mechanism: str = "",
               prompt_version: int | str | None = None,
               prompt_override: str | None = None) -> dict[str, Any]:
        content = str(content or "").strip()
        if len(content) < 10:
            raise ValueError("正文至少需要 10 个字符")
        if len(content) > 200_000:
            raise ValueError("正文不能超过 200000 个字符")
        subsystem = str(subsystem or "").strip() or self._registry.default_name
        if self._registry.resolve(subsystem) is None:
            raise ValueError(f"未知子系统: {subsystem}")
        mechanism, _ = self._resolve_analysis_port(mechanism)
        catalog = self.prompt_catalog(subsystem, mechanism)
        if prompt_version in (None, ""):
            prompt_version = catalog["default_version"]
        prompt_version = int(prompt_version)
        if prompt_version not in catalog["versions"]:
            raise ValueError(f"Prompt v{prompt_version} 未配置")
        if prompt_override is not None:
            prompt_override = str(prompt_override)
            if not prompt_override.strip():
                raise ValueError("覆盖 Prompt 不能为空")
            if len(prompt_override) > 100_000:
                raise ValueError("覆盖 Prompt 不能超过 100000 个字符")

        job_id = str(uuid4())
        now = datetime.now(timezone.utc)
        collected = CollectedData(
            UUID=job_id,
            token=MANUAL_TEST_TOKEN,
            source=MANUAL_TEST_SOURCE,
            target="manual-debug",
            prompt=prompt_override,
            title=_derive_title(title, content),
            authors=[],
            content=content,
            pub_time=now,
            collect_time=now,
            informant=f"test://manual/{job_id}",
            subsystem=subsystem,
            temp_data={
                "transient": True,
                "manual_debug": {
                    "prompt_version": prompt_version,
                    "prompt_overridden": prompt_override is not None,
                },
            },
        ).model_dump()
        record = {
            "job_id": job_id,
            "status": "queued",
            "submitted_at": now,
            "started_at": None,
            "finished_at": None,
            "subsystem": subsystem,
            "mechanism": mechanism,
            "prompt": {
                "version": prompt_version,
                "overridden": prompt_override is not None,
            },
            "input": collected,
            "result": None,
            "error": "",
        }
        with self._lock:
            if self._executor is None:
                raise RuntimeError("人工调试分析服务尚未启动")
            self._trim_locked()
            if len(self._records) >= self._max_results:
                raise RuntimeError("人工调试队列已满，请等待当前任务完成")
            self._records[job_id] = record
            self._executor.submit(self._run, job_id)
        return deepcopy(record)

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._records.get(str(job_id))
            return deepcopy(record) if record is not None else None

    def list(self, *, limit: int = 20) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), self._max_results))
        with self._lock:
            records = list(reversed(self._records.values()))[:limit]
            return [self._summary(record) for record in records]

    def _run(self, job_id: str) -> None:
        with self._lock:
            record = self._records.get(job_id)
            if record is None:
                return
            record["status"] = "running"
            record["started_at"] = datetime.now(timezone.utc)
            payload = dict(record["input"])
            subsystem = record["subsystem"]
            analysis_port = self._analysis_ports[record["mechanism"]]
        try:
            result = analysis_port.analyze_transient(
                HubEvent("debug.analysis.requested", payload, subsystem)
            )
            if not isinstance(result, StageResult):
                raise TypeError("analyze_transient() 必须返回 StageResult")
            with self._lock:
                record = self._records.get(job_id)
                if record is None:
                    return
                record["finished_at"] = datetime.now(timezone.utc)
                if result.accepted:
                    record["status"] = "completed"
                    record["result"] = result.payload
                else:
                    record["status"] = "failed"
                    record["error"] = result.reason or "analysis_rejected"
        except Exception as exc:
            with self._lock:
                record = self._records.get(job_id)
                if record is not None:
                    record["status"] = "failed"
                    record["finished_at"] = datetime.now(timezone.utc)
                    record["error"] = f"{type(exc).__name__}: {exc}"

    def _trim_locked(self) -> None:
        while len(self._records) >= self._max_results:
            removable = next(
                (key for key, value in self._records.items()
                 if value["status"] in {"completed", "failed"}),
                None,
            )
            if removable is None:
                return
            self._records.pop(removable, None)

    @staticmethod
    def _summary(record: dict[str, Any]) -> dict[str, Any]:
        source = record["input"]
        return {
            "job_id": record["job_id"],
            "status": record["status"],
            "submitted_at": record["submitted_at"],
            "started_at": record["started_at"],
            "finished_at": record["finished_at"],
            "subsystem": record["subsystem"],
            "mechanism": record["mechanism"],
            "prompt": dict(record["prompt"]),
            "input": {
                "UUID": source["UUID"],
                "title": source["title"],
                "source": source["source"],
                "informant": source["informant"],
            },
            "has_result": record["result"] is not None,
            "error": record["error"],
        }

    def _resolve_analysis_port(self, mechanism: str) -> tuple[str, Any]:
        mechanism = str(mechanism or "").strip() or DEFAULT_ANALYSIS_MECHANISM
        analysis_port = self._analysis_ports.get(mechanism)
        if analysis_port is None:
            raise ValueError(f"未知分析机制: {mechanism}")
        return mechanism, analysis_port


def _derive_title(title: str, content: str) -> str:
    title = str(title or "").strip()
    if not title:
        first_line = next((line.strip() for line in content.splitlines() if line.strip()), "")
        title = re.sub(r"^#{1,6}\s*", "", first_line)
    return (title or "人工调试情报")[:120]
