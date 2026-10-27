"""行业公司行动情报领域。"""

from __future__ import annotations

from typing import Any, Callable

from IntelligenceDomains.contracts import DomainDefinition, DomainSubsystems
from ServiceComponent.prompt_protocols import PromptCatalog


def create_domain(
    *, name: str = "industry", event_registry: Any = None,
    prompt_catalog: PromptCatalog | None = None,
    scorer_factory: Callable[[dict | None], Any] | None = None,
    archive_repository: Any = None,
    **_legacy_compat: Any,
) -> DomainDefinition:
    """构造独立行业领域；旧新闻 Prompt 表参数在这里有意忽略。"""

    from .prompt import build_industry_prompt_catalog
    from .schema import DEFAULT_EVENT_REGISTRY
    from .scoring import IndustryScoringEngine
    from .storage import IndustryStorage

    registry = event_registry or DEFAULT_EVENT_REGISTRY
    catalog = prompt_catalog or build_industry_prompt_catalog(registry)
    if not catalog.sampling_versions():
        raise ValueError(f"领域 {name!r} 的 prompt catalog 没有生产抽样版本")

    def default_scorer(config: dict | None):
        return IndustryScoringEngine(config=config)

    return DomainDefinition(
        name=name,
        prompt_catalog=catalog,
        scorer_factory=scorer_factory or default_scorer,
        storage=IndustryStorage(registry, repository_override=archive_repository),
        event_registry=registry,
        # Batch/Shadow 必须由领域显式实现；行业第一版不借用新闻实现。
        subsystems=DomainSubsystems(),
    )


__all__ = ["create_domain"]
