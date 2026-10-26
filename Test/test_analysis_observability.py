from flask import Flask

from AIClientCenter.core.observation import CallObservationSQLiteLogger
from AnalysisObservability import AnalysisReportService, AnalysisStatsRecorder
from AnalysisObservability.store import OUTCOME_SUCCESS
from AnalysisObservability.web import create_blueprint


def _call(observer, request_id, *, elapsed, cached=0):
    observer.record({
        "type": "chat_end", "request_id": request_id,
        "started_at": 100.0, "ts": 101.0,
        "provider": "test-provider", "model": "model-a",
        "client_name": "named-a", "success": True,
        "elapsed_ms": elapsed, "reported_input_tokens": 100,
        "reported_output_tokens": 20, "reported_total_tokens": 120,
        "cached_input_tokens": cached, "uncached_input_tokens": 100 - cached,
        "token_source": "reported",
    })


def _fixture_data(tmp_path):
    stats_path = tmp_path / "stats.db"
    calls_path = tmp_path / "calls.db"
    stats = AnalysisStatsRecorder(stats_path)
    calls = CallObservationSQLiteLogger(str(calls_path))
    _call(calls, "r1", elapsed=1000, cached=50)
    _call(calls, "r2", elapsed=2000, cached=0)
    _call(calls, "r3", elapsed=1000, cached=50)

    stats.record_attempt(
        uuid="one", domain_name="news", shadow=False, prompt_version=50,
        ai_service="svc", ai_model="model-a", attempt_no=1, latency_ms=1000,
        error_class=None, request_id="r1", client_name="named-a")
    stats.record_outcome(
        uuid="one", domain_name="news", shadow=False, prompt_version=50,
        ai_service="svc", ai_model="model-a", outcome=OUTCOME_SUCCESS,
        attempts_used=1)
    stats.record_attempt(
        uuid="two", domain_name="news", shadow=False, prompt_version=50,
        ai_service="svc", ai_model="model-a", attempt_no=1, latency_ms=2000,
        error_class="schema_shape", request_id="r2", client_name="named-a")
    stats.record_attempt(
        uuid="two", domain_name="news", shadow=False, prompt_version=50,
        ai_service="svc", ai_model="model-a", attempt_no=2, latency_ms=1000,
        error_class=None, request_id="r3", client_name="named-a")
    stats.record_outcome(
        uuid="two", domain_name="news", shadow=False, prompt_version=50,
        ai_service="svc", ai_model="model-a", outcome=OUTCOME_SUCCESS,
        attempts_used=2)
    return stats_path, calls_path


def test_report_joins_task_and_call_observations(tmp_path):
    stats_path, calls_path = _fixture_data(tmp_path)
    report = AnalysisReportService(stats_path, calls_path).report(hours=24)
    overview = report["overview"]
    assert overview["started_items"] == 2
    assert overview["success_items"] == 2
    assert overview["first_pass_rate"] == 0.5
    assert overview["result_error_rate"] == 0.5
    assert overview["recovered_items"] == 1
    assert overview["calls"] == 3
    assert overview["linked_calls"] == 3
    assert overview["link_coverage"] == 1.0
    assert overview["total_tokens"] == 360
    assert overview["cache_token_rate"] == round(100 / 300, 4)
    assert overview["completions_per_100_calls"] == round(200 / 3, 4)
    assert overview["busy_productivity"] == 1800.0
    assert report["errors"][0]["kind"] == "result"


def test_blueprint_is_standalone_and_exposes_json(tmp_path):
    stats_path, calls_path = _fixture_data(tmp_path)
    app = Flask(__name__)
    app.register_blueprint(create_blueprint(
        analysis_db_path=stats_path, call_db_path=calls_path), url_prefix="/quality")
    client = app.test_client()

    page = client.get("/quality/")
    assert page.status_code == 200
    assert "AI 分析质量与成本" in page.get_data(as_text=True)
    response = client.get("/quality/api/report?hours=24&dimension=client")
    assert response.status_code == 200
    assert response.get_json()["comparison"][0]["name"] == "named-a"
    assert client.get("/quality/api/report?dimension=bad").status_code == 400
