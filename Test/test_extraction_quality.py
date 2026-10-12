"""extraction_quality：归档后抽取质量统计（仅参考）的单元测试。"""

from ServiceComponent.extraction_quality import compute_extraction_quality
from ServiceComponent.IntelligenceHubDefines_v4 import (
    DEFAULT_EVENT_REGISTRY,
    validate_analysis_result_v4,
)

_REGISTRY = DEFAULT_EVENT_REGISTRY


def _extraction(roles, entities, surface="袭击", time_surface=None):
    time_block = None
    if time_surface:
        time_block = {
            "event_time": {
                "normalized": "2026-10-12", "precision": "day",
                "approximate": False, "surface": time_surface,
            },
        }
    event = {
        "id": "E1",
        "core": {
            "frame": {"dynamics": "process", "topology": "targeted", "agency": "agentive"},
            "predicate": {"id": "attack", "surface": surface},
            "roles": roles,
        },
    }
    if time_block:
        event["core"]["time"] = time_block
    return {
        "schema_version": "event-extraction/1.0",
        "primary_event_id": "E1",
        "entities": entities,
        "events": [event],
    }


def _quality(roles, entities, surface="袭击", time_surface=None, raw_text=""):
    payload = _extraction(roles, entities, surface=surface, time_surface=time_surface)
    extraction = validate_analysis_result_v4({
        "TAXONOMY": "政治与安全", "SUB_CATEGORY": ["国防军事"],
        "EVENT_TITLE": "t", "EVENT_BRIEF": "b", "EVENT_TEXT": "x",
        "IMPACT": "i", "REASON": "r", "TIPS": "",
        "RATE": {"影响广度": 4, "影响深度": 5, "新颖性与异常性": 6,
                 "演化与连锁潜力": 5, "舆情及认知影响": 3, "可行动性": 6},
        "EVENT_EXTRACTION": payload,
    }).EVENT_EXTRACTION
    return compute_extraction_quality(extraction, _REGISTRY, raw_text=raw_text)


def test_registered_roles_and_compatible_types_produce_clean_report():
    quality = _quality(
        roles={"actor": ["ENT1"], "target": ["ENT2"]},
        entities=[
            {"id": "ENT1", "name": "A军", "type": "organization"},
            {"id": "ENT2", "name": "B设施", "type": "facility"},
        ],
    )
    assert quality["unregistered_role_count"] == 0
    assert quality["role_type_conflict_count"] == 0


def test_unregistered_role_is_flagged():
    quality = _quality(
        roles={"actor": ["ENT1"], "launcher": ["ENT2"]},
        entities=[
            {"id": "ENT1", "name": "A军", "type": "organization"},
            {"id": "ENT2", "name": "某导弹", "type": "equipment"},
        ],
    )
    assert quality["unregistered_role_count"] == 1
    assert quality["unregistered_roles"][0]["role"] == "launcher"


def test_agent_role_binding_abstract_entity_is_flagged():
    # NAZA 类错误：AGENT 组角色绑定了 product 类型实体。
    quality = _quality(
        roles={"actor": ["ENT1"]},
        entities=[{"id": "ENT1", "name": "纪录片《NAZA》", "type": "product"}],
    )
    assert quality["role_type_conflict_count"] == 1
    conflict = quality["role_type_conflicts"][0]
    assert conflict["role"] == "actor"
    assert conflict["entity_type"] == "product"


def test_surface_grounding_uses_normalized_substring():
    quality = _quality(
        roles={"actor": ["ENT1"]},
        entities=[{"id": "ENT1", "name": "A军", "type": "organization"}],
        surface="发 动\n袭击",  # 空白差异不影响命中
        time_surface="10月12日",
        raw_text="A 军 对 B 设 施 发 动 袭 击。事发于 10月12日。",
    )
    grounding = quality["surface_grounding"]
    assert grounding["predicate"] == {"total": 1, "hit": 1}
    assert grounding["time"] == {"total": 1, "hit": 1}


def test_surface_grounding_flags_translated_surface():
    quality = _quality(
        roles={"actor": ["ENT1"]},
        entities=[{"id": "ENT1", "name": "A军", "type": "organization"}],
        surface="袭击",  # 原文是英文，surface 被翻译 → 不命中
        raw_text="Force A launched an attack on facility B.",
    )
    assert quality["surface_grounding"]["predicate"] == {"total": 1, "hit": 0}
