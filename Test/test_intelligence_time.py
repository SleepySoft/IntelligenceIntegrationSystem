import datetime

from ServiceComponent.intelligence_time import (
    document_content_time,
    normalize_intelligence_datetime,
    resolve_content_time,
)


def test_content_time_prefers_publication_over_collection_and_archive():
    published = "2026-10-10T08:00:00Z"
    collected = datetime.datetime(2026, 10, 10, 9, tzinfo=datetime.timezone.utc)
    archived = datetime.datetime(2026, 10, 10, 10, tzinfo=datetime.timezone.utc)

    value, source = resolve_content_time(
        {"pub_time": published, "collect_time": collected}, archived)

    assert value == datetime.datetime(2026, 10, 10, 8, tzinfo=datetime.timezone.utc)
    assert source == "published"


def test_content_time_uses_collection_when_publication_is_invalid():
    collected = datetime.datetime(2026, 10, 10, 9, tzinfo=datetime.timezone.utc)

    value, source = resolve_content_time(
        {"pub_time": "unknown", "collect_time": collected},
        datetime.datetime(2026, 10, 10, 10, tzinfo=datetime.timezone.utc),
    )

    assert value == collected
    assert source == "collected"


def test_content_time_uses_archive_as_last_resort():
    archived = datetime.datetime(2026, 10, 10, 10, tzinfo=datetime.timezone.utc)

    assert resolve_content_time({}, archived) == (archived, "archived")


def test_document_content_time_supports_old_documents_without_materialized_field():
    collected = datetime.datetime(2026, 10, 10, 9, tzinfo=datetime.timezone.utc)

    assert document_content_time({"raw_data": {"collect_time": collected}}) == collected


def test_numeric_timestamp_is_normalized_to_utc():
    value = normalize_intelligence_datetime(0)

    assert value == datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)
