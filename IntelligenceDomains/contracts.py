"""领域实现与公共分析流程之间的最小契约。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from ServiceComponent.prompt_protocols import PromptCatalog


@dataclass(frozen=True, slots=True)
class DomainShadowSettings:
    """领域自己的影子分析策略；版本号只在该领域 Prompt Catalog 内解释。"""

    enabled: bool = False
    versions: tuple[int, ...] = ()
    sample_rate: float = 0.0
    comparison_mode: str = "same_model"
    client_group: str | None = None


@dataclass(frozen=True, slots=True)
class DomainSubsystems:
    """领域可选择提供的附属能力；缺失即表示该领域不执行对应机制。"""

    batch: Any | None = None
    shadow: Any | None = None


@dataclass(frozen=True, slots=True)
class DomainPreparedResult:
    """领域后处理结果，不向公共流程暴露具体归档 DTO。"""

    low_value: bool
    archive_payload: Any
    output: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class PreparedDomainArchive:
    """公共 archive 阶段使用的领域无关信封。"""

    domain_name: str
    payload: Any
    intelligence_uuid: str
    retry_metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def archive(self) -> Any:
        """迁移期兼容旧 Event V4 测试/扩展；公共流程不依赖该属性。"""
        return getattr(self.payload, "archive")

    @property
    def conversion(self) -> Any:
        return getattr(self.payload, "conversion")


@dataclass(frozen=True, slots=True)
class DomainDefinition:
    """一个情报领域在分析主链中的全部可替换行为。"""

    name: str
    prompt_catalog: PromptCatalog
    scorer_factory: Callable[[dict | None], Any]
    storage: Any
    event_registry: Any = None
    subsystems: DomainSubsystems = field(default_factory=DomainSubsystems)

    def __post_init__(self) -> None:
        """启动期校验领域版本池，避免正式 Prompt 与同模型 Shadow 候选混用。"""
        settings = self.subsystems.shadow
        if settings is None or not bool(getattr(settings, "enabled", False)):
            return
        versions = tuple(int(item) for item in (getattr(settings, "versions", ()) or ()))
        unknown = [version for version in versions if not self.prompt_catalog.has(version)]
        if unknown:
            raise ValueError(
                f"领域 {self.name!r} 的 shadow prompt 未注册: {unknown}")
        if str(getattr(settings, "comparison_mode", "same_model")) == "same_model":
            overlap = sorted(set(versions) & set(self.prompt_catalog.sampling_versions()))
            if overlap:
                raise ValueError(
                    f"领域 {self.name!r} 的正式与 shadow Prompt 池重叠: {overlap}")

    def score(self, result: Any, config: dict | None = None) -> float:
        scorer = self.scorer_factory(config)
        calculate = getattr(scorer, "calculate", None)
        if callable(calculate):
            return float(calculate(result))
        calculate_v4 = getattr(scorer, "calculate_v4", None)
        if callable(calculate_v4):
            return float(calculate_v4(result))
        raise TypeError(f"领域 {self.name!r} 的评分器必须实现 calculate()")
