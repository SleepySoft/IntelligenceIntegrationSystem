import time

from ServiceComponent.manual_debug_analysis import (
    MANUAL_TEST_SOURCE,
    ManualDebugAnalysisService,
)
from ServiceComponent.pipeline import StageResult
from ServiceComponent.runtime import HubRuntime


class Registry:
    default_name = "news"

    def resolve(self, name):
        return object() if name == "news" else None


class TransientPort:
    def __init__(self):
        self.events = []

    def analyze_transient(self, event):
        self.events.append(event)
        return StageResult.allow({"echo": event.payload["content"]})


def _wait_finished(service, job_id):
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        record = service.get(job_id)
        if record["status"] in {"completed", "failed"}:
            return record
        time.sleep(0.005)
    raise AssertionError("debug analysis did not finish")


def test_manual_debug_service_autofills_collected_data_and_keeps_result_in_memory():
    port = TransientPort()
    service = ManualDebugAnalysisService(port, Registry(), max_results=5)
    runtime = HubRuntime()
    runtime.install(service)
    runtime.start()

    submitted = service.submit(content="# 自动标题\n这是一段用于人工分析调试的正文内容。")
    finished = _wait_finished(service, submitted["job_id"])
    runtime.stop()

    assert finished["status"] == "completed"
    assert finished["result"]["echo"].startswith("# 自动标题")
    assert finished["input"]["title"] == "自动标题"
    assert finished["input"]["source"] == MANUAL_TEST_SOURCE
    assert finished["input"]["informant"] == f"test://manual/{submitted['job_id']}"
    assert finished["input"]["temp_data"]["transient"] is True
    summary = service.list()[0]
    assert summary["job_id"] == submitted["job_id"]
    assert "content" not in summary["input"]
    assert "result" not in summary


def test_manual_debug_service_rejects_short_content_and_unknown_subsystem():
    service = ManualDebugAnalysisService(TransientPort(), Registry())
    runtime = HubRuntime()
    runtime.install(service)
    runtime.start()
    try:
        try:
            service.submit(content="short")
        except ValueError as exc:
            assert "10" in str(exc)
        else:
            raise AssertionError("short content should be rejected")

        try:
            service.submit(content="这是一段长度足够的正文内容。", subsystem="missing")
        except ValueError as exc:
            assert "未知子系统" in str(exc)
        else:
            raise AssertionError("unknown subsystem should be rejected")
    finally:
        runtime.stop()
