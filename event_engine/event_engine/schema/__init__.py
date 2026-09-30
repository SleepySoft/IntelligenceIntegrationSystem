"""公共数据与契约；不加载推理内核、领域配置或存储实现。"""
from .models import (
    Agency, CanonicalEvent, Dynamics, EventIR, EventRecord, EventRelation,
    EventRoleClassification, Frame, MatchDecision, MatchResult, Predicate,
    Qualifier, QualifierObservation, RoleBinding, SemanticRoleGroup,
    StateProjection, TimeExpression, Topology,
)
from .specs import ArgumentRoleSpec, DomainPack, IdentitySpec, LifecycleSpec, PredicateSpec
from .queries import EventPage, EventQuery
from .ports import CanonicalEventRepository, EventRepository

__all__ = [
    "Agency", "CanonicalEvent", "Dynamics", "EventIR", "EventRecord", "EventRelation",
    "EventRoleClassification", "Frame", "MatchDecision", "MatchResult", "Predicate",
    "Qualifier", "QualifierObservation", "RoleBinding", "SemanticRoleGroup",
    "StateProjection", "TimeExpression", "Topology", "ArgumentRoleSpec", "DomainPack",
    "IdentitySpec", "LifecycleSpec", "PredicateSpec", "EventPage", "EventQuery",
    "CanonicalEventRepository", "EventRepository",
]
