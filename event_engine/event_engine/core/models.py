from __future__ import annotations
from dataclasses import dataclass, field, fields
from datetime import datetime
from enum import Enum
from typing import Any, Mapping
from uuid import UUID
from ..ir import (EventIR, EventRelation, Frame, Predicate,
                  Qualifier, RoleBinding, SemanticRoleGroup, TimeExpression)


class MatchDecision(str, Enum):
    SAME_EVENT = "same_event"
    STATE_UPDATE = "state_update"
    DUPLICATE = "duplicate_observation"
    DIFFERENT = "different_event"
    AMBIGUOUS = "ambiguous"
    NEW_EVENT = "new_event"

@dataclass(frozen=True, slots=True)
class EventRecord:
    """带来源与存储身份的观察记录；保留 v0.1 构造和存储协议。"""
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

    @property
    def ir(self) -> EventIR:
        return EventIR(**{f.name: getattr(self, f.name) for f in fields(EventIR)})

    @classmethod
    def from_ir(cls, ir: EventIR, *, uuid: UUID, intelligence_uuid: UUID,
                local_event_id: str, observed_at: datetime | None = None,
                is_primary: bool = False, metadata: Mapping[str, Any] | None = None) -> EventRecord:
        return cls(uuid=uuid, intelligence_uuid=intelligence_uuid,
                   local_event_id=local_event_id, observed_at=observed_at,
                   is_primary=is_primary, metadata=metadata or {},
                   **{f.name: getattr(ir, f.name) for f in fields(EventIR)})

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
    discriminator_roles: Mapping[str, tuple[UUID, ...]] = field(default_factory=dict)
    identity_attributes: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    state_projection: StateProjection | None = None

@dataclass(frozen=True, slots=True)
class MatchResult:
    decision: MatchDecision
    score: float
    candidate_uuid: UUID | None = None
    matched: tuple[str, ...] = ()
    unknown: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    evidence_coverage: float = 0.0


@dataclass(frozen=True, slots=True)
class QualifierObservation:
    """限定词及其来源；不因生成当前状态而丢弃主张。"""

    event_uuid: UUID
    intelligence_uuid: UUID
    qualifier: Qualifier
    effective_at: datetime | None
    observed_at: datetime | None


@dataclass(frozen=True, slots=True)
class StateProjection:
    """报道状态的可解释投影，不承担事实真值裁决。"""

    values: Mapping[str, str] = field(default_factory=dict)
    supporting_event_uuids: Mapping[str, tuple[UUID, ...]] = field(default_factory=dict)
    observations: tuple[QualifierObservation, ...] = ()
    conflicts: tuple[str, ...] = ()
    unknown: tuple[str, ...] = ()
    assertion_status: str = "unverified"

@dataclass(frozen=True, slots=True)
class EventRoleClassification:
    """基于谓词语义划分的主体、客体和其他角色绑定。"""

    subjects: tuple[RoleBinding, ...] = ()
    objects: tuple[RoleBinding, ...] = ()
    others: tuple[RoleBinding, ...] = ()
