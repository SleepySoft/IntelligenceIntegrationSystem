"""旧 API 的默认配置兼容入口；纯算法在 core.canonicalizer。"""
from ..core.canonicalizer import CanonicalEventMatcher as CoreCanonicalEventMatcher
from ..configs import default_registry


class CanonicalEventMatcher(CoreCanonicalEventMatcher):
    def __init__(self, specs=None):
        super().__init__(default_registry() if specs is None else specs)
