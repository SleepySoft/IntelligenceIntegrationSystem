"""领域定义注册器及启动期工厂装配。"""

from __future__ import annotations

import importlib
from typing import Any, Callable, Iterable

from .contracts import DomainDefinition


DomainFactory = Callable[..., DomainDefinition]


class DomainRegistry:
    def __init__(self, default_name: str):
        self.default_name = str(default_name).strip()
        self._definitions: dict[str, DomainDefinition] = {}

    def register(self, definition: DomainDefinition) -> None:
        name = str(definition.name).strip()
        if not name:
            raise ValueError("domain definition name cannot be empty")
        if name in self._definitions:
            raise ValueError(f"domain already registered: {name}")
        self._definitions[name] = definition

    def get(self, name: str | None) -> DomainDefinition | None:
        resolved = str(name or self.default_name).strip() or self.default_name
        return self._definitions.get(resolved)

    def require(self, name: str | None) -> DomainDefinition:
        definition = self.get(name)
        if definition is None:
            raise ValueError(f"domain_not_registered:{name!r}")
        return definition

    def default(self) -> DomainDefinition:
        return self.require(self.default_name)

    def names(self) -> list[str]:
        return list(self._definitions)


def _load_factory(spec: str) -> DomainFactory:
    normalized = str(spec or "news").strip()
    if normalized == "news":
        from IntelligenceDomains.news import create_domain
        return create_domain
    if ":" not in normalized:
        raise ValueError(f"unknown domain factory: {normalized!r}")
    module_name, callable_name = normalized.split(":", 1)
    factory = getattr(importlib.import_module(module_name), callable_name)
    if not callable(factory):
        raise TypeError(f"domain factory is not callable: {normalized!r}")
    return factory


def build_domain_registry(
    contexts: Iterable[Any], *, default_name: str,
    factory_overrides: dict[str, DomainFactory] | None = None,
    factory_kwargs: dict[str, Any] | None = None,
) -> DomainRegistry:
    """按已启用领域上下文装配实现；未声明 factory 时暂使用 news 实现。"""

    registry = DomainRegistry(default_name)
    overrides = factory_overrides or {}
    shared_kwargs = dict(factory_kwargs or {})
    for ctx in contexts:
        name = str(ctx.name)
        factory = overrides.get(name) or _load_factory(
            getattr(ctx, "domain_factory", "news"))
        definition = factory(name=name, **shared_kwargs)
        if definition.name != name:
            raise ValueError(
                f"domain factory returned mismatched name: {definition.name!r} != {name!r}")
        registry.register(definition)
    registry.default()
    return registry
