from datetime import datetime, timezone

from ServiceComponent.IntelligenceEntityFrequencyEngine import (
    ENTITY_TYPE_GEOGRAPHY,
    ENTITY_TYPE_ORGANIZATION,
    ENTITY_TYPE_PEOPLE,
    EntityFrequencyEngine,
)


class FakeEvents:
    def __init__(self):
        self.pipeline = None

    def aggregate(self, pipeline):
        self.pipeline = pipeline
        return [{"_id": "person-1", "count": 2}, {"_id": "org-1", "count": 1},
                {"_id": "country-1", "count": 3}]


class FakeEntities:
    def find(self, query, projection):
        assert set(query["_id"]["$in"]) == {"person-1", "org-1", "country-1"}
        return [
            {"_id": "person-1", "canonical_name": "张三", "entity_type": "person"},
            {"_id": "org-1", "canonical_name": "甲公司", "entity_type": "organization"},
            {"_id": "country-1", "canonical_name": "中国", "entity_type": "country"},
        ]


def test_entity_frequency_aggregates_v4_event_bindings(tmp_path):
    events = FakeEvents()
    engine = EntityFrequencyEngine(
        db_path=str(tmp_path / "frequency.db"),
        event_collection=events,
        entity_collection=FakeEntities(),
    )

    result = engine._aggregate_entities_for_day(
        datetime(2026, 10, 1, tzinfo=timezone.utc))

    assert result[ENTITY_TYPE_PEOPLE] == {"张三": 2}
    assert result[ENTITY_TYPE_ORGANIZATION] == {"甲公司": 1}
    assert result[ENTITY_TYPE_GEOGRAPHY] == {"中国": 3}
    assert "observed_at" in events.pipeline[0]["$match"]
