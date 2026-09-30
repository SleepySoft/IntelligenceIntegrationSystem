"""领域能力的可选入口；导入本模块不会加载任何具体领域或算法。"""
from __future__ import annotations

from typing import TYPE_CHECKING
from importlib import import_module

if TYPE_CHECKING:
    from ..core.registry import PredicateRegistry
    from ..schema import DomainPack

BUILTIN_DOMAINS = ("news", "industry", "financial")


def load_pack(domain_id: str) -> DomainPack:
    """只加载指定领域的声明配置，不加载其分析算法。"""
    if domain_id not in BUILTIN_DOMAINS:
        raise ValueError(f"未知领域包: {domain_id}")
    return import_module(f".{domain_id}.config", __name__).PACK


def registry_for(*domain_ids: str) -> PredicateRegistry:
    """组合显式选择的领域；不传领域得到空 Registry。自定义包可直接用 from_packs。"""
    from ..core.registry import PredicateRegistry
    return PredicateRegistry.from_packs(*(load_pack(name) for name in domain_ids))


def default_registry() -> PredicateRegistry:
    """完整演示/兼容入口显式调用时加载全部内置领域。"""
    return registry_for(*BUILTIN_DOMAINS)


__all__ = ["BUILTIN_DOMAINS", "load_pack", "registry_for", "default_registry"]
