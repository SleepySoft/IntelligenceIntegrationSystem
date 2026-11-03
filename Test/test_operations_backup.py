import json
import sqlite3
from pathlib import Path

import pytest

from OperationsBackup.manager import (
    BackupError, _backup_sqlite, _copy_config_files, _wait_for_intake_barrier,
    verify_backup,
)
from OperationsBackup.config import load_backup_config
from ServiceComponent.runtime.control import RuntimeControlState


def test_backup_lease_pauses_only_intake_and_release_restores():
    control = RuntimeControlState()
    assert control.try_enter_intake() is True
    lease = control.acquire_backup_lease(10, operator="test")
    assert lease["generation"] == 1
    assert control.status()["revision"] >= 1
    assert control.status()["active_intake"] == 1
    assert control.try_enter_intake() is False
    control.leave_intake()
    assert control.is_paused("intake") is True
    assert control.is_paused("analysis") is False
    assert control.status()["scopes"]["intake"] is True
    assert control.status()["backup_lease"]["lease_id"] == lease["lease_id"]
    control.renew_backup_lease(lease["lease_id"], 20)
    control.release_backup_lease(lease["lease_id"])
    assert control.is_paused("intake") is False


def test_expired_backup_lease_fails_open():
    control = RuntimeControlState()
    control.acquire_backup_lease(10)
    with control._lock:
        control._backup_lease["deadline"] = 0
    assert control.is_paused("intake") is False
    assert control.status()["backup_lease"] is None


def test_sqlite_online_backup_and_manifest_verification(tmp_path):
    source = tmp_path / "source.db"
    destination = tmp_path / "backup" / "sqlite" / "data" / "source.db"
    with sqlite3.connect(source) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE records (value TEXT)")
        db.execute("INSERT INTO records VALUES ('kept')")
        db.commit()

    _backup_sqlite(source, destination)
    with sqlite3.connect(destination) as db:
        assert db.execute("SELECT value FROM records").fetchone() == ("kept",)

    import hashlib
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    manifest = {
        "format": "iis-backup", "format_version": 1, "backup_id": "test",
        "files": [{"path": "sqlite/data/source.db",
                   "size": destination.stat().st_size, "sha256": digest}],
    }
    (tmp_path / "backup" / "manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8")
    assert verify_backup(tmp_path / "backup")["backup_id"] == "test"


def test_backup_maintenance_endpoint_uses_separate_token(monkeypatch):
    from test_runtime_control import _build_app

    monkeypatch.setenv("IIS_MAINTENANCE_TOKEN", "maintenance-secret")
    app = _build_app(RuntimeControlState())
    client = app.test_client()
    assert client.post("/api/maintenance/backup/acquire", json={"ttl_seconds": 10}).status_code == 401
    response = client.post(
        "/api/maintenance/backup/acquire", json={"ttl_seconds": 10},
        headers={"Authorization": "Bearer maintenance-secret"})
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["lease"]["lease_id"]
    assert payload["ready_for_snapshot"] is True
    assert payload["active_intake"] == 0
    release = client.post(
        "/api/maintenance/backup/release",
        json={"lease_id": payload["lease"]["lease_id"]},
        headers={"Authorization": "Bearer maintenance-secret"})
    assert release.status_code == 200


def test_loopback_runtime_state_and_ping_need_no_login():
    from test_runtime_control import _build_app

    app = _build_app(RuntimeControlState())
    client = app.test_client()
    state_response = client.get("/api/runtime/state")
    assert state_response.status_code == 200
    state = state_response.get_json()
    assert state["instance"]["instance_id"]
    assert state["instance"]["uptime_sec"] >= 0
    assert state["health"] == {
        "live": True, "read_ready": True, "write_ready": True, "reason": ""}
    assert state["control"]["active_intake"] == 0

    ping = client.get("/maintenance/ping")
    assert ping.status_code == 200
    assert ping.get_json()["instance_id"] == state["instance"]["instance_id"]
    assert ping.headers["Cache-Control"] == "no-store"

    remote = app.test_client()
    assert remote.get(
        "/api/runtime/state", environ_base={"REMOTE_ADDR": "192.0.2.10"}).status_code == 403
    assert remote.get(
        "/maintenance/ping", environ_base={"REMOTE_ADDR": "192.0.2.10"}).status_code == 403


def test_backup_barrier_uses_unified_runtime_state_and_fencing_identity():
    class Client:
        def runtime_state(self):
            return {
                "instance": {"instance_id": "instance-1"},
                "health": {"write_ready": False},
                "control": {
                    "active_intake": 0,
                    "backup_lease": {"lease_id": "lease-1", "generation": 7},
                },
            }

    state = _wait_for_intake_barrier(
        Client(), 0.1, lease_id="lease-1", generation=7,
        instance_id="instance-1")
    assert state["control"]["active_intake"] == 0

    with pytest.raises(BackupError, match="实例.*变化"):
        _wait_for_intake_barrier(
            Client(), 0.1, lease_id="lease-1", generation=7,
            instance_id="different-instance")

    with pytest.raises(BackupError, match="租约.*不一致"):
        _wait_for_intake_barrier(
            Client(), 0.1, lease_id="stale-lease", generation=7,
            instance_id="instance-1")


def test_independent_backup_config_resolves_relative_destination(tmp_path, monkeypatch):
    config = tmp_path / "backup.yaml"
    config.write_text(
        "destination: ../backup-target\n"
        "service_url: http://127.0.0.1:5050\n"
        "maintenance_token_env: TEST_MAINTENANCE_TOKEN\n"
        "lease_ttl_seconds: 120\n"
        "barrier_timeout_seconds: 15\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TEST_MAINTENANCE_TOKEN", "secret")
    loaded = load_backup_config(config)
    assert loaded.destination == (tmp_path / "../backup-target").resolve()
    assert loaded.service_url == "http://127.0.0.1:5050"
    assert loaded.maintenance_token() == "secret"
    assert loaded.lease_ttl_seconds == 120


def test_backup_config_rejects_remote_control_url(tmp_path):
    config = tmp_path / "backup.yaml"
    config.write_text(
        "destination: /mnt/backups\nservice_url: https://example.test\n",
        encoding="utf-8",
    )
    with pytest.raises(BackupError, match="loopback"):
        load_backup_config(config)


def test_config_backup_excludes_python_cache(tmp_path):
    source = tmp_path / "config"
    (source / "__pycache__").mkdir(parents=True)
    (source / "iis.yaml").write_text("server: {}\n", encoding="utf-8")
    (source / "__pycache__/iis.pyc").write_bytes(b"derived")
    destination = tmp_path / "backup"
    _copy_config_files(source, destination)
    assert (destination / "config/iis.yaml").is_file()
    assert not (destination / "config/__pycache__/iis.pyc").exists()
