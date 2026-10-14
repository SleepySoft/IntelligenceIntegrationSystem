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


class FakeCache:
    """最小心存 cache：仅支持弃用端点使用的 update(query, data)。"""

    def __init__(self, records):
        self.records = records

    def update(self, query, update):
        matched = 0
        for record in self.records:
            appendix = record.setdefault("APPENDIX", {})
            if "__ARCHIVED__" in appendix:
                continue
            matched += 1
            for key, value in update.items():
                appendix[key.split(".")[-1]] = value
        return matched, matched


class FakeSubsystemRegistry:
    def __init__(self, contexts):
        self._contexts = contexts

    def enabled_subsystems(self):
        return self._contexts


def _build_app(control: RuntimeControlState, subsystem_registry=None):
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
        subsystem_registry=subsystem_registry,
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


def test_discard_pending_marks_unanalyzed_cache_deprecated():
    from types import SimpleNamespace

    cache = FakeCache([
        {"UUID": "u1", "APPENDIX": {}},                              # 未分析 → 弃用
        {"UUID": "u2", "APPENDIX": {}},                              # 未分析 → 弃用
        {"UUID": "u3", "APPENDIX": {"__ARCHIVED__": "A"}},           # 已归档 → 不动
        {"UUID": "u4", "APPENDIX": {"__ARCHIVED__": "D"}},           # 已终态 → 不动
    ])
    registry = FakeSubsystemRegistry([SimpleNamespace(
        name="news", mongo_db_cache=cache,
        url_prefix="/news", is_default=True, ui_plugin_enabled=False,
    )])
    app = _build_app(RuntimeControlState(), subsystem_registry=registry)
    client = _logged_in_client(app)

    resp = client.post("/api/control/discard_pending")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["deprecated_total"] == 2
    assert data["details"]["news"]["deprecated"] == 2
    flags = {r["UUID"]: r["APPENDIX"].get("__ARCHIVED__") for r in cache.records}
    assert flags == {"u1": "X", "u2": "X", "u3": "A", "u4": "D"}

    # 弃用后这些记录不再被重放查询命中（unarchived_cache_query 只认无标志记录）。
    from ServiceComponent.intelligence_contracts import unarchived_cache_query
    assert unarchived_cache_query()  # 查询存在
    remaining = [r for r in cache.records if "__ARCHIVED__" not in r["APPENDIX"]]
    assert remaining == []
