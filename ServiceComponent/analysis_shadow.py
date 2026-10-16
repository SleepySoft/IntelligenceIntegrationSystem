"""影子分析记录器：并行链路（prompt+后处理）的对比样本落盘。

影子分析用于保守灰度：生产归档仍由主链（抽样表中的 prompt 版本）产生，
影子版本对同一输入并行分析，结果只写入本目录、绝不进入归档与索引，
供离线对比两条链路的抽取质量。写入失败绝不影响分析主链。
"""

from __future__ import annotations

import json
import logging
import os
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

    def record(self, *, original_data: Mapping[str, Any], subsystem: str,
               primary_prompt_version: int, shadow_prompt_version: int,
               analysis: Any, ai_service: str, ai_model: str,
               extraction_quality: Any = None) -> Path | None:
        if not self.enabled:
            return None
        try:
            now = datetime.now(timezone.utc)
            source_uuid = str(original_data.get("UUID", "")).strip()
            document = {
                "schema_version": 1,
                "captured_at": now.isoformat(),
                "source_uuid": source_uuid,
                "subsystem": str(subsystem),
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
                self._trim_locked()
                return target
        except Exception:
            logger.exception("Failed to persist shadow analysis record.")
            return None

    @staticmethod
    def _safe_name(value: str) -> str:
        safe = "".join(char if char.isalnum() or char in "-_" else "_" for char in value)
        return safe[:80] or "unknown"

    def _trim_locked(self) -> None:
        files = sorted(self.directory.glob("*.json"), key=lambda item: item.stat().st_mtime)
        for stale in files[:-self.max_files]:
            try:
                stale.unlink()
            except OSError:
                logger.warning("Failed to remove stale shadow analysis record: %s", stale)
