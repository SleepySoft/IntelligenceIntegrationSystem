"""分析错误分类与 IIS 侧统计的单元测试。"""
import time

from pydantic import ValidationError

from ServiceComponent.analysis_errors import (
    AnalysisErrorClass, RETRY_ANY, RETRY_SAME_MODEL, TERMINAL,
    classify_analysis_error, retry_action_for,
)
from ServiceComponent.analysis_stats import (
    OUTCOME_SENSITIVE, OUTCOME_SUCCESS, AnalysisStatsRecorder,
)
from ServiceComponent.IntelligenceHubDefines_v4 import AnalysisResultV4


def _validation_error():
    try:
        AnalysisResultV4.model_validate({"TAXONOMY": 123})
    except ValidationError as exc:
        return exc
    raise AssertionError("应当抛出 ValidationError")


class TestClassify:
    def test_call_errors(self):
        assert classify_analysis_error("call", TimeoutError()) == AnalysisErrorClass.NETWORK
        assert classify_analysis_error("call", ConnectionError()) == AnalysisErrorClass.NETWORK
        assert classify_analysis_error("call", RuntimeError("x")) == AnalysisErrorClass.UNKNOWN

    def test_api_errors(self):
        assert classify_analysis_error(
            "api", None, {"api_error_code": "CONTENT_SAFETY"}) == AnalysisErrorClass.CONTENT_SAFETY
        assert classify_analysis_error(
            "api", None, {"api_error_code": "HTTP_429"}) == AnalysisErrorClass.RATE_LIMIT
        assert classify_analysis_error(
            "api", None, {"api_error_code": "HTTP_400"}) == AnalysisErrorClass.PROVIDER_FATAL
        assert classify_analysis_error(
            "api", None, {"api_error_code": "HTTP_500"}) == AnalysisErrorClass.NETWORK

    def test_validation_errors(self):
        assert classify_analysis_error(
            "validation", _validation_error()) == AnalysisErrorClass.SCHEMA_SHAPE
        assert classify_analysis_error(
            "validation", ValueError("event E1 has unsupported phase qualifier: completed")
        ) == AnalysisErrorClass.SEMANTIC_RULE

    def test_retry_policy(self):
        assert retry_action_for(AnalysisErrorClass.SCHEMA_SHAPE) == RETRY_SAME_MODEL
        assert retry_action_for(AnalysisErrorClass.SEMANTIC_RULE) == RETRY_SAME_MODEL
        assert retry_action_for(AnalysisErrorClass.CONTENT_SAFETY) == TERMINAL
        assert retry_action_for(AnalysisErrorClass.RATE_LIMIT) == RETRY_ANY


class TestAnalysisStats:
    def test_record_and_aggregate(self, tmp_path):
        recorder = AnalysisStatsRecorder(tmp_path / "stats.db")
        # 模型A：2 件成功（一件两次过）、1 件敏感
        recorder.record_attempt(uuid="a1", subsystem="news", shadow=False,
                                prompt_version=46, ai_service="svc", ai_model="modelA",
                                attempt_no=1, latency_ms=800, error_class=None)
        recorder.record_outcome(uuid="a1", subsystem="news", shadow=False,
                                prompt_version=46, ai_service="svc", ai_model="modelA",
                                outcome=OUTCOME_SUCCESS, attempts_used=1)
        recorder.record_attempt(uuid="a2", subsystem="news", shadow=False,
                                prompt_version=46, ai_service="svc", ai_model="modelA",
                                attempt_no=1, latency_ms=900,
                                error_class=AnalysisErrorClass.SCHEMA_SHAPE)
        recorder.record_attempt(uuid="a2", subsystem="news", shadow=False,
                                prompt_version=46, ai_service="svc", ai_model="modelA",
                                attempt_no=2, latency_ms=950, error_class=None)
        recorder.record_outcome(uuid="a2", subsystem="news", shadow=False,
                                prompt_version=46, ai_service="svc", ai_model="modelA",
                                outcome=OUTCOME_SUCCESS, attempts_used=2)
        recorder.record_attempt(uuid="a3", subsystem="news", shadow=False,
                                prompt_version=46, ai_service="svc", ai_model="modelA",
                                attempt_no=1, latency_ms=700,
                                error_class=AnalysisErrorClass.CONTENT_SAFETY)
        recorder.record_outcome(uuid="a3", subsystem="news", shadow=False,
                                prompt_version=46, ai_service="svc", ai_model="modelA",
                                outcome=OUTCOME_SENSITIVE, attempts_used=1)

        groups = recorder.aggregate_by_model(days=1)
        assert len(groups) == 1
        g = groups[0]
        assert g["ai_model"] == "modelA"
        assert g["items"] == 3
        assert g["success_rate"] == round(2 / 3, 4)
        assert g["sensitive_rate"] == round(1 / 3, 4)
        assert g["attempts_per_item"] == round(4 / 3, 2)
        assert g["errors"] == {"schema_shape": 1, "content_safety": 1}
        assert g["first_pass_rate"] == round(1 - 2 / 4, 4)  # 4 次调用 2 次首试出错

    def test_disabled_recorder_is_noop(self, tmp_path):
        recorder = AnalysisStatsRecorder(tmp_path / "s.db", enabled=False)
        recorder.record_attempt(uuid="x", subsystem="news", shadow=False,
                                prompt_version=46, ai_service="s", ai_model="m",
                                attempt_no=1, latency_ms=1, error_class=None)
        assert recorder.aggregate_by_model() == []
