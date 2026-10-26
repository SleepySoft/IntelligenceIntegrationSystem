"""Persist bounded AI analysis failure samples for offline diagnosis."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from pydantic import ValidationError


logger = logging.getLogger(__name__)


class AnalysisFailureRecorder:
    """将失败样本写入本地 JSON；写入失败绝不影响分析主链。"""

    def __init__(self, directory: str | Path, *, enabled: bool = True,
                 max_files: int = 500):
        if max_files < 1:
            raise ValueError("max_files 必须大于 0。")
        self.directory = Path(directory)
        self.enabled = enabled
        self.max_files = max_files
        self._lock = threading.Lock()

    def record(self, *, category: str, error: Any, raw_response: Any,
               original_data: Mapping[str, Any], domain_name: str = "",
               subsystem: str | None = None,
               trace_id: str, attempt: int, prompt_version: int,
               prompt: str, ai_service: str, ai_model: str,
               transient: bool) -> Path | None:
        if not self.enabled:
            return None
        try:
            error_detail = self._error_detail(error)
            now = datetime.now(timezone.utc)
            source_uuid = str(original_data.get("UUID", "")).strip()
            if domain_name and subsystem and domain_name != subsystem:
                raise ValueError("domain_name and legacy subsystem disagree")
            resolved_domain = domain_name or subsystem or ""
            document = {
                "schema_version": 1,
                "captured_at": now.isoformat(),
                "category": str(category),
                "trace_id": str(trace_id),
                "source_uuid": source_uuid,
                "domain_name": str(resolved_domain),
                "attempt": int(attempt),
                "prompt_version": int(prompt_version),
                "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                "ai_service": str(ai_service),
                "ai_model": str(ai_model),
                "transient": bool(transient),
                "input": {
                    "title": original_data.get("title"),
                    "informant": original_data.get("informant"),
                    "source": original_data.get("source"),
                },
                "error": error_detail,
                "raw_response": raw_response,
            }
            with self._lock:
                self.directory.mkdir(parents=True, exist_ok=True)
                filename = (
                    f"{now.strftime('%Y%m%dT%H%M%S.%fZ')}_"
                    f"{self._safe_name(source_uuid or trace_id)}_a{attempt}_"
                    f"{uuid4().hex[:8]}.json"
                )
                target = self.directory / filename
                temporary = target.with_suffix(".tmp")
                temporary.write_text(
                    json.dumps(document, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8",
                )
                os.replace(temporary, target)
                self._trim_locked()
                return target
        except Exception:
            logger.exception("Failed to persist AI analysis failure sample.")
            return None

    @staticmethod
    def _error_detail(error: Any) -> dict[str, Any]:
        if isinstance(error, ValidationError):
            return {
                "type": type(error).__name__,
                "message": str(error),
                "details": error.errors(include_url=False),
            }
        return {
            "type": type(error).__name__,
            "message": str(error),
            "details": [],
        }

    @staticmethod
    def _safe_name(value: str) -> str:
        safe = "".join(char if char.isalnum() or char in "-_" else "_" for char in value)
        return safe[:80] or "unknown"

    def _trim_locked(self) -> None:
        # 文件名以固定宽度 UTC 微秒时间开头；按名称排序既是时间顺序，也不受某些
        # 文件系统 mtime 分辨率影响。否则同一时钟刻度内连续写入时可能误删最新样本。
        files = sorted(self.directory.glob("*.json"), key=lambda item: item.name)
        for stale in files[:-self.max_files]:
            try:
                stale.unlink()
            except OSError:
                logger.warning("Failed to remove stale analysis failure sample: %s", stale)
