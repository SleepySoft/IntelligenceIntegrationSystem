from __future__ import annotations
from uuid import UUID
from ..analysis.event_analyzer import EventAnalyzer
from ..analysis.canonicalizer import CanonicalEventMatcher
from ..domain.models import (
    CanonicalEvent,
    EventRecord,
    EventRoleClassification,
    MatchDecision,
    MatchResult,
    SemanticRoleGroup,
    WarZoneView,
)
from ..domain.queries import EventPage, EventQuery
from .ports import CanonicalEventRepository, EventRepository

class EventEngine:
    def __init__(self, events: EventRepository, canonicals: CanonicalEventRepository | None = None,
                 analyzer: EventAnalyzer | None = None, matcher: CanonicalEventMatcher | None = None):
        self.events = events
        self.canonicals = canonicals
        self.analyzer = analyzer or EventAnalyzer()
        self.matcher = matcher or CanonicalEventMatcher()

    def register_event(self, event: EventRecord) -> None:
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

    def extract_war_zones(self, predicate_ids: frozenset[str] = frozenset({"attack", "armed_conflict"}),
                          active_days: int = 30) -> tuple[WarZoneView, ...]:
        events = self.events.search(EventQuery(predicate_ids=predicate_ids)).items
        return self.analyzer.extract_war_zones(events, active_days=active_days)

    def timeline(self, event_uuids: frozenset[UUID]) -> tuple[EventRecord, ...]:
        return self.analyzer.timeline(self.events.search(EventQuery(event_uuids=event_uuids)).items)

    def resolve_canonical(self, observation_uuid: UUID) -> tuple[CanonicalEvent, MatchResult]:
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
            members = self.events.search(EventQuery(event_uuids=frozenset(set(canonical.observation_event_uuids)|{observation.uuid}))).items
            updated = self.matcher.update_canonical(canonical, members)
            self.canonicals.update(updated)
            return updated, result
        raise ValueError(f"CanonicalEvent未自动解析: {result.decision.value}; conflicts={result.conflicts}")
