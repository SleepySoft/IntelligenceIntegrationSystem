import datetime

from ServiceComponent.DynamicGraphEngine import DynamicGraphEngine
from ServiceComponent.IntelligenceAggregationEngine import IntelligenceAggregationEngine
from ServiceComponent.intelligence_contracts import (
    APPENDIX_CONTENT_TIME, APPENDIX_CONTENT_TIME_SOURCE, APPENDIX_RAW_DATA,
)


def _document():
    return {
        "UUID": "intel-1",
        "EVENT_TITLE": "港口遇袭", "EVENT_BRIEF": "A组织袭击B港口。",
        "APPENDIX": {
            APPENDIX_CONTENT_TIME: datetime.datetime(2026, 10, 8, 8, 30, tzinfo=datetime.timezone.utc),
            APPENDIX_CONTENT_TIME_SOURCE: "published",
            APPENDIX_RAW_DATA: {"pub_time": datetime.datetime(2026, 10, 8, 8, 30, tzinfo=datetime.timezone.utc)},
        },
        "EVENT_EXTRACTION": {
                "entities": [
                    {"name": "A组织", "type": "organization"},
                    {"name": "B港口", "type": "facility"},
                ],
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
    assert engine._get_content_sort_value(document) == document["APPENDIX"][APPENDIX_CONTENT_TIME].timestamp()
