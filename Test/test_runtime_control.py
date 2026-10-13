"""运行控制（RuntimeControlState）与相关端点、pipeline 暂停行为的测试。"""

import threading
import time
from pathlib import Path

import pytest
from flask import Flask
from tenacity import wait_none

from IntelligenceHubWebService import IntelligenceHubWebService, WebServiceAccessManager
from ServiceComponent.runtime.control import RuntimeControlState
from ServiceComponent.adapters import EventV4PipelinePorts
from ServiceComponent.runtime import HubEvent

from test_event_v4_pipeline_adapter import (
    FakeClientManager,
    FakeContext,
    FakeArchiveRepository,
    FakeRegistry,
    FakeScorer,
    _original,
    _valuable,
)


class FakeRSSPublisher:
    pass


class FakeHub:
    default_subsystem_name = "news"

    @property
    def statistics(self):
        return {"runtime": {"emitted": 1, "processed": 1}, "analysis": {}, "subsystems": []}

    def submit_collected_data(self, data):
        return True

    def get_service(self, name, default=None):
        return default

    def get_subsystems(self):
        return []


def _build_app(control: RuntimeControlState):
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).resolve().parent.parent / "templates"),
    )
    app.secret_key = "test-secret"
    access_manager = WebServiceAccessManager(
        rpc_api_tokens=["rpc-token"], collector_tokens=["collector-token"],
        user_manager=None, deny_on_empty_config=True)
    service = IntelligenceHubWebService(
        intelligence_hub=FakeHub(),
        access_manager=access_manager,
        rss_publisher=FakeRSSPublisher(),
        subsystem_registry=None,
        control_state=control,
    )
    service.register_routers(app)
    return app


def _logged_in_client(app):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["username"] = "tester"
    return client


def test_control_state_pause_resume_and_maintenance_umbrella():
    control = RuntimeControlState()
    assert not control.is_paused("intake")
    control.pause("intake", operator="tester")
    assert control.is_paused("intake")
    assert not control.is_paused("analysis")
    control.pause("maintenance")
    assert control.is_paused("analysis")  # 总开关覆盖所有 scope
    control.resume("maintenance")
    assert not control.is_paused("analysis")
    assert control.is_paused("intake")    # 单独暂停不受总开关恢复影响
    with pytest.raises(ValueError):
        control.pause("not-a-scope")
    status = control.status()
    assert status["scopes"]["intake"] is True
    assert status["scopes"]["analysis"] is False
    assert status["maintenance"] is False


def test_control_endpoints_and_intake_gate():
    control = RuntimeControlState()
    app = _build_app(control)
    client = _logged_in_client(app)

    status = client.get("/api/control/status").get_json()
    assert status["maintenance"] is False

    resp = client.post("/api/control/pause", json={"scope": "intake"})
    assert resp.status_code == 200
    assert resp.get_json()["status"]["scopes"]["intake"] is True

    # intake 暂停时 /collect 拒绝接收（503），爬虫可稍后重试。
    resp = client.post("/collect", json={"UUID": "u-1", "token": "collector-token"})
    assert resp.status_code == 503
    assert resp.get_json()["resp"] == "maintenance"

    resp = client.post("/api/control/resume", json={"scope": "intake"})
    assert resp.status_code == 200
    resp = client.post("/collect", json={"UUID": "u-1", "token": "collector-token"})
    assert resp.status_code == 200
    assert resp.get_json()["resp"] == ["queued"]  # 既有实现返回的是单元素列表

    assert client.post("/api/control/pause", json={"scope": "bad"}).status_code == 400
    # 未登录不可访问控制端点
    anon = app.test_client()
    assert anon.get("/api/control/status").status_code == 302


def test_control_panel_page_renders():
    app = _build_app(RuntimeControlState())
    client = _logged_in_client(app)
    resp = client.get("/control")
    assert resp.status_code == 200
    assert "运行控制" in resp.get_data(as_text=True)


def test_runtime_status_endpoint_returns_console_text():
    app = _build_app(RuntimeControlState())
    client = _logged_in_client(app)
    data = client.get("/api/runtime/status").get_json()
    assert "status_text" in data
    assert "HUB STATUS" in data["status_text"]
    assert data["hub"]["runtime"]["emitted"] == 1


def test_analysis_pause_holds_worker_without_consuming_ai():
    control = RuntimeControlState()
    control.pause("analysis", operator="test")
    calls = []

    def analyzer(client, prompt, original):
        calls.append(prompt)
        return _valuable()

    ports = EventV4PipelinePorts(
        FakeRegistry(FakeContext()), FakeClientManager(),
        archive_repository=FakeArchiveRepository(),
        prompt_table={41: "v41 prompt"},
        analyzer=analyzer,
        scorer_factory=lambda _: FakeScorer(),
        retry_wait=wait_none(),
        control_state=control,
    )

    result_box = {}

    def run():
        result_box["result"] = ports.analyze(
            HubEvent("analysis.requested", _original(), "news"))

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    time.sleep(0.5)
    # 暂停期间：分析不执行、AI 未被调用。
    assert worker.is_alive()
    assert not calls
    control.resume("analysis", operator="test")
    worker.join(timeout=10)
    assert not worker.is_alive()
    assert result_box["result"].accepted
    assert len(calls) == 1
    ports.stop()
