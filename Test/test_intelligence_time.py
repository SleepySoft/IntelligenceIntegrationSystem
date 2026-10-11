import datetime

from ServiceComponent.intelligence_time import (
    document_content_time,
    normalize_intelligence_datetime,
    resolve_content_time,
)
from ServiceComponent.intelligence_contracts import (
    APPENDIX_CONTENT_TIME, APPENDIX_CONTENT_TIME_SOURCE, APPENDIX_RAW_DATA,
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


def test_content_time_uses_collection_when_publication_only_has_a_date():
    collected = datetime.datetime(2026, 10, 10, 9, tzinfo=datetime.timezone.utc)

    value, source = resolve_content_time(
        {"pub_time": "2026-10-10", "collect_time": collected},
        datetime.datetime(2026, 10, 10, 10, tzinfo=datetime.timezone.utc),
    )

    assert value == collected
    assert source == "collected"


def test_content_time_treats_midnight_publication_as_date_only():
    collected = datetime.datetime(2026, 10, 10, 9, tzinfo=datetime.timezone.utc)

    value, source = resolve_content_time(
        {
            "pub_time": datetime.datetime(2026, 10, 10, tzinfo=datetime.timezone.utc),
            "collect_time": collected,
        },
        datetime.datetime(2026, 10, 10, 10, tzinfo=datetime.timezone.utc),
    )

    assert value == collected
    assert source == "collected"


def test_content_time_uses_archive_as_last_resort():
    archived = datetime.datetime(2026, 10, 10, 10, tzinfo=datetime.timezone.utc)

    assert resolve_content_time({}, archived) == (archived, "archived")


def test_document_content_time_falls_back_to_appendix_raw_data():
    collected = datetime.datetime(2026, 10, 10, 9, tzinfo=datetime.timezone.utc)

    assert document_content_time({"APPENDIX": {APPENDIX_RAW_DATA: {"collect_time": collected}}}) == collected


def test_document_content_time_repairs_materialized_midnight_publication():
    published = datetime.datetime(2026, 10, 10, tzinfo=datetime.timezone.utc)
    collected = datetime.datetime(2026, 10, 10, 9, tzinfo=datetime.timezone.utc)

    assert document_content_time({
        "APPENDIX": {
            APPENDIX_CONTENT_TIME: published,
            APPENDIX_CONTENT_TIME_SOURCE: "published",
            APPENDIX_RAW_DATA: {"pub_time": published, "collect_time": collected},
        },
    }) == collected


def test_numeric_timestamp_is_normalized_to_utc():
    value = normalize_intelligence_datetime(0)

    assert value == datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)
