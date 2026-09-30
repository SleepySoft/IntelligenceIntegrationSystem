from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Mapping

from .models import Frame, SemanticRoleGroup


@dataclass(frozen=True, slots=True)
class IdentitySpec:
    identity_roles: tuple[str, ...]
    discriminator_roles: tuple[str, ...] = ()
    time_mode: str = "episode"  # occurrence | episode | interval | ignore
    time_tolerance: timedelta | None = None
    location_mode: str = "weak"  # strict | weak | ignore
    repeatability: str = "medium"
    role_weight: float = 0.50
    time_weight: float = 0.20
    location_weight: float = 0.15
    attribute_weight: float = 0.10
    lifecycle_weight: float = 0.05  # 兼容旧配置；状态变化不再参与身份评分。
    auto_merge_threshold: float = 0.85
    review_threshold: float = 0.65
    auto_merge_margin: float = 0.15
    identity_attributes: tuple[str, ...] = ()
    auto_merge_required_roles: tuple[str, ...] | None = None
    auto_merge_required_attributes: tuple[str, ...] = ()
    min_evidence_coverage: float = 0.75
    max_time_uncertainty: timedelta | None = None
    auto_merge_require_time: bool = False


@dataclass(frozen=True, slots=True)
class LifecycleSpec:
    """各状态维度的允许迁移；可跨越未报道的中间步骤，但不生成中间事实。"""

    transitions: Mapping[str, frozenset[tuple[str, str]]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ArgumentRoleSpec:
    """主体/客体是领域定义的展示投影，不取代完整角色绑定。"""

    subject_roles: frozenset[str] = frozenset()
    object_roles: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class PredicateSpec:
    """内核消费的语义协议；具体定义由外部配置提供。"""

    predicate_id: str
    role_groups: Mapping[str, SemanticRoleGroup]
    identity: IdentitySpec
    arguments: ArgumentRoleSpec = field(default_factory=ArgumentRoleSpec)
    tags: frozenset[str] = frozenset()
    required_roles: tuple[str, ...] = ()
    frame: Frame | None = None
    allowed_frames: tuple[Frame, ...] = ()
    lifecycle: LifecycleSpec | None = None


@dataclass(frozen=True, slots=True)
class DomainPack:
    """领域配置的数据载体，不包含注册或推理逻辑。"""

    domain_id: str
    version: str
    specs: Mapping[str, PredicateSpec]
