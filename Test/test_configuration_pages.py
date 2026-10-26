from __future__ import annotations

import re

import yaml
from flask import Flask

from AIClientCenter.configuration import (
    AIClientConfigurationService, KEEP_SECRET as AI_KEEP_SECRET,
)
from AIClientCenter.web.configuration import AIClientConfigurationWeb
from IISConfiguration.service import IISConfigurationService
from IISConfiguration import schema_contract
from IISConfiguration.web import IISConfigurationWeb


def _csrf(html: str) -> str:
    match = re.search(r"const csrf=\"([^\"]+)\"", html)
    assert match
    return match.group(1)


def test_iis_configuration_validates_masks_saves_and_backs_up(tmp_path):
    path = tmp_path / "iis.yaml"
    path.write_text("database:\n  password: first-secret\n", encoding="utf-8")
    service = IISConfigurationService(path)

    public = service.read()
    assert public["data"]["database"]["password"] != "first-secret"
    assert public["schema_ui_contract"]["compatible"] is True
    public["data"]["server"]["port"] = 5100
    result = service.save_data(public["data"])

    assert result["restart_required"] is True
    saved = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert saved["server"]["port"] == 5100
    assert saved["database"]["password"] == "first-secret"
    assert len(service.history()) == 1


def test_iis_configuration_rejects_invalid_draft_without_overwrite(tmp_path):
    path = tmp_path / "iis.yaml"
    original = "server:\n  port: 5000\n"
    path.write_text(original, encoding="utf-8")
    service = IISConfigurationService(path)

    try:
        service.save_text("server:\n  port: 70000\n")
    except ValueError:
        pass
    else:
        raise AssertionError("invalid config unexpectedly saved")
    assert path.read_text(encoding="utf-8") == original


def test_iis_schema_change_requires_configuration_ui_review(monkeypatch):
    monkeypatch.setattr(schema_contract, "REVIEWED_IIS_SCHEMA_SHA256", "outdated")
    contract = schema_contract.schema_ui_contract()
    assert contract["compatible"] is False
    assert contract["warning"]


def test_ai_configuration_preserves_masked_secret_and_creates_history(tmp_path):
    path = tmp_path / "clients.yaml"
    path.write_text(
        "clients:\n"
        "- factory: ignored\n  name: disabled\n  group: off\n"
        "  enable: false\n  api_key: top-secret\n"
        "group_limits: {}\n",
        encoding="utf-8",
    )
    service = AIClientConfigurationService(path, include_builtins=False)
    response = service.read()
    assert response["schema_ui_contract"]["compatible"] is True
    assert response["selected_by"] == "explicit_argument"
    public = response["data"]
    assert public["clients"][0]["api_key"] == AI_KEEP_SECRET
    public["clients"][0]["name"] = "still disabled"

    service.save_data(public)
    saved = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert saved["clients"][0]["api_key"] == "top-secret"
    assert len(service.history()) == 1


def test_iis_configuration_blueprint_supports_page_and_csrf_api(tmp_path):
    path = tmp_path / "iis.yaml"
    path.write_text("{}\n", encoding="utf-8")
    app = Flask(__name__)
    app.secret_key = "test"
    IISConfigurationWeb(IISConfigurationService(path)).mount_to_app(
        app, url_prefix="/settings")
    client = app.test_client()

    page = client.get("/settings/?lang=en")
    assert page.status_code == 200
    token = _csrf(page.get_data(as_text=True))
    config = client.get("/settings/api/config")
    assert config.status_code == 200
    denied = client.post("/settings/api/validate", json={"data": {}})
    assert denied.status_code == 403
    accepted = client.post(
        "/settings/api/validate", json={"data": {}},
        headers={"X-Config-CSRF": token})
    assert accepted.status_code == 200


def test_iis_first_run_redirects_to_setup_and_can_save(tmp_path):
    target = tmp_path / "iis.yaml"
    fallback = tmp_path / "example.yaml"
    fallback.write_text("{}\n", encoding="utf-8")
    app = Flask(__name__)
    app.secret_key = "test"
    IISConfigurationWeb(IISConfigurationService(
        target, fallback_path=fallback),
        setup_ai_url="/ai-config/",
    ).mount_to_app(app, url_prefix="/settings")
    client = app.test_client()

    redirect = client.get("/settings/")
    assert redirect.status_code == 302
    assert redirect.headers["Location"].endswith("/settings/setup/")
    page = client.get("/settings/setup/?lang=en")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "Create the minimum configuration" in html
    token = _csrf(html)
    chinese = client.get("/settings/setup/?lang=zh-CN")
    assert chinese.headers["Content-Language"] == "zh-CN"
    assert "创建启动 IIS 所需的最小配置" in chinese.get_data(as_text=True)
    token = _csrf(chinese.get_data(as_text=True))
    config = client.get("/settings/api/config").get_json()
    config["data"]["server"]["port"] = 5055
    saved = client.post(
        "/settings/api/save", json={"data": config["data"]},
        headers={"X-Config-CSRF": token})

    assert saved.status_code == 200
    assert target.is_file()
    assert yaml.safe_load(target.read_text(encoding="utf-8"))["server"]["port"] == 5055
    assert client.get("/settings/").status_code == 200


def test_ai_configuration_blueprint_renders_standalone(tmp_path):
    path = tmp_path / "clients.yaml"
    path.write_text("clients: []\ngroup_limits: {}\n", encoding="utf-8")
    app = Flask(__name__)
    app.secret_key = "test"
    AIClientConfigurationWeb(AIClientConfigurationService(path)).mount_to_app(
        app, url_prefix="/config")

    response = app.test_client().get("/config/?lang=en")
    assert response.status_code == 200
    assert "AI client configuration" in response.get_data(as_text=True)
