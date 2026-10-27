"""新闻 v48 的 Event Engine 兼容目录边界。

新闻历史上组合了 news/industry/financial 三个 Pack。领域 Pack 后续扩展时，不能让
已经投产的新闻 Prompt 因共享目录增长而静默变化；这里显式冻结 v48 可见的跨领域谓词
集合，同时继续复用 Event Engine 中对应谓词的唯一规则定义。
"""

from __future__ import annotations

from event_engine.core.registry import PredicateRegistry
from event_engine.domains import load_pack
from event_engine.schema.specs import DomainPack


NEWS_V48_INDUSTRY_PREDICATES = frozenset({
    "shortage",
    "supply",
    "operate",
    "produce",
    "construct",
    "develop",
    "test",
    "deploy",
    "maintain",
    "build_facility",
    "expand_capacity",
    "suspend_production",
    "resume_production",
})

NEWS_V48_FINANCIAL_PREDICATES = frozenset({
    "default",
    "insolvency",
    "trade",
    "acquire",
    "invest",
    "fund",
    "pay",
    "lend",
    "repay",
    "issue_bond",
    "rating_change",
    "declare_dividend",
})


def _compat_pack(domain_id: str, version: str, allowed: frozenset[str]) -> DomainPack:
    source = load_pack(domain_id)
    missing = allowed - set(source.specs)
    if missing:
        raise RuntimeError(
            f"news v48 compatibility predicates missing from {domain_id}: {sorted(missing)}")
    return DomainPack(
        domain_id=domain_id,
        version=version,
        specs={name: source.specs[name] for name in source.specs if name in allowed},
    )


def build_news_v48_registry() -> PredicateRegistry:
    """构造不会因其他领域新增谓词而扩张的新闻 v48 Registry。"""

    return PredicateRegistry.from_packs(
        load_pack("news"),
        _compat_pack("industry", "1.2", NEWS_V48_INDUSTRY_PREDICATES),
        _compat_pack("financial", "1.2", NEWS_V48_FINANCIAL_PREDICATES),
    )


__all__ = [
    "NEWS_V48_FINANCIAL_PREDICATES",
    "NEWS_V48_INDUSTRY_PREDICATES",
    "build_news_v48_registry",
]
