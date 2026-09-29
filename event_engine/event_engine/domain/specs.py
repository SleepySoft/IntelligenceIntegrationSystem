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

DEFAULT_ROLE_GROUPS = {
    "actor": SemanticRoleGroup.AGENT,
    "agent": SemanticRoleGroup.AGENT,
    "acquirer": SemanticRoleGroup.AGENT,
    "attacker": SemanticRoleGroup.AGENT,
    "imposer": SemanticRoleGroup.AGENT,
    "issuer": SemanticRoleGroup.AGENT,
    "authority": SemanticRoleGroup.AUTHORITY,
    "target": SemanticRoleGroup.AFFECTED,
    "affected": SemanticRoleGroup.AFFECTED,
    "victim": SemanticRoleGroup.AFFECTED,
    "detainee": SemanticRoleGroup.AFFECTED,
    "participant": SemanticRoleGroup.PARTICIPANT,
    "party": SemanticRoleGroup.PARTICIPANT,
    "belligerent": SemanticRoleGroup.PARTICIPANT,
    "theme": SemanticRoleGroup.THEME,
    "source": SemanticRoleGroup.SOURCE,
    "destination": SemanticRoleGroup.DESTINATION,
    "instrument": SemanticRoleGroup.INSTRUMENT,
    "beneficiary": SemanticRoleGroup.BENEFICIARY,
    "location": SemanticRoleGroup.LOCATION,
}

DEFAULT_PREDICATE_SPECS: dict[str, PredicateSpec] = {
    "acquire": PredicateSpec(
        "acquire", DEFAULT_ROLE_GROUPS,
        IdentitySpec(
            identity_roles=("acquirer", "target"),
            discriminator_roles=("theme",),
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
