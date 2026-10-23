from datetime import datetime, timedelta, timezone

from ServiceComponent.IntelligenceHubDefines_v4 import DEFAULT_EVENT_REGISTRY
from ServiceComponent.intelligence_contracts import (
    APPENDIX_CONTENT_TIME, APPENDIX_TOTAL_SCORE,
)
from SituationViews.engine import (
    GRAPH_EVENT_PROJECTION,
    MAP_EVENT_PROJECTION,
    TIMELINE_EVENT_PROJECTION,
    SituationViewEngine,
    ViewQuery,
)
from SituationViews.iis_integration import mount_to_iis
from SituationViews.web import SituationViewsService


class FakeCursor(list):
    def limit(self, count):
        return FakeCursor(self[:count])


class FakeCollection:
    def __init__(self, values):
        self.values = list(values)
        self.projections = []

    def find(self, query, projection=None):
        self.projections.append(projection)
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
        "EVENT_BRIEF": f"summary-{uuid}",
        "INFORMANT": f"https://source.example/{uuid}",
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
    assert result["hotspots"][0]["timeline"] == [[
        now.timestamp(), "e1", "a", 7.0, "war", "attack", ["基辅"],
    ]]
    assert result["meta"]["location_coverage"] == 0.5


def test_situation_uses_separate_fixed_event_projections():
    now = datetime.now(timezone.utc)
    event = _attack("e1", "a", "行动方", "目标")
    event["time"] = {"event_time": {
        "normalized": "2026-10-20", "precision": "day",
        "approximate": False, "surface": "10月20日",
    }}
    events = FakeCollection([event])
    archives = FakeCollection([_archive("a", now, 8)])
    engine = SituationViewEngine(archives, events, DEFAULT_EVENT_REGISTRY)
    query = ViewQuery(now - timedelta(days=1), now + timedelta(days=1))

    engine.map_view(query)
    engine.entity_graph(query, min_support=1)
    engine.entity_timeline(query, entity_ids=["organization|RU|行动方"])

    assert events.projections == [
        {"_id": 0, **MAP_EVENT_PROJECTION},
        {"_id": 0, **GRAPH_EVENT_PROJECTION},
        {"_id": 0, **TIMELINE_EVENT_PROJECTION},
    ]
    assert "time" not in GRAPH_EVENT_PROJECTION
    assert TIMELINE_EVENT_PROJECTION["time"] == 1
    assert "EVENT_BRIEF" not in archives.projections[0]
    assert "EVENT_BRIEF" not in archives.projections[1]
    assert archives.projections[2]["EVENT_BRIEF"] == 1


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

    assert "eligible_event_count" not in result["meta"]
    assert "categories" not in result["meta"]
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
    assert result["intelligences"][0]["predicates"] == [
        {"label": "攻击", "count": 1},
    ]
    assert set(result["intelligences"][0]) == {
        "uuid", "title", "score", "content_time", "event_count", "predicates",
    }
    assert set(result["meta"]) == {
        "country_code", "location_name", "total", "offset", "limit", "has_more",
    }

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
    assert result["edges"][0]["event_refs"] == [0, 1]
    assert result["temporal_events"] == [
        [now.timestamp(), "a", 7.0, 0],
        [now.timestamp(), "b", 7.5, 0],
    ]
    assert all(node["event_refs"] == [0, 1] for node in result["nodes"])
    assert {item["name"] for item in result["nodes"]} == {"同一行动方", "同一目标"}
    assert result["meta"]["filtered_edge_count"] == 1


def test_entity_graph_uses_registry_label_and_keeps_surface_label_counts():
    now = datetime.now(timezone.utc)
    first = _attack("e1", "a", "同一行动方", "同一目标")
    second = _attack("e2", "b", "同一行动方", "同一目标")
    second["predicate"]["label"] = "ataque"
    engine = SituationViewEngine(
        FakeCollection([_archive("a", now, 7), _archive("b", now, 8)]),
        FakeCollection([first, second]),
        DEFAULT_EVENT_REGISTRY,
    )

    result = engine.entity_graph(
        ViewQuery(now - timedelta(days=1), now + timedelta(days=1)),
        min_support=1,
    )

    assert result["edges"][0]["predicate_label"] == "攻击"
    assert result["edges"][0]["surface_labels"] == [
        {"label": "ataque", "count": 1},
    ]


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
    assert "high_score_singleton_cutoff" not in result["meta"]


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
    assert "inferred_country_node_count" not in result["meta"]


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
    assert "inferred_country_node_count" not in result["meta"]


def test_entity_graph_temporarily_merges_unresolved_same_surface_refs():
    now = datetime.now(timezone.utc)
    first = _attack("e1", "a", "示威者", "政府")
    second = _attack("e2", "b", "示威者", "政府")
    for event in (first, second):
        event["roles"]["actor"][0]["local_entity_id"] = "ENT1"
        event["roles"]["target"][0]["local_entity_id"] = "ENT2"
    engine = SituationViewEngine(
        FakeCollection([_archive("a", now, 7), _archive("b", now, 8)]),
        FakeCollection([first, second]), DEFAULT_EVENT_REGISTRY,
        subsystem="news",
    )

    result = engine.entity_graph(
        ViewQuery(now - timedelta(days=1), now + timedelta(days=1)),
        min_support=1,
    )

    protesters = [item for item in result["nodes"] if item["name"] == "示威者"]
    assert len(protesters) == 1
    assert protesters[0]["id"] == "organization|RU|示威者"
    assert protesters[0]["identity_kind"] == "surface"
    assert protesters[0]["reference_count"] == 2
    assert result["edges"][0]["support"] == 2


def test_entity_graph_manual_assignment_splits_ref_from_surface_group():
    now = datetime.now(timezone.utc)
    first = _attack("e1", "a", "示威者", "政府")
    second = _attack("e2", "b", "示威者", "政府")
    for event in (first, second):
        event["roles"]["actor"][0]["local_entity_id"] = "ENT1"
        event["roles"]["target"][0]["local_entity_id"] = "ENT2"

    class Resolver:
        @staticmethod
        def resolve_many(values):
            return {
                "news:a:ENT1": {
                    "kind": "contextual", "target_id": "ctx_fr_protesters",
                    "name": "法国示威者", "type": "organization",
                },
            }

    engine = SituationViewEngine(
        FakeCollection([_archive("a", now, 7), _archive("b", now, 8)]),
        FakeCollection([first, second]), DEFAULT_EVENT_REGISTRY,
        subsystem="news", entity_resolver=Resolver(),
    )

    result = engine.entity_graph(
        ViewQuery(now - timedelta(days=1), now + timedelta(days=1)),
        min_support=1,
    )

    actors = [item for item in result["nodes"] if item["name"] in {"示威者", "法国示威者"}]
    assert {item["id"] for item in actors} == {
        "organization|RU|示威者", "ctx_fr_protesters",
    }
    assert {item["identity_kind"] for item in actors} == {"surface", "contextual"}


def test_entity_graph_naturally_merges_refs_assigned_to_same_targets():
    now = datetime.now(timezone.utc)
    first = _attack("e1", "a", "示威者", "政府")
    second = _attack("e2", "b", "抗议者", "法国政府")
    for event in (first, second):
        event["roles"]["actor"][0]["local_entity_id"] = "ENT1"
        event["roles"]["target"][0]["local_entity_id"] = "ENT2"

    class Resolver:
        @staticmethod
        def resolve_many(values):
            result = {}
            for value in values:
                key = f"news:{value['intelligence_uuid']}:{value['local_entity_id']}"
                if value["local_entity_id"] == "ENT1":
                    result[key] = {
                        "kind": "contextual", "target_id": "ctx_fr_protesters",
                        "name": "法国养老金改革示威者", "type": "organization",
                    }
                else:
                    result[key] = {
                        "kind": "global", "target_id": "ent_fr_government",
                        "name": "法国政府", "type": "organization",
                    }
            return result

    engine = SituationViewEngine(
        FakeCollection([_archive("a", now, 7), _archive("b", now, 8)]),
        FakeCollection([first, second]), DEFAULT_EVENT_REGISTRY,
        subsystem="news", entity_resolver=Resolver(),
    )
    query = ViewQuery(now - timedelta(days=1), now + timedelta(days=1))

    result = engine.entity_graph(query, min_support=2, keep_high_score_singletons=False)
    references = engine.entity_graph_references(query, "ctx_fr_protesters")

    assert len(result["nodes"]) == 2
    assert result["edges"][0]["support"] == 2
    assert {item["id"] for item in result["nodes"]} == {
        "ctx_fr_protesters", "ent_fr_government"}
    assert references["total"] == 2
    assert {item["ref"]["intelligence_uuid"] for item in references["references"]} == {"a", "b"}


def test_entity_graph_filters_relationships_by_both_entity_type_groups():
    now = datetime.now(timezone.utc)
    organization_edge = _attack("e1", "a", "组织甲", "组织乙")
    organization_edge["roles"]["target"][0]["type"] = "organization"
    facility_edge = _attack("e2", "b", "组织甲", "设施甲")
    engine = SituationViewEngine(
        FakeCollection([_archive("a", now, 7), _archive("b", now, 8)]),
        FakeCollection([organization_edge, facility_edge]),
        DEFAULT_EVENT_REGISTRY,
    )

    result = engine.entity_graph(
        ViewQuery(now - timedelta(days=1), now + timedelta(days=1)),
        min_support=1,
        entity_types=frozenset({"organization"}),
    )

    assert len(result["edges"]) == 1
    assert {node["type"] for node in result["nodes"]} == {"organization"}
    assert "entity_types" not in result["meta"]


def test_entity_graph_node_intelligences_support_role_filtering():
    now = datetime.now(timezone.utc)
    events = [
        _attack("e1", "a", "行动方", "目标一"),
        _attack("e2", "b", "行动方", "目标二"),
    ]
    engine = SituationViewEngine(
        FakeCollection([_archive("a", now, 7), _archive("b", now, 8)]),
        FakeCollection(events),
        DEFAULT_EVENT_REGISTRY,
    )
    query = ViewQuery(now - timedelta(days=1), now + timedelta(days=1))

    result = engine.entity_graph_intelligences(
        query,
        selection_type="node",
        entity_id="organization|RU|行动方",
        role="subject",
    )
    excluded = engine.entity_graph_intelligences(
        query,
        selection_type="node",
        entity_id="organization|RU|行动方",
        role="object",
    )

    assert result["meta"]["total"] == 2
    assert result["intelligences"][0]["uuid"] in {"a", "b"}
    assert result["intelligences"][0]["roles"] == [
        {"role": "subject", "event_count": 1},
    ]
    assert result["intelligences"][0]["counterparts"][0]["name"] in {"目标一", "目标二"}
    assert excluded["meta"]["total"] == 0
    assert set(result["meta"]) == {"total", "offset", "limit", "has_more"}


def test_entity_graph_edge_intelligences_match_exact_direction_and_predicate():
    now = datetime.now(timezone.utc)
    exact = _attack("e1", "a", "行动方", "目标")
    other_predicate = _attack("e2", "b", "行动方", "目标")
    other_predicate["predicate"] = {"id": "sanction", "label": "制裁"}
    engine = SituationViewEngine(
        FakeCollection([_archive("a", now, 7), _archive("b", now, 8)]),
        FakeCollection([exact, other_predicate]),
        DEFAULT_EVENT_REGISTRY,
    )

    result = engine.entity_graph_intelligences(
        ViewQuery(now - timedelta(days=1), now + timedelta(days=1)),
        selection_type="edge",
        source_id="organization|RU|行动方",
        target_id="facility|UA|目标",
        predicate="attack",
        directed=True,
    )

    assert result["meta"]["total"] == 1
    assert result["intelligences"][0]["uuid"] == "a"
    assert result["intelligences"][0]["predicates"] == [
        {"label": "攻击", "count": 1},
    ]


def test_entity_timeline_targets_multiple_entities_without_graph_size_clipping():
    now = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)
    first = _attack("e1", "a", "美国", "目标一")
    first["time"] = {"event_time": {
        "normalized": "2026-10-20", "precision": "day",
        "approximate": False, "surface": "10月20日",
    }}
    second = _attack("e2", "b", "白宫", "美国")
    second["roles"]["target"][0].update({
        "type": "organization", "country_code": "RU",
    })
    engine = SituationViewEngine(
        FakeCollection([_archive("a", now, 7), _archive("b", now, 8)]),
        FakeCollection([first, second]), DEFAULT_EVENT_REGISTRY,
    )
    query = ViewQuery(now - timedelta(days=7), now + timedelta(days=1))
    entity_ids = ["organization|RU|美国", "organization|RU|白宫"]

    result = engine.entity_timeline(query, entity_ids=entity_ids)
    suggestions = engine.entity_timeline_entities(query, search="白宫")

    assert [item["id"] for item in result["entities"]] == entity_ids
    assert result["meta"]["timeline_event_count"] == 2
    assert result["meta"]["cooccurrence_event_count"] == 1
    assert result["events"][0]["time"] == {
        "timestamp": datetime(2026, 10, 20, tzinfo=timezone.utc).timestamp(),
        "normalized": "2026-10-20", "precision": "day",
        "approximate": False, "surface": "10月20日",
        "slot": "event_time", "source": "event_time",
    }
    assert result["events"][1]["time"]["source"] == "content_time"
    assert result["events"][1]["relations"][0]["internal"] is True
    assert result["events"][0]["summary"] == "summary-a"
    assert result["events"][0]["source_domain"] == "source.example"
    assert suggestions["entities"][0]["name"] == "白宫"


def test_entity_timeline_rejects_missing_or_excessive_selection():
    now = datetime.now(timezone.utc)
    engine = SituationViewEngine(FakeCollection([]), FakeCollection([]), DEFAULT_EVENT_REGISTRY)
    query = ViewQuery(now - timedelta(days=1), now)

    try:
        engine.entity_timeline(query, entity_ids=[])
        assert False, "missing selection should fail"
    except ValueError as exc:
        assert "至少需要" in str(exc)
    try:
        engine.entity_timeline(query, entity_ids=[f"ent_{value}" for value in range(9)])
        assert False, "too many selected entities should fail"
    except ValueError as exc:
        assert "最多选择 8" in str(exc)
    try:
        engine.entity_timeline(
            query, entity_ids=["ent_a", "ent_b", "ent_c"], max_entities=2)
        assert False, "configured entity limit should fail"
    except ValueError as exc:
        assert "最多选择 2" in str(exc)


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
    SituationViewsService(
        Registry(), geocoder=Geocoder(),
        dashboard={
            "subsystem": "news", "window_days": 7, "public_window_days": 3,
            "min_score": 6, "public_timeline_max_entities": 1,
        },
    ).mount_to_app(
        app, url_prefix="/situation", public_scoped=True)
    client = app.test_client()
    params = {
        "subsystem": "news",
        "start": (now - timedelta(days=7)).isoformat(),
        "end": (now + timedelta(minutes=1)).isoformat(),
        "min_score": "6",
    }

    page = client.get("/situation/")
    map_page = client.get("/situation/map")
    entity_page = client.get("/situation/entities")
    embedded_map = client.get("/situation/embed/map", query_string={
        "subsystem": "missing",
        "start": "bad",
        "end": "bad",
        "min_score": "9",
    })
    embedded_timeline = client.get("/situation/embed/timeline", query_string={
        "entity": "organization|RU|行动方",
    })
    scoped_map = client.get("/situation/api/scoped/map", query_string={
        "subsystem": "missing", "start": "bad", "min_score": "9",
    })
    map_payload = client.get("/situation/api/map", query_string=params)
    graph_payload = client.get("/situation/api/entity-graph", query_string=params)
    large_graph_payload = client.get(
        "/situation/api/entity-graph",
        query_string={**params, "max_nodes": "5000", "max_edges": "20000"},
    )
    excessive_graph_payload = client.get(
        "/situation/api/entity-graph",
        query_string={**params, "max_nodes": "5001"},
    )
    timeline_payload = client.get(
        "/situation/api/entity-timeline",
        query_string=[*params.items(), ("entity", "organization|RU|行动方")],
    )
    timeline_over_limit = client.get(
        "/situation/api/scoped/entity-timeline",
        query_string=[*params.items(), ("entity", "ent_a"), ("entity", "ent_b")],
    )
    graph_intelligences = client.get(
        "/situation/api/entity-graph/intelligences",
        query_string={
            **params,
            "selection_type": "node",
            "entity_id": "organization|RU|行动方",
            "role": "subject",
        },
    )
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

    assert page.status_code == 404
    assert map_page.status_code == 404
    assert entity_page.status_code == 404
    assert embedded_map.status_code == 200
    assert embedded_timeline.status_code == 200
    timeline_html = embedded_timeline.get_data(as_text=True)
    assert "实体时间线" in timeline_html
    assert "data-mode=\"union\"" in timeline_html
    assert "data-orientation=\"horizontal\"" in timeline_html
    assert "const API_PREFIX=\"../api/scoped\"" in timeline_html
    assert 'id="timeline-svg"' in timeline_html
    assert 'src="../assets/d3.min.js"' in timeline_html
    assert 'id="zoom-fit"' in timeline_html
    assert 'id="zoom-reset"' in timeline_html
    assert 'id="range-start"' not in timeline_html
    assert 'id="range-end"' not in timeline_html
    assert "d3.zoom()" in timeline_html
    assert "最近 3 天收录情报" in timeline_html
    assert "最多 1 个实体" in timeline_html
    assert "function flowingPath" in timeline_html
    assert "attr('class','edge-label')" in timeline_html
    assert "function isCoarseTime" in timeline_html
    assert "value.side==='above'?-40:42" in timeline_html
    assert "edgesByAnchor=d3.group" in timeline_html
    assert "getPointAtLength" in timeline_html
    assert "box.width/2-scale*(bounds.x+bounds.width/2)" in timeline_html
    assert 'src="/static/js/article_modal_manager.js"' in timeline_html
    assert 'src="/static/js/situation_article_modal.js"' in timeline_html
    assert 'class="situation-article-title" data-uuid="${escapeHtml(item.article_uuid)}"' in timeline_html
    assert '<h3>情报摘要</h3>' in timeline_html
    assert '<h3>事件关系</h3>' in timeline_html
    assert "item.source_domain" in timeline_html
    embedded_html = embedded_map.get_data(as_text=True)
    assert '<body class="dashboard embedded">' in embedded_html
    assert 'const API_PREFIX = "../api/scoped"' in embedded_html
    assert 'src="/situation/assets/d3.min.js"' in embedded_html
    assert 'src="/situation/assets/topojson-client.min.js"' in embedded_html
    assert 'const WORLD_URL = "/situation/assets/countries-110m.json"' in embedded_html
    assert 'const COUNTRIES_URL = "/situation/assets/countries.json"' in embedded_html
    assert "cdn.jsdelivr.net" not in embedded_html
    assert '"min_score": 6.0' in embedded_html
    assert 'id="graph-selection-detail" hidden' in embedded_html
    assert 'id="max-nodes" type="number" min="10" max="5000"' in embedded_html
    assert 'class="intelligence-panel is-collapsed" id="map-intelligence-panel" hidden' in embedded_html
    assert 'class="intelligence-panel is-collapsed" id="graph-intelligence-panel" hidden' in embedded_html
    assert 'data-drawer-summary="map-intelligence-panel" aria-expanded="false"' in embedded_html
    assert 'data-drawer-summary="graph-intelligence-panel" aria-expanded="false"' in embedded_html
    assert 'id="map-intelligence-summary" aria-live="polite"' in embedded_html
    assert 'id="graph-intelligence-summary" aria-live="polite"' in embedded_html
    assert 'data-drawer-toggle="map-intelligence-panel"' in embedded_html
    assert 'data-drawer-close="map-intelligence-panel"' in embedded_html
    assert 'data-drawer-toggle="graph-intelligence-panel"' in embedded_html
    assert 'data-drawer-close="graph-intelligence-panel"' in embedded_html
    assert "openIntelligenceDrawer('map-intelligence-panel')" in embedded_html
    assert "openIntelligenceDrawer('graph-intelligence-panel')" in embedded_html
    assert "setIntelligenceDrawerCollapsed(panel, false);" not in embedded_html
    assert "updateIntelligenceDrawerSummary('map-intelligence-panel'" in embedded_html
    assert "updateIntelligenceDrawerSummary(\n            'graph-intelligence-panel'" in embedded_html
    assert 'intelligence-panel.is-collapsed.has-update' in embedded_html
    assert 'id="entity-editor"' in embedded_html
    assert 'type="radio" name="entity-target-kind" id="entity-kind-global"' in embedded_html
    assert 'type="radio" name="entity-target-kind" id="entity-kind-contextual"' in embedded_html
    assert 'type="checkbox" name="editing-ref"' in embedded_html
    assert 'id="entity-ref-select-all"' in embedded_html
    assert 'id="entity-limited-scope"' in embedded_html
    assert '仅针对所选情报中的实体生效' in embedded_html
    assert 'id="entity-mapping-preview">请先在上方选择一个目标实体' in embedded_html
    assert '将所选情报中的“${sourceName}”关联到“${targetName}”' in embedded_html
    assert '将“${sourceName}”全局映射到“${targetName}”' in embedded_html
    assert '已选择：${name}' not in embedded_html
    assert '即使已有结果，也始终可以添加新实体' in embedded_html
    assert 'id="entity-assign">保存全局映射' in embedded_html
    assert "scope.disabled = kind === 'contextual'" in embedded_html
    assert "if (!limited) return saveGlobalEntityMapping()" in embedded_html
    assert '目标实体与当前名称相同，无需添加重复别名' in embedded_html
    assert 'id="entity-fixed-alias-save"' not in embedded_html
    assert 'name="entity-operation"' not in embedded_html
    assert 'entity-register-fixed-alias' not in embedded_html
    assert 'id="graph-search-panel"' in embedded_html
    assert 'id="graph-search-results"' in embedded_html
    assert '搜索 Ctrl+F' in embedded_html
    assert 'id="map-time-start" type="range"' in embedded_html
    assert 'id="map-time-end" type="range"' in embedded_html
    assert 'id="graph-time-start" type="range"' in embedded_html
    assert 'id="graph-time-end" type="range"' in embedded_html
    assert "function setupTemporalAxis(kind, meta, eventSeconds, onChange)" in embedded_html
    assert "startInput.oninput = () => apply(startInput)" in embedded_html
    assert "loadJson(`${API_PREFIX}/map?" not in embedded_html.split(
        "function setupTemporalAxis", 1)[1].split("function legendHtml", 1)[0]
    assert 'id="graph-manual-refresh"' in embedded_html
    assert '.graph-notice { position: fixed;' in embedded_html
    assert 'top: 14px; z-index: 30' in embedded_html
    assert 'id="copy-graph-entity-name" title="复制实体名称"' in embedded_html
    assert 'id="open-entity-timeline"' in embedded_html
    assert 'id="open-map-entity-timeline"' in embedded_html
    assert "type: 'iis:open-timeline'" in embedded_html
    assert "async function openMapEntityTimeline(button)" in embedded_html
    assert 'src="/static/js/article_modal_manager.js"' in embedded_html
    assert 'src="/static/js/situation_article_modal.js"' in embedded_html
    assert 'class="situation-article-title" data-uuid="${escapeHtml(item.uuid)}"' in embedded_html
    assert 'target="_blank" rel="noopener">${escapeHtml(item.title)}</a>' not in embedded_html
    assert 'async function copyEntityName(name, button)' in embedded_html
    assert 'id="entity-global-create-panel"' in embedded_html
    assert 'id="entity-contextual-create-panel"' in embedded_html
    assert "$('entity-global-new-name').value = '';" in embedded_html
    assert "$('entity-contextual-new-name').value = '';" in embedded_html
    assert "$('entity-global-create-panel').open = false;" in embedded_html
    assert "$('entity-contextual-create-panel').open = false;" in embedded_html
    assert "showGraphNotice('所选情报的实体关联已保存。" in embedded_html
    assert "$('entity-editor').close(); refreshGraph()" not in embedded_html
    assert 'const ENTITY_EDIT_ENABLED = false' in embedded_html
    assert 'https://baike.baidu.com/search/word?word=' in embedded_html
    assert 'https://zh.wikipedia.org/w/index.php?search=' in embedded_html
    assert 'https://www.bing.com/search?q=' in embedded_html
    assert 'https://www.google.com/search?q=' in embedded_html
    assert "const lookupName = String(d.wiki_name || d.name || '').trim()" in embedded_html
    assert "$('graph-detail').innerHTML" not in embedded_html
    assert "detail.innerHTML = ''" in embedded_html
    assert ".attr('class','edge-arrow').attr('marker-end','url(#arrow)')" in embedded_html
    assert embedded_html.index("const labels = root.append('g')") < embedded_html.index(
        "const arrowLines = root.append('g')")
    assert scoped_map.status_code == 200
    assert scoped_map.get_json()["meta"]["hotspot_count"] == 1
    assert map_payload.status_code == 200
    assert map_payload.get_json()["meta"]["hotspot_count"] == 1
    assert map_payload.get_json()["hotspots"][0]["timeline"][0][1:3] == ["e1", "a"]
    assert graph_payload.status_code == 200
    assert graph_payload.get_json()["meta"]["edge_count"] == 1
    assert graph_payload.get_json()["edges"][0]["event_refs"] == [0]
    assert graph_payload.get_json()["temporal_events"][0][1] == "a"
    assert large_graph_payload.status_code == 200
    assert large_graph_payload.get_json()["meta"]["node_count"] == 2
    assert large_graph_payload.get_json()["meta"]["edge_count"] == 1
    assert excessive_graph_payload.status_code == 400
    assert "10 到 5000" in excessive_graph_payload.get_json()["error"]
    assert timeline_payload.status_code == 200
    assert timeline_payload.get_json()["events"][0]["id"] == "e1"
    assert timeline_over_limit.status_code == 400
    assert "最多选择 1" in timeline_over_limit.get_json()["error"]
    assert graph_intelligences.status_code == 200
    assert graph_intelligences.get_json()["intelligences"][0]["uuid"] == "a"
    assert intelligence_payload.status_code == 200
    assert intelligence_payload.get_json()["intelligences"][0]["uuid"] == "a"
    assert location_payload.status_code == 200
    assert location_payload.get_json()["locations"][0]["name"] == "基辅"


def test_frontend_assets_are_same_origin_cached_and_allowlisted(monkeypatch):
    from flask import Flask
    import SituationViews.web as web_module

    class Registry:
        @staticmethod
        def describe():
            return [{"name": "news", "display_name": "国际新闻", "is_default": True}]

    monkeypatch.setattr(web_module, "_load_frontend_asset",
                        lambda asset_name: f"asset:{asset_name}".encode())
    app = Flask(__name__)
    SituationViewsService(Registry()).mount_to_app(app, url_prefix="/situation")
    client = app.test_client()

    response = client.get("/situation/assets/d3.min.js")
    assert response.status_code == 200
    assert response.data == b"asset:d3.min.js"
    assert response.headers["Cache-Control"] == "public, max-age=604800"
    assert response.headers["Content-Type"] == "application/javascript; charset=utf-8"
    assert client.get("/situation/assets/not-allowed.js").status_code == 404


def test_entity_editor_is_enabled_only_for_logged_in_user():
    from flask import Flask

    class Registry:
        @staticmethod
        def describe():
            return [{"name": "news", "display_name": "国际新闻", "is_default": True}]

    app = Flask(__name__)
    app.secret_key = "test-only"
    SituationViewsService(
        Registry(), entity_resolution_service=object(),
    ).mount_to_app(app, url_prefix="/situation")
    client = app.test_client()

    anonymous = client.get("/situation/embed/entities").get_data(as_text=True)
    assert "const ENTITY_EDIT_ENABLED = false" in anonymous
    with client.session_transaction() as user_session:
        user_session["logged_in"] = True
        user_session["username"] = "alice"
    logged_in = client.get("/situation/embed/entities").get_data(as_text=True)
    assert "const ENTITY_EDIT_ENABLED = true" in logged_in


def test_entity_resolution_routes_batch_refs_and_keep_fixed_alias_global():
    from flask import Flask

    class Registry:
        @staticmethod
        def describe():
            return [{"name": "news", "display_name": "国际新闻", "is_default": True}]

    class ResolutionService:
        def __init__(self):
            self.assignments = []

        def assign_many(self, refs, kind, target_id, actor, reason=""):
            self.assignments.append((refs, kind, target_id, actor, reason))
            return {"total": len(refs), "changed": len(refs), "results": []}

        def unassign_many(self, refs, actor, reason=""):
            return {"total": len(refs), "changed": len(refs), "results": []}

    class AliasService:
        def __init__(self):
            self.aliases = []

        @staticmethod
        def get_entity(entity_id):
            return {"id": entity_id, "canonical": "美国"} if entity_id == "ent_us" else None

        def resolve(self, name):
            return None

        def add_alias(self, entity_id, name):
            self.aliases.append((entity_id, name))

    resolution = ResolutionService()
    aliases = AliasService()
    app = Flask(__name__)
    app.secret_key = "test-only"
    SituationViewsService(
        Registry(), entity_resolution_service=resolution,
        entity_alias_service=aliases,
    ).mount_to_app(app, url_prefix="/situation")
    client = app.test_client()
    with client.session_transaction() as user_session:
        user_session["username"] = "alice"

    refs = [
        {"subsystem": "news", "intelligence_uuid": "article-a", "local_entity_id": "ENT1"},
        {"subsystem": "news", "intelligence_uuid": "article-b", "local_entity_id": "ENT2"},
    ]
    assigned = client.post("/situation/api/entity-resolution/assign", json={
        "refs": refs, "target_kind": "global", "target_id": "ent_us",
    })
    fixed = client.post("/situation/api/entity-resolution/fixed-aliases", json={
        "target_id": "ent_us", "names": ["美利坚合众国"],
    })

    assert assigned.status_code == 200
    assert assigned.get_json()["total"] == 2
    assert len(resolution.assignments[0][0]) == 2
    assert resolution.assignments[0][1:4] == ("global", "ent_us", "alice")
    assert fixed.status_code == 200
    assert fixed.get_json()["registered"] == ["美利坚合众国"]
    assert aliases.aliases == [("ent_us", "美利坚合众国")]
    # 固定别名端点只改全局别名表，不新增任何情报引用关联。
    assert len(resolution.assignments) == 1


def test_iis_integration_mounts_routes_behind_supplied_auth_wrapper(tmp_path):
    from flask import Flask
    from functools import wraps

    class Config:
        @staticmethod
        def get(key, default=None):
            if key == "intelligence_hub_web_service.situation_views":
                return {
                    "enabled": True,
                    "geocoding": {"enabled": False},
                }
            return default

    class Registry:
        @staticmethod
        def describe():
            return [{"name": "news", "display_name": "国际新闻", "is_default": True}]

    def require_login(func):
        @wraps(func)
        def protected(*args, **kwargs):
            return "login required", 401
        return protected

    app = Flask(__name__)
    service = mount_to_iis(
        app,
        config=Config(),
        subsystem_registry=Registry(),
        wrapper=require_login,
        data_path=tmp_path,
        dashboard={"window_days": 14, "min_score": 7.5},
    )

    assert service is not None
    assert service.dashboard["window_days"] == 14
    assert service.dashboard["min_score"] == 7.5
    assert app.config["SITUATION_VIEWS_ENABLED"] is True
    assert app.test_client().get("/situation/embed/map").status_code == 401
    assert app.test_client().get("/situation/api/scoped/map").status_code == 401
    assert app.test_client().get(
        "/situation/api/entity-graph/references").status_code == 401
    assert app.test_client().post(
        "/situation/api/entity-resolution/assign", json={}).status_code == 401


def test_iis_integration_can_publish_only_fixed_scope_routes(tmp_path):
    from flask import Flask
    from functools import wraps

    class Config:
        @staticmethod
        def get(key, default=None):
            if key == "intelligence_hub_web_service.situation_views":
                return {
                    "enabled": True,
                    "geocoding": {"enabled": False},
                }
            return default

    class Registry:
        @staticmethod
        def describe():
            return [{"name": "news", "display_name": "国际新闻", "is_default": True}]

        @staticmethod
        def resolve(name=None):
            return None

    def require_login(func):
        @wraps(func)
        def protected(*args, **kwargs):
            return "login required", 401
        return protected

    app = Flask(__name__)
    service = mount_to_iis(
        app,
        config=Config(),
        subsystem_registry=Registry(),
        wrapper=require_login,
        data_path=tmp_path,
        public_scoped=True,
    )

    assert service is not None
    client = app.test_client()
    assert client.get("/situation/embed/map").status_code == 200
    # The scoped route reaches its handler (400 here because this minimal
    # registry deliberately has no backing subsystem) instead of auth (401).
    assert client.get("/situation/api/scoped/map").status_code == 400
    assert client.get("/situation/api/map").status_code == 401
    assert client.post("/situation/api/scoped/map/locations", json={}).status_code == 200
    assert client.post("/situation/api/map/locations", json={}).status_code == 401


def test_iis_integration_can_be_disabled_without_registering_routes(tmp_path):
    from flask import Flask

    class Config:
        @staticmethod
        def get(key, default=None):
            if key == "intelligence_hub_web_service.situation_views":
                return {"enabled": False}
            return default

    app = Flask(__name__)
    service = mount_to_iis(
        app,
        config=Config(),
        subsystem_registry=object(),
        wrapper=None,
        data_path=tmp_path,
    )

    assert service is None
    assert app.config["SITUATION_VIEWS_ENABLED"] is False
    assert app.test_client().get("/situation/").status_code == 404


def test_iis_integration_keeps_views_when_geocoder_initialization_fails(tmp_path):
    from flask import Flask

    class Config:
        @staticmethod
        def get(key, default=None):
            if key == "intelligence_hub_web_service.situation_views":
                return {
                    "enabled": True,
                    "geocoding": {
                        "enabled": True,
                        "request_interval_sec": "invalid",
                    },
                }
            return default

    class Registry:
        @staticmethod
        def describe():
            return [{"name": "news", "display_name": "国际新闻", "is_default": True}]

    app = Flask(__name__)
    service = mount_to_iis(
        app,
        config=Config(),
        subsystem_registry=Registry(),
        wrapper=None,
        data_path=tmp_path,
    )

    assert service is not None
    assert service.geocoder is None
    assert app.test_client().get("/situation/embed/map").status_code == 200


def test_entity_graph_marks_negated_edges():
    """否定事件（polarity=negated）必须计入边的 negated_count。"""
    now = datetime.now(timezone.utc)
    normal = _attack("e1", "a", "甲方", "设施一")
    negated = {
        **_attack("e2", "b", "甲方", "设施一"),
        "qualifiers": [{"type": "polarity", "value": "negated", "scope": "event"}],
    }
    engine = SituationViewEngine(
        FakeCollection([_archive("a", now, 7), _archive("b", now, 8)]),
        FakeCollection([normal, negated]),
        DEFAULT_EVENT_REGISTRY,
    )

    result = engine.entity_graph(
        ViewQuery(now - timedelta(days=1), now + timedelta(days=1)))

    assert len(result["edges"]) == 1
    edge = result["edges"][0]
    assert edge["support"] == 2
    assert edge["negated_count"] == 1  # 部分否定


def test_entity_graph_fully_negated_edge():
    now = datetime.now(timezone.utc)
    negated = {
        **_attack("e1", "a", "甲方", "设施一"),
        "qualifiers": [{"type": "polarity", "value": "negated", "scope": "event"}],
    }
    engine = SituationViewEngine(
        FakeCollection([_archive("a", now, 8)]),
        FakeCollection([negated]),
        DEFAULT_EVENT_REGISTRY,
    )

    result = engine.entity_graph(
        ViewQuery(now - timedelta(days=1), now + timedelta(days=1)),
        min_support=1)

    edge = result["edges"][0]
    assert edge["negated_count"] == edge["support"] == 1  # 全部否定 → 前端显示 ✗
