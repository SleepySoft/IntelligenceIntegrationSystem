import datetime
from contextlib import contextmanager
from types import SimpleNamespace

from flask import Flask

from IntelligenceHubWebService import (
    DEFAULT_PUBLIC_SEARCH_LIMITS,
    IntelligenceHubWebService,
)


def test_public_mongo_search_uses_content_time_window_only(monkeypatch):
    fixed_now = datetime.datetime(2026, 10, 11, 12, tzinfo=datetime.timezone.utc)
    monkeypatch.setattr("IntelligenceHubWebService.get_aware_time", lambda: fixed_now)
    service = IntelligenceHubWebService.__new__(IntelligenceHubWebService)
    service.public_search_limits = dict(DEFAULT_PUBLIC_SEARCH_LIMITS)
    params = {
        "search_mode": "mongo",
        "page": 1,
        "per_page": 10,
        "start_time": "",
        "end_time": "",
        "archive_start_time": "2000-01-01T00:00:00",
        "archive_end_time": "2000-01-02T00:00:00",
    }

    error = service._apply_public_search_limits(params, "127.0.0.1")

    assert error is None
    assert params["start_time"] == "2026-09-11T12:00:00"
    assert params["end_time"] == "2026-10-11T12:00:00"
    assert params["archive_start_time"] == ""
    assert params["archive_end_time"] == ""


def test_list_sort_parameter_is_allowlisted_and_defaults_to_archive_time():
    service = IntelligenceHubWebService.__new__(IntelligenceHubWebService)
    app = Flask(__name__)

    with app.test_request_context("/?sort_by=content_time"):
        assert service._get_combined_params()["sort_by"] == "content_time"
    with app.test_request_context("/?sort_by=APPENDIX.__RAW_DATA__"):
        assert service._get_combined_params()["sort_by"] == "archived_at"


def test_public_query_returns_effective_clamped_pagination():
    class PerfLogger:
        @contextmanager
        def timed(self, *_args, **_kwargs):
            yield

        def record(self, *_args, **_kwargs):
            pass

    service = IntelligenceHubWebService.__new__(IntelligenceHubWebService)
    service.public_search_limits = dict(DEFAULT_PUBLIC_SEARCH_LIMITS)
    service._perf_logger = PerfLogger()
    service._get_client_ip = lambda: "127.0.0.1"
    service._perform_search_logic = lambda params, subsystem: {
        "results": [{"UUID": "one"}], "total": 100,
    }
    service.intelligence_hub = SimpleNamespace(default_domain_name="news")
    app = Flask(__name__)
    app.secret_key = "test"
    params = {
        "search_mode": "mongo", "page": 2, "per_page": 100,
        "start_time": "", "end_time": "", "archive_start_time": "",
        "archive_end_time": "",
    }

    with app.test_request_context("/intelligences/query"):
        response = service._query_api_with_params(params)
        payload = response.get_json()

    assert payload["page"] == 2
    assert payload["per_page"] == DEFAULT_PUBLIC_SEARCH_LIMITS["mongo_max_per_page"]
