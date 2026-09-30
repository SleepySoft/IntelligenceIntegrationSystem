from __future__ import annotations
from uuid import UUID
from ..core.models import CanonicalEvent, EventRecord
from ..core.queries import EventPage, EventQuery

class InMemoryEventRepository:
    def __init__(self): self.data: dict[UUID, EventRecord] = {}
    def add(self, event: EventRecord) -> None:
        if event.uuid in self.data: raise ValueError(f"Event已存在: {event.uuid}")
        self.data[event.uuid] = event
    def get(self, event_uuid: UUID) -> EventRecord | None: return self.data.get(event_uuid)
    def search(self, q: EventQuery) -> EventPage:
        items=list(self.data.values())
        if q.event_uuids: items=[e for e in items if e.uuid in q.event_uuids]
        if q.intelligence_uuid: items=[e for e in items if e.intelligence_uuid==q.intelligence_uuid]
        if q.predicate_ids: items=[e for e in items if e.predicate.id in q.predicate_ids]
        if q.location_entity_uuids: items=[e for e in items if set(e.location_entity_uuids)&set(q.location_entity_uuids)]
        if q.entity_uuid:
            items=[e for e in items if any(b.entity_uuid==q.entity_uuid and
                (not q.roles or b.role in q.roles) and
                (not q.semantic_groups or b.semantic_group in q.semantic_groups) for b in e.role_bindings)]
        if q.qualifier_types: items=[e for e in items if any(x.type in q.qualifier_types for x in e.qualifiers)]
        if q.qualifier_values: items=[e for e in items if any(x.value in q.qualifier_values for x in e.qualifiers)]
        if q.observed_from: items=[e for e in items if e.observed_at and e.observed_at>=q.observed_from]
        if q.observed_to: items=[e for e in items if e.observed_at and e.observed_at<=q.observed_to]
        items.sort(key=lambda e: (e.observed_at is None, e.observed_at, str(e.uuid)))
        total=len(items); items=items[q.offset:]
        if q.limit is not None: items=items[:q.limit]
        return EventPage(tuple(items), total, q.offset, q.limit)

class InMemoryCanonicalEventRepository:
    def __init__(self): self.data: dict[UUID, CanonicalEvent] = {}
    def add(self, event):
        if event.uuid in self.data: raise ValueError(f"CanonicalEvent已存在: {event.uuid}")
        self.data[event.uuid]=event
    def update(self, event):
        if event.uuid not in self.data: raise KeyError(event.uuid)
        self.data[event.uuid]=event
    def get(self, event_uuid): return self.data.get(event_uuid)
    def find_candidates(self, observation):
        result=[]
        for c in self.data.values():
            if c.predicate_id != observation.predicate.id: continue
            # blocking: at least one identity role overlaps, or candidate is incomplete
            overlaps=False
            for role, ids in c.identity_roles.items():
                if set(ids)&set(observation.entities_for_role(role)): overlaps=True; break
            if overlaps or not c.identity_roles: result.append(c)
        return tuple(result)
