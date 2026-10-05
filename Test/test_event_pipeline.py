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
    assert accepted[0].subsystem == "finance"
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
