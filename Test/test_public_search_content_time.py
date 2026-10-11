import datetime

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
