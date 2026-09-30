"""应用组装与兼容入口；领域算法由调用方显式选择。"""
from ..core.engine import EventEngine as CoreEventEngine
from ..domains import default_registry


class EventEngine(CoreEventEngine):
    def __init__(self, events, canonicals=None, analyzer=None, matcher=None, registry=None):
        if registry is None and analyzer is None and matcher is None:
            registry = default_registry()
        super().__init__(events, canonicals, analyzer, matcher, registry)
