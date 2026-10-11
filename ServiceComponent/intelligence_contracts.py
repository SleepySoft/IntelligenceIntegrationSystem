"""IIS V4 跨组件共享的稳定契约值。

这里只放需要被多个边界共同理解的值：持久化版本、状态码、集合后缀、派生身份
命名空间和枚举。业务模型字段本身仍由 Pydantic/Event Engine 模型定义。
"""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID


INTELLIGENCE_ARCHIVE_SCHEMA_VERSION = "iis-intelligence/4.0"
LOW_VALUE_ARCHIVE_SCHEMA_VERSION = "iis-non-intelligence/4.0"


class ContentTimeSource(StrEnum):
    PUBLISHED = "published"
    COLLECTED = "collected"
    ARCHIVED = "archived"


class CacheArchiveState(StrEnum):
    DROP = "D"
    ERROR = "E"
    RETRY = "R"
    ARCHIVED = "A"
    SENSITIVE = "S"
    DUPLICATED = "U"


CACHE_ARCHIVED_FLAG_FIELD = "APPENDIX.__ARCHIVED__"
CACHE_LEGACY_ARCHIVED_FLAG_FIELD = "__ARCHIVED__"


def unarchived_cache_query() -> dict:
    """返回 cache 恢复器使用的未处理记录查询。"""

    return {"$and": [
        {CACHE_LEGACY_ARCHIVED_FLAG_FIELD: {"$exists": False}},
        {CACHE_ARCHIVED_FLAG_FIELD: {"$exists": False}},
    ]}


class EventV4CollectionSuffix(StrEnum):
    ARCHIVED = "v4_archived"
    LOW_VALUE = "v4_low_value"
    EVENTS = "v4_events"
    ENTITIES = "v4_entities"
    OUTBOX = "v4_outbox"


# UUIDv5 的命名空间属于持久化身份契约。修改它会为相同实体生成不同 UUID，必须配套迁移。
ENTITY_ID_NAMESPACE = UUID("e23ca3db-3477-5b81-a391-206f12dfb0af")
