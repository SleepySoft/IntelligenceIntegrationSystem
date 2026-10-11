import datetime
import json
import time

from ServiceComponent.IntelligenceHubDefines_v4 import CollectedDataV4
from Workflow import CollectorSubmission


def _collected(**overrides) -> CollectedDataV4:
    values = {
        "UUID": "test-uuid",
        "token": "test-token",
        "title": "Test article",
        "content": "Long enough article content.",
        "informant": "https://example.test/article",
    }
    values.update(overrides)
    return CollectedDataV4(**values)


def test_submission_serializes_rss_struct_time_as_iso8601(monkeypatch):
    captured = {}

    def fake_post(url, data, timeout):
        captured.update(url=url, data=data, timeout=timeout)
        return {"status": "ok"}

    monkeypatch.setattr(CollectorSubmission, "common_post", fake_post)
    rss_time = time.struct_time((2026, 10, 11, 3, 49, 0, 6, 284, 0))

    result = CollectorSubmission.post_collected_intelligence(
        "http://127.0.0.1:5000", _collected(pub_time=rss_time), timeout=10,
    )

    assert result == {"status": "ok"}
    assert captured["url"] == "http://127.0.0.1:5000/collect"
    assert captured["data"]["pub_time"] == "2026-10-11T03:49:00"
    json.dumps(captured["data"], allow_nan=False)


def test_submission_serializes_explicit_datetimes_with_timezone(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        CollectorSubmission,
        "common_post",
        lambda _url, data, _timeout: captured.setdefault("data", data) or {"status": "ok"},
    )
    collected = _collected(
        pub_time=datetime.datetime(2026, 10, 11, 3, 49, tzinfo=datetime.timezone.utc),
        collect_time=datetime.datetime(2026, 10, 11, 4, 0, tzinfo=datetime.timezone.utc),
    )

    CollectorSubmission.post_collected_intelligence("http://127.0.0.1:5000", collected)

    assert captured["data"]["pub_time"] == "2026-10-11T03:49:00Z"
    assert captured["data"]["collect_time"] == "2026-10-11T04:00:00Z"
    json.dumps(captured["data"], allow_nan=False)
