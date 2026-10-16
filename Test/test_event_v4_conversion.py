from datetime import datetime, timezone
from uuid import UUID, uuid5

from ServiceComponent.IntelligenceHubDefines_v4 import (
    DEFAULT_EVENT_REGISTRY,
    validate_analysis_result_v4,
)
from ServiceComponent.event_v4_conversion import convert_event_extraction
from ServiceComponent.event_v4_archive import derive_event_projections


def _payload():
    return {
        "TAXONOMY": "政治与安全", "SUB_CATEGORY": ["国防军事"],
        "EVENT_TITLE": "A军袭击B设施", "EVENT_BRIEF": "A军袭击B设施。", "EVENT_TEXT": "详细简报。",
        "IMPACT": "造成安全影响。", "REASON": "存在明确军事行动。", "TIPS": "",
        "RATE": {
                "影响广度": 4, "影响深度": 5, "新颖性与异常性": 6,
                "演化与连锁潜力": 5, "舆情及认知影响": 3, "可行动性": 6,
        },
        "EVENT_EXTRACTION": {
            "schema_version": "event-extraction/1.0",
            "primary_event_id": "E1",
            "entities": [
                {"id": "ENT1", "name": " A军 ", "type": "organization"},
                {"id": "ENT2", "name": "B设施", "type": "facility", "country_code": "CN"},
                {"id": "ENT3", "name": "上海", "type": "location", "country_code": "CN"},
            ],
            "events": [
                {
                    "id": "E1",
                    "core": {
                        "frame": {"dynamics": "process", "topology": "targeted", "agency": "agentive"},
                        "predicate": {"id": "attack", "surface": "袭击"},
                        "roles": {"actor": ["ENT1"], "target": ["ENT2"], "location": ["ENT3"]},
                        "context": {"event_location": ["ENT2"]},
                        "time": {"event_time": {"normalized": "2026-10-08", "precision": "day", "approximate": False, "surface": "10月8日"}},
                    },
                    "qualifiers": [{"id": "Q1", "type": "epistemic", "value": "asserted", "by": ["ENT1"], "scope": "event"}],
                    "relations": [{"predicate": "precedes", "target_event_id": "E2"}],
                },
                {
                    "id": "E2",
                    "core": {
                        "frame": {"dynamics": "state", "topology": "intrinsic", "agency": "unknown"},
                        "predicate": {"id": None, "surface": "设施受损", "gloss": "设施处于受损状态"},
                        "roles": {"subject": ["ENT2"]},
                    },
                },
            ],
        },
    }


def test_conversion_resolves_all_references_and_uses_stable_event_uuids():
    analysis = validate_analysis_result_v4(_payload())
    intelligence_uuid = UUID("df39ffef-1ab6-43a6-b9cf-5845e612409f")
    observed_at = datetime(2026, 10, 8, 10, tzinfo=timezone.utc)
    first = convert_event_extraction(
        analysis.EVENT_EXTRACTION,
        intelligence_uuid=intelligence_uuid,
        registry=DEFAULT_EVENT_REGISTRY,
        observed_at=observed_at,
    )
    second = convert_event_extraction(
        analysis.EVENT_EXTRACTION,
        intelligence_uuid=intelligence_uuid,
        registry=DEFAULT_EVENT_REGISTRY,
        observed_at=observed_at,
    )

    assert first.events == second.events
    assert first.events[0]["event_uuid"] == str(uuid5(intelligence_uuid, "E1"))
    assert first.events[0]["relations"][0]["target_event_uuid"] == str(uuid5(intelligence_uuid, "E2"))
    assert first.events[0]["roles"]["actor"] == [{"name": "A军", "type": "organization"}]
    assert first.events[0]["qualifiers"][0]["by"] == [{"name": "A军", "type": "organization"}]
    assert first.events[0]["event_location"] == [{"name": "B设施", "type": "facility", "country_code": "CN"}]
    assert "entity_uuid" not in str(first.events)
    projections = derive_event_projections(analysis.EVENT_EXTRACTION)
    assert projections["LOCATION"] == ["B设施", "上海"]
    assert projections["ORGANIZATION"] == ["A军"]

