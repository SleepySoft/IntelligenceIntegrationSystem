from datetime import datetime, timezone

from ServiceComponent.IntelligenceEntityFrequencyEngine import (
    ENTITY_TYPE_LOCATION, ENTITY_TYPE_ORGANIZATION, ENTITY_TYPE_PEOPLE,
    EntityFrequencyEngine,
)


class FakeEvents:
    def __init__(self):
        self.query = None

    def find(self, query):
        self.query = query
        return [
            {
                "roles": {
                    "actor": [{"name": "张三", "type": "person"},
                              {"name": "甲公司", "type": "organization"}],
                    "target": [{"name": "中国", "type": "geopolitical_entity"}],
                },
                "event_location": [{"name": "中国", "type": "geopolitical_entity"}],
            },
            {"roles": {"actor": [{"name": "张三", "type": "person"}]},
             "event_location": []},
        ]


def test_entity_frequency_uses_embedded_event_name_snapshots(tmp_path):
    events = FakeEvents()
    engine = EntityFrequencyEngine(str(tmp_path / "frequency.db"), events)
    result = engine._aggregate_entities_for_day(datetime(2026, 10, 1, tzinfo=timezone.utc))

    assert result[ENTITY_TYPE_PEOPLE] == {"张三": 2}
    assert result[ENTITY_TYPE_ORGANIZATION] == {"甲公司": 1}
    assert result[ENTITY_TYPE_LOCATION] == {"中国": 1}
    assert "observed_at" in events.query
