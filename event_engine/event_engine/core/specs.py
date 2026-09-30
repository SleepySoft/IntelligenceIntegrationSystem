from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Mapping

from ..ir import Frame, SemanticRoleGroup


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

    def allows(self, kind: str, before: str, after: str) -> bool:
        if before == after:
            return True
        edges = self.transitions.get(kind, frozenset())
        reached, pending = {before}, [before]
        while pending:
            node = pending.pop()
            for source, target in edges:
                if source == node and target not in reached:
                    if target == after:
                        return True
                    reached.add(target)
                    pending.append(target)
        return False


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


def generic_spec(predicate_id: str | None) -> IdentitySpec:
    return IdentitySpec(identity_roles=("subject",), auto_merge_threshold=.92, review_threshold=.75)
