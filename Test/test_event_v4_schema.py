import pytest
from pydantic import ValidationError

from prompts_event_v4 import EVENT_ANALYSIS_PROMPT_V40, EVENT_V4_PREDICATE_CATALOG
from ServiceComponent.IntelligenceHubDefines_v4 import (
    EventPredicateV4,
    PREDICATE_SPECS,
    event_v4_json_schema,
    validate_analysis_result_v4,
)


def _valuable_payload():
    return {
        "EVENT_SCHEMA_VERSION": "4.0",
        "PRIMARY_EVENT_ID": "E1",
        "ENTITIES": [
            {"id": "ENT1", "name": "A军", "type": "organization"},
            {"id": "ENT2", "name": "B设施", "type": "facility"},
        ],
        "EVENTS": [
            {
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
            }
        ],
        "EVENT_TITLE": "A军袭击B设施",
        "EVENT_BRIEF": "A军袭击B设施。",
        "EVENT_TEXT": "A军对B设施实施袭击。",
        "TAXONOMY": "政治与安全",
        "SUB_CATEGORY": ["国防军事"],
        "IMPACT": "造成局部安全影响。",
        "REASON": "正文包含明确军事行动。",
        "RATE": {
            "影响广度": 4,
            "影响深度": 5,
            "新颖性与异常性": 5,
            "演化与连锁潜力": 4,
            "舆情及认知影响": 4,
            "可行动性": 5,
        },
        "TIPS": "",
    }


def test_valid_event_v4_payload():
    result = validate_analysis_result_v4(_valuable_payload())
    assert result.PRIMARY_EVENT_ID == "E1"


def test_invalid_predicate_is_rejected():
    with pytest.raises(ValidationError):
        EventPredicateV4.model_validate({"id": "airstrike", "surface": "空袭"})


def test_dangling_entity_reference_is_rejected():
    payload = _valuable_payload()
    payload["EVENTS"][0]["core"]["roles"]["actor"] = ["ENT9"]
    with pytest.raises(ValidationError):
        validate_analysis_result_v4(payload)


def test_prompt_embeds_complete_predicate_catalog():
    assert len(PREDICATE_SPECS) == len(EVENT_V4_PREDICATE_CATALOG.splitlines())
    assert EVENT_V4_PREDICATE_CATALOG in EVENT_ANALYSIS_PROMPT_V40
    assert "{{CURRENT_DATE}}" in EVENT_ANALYSIS_PROMPT_V40
    assert "{{CONTENT}}" in EVENT_ANALYSIS_PROMPT_V40
    assert "{{SIMILAR_MESSAGES}}" in EVENT_ANALYSIS_PROMPT_V40


def test_json_schema_contains_closed_predicates():
    schema_text = str(event_v4_json_schema())
    assert "armed_conflict" in schema_text
    assert "attack" in schema_text
    assert "acquire" in schema_text