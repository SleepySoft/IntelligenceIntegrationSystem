from uuid import uuid5

from ServiceComponent.intelligence_contracts import (
    CACHE_ARCHIVED_FLAG_FIELD,
    ENTITY_ID_NAMESPACE,
    INTELLIGENCE_ARCHIVE_SCHEMA_VERSION,
    CacheArchiveState,
    ContentTimeSource,
    EventV4CollectionSuffix,
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


def test_entity_namespace_keeps_existing_entity_ids_stable():
    assert str(uuid5(ENTITY_ID_NAMESPACE, "organization|CN|示例机构")) == (
        "9089da61-6fe2-5e33-8493-107c0716a8e5"
    )
