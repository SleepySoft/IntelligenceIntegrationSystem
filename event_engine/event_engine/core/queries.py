from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from typing import FrozenSet
from uuid import UUID
from ..ir import SemanticRoleGroup

@dataclass(frozen=True, slots=True)
class EventQuery:
    event_uuids: FrozenSet[UUID] = frozenset()
    intelligence_uuid: UUID | None = None
    predicate_ids: FrozenSet[str] = frozenset()
    entity_uuid: UUID | None = None
    roles: FrozenSet[str] = frozenset()
    semantic_groups: FrozenSet[SemanticRoleGroup] = frozenset()
    location_entity_uuids: FrozenSet[UUID] = frozenset()
    observed_from: datetime | None = None
    observed_to: datetime | None = None
    qualifier_types: FrozenSet[str] = frozenset()
    qualifier_values: FrozenSet[str] = frozenset()
    limit: int | None = None
    offset: int = 0

@dataclass(frozen=True, slots=True)
class EventPage:
    items: tuple
    total: int
    offset: int = 0
    limit: int | None = None
