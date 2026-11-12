"""EntityAlias V2：基于 SQLite 的类型化实体别名候选服务。

V2 与现有 :mod:`EntityAlias` 完全隔离，不读取其 JSONL，也不参与 IIS 组合根装配。
"""

from .model import (
    AliasV2Error,
    EntityName,
    EntityRecord,
    ResolutionCandidate,
    ResolutionResult,
)
from .service import EntityAliasV2Service
from .store import SQLiteAliasV2Store
from .types import CANONICAL_TYPES, normalize_entity_type

__all__ = [
    "AliasV2Error",
    "CANONICAL_TYPES",
    "EntityAliasV2Service",
    "EntityName",
    "EntityRecord",
    "ResolutionCandidate",
    "ResolutionResult",
    "SQLiteAliasV2Store",
    "normalize_entity_type",
]
