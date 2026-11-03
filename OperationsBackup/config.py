"""独立备份配置；不进入 IIS 主配置，也不由 IIS 进程调度。"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import yaml

from .manager import BackupError


@dataclass(frozen=True)
class BackupConfig:
    destination: Path
    service_url: str = "http://127.0.0.1:5000"
    maintenance_token_env: str = "IIS_MAINTENANCE_TOKEN"
    maintenance_token_file: Path | None = None
    lease_ttl_seconds: int = 900
    barrier_timeout_seconds: float = 60.0

    def maintenance_token(self) -> str:
        value = os.environ.get(self.maintenance_token_env, "")
        if value:
            return value
        if self.maintenance_token_file is not None:
            try:
                mode = stat.S_IMODE(self.maintenance_token_file.stat().st_mode)
                if mode & 0o077:
                    raise BackupError(
                        f"维护 Token 文件权限过宽: {self.maintenance_token_file} ({mode:o})")
                return self.maintenance_token_file.read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise BackupError(
                    f"维护 Token 文件读取失败: {self.maintenance_token_file}: {exc}") from exc
        return ""


def load_backup_config(path: Path) -> BackupConfig:
    path = path.expanduser().resolve()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise BackupError(f"备份配置读取失败: {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise BackupError("备份配置顶层必须是对象")
    allowed = {
        "destination", "service_url", "maintenance_token_env",
        "maintenance_token_file", "lease_ttl_seconds", "barrier_timeout_seconds",
    }
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise BackupError(f"备份配置包含未知字段: {unknown}")
    destination_text = str(raw.get("destination") or "").strip()
    if not destination_text:
        raise BackupError("备份配置必须提供 destination")
    destination = Path(destination_text).expanduser()
    if not destination.is_absolute():
        destination = (path.parent / destination).resolve()
    service_url = str(raw.get("service_url") or "http://127.0.0.1:5000").rstrip("/")
    parsed_url = urlparse(service_url)
    if parsed_url.scheme != "http" or parsed_url.hostname not in {"127.0.0.1", "::1"}:
        raise BackupError("service_url 必须指向 loopback HTTP 地址")
    token_env = str(raw.get("maintenance_token_env") or "IIS_MAINTENANCE_TOKEN").strip()
    if not token_env or not token_env.replace("_", "A").isalnum():
        raise BackupError("maintenance_token_env 不是有效环境变量名")
    token_file_text = str(raw.get("maintenance_token_file") or "maintenance.token").strip()
    token_file = Path(token_file_text).expanduser()
    if not token_file.is_absolute():
        token_file = (path.parent / token_file).resolve()
    lease_ttl = int(raw.get("lease_ttl_seconds", 900))
    barrier_timeout = float(raw.get("barrier_timeout_seconds", 60))
    if not 10 <= lease_ttl <= 3600:
        raise BackupError("lease_ttl_seconds 必须在 10..3600 之间")
    if not 1 <= barrier_timeout <= 600:
        raise BackupError("barrier_timeout_seconds 必须在 1..600 之间")
    return BackupConfig(
        destination=destination.resolve(), service_url=service_url,
        maintenance_token_env=token_env, maintenance_token_file=token_file,
        lease_ttl_seconds=lease_ttl,
        barrier_timeout_seconds=barrier_timeout,
    )
