from __future__ import annotations

import json
import stat
import sys
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from deploy.installer import (
    InstallError, InstallLayout, InstallRequest, install,
)


def _inputs(tmp_path: Path):
    config = tmp_path / "input" / "iis.yaml"
    ai = tmp_path / "input" / "ai_clients.yaml"
    config.parent.mkdir()
    config.write_text(
        "server:\n  port: 5050\n"
        "access:\n  collector_tokens: [collector-secret]\n"
        "optional_subsystems:\n  translation:\n    enabled: false\n",
        encoding="utf-8",
    )
    ai.write_text(
        "clients:\n"
        "- factory: codex\n  name: Test Codex\n  group: codex_cli\n"
        "  mode: stateless\n  sandbox: read-only\n"
        "group_limits:\n  codex_cli: 1\n",
        encoding="utf-8",
    )
    return config, ai


def _request(tmp_path: Path, config: Path, ai: Path, *, force=False):
    return InstallRequest(
        config_path=config,
        ai_config_path=ai,
        collector_config_path=None,
        layout=InstallLayout.build(
            Path(__file__).resolve().parents[2], Path("/opt/iis"), tmp_path / "root"),
        service_user="iis-test-user",
        install_dependencies=False,
        install_browser=False,
        manage_systemd=False,
        start_services=False,
        force_config=force,
        skip_admin=True,
    )


def test_sandbox_unattended_install_writes_complete_layout(tmp_path):
    config, ai = _inputs(tmp_path)
    request = _request(tmp_path, config, ai)
    result = install(request)

    root = tmp_path / "root"
    installation = root / "opt/iis"
    installed_config = installation / "_config/iis.yaml"
    environment = installation / "_config/iis.env"
    collector = json.loads(
        (installation / "_config/collector.json").read_text(encoding="utf-8"))
    state = json.loads(Path(result.state_file).read_text(encoding="utf-8"))
    assert result.status == "complete"
    assert state["phase"] == "complete"
    assert stat.S_IMODE(installed_config.stat().st_mode) == 0o600
    assert stat.S_IMODE(environment.stat().st_mode) == 0o600
    assert collector["collector"]["default_token"] == "collector-secret"
    environment_text = environment.read_text(encoding="utf-8")
    assert f"IIS_RUNTIME_ROOT={installation}" in environment_text
    assert f"IIS_CONFIG={installation / '_config/iis.yaml'}" in environment_text
    assert "IIS_DATA_DIR=" not in environment_text
    web_unit = (root / "etc/systemd/system/iis-web.service").read_text(
        encoding="utf-8")
    assert "mongod.service" not in web_unit
    expected_python = Path(sys.executable).resolve()
    assert f'ExecStart="{expected_python}"' in web_unit
    assert "/home/sleepy/anaconda3/envs/iis/bin/python" not in web_unit
    assert "deployment_preflight.py" in web_unit


def test_sandbox_install_is_idempotent_and_preserves_session_secret(tmp_path):
    config, ai = _inputs(tmp_path)
    request = _request(tmp_path, config, ai)
    install(request)
    env_path = tmp_path / "root/opt/iis/_config/iis.env"
    first_secret = next(
        line for line in env_path.read_text(encoding="utf-8").splitlines()
        if line.startswith("IIS_SESSION_SECRET="))

    install(request)
    second_secret = next(
        line for line in env_path.read_text(encoding="utf-8").splitlines()
        if line.startswith("IIS_SESSION_SECRET="))
    assert second_secret == first_secret


def test_install_refuses_to_overwrite_changed_config_without_force(tmp_path):
    config, ai = _inputs(tmp_path)
    request = _request(tmp_path, config, ai)
    install(request)
    config_data = yaml.safe_load(config.read_text(encoding="utf-8"))
    config_data["server"]["port"] = 5051
    config.write_text(yaml.safe_dump(config_data), encoding="utf-8")

    with pytest.raises(InstallError, match="目标配置已存在"):
        install(request)
    installed = yaml.safe_load(
        (tmp_path / "root/opt/iis/_config/iis.yaml").read_text(encoding="utf-8"))
    assert installed["server"]["port"] == 5050


def test_unattended_install_can_create_administrator_from_password_file(tmp_path):
    config, ai = _inputs(tmp_path)
    password_file = tmp_path / "admin-password"
    password_file.write_text("correct horse battery staple\n", encoding="utf-8")
    password_file.chmod(0o600)
    request = replace(
        _request(tmp_path, config, ai),
        skip_admin=False,
        admin_user="admin",
        admin_password_file=password_file,
    )

    install(request)

    from ServiceComponent.UserManager import UserManager
    database = tmp_path / "root/opt/iis/_config/Authentication.db"
    assert stat.S_IMODE(database.stat().st_mode) == 0o600
    assert UserManager(str(database)).authenticate(
        "admin", "correct horse battery staple", "127.0.0.1")[0] is True
