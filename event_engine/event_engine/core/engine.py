from __future__ import annotations
from uuid import UUID
from dataclasses import replace
from .analyzer import EventAnalyzer
from .canonicalizer import CanonicalEventMatcher
from .models import (
    CanonicalEvent,
    EventRecord,
    EventRoleClassification,
    MatchDecision,
    MatchResult,
    SemanticRoleGroup,
)
from .queries import EventPage, EventQuery
from .ports import CanonicalEventRepository, EventRepository
from .registry import PredicateRegistry, as_registry

class EventEngine:
    def __init__(self, events: EventRepository, canonicals: CanonicalEventRepository | None = None,
                 analyzer: EventAnalyzer | None = None, matcher: CanonicalEventMatcher | None = None,
                 registry: PredicateRegistry | None = None):
        self.events = events
        self.canonicals = canonicals
        if registry is None:
            registry = analyzer.specs if analyzer is not None else matcher.specs if matcher is not None else None
        self.registry = as_registry(registry)
        for component in (analyzer, matcher):
            if component is not None and dict(component.specs) != dict(self.registry):
                raise ValueError("接入、分析和匹配必须使用同一语义配置")
        self.analyzer = analyzer or EventAnalyzer(self.registry)
        self.matcher = matcher or CanonicalEventMatcher(self.registry)
        self.analyzer.specs = self.registry
        self.matcher.specs = self.registry

    def register_event(self, event: EventRecord) -> None:
        errors = self.registry.validate(event.ir)
        if errors:
            raise ValueError("; ".join(errors))
        spec = self.registry.get(event.predicate.id or "")
        if spec is not None:
            bindings = tuple(replace(b, semantic_group=spec.role_groups.get(b.role, b.semantic_group))
                             for b in event.role_bindings)
            if bindings != event.role_bindings:
                event = replace(event, role_bindings=bindings)
        self.events.add(event)

    def query(self, query: EventQuery) -> EventPage:
        return self.events.search(query)

    def classify_event_roles(self, event_uuid: UUID) -> EventRoleClassification:
        event = self.events.get(event_uuid)
        if not event:
            raise KeyError(event_uuid)
        return self.analyzer.classify_roles(event)

    def get_entity_actions(self, entity_uuid: UUID, **kwargs) -> tuple[EventRecord, ...]:
        q = EventQuery(entity_uuid=entity_uuid, semantic_groups=frozenset({SemanticRoleGroup.AGENT}), **kwargs)
        return self.events.search(q).items

    def get_actions_affecting_entity(self, entity_uuid: UUID, **kwargs) -> tuple[EventRecord, ...]:
        q = EventQuery(entity_uuid=entity_uuid, semantic_groups=frozenset({SemanticRoleGroup.AFFECTED}), **kwargs)
        return self.events.search(q).items

    def timeline(self, event_uuids: frozenset[UUID]) -> tuple[EventRecord, ...]:
        return self.analyzer.timeline(self.events.search(EventQuery(event_uuids=event_uuids)).items)

    def resolve_canonical(self, observation_uuid: UUID) -> tuple[CanonicalEvent | None, MatchResult]:
        if not self.canonicals:
            raise RuntimeError("CanonicalEventRepository未配置")
        observation = self.events.get(observation_uuid)
        if not observation:
            raise KeyError(observation_uuid)
        candidates = self.canonicals.find_candidates(observation)
        result = self.matcher.resolve(observation, candidates)
        if result.decision == MatchDecision.NEW_EVENT:
            canonical = self.matcher.create_canonical(observation)
            self.canonicals.add(canonical)
            return canonical, result
        if result.decision in (MatchDecision.SAME_EVENT, MatchDecision.STATE_UPDATE, MatchDecision.DUPLICATE):
            canonical = self.canonicals.get(result.candidate_uuid)
            assert canonical is not None
            if result.decision == MatchDecision.DUPLICATE:
                return canonical, result
            members = self.events.search(EventQuery(event_uuids=frozenset(set(canonical.observation_event_uuids)|{observation.uuid}))).items
            try:
                updated = self.matcher.update_canonical(canonical, members)
            except ValueError as error:
                return None, replace(result, decision=MatchDecision.AMBIGUOUS,
                                     conflicts=result.conflicts + (str(error),))
            self.canonicals.update(updated)
            changed = updated.current_qualifiers != canonical.current_qualifiers
            result = replace(result, decision=MatchDecision.STATE_UPDATE if changed else MatchDecision.SAME_EVENT,
                             conflicts=result.conflicts + updated.unresolved_conflicts)
            return updated, result
        # 信息不足时保留原观察，让调用方读取原因；不合并也不创建重复身份。
        return None, result
