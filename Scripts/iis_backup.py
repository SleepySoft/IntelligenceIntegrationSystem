#!/usr/bin/env python3
"""IIS 进程外备份、校验与离线恢复入口。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from GlobalConfig import CONFIG_PATH, DATA_PATH, resolve_iis_config_files
from IISConfig import load_iis_config
from OperationsBackup import (
    BackupError, create_backup, load_backup_config, restore_backup, verify_backup,
)


def _config():
    primary, fallback = resolve_iis_config_files()
    return load_iis_config(primary, fallback_file=fallback)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="IIS backup and restore")
    parser.add_argument("--json-details", action="store_true",
                        help="输出完整 manifest；默认只输出适合 journald 的摘要")
    commands = parser.add_subparsers(dest="command", required=True)
    backup = commands.add_parser("backup", help="创建 Mongo/SQLite/配置备份")
    backup.add_argument("--config", type=Path,
                        help="独立备份 YAML；systemd 默认传入 _config/backup.yaml")
    backup.add_argument("--destination", type=Path,
                        help="手工覆盖配置中的目标目录")
    backup.add_argument("--service-url", default="",
                        help="默认使用主配置端口上的 http://127.0.0.1")
    backup.add_argument("--token", default="", help=argparse.SUPPRESS)
    backup.add_argument("--lease-ttl", type=int)
    backup.add_argument("--barrier-timeout", type=float)

    verify = commands.add_parser("verify", help="校验备份清单和所有文件")
    verify.add_argument("backup_dir", type=Path)

    restore = commands.add_parser("restore", help="恢复备份；生产服务必须先停止")
    restore.add_argument("backup_dir", type=Path)
    restore.add_argument("--target-config-dir", type=Path, default=Path(CONFIG_PATH))
    restore.add_argument("--target-data-dir", type=Path, default=Path(DATA_PATH))
    restore.add_argument("--apply", action="store_true")
    restore.add_argument("--confirm-services-stopped", action="store_true",
                         help="确认 IIS/crawler 已停止；--apply 时必需")
    restore.add_argument("--drop-mongo", action="store_true",
                         help="恢复前删除备份中同名 Mongo 集合；灾难恢复通常需要")
    restore.add_argument("--confirm-backup-id", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "verify":
            manifest = verify_backup(args.backup_dir)
        elif args.command == "backup":
            config = _config()
            backup_config = load_backup_config(args.config) if args.config else None
            destination = args.destination or (
                backup_config.destination if backup_config is not None else None)
            if destination is None:
                raise BackupError("必须提供 --config 或 --destination")
            service_url = (
                args.service_url
                or (backup_config.service_url if backup_config is not None else "")
                or f"http://127.0.0.1:{config.settings.server.port}")
            token = args.token or (
                backup_config.maintenance_token() if backup_config is not None
                else os.environ.get("IIS_MAINTENANCE_TOKEN", ""))
            lease_ttl = args.lease_ttl or (
                backup_config.lease_ttl_seconds if backup_config is not None else 900)
            barrier_timeout = args.barrier_timeout or (
                backup_config.barrier_timeout_seconds if backup_config is not None else 60)
            path = create_backup(
                destination=destination, settings=config.settings,
                config_dir=Path(CONFIG_PATH), data_dir=Path(DATA_PATH),
                service_url=service_url, maintenance_token=token,
                lease_ttl=lease_ttl, barrier_timeout=barrier_timeout)
            manifest = verify_backup(path)
            manifest["path"] = str(path)
        else:
            config = _config()
            checked = verify_backup(args.backup_dir)
            if args.apply and args.confirm_backup_id != checked["backup_id"]:
                raise BackupError("--confirm-backup-id 必须与 manifest 中的 backup_id 完全一致")
            if args.apply and not args.confirm_services_stopped:
                raise BackupError("在线恢复不安全；停止 IIS/crawler 后传入 --confirm-services-stopped")
            manifest = restore_backup(
                backup_dir=args.backup_dir, settings=config.settings,
                target_config_dir=args.target_config_dir,
                target_data_dir=args.target_data_dir,
                apply=args.apply, drop_mongo=args.drop_mongo)
            manifest["applied"] = bool(args.apply)
        output = manifest if args.json_details else {
            "backup_id": manifest.get("backup_id"),
            "created_at": manifest.get("created_at"),
            "path": manifest.get("path", str(getattr(args, "backup_dir", ""))),
            "files": len(manifest.get("files", [])),
            "bytes": sum(int(item.get("size", 0)) for item in manifest.get("files", [])),
            **({"applied": manifest.get("applied")} if "applied" in manifest else {}),
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    except BackupError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
