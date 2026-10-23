"""领域无关的收集、分析和归档流程插件。"""

from ServiceComponent.pipeline.contracts import PipelineStageFailure, StageResult
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
from ServiceComponent.pipeline.plugin import EventPipelinePlugin

__all__ = [
    "ANALYSIS_COMPLETED", "ANALYSIS_FAILED", "ANALYSIS_REQUESTED",
    "ARCHIVE_COMPLETED", "ARCHIVE_FAILED", "ARCHIVE_REQUESTED",
    "EventPipelinePlugin", "INTAKE_ACCEPTED", "INTAKE_RECEIVED", "INTAKE_REJECTED",
    "PipelineStageFailure", "StageResult",
]
