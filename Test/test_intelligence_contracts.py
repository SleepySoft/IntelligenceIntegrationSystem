from ServiceComponent.intelligence_contracts import (
    APPENDIX_ARCHIVED_FLAG,
    APPENDIX_RAW_DATA,
    CACHE_ARCHIVED_FLAG_FIELD,
    INTELLIGENCE_ARCHIVE_SCHEMA_VERSION,
    CacheArchiveState,
    ContentTimeSource,
    EventV4CollectionSuffix,
    appendix_path,
    unarchived_cache_query,
)


def test_shared_contract_values_are_stable():
    assert INTELLIGENCE_ARCHIVE_SCHEMA_VERSION == "iis-intelligence/4.0"
    assert ContentTimeSource.PUBLISHED == "published"
    assert CacheArchiveState.ARCHIVED == "A"
    assert EventV4CollectionSuffix.ARCHIVED == "v4_archived"


def test_cache_replay_query_uses_the_canonical_archive_field():
    assert unarchived_cache_query() == {"$and": [
        {"__ARCHIVED__": {"$exists": False}},
        {CACHE_ARCHIVED_FLAG_FIELD: {"$exists": False}},
    ]}


def test_appendix_keys_and_paths_are_centralized():
    assert APPENDIX_RAW_DATA == "RAW_DATA"
    assert CACHE_ARCHIVED_FLAG_FIELD == appendix_path(APPENDIX_ARCHIVED_FLAG)
    assert EventV4CollectionSuffix.EVENTS == "v4_events"
    assert not hasattr(EventV4CollectionSuffix, "ENTITIES")
