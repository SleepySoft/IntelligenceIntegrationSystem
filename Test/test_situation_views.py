from datetime import datetime, timedelta, timezone

from ServiceComponent.IntelligenceHubDefines_v4 import DEFAULT_EVENT_REGISTRY
from ServiceComponent.intelligence_contracts import (
    APPENDIX_CONTENT_TIME, APPENDIX_TOTAL_SCORE,
)
from SituationViews.engine import SituationViewEngine, ViewQuery
from SituationViews.standalone import create_app


class FakeCursor(list):
    def limit(self, count):
        return FakeCursor(self[:count])


class FakeCollection:
    def __init__(self, values):
        self.values = list(values)

    def find(self, query, projection=None):
        values = list(self.values)
        if "intelligence_uuid" in query:
            allowed = set(query["intelligence_uuid"]["$in"])
            values = [item for item in values if item.get("intelligence_uuid") in allowed]
        if query.get("is_primary") is True:
            values = [item for item in values if item.get("is_primary") is True]
        return FakeCursor(dict(item) for item in values)


def _archive(uuid, when, score):
    return {
        "UUID": uuid,
        "EVENT_TITLE": f"event-{uuid}",
        "APPENDIX": {
            APPENDIX_CONTENT_TIME: when,
            APPENDIX_TOTAL_SCORE: score,
        },
    }


def _attack(event_uuid, article_uuid, actor, target, location="基辅"):
    return {
        "event_uuid": event_uuid,
        "intelligence_uuid": article_uuid,
        "is_primary": True,
        "frame": {"topology": "targeted"},
        "predicate": {"id": "attack", "label": "攻击"},
        "roles": {
            "actor": [{"name": actor, "type": "organization", "country_code": "RU"}],
            "target": [{"name": target, "type": "facility", "country_code": "UA"}],
        },
        "event_location": [
            {"name": location, "type": "location", "country_code": "UA"}
        ],
    }


def test_map_view_uses_explicit_event_locations_and_reports_coverage():
    now = datetime.now(timezone.utc)
    archives = [_archive("a", now, 7), _archive("b", now, 8)]
    events = [
        _attack("e1", "a", "甲方", "设施一"),
        {**_attack("e2", "b", "乙方", "设施二"), "event_location": []},
    ]
    engine = SituationViewEngine(
        FakeCollection(archives), FakeCollection(events), DEFAULT_EVENT_REGISTRY)

    result = engine.map_view(ViewQuery(now - timedelta(days=1), now + timedelta(days=1)))

    assert result["hotspots"][0]["country_code"] == "UA"
    assert result["hotspots"][0]["event_count"] == 1
    assert result["hotspots"][0]["dominant_category"] == "war"
    assert result["meta"]["location_coverage"] == 0.5


def test_entity_graph_aggregates_subject_object_and_filters_weak_edges():
    now = datetime.now(timezone.utc)
    archives = [
        _archive("a", now, 7), _archive("b", now, 7.5), _archive("c", now, 6),
    ]
    events = [
        _attack("e1", "a", "同一行动方", "同一目标"),
        _attack("e2", "b", "同一行动方", "同一目标"),
        _attack("e3", "c", "零散行动方", "零散目标"),
    ]
    engine = SituationViewEngine(
        FakeCollection(archives), FakeCollection(events), DEFAULT_EVENT_REGISTRY)

    result = engine.entity_graph(
        ViewQuery(now - timedelta(days=1), now + timedelta(days=1)),
        min_support=2,
        keep_high_score_singletons=False,
    )

    assert len(result["edges"]) == 1
    assert result["edges"][0]["support"] == 2
    assert result["edges"][0]["directed"] is True
    assert {item["name"] for item in result["nodes"]} == {"同一行动方", "同一目标"}
    assert result["meta"]["filtered_edge_count"] == 1


def test_entity_graph_keeps_high_score_singleton_for_review():
    now = datetime.now(timezone.utc)
    engine = SituationViewEngine(
        FakeCollection([_archive("a", now, 9)]),
        FakeCollection([_attack("e1", "a", "新行动方", "新目标")]),
        DEFAULT_EVENT_REGISTRY,
    )

    result = engine.entity_graph(
        ViewQuery(now - timedelta(days=1), now + timedelta(days=1), min_score=6),
        min_support=2,
        keep_high_score_singletons=True,
    )

    assert len(result["edges"]) == 1
    assert result["edges"][0]["support"] == 1
    assert result["meta"]["high_score_singleton_cutoff"] == 8.0


def test_demo_app_serves_both_views_without_mongodb():
    app = create_app(demo=True)
    client = app.test_client()
    now = datetime.now(timezone.utc)
    params = {
        "subsystem": "demo",
        "start": (now - timedelta(days=7)).isoformat(),
        "end": (now + timedelta(minutes=1)).isoformat(),
        "min_score": "6",
    }

    page = client.get("/situation/")
    map_payload = client.get("/situation/api/map", query_string=params)
    graph_payload = client.get("/situation/api/entity-graph", query_string=params)

    assert page.status_code == 200
    assert "态势实验视图" in page.get_data(as_text=True)
    assert map_payload.status_code == 200
    assert map_payload.get_json()["meta"]["hotspot_count"] == 4
    assert graph_payload.status_code == 200
    assert graph_payload.get_json()["meta"]["edge_count"] == 2
