"""现有新闻/Event V4 领域实现。"""

from __future__ import annotations

from typing import Any, Callable, Mapping

from IntelligenceDomains.contracts import DomainDefinition, DomainSubsystems
from ServiceComponent.prompt_protocols import PromptCatalog


def create_domain(
    *, name: str = "news", event_registry: Any = None,
    prompt_catalog: PromptCatalog | None = None,
    prompt_table: Mapping[int, str] | None = None,
    extra_prompt_versions: Mapping[int, str] | None = None,
    extraction_modes: Mapping[int, str] | None = None,
    scorer_factory: Callable[[dict | None], Any] | None = None,
    archive_repository: Any = None,
) -> DomainDefinition:
    """构造新闻领域；兼容参数仅用于测试和渐进迁移。"""

    from .prompt import build_legacy_compatible_catalog
    from .scoring import IntelligenceScoringEngine
    from .schema import DEFAULT_EVENT_REGISTRY
    from .storage import NewsStorage
    from .subsystems.batch import NewsBatchSupport
    from .subsystems.shadow import NewsShadowSupport

    registry = event_registry or DEFAULT_EVENT_REGISTRY
    catalog = prompt_catalog or build_legacy_compatible_catalog(
        registry,
        prompt_table=prompt_table,
        extra_prompt_versions=extra_prompt_versions,
        extraction_modes=extraction_modes,
    )
    if not catalog.sampling_versions():
        raise ValueError(f"领域 {name!r} 的 prompt catalog 没有生产抽样版本")

    def default_scorer(config: dict | None):
        return IntelligenceScoringEngine(config=config) if config else IntelligenceScoringEngine()

    return DomainDefinition(
        name=name,
        prompt_catalog=catalog,
        scorer_factory=scorer_factory or default_scorer,
        storage=NewsStorage(registry, repository_override=archive_repository),
        event_registry=registry,
        subsystems=DomainSubsystems(
            batch=NewsBatchSupport(),
            shadow=NewsShadowSupport(),
        ),
    )


__all__ = ["create_domain"]
