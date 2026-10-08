"""Event V4 情报归档信封与存储端口。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from event_engine.schema import EventRecord
from ServiceComponent.IntelligenceHubDefines_v4 import (
    NonIntelligenceV4,
    ValuableIntelligenceV4,
)


class ArchiveModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ArchivedIntelligenceV4(ArchiveModel):
    schema_version: str = Field(default="iis-intelligence/4.0", frozen=True)
    intelligence_uuid: UUID
    informant: str
    raw_data: dict[str, Any]
    analysis: ValuableIntelligenceV4
    ai_service: str
    ai_model: str
    prompt_version: int
    processed_at: datetime
    archived_at: datetime
    total_score: float = Field(..., ge=0, le=10)
    event_uuids: list[UUID] = Field(..., min_length=1)
    primary_event_uuid: UUID
    subsystem: str


class LowValueIntelligenceV4(ArchiveModel):
    schema_version: str = Field(default="iis-non-intelligence/4.0", frozen=True)
    intelligence_uuid: UUID
    informant: str
    raw_data: dict[str, Any]
    analysis: NonIntelligenceV4
    ai_service: str
    ai_model: str
    prompt_version: int
    processed_at: datetime
    subsystem: str


class EventV4ArchiveRepository(Protocol):
    """V4 多集合提交端口；实现必须保证重复调用不会制造重复记录。"""

    def contains(self, intelligence_uuid: UUID, informant: str) -> bool:
        ...

    def save_low_value(self, record: LowValueIntelligenceV4) -> None:
        ...

    def commit(
        self,
        record: ArchivedIntelligenceV4,
        events: tuple[EventRecord, ...],
    ) -> None:
        ...


def archive_document(record: ArchiveModel) -> dict[str, Any]:
    """转换为 BSON 友好的文档，UUID 使用字符串并以全局 UUID 为主键。"""

    document = _bson_safe(record.model_dump(mode="python", by_alias=True))
    document["_id"] = str(record.intelligence_uuid)
    return document


def _bson_safe(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {key: _bson_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_bson_safe(item) for item in value]
    return value
