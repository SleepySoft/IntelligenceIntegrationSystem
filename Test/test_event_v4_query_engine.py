from datetime import datetime, timezone

from ServiceComponent.EventV4QueryEngine import EventV4QueryEngine
from ServiceComponent.intelligence_contracts import (
    APPENDIX_CONTENT_TIME, APPENDIX_RAW_DATA, APPENDIX_TIME_ARCHIVED,
    APPENDIX_TIME_GOT, APPENDIX_TIME_PUB, APPENDIX_TOTAL_SCORE, appendix_path,
)


class FakeCursor(list):
    def __init__(self, values=(), collection=None):
        super().__init__(values)
        self.collection = collection

    def sort(self, spec, *args, **kwargs):
        if self.collection is not None:
            self.collection.sorts.append(spec)
        return self
    def skip(self, count): return FakeCursor(self[count:])
    def limit(self, count): return FakeCursor(self[:count])


class RecordingCollection:
    def __init__(self, documents=()):
        self.documents, self.queries, self.sorts, self.find_one_sorts = list(documents), [], [], []

    def find_one(self, query, projection=None, sort=None):
        self.find_one_sorts.append(sort)
        values = list(self.find(query, projection))
        return values[0] if values else None

    def find(self, query, projection=None):
        self.queries.append(query)
        values = list(self.documents)
        if isinstance(query.get("UUID"), dict) and "$in" in query["UUID"]:
            values = [item for item in values if item.get("UUID") in query["UUID"]["$in"]]
        elif "UUID" in query:
            values = [item for item in values if item.get("UUID") == query["UUID"]]
        if "intelligence_uuid" in query:
            values = [item for item in values if item.get("intelligence_uuid") == query["intelligence_uuid"]]
        if projection and projection.get("_id") == 0:
            values = [{key: value for key, value in item.items() if key != "_id"} for item in values]
        return FakeCursor((dict(item) for item in values), self)

    def count_documents(self, query):
        self.queries.append(query)
        return len(self.documents)

    def aggregate(self, pipeline):
        return [{"_id": "group-key", "count": 1}]


def _archive(uuid):
    now = datetime.now(timezone.utc)
    return {
        "_id": f"mongo-{uuid}", "UUID": uuid, "INFORMANT": "https://example.test/item",
        "TAXONOMY": "政治与安全", "SUB_CATEGORY": ["国防军事"], "EVENT_TITLE": "示例事件",
        "APPENDIX": {APPENDIX_CONTENT_TIME: now, APPENDIX_TIME_ARCHIVED: now,
                     APPENDIX_TIME_PUB: "2026-10-12T08:00:00Z",
                     APPENDIX_TIME_GOT: now,
                     APPENDIX_TOTAL_SCORE: 6.0, APPENDIX_RAW_DATA: {"content": "原文"}},
    }


def test_v4_query_preserves_order_and_strips_mongo_id_and_appendix():
    engine = EventV4QueryEngine(RecordingCollection([_archive("b"), _archive("a")]), RecordingCollection())
    result = engine.get_intelligence(["a", "b"])
    assert [item["UUID"] for item in result] == ["a", "b"]
    assert all("_id" not in item and "APPENDIX" not in item for item in result)


def test_v4_query_builds_top_level_and_appendix_filters():
    intelligence = RecordingCollection([_archive("a")])
    engine = EventV4QueryEngine(intelligence, RecordingCollection())
    result, total = engine.query_intelligence(
        keywords="能源 安全", threshold=6,
        period=(datetime(2026, 1, 2, tzinfo=timezone.utc), datetime(2026, 1, 31, tzinfo=timezone.utc)),
        archive_period=(datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 2, 1, tzinfo=timezone.utc)),
    )
    query = intelligence.queries[0]
    assert total == 1 and len(result) == 1
    assert result[0]["published_at"] == "2026-10-12T08:00:00Z"
    assert result[0]["collected_at"] is not None
    assert {appendix_path(APPENDIX_TOTAL_SCORE): {"$gte": 6.0}} in query["$and"]
    assert any(appendix_path(APPENDIX_CONTENT_TIME) in clause for clause in query["$and"])
    assert any(appendix_path(APPENDIX_TIME_ARCHIVED) in clause for clause in query["$and"])
    fields = {next(iter(item)) for clause in query["$and"] if "$or" in clause for item in clause["$or"]}
    assert {"EVENT_TITLE", "EVENT_BRIEF", "EVENT_TEXT", appendix_path(APPENDIX_RAW_DATA) + ".content"} <= fields


def test_v4_query_defaults_to_archive_order_and_can_use_content_order():
    intelligence = RecordingCollection([_archive("a")])
    engine = EventV4QueryEngine(intelligence, RecordingCollection())

    engine.query_intelligence()
    engine.query_intelligence(sort_by="content_time")
    engine.get_intelligence_summary()

    assert intelligence.sorts[0][0] == (appendix_path(APPENDIX_TIME_ARCHIVED), -1)
    assert intelligence.sorts[1][0] == (appendix_path(APPENDIX_CONTENT_TIME), -1)
    assert intelligence.find_one_sorts[-1][0] == (appendix_path(APPENDIX_TIME_ARCHIVED), -1)


def test_detail_uses_embedded_names_without_entity_collection():
    event = {"_id": "mongo-event", "event_uuid": "event-1", "intelligence_uuid": "intel-1",
             "roles": {"actor": [{"name": "示例机构", "type": "organization"}]}}
    engine = EventV4QueryEngine(RecordingCollection([_archive("intel-1")]), RecordingCollection([event]))
    result = engine.get_intelligence("intel-1")
    assert result["events"][0]["roles"]["actor"][0]["name"] == "示例机构"
    assert "_id" not in result["events"][0] and "entities" not in result


def test_aggregate_preserves_semantic_group_id():
    engine = EventV4QueryEngine(RecordingCollection(), RecordingCollection())
    assert engine.aggregate([]) == [{"_id": "group-key", "count": 1}]
