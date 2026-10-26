"""领域实现与公共分析流程之间的最小契约。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from ServiceComponent.prompt_protocols import PromptCatalog


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

    def score(self, result: Any, config: dict | None = None) -> float:
        scorer = self.scorer_factory(config)
        calculate = getattr(scorer, "calculate", None)
        if callable(calculate):
            return float(calculate(result))
        calculate_v4 = getattr(scorer, "calculate_v4", None)
        if callable(calculate_v4):
            return float(calculate_v4(result))
        raise TypeError(f"领域 {self.name!r} 的评分器必须实现 calculate()")
