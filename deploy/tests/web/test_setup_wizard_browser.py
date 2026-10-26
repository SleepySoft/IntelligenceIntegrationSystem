"""真实浏览器测试；与无人值守安装测试分开运行。"""

from __future__ import annotations

import threading

import pytest
import yaml
from flask import Flask
from werkzeug.serving import make_server

from IISConfiguration import IISConfigurationService, IISConfigurationWeb
from IISConfiguration.admin_secret import AdminSecretService


playwright = pytest.importorskip("playwright.sync_api")


def test_setup_wizard_in_real_browser(tmp_path):
    target = tmp_path / "iis.yaml"
    fallback = tmp_path / "example.yaml"
    secret_dir = tmp_path / "runtime-secrets"
    fallback.write_text("{}\n", encoding="utf-8")
    app = Flask(__name__)
    app.secret_key = "browser-test"
    IISConfigurationWeb(
        IISConfigurationService(target, fallback_path=fallback),
        setup_ai_url="/ai-config/",
        admin_secret_service=AdminSecretService(secret_dir),
    ).mount_to_app(app, url_prefix="")
    server = make_server("127.0.0.1", 0, app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with playwright.sync_playwright() as runner:
            try:
                browser = runner.chromium.launch(headless=True)
            except playwright.Error as exc:
                pytest.skip(f"Playwright Chromium 未安装: {exc}")
            page = browser.new_page(locale="en-US")
            page.goto(f"http://127.0.0.1:{server.server_port}/?lang=en")
            page.get_by_role("button", name="Next").click()
            page.get_by_label("Web port").fill("5058")
            page.get_by_label("MongoDB host").fill("mongo.test.internal")
            page.get_by_role("button", name="Next").click()
            page.get_by_role("button", name="Generate token").nth(0).click()
            page.get_by_role("button", name="Generate token").nth(1).click()
            page.get_by_role("button", name="Next").click()
            page.get_by_label("Translate non-Chinese intelligence").uncheck()
            page.get_by_label("Vector search and aggregation").uncheck()
            page.get_by_role("button", name="Next").click()
            page.get_by_label("Administrator password", exact=True).fill(
                "browser-admin-password")
            page.get_by_label("Confirm administrator password").fill(
                "browser-admin-password")
            page.get_by_role("button", name="Validate and save").click()
            page.get_by_text("temporary administrator credential").wait_for()
            command = page.locator("pre").inner_text()
            assert "deploy/iisctl install --non-interactive" in command
            assert "--consume-admin-password-file" in command
            assert str(secret_dir) in command
            assert "admin-password-" in command
            assert "browser-admin-password" not in command
            assert page.get_by_label(
                "Administrator password", exact=True).input_value() == ""
            browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)

    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert data["server"]["port"] == 5058
    assert data["database"]["host"] == "mongo.test.internal"
    assert data["access"]["collector_tokens"]
    assert data["access"]["rpc_tokens"]
    secrets = list(secret_dir.glob("admin-password-*"))
    assert len(secrets) == 1
    secret = secrets[0]
    assert secret.read_text(encoding="utf-8") == "browser-admin-password\n"
    assert secret.stat().st_mode & 0o777 == 0o600
