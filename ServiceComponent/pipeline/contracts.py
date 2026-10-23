"""流程插件依赖的端口和阶段控制结果。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Protocol

from ServiceComponent.runtime.events import HubEvent


@dataclass(frozen=True)
class StageResult:
    """阶段是否继续的控制结果；payload 保留给领域实现自由定义。"""

    accepted: bool
    payload: Any = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    reason: str = ""

    @classmethod
    def allow(cls, payload: Any = None, **metadata: Any) -> "StageResult":
        return cls(accepted=True, payload=payload, metadata=metadata)

    @classmethod
    def reject(cls, reason: str, payload: Any = None, **metadata: Any) -> "StageResult":
        return cls(accepted=False, payload=payload, metadata=metadata, reason=reason)


@dataclass(frozen=True)
class PipelineStageFailure:
    """领域端口的失败结果，便于页面或运维扩展订阅。"""

    stage: str
    reason: str
    exception_type: str = ""


class IntakePort(Protocol):
    def accept(self, event: HubEvent) -> StageResult:
        """校验、去重或持久化原始输入；拒绝时返回 StageResult.reject。"""


class AnalysisPort(Protocol):
    def analyze(self, event: HubEvent) -> StageResult:
        """按 subsystem 执行任意领域分析，不要求统一输出模型。"""


class ArchivePort(Protocol):
    def archive(self, event: HubEvent) -> StageResult:
        """验证/评分/持久化分析结果，不要求统一归档结构。"""
