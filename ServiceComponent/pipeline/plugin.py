"""将三个领域端口串接为事件主链路的插件。"""

from __future__ import annotations

from typing import Any

from ServiceComponent.pipeline.contracts import (
    AnalysisPort,
    ArchivePort,
    IntakePort,
    PipelineStageFailure,
    StageResult,
)
from ServiceComponent.pipeline.events import (
    ANALYSIS_COMPLETED,
    ANALYSIS_FAILED,
    ANALYSIS_REQUESTED,
    ARCHIVE_COMPLETED,
    ARCHIVE_FAILED,
    ARCHIVE_REQUESTED,
    INTAKE_ACCEPTED,
    INTAKE_RECEIVED,
    INTAKE_REJECTED,
)
from ServiceComponent.runtime import HubEvent, HubPlugin, HubRuntime


class EventPipelinePlugin(HubPlugin):
    """主链路编排器。

    三个端口可按 subsystem 代理到不同领域实现。插件不导入、也不假设它们的
    payload 结构；只有 ``StageResult.accepted`` 决定事件是否进入下一阶段。
    """

    def __init__(self, intake: IntakePort, analysis: AnalysisPort, archive: ArchivePort):
        self._intake = intake
        self._analysis = analysis
        self._archive = archive

    def register(self, runtime: HubRuntime) -> None:
        runtime.subscribe(INTAKE_RECEIVED, self._on_intake_received)
        runtime.subscribe(INTAKE_ACCEPTED, self._on_intake_accepted)
        runtime.subscribe(ANALYSIS_REQUESTED, self._on_analysis_requested)
        runtime.subscribe(ANALYSIS_COMPLETED, self._on_analysis_completed)
        runtime.subscribe(ARCHIVE_REQUESTED, self._on_archive_requested)

    def _on_intake_received(self, event: HubEvent, runtime: HubRuntime) -> None:
        self._run_stage(
            event, runtime, "intake", self._intake.accept,
            accepted_event=INTAKE_ACCEPTED, rejected_event=INTAKE_REJECTED,
        )

    def _on_analysis_requested(self, event: HubEvent, runtime: HubRuntime) -> None:
        self._run_stage(
            event, runtime, "analysis", self._analysis.analyze,
            accepted_event=ANALYSIS_COMPLETED, rejected_event=ANALYSIS_FAILED,
        )

    @staticmethod
    def _on_intake_accepted(event: HubEvent, runtime: HubRuntime) -> None:
        runtime.emit(event.derive(ANALYSIS_REQUESTED, event.payload))

    @staticmethod
    def _on_analysis_completed(event: HubEvent, runtime: HubRuntime) -> None:
        runtime.emit(event.derive(ARCHIVE_REQUESTED, event.payload))

    def _on_archive_requested(self, event: HubEvent, runtime: HubRuntime) -> None:
        self._run_stage(
            event, runtime, "archive", self._archive.archive,
            accepted_event=ARCHIVE_COMPLETED, rejected_event=ARCHIVE_FAILED,
        )

    @staticmethod
    def _run_stage(event: HubEvent, runtime: HubRuntime, stage: str, handler: Any,
                   *, accepted_event: str, rejected_event: str) -> None:
        try:
            result = handler(event)
            if not isinstance(result, StageResult):
                raise TypeError(f"{stage} 端口必须返回 StageResult。")
        except Exception as exc:
            runtime.emit(event.derive(
                rejected_event,
                payload=PipelineStageFailure(stage, str(exc), type(exc).__name__),
            ))
            return

        if result.accepted:
            runtime.emit(event.derive(
                accepted_event, result.payload, metadata=result.metadata,
            ))
        else:
            runtime.emit(event.derive(
                rejected_event,
                payload=PipelineStageFailure(stage, result.reason),
                metadata=result.metadata,
            ))
