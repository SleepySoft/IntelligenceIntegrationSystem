from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping
from uuid import UUID


class Dynamics(str, Enum):
    STATE = "state"
    PROCESS = "process"
    CHANGE = "change"


class Topology(str, Enum):
    INTRINSIC = "intrinsic"
    RELATIONAL = "relational"
    TARGETED = "targeted"
    TRANSFER = "transfer"


class Agency(str, Enum):
    AGENTIVE = "agentive"
    NON_AGENTIVE = "non_agentive"
    UNKNOWN = "unknown"


class SemanticRoleGroup(str, Enum):
    AGENT = "agent"
    AFFECTED = "affected"
    PARTICIPANT = "participant"
    THEME = "theme"
    SOURCE = "source"
    DESTINATION = "destination"
    INSTRUMENT = "instrument"
    AUTHORITY = "authority"
    BENEFICIARY = "beneficiary"
    LOCATION = "location"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class Frame:
    dynamics: Dynamics
    topology: Topology
    agency: Agency


@dataclass(frozen=True, slots=True)
class Predicate:
    id: str | None
    surface: str
    gloss: str | None = None


@dataclass(frozen=True, slots=True)
class RoleBinding:
    role: str
    entity_uuid: UUID
    semantic_group: SemanticRoleGroup = SemanticRoleGroup.OTHER
    local_entity_id: str | None = None


@dataclass(frozen=True, slots=True)
class TimeExpression:
    normalized: str | None
    precision: str
    approximate: bool
    surface: str


@dataclass(frozen=True, slots=True)
class Qualifier:
    id: str
    type: str
    value: str
    scope: str = "event"
    by: tuple[UUID, ...] = ()
    time: TimeExpression | None = None
    surface: str | None = None


@dataclass(frozen=True, slots=True)
class EventRelation:
    predicate: str
    target_event_uuid: UUID
    surface: str | None = None


@dataclass(frozen=True, slots=True)
class EventIR:
    """纯事件语义，可供没有新闻来源或持久化身份的调用方使用。"""

    frame: Frame
    predicate: Predicate
    role_bindings: tuple[RoleBinding, ...]
    time: Mapping[str, TimeExpression] = field(default_factory=dict)
    location_entity_uuids: tuple[UUID, ...] = ()
    attributes: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    qualifiers: tuple[Qualifier, ...] = ()
    relations: tuple[EventRelation, ...] = ()

    def entities_for_role(self, role: str) -> tuple[UUID, ...]:
        return tuple(x.entity_uuid for x in self.role_bindings if x.role == role)

    def entities_for_group(self, group: SemanticRoleGroup) -> tuple[UUID, ...]:
        return tuple(x.entity_uuid for x in self.role_bindings if x.semantic_group == group)
