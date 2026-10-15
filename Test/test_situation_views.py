from datetime import datetime, timedelta, timezone

from ServiceComponent.IntelligenceHubDefines_v4 import DEFAULT_EVENT_REGISTRY
from ServiceComponent.intelligence_contracts import (
    APPENDIX_CONTENT_TIME, APPENDIX_TOTAL_SCORE,
)
from SituationViews.engine import SituationViewEngine, ViewQuery
from SituationViews.web import SituationViewsService


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


def test_map_view_accepts_multiple_event_categories():
    now = datetime.now(timezone.utc)
    war = _attack("e1", "a", "甲方", "设施一")
    security = _attack("e2", "b", "乙方", "设施二")
    security["predicate"] = {"id": "sanction", "label": "制裁"}
    engine = SituationViewEngine(
        FakeCollection([_archive("a", now, 7), _archive("b", now, 8)]),
        FakeCollection([war, security]),
        DEFAULT_EVENT_REGISTRY,
    )

    result = engine.map_view(ViewQuery(
        now - timedelta(days=1), now + timedelta(days=1),
        categories=frozenset({"war", "security"}),
    ))

    assert result["meta"]["eligible_event_count"] == 2
    assert result["meta"]["categories"] == ["security", "war"]
    assert result["hotspots"][0]["categories"] == {"war": 1, "security": 1}


def test_map_intelligences_returns_real_article_summaries_for_country():
    now = datetime.now(timezone.utc)
    archives = [_archive("a", now, 7), _archive("b", now - timedelta(hours=1), 8)]
    events = [
        _attack("e1", "a", "甲方", "设施一"),
        _attack("e2", "b", "乙方", "设施二", location="哈尔科夫"),
    ]
    engine = SituationViewEngine(
        FakeCollection(archives), FakeCollection(events), DEFAULT_EVENT_REGISTRY)

    result = engine.map_intelligences(
        ViewQuery(now - timedelta(days=1), now + timedelta(days=1)), "ua")

    assert result["meta"]["country_code"] == "UA"
    assert result["meta"]["total"] == 2
    assert result["intelligences"][0]["uuid"] == "a"
    assert result["intelligences"][0]["title"] == "event-a"
    assert result["intelligences"][0]["predicates"] == ["攻击"]

    filtered = engine.map_intelligences(
        ViewQuery(now - timedelta(days=1), now + timedelta(days=1)),
        "UA", location_name="哈尔科夫")
    assert filtered["meta"]["total"] == 1
    assert filtered["intelligences"][0]["uuid"] == "b"


def test_map_locations_aggregates_explicit_places_for_visible_countries():
    now = datetime.now(timezone.utc)
    archives = [_archive("a", now, 7), _archive("b", now, 8)]
    events = [
        _attack("e1", "a", "甲方", "设施一", location="基辅"),
        _attack("e2", "b", "乙方", "设施二", location="基辅"),
    ]
    engine = SituationViewEngine(
        FakeCollection(archives), FakeCollection(events), DEFAULT_EVENT_REGISTRY)

    result = engine.map_locations(
        ViewQuery(now - timedelta(days=1), now + timedelta(days=1)), {"UA"})

    assert result == [{
        "name": "基辅",
        "country_code": "UA",
        "event_count": 2,
        "article_count": 2,
        "max_score": 8.0,
        "dominant_category": "war",
    }]


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


def test_entity_graph_merges_missing_country_when_same_name_has_one_country():
    now = datetime.now(timezone.utc)
    first = _attack("e1", "a", "胡塞武装", "同一目标")
    second = _attack("e2", "b", "胡塞武装", "同一目标")
    first["roles"]["actor"][0].pop("country_code")
    second["roles"]["actor"][0]["country_code"] = "YE"
    engine = SituationViewEngine(
        FakeCollection([_archive("a", now, 7), _archive("b", now, 8)]),
        FakeCollection([first, second]),
        DEFAULT_EVENT_REGISTRY,
    )

    result = engine.entity_graph(
        ViewQuery(now - timedelta(days=1), now + timedelta(days=1)),
        min_support=1,
    )

    actors = [node for node in result["nodes"] if node["name"] == "胡塞武装"]
    assert len(actors) == 1
    assert actors[0]["country_code"] == "YE"
    assert result["edges"][0]["support"] == 2
    assert result["meta"]["inferred_country_node_count"] == 1


def test_entity_graph_does_not_guess_country_when_candidates_are_ambiguous():
    now = datetime.now(timezone.utc)
    events = []
    for index, country in enumerate(("YE", "IR", None), start=1):
        event = _attack(f"e{index}", f"a{index}", "同名组织", "同一目标")
        if country:
            event["roles"]["actor"][0]["country_code"] = country
        else:
            event["roles"]["actor"][0].pop("country_code")
        events.append(event)
    engine = SituationViewEngine(
        FakeCollection([_archive(f"a{i}", now, 7) for i in range(1, 4)]),
        FakeCollection(events),
        DEFAULT_EVENT_REGISTRY,
    )

    result = engine.entity_graph(
        ViewQuery(now - timedelta(days=1), now + timedelta(days=1)),
        min_support=1,
    )

    actors = [node for node in result["nodes"] if node["name"] == "同名组织"]
    assert {node["country_code"] for node in actors} == {"YE", "IR", None}
    assert result["meta"]["inferred_country_node_count"] == 0


def test_web_service_serves_both_views_with_injected_collections():
    from flask import Flask
    from types import SimpleNamespace

    now = datetime.now(timezone.utc)
    archives = [_archive("a", now, 8)]
    events = [_attack("e1", "a", "行动方", "目标")]
    context = SimpleNamespace(
        name="news",
        event_v4_intelligence_collection=FakeCollection(archives),
        event_v4_event_collection=FakeCollection(events),
    )

    class Registry:
        @staticmethod
        def resolve(name=None):
            return context if not name or name == "news" else None

        @staticmethod
        def describe():
            return [{"name": "news", "display_name": "国际新闻", "is_default": True}]

    class Geocoder:
        @staticmethod
        def resolve_many(values):
            return {
                "locations": [{**value, "latitude": 50.45, "longitude": 30.52}
                              for value in values],
                "pending_count": 0,
            }

    app = Flask(__name__)
    SituationViewsService(Registry(), geocoder=Geocoder()).mount_to_app(
        app, url_prefix="/situation")
    client = app.test_client()
    params = {
        "subsystem": "news",
        "start": (now - timedelta(days=7)).isoformat(),
        "end": (now + timedelta(minutes=1)).isoformat(),
        "min_score": "6",
    }

    page = client.get("/situation/")
    map_payload = client.get("/situation/api/map", query_string=params)
    graph_payload = client.get("/situation/api/entity-graph", query_string=params)
    intelligence_payload = client.get(
        "/situation/api/map/intelligences",
        query_string={**params, "country_code": "UA"},
    )
    location_payload = client.post("/situation/api/map/locations", json={
        "locations": [{
            "name": "基辅", "country_code": "UA", "event_count": 1,
            "dominant_category": "war",
        }],
    })

    assert page.status_code == 200
    assert "态势实验视图" in page.get_data(as_text=True)
    assert map_payload.status_code == 200
    assert map_payload.get_json()["meta"]["hotspot_count"] == 1
    assert graph_payload.status_code == 200
    assert graph_payload.get_json()["meta"]["edge_count"] == 1
    assert intelligence_payload.status_code == 200
    assert intelligence_payload.get_json()["intelligences"][0]["uuid"] == "a"
    assert location_payload.status_code == 200
    assert location_payload.get_json()["locations"][0]["name"] == "基辅"
