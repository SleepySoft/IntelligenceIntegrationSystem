from __future__ import annotations
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Mapping
from .models import SemanticRoleGroup

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
    lifecycle_weight: float = 0.05
    auto_merge_threshold: float = 0.85
    review_threshold: float = 0.65
    auto_merge_margin: float = 0.15

@dataclass(frozen=True, slots=True)
class PredicateSpec:
    predicate_id: str
    role_groups: Mapping[str, SemanticRoleGroup]
    identity: IdentitySpec
    war_related: bool = False

@dataclass(frozen=True, slots=True)
class ArgumentRoleSpec:
    """谓词的逻辑主体和客体角色；未列出的角色归入 others。"""

    subject_roles: frozenset[str]
    object_roles: frozenset[str]


def _arguments(subjects: str = "", objects: str = "") -> ArgumentRoleSpec:
    return ArgumentRoleSpec(frozenset(subjects.split()), frozenset(objects.split()))


# 该表描述逻辑论元，而非单纯依赖角色名称猜测。例如 natural_hazard 的
# phenomenon 是主体，而 move 的 source/destination 是其他角色。
PREDICATE_ARGUMENT_SPECS: dict[str, ArgumentRoleSpec] = {
    "exist": _arguments("subject"),
    "possess": _arguments("holder", "asset"),
    "control": _arguments("controller", "controlled"),
    "member_of": _arguments("member", "organization"),
    "located_at": _arguments("subject"),
    "depend_on": _arguments("dependent", "dependency"),
    "connected_to": _arguments("participant"),
    "capable": _arguments("subject", "capability"),
    "valid": _arguments("subject"),
    "shortage": _arguments("resource", "affected"),
    "increase": _arguments("subject agent"),
    "decrease": _arguments("subject agent"),
    "improve": _arguments("subject agent"),
    "deteriorate": _arguments("subject agent"),
    "create": _arguments("actor", "object"),
    "terminate": _arguments("subject agent"),
    "gain": _arguments("subject agent", "object"),
    "lose": _arguments("subject agent", "object"),
    "damage": _arguments("actor", "affected"),
    "casualty": _arguments("actor", "affected"),
    "restore": _arguments("actor", "affected"),
    "appoint": _arguments("authority", "person position"),
    "remove": _arguments("authority", "person position"),
    "discover": _arguments("discoverer", "object"),
    "default": _arguments("debtor", "obligation"),
    "insolvency": _arguments("subject"),
    "violate": _arguments("actor", "rule"),
    "move": _arguments("agent theme"),
    "transfer": _arguments("agent", "theme"),
    "trade": _arguments("buyer seller", "goods"),
    "acquire": _arguments("acquirer", "asset"),
    "invest": _arguments("investor", "recipient resource"),
    "fund": _arguments("provider", "recipient resource"),
    "aid": _arguments("provider", "recipient goods"),
    "pay": _arguments("payer", "payee"),
    "lend": _arguments("lender", "borrower"),
    "repay": _arguments("debtor", "creditor"),
    "supply": _arguments("supplier", "recipient goods"),
    "communicate": _arguments("sender", "information recipient"),
    "operate": _arguments("subject operator"),
    "produce": _arguments("producer", "product"),
    "construct": _arguments("builder", "object"),
    "develop": _arguments("developer", "object"),
    "test": _arguments("tester", "object"),
    "deploy": _arguments("deployer", "object"),
    "maintain": _arguments("actor", "object"),
    "inspect": _arguments("inspector", "object"),
    "investigate": _arguments("investigator", "object"),
    "negotiate": _arguments("party", "topic"),
    "agree": _arguments("party", "agreement"),
    "regulate": _arguments("authority", "target rule"),
    "sanction": _arguments("authority", "target object"),
    "restrict": _arguments("authority", "target object"),
    "elect": _arguments("electorate", "person position"),
    "adjudicate": _arguments("authority", "case party"),
    "detain": _arguments("authority", "person"),
    "seize": _arguments("authority", "object"),
    "protest": _arguments("participant", "target"),
    "cooperate": _arguments("party", "topic"),
    "release": _arguments("releaser", "object recipient"),
    "armed_conflict": _arguments("belligerent"),
    "attack": _arguments("actor", "target"),
    "defend": _arguments("actor", "target"),
    "intercept": _arguments("actor", "target"),
    "observe": _arguments("actor", "target"),
    "disrupt": _arguments("actor", "target"),
    "accident": _arguments("", "affected"),
    "natural_hazard": _arguments("phenomenon", "affected_area"),
    "outbreak": _arguments("phenomenon", "affected_area"),
}

DEFAULT_ROLE_GROUPS = {
    "actor": SemanticRoleGroup.AGENT,
    "agent": SemanticRoleGroup.AGENT,
    "acquirer": SemanticRoleGroup.AGENT,
    "builder": SemanticRoleGroup.AGENT,
    "controller": SemanticRoleGroup.AGENT,
    "developer": SemanticRoleGroup.AGENT,
    "deployer": SemanticRoleGroup.AGENT,
    "discoverer": SemanticRoleGroup.AGENT,
    "holder": SemanticRoleGroup.AGENT,
    "inspector": SemanticRoleGroup.AGENT,
    "investigator": SemanticRoleGroup.AGENT,
    "investor": SemanticRoleGroup.AGENT,
    "lender": SemanticRoleGroup.AGENT,
    "operator": SemanticRoleGroup.AGENT,
    "payer": SemanticRoleGroup.AGENT,
    "producer": SemanticRoleGroup.AGENT,
    "provider": SemanticRoleGroup.AGENT,
    "releaser": SemanticRoleGroup.AGENT,
    "sender": SemanticRoleGroup.AGENT,
    "supplier": SemanticRoleGroup.AGENT,
    "tester": SemanticRoleGroup.AGENT,
    "authority": SemanticRoleGroup.AUTHORITY,
    "target": SemanticRoleGroup.AFFECTED,
    "affected": SemanticRoleGroup.AFFECTED,
    "controlled": SemanticRoleGroup.AFFECTED,
    "dependent": SemanticRoleGroup.AFFECTED,
    "person": SemanticRoleGroup.AFFECTED,
    "borrower": SemanticRoleGroup.BENEFICIARY,
    "creditor": SemanticRoleGroup.BENEFICIARY,
    "payee": SemanticRoleGroup.BENEFICIARY,
    "recipient": SemanticRoleGroup.BENEFICIARY,
    "participant": SemanticRoleGroup.PARTICIPANT,
    "party": SemanticRoleGroup.PARTICIPANT,
    "belligerent": SemanticRoleGroup.PARTICIPANT,
    "electorate": SemanticRoleGroup.PARTICIPANT,
    "member": SemanticRoleGroup.PARTICIPANT,
    "organization": SemanticRoleGroup.PARTICIPANT,
    "theme": SemanticRoleGroup.THEME,
    "asset": SemanticRoleGroup.THEME,
    "agreement": SemanticRoleGroup.THEME,
    "capability": SemanticRoleGroup.THEME,
    "case": SemanticRoleGroup.THEME,
    "dependency": SemanticRoleGroup.THEME,
    "obligation": SemanticRoleGroup.THEME,
    "phenomenon": SemanticRoleGroup.THEME,
    "position": SemanticRoleGroup.THEME,
    "goods": SemanticRoleGroup.THEME,
    "information": SemanticRoleGroup.THEME,
    "object": SemanticRoleGroup.THEME,
    "product": SemanticRoleGroup.THEME,
    "resource": SemanticRoleGroup.THEME,
    "rule": SemanticRoleGroup.THEME,
    "subject": SemanticRoleGroup.THEME,
    "topic": SemanticRoleGroup.THEME,
    "debtor": SemanticRoleGroup.SOURCE,
    "source": SemanticRoleGroup.SOURCE,
    "seller": SemanticRoleGroup.SOURCE,
    "destination": SemanticRoleGroup.DESTINATION,
    "buyer": SemanticRoleGroup.DESTINATION,
    "instrument": SemanticRoleGroup.INSTRUMENT,
    "beneficiary": SemanticRoleGroup.BENEFICIARY,
    "location": SemanticRoleGroup.LOCATION,
    "affected_area": SemanticRoleGroup.LOCATION,
    "theater": SemanticRoleGroup.LOCATION,
}

DEFAULT_PREDICATE_SPECS: dict[str, PredicateSpec] = {
    "acquire": PredicateSpec(
        "acquire", DEFAULT_ROLE_GROUPS,
        IdentitySpec(
            identity_roles=("acquirer", "asset"),
            discriminator_roles=("seller",),
            time_mode="episode", time_tolerance=timedelta(days=730),
            location_mode="ignore", repeatability="low",
            role_weight=.65, time_weight=.10, location_weight=0,
            attribute_weight=.15, lifecycle_weight=.10,
        )
    ),
    "attack": PredicateSpec(
        "attack", DEFAULT_ROLE_GROUPS,
        IdentitySpec(
            identity_roles=("actor", "target"),
            discriminator_roles=("instrument",),
            time_mode="occurrence", time_tolerance=timedelta(hours=48),
            location_mode="strict", repeatability="high",
            role_weight=.35, time_weight=.30, location_weight=.25,
            attribute_weight=.10, lifecycle_weight=0,
            auto_merge_threshold=.90, review_threshold=.72,
        ), war_related=True
    ),
    "armed_conflict": PredicateSpec(
        "armed_conflict", DEFAULT_ROLE_GROUPS,
        IdentitySpec(
            identity_roles=("belligerent",),
            time_mode="interval", time_tolerance=timedelta(days=365),
            location_mode="strict", repeatability="low",
            role_weight=.45, time_weight=.15, location_weight=.25,
            attribute_weight=0, lifecycle_weight=.15,
        ), war_related=True
    ),
    "move": PredicateSpec(
        "move", DEFAULT_ROLE_GROUPS,
        IdentitySpec(
            identity_roles=("theme",), discriminator_roles=("source", "destination"),
            time_mode="occurrence", time_tolerance=timedelta(days=2),
            location_mode="strict", repeatability="high",
        ), war_related=False
    ),
}

def generic_spec(predicate_id: str | None) -> IdentitySpec:
    return IdentitySpec(identity_roles=("subject",), auto_merge_threshold=.92, review_threshold=.75)
