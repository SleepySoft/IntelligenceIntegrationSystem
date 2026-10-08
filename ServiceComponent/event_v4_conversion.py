"""把 LLM 的 Event V4 局部抽取结果转换为可持久化事件观察。"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol
from uuid import UUID, uuid5

from event_engine.extraction import EventExtractionResult, ExtractedEntity
from event_engine.schema import (
    Agency,
    Dynamics,
    EventRecord,
    EventRelation,
    Frame,
    Predicate,
    Qualifier,
    RoleBinding,
    SemanticRoleGroup,
    TimeExpression,
    Topology,
)


ENTITY_NAMESPACE = UUID("e23ca3db-3477-5b81-a391-206f12dfb0af")


@dataclass(frozen=True, slots=True)
class ResolvedEntity:
    """局部实体解析后的全局身份，同时保留规范化键供存储去重。"""

    uuid: UUID
    identity_key: str
    canonical_name: str
    entity_type: str
    country_code: str | None = None


class EntityResolver(Protocol):
    def resolve(self, entity: ExtractedEntity, *, intelligence_uuid: UUID) -> ResolvedEntity:
        """将一个来源内实体解析成全局实体身份。"""


class DeterministicEntityResolver:
    """保守的第一阶段实体解析器。

    仅合并规范名称、实体类型和国家代码完全一致的实体。UUID 由规范键稳定派生，
    因而并发和重试不会生成重复身份。更复杂的别名或人工合并可替换此端口。
    """

    def __init__(self, repository: Any = None):
        self._repository = repository

    def resolve(self, entity: ExtractedEntity, *, intelligence_uuid: UUID) -> ResolvedEntity:
        del intelligence_uuid  # 基础策略刻意不把来源情报纳入全局身份。
        name = normalize_entity_name(entity.name)
        identity_key = "|".join((entity.type, entity.country_code or "", name.casefold()))
        resolved = ResolvedEntity(
            uuid=uuid5(ENTITY_NAMESPACE, identity_key),
            identity_key=identity_key,
            canonical_name=name,
            entity_type=entity.type,
            country_code=entity.country_code,
        )
        if self._repository is not None:
            stored = self._repository.upsert(resolved, alias=entity.name)
            if stored is not None:
                resolved = stored
        return resolved


@dataclass(frozen=True, slots=True)
class EventConversionResult:
    intelligence_uuid: UUID
    entities: Mapping[str, ResolvedEntity]
    events: tuple[EventRecord, ...]
    primary_event_uuid: UUID


def normalize_entity_name(value: str) -> str:
    """执行不丢失语言信息的最小名称规范化。"""

    normalized = unicodedata.normalize("NFKC", value)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if not normalized:
        raise ValueError("实体规范名称不能为空")
    return normalized


def convert_event_extraction(
    extraction: EventExtractionResult,
    *,
    intelligence_uuid: UUID,
    entity_resolver: EntityResolver,
    registry: Any,
    observed_at: datetime | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> EventConversionResult:
    """将一个已校验抽取结果转换成 EventRecord，且转换对重试幂等。"""

    observed_at = observed_at or datetime.now(timezone.utc)
    if observed_at.tzinfo is None:
        raise ValueError("observed_at 必须包含时区")

    entities = {
        entity.id: entity_resolver.resolve(entity, intelligence_uuid=intelligence_uuid)
        for entity in extraction.entities
    }
    event_uuids = {
        event.id: uuid5(intelligence_uuid, event.id)
        for event in extraction.events
    }

    records = tuple(
        _convert_event(
            event,
            intelligence_uuid=intelligence_uuid,
            event_uuid=event_uuids[event.id],
            event_uuids=event_uuids,
            entities=entities,
            registry=registry,
            observed_at=observed_at,
            is_primary=event.id == extraction.primary_event_id,
            metadata=metadata or {},
        )
        for event in extraction.events
    )
    return EventConversionResult(
        intelligence_uuid=intelligence_uuid,
        entities=entities,
        events=records,
        primary_event_uuid=event_uuids[extraction.primary_event_id],
    )


def _convert_event(
    event,
    *,
    intelligence_uuid: UUID,
    event_uuid: UUID,
    event_uuids: Mapping[str, UUID],
    entities: Mapping[str, ResolvedEntity],
    registry: Any,
    observed_at: datetime,
    is_primary: bool,
    metadata: Mapping[str, Any],
) -> EventRecord:
    core = event.core
    spec = registry.get(core.predicate.id or "")
    role_groups = spec.role_groups if spec else {}
    bindings = tuple(
        RoleBinding(
            role=role,
            entity_uuid=entities[local_entity_id].uuid,
            semantic_group=role_groups.get(role, SemanticRoleGroup.OTHER),
            local_entity_id=local_entity_id,
        )
        for role, local_entity_ids in core.roles.items()
        for local_entity_id in local_entity_ids
    )
    locations = tuple(
        entities[local_entity_id].uuid
        for local_entity_id in (core.context.event_location if core.context else [])
    )
    times = {
        name: _convert_time(value)
        for name, value in (
            core.time.model_dump(exclude_none=True).items() if core.time else ()
        )
    }
    attributes = (
        core.attributes.model_dump(exclude_none=True) if core.attributes else {}
    )
    qualifiers = tuple(
        Qualifier(
            id=value.id,
            type=value.type,
            value=value.value,
            scope=value.scope,
            by=tuple(entities[key].uuid for key in (value.by or ())),
            time=_convert_time(value.time) if value.time else None,
            surface=value.surface,
        )
        for value in event.qualifiers
    )
    relations = tuple(
        EventRelation(
            predicate=value.predicate,
            target_event_uuid=event_uuids[value.target_event_id],
            surface=value.surface,
        )
        for value in event.relations
    )
    record = EventRecord(
        uuid=event_uuid,
        intelligence_uuid=intelligence_uuid,
        local_event_id=event.id,
        frame=Frame(
            Dynamics(core.frame.dynamics),
            Topology(core.frame.topology),
            Agency(core.frame.agency),
        ),
        predicate=Predicate(
            id=core.predicate.id,
            surface=core.predicate.surface,
            gloss=core.predicate.gloss,
        ),
        role_bindings=bindings,
        time=times,
        location_entity_uuids=locations,
        attributes=attributes,
        qualifiers=qualifiers,
        relations=relations,
        observed_at=observed_at.astimezone(timezone.utc),
        is_primary=is_primary,
        metadata=dict(metadata),
    )
    errors = registry.validate(record.ir, require_known=core.predicate.id is not None)
    if errors:
        raise ValueError(f"事件 {event.id} 转换后校验失败: {'; '.join(errors)}")
    return record


def _convert_time(value: Any) -> TimeExpression:
    if isinstance(value, dict):
        return TimeExpression(**value)
    return TimeExpression(**value.model_dump())
