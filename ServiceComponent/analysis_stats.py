"""IIS 兼容入口；实现位于独立的 :mod:`AnalysisObservability` 包。"""

from AnalysisObservability.store import (  # noqa: F401
    AnalysisStatsRecorder,
    OUTCOME_FAILED,
    OUTCOME_RETRY_SCHEDULED,
    OUTCOME_SENSITIVE,
    OUTCOME_SUCCESS,
)

__all__ = [
    "AnalysisStatsRecorder",
    "OUTCOME_FAILED",
    "OUTCOME_RETRY_SCHEDULED",
    "OUTCOME_SENSITIVE",
    "OUTCOME_SUCCESS",
]
