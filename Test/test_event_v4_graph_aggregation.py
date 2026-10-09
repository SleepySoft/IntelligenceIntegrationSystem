import datetime

from ServiceComponent.DynamicGraphEngine import DynamicGraphEngine
from ServiceComponent.IntelligenceAggregationEngine import IntelligenceAggregationEngine


def _document():
    return {
        "_id": "intel-1",
        "intelligence_uuid": "intel-1",
        "archived_at": datetime.datetime(2026, 10, 9, tzinfo=datetime.timezone.utc),
        "analysis": {
            "message": {"title": "港口遇袭", "brief": "A组织袭击B港口。"},
            "event_extraction": {
                "entities": [
                    {"name": "A组织", "entity_type": "organization"},
                    {"name": "B港口", "entity_type": "facility"},
                ],
            },
        },
    }


def test_dynamic_graph_builds_nodes_from_event_v4_fields():
    engine = object.__new__(DynamicGraphEngine)
    document = _document()

    node = engine._create_graph_node(document, is_seed=True)

    assert node.uuid == "intel-1"
    assert node.title == "港口遇袭"
    assert node.brief == "A组织袭击B港口。"
    assert node.key_actors == ["A组织"]
    assert node.location == ["B港口"]
    assert engine._extract_rare_entities(document, {"A组织"}) == {"B港口"}


def test_aggregation_helpers_read_event_v4_identity_message_and_time():
    engine = object.__new__(IntelligenceAggregationEngine)
    document = _document()

    assert engine._document_id(document) == "intel-1"
    assert engine._message(document)["title"] == "港口遇袭"
    assert engine._get_archived_sort_value(document) == document["archived_at"].timestamp()
