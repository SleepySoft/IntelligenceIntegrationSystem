from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from uuid import UUID, uuid5

from ..domain.models import (
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
from ..domain.specs import DEFAULT_PREDICATE_SPECS, DEFAULT_ROLE_GROUPS
from .service import EventEngine


@dataclass(frozen=True, slots=True)
class FileEntity:
    """文件中的实体定义及其稳定全局 UUID。"""

    key: str
    uuid: UUID
    name: str
    type: str
    country_code: str | None = None


@dataclass(frozen=True, slots=True)
class LoadedEventFile:
    """完整解析后的单文件事件数据集。"""

    dataset_id: str
    entities: Mapping[str, FileEntity]
    events: tuple[EventRecord, ...]

    def entity_name(self, entity_uuid: UUID) -> str:
        for entity in self.entities.values():
            if entity.uuid == entity_uuid:
                return entity.name
        return str(entity_uuid)


def load_event_file(path: str | Path) -> LoadedEventFile:
    """读取一个 Event File v1 JSON 文件，不执行任何持久化。"""

    source = Path(path)
    with source.open("r", encoding="utf-8") as stream:
        payload = json.load(stream)

    if payload.get("schema_version") != "event-file/1.0":
        raise ValueError("schema_version 必须为 event-file/1.0")
    dataset_id = _required_text(payload, "dataset_id")
    namespace = uuid5(UUID("4ae43b98-9fbf-4e4d-b663-14bf9e91df5c"), dataset_id)

    entities = _load_entities(payload.get("entities"), namespace)
    raw_events = payload.get("events")
    if not isinstance(raw_events, list) or not raw_events:
        raise ValueError("events 必须是非空数组")

    event_uuids: dict[str, UUID] = {}
    for raw_event in raw_events:
        key = _required_text(raw_event, "id")
        if key in event_uuids:
            raise ValueError(f"重复事件 ID: {key}")
        event_uuids[key] = _uuid_or_derived(raw_event.get("uuid"), namespace, f"event:{key}")
    if len(set(event_uuids.values())) != len(event_uuids):
        raise ValueError("事件 UUID 存在重复")

    events = tuple(
        _load_event(raw_event, namespace, entities, event_uuids)
        for raw_event in raw_events
    )
    return LoadedEventFile(dataset_id=dataset_id, entities=entities, events=events)


def ingest_event_file(path: str | Path, engine: EventEngine) -> LoadedEventFile:
    """校验并把单个文件内的全部事件注册到 EventEngine。"""

    dataset = load_event_file(path)
    duplicates = [event.uuid for event in dataset.events if engine.events.get(event.uuid)]
    if duplicates:
        raise ValueError(f"事件已经存在，未执行导入: {duplicates}")
    for event in dataset.events:
        engine.register_event(event)
    return dataset


def _load_entities(raw_entities: Any, namespace: UUID) -> dict[str, FileEntity]:
    if not isinstance(raw_entities, list) or not raw_entities:
        raise ValueError("entities 必须是非空数组")
    result: dict[str, FileEntity] = {}
    uuids: set[UUID] = set()
    for raw in raw_entities:
        key = _required_text(raw, "id")
        if key in result:
            raise ValueError(f"重复实体 ID: {key}")
        entity_uuid = _uuid_or_derived(raw.get("uuid"), namespace, f"entity:{key}")
        if entity_uuid in uuids:
            raise ValueError(f"重复实体 UUID: {entity_uuid}")
        country_code = raw.get("country_code")
        if country_code is not None and (
            not isinstance(country_code, str)
            or len(country_code) != 2
            or country_code.upper() != country_code
        ):
            raise ValueError(f"实体 {key} 的 country_code 必须是两位大写代码")
        result[key] = FileEntity(
            key=key,
            uuid=entity_uuid,
            name=_required_text(raw, "name"),
            type=_required_text(raw, "type"),
            country_code=country_code,
        )
        uuids.add(entity_uuid)
    return result


def _load_event(
    raw: Mapping[str, Any],
    namespace: UUID,
    entities: Mapping[str, FileEntity],
    event_uuids: Mapping[str, UUID],
) -> EventRecord:
    event_key = _required_text(raw, "id")
    frame = raw.get("frame")
    predicate = raw.get("predicate")
    roles = raw.get("roles")
    if not isinstance(frame, dict) or not isinstance(predicate, dict):
        raise ValueError(f"事件 {event_key} 缺少 frame 或 predicate")
    if not isinstance(roles, dict) or not roles:
        raise ValueError(f"事件 {event_key} 的 roles 必须是非空对象")

    predicate_id = predicate.get("id")
    if predicate_id is not None and not isinstance(predicate_id, str):
        raise ValueError(f"事件 {event_key} 的 predicate.id 无效")
    bindings: list[RoleBinding] = []
    role_groups = DEFAULT_PREDICATE_SPECS.get(predicate_id or "")
    role_groups_map = role_groups.role_groups if role_groups else DEFAULT_ROLE_GROUPS
    for role, entity_keys in roles.items():
        if not isinstance(entity_keys, list) or not entity_keys:
            raise ValueError(f"事件 {event_key} 的角色 {role} 必须引用至少一个实体")
        semantic_group = role_groups_map.get(role, SemanticRoleGroup.OTHER)
        for entity_key in entity_keys:
            entity = _entity_ref(entities, entity_key, event_key)
            bindings.append(RoleBinding(role, entity.uuid, semantic_group, entity.key))

    locations = tuple(
        _entity_ref(entities, key, event_key).uuid
        for key in raw.get("locations", [])
    )
    times = {
        key: TimeExpression(
            normalized=value.get("normalized"),
            precision=_required_text(value, "precision"),
            approximate=bool(value.get("approximate", False)),
            surface=_required_text(value, "surface"),
        )
        for key, value in raw.get("time", {}).items()
    }
    qualifiers = tuple(
        Qualifier(
            id=_required_text(value, "id"),
            type=_required_text(value, "type"),
            value=_required_text(value, "value"),
            scope=value.get("scope", "event"),
            by=tuple(_entity_ref(entities, key, event_key).uuid for key in value.get("by", [])),
            time=_load_time_expression(value.get("time")),
            surface=value.get("surface"),
        )
        for value in raw.get("qualifiers", [])
    )
    relations = tuple(
        EventRelation(
            predicate=_required_text(value, "predicate"),
            target_event_uuid=_event_ref(event_uuids, value.get("target"), event_key),
            surface=value.get("surface"),
        )
        for value in raw.get("relations", [])
    )
    observed_at = _datetime(raw.get("observed_at"), event_key)
    intelligence_key = str(raw.get("intelligence_id") or event_key)

    metadata = dict(raw.get("metadata", {}))
    if "topic" in raw:
        metadata.setdefault("topic", raw["topic"])

    return EventRecord(
        uuid=event_uuids[event_key],
        intelligence_uuid=_uuid_or_derived(
            raw.get("intelligence_uuid"), namespace, f"intelligence:{intelligence_key}"
        ),
        local_event_id=str(raw.get("local_event_id") or "E1"),
        frame=Frame(
            Dynamics(_required_text(frame, "dynamics")),
            Topology(_required_text(frame, "topology")),
            Agency(_required_text(frame, "agency")),
        ),
        predicate=Predicate(
            id=predicate_id,
            surface=_required_text(predicate, "surface"),
            gloss=predicate.get("gloss"),
        ),
        role_bindings=tuple(bindings),
        time=times,
        location_entity_uuids=locations,
        attributes=dict(raw.get("attributes", {})),
        qualifiers=qualifiers,
        relations=relations,
        observed_at=observed_at,
        is_primary=bool(raw.get("is_primary", True)),
        metadata=metadata,
    )


def _load_time_expression(value: Any) -> TimeExpression | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("qualifier.time 必须是对象")
    return TimeExpression(
        normalized=value.get("normalized"),
        precision=_required_text(value, "precision"),
        approximate=bool(value.get("approximate", False)),
        surface=_required_text(value, "surface"),
    )


def _entity_ref(
    entities: Mapping[str, FileEntity], key: Any, event_key: str
) -> FileEntity:
    if not isinstance(key, str) or key not in entities:
        raise ValueError(f"事件 {event_key} 引用了未知实体: {key}")
    return entities[key]


def _event_ref(event_uuids: Mapping[str, UUID], key: Any, event_key: str) -> UUID:
    if not isinstance(key, str) or key not in event_uuids:
        raise ValueError(f"事件 {event_key} 引用了未知事件: {key}")
    if key == event_key:
        raise ValueError(f"事件 {event_key} 不能关联自身")
    return event_uuids[key]


def _datetime(value: Any, event_key: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"事件 {event_key} 必须提供 observed_at")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"事件 {event_key} 的 observed_at 必须包含时区")
    return parsed.astimezone(timezone.utc)


def _uuid_or_derived(value: Any, namespace: UUID, name: str) -> UUID:
    if value is None:
        return uuid5(namespace, name)
    try:
        return UUID(str(value))
    except ValueError as exc:
        raise ValueError(f"无效 UUID: {value}") from exc


def _required_text(value: Mapping[str, Any], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result.strip():
        raise ValueError(f"{key} 必须是非空字符串")
    return result
