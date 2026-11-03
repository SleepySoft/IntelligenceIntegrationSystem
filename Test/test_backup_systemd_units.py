from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_backup_runs_outside_iis_and_timer_is_persistent():
    service = (ROOT / "systemd/iis-backup.service").read_text(encoding="utf-8")
    timer = (ROOT / "systemd/iis-backup.timer").read_text(encoding="utf-8")
    assert "Type=oneshot" in service
    assert "Scripts/iis_backup.py backup --config _config/backup.yaml" in service
    assert "KillMode=process" in service
    assert "TimeoutStartSec=" in service
    assert "OnCalendar=" in timer
    assert "RandomizedDelaySec=" in timer
    assert "Persistent=true" in timer
    assert "WantedBy=timers.target" in timer
