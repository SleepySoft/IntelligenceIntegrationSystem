from datetime import datetime, timezone

from ServiceComponent.EventV4QueryEngine import EventV4QueryEngine


class FakeCursor(list):
    def sort(self, *args, **kwargs):
        return self

    def skip(self, count):
        return FakeCursor(self[count:])

    def limit(self, count):
        return FakeCursor(self[:count])


class RecordingCollection:
    def __init__(self, documents=()):
        self.documents = list(documents)
        self.queries = []

    def find_one(self, query, projection=None, sort=None):
        values = list(self.find(query, projection))
        return values[0] if values else None

    def find(self, query, projection=None):
        self.queries.append(query)
        values = self.documents
        identifiers = query.get("_id", {}).get("$in") if isinstance(query.get("_id"), dict) else None
        if identifiers is not None:
            values = [item for item in values if item["_id"] in identifiers]
        if projection:
            included = [key for key, enabled in projection.items() if enabled]
            if included:
                values = [{key: item[key] for key in included if key in item} for item in values]
        return FakeCursor(dict(item) for item in values)

    def count_documents(self, query):
        self.queries.append(query)
        return len(self.documents)

    def aggregate(self, pipeline):
        return []


def test_v4_query_engine_preserves_requested_identifier_order():
    intelligence = RecordingCollection([
        {"_id": "b", "archived_at": datetime.now(timezone.utc)},
        {"_id": "a", "archived_at": datetime.now(timezone.utc)},
    ])
    engine = EventV4QueryEngine(
        intelligence, RecordingCollection(), RecordingCollection())

    result = engine.get_intelligence(["a", "b"])

    assert [item["_id"] for item in result] == ["a", "b"]


def test_v4_query_engine_builds_native_nested_field_filters():
    intelligence = RecordingCollection([{"_id": "a"}])
    engine = EventV4QueryEngine(
        intelligence, RecordingCollection(), RecordingCollection())

    result, total = engine.query_intelligence(
        keywords="能源 安全",
        threshold=6,
        archive_period=(
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 2, 1, tzinfo=timezone.utc),
        ),
    )

    query = intelligence.queries[0]
    assert total == 1
    assert len(result) == 1
    assert {"total_score": {"$gte": 6.0}} in query["$and"]
    assert any("archived_at" in clause for clause in query["$and"])
    keyword_fields = {
        next(iter(item))
        for clause in query["$and"] if "$or" in clause
        for item in clause["$or"]
    }
    assert "analysis.message.title" in keyword_fields
    assert "raw_data.content" in keyword_fields
