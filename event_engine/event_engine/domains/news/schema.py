"""新闻领域算法的输出声明，不进入通用 schema。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping
from uuid import UUID


@dataclass(frozen=True, slots=True)
class WarZoneView:
    location_entity_uuid: UUID
    event_count: int
    first_activity: str | None
    last_activity: str | None
    predicate_distribution: Mapping[str, int]
    supporting_event_uuids: tuple[UUID, ...]
    active: bool
