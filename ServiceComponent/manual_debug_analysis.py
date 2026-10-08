"""人工输入情报的无持久化调试服务。"""

from __future__ import annotations

import re
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from ServiceComponent.IntelligenceHubDefines_v2 import CollectedData
from ServiceComponent.pipeline.contracts import StageResult
from ServiceComponent.runtime import HubEvent, HubPlugin, HubRuntime


MANUAL_TEST_SOURCE = "__IIS_MANUAL_TEST__"
MANUAL_TEST_TOKEN = "manual-debug-internal"


class ManualDebugAnalysisService(HubPlugin):
    """异步执行人工调试分析，并在进程内保留有限数量的结果。

    服务不会发出 Hub 主链事件，也不会调用 intake/archive 端口，因此不会写入 cache、
    archive、low_value，亦不会触发翻译、向量等归档订阅者。
    """

    def __init__(self, analysis_port: Any, subsystem_registry: Any, *,
                 max_results: int = 50, worker_count: int = 1):
        if max_results < 1:
            raise ValueError("max_results 必须大于 0")
        if worker_count < 1:
            raise ValueError("worker_count 必须大于 0")
        if not callable(getattr(analysis_port, "analyze_transient", None)):
            raise TypeError("analysis_port 必须实现 analyze_transient()")
        self._analysis_port = analysis_port
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

    def submit(self, *, content: str, title: str = "", subsystem: str = "") -> dict[str, Any]:
        content = str(content or "").strip()
        if len(content) < 10:
            raise ValueError("正文至少需要 10 个字符")
        if len(content) > 200_000:
            raise ValueError("正文不能超过 200000 个字符")
        subsystem = str(subsystem or "").strip() or self._registry.default_name
        if self._registry.resolve(subsystem) is None:
            raise ValueError(f"未知子系统: {subsystem}")

        job_id = str(uuid4())
        now = datetime.now(timezone.utc)
        collected = CollectedData(
            UUID=job_id,
            token=MANUAL_TEST_TOKEN,
            source=MANUAL_TEST_SOURCE,
            target="manual-debug",
            title=_derive_title(title, content),
            authors=[],
            content=content,
            pub_time=now,
            collect_time=now,
            informant=f"test://manual/{job_id}",
            subsystem=subsystem,
            temp_data={"transient": True, "manual_debug": True},
        ).model_dump()
        record = {
            "job_id": job_id,
            "status": "queued",
            "submitted_at": now,
            "started_at": None,
            "finished_at": None,
            "subsystem": subsystem,
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
        try:
            result = self._analysis_port.analyze_transient(
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
            "input": {
                "UUID": source["UUID"],
                "title": source["title"],
                "source": source["source"],
                "informant": source["informant"],
            },
            "has_result": record["result"] is not None,
            "error": record["error"],
        }


def _derive_title(title: str, content: str) -> str:
    title = str(title or "").strip()
    if not title:
        first_line = next((line.strip() for line in content.splitlines() if line.strip()), "")
        title = re.sub(r"^#{1,6}\s*", "", first_line)
    return (title or "人工调试情报")[:120]
