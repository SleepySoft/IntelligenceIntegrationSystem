"""AI Client 配置元数据的真实浏览器测试。"""

from __future__ import annotations

import threading

import pytest
from flask import Flask
from werkzeug.serving import make_server

from AIClientCenter.configuration import AIClientConfigurationService
from AIClientCenter.web.configuration import AIClientConfigurationWeb


playwright = pytest.importorskip("playwright.sync_api")


def test_ai_factory_help_tracks_selected_factory(tmp_path):
    config = tmp_path / "ai_clients.yaml"
    config.write_text(
        "clients:\n"
        "- factory: openai_compatible\n"
        "  name: demo\n"
        "  group: demo\n"
        "  enable: false\n"
        "  api_base_url: https://example.invalid/v1\n"
        "  model: demo-model\n"
        "group_limits: {}\n",
        encoding="utf-8",
    )
    app = Flask(__name__)
    app.secret_key = "browser-test"
    AIClientConfigurationWeb(
        AIClientConfigurationService(config)).mount_to_app(app, url_prefix="")
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
            page.locator("details.config-help").first.wait_for()
            assert page.locator("details.config-help").count() >= 7
            parameter_help = page.locator("[data-factory-help] details")
            parameter_help.locator("summary").click()
            assert "api_base_url" in parameter_help.locator(
                ".config-help-text").inner_text()
            assert "Provider model identifier" in parameter_help.locator(
                ".config-help-text").inner_text()
            browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
