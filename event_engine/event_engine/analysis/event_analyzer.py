from __future__ import annotations
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Iterable
from uuid import UUID
from ..domain.models import EventRecord, EventRoleClassification, SemanticRoleGroup, Topology, WarZoneView
from ..domain.specs import PREDICATE_ARGUMENT_SPECS, PredicateSpec

class EventAnalyzer:
    def __init__(self, specs: dict[str, PredicateSpec] | None = None):
        self.specs = specs or {}

    def participants(self, event: EventRecord) -> tuple[UUID, ...]:
        return tuple(dict.fromkeys(x.entity_uuid for x in event.role_bindings))

    def agents(self, event: EventRecord) -> tuple[UUID, ...]:
        return event.entities_for_group(SemanticRoleGroup.AGENT)

    def affected(self, event: EventRecord) -> tuple[UUID, ...]:
        return event.entities_for_group(SemanticRoleGroup.AFFECTED)

    def qualifiers_by_type(self, event: EventRecord) -> dict[str, tuple[str, ...]]:
        result: dict[str, list[str]] = defaultdict(list)
        for q in event.qualifiers:
            result[q.type].append(q.value)
        return {k: tuple(v) for k, v in result.items()}

    def classify_roles(self, event: EventRecord) -> EventRoleClassification:
        """按谓词将角色绑定划分为逻辑主体、客体和其他角色。"""

        spec = PREDICATE_ARGUMENT_SPECS.get(event.predicate.id or "")
        if spec:
            subject_roles = spec.subject_roles
            object_roles = spec.object_roles
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
        ordered = sorted(events, key=lambda e: e.observed_at or datetime.min.replace(tzinfo=timezone.utc))
        state: dict[str, str] = {}
        for event in ordered:
            for q in event.qualifiers:
                if q.scope == "event":
                    state[q.type] = q.value
        return state

    def entity_actions(self, events: Iterable[EventRecord], entity_uuid: UUID) -> tuple[EventRecord, ...]:
        return tuple(e for e in events if entity_uuid in self.agents(e))

    def actions_affecting_entity(self, events: Iterable[EventRecord], entity_uuid: UUID) -> tuple[EventRecord, ...]:
        return tuple(e for e in events if entity_uuid in self.affected(e))

    def timeline(self, events: Iterable[EventRecord]) -> tuple[EventRecord, ...]:
        return tuple(sorted(events, key=lambda e: (self._time_key(e), str(e.uuid))))

    def extract_war_zones(self, events: Iterable[EventRecord], active_days: int = 30, now: datetime | None = None) -> tuple[WarZoneView, ...]:
        now = now or datetime.now(timezone.utc)
        by_location: dict[UUID, list[EventRecord]] = defaultdict(list)
        for event in events:
            spec = self.specs.get(event.predicate.id or "")
            if not (spec and spec.war_related):
                continue
            for location in set(event.location_entity_uuids):
                by_location[location].append(event)
        views = []
        for location, items in by_location.items():
            ordered = self.timeline(items)
            observed = [x.observed_at for x in ordered if x.observed_at]
            last_dt = max(observed) if observed else None
            active = bool(last_dt and (now - last_dt).days <= active_days)
            preds = Counter(x.predicate.id or "unknown" for x in items)
            views.append(WarZoneView(
                location_entity_uuid=location,
                event_count=len(items),
                first_activity=self._time_surface(ordered[0]) if ordered else None,
                last_activity=self._time_surface(ordered[-1]) if ordered else None,
                predicate_distribution=dict(preds),
                supporting_event_uuids=tuple(x.uuid for x in ordered),
                active=active,
            ))
        return tuple(sorted(views, key=lambda x: (-x.event_count, str(x.location_entity_uuid))))

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
