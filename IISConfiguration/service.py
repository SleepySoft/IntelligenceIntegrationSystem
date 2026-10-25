"""IIS 主配置的草稿校验、安全保存和历史恢复。"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import yaml

from IISConfig import IISConfig, IISSettings


KEEP_SECRET = "__KEEP_EXISTING_SECRET__"
_SECRET_KEY = re.compile(r"(?:password|passwd|secret|token|api[_-]?key)s?$", re.I)


def _parse(text: str) -> dict[str, Any]:
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"YAML 解析失败: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("YAML 顶层必须是对象。")
    return data


def _redact(value: Any, key: str = "") -> Any:
    if _SECRET_KEY.search(key) and value not in (None, "", []):
        if isinstance(value, list):
            return [KEEP_SECRET for _ in value]
        return KEEP_SECRET
    if isinstance(value, dict):
        return {str(k): _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _restore(candidate: Any, current: Any) -> Any:
    if candidate == KEEP_SECRET:
        if current is None:
            raise ValueError("隐藏值没有可保留的现有配置。")
        return current
    if isinstance(candidate, dict):
        old = current if isinstance(current, dict) else {}
        return {key: _restore(value, old.get(key)) for key, value in candidate.items()}
    if isinstance(candidate, list):
        old = current if isinstance(current, list) else []
        return [_restore(value, old[index] if index < len(old) else None)
                for index, value in enumerate(candidate)]
    return candidate


class IISConfigurationService:
    def __init__(self, config_path, *, fallback_path=None, history_limit: int = 30):
        self.config_path = Path(config_path).expanduser().resolve()
        self.fallback_path = (Path(fallback_path).expanduser().resolve()
                              if fallback_path else None)
        self.history_limit = max(1, int(history_limit))
        self._lock = threading.RLock()

    @property
    def history_dir(self) -> Path:
        return self.config_path.parent / ".config-history" / self.config_path.stem

    def _source_path(self) -> Path | None:
        if self.config_path.is_file():
            return self.config_path
        if self.fallback_path and self.fallback_path.is_file():
            return self.fallback_path
        return None

    def _current(self) -> dict[str, Any]:
        source = self._source_path()
        return _parse(source.read_text(encoding="utf-8")) if source else {}

    def read(self) -> dict[str, Any]:
        with self._lock:
            raw = self._current()
            settings = IISSettings.model_validate(raw)
            effective = settings.model_dump(mode="json")
        return {
            "path": str(self.config_path),
            "source_path": str(self._source_path() or self.config_path),
            "exists": self.config_path.is_file(),
            "data": _redact(effective),
            "yaml": yaml.safe_dump(_redact(raw), allow_unicode=True, sort_keys=False),
            "schema": IISSettings.model_json_schema(),
            "keep_secret_sentinel": KEEP_SECRET,
        }

    def validate_data(self, data: Mapping[str, Any]) -> dict[str, Any]:
        with self._lock:
            merged = _restore(dict(data), self._current())
        return self._validate(merged)

    def validate_text(self, text: str) -> dict[str, Any]:
        return self.validate_data(_parse(text))

    def save_data(self, data: Mapping[str, Any]) -> dict[str, Any]:
        return self._save(dict(data))

    def save_text(self, text: str) -> dict[str, Any]:
        return self._save(_parse(text))

    def _validate(self, raw: dict[str, Any]) -> dict[str, Any]:
        settings = IISSettings.model_validate(raw)
        # 同时执行运行时投影中的跨字段检查（例如默认子系统必须已启用）。
        IISConfig(settings, config_file=self.config_path)
        return {"valid": True, "warnings": [],
                "section_count": len(settings.model_fields_set or raw)}

    def _save(self, candidate: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            merged = _restore(candidate, self._current())
            result = self._validate(merged)
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            if self.config_path.is_file():
                self._backup()
            self._atomic_write(yaml.safe_dump(
                merged, allow_unicode=True, sort_keys=False))
            self._prune()
        return {**result, "saved": True, "restart_required": True,
                "path": str(self.config_path)}

    def history(self) -> list[dict[str, Any]]:
        if not self.history_dir.is_dir():
            return []
        return [{"name": path.name, "size": path.stat().st_size,
                 "modified_at": datetime.fromtimestamp(
                     path.stat().st_mtime, timezone.utc).isoformat()}
                for path in sorted(self.history_dir.glob("*.yaml"), reverse=True)]

    def restore(self, name: str) -> dict[str, Any]:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+\.yaml", str(name or "")):
            raise ValueError("无效的历史版本名称。")
        source = (self.history_dir / name).resolve()
        if source.parent != self.history_dir.resolve() or not source.is_file():
            raise ValueError("历史版本不存在。")
        return self.save_text(source.read_text(encoding="utf-8"))

    def _backup(self) -> None:
        self.history_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        shutil.copy2(self.config_path, self.history_dir / f"{stamp}.yaml")

    def _atomic_write(self, text: str) -> None:
        mode = self.config_path.stat().st_mode & 0o777 if self.config_path.exists() else 0o600
        handle, temporary = tempfile.mkstemp(
            prefix=f".{self.config_path.name}.", suffix=".tmp",
            dir=self.config_path.parent)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, mode)
            os.replace(temporary, self.config_path)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    def _prune(self) -> None:
        for path in sorted(self.history_dir.glob("*.yaml"), reverse=True)[self.history_limit:]:
            path.unlink()
