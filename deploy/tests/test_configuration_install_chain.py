"""配置网页后端到无人值守安装器的契约链路测试。"""

from __future__ import annotations

import re

from flask import Flask

from IISConfiguration import IISConfigurationService, IISConfigurationWeb
from deploy import iis_deploy


def test_web_generated_configuration_is_consumed_by_unattended_installer(
        tmp_path, monkeypatch):
    # 本测试验证网页输出到安装后端的契约，不验证 bootstrap 的版本选择。
    monkeypatch.setattr(iis_deploy, "install_python_supported", lambda: True)
    config = tmp_path / "answers" / "iis.yaml"
    fallback = tmp_path / "example.yaml"
    config.parent.mkdir()
    fallback.write_text("{}\n", encoding="utf-8")
    app = Flask(__name__)
    app.secret_key = "chain-test"
    IISConfigurationWeb(IISConfigurationService(
        config, fallback_path=fallback)).mount_to_app(app, url_prefix="/config")
    client = app.test_client()
    page = client.get("/config/setup/?lang=en").get_data(as_text=True)
    csrf = re.search(r'const csrf="([^"]+)"', page).group(1)
    payload = client.get("/config/api/config").get_json()["data"]
    payload["access"]["collector_tokens"] = ["chain-collector-token"]
    payload["access"]["rpc_tokens"] = ["chain-rpc-token"]
    payload["optional_subsystems"]["translation"]["enabled"] = False
    response = client.post(
        "/config/api/save", json={"data": payload},
        headers={"X-Config-CSRF": csrf})
    assert response.status_code == 200

    ai = config.with_name("ai_clients.yaml")
    ai.write_text(
        "clients:\n"
        "- factory: codex\n  name: Chain Codex\n  group: codex_cli\n"
        "  mode: stateless\n  sandbox: read-only\n"
        "group_limits:\n  codex_cli: 1\n",
        encoding="utf-8",
    )
    root = tmp_path / "installed"
    code = iis_deploy.main([
        "install", "--non-interactive",
        "--config", str(config), "--ai-config", str(ai),
        "--sandbox-root", str(root), "--service-user", "iis-chain-test",
        "--skip-dependencies", "--skip-browser", "--no-systemd", "--no-start",
        "--skip-admin",
    ])

    assert code == 0
    install_root = root / "opt/iis"
    assert (install_root / "_config/iis.yaml").is_file()
    assert (install_root / "_config/ai_clients.yaml").is_file()
    assert (install_root / "_config/collector.json").is_file()
    assert (install_root / "_data/deployment/install-state.json").is_file()
