"""旧 API 的默认配置与新闻分析兼容入口；纯算法在 core.analyzer。"""
from ..core.analyzer import EventAnalyzer as CoreEventAnalyzer
from ..configs import default_registry
from ..extensions.news import NewsAnalyzer


class EventAnalyzer(CoreEventAnalyzer):
    def __init__(self, specs=None):
        super().__init__(default_registry() if specs is None else specs)

    def extract_war_zones(self, events, active_days=30, now=None):
        return NewsAnalyzer(self).extract_war_zones(events, active_days, now)
