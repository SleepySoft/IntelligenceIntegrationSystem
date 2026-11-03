"""Mongo 写锁失联保护；父备份进程被强杀后按租约期限自动解锁。"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from pymongo import MongoClient

from .manager import MaintenanceClient


def main(argv: list[str]) -> int:
    if len(argv) != 8:
        return 2
    marker_path = Path(argv[0])
    backup_id, lease_id, service_url, host, port, username, delay = argv[1:]
    time.sleep(float(delay))
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        if marker.get("backup_id") != backup_id:
            return 0
    except (OSError, json.JSONDecodeError):
        return 0

    kwargs = {"host": host, "port": int(port), "serverSelectionTimeoutMS": 10000}
    password = os.environ.get("IIS_BACKUP_MONGO_PASSWORD", "")
    if username:
        kwargs.update(username=username, password=password, authSource="admin")
    try:
        mongo = MongoClient(**kwargs)
        try:
            mongo.admin.command("fsyncUnlock")
        finally:
            mongo.close()
    except Exception:
        pass
    token = os.environ.get("IIS_MAINTENANCE_TOKEN", "")
    if token:
        try:
            MaintenanceClient(service_url, token).call("release", {"lease_id": lease_id})
        except Exception:
            pass
    marker_path.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
