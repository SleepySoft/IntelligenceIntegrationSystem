from __future__ import annotations
from collections import defaultdict
from typing import Iterable, Mapping
from uuid import UUID
from ..ir import EventIR, SemanticRoleGroup, Topology
from .models import EventRecord, EventRoleClassification
from .specs import PredicateSpec
from .registry import as_registry
from .models import StateProjection
from .state import project_state

class EventAnalyzer:
    def __init__(self, specs: Mapping[str, PredicateSpec] | None = None):
        self.specs = as_registry(specs)

    def participants(self, event: EventIR | EventRecord) -> tuple[UUID, ...]:
        return tuple(dict.fromkeys(x.entity_uuid for x in event.role_bindings))

    def agents(self, event: EventIR | EventRecord) -> tuple[UUID, ...]:
        return event.entities_for_group(SemanticRoleGroup.AGENT)

    def affected(self, event: EventIR | EventRecord) -> tuple[UUID, ...]:
        return event.entities_for_group(SemanticRoleGroup.AFFECTED)

    def qualifiers_by_type(self, event: EventIR | EventRecord) -> dict[str, tuple[str, ...]]:
        result: dict[str, list[str]] = defaultdict(list)
        for q in event.qualifiers:
            result[q.type].append(q.value)
        return {k: tuple(v) for k, v in result.items()}

    def classify_roles(self, event: EventIR | EventRecord) -> EventRoleClassification:
        """按谓词将角色绑定划分为逻辑主体、客体和其他角色。"""

        spec = self.specs.get(event.predicate.id or "")
        if spec:
            subject_roles = spec.arguments.subject_roles
            object_roles = spec.arguments.object_roles
        else:
            subject_roles, object_roles = self._fallback_argument_roles(event)

        subjects = tuple(binding for binding in event.role_bindings if binding.role in subject_roles)
        objects = tuple(binding for binding in event.role_bindings if binding.role in object_roles)
        others = tuple(
            binding
            for binding in event.role_bindings
            if binding.role not in subject_roles and binding.role not in object_roles
        )
        return EventRoleClassification(subjects, objects, others)

    def current_state(self, events: Iterable[EventRecord]) -> dict[str, str]:
        """兼容视图，仅返回生命周期值；证据与争议见 project_state。"""
        return dict(self.project_state(events).values)

    def project_state(self, events: Iterable[EventRecord]) -> StateProjection:
        events = tuple(events)
        predicates = {event.predicate.id for event in events}
        if len(predicates) > 1:
            raise ValueError("状态投影只能处理同一谓词的事件观察")
        spec = self.specs.get(events[0].predicate.id or "") if events else None
        return project_state(events, spec.lifecycle if spec else None)

    def entity_actions(self, events: Iterable[EventRecord], entity_uuid: UUID) -> tuple[EventRecord, ...]:
        return tuple(e for e in events if entity_uuid in self.agents(e))

    def actions_affecting_entity(self, events: Iterable[EventRecord], entity_uuid: UUID) -> tuple[EventRecord, ...]:
        return tuple(e for e in events if entity_uuid in self.affected(e))

    def timeline(self, events: Iterable[EventRecord]) -> tuple[EventRecord, ...]:
        return tuple(sorted(events, key=lambda e: (self._time_key(e), str(e.uuid))))

    @staticmethod
    def _time_surface(event: EventRecord) -> str | None:
        for key in ("event_time", "start_time", "effective_time", "expected_start_time"):
            value = event.time.get(key)
            if value:
                return value.normalized or value.surface
        return event.observed_at.isoformat() if event.observed_at else None

    @classmethod
    def _time_key(cls, event: EventRecord) -> str:
        return cls._time_surface(event) or "9999"

    @staticmethod
    def _fallback_argument_roles(event: EventRecord) -> tuple[frozenset[str], frozenset[str]]:
        if event.frame.topology == Topology.INTRINSIC:
            return frozenset({"subject"}), frozenset()
        if event.frame.topology == Topology.RELATIONAL:
            return frozenset({"subject", "counterpart"}), frozenset()
        if event.frame.topology == Topology.TARGETED:
            return frozenset({"actor"}), frozenset({"target"})
        return frozenset({"agent"}), frozenset({"theme"})
