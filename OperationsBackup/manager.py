"""进程外备份实现：Mongo 短时静默、SQLite 在线备份、清单校验与离线恢复。"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from pymongo import MongoClient


FORMAT_VERSION = 1
SQLITE_SUFFIXES = {".db", ".sqlite", ".sqlite3"}
DERIVED_DIRECTORIES = {"VectorDB", "Aggressive", "__pycache__"}


class BackupError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _mongo_client(settings) -> MongoClient:
    kwargs: dict[str, Any] = {
        "host": settings.database.host,
        "port": settings.database.port,
        "serverSelectionTimeoutMS": 10000,
    }
    if settings.database.username:
        kwargs.update(
            username=settings.database.username,
            password=settings.database.password,
            authSource="admin",
        )
    return MongoClient(**kwargs)


def _mongo_tool_command(tool: str, settings, *arguments: str) -> tuple[list[str], str | None]:
    executable = shutil.which(tool)
    if not executable:
        raise BackupError(f"缺少 MongoDB Database Tools 命令: {tool}")
    command = [executable, "--host", settings.database.host,
               "--port", str(settings.database.port)]
    password_input = None
    if settings.database.username:
        command.extend(("--username", settings.database.username,
                        "--authenticationDatabase", "admin", "--password"))
        password_input = settings.database.password + "\n"
    command.extend(arguments)
    return command, password_input


class MaintenanceClient:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.token = token

    def call(self, action: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/api/maintenance/backup/{action}", data=body,
            headers={"Authorization": f"Bearer {self.token}",
                     "Content-Type": "application/json"},
            method="GET" if payload is None else "POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, json.JSONDecodeError) as exc:
            raise BackupError(f"IIS 维护接口调用失败 ({action}): {exc}") from exc

    def runtime_state(self) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self.base_url}/api/runtime/state", method="GET")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, json.JSONDecodeError) as exc:
            raise BackupError(f"IIS 运行状态读取失败: {exc}") from exc


def _wait_for_intake_barrier(client: MaintenanceClient, timeout: float, *,
                             lease_id: str, generation: int,
                             instance_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        status = client.runtime_state()
        current_instance = str((status.get("instance") or {}).get("instance_id") or "")
        lease = (status.get("control") or {}).get("backup_lease") or {}
        if current_instance != instance_id:
            raise BackupError("IIS 实例在备份静默期间发生变化，取消本次备份")
        if (str(lease.get("lease_id") or "") != lease_id
                or int(lease.get("generation") or 0) != generation):
            raise BackupError("IIS 当前维护租约与本次备份不一致")
        active = int((status.get("control") or {}).get("active_intake") or 0)
        if active == 0 and not bool((status.get("health") or {}).get("write_ready", True)):
            return status
        if time.monotonic() >= deadline:
            raise BackupError(
                f"等待 intake 临界区结束超时，仍有 "
                f"{active} 个提交")
        time.sleep(0.5)


def _sqlite_sources(config_dir: Path, data_dir: Path) -> Iterable[tuple[str, Path]]:
    for category, root in (("config", config_dir), ("data", data_dir)):
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in SQLITE_SUFFIXES:
                continue
            relative = path.relative_to(root)
            if any(part in DERIVED_DIRECTORIES for part in relative.parts):
                continue
            yield category, path


def _backup_sqlite(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_uri = f"file:{source.resolve()}?mode=ro"
    with sqlite3.connect(source_uri, uri=True, timeout=30) as source_db:
        with sqlite3.connect(destination) as destination_db:
            source_db.backup(destination_db, pages=2048, sleep=0.05)
            result = destination_db.execute("PRAGMA integrity_check").fetchone()
            # 备份副本是静态产物，不继承源库 WAL 运行模式，避免遗留未纳入恢复的 sidecar。
            destination_db.execute("PRAGMA journal_mode=DELETE")
    if not result or result[0] != "ok":
        raise BackupError(f"SQLite 备份完整性检查失败: {source}")


def _copy_config_files(config_dir: Path, destination: Path) -> None:
    if not config_dir.is_dir():
        return
    for source in sorted(config_dir.rglob("*")):
        if not source.is_file() or source.suffix.lower() in SQLITE_SUFFIXES:
            continue
        relative = source.relative_to(config_dir)
        if any(part in DERIVED_DIRECTORIES for part in relative.parts):
            continue
        target = destination / "config" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def _file_manifest(root: Path) -> list[dict[str, Any]]:
    files = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name != "manifest.json":
            files.append({
                "path": path.relative_to(root).as_posix(),
                "size": path.stat().st_size,
                "sha256": _sha256(path),
            })
    return files


def create_backup(*, destination: Path, settings, config_dir: Path, data_dir: Path,
                  service_url: str, maintenance_token: str, lease_ttl: int = 900,
                  barrier_timeout: float = 60) -> Path:
    """创建完整本地备份；Mongo 静默窗口结束后才备份 SQLite 与配置。"""

    if not maintenance_token:
        raise BackupError("未提供 IIS_MAINTENANCE_TOKEN")
    destination = destination.expanduser().resolve()
    for live_root in (config_dir.resolve(), data_dir.resolve()):
        if destination == live_root or live_root in destination.parents:
            raise BackupError("备份目标不能位于 IIS 配置或数据目录内部")
    destination.mkdir(parents=True, exist_ok=True)
    backup_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    partial = destination / f".{backup_id}.partial"
    final = destination / backup_id
    partial.mkdir(mode=0o700)
    client = MaintenanceClient(service_url, maintenance_token)
    lease_id = ""
    mongo_locked = False
    guard_process: subprocess.Popen[str] | None = None
    guard_marker = data_dir / ".iis_backup_mongo_lock.json"
    guard_owned = False
    mongo = _mongo_client(settings)
    started = datetime.now(timezone.utc)
    runtime_snapshot: dict[str, Any] = {}
    try:
        if guard_marker.exists():
            raise BackupError(
                f"发现未清理的 Mongo 写锁 guard: {guard_marker}；请先确认 Mongo 已解锁")
        lease = client.call("acquire", {"ttl_seconds": lease_ttl}).get("lease") or {}
        lease_id = str(lease.get("lease_id") or "")
        if not lease_id:
            raise BackupError("IIS 未返回维护租约")
        generation = int(lease.get("generation") or 0)
        first_state = client.runtime_state()
        instance_id = str((first_state.get("instance") or {}).get("instance_id") or "")
        if not instance_id or generation <= 0:
            raise BackupError("IIS 运行状态缺少 instance_id 或租约 generation")
        runtime_snapshot = _wait_for_intake_barrier(
            client, barrier_timeout, lease_id=lease_id,
            generation=generation, instance_id=instance_id)

        # standalone mongodump 没有跨集合快照；短时 fsyncLock 阻止所有写入，读查询仍可用。
        guard_marker.parent.mkdir(parents=True, exist_ok=True)
        guard_marker.write_text(json.dumps({"backup_id": backup_id}), encoding="utf-8")
        guard_marker.chmod(0o600)
        guard_owned = True
        guard_env = os.environ.copy()
        guard_env["IIS_MAINTENANCE_TOKEN"] = maintenance_token
        guard_env["IIS_BACKUP_MONGO_PASSWORD"] = settings.database.password
        guard_process = subprocess.Popen(
            [sys.executable, "-m", "OperationsBackup.guard", str(guard_marker),
             backup_id, lease_id, service_url, settings.database.host,
             str(settings.database.port), settings.database.username, str(lease_ttl)],
            cwd=Path(__file__).resolve().parent.parent,
            env=guard_env, start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True,
        )
        mongo.admin.command("fsync", lock=True)
        mongo_locked = True
        mongo_dir = partial / "mongo"
        command, password_input = _mongo_tool_command(
            "mongodump", settings, "--db", "IntelligenceIntegrationSystem",
            "--out", str(mongo_dir), "--gzip")
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        dump_timeout = max(1.0, lease_ttl - elapsed - 10)
        try:
            result = subprocess.run(command, input=password_input, text=True,
                                    capture_output=True, check=False, timeout=dump_timeout)
        except subprocess.TimeoutExpired as exc:
            raise BackupError("mongodump 超过维护租约时限，已中止") from exc
        if result.returncode:
            raise BackupError(f"mongodump 失败: {result.stderr.strip()[-1000:]}")
    except Exception:
        shutil.rmtree(partial, ignore_errors=True)
        raise
    finally:
        if mongo_locked:
            try:
                mongo.admin.command("fsyncUnlock")
            except Exception as exc:
                raise BackupError(f"严重错误：MongoDB 写锁解除失败: {exc}") from exc
        if guard_owned:
            guard_marker.unlink(missing_ok=True)
        if guard_owned and guard_process is not None:
            guard_process.terminate()
            try:
                guard_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                guard_process.kill()
        mongo.close()
        if lease_id:
            for attempt in range(3):
                try:
                    client.call("release", {"lease_id": lease_id})
                    break
                except Exception:
                    if attempt < 2:
                        time.sleep(0.5)

    try:
        resumed_state = client.runtime_state()
        resumed_instance = str(
            (resumed_state.get("instance") or {}).get("instance_id") or "")
        snapshot_instance = str(
            (runtime_snapshot.get("instance") or {}).get("instance_id") or "")
        if resumed_instance != snapshot_instance:
            raise BackupError(
                "IIS 实例在 Mongo 快照期间发生变化；数据已解锁，但本次备份不予正式入库")
        remaining_lease = (resumed_state.get("control") or {}).get("backup_lease") or {}
        if str(remaining_lease.get("lease_id") or "") == lease_id:
            raise BackupError(
                "备份数据已生成，但未能确认 IIS 释放维护租约；租约到期后会自动恢复")
    except Exception:
        shutil.rmtree(partial, ignore_errors=True)
        raise

    try:
        for category, source in _sqlite_sources(config_dir, data_dir):
            root = config_dir if category == "config" else data_dir
            target = partial / "sqlite" / category / source.relative_to(root)
            _backup_sqlite(source, target)
        _copy_config_files(config_dir, partial / "files")
        manifest = {
            "format": "iis-backup",
            "format_version": FORMAT_VERSION,
            "backup_id": backup_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "started_at": started.isoformat(),
            "source": {"config_dir": str(config_dir), "data_dir": str(data_dir)},
            "runtime": {
                "instance_id": (runtime_snapshot.get("instance") or {}).get("instance_id"),
                "started_at": (runtime_snapshot.get("instance") or {}).get("started_at"),
                "control_revision": (runtime_snapshot.get("control") or {}).get("revision"),
                "lease_generation": (
                    ((runtime_snapshot.get("control") or {}).get("backup_lease") or {})
                    .get("generation")),
            },
            "mongo": {"host": settings.database.host, "port": settings.database.port,
                      "database": "IntelligenceIntegrationSystem",
                      "write_locked_during_dump": True},
            "files": _file_manifest(partial),
        }
        (partial / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(partial, final)
        return final
    except Exception:
        shutil.rmtree(partial, ignore_errors=True)
        raise


def verify_backup(backup_dir: Path) -> dict[str, Any]:
    backup_dir = backup_dir.expanduser().resolve()
    manifest_path = backup_dir / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BackupError(f"无法读取备份清单: {exc}") from exc
    if manifest.get("format") != "iis-backup" or manifest.get("format_version") != FORMAT_VERSION:
        raise BackupError("不支持的备份格式")
    listed: set[str] = set()
    for item in manifest.get("files", []):
        relative = Path(str(item.get("path", "")))
        if relative.is_absolute() or not relative.parts or ".." in relative.parts:
            raise BackupError(f"备份清单包含非法路径: {relative}")
        rendered = relative.as_posix()
        if rendered in listed:
            raise BackupError(f"备份清单包含重复路径: {rendered}")
        listed.add(rendered)
        path = backup_dir / relative
        if not path.is_file() or path.stat().st_size != item["size"]:
            raise BackupError(f"备份文件缺失或大小不符: {rendered}")
        if _sha256(path) != item["sha256"]:
            raise BackupError(f"备份校验和不符: {rendered}")
    actual = {
        path.relative_to(backup_dir).as_posix()
        for path in backup_dir.rglob("*")
        if path.is_file() and path.name != "manifest.json"
    }
    if actual != listed:
        missing = sorted(listed - actual)
        extra = sorted(actual - listed)
        raise BackupError(f"备份文件集合与清单不符: missing={missing[:5]} extra={extra[:5]}")
    return manifest


def restore_backup(*, backup_dir: Path, settings, target_config_dir: Path,
                   target_data_dir: Path, apply: bool, drop_mongo: bool) -> dict[str, Any]:
    """恢复到已停止的 IIS；调用方必须显式 apply，SQLite 使用原子替换。"""

    manifest = verify_backup(backup_dir)
    if not apply:
        return manifest
    mongo_dir = backup_dir / "mongo"
    command_args = ["--gzip"]
    if drop_mongo:
        command_args.append("--drop")
    command_args.append(str(mongo_dir))
    command, password_input = _mongo_tool_command("mongorestore", settings, *command_args)
    result = subprocess.run(command, input=password_input, text=True,
                            capture_output=True, check=False)
    if result.returncode:
        raise BackupError(f"mongorestore 失败: {result.stderr.strip()[-1000:]}")

    for category, target_root in (("config", target_config_dir), ("data", target_data_dir)):
        source_root = backup_dir / "sqlite" / category
        if not source_root.is_dir():
            continue
        for source in source_root.rglob("*"):
            if not source.is_file():
                continue
            target = target_root / source.relative_to(source_root)
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as temporary:
                temporary_path = Path(temporary.name)
            try:
                shutil.copy2(source, temporary_path)
                os.replace(temporary_path, target)
            finally:
                temporary_path.unlink(missing_ok=True)

    config_files = backup_dir / "files" / "config"
    if config_files.is_dir():
        for source in config_files.rglob("*"):
            if not source.is_file():
                continue
            target = target_config_dir / source.relative_to(config_files)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    return manifest
