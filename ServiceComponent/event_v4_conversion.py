"""把 EVENT_EXTRACTION 转换为可重建的事件查询索引文档。

实体和地域直接嵌入名称快照，不创建无别名、无消歧能力的全局实体 UUID。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import UUID, uuid5

from event_engine.extraction import EventExtractionResult, ExtractedEntity


@dataclass(frozen=True, slots=True)
class EventConversionResult:
    intelligence_uuid: str
    events: tuple[dict[str, Any], ...]


def entity_snapshot(entity: ExtractedEntity) -> dict[str, Any]:
    value: dict[str, Any] = {"name": entity.name.strip(), "type": entity.type}
    if entity.country_code:
        value["country_code"] = entity.country_code
    return value


def convert_event_extraction(
    extraction: EventExtractionResult,
    *,
    intelligence_uuid: UUID | str,
    registry: Any,
    observed_at: datetime | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> EventConversionResult:
    """生成事件查询索引；权威内容仍是归档记录中的 EVENT_EXTRACTION。"""

    source_uuid = str(intelligence_uuid)
    namespace = UUID(source_uuid)
    observed_at = observed_at or datetime.now(timezone.utc)
    if observed_at.tzinfo is None:
        raise ValueError("observed_at 必须包含时区")

    entities = {entity.id: entity for entity in extraction.entities}
    event_uuids = {event.id: str(uuid5(namespace, event.id)) for event in extraction.events}
    events = tuple(
        _event_document(
            event,
            intelligence_uuid=source_uuid,
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
    return EventConversionResult(intelligence_uuid=source_uuid, events=events)


def _event_document(
    event,
    *,
    intelligence_uuid: str,
    event_uuid: str,
    event_uuids: Mapping[str, str],
    entities: Mapping[str, ExtractedEntity],
    registry: Any,
    observed_at: datetime,
    is_primary: bool,
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    core = event.core
    spec = registry.get(core.predicate.id or "")
    role_groups = spec.role_groups if spec else {}

    roles = {
        role: [entity_snapshot(entities[key]) for key in references]
        for role, references in core.roles.items()
    }
    role_semantics = {
        role: role_groups[role].value
        for role in core.roles
        if role in role_groups
    }
    locations = [
        entity_snapshot(entities[key])
        for key in (core.context.event_location if core.context else [])
    ]
    qualifiers = []
    for value in event.qualifiers:
        item = value.model_dump(mode="python", exclude_none=True)
        if value.by:
            item["by"] = [entity_snapshot(entities[key]) for key in value.by]
        qualifiers.append(item)

    return {
        "event_uuid": event_uuid,
        "intelligence_uuid": intelligence_uuid,
        "local_event_id": event.id,
        "frame": core.frame.model_dump(mode="python"),
        "predicate": core.predicate.model_dump(mode="python"),
        "roles": roles,
        "role_semantics": role_semantics,
        "time": core.time.model_dump(mode="python", exclude_none=True) if core.time else {},
        "event_location": locations,
        "attributes": (
            core.attributes.model_dump(mode="python", exclude_none=True)
            if core.attributes else {}
        ),
        "qualifiers": qualifiers,
        "relations": [
            {
                "predicate": value.predicate,
                "target_event_uuid": event_uuids[value.target_event_id],
                "surface": value.surface,
            }
            for value in event.relations
        ],
        "observed_at": observed_at.astimezone(timezone.utc),
        "is_primary": is_primary,
        "metadata": dict(metadata),
    }
