"""Event V4 归档模型、APPENDIX 构造与存储端口。"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Protocol

from pydantic import ConfigDict, Field, model_validator

from ServiceComponent.IntelligenceHubDefines_v4 import AnalysisResultV4, DEFAULT_EVENT_REGISTRY
from ServiceComponent.intelligence_contracts import (
    APPENDIX_AI_MODEL,
    APPENDIX_AI_SERVICE,
    APPENDIX_CONTENT_TIME,
    APPENDIX_CONTENT_TIME_SOURCE,
    APPENDIX_FIELD,
    APPENDIX_MANUAL_RATING,
    APPENDIX_PROMPT_VERSION,
    APPENDIX_RAW_DATA,
    APPENDIX_SCHEMA_VERSION,
    APPENDIX_SUBSYSTEM,
    APPENDIX_TIME_ARCHIVED,
    APPENDIX_TIME_DONE,
    APPENDIX_TIME_GOT,
    APPENDIX_TIME_PUB,
    APPENDIX_TOTAL_SCORE,
    APPENDIX_TRANSLATED_REVISION,
    INTELLIGENCE_ARCHIVE_SCHEMA_VERSION,
)
from ServiceComponent.intelligence_time import resolve_content_time


class ArchivedDataV4(AnalysisResultV4):
    """V2-shaped archive: analysis result plus identity, projections and APPENDIX."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    UUID: str = Field(..., min_length=1)
    INFORMANT: str = Field(..., min_length=1)
    TIME: list[str] = Field(default_factory=list)
    LOCATION: list[str] = Field(default_factory=list)
    PEOPLE: list[str] = Field(default_factory=list)
    ORGANIZATION: list[str] = Field(default_factory=list)
    APPENDIX: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def populate_event_projections(self):
        if self.EVENT_EXTRACTION is None:
            self.TIME = []
            self.LOCATION = []
            self.PEOPLE = []
            self.ORGANIZATION = []
            return self
        projections = derive_event_projections(self.EVENT_EXTRACTION)
        self.TIME = projections["TIME"]
        self.LOCATION = projections["LOCATION"]
        self.PEOPLE = projections["PEOPLE"]
        self.ORGANIZATION = projections["ORGANIZATION"]
        return self


class EventV4ArchiveRepository(Protocol):
    def contains_source(self, informant: str) -> bool:
        ...

    def contains_id(self, intelligence_uuid: str) -> bool:
        ...

    def save_low_value(self, record: ArchivedDataV4) -> None:
        ...

    def commit(self, record: ArchivedDataV4, events: tuple[dict[str, Any], ...]) -> None:
        ...


def sanitize_raw_data(data: dict[str, Any]) -> dict[str, Any]:
    """生成可长期保存的采集快照，排除凭据、路由和临时控制字段。"""

    excluded = {"token", "prompt", "temp_data", "UUID", "informant", "subsystem"}
    return {key: value for key, value in data.items() if key not in excluded}


def build_archive_appendix(
    original_data: dict[str, Any],
    *,
    subsystem: str,
    ai_service: str,
    ai_model: str,
    prompt_version: int,
    processed_at: datetime,
    archived_at: datetime,
    total_score: float | None = None,
) -> dict[str, Any]:
    """集中构造归档 APPENDIX；业务代码不得自行散写这些键。"""

    raw_data = sanitize_raw_data(original_data)
    content_time, content_time_source = resolve_content_time(raw_data, archived_at)
    appendix: dict[str, Any] = {
        APPENDIX_RAW_DATA: raw_data,
        APPENDIX_TIME_PUB: raw_data.get("pub_time"),
        APPENDIX_TIME_GOT: raw_data.get("collect_time"),
        APPENDIX_TIME_DONE: processed_at,
        APPENDIX_TIME_ARCHIVED: archived_at,
        APPENDIX_CONTENT_TIME: content_time,
        APPENDIX_CONTENT_TIME_SOURCE: content_time_source,
        APPENDIX_SUBSYSTEM: subsystem,
        APPENDIX_PROMPT_VERSION: prompt_version,
        APPENDIX_AI_SERVICE: ai_service,
        APPENDIX_AI_MODEL: ai_model,
        APPENDIX_MANUAL_RATING: {},
        APPENDIX_TRANSLATED_REVISION: None,
        APPENDIX_SCHEMA_VERSION: INTELLIGENCE_ARCHIVE_SCHEMA_VERSION,
    }
    if total_score is not None:
        appendix[APPENDIX_TOTAL_SCORE] = total_score
    return appendix


def derive_event_projections(extraction) -> dict[str, list[str]]:
    """从权威 EVENT_EXTRACTION 生成可重建的 V2 风格速查字段。"""

    entities = {entity.id: entity for entity in extraction.entities}
    referenced: list[str] = []
    times: list[str] = []
    locations: list[str] = []

    for event in extraction.events:
        core = event.core
        for references in core.roles.values():
            referenced.extend(references)
        if core.context:
            referenced.extend(core.context.event_location)
            if event.id == extraction.primary_event_id:
                locations.extend(
                    entities[key].name
                    for key in core.context.event_location
                    if key in entities
                )
        if event.id == extraction.primary_event_id:
            predicate = DEFAULT_EVENT_REGISTRY.get(core.predicate.id or "")
            role_groups = predicate.role_groups if predicate else {}
            location_refs: list[str] = []
            for role, references in core.roles.items():
                semantic_group = role_groups.get(role)
                if getattr(semantic_group, "value", None) == "location":
                    location_refs.extend(references)
                location_refs.extend(
                    key for key in references
                    if key in entities and entities[key].type in {
                        "geopolitical_entity", "location"
                    }
                )
            locations.extend(
                entities[key].name for key in _unique(location_refs) if key in entities
            )
        if core.time:
            for value in core.time.model_dump(exclude_none=True).values():
                normalized = value.get("normalized") if isinstance(value, dict) else None
                if normalized:
                    times.append(normalized)
        for qualifier in event.qualifiers:
            referenced.extend(qualifier.by or [])

    referenced_entities = [entities[key] for key in _unique(referenced) if key in entities]
    return {
        "TIME": _unique(times),
        "LOCATION": _unique(locations),
        "PEOPLE": _unique(
            entity.name for entity in referenced_entities if entity.type == "person"
        ),
        "ORGANIZATION": _unique(
            entity.name for entity in referenced_entities if entity.type == "organization"
        ),
    }


def archive_document(record: ArchivedDataV4) -> dict[str, Any]:
    """转换为 BSON 友好文档；Mongo ``_id`` 不属于业务 Schema。"""

    return _bson_safe(record.model_dump(mode="python", by_alias=True))


def strip_mongo_id(document: dict[str, Any] | None) -> dict[str, Any] | None:
    if document is not None:
        document.pop("_id", None)
    return document


def archive_public_view(document: dict[str, Any]) -> dict[str, Any]:
    """移除 APPENDIX，并只投影列表/前端确实需要的派生显示值。"""

    result = dict(document)
    result.pop("_id", None)
    appendix = result.pop(APPENDIX_FIELD, {}) or {}
    result["content_time"] = appendix.get(APPENDIX_CONTENT_TIME)
    result["content_time_source"] = appendix.get(APPENDIX_CONTENT_TIME_SOURCE)
    result["archived_at"] = appendix.get(APPENDIX_TIME_ARCHIVED)
    result["total_score"] = appendix.get(APPENDIX_TOTAL_SCORE)
    result["prompt_version"] = appendix.get(APPENDIX_PROMPT_VERSION)
    result["manual_rating"] = appendix.get(APPENDIX_MANUAL_RATING) or {}
    result["ai_service"] = appendix.get(APPENDIX_AI_SERVICE)
    result["ai_model"] = appendix.get(APPENDIX_AI_MODEL)
    return result


def _unique(values) -> list[str]:
    normalized = (str(value).strip() for value in values if value is not None)
    return list(dict.fromkeys(value for value in normalized if value))


def _bson_safe(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _bson_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_bson_safe(item) for item in value]
    return value
