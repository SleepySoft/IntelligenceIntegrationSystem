import pytest
from pydantic import ValidationError

from event_engine.extraction import build_event_extraction_prompt
from prompts_event_v4 import EVENT_ANALYSIS_PROMPT_V40, EVENT_V4_PREDICATE_CATALOG
from ServiceComponent.IntelligenceHubDefines_v4 import (
    DEFAULT_EVENT_REGISTRY,
    ValuableIntelligenceV4,
    event_v4_json_schema,
    validate_analysis_result_v4,
)


def _valuable_payload():
    return {
        "kind": "valuable",
        "message": {
            "title": "A军袭击B设施",
            "brief": "A军袭击B设施。",
            "text": "A军对B设施实施袭击。",
        },
        "classification": {
            "taxonomy": "政治与安全",
            "subcategories": ["国防军事"],
        },
        "assessment": {
            "impact": "造成局部安全影响。",
            "reason": "正文包含明确军事行动。",
            "rate": {
                "影响广度": 4,
                "影响深度": 5,
                "新颖性与异常性": 5,
                "演化与连锁潜力": 4,
                "舆情及认知影响": 4,
                "可行动性": 5,
            },
            "tips": "",
        },
        "event_extraction": {
            "schema_version": "event-extraction/1.0",
            "primary_event_id": "E1",
            "entities": [
                {"id": "ENT1", "name": "A军", "type": "organization"},
                {"id": "ENT2", "name": "B设施", "type": "facility"},
            ],
            "events": [{
                "id": "E1",
                "core": {
                    "frame": {
                        "dynamics": "process",
                        "topology": "targeted",
                        "agency": "agentive",
                    },
                    "predicate": {"id": "attack", "surface": "袭击"},
                    "roles": {"actor": ["ENT1"], "target": ["ENT2"]},
                },
            }],
        },
    }


def test_valid_composed_event_v4_payload():
    result = validate_analysis_result_v4(_valuable_payload())
    assert result.event_extraction.primary_event_id == "E1"


def test_unregistered_predicate_is_rejected_by_event_registry():
    payload = _valuable_payload()
    payload["event_extraction"]["events"][0]["core"]["predicate"]["id"] = "airstrike"
    with pytest.raises(ValueError, match="unregistered predicate"):
        validate_analysis_result_v4(payload)


def test_frame_must_follow_event_registry():
    payload = _valuable_payload()
    payload["event_extraction"]["events"][0]["core"]["predicate"] = {
        "id": "aid", "surface": "援助",
    }
    with pytest.raises(ValueError, match="frame does not match"):
        validate_analysis_result_v4(payload)


def test_dangling_entity_reference_is_rejected():
    payload = _valuable_payload()
    payload["event_extraction"]["events"][0]["core"]["roles"]["actor"] = ["ENT9"]
    with pytest.raises(ValidationError):
        validate_analysis_result_v4(payload)


def test_event_prompt_is_standalone_and_iis_prompt_composes_it():
    standalone = build_event_extraction_prompt(DEFAULT_EVENT_REGISTRY)
    assert "event-extraction/1.0" in standalone
    assert "{{CONTENT}}" in standalone
    assert EVENT_V4_PREDICATE_CATALOG in standalone
    assert EVENT_V4_PREDICATE_CATALOG in EVENT_ANALYSIS_PROMPT_V40
    assert "IIS 情报分析要求" in EVENT_ANALYSIS_PROMPT_V40


def test_catalog_and_json_schema_follow_complete_event_registry():
    from event_engine.extraction import EventExtractionResult

    assert ValuableIntelligenceV4.model_fields["event_extraction"].annotation is EventExtractionResult
    assert len(DEFAULT_EVENT_REGISTRY) == len(EVENT_V4_PREDICATE_CATALOG.splitlines())
    assert "build_facility" in EVENT_V4_PREDICATE_CATALOG
    assert "issue_bond" in EVENT_V4_PREDICATE_CATALOG
    predicate_schema = event_v4_json_schema()["$defs"]["ExtractedPredicate"]["properties"]["id"]
    predicate_ids = predicate_schema["anyOf"][0]["enum"]
    assert predicate_ids == list(DEFAULT_EVENT_REGISTRY)
