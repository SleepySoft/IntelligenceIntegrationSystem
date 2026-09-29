from __future__ import annotations
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Sequence
from uuid import UUID, uuid4


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

class MatchDecision(str, Enum):
    SAME_EVENT = "same_event"
    STATE_UPDATE = "state_update"
    DUPLICATE = "duplicate_observation"
    DIFFERENT = "different_event"
    AMBIGUOUS = "ambiguous"
    NEW_EVENT = "new_event"

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
class EventRecord:
    uuid: UUID
    intelligence_uuid: UUID
    local_event_id: str
    frame: Frame
    predicate: Predicate
    role_bindings: tuple[RoleBinding, ...]
    time: Mapping[str, TimeExpression] = field(default_factory=dict)
    location_entity_uuids: tuple[UUID, ...] = ()
    attributes: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    qualifiers: tuple[Qualifier, ...] = ()
    relations: tuple[EventRelation, ...] = ()
    observed_at: datetime | None = None
    is_primary: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def entities_for_role(self, role: str) -> tuple[UUID, ...]:
        return tuple(x.entity_uuid for x in self.role_bindings if x.role == role)

    def entities_for_group(self, group: SemanticRoleGroup) -> tuple[UUID, ...]:
        return tuple(x.entity_uuid for x in self.role_bindings if x.semantic_group == group)

@dataclass(frozen=True, slots=True)
class CanonicalEvent:
    uuid: UUID
    predicate_id: str | None
    frame: Frame
    identity_roles: Mapping[str, tuple[UUID, ...]]
    observation_event_uuids: tuple[UUID, ...]
    current_qualifiers: Mapping[str, str]
    location_entity_uuids: tuple[UUID, ...] = ()
    first_observed_at: datetime | None = None
    last_observed_at: datetime | None = None
    event_time_start: str | None = None
    event_time_end: str | None = None
    unresolved_conflicts: tuple[str, ...] = ()
    version: int = 1

@dataclass(frozen=True, slots=True)
class MatchResult:
    decision: MatchDecision
    score: float
    candidate_uuid: UUID | None = None
    matched: tuple[str, ...] = ()
    unknown: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()

@dataclass(frozen=True, slots=True)
class WarZoneView:
    location_entity_uuid: UUID
    event_count: int
    first_activity: str | None
    last_activity: str | None
    predicate_distribution: Mapping[str, int]
    supporting_event_uuids: tuple[UUID, ...]
    active: bool
