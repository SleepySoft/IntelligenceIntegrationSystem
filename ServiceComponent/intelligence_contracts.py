"""IIS V4 跨组件共享的稳定契约值。

这里只放需要被多个边界共同理解的值：持久化版本、状态码、集合后缀、派生身份
命名空间和枚举。业务模型字段本身仍由 Pydantic/Event Engine 模型定义。
"""

from __future__ import annotations

from enum import StrEnum


INTELLIGENCE_ARCHIVE_SCHEMA_VERSION = "iis-intelligence/4.0"
LOW_VALUE_ARCHIVE_SCHEMA_VERSION = "iis-non-intelligence/4.0"

# APPENDIX 是采集 cache 与归档记录共享的唯一系统元数据容器。键名及 Mongo
# 字段路径必须集中定义在这里，业务模块不得重复字符串常量。
APPENDIX_FIELD = "APPENDIX"
APPENDIX_RAW_DATA = "RAW_DATA"
APPENDIX_TIME_PUB = "__TIME_PUB__"
APPENDIX_TIME_GOT = "__TIME_GOT__"
APPENDIX_TIME_POST = "__TIME_POST__"
APPENDIX_TIME_DONE = "__TIME_DONE__"
APPENDIX_TIME_ARCHIVED = "__TIME_ARCHIVED__"
APPENDIX_CONTENT_TIME = "__CONTENT_TIME__"
APPENDIX_CONTENT_TIME_SOURCE = "__CONTENT_TIME_SOURCE__"
APPENDIX_SUBSYSTEM = "__SUBSYSTEM__"
APPENDIX_ARCHIVED_FLAG = "__ARCHIVED__"
APPENDIX_TOTAL_SCORE = "__TOTAL_SCORE__"
APPENDIX_MANUAL_RATING = "__MANUAL_RATING__"
APPENDIX_PROMPT_VERSION = "__PROMPT_VERSION__"
APPENDIX_PROMPT_OVERRIDE = "__PROMPT_OVERRIDE__"
APPENDIX_AI_SERVICE = "__AI_SERVICE__"
APPENDIX_AI_MODEL = "__AI_MODEL__"
APPENDIX_TRANSLATED_REVISION = "__TRANSLATED_REVISION__"
APPENDIX_SCHEMA_VERSION = "__SCHEMA_VERSION__"
APPENDIX_VECTOR_SCORE = "__VECTOR_SCORE__"  # 查询时临时字段，不写回权威归档


def appendix_path(key: str) -> str:
    return f"{APPENDIX_FIELD}.{key}"


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


CACHE_ARCHIVED_FLAG_FIELD = appendix_path(APPENDIX_ARCHIVED_FLAG)
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
    OUTBOX = "v4_outbox"
