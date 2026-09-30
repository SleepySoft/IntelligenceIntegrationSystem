"""通用事件内核；只读取语义协议，不加载具体领域配置。"""
from .models import CanonicalEvent, EventRecord, MatchDecision, MatchResult
from .specs import ArgumentRoleSpec, IdentitySpec, PredicateSpec
from .registry import DomainPack, PredicateRegistry
from .analyzer import EventAnalyzer
from .canonicalizer import CanonicalEventMatcher
from .engine import EventEngine
from .queries import EventPage, EventQuery
from .ports import CanonicalEventRepository, EventRepository

__all__ = ["CanonicalEvent", "EventRecord", "MatchDecision", "MatchResult",
           "ArgumentRoleSpec", "IdentitySpec", "PredicateSpec", "DomainPack", "PredicateRegistry",
           "EventAnalyzer", "CanonicalEventMatcher", "EventEngine", "EventPage", "EventQuery",
           "CanonicalEventRepository", "EventRepository"]
