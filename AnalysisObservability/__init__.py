"""可复用的 AI 任务级观测、聚合与 Web 看板。"""

from .store import (
    AnalysisStatsRecorder,
    OUTCOME_FAILED,
    OUTCOME_RETRY_SCHEDULED,
    OUTCOME_SENSITIVE,
    OUTCOME_SUCCESS,
)
from .report import AnalysisReportService

__all__ = [
    "AnalysisStatsRecorder",
    "OUTCOME_FAILED",
    "OUTCOME_RETRY_SCHEDULED",
    "OUTCOME_SENSITIVE",
    "OUTCOME_SUCCESS",
    "AnalysisReportService",
]
