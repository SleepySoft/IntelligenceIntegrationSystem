"""Storage-independent event engine; domain packs are loaded at composition time."""
from .ir import *
from .core.models import CanonicalEvent, EventRecord, EventRoleClassification, MatchDecision, MatchResult
