"""应用组装与旧接口兼容；默认同时加载三个领域包。"""
from ..core.engine import EventEngine as CoreEventEngine
from ..core.queries import EventQuery
from ..configs import default_registry
from ..extensions.news import NewsAnalyzer


class EventEngine(CoreEventEngine):
    def __init__(self, events, canonicals=None, analyzer=None, matcher=None, registry=None):
        if registry is None and analyzer is None and matcher is None:
            registry = default_registry()
        super().__init__(events, canonicals, analyzer, matcher, registry)

    def extract_war_zones(self, predicate_ids=None, active_days=30):
        if predicate_ids is None:
            predicate_ids = frozenset(key for key, spec in self.registry.items() if "war" in spec.tags)
        events = self.events.search(EventQuery(predicate_ids=predicate_ids)).items
        return NewsAnalyzer(self.analyzer).extract_war_zones(events, active_days=active_days)
