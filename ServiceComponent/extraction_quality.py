"""Event V4 抽取结果的轻量语义质量检查（仅统计，不拦截）。

对已通过结构与 Registry 校验的 EVENT_EXTRACTION 做确定性交叉检查，产出质量
指标写入归档 APPENDIX 或影子分析记录，供按 prompt 版本/模型聚合对比。
所有检查都是只读统计：不修改结果、不触发重试、不得影响分析主链。

检查项：

- 未登记角色名：已登记谓词使用了 Registry 未声明的角色；
- 角色-实体类型冲突：按角色的 SemanticRoleGroup 检查实体类型兼容性
  （如 AGENT 组角色绑定 product/information 等抽象实体）；
  组级限制之外，议题（topic）槽按角色名禁用人物/动物类实体；
- surface 接地率：谓词/时间 surface 是否逐字出现在原文中（规范化后子串匹配）。
"""

from __future__ import annotations

import re
from typing import Any

from event_engine.schema import SemanticRoleGroup

# 只列出可以确定性判正的语义组；其余组（THEME/AFFECTED/SOURCE 等）语义太宽，不做类型限制。
_AGENT_LIKE_TYPES = frozenset({"person", "organization", "geopolitical_entity", "position"})
_LOCATION_TYPES = frozenset({"location", "geopolitical_entity", "facility"})

GROUP_ALLOWED_ENTITY_TYPES = {
    SemanticRoleGroup.AGENT: _AGENT_LIKE_TYPES,
    SemanticRoleGroup.AUTHORITY: _AGENT_LIKE_TYPES,
    SemanticRoleGroup.LOCATION: _LOCATION_TYPES,
}

# 组级限制之外按角色名判负的名单：topic（议题）属于 THEME 组，语义上是抽象事项，
# 人物/动物不可能作议题（典型错位：把表态人物填进 topic，事项反而塞进谓词 surface）。
_ROLE_DENIED_ENTITY_TYPES = {
    "topic": frozenset({"person", "animal"}),
}

_DETAIL_CAP = 10


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).casefold()


def _check_roles(extraction, registry) -> tuple[list[dict], list[dict]]:
    unregistered_roles: list[dict] = []
    type_conflicts: list[dict] = []
    entities = {entity.id: entity for entity in extraction.entities}
    for event in extraction.events:
        predicate_id = event.core.predicate.id
        if predicate_id is None:
            continue
        spec = registry.get(predicate_id)
        if spec is None:
            continue
        for role, refs in event.core.roles.items():
            group = spec.role_groups.get(role)
            if group is None:
                unregistered_roles.append(
                    {"event": event.id, "predicate": predicate_id, "role": role})
                continue
            allowed = GROUP_ALLOWED_ENTITY_TYPES.get(SemanticRoleGroup(group))
            denied = _ROLE_DENIED_ENTITY_TYPES.get(role)
            if not allowed and not denied:
                continue
            for ref in refs:
                entity = entities.get(ref)
                if entity is None:
                    continue
                if (allowed and entity.type not in allowed) or (
                        denied and entity.type in denied):
                    type_conflicts.append({
                        "event": event.id,
                        "predicate": predicate_id,
                        "role": role,
                        "group": SemanticRoleGroup(group).value,
                        "entity": entity.name,
                        "entity_type": entity.type,
                    })
    return unregistered_roles, type_conflicts


def _surface_hit_rate(surfaces: list[str], normalized_source: str) -> dict[str, int]:
    total = len(surfaces)
    hit = sum(
        1 for surface in surfaces
        if _normalize_text(surface) and _normalize_text(surface) in normalized_source
    )
    return {"total": total, "hit": hit}


def _collect_surfaces(extraction) -> tuple[list[str], list[str]]:
    predicate_surfaces: list[str] = []
    time_surfaces: list[str] = []
    for event in extraction.events:
        if event.core.predicate.surface:
            predicate_surfaces.append(event.core.predicate.surface)
        if event.core.time is not None:
            for expression in event.core.time.model_dump(exclude_none=True).values():
                surface = expression.get("surface")
                if surface:
                    time_surfaces.append(surface)
    return predicate_surfaces, time_surfaces


def compute_extraction_quality(extraction, registry, *, raw_text: str = "") -> dict[str, Any]:
    """计算抽取质量指标。extraction 须已通过 validate_event_extraction 校验。"""
    unregistered_roles, type_conflicts = _check_roles(extraction, registry)
    normalized_source = _normalize_text(raw_text)
    predicate_surfaces, time_surfaces = _collect_surfaces(extraction)
    return {
        "unregistered_role_count": len(unregistered_roles),
        "unregistered_roles": unregistered_roles[:_DETAIL_CAP],
        "role_type_conflict_count": len(type_conflicts),
        "role_type_conflicts": type_conflicts[:_DETAIL_CAP],
        "surface_grounding": {
            "predicate": _surface_hit_rate(predicate_surfaces, normalized_source),
            "time": _surface_hit_rate(time_surfaces, normalized_source),
        },
    }
