import threading

from ServiceComponent.pipeline import (
    ANALYSIS_COMPLETED,
    ANALYSIS_REQUESTED,
    ARCHIVE_COMPLETED,
    ARCHIVE_REQUESTED,
    EventPipelinePlugin,
    INTAKE_ACCEPTED,
    INTAKE_RECEIVED,
    INTAKE_REJECTED,
    PipelineStageFailure,
    StageResult,
)
from ServiceComponent.runtime import HubEvent, HubRuntime


class FakeIntake:
    def accept(self, event):
        if event.payload.get("reject"):
            return StageResult.reject("duplicate")
        return StageResult.allow({"raw": event.payload}, source="fake")


class FakeAnalysis:
    def analyze(self, event):
        return StageResult.allow({"domain_specific": event.payload}, model="fake")


class FakeArchive:
    def archive(self, event):
        return StageResult.allow({"stored": event.payload}, collection="fake")


def _runtime():
    runtime = HubRuntime()
    runtime.install(EventPipelinePlugin(FakeIntake(), FakeAnalysis(), FakeArchive()))
    return runtime


def test_pipeline_keeps_domain_payload_opaque_between_stages():
    runtime = _runtime()
    accepted = []
    analyzed = []
    archived = []

    runtime.subscribe(INTAKE_ACCEPTED, lambda event, _: accepted.append(event))
    runtime.subscribe(ANALYSIS_COMPLETED, lambda event, _: analyzed.append(event))
    runtime.subscribe(ARCHIVE_COMPLETED, lambda event, _: archived.append(event))
    runtime.start()
    runtime.emit(HubEvent(INTAKE_RECEIVED, {"ticker": ["ABC"], "free_form": object()}, "finance"))

    assert runtime.wait_for_idle(timeout=1)
    runtime.stop()
    assert accepted[0].domain_name == "finance"
    assert accepted[0].metadata == {"source": "fake"}
    assert analyzed[0].metadata == {"model": "fake"}
    assert archived[0].payload["stored"]["domain_specific"]["raw"]["ticker"] == ["ABC"]


def test_pipeline_rejection_stops_the_following_stages():
    runtime = _runtime()
    rejected = []
    runtime.subscribe(INTAKE_REJECTED, lambda event, _: rejected.append(event.payload))
    runtime.start()
    runtime.emit(HubEvent(INTAKE_RECEIVED, {"reject": True}, "news"))

    assert runtime.wait_for_idle(timeout=1)
    runtime.stop()
    assert isinstance(rejected[0], PipelineStageFailure)
    assert rejected[0].reason == "duplicate"
    assert runtime.stats["handler_failures"] == 0


def test_postprocess_worker_runs_while_analysis_worker_is_blocked():
    analysis_entered = threading.Event()
    release_analysis = threading.Event()
    archive_finished = threading.Event()
    worker_names = {}

    class BlockingAnalysis(FakeAnalysis):
        def analyze(self, event):
            worker_names["analysis"] = threading.current_thread().name
            analysis_entered.set()
            release_analysis.wait(timeout=1)
            return super().analyze(event)

    class RecordingArchive(FakeArchive):
        def archive(self, event):
            worker_names["postprocess"] = threading.current_thread().name
            archive_finished.set()
            return super().archive(event)

    runtime = HubRuntime(
        worker_count=1,
        worker_groups={"analysis": 1, "postprocess": 1},
    )
    runtime.install(EventPipelinePlugin(FakeIntake(), BlockingAnalysis(), RecordingArchive()))
    runtime.start()
    try:
        runtime.emit(HubEvent(ANALYSIS_REQUESTED, {"slow": True}, "news"))
        assert analysis_entered.wait(timeout=1)

        # analysis worker 仍被占用时，独立 post-process worker 必须可以完成归档。
        runtime.emit(HubEvent(ARCHIVE_REQUESTED, {"ready": True}, "news"))
        assert archive_finished.wait(timeout=1)
    finally:
        release_analysis.set()
        runtime.wait_for_idle(timeout=1)
        runtime.stop()

    assert worker_names["analysis"].startswith("HubRuntime-analysis-")
    assert worker_names["postprocess"].startswith("HubRuntime-postprocess-")


def test_shadow_runs_on_its_own_worker_after_archive_completed():
    order = []
    worker_names = {}

    class AnalysisWithShadow(FakeAnalysis):
        def analyze(self, event):
            return StageResult.allow(
                {"domain_specific": event.payload}, shadow_task={"uuid": "one"})

        def run_shadow(self, event):
            order.append("shadow")
            worker_names["shadow"] = threading.current_thread().name

    class OrderedArchive(FakeArchive):
        def archive(self, event):
            order.append("archive")
            return super().archive(event)

    runtime = HubRuntime(
        worker_count=1,
        worker_groups={"analysis": 1, "postprocess": 1, "shadow": 1},
    )
    runtime.install(EventPipelinePlugin(
        FakeIntake(), AnalysisWithShadow(), OrderedArchive()))
    runtime.start()
    try:
        runtime.emit(HubEvent(ANALYSIS_REQUESTED, {"x": 1}, "industry"))
        assert runtime.wait_for_idle(timeout=1)
    finally:
        runtime.stop()

    assert order == ["archive", "shadow"]
    assert worker_names["shadow"].startswith("HubRuntime-shadow-")


def test_low_value_analysis_can_queue_shadow_without_archive_stage():
    shadowed = []
    archived = []

    class LowValueAnalysis(FakeAnalysis):
        def analyze(self, event):
            # 真实端口在返回 reject 前已经持久化低价值结果。
            return StageResult.reject(
                "non_intelligence", shadow_task={"uuid": "low"})

        def run_shadow(self, event):
            shadowed.append(event.payload)

    class RecordingArchive(FakeArchive):
        def archive(self, event):
            archived.append(event.payload)
            return super().archive(event)

    runtime = HubRuntime(
        worker_count=1,
        worker_groups={"analysis": 1, "postprocess": 1, "shadow": 1},
    )
    runtime.install(EventPipelinePlugin(
        FakeIntake(), LowValueAnalysis(), RecordingArchive()))
    runtime.start()
    try:
        runtime.emit(HubEvent(ANALYSIS_REQUESTED, {"x": 1}, "industry"))
        assert runtime.wait_for_idle(timeout=1)
    finally:
        runtime.stop()

    assert shadowed == [{"uuid": "low"}]
    assert archived == []
