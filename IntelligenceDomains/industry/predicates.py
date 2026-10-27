"""行业领域专用的公司行动谓词及其 Registry 注入。

Event Engine 提供通用事件契约、匹配算法和基础 Pack；IIS 行业领域新增的词汇在此定义，
避免修改共享 Pack 后静默改变新闻等其他领域的 Prompt 与校验目录。
"""

from __future__ import annotations

from datetime import timedelta

from event_engine.core.registry import PredicateRegistry
from event_engine.domains import load_pack
from event_engine.domains.common import COMMON_LIFECYCLE
from event_engine.schema import Agency, Dynamics, Frame, SemanticRoleGroup, Topology
from event_engine.schema.specs import (
    ArgumentRoleSpec,
    DomainPack,
    IdentitySpec,
    PredicateSpec,
)


INDUSTRY_ACTION_PACK_ID = "iis_industry_actions"
INDUSTRY_ACTION_PACK_VERSION = "1.0"

_AGENT = SemanticRoleGroup.AGENT
_AFFECTED = SemanticRoleGroup.AFFECTED
_PARTICIPANT = SemanticRoleGroup.PARTICIPANT
_THEME = SemanticRoleGroup.THEME
_SOURCE = SemanticRoleGroup.SOURCE
_DESTINATION = SemanticRoleGroup.DESTINATION

_CHANGE_TARGETED = Frame(Dynamics.CHANGE, Topology.TARGETED, Agency.AGENTIVE)
_CHANGE_RELATIONAL = Frame(Dynamics.CHANGE, Topology.RELATIONAL, Agency.AGENTIVE)
_CHANGE_TRANSFER = Frame(Dynamics.CHANGE, Topology.TRANSFER, Agency.AGENTIVE)


def _arguments(subjects: tuple[str, ...], objects: tuple[str, ...]) -> ArgumentRoleSpec:
    return ArgumentRoleSpec(frozenset(subjects), frozenset(objects))


def _spec(
    predicate_id: str, *, label: str, definition: str,
    roles: dict[str, SemanticRoleGroup], required_roles: tuple[str, ...],
    identity: IdentitySpec, subjects: tuple[str, ...], objects: tuple[str, ...],
    frame: Frame = _CHANGE_TARGETED,
) -> PredicateSpec:
    return PredicateSpec(
        predicate_id=predicate_id,
        role_groups=roles,
        identity=identity,
        arguments=_arguments(subjects, objects),
        tags=frozenset({"industry", "company_action"}),
        required_roles=required_roles,
        frame=frame,
        lifecycle=COMMON_LIFECYCLE,
        label=label,
        definition=definition,
    )


INDUSTRY_ACTION_SPECS = {
    "increase_output": _spec(
        "increase_output", label="增加产量",
        definition="提高某产品的实际或计划产出，不表示生产能力必然增加",
        roles={"producer": _AGENT, "product": _THEME},
        required_roles=("producer", "product"),
        identity=IdentitySpec(("producer", "product"), time_mode="episode",
                              auto_merge_require_time=True),
        subjects=("producer",), objects=("product",),
    ),
    "reduce_output": _spec(
        "reduce_output", label="减少产量",
        definition="降低某产品的实际或计划产出，但保留相应生产能力",
        roles={"producer": _AGENT, "product": _THEME},
        required_roles=("producer", "product"),
        identity=IdentitySpec(("producer", "product"), time_mode="episode",
                              auto_merge_require_time=True),
        subjects=("producer",), objects=("product",),
    ),
    "reduce_capacity": _spec(
        "reduce_capacity", label="削减产能",
        definition="永久或长期降低设施或产品的可用生产能力，不含短期减产",
        roles={"operator": _AGENT, "facility": _THEME, "product": _THEME},
        required_roles=("operator",),
        identity=IdentitySpec(("operator", "facility"), discriminator_roles=("product",),
                              time_mode="episode"),
        subjects=("operator",), objects=("facility", "product"),
    ),
    "close_facility": _spec(
        "close_facility", label="关闭设施",
        definition="永久结束工厂、门店、仓库等设施的运营，不含临时停产或检修",
        roles={"operator": _AGENT, "facility": _THEME},
        required_roles=("operator", "facility"),
        identity=IdentitySpec(("operator", "facility"), time_mode="episode",
                              auto_merge_require_time=True),
        subjects=("operator",), objects=("facility",),
    ),
    "commercialize": _spec(
        "commercialize", label="商业化",
        definition="产品或技术从研发、测试进入商业销售或规模供应",
        roles={"provider": _AGENT, "product": _THEME},
        required_roles=("provider", "product"),
        identity=IdentitySpec(("provider", "product"), time_mode="episode",
                              auto_merge_require_time=True),
        subjects=("provider",), objects=("product",),
    ),
    "discontinue_product": _spec(
        "discontinue_product", label="停止产品",
        definition="停止销售、维护或继续发展某项产品、型号或技术路线",
        roles={"actor": _AGENT, "product": _THEME},
        required_roles=("actor", "product"),
        identity=IdentitySpec(("actor", "product"), time_mode="episode",
                              auto_merge_require_time=True),
        subjects=("actor",), objects=("product",),
    ),
    "layoff": _spec(
        "layoff", label="裁员",
        definition="雇主主动取消岗位或终止一批雇佣关系，不含招聘冻结和自然减员",
        roles={"employer": _AGENT, "workforce": _AFFECTED, "business_unit": _AFFECTED},
        required_roles=("employer",),
        identity=IdentitySpec(("employer",), discriminator_roles=("business_unit",),
                              time_mode="episode", auto_merge_require_time=True),
        subjects=("employer",), objects=("workforce", "business_unit"),
    ),
    "restructure": _spec(
        "restructure", label="业务重组",
        definition="调整公司、业务单元或经营组织的结构与职责",
        roles={"actor": _AGENT, "organization": _AFFECTED, "business_unit": _AFFECTED},
        required_roles=("actor",),
        identity=IdentitySpec(("actor", "organization"),
                              discriminator_roles=("business_unit",), time_mode="episode"),
        subjects=("actor",), objects=("organization", "business_unit"),
    ),
    "form_joint_venture": _spec(
        "form_joint_venture", label="成立合资企业",
        definition="两个或多个参与方共同设立并控制新的经营组织",
        roles={"participant": _PARTICIPANT, "organization": _THEME},
        required_roles=("participant", "organization"),
        identity=IdentitySpec(("participant", "organization"), time_mode="episode",
                              location_mode="ignore"),
        subjects=("participant",), objects=("organization",), frame=_CHANGE_RELATIONAL,
    ),
    "enter_market": _spec(
        "enter_market", label="进入市场",
        definition="以产品、投资、设施或组织行动进入明确地域或业务市场",
        roles={"actor": _AGENT, "market": _THEME},
        required_roles=("actor", "market"),
        identity=IdentitySpec(("actor", "market"), time_mode="episode",
                              location_mode="ignore"),
        subjects=("actor",), objects=("market",),
    ),
    "exit_market": _spec(
        "exit_market", label="退出市场",
        definition="停止或处置明确地域或业务市场中的经营活动",
        roles={"actor": _AGENT, "market": _THEME},
        required_roles=("actor", "market"),
        identity=IdentitySpec(("actor", "market"), time_mode="episode",
                              location_mode="ignore"),
        subjects=("actor",), objects=("market",),
    ),
    "shift_business_focus": _spec(
        "shift_business_focus", label="转移业务重心",
        definition="有资源、资产、产品或组织行动支撑的业务重点迁移，非一般战略口号",
        roles={"actor": _AGENT, "source": _SOURCE, "destination": _DESTINATION},
        required_roles=("actor", "destination"),
        identity=IdentitySpec(("actor", "destination"), discriminator_roles=("source",),
                              time_mode="episode", location_mode="ignore"),
        subjects=("actor",), objects=("source", "destination"), frame=_CHANGE_TRANSFER,
    ),
    "divest": _spec(
        "divest", label="资产剥离",
        definition="出售企业、业务、股权或资产组合，改变其控制权或归属",
        roles={"seller": _SOURCE, "asset": _THEME, "buyer": _DESTINATION},
        required_roles=("seller", "asset"),
        identity=IdentitySpec(
            ("seller", "asset"), discriminator_roles=("buyer",),
            time_mode="episode", time_tolerance=timedelta(days=730),
            location_mode="ignore", repeatability="low", auto_merge_require_time=True,
        ),
        subjects=("seller",), objects=("asset", "buyer"),
    ),
    "merge": _spec(
        "merge", label="企业合并",
        definition="两个或多个企业结合为同一经营或控制实体，不等同于单方收购",
        roles={"party": _PARTICIPANT, "merged_entity": _THEME},
        required_roles=("party",),
        identity=IdentitySpec(
            ("party",), discriminator_roles=("merged_entity",),
            time_mode="episode", location_mode="ignore", repeatability="low",
            auto_merge_require_time=True,
        ),
        subjects=("party",), objects=("merged_entity",), frame=_CHANGE_RELATIONAL,
    ),
}

INDUSTRY_ACTION_PACK = DomainPack(
    INDUSTRY_ACTION_PACK_ID,
    INDUSTRY_ACTION_PACK_VERSION,
    INDUSTRY_ACTION_SPECS,
)


def build_industry_event_registry() -> PredicateRegistry:
    """组合基础事件词表，并仅为行业领域注入公司行动扩展。"""

    return PredicateRegistry.from_packs(
        load_pack("news"),
        load_pack("industry"),
        load_pack("financial"),
        INDUSTRY_ACTION_PACK,
    )


__all__ = [
    "INDUSTRY_ACTION_PACK",
    "INDUSTRY_ACTION_PACK_ID",
    "INDUSTRY_ACTION_PACK_VERSION",
    "INDUSTRY_ACTION_SPECS",
    "build_industry_event_registry",
]
