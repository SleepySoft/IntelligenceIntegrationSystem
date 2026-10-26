"""情报领域注册层。

流程编排只依赖这里的领域契约；具体 Prompt、结果协议、评分与存储实现位于各领域目录。
"""

from .contracts import (
    DomainDefinition,
    DomainPreparedResult,
    DomainSubsystems,
    PreparedDomainArchive,
)
from .registry import DomainRegistry, build_domain_registry

__all__ = [
    "DomainDefinition",
    "DomainPreparedResult",
    "DomainRegistry",
    "DomainSubsystems",
    "PreparedDomainArchive",
    "build_domain_registry",
]
