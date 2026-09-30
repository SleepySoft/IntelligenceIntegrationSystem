from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Mapping
from uuid import UUID

from ..core.analyzer import EventAnalyzer
from ..schema.models import EventRecord


@dataclass(frozen=True, slots=True)
class WarZoneView:
    location_entity_uuid: UUID
    event_count: int
    first_activity: str | None
    last_activity: str | None
    predicate_distribution: Mapping[str, int]
    supporting_event_uuids: tuple[UUID, ...]
    active: bool


class NewsAnalyzer:
    """新闻领域分析；具体战争含义不进入通用内核。"""

    def __init__(self, analyzer: EventAnalyzer):
        self.analyzer = analyzer

    def extract_war_zones(self, events: Iterable[EventRecord], active_days: int = 30,
                          now: datetime | None = None) -> tuple[WarZoneView, ...]:
        now = now or datetime.now(timezone.utc)
        by_location: dict[UUID, list[EventRecord]] = defaultdict(list)
        for event in events:
            spec = self.analyzer.specs.get(event.predicate.id or "")
            if spec is None or "war" not in spec.tags:
                continue
            for location in set(event.location_entity_uuids):
                by_location[location].append(event)
        views = []
        for location, items in by_location.items():
            ordered = self.analyzer.timeline(items)
            observed = [x.observed_at for x in ordered if x.observed_at]
            last_dt = max(observed) if observed else None
            views.append(WarZoneView(
                location_entity_uuid=location, event_count=len(items),
                first_activity=self.analyzer._time_surface(ordered[0]),
                last_activity=self.analyzer._time_surface(ordered[-1]),
                predicate_distribution=dict(Counter(x.predicate.id or "unknown" for x in items)),
                supporting_event_uuids=tuple(x.uuid for x in ordered),
                active=bool(last_dt and (now - last_dt).days <= active_days),
            ))
        return tuple(sorted(views, key=lambda x: (-x.event_count, str(x.location_entity_uuid))))
