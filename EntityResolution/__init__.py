"""与具体情报绑定的实体指称解析。"""

from .model import EntityRef, ResolutionError
from .service import EntityResolutionService, ResolutionScope
from .store import MongoResolutionStore

__all__ = [
    "EntityRef",
    "EntityResolutionService",
    "MongoResolutionStore",
    "ResolutionError",
    "ResolutionScope",
]
