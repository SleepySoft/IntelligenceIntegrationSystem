"""兼容旧导入；新代码使用 ir 和 core.models。"""
from ..ir import *
from ..core.models import (
    CanonicalEvent, EventRecord, EventRoleClassification, MatchDecision, MatchResult,
)
from ..extensions.news import WarZoneView
