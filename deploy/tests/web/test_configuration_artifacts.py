from __future__ import annotations

import re
import stat
from pathlib import Path

import yaml
from flask import Flask

from IISConfiguration import IISConfigurationService, IISConfigurationWeb
from IISConfiguration.admin_secret import AdminSecretService


def _csrf(html: str) -> str:
    match = re.search(r'const csrf="([^"]+)"', html)
    assert match
    return match.group(1)


def test_configuration_web_generates_config_and_private_admin_secret(tmp_path):
    config = tmp_path / "answers/iis.yaml"
    fallback = tmp_path / "example.yaml"
    fallback.write_text("{}\n", encoding="utf-8")
    secret_dir = tmp_path / "runtime/iis-bootstrap"
    app = Flask(__name__)
    app.secret_key = "artifact-test"
    IISConfigurationWeb(
        IISConfigurationService(config, fallback_path=fallback),
        admin_secret_service=AdminSecretService(secret_dir),
    ).mount_to_app(app, url_prefix="/config")
    client = app.test_client()
    token = _csrf(client.get("/config/setup/?lang=en").get_data(as_text=True))
    payload = client.get("/config/api/config").get_json()["data"]
    payload["access"]["collector_tokens"] = ["artifact-collector"]
    payload["access"]["rpc_tokens"] = ["artifact-rpc"]

    saved = client.post(
        "/config/api/save", json={"data": payload},
        headers={"X-Config-CSRF": token})
    generated = client.post(
        "/config/api/admin-secret",
        json={
            "username": "admin_user",
            "password": "correct horse battery staple ",
            "confirmation": "correct horse battery staple ",
        },
        headers={"X-Config-CSRF": token},
    )

    assert saved.status_code == 200
    assert yaml.safe_load(config.read_text(encoding="utf-8"))[
        "access"]["collector_tokens"] == ["artifact-collector"]
    assert generated.status_code == 200
    response = generated.get_json()
    secret = Path(response["password_file"])
    assert secret.parent == secret_dir
    assert secret.name.startswith("admin-password-")
    assert secret.read_text(encoding="utf-8") == "correct horse battery staple \n"
    assert stat.S_IMODE(secret.stat().st_mode) == 0o600
    assert stat.S_IMODE(secret_dir.stat().st_mode) == 0o700
    assert "correct horse" not in generated.get_data(as_text=True)
    assert "--consume-admin-password-file" in response["install_arguments"]


def test_admin_secret_endpoint_rejects_csrf_mismatch_and_multiline(tmp_path):
    config = tmp_path / "iis.yaml"
    app = Flask(__name__)
    app.secret_key = "artifact-test"
    IISConfigurationWeb(
        IISConfigurationService(config),
        admin_secret_service=AdminSecretService(tmp_path / "secrets"),
    ).mount_to_app(app, url_prefix="/config")
    client = app.test_client()
    token = _csrf(client.get("/config/setup/?lang=en").get_data(as_text=True))
    body = {
        "username": "admin",
        "password": "first line\nsecond line",
        "confirmation": "first line\nsecond line",
    }

    assert client.post("/config/api/admin-secret", json=body).status_code == 403
    rejected = client.post(
        "/config/api/admin-secret", json=body,
        headers={"X-Config-CSRF": token})
    assert rejected.status_code == 400
    assert not list((tmp_path / "secrets").glob("admin-password-*"))


def test_existing_authentication_database_disables_initial_admin_artifact(tmp_path):
    config = tmp_path / "iis.yaml"
    config.write_text("{}\n", encoding="utf-8")
    config.with_name("Authentication.db").write_bytes(b"existing")
    app = Flask(__name__)
    app.secret_key = "artifact-test"
    IISConfigurationWeb(
        IISConfigurationService(config),
        admin_secret_service=AdminSecretService(tmp_path / "secrets"),
    ).mount_to_app(app, url_prefix="/config")
    client = app.test_client()
    page = client.get("/config/setup/?lang=en").get_data(as_text=True)
    token = _csrf(page)

    assert 'id="adminPassword"' not in page
    rejected = client.post(
        "/config/api/admin-secret",
        json={
            "username": "admin",
            "password": "not-used-password",
            "confirmation": "not-used-password",
        },
        headers={"X-Config-CSRF": token},
    )
    assert rejected.status_code == 400
    assert rejected.get_json()["error_code"] == "authentication_exists"
    assert not list((tmp_path / "secrets").glob("admin-password-*"))


def test_admin_secret_validation_error_is_localized(tmp_path):
    app = Flask(__name__)
    app.secret_key = "artifact-test"
    IISConfigurationWeb(
        IISConfigurationService(tmp_path / "iis.yaml"),
        admin_secret_service=AdminSecretService(tmp_path / "secrets"),
    ).mount_to_app(app, url_prefix="/config")
    client = app.test_client()
    page = client.get("/config/setup/?lang=zh-CN")
    token = _csrf(page.get_data(as_text=True))

    response = client.post(
        "/config/api/admin-secret",
        json={
            "username": "?",
            "password": "valid-long-password",
            "confirmation": "valid-long-password",
        },
        headers={"X-Config-CSRF": token},
    )

    assert response.status_code == 400
    assert response.get_json()["error_code"] == "invalid_username"
    assert "管理员用户名" in response.get_json()["error"]


def test_existing_shared_secret_directory_is_rejected_without_chmod(tmp_path):
    secret_dir = tmp_path / "shared"
    secret_dir.mkdir(mode=0o755)
    secret_dir.chmod(0o755)
    service = AdminSecretService(secret_dir)

    try:
        service.create(
            "admin", "valid-admin-password", "valid-admin-password")
    except ValueError as exc:
        assert getattr(exc, "code", None) == "secret_directory_permissions"
    else:
        raise AssertionError("shared secret directory was unexpectedly accepted")

    assert stat.S_IMODE(secret_dir.stat().st_mode) == 0o755
