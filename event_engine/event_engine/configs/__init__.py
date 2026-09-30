"""应用组装处显式选择配置包；核心不依赖本模块。"""
from ..core.registry import PredicateRegistry
from .news import PACK as NEWS_PACK
from .industry import PACK as INDUSTRY_PACK
from .financial import PACK as FINANCIAL_PACK

def default_registry() -> PredicateRegistry:
    return PredicateRegistry.from_packs(NEWS_PACK, INDUSTRY_PACK, FINANCIAL_PACK)

DEFAULT_PREDICATE_SPECS = default_registry()
PREDICATE_ARGUMENT_SPECS = {key: spec.arguments for key, spec in DEFAULT_PREDICATE_SPECS.items()}
