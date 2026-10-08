from datetime import datetime, timezone
from uuid import UUID, uuid5

from ServiceComponent.IntelligenceHubDefines_v4 import (
    DEFAULT_EVENT_REGISTRY,
    validate_analysis_result_v4,
)
from ServiceComponent.event_v4_conversion import (
    DeterministicEntityResolver,
    convert_event_extraction,
)


def _payload():
    return {
        "kind": "valuable",
        "message": {"title": "A军袭击B设施", "brief": "A军袭击B设施。", "text": "详细简报。"},
        "classification": {"taxonomy": "政治与安全", "subcategories": ["国防军事"]},
        "assessment": {
            "impact": "造成安全影响。", "reason": "存在明确军事行动。", "tips": "",
            "rate": {
                "影响广度": 4, "影响深度": 5, "新颖性与异常性": 6,
                "演化与连锁潜力": 5, "舆情及认知影响": 3, "可行动性": 6,
            },
        },
        "event_extraction": {
            "schema_version": "event-extraction/1.0",
            "primary_event_id": "E1",
            "entities": [
                {"id": "ENT1", "name": " A军 ", "type": "organization"},
                {"id": "ENT2", "name": "B设施", "type": "facility", "country_code": "CN"},
            ],
            "events": [
                {
                    "id": "E1",
                    "core": {
                        "frame": {"dynamics": "process", "topology": "targeted", "agency": "agentive"},
                        "predicate": {"id": "attack", "surface": "袭击"},
                        "roles": {"actor": ["ENT1"], "target": ["ENT2"]},
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
        analysis.event_extraction,
        intelligence_uuid=intelligence_uuid,
        entity_resolver=DeterministicEntityResolver(),
        registry=DEFAULT_EVENT_REGISTRY,
        observed_at=observed_at,
    )
    second = convert_event_extraction(
        analysis.event_extraction,
        intelligence_uuid=intelligence_uuid,
        entity_resolver=DeterministicEntityResolver(),
        registry=DEFAULT_EVENT_REGISTRY,
        observed_at=observed_at,
    )

    assert first.events == second.events
    assert first.primary_event_uuid == uuid5(intelligence_uuid, "E1")
    assert first.events[0].relations[0].target_event_uuid == uuid5(intelligence_uuid, "E2")
    assert first.events[0].role_bindings[0].local_entity_id == "ENT1"
    assert first.events[0].qualifiers[0].by == (first.entities["ENT1"].uuid,)
    assert first.events[0].location_entity_uuids == (first.entities["ENT2"].uuid,)
    assert first.entities["ENT1"].canonical_name == "A军"

