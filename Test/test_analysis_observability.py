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
    assert overview["cache_observed_calls"] == 3
    assert overview["cache_unreported_calls"] == 0
    assert overview["cache_observation_coverage"] == 1.0
    assert overview["cold_start_calls"] == 1
    assert overview["cold_start_rate"] == round(1 / 3, 4)
    assert overview["cache_hit_calls"] == 2
    assert overview["cache_hit_call_rate"] == round(2 / 3, 4)
    assert overview["average_cached_tokens_per_hit"] == 50.0
    assert overview["completions_per_100_calls"] == round(200 / 3, 4)
    assert overview["busy_productivity"] == 1800.0
    assert report["errors"][0]["kind"] == "result"
    assert report["series"][0]["cold_start_calls"] == 1
    assert report["recent_attempts"][0]["cache_status"] == "hit"


def test_cache_unreported_call_is_not_counted_as_cold_start(tmp_path):
    stats_path = tmp_path / "stats.db"
    calls_path = tmp_path / "calls.db"
    stats = AnalysisStatsRecorder(stats_path)
    calls = CallObservationSQLiteLogger(str(calls_path))
    calls.record({
        "type": "chat_end", "request_id": "unreported",
        "started_at": 100.0, "ts": 101.0,
        "provider": "test-provider", "model": "model-a",
        "client_name": "named-a", "success": True,
        "elapsed_ms": 1000, "reported_input_tokens": 100,
        "reported_output_tokens": 20, "reported_total_tokens": 120,
        "token_source": "reported",
    })
    stats.record_attempt(
        uuid="unreported-item", domain_name="industry", shadow=False,
        prompt_version=1, ai_service="svc", ai_model="model-a",
        attempt_no=1, latency_ms=1000, error_class=None,
        request_id="unreported", client_name="named-a")
    stats.record_outcome(
        uuid="unreported-item", domain_name="industry", shadow=False,
        prompt_version=1, ai_service="svc", ai_model="model-a",
        outcome=OUTCOME_SUCCESS, attempts_used=1)

    report = AnalysisReportService(stats_path, calls_path).report(
        hours=24, dimension="service_model_prompt")
    overview = report["overview"]
    assert overview["cache_observed_calls"] == 0
    assert overview["cache_unreported_calls"] == 1
    assert overview["cache_observation_coverage"] == 0.0
    assert overview["cold_start_calls"] == 0
    assert overview["cold_start_rate"] is None
    assert report["comparison"][0]["name"] == (
        "test-provider · model-a · industry · v1")
    assert report["recent_attempts"][0]["cache_status"] == "unreported"


def test_blueprint_is_standalone_and_exposes_json(tmp_path):
    stats_path, calls_path = _fixture_data(tmp_path)
    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.register_blueprint(create_blueprint(
        analysis_db_path=stats_path, call_db_path=calls_path), url_prefix="/quality")
    client = app.test_client()

    page = client.get("/quality/")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "AI 分析质量与成本" in html
    assert 'value="service_model_prompt" selected' in html
    assert "服务商 × 模型 × 领域 × Prompt" in html
    assert "效率、缓存与成本" in html
    assert "cold_start_count:" in html
    assert "cache_observation_coverage:" in html
    assert page.headers["Content-Language"] == "zh-CN"
    english_page = client.get("/quality/?lang=en")
    english_html = english_page.get_data(as_text=True)
    assert '<html lang="en">' in english_html
    assert "Efficiency, cache, and cost" in english_html
    assert "Cold starts" in english_html
    assert english_page.headers["Content-Language"] == "en"
    default_report = client.get("/quality/api/report?hours=24")
    assert default_report.status_code == 200
    assert default_report.get_json()["dimension"] == "service_model_prompt"
    response = client.get("/quality/api/report?hours=24&dimension=client")
    assert response.status_code == 200
    assert response.get_json()["comparison"][0]["name"] == "named-a"
    assert client.get("/quality/api/report?dimension=bad").status_code == 400
