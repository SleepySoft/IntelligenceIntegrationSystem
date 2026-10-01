from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import replace
from math import isfinite
from types import MappingProxyType

from ..schema import Agency, EventIR, SemanticRoleGroup
from ..schema.specs import DomainPack, PredicateSpec


class PredicateRegistry(Mapping[str, PredicateSpec]):
    """统一冻结语义定义，供接入、分析和匹配共享。"""

    def __init__(self, specs: Mapping[str, PredicateSpec] | None = None):
        frozen = {}
        for key, spec in (specs or {}).items():
            if key != spec.predicate_id:
                raise ValueError(f"谓词 ID 不一致: {key}")
            identity = spec.identity
            weights = (identity.role_weight, identity.time_weight, identity.location_weight,
                       identity.attribute_weight, identity.lifecycle_weight)
            if any(not isfinite(w) or w < 0 for w in weights) or sum(weights) <= 0:
                raise ValueError(f"身份权重无效: {key}")
            active_weight = (identity.role_weight if identity.identity_roles else 0)
            active_weight += identity.time_weight if identity.time_mode != "ignore" else 0
            active_weight += identity.location_weight if identity.location_mode != "ignore" else 0
            active_weight += identity.attribute_weight if (identity.identity_attributes or identity.auto_merge_required_attributes) else 0
            if active_weight <= 0:
                raise ValueError(f"缺少身份证据权重: {key}")
            if not 0 <= identity.review_threshold <= identity.auto_merge_threshold <= 1:
                raise ValueError(f"身份阈值无效: {key}")
            if not 0 <= identity.auto_merge_margin <= 1:
                raise ValueError(f"候选领先差值无效: {key}")
            if not 0 <= identity.min_evidence_coverage <= 1:
                raise ValueError(f"证据覆盖率无效: {key}")
            if identity.max_time_uncertainty is not None and identity.max_time_uncertainty.total_seconds() <= 0:
                raise ValueError(f"时间精度限制无效: {key}")
            if identity.auto_merge_required_roles is not None and not set(identity.auto_merge_required_roles) <= set(identity.identity_roles):
                raise ValueError(f"自动合并必备角色不是身份角色: {key}")
            if identity.time_mode not in {"occurrence", "episode", "interval", "ignore"}:
                raise ValueError(f"时间模式无效: {key}")
            if identity.auto_merge_require_time and identity.time_mode == "ignore":
                raise ValueError(f"必备事件时间不能设置为 ignore: {key}")
            if identity.location_mode not in {"strict", "weak", "ignore"}:
                raise ValueError(f"地点模式无效: {key}")
            if identity.time_tolerance is not None and identity.time_tolerance.total_seconds() <= 0:
                raise ValueError(f"时间容忍窗口无效: {key}")
            if any(not isinstance(group, SemanticRoleGroup) for group in spec.role_groups.values()):
                raise ValueError(f"角色语义组无效: {key}")
            declared = set(spec.role_groups)
            referenced = (set(spec.required_roles) | set(spec.identity.identity_roles)
                          | set(spec.identity.discriminator_roles)
                          | set(spec.arguments.subject_roles) | set(spec.arguments.object_roles))
            if referenced - declared:
                raise ValueError(f"未定义的角色: {key}: {sorted(referenced - declared)}")
            lifecycle = spec.lifecycle
            if lifecycle is not None:
                if set(lifecycle.transitions) - {"phase", "intention", "authorization", "directive"}:
                    raise ValueError(f"事实性不能作为生命周期状态: {key}")
                lifecycle = replace(lifecycle, transitions=MappingProxyType(
                    {kind: frozenset(edges) for kind, edges in lifecycle.transitions.items()}))
            frozen[key] = replace(spec, role_groups=MappingProxyType(dict(spec.role_groups)), lifecycle=lifecycle)
        self._specs = MappingProxyType(frozen)
        self.pack_versions: Mapping[str, str] = MappingProxyType({})

    @classmethod
    def from_packs(cls, *packs: DomainPack) -> PredicateRegistry:
        specs = {}
        versions = {}
        for pack in packs:
            if pack.domain_id in versions:
                raise ValueError(f"重复领域包: {pack.domain_id}")
            duplicates = specs.keys() & pack.specs.keys()
            if duplicates:
                raise ValueError(f"重复谓词定义: {sorted(duplicates)}")
            versions[pack.domain_id] = pack.version
            specs.update(pack.specs)
        registry = cls(specs)
        registry.pack_versions = MappingProxyType(versions)
        return registry

    def __getitem__(self, key: str) -> PredicateSpec:
        return self._specs[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._specs)

    def __len__(self) -> int:
        return len(self._specs)

    def validate(self, event: EventIR, *, require_known: bool = False) -> tuple[str, ...]:
        """允许不完整观察；仅检查配置明确声明的必填角色与 Frame。"""
        spec = self.get(event.predicate.id or "")
        if spec is None:
            return ("谓词未注册",) if require_known else ()
        errors = [f"必填角色缺失: {role}" for role in spec.required_roles
                  if not event.entities_for_role(role)]
        allowed = spec.allowed_frames or ((spec.frame,) if spec.frame is not None else ())
        if allowed and not any(
            frame.dynamics == event.frame.dynamics and frame.topology == event.frame.topology
            and (frame.agency == event.frame.agency or Agency.UNKNOWN in (frame.agency, event.frame.agency))
            for frame in allowed
        ):
            errors.append("Frame 与谓词定义不一致")
        return tuple(errors)


def as_registry(specs: Mapping[str, PredicateSpec] | None) -> PredicateRegistry:
    return specs if isinstance(specs, PredicateRegistry) else PredicateRegistry(specs)
