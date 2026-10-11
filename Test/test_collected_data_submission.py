import datetime
import json
import time
import pytest

from ServiceComponent.IntelligenceHubDefines_v4 import CollectedData
from Workflow import CollectorSubmission


EXPECTED_FIELDS = [
    "UUID", "token", "source", "target", "prompt", "title", "authors",
    "content", "pub_time", "collect_time", "informant", "subsystem", "temp_data",
]


def _collected(**overrides) -> CollectedData:
    values = {
        "UUID": "test-uuid",
        "token": "test-token",
        "title": "Test article",
        "content": "Long enough article content.",
        "informant": "https://example.test/article",
    }
    values.update(overrides)
    return CollectedData(**values)


def test_collected_data_keeps_the_original_input_contract():
    fields = CollectedData.model_fields

    assert CollectedData.__name__ == "CollectedData"
    assert list(fields) == EXPECTED_FIELDS
    assert {name for name, field in fields.items() if field.is_required()} == {
        "UUID", "token", "title", "content", "informant",
    }
    assert fields["authors"].default_factory is list
    assert fields["collect_time"].default_factory.__name__ == "_utc_now"
    assert fields["temp_data"].default_factory is dict
    assert fields["subsystem"].default == ""
    assert CollectedData.model_config["extra"] == "ignore"
    assert _collected().collect_time.tzinfo == datetime.timezone.utc


def test_collected_data_rejects_blank_informant():
    with pytest.raises(ValueError, match="informant/source URL cannot be empty"):
        _collected(informant="   ")


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
