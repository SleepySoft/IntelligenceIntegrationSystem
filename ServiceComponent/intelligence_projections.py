"""Web 查询使用的固定情报投影与 DTO 边界。

这里是归档查询分级的单一事实源。调用方只能选择具名 profile，不能把浏览器传入的
任意字段列表转交 MongoDB。态势地图/实体图的事件索引投影属于 SituationViews 自己的
只读契约，不放进归档 profile。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from ServiceComponent.event_v4_archive import archive_public_view
from ServiceComponent.intelligence_contracts import (
    APPENDIX_AI_MODEL,
    APPENDIX_AI_SERVICE,
    APPENDIX_CONTENT_TIME,
    APPENDIX_CONTENT_TIME_SOURCE,
    APPENDIX_MANUAL_RATING,
    APPENDIX_PROMPT_VERSION,
    APPENDIX_TIME_ARCHIVED,
    APPENDIX_TIME_GOT,
    APPENDIX_TIME_PUB,
    APPENDIX_TOTAL_SCORE,
    appendix_path,
)


class IntelligenceProjectionProfile(StrEnum):
    """稳定的归档读取级别；不是允许外部自由组合的 fields 参数。"""

    CARD = "card"
    DETAIL = "detail"
    INTERNAL = "internal"


CARD_TOP_LEVEL_FIELDS = (
    "UUID", "INFORMANT", "EVENT_TITLE", "EVENT_BRIEF",
    "TAXONOMY", "SUB_CATEGORY",
)

CARD_APPENDIX_FIELDS = (
    APPENDIX_CONTENT_TIME,
    APPENDIX_CONTENT_TIME_SOURCE,
    APPENDIX_TIME_PUB,
    APPENDIX_TIME_GOT,
    APPENDIX_TIME_ARCHIVED,
    APPENDIX_TOTAL_SCORE,
    APPENDIX_PROMPT_VERSION,
    APPENDIX_AI_SERVICE,
    APPENDIX_AI_MODEL,
)

# 详情保留分析展示字段与权威 EVENT_EXTRACTION，但只读取批准公开的 APPENDIX 元数据。
DETAIL_TOP_LEVEL_FIELDS = (
    "UUID", "INFORMANT", "TAXONOMY", "SUB_CATEGORY", "REASON",
    "NON_INTELLIGENCE_REASON",
    "EVENT_TITLE", "EVENT_BRIEF", "EVENT_TEXT", "IMPACT", "RATE", "TIPS",
    "EVENT_EXTRACTION", "TIME", "LOCATION", "PEOPLE", "ORGANIZATION",
)

DETAIL_APPENDIX_FIELDS = CARD_APPENDIX_FIELDS + (APPENDIX_MANUAL_RATING,)

CARD_RESPONSE_FIELD_ORDER = (
    *CARD_TOP_LEVEL_FIELDS,
    "content_time", "content_time_source", "published_at", "collected_at",
    "archived_at", "total_score", "prompt_version", "ai_service", "ai_model",
)
CARD_RESPONSE_FIELDS = frozenset(CARD_RESPONSE_FIELD_ORDER)


def normalize_projection_profile(
    value: IntelligenceProjectionProfile | str,
) -> IntelligenceProjectionProfile:
    try:
        return IntelligenceProjectionProfile(str(value))
    except ValueError as exc:
        raise ValueError(f"未知情报投影级别: {value}") from exc


def archive_mongo_projection(
    profile: IntelligenceProjectionProfile | str,
) -> dict[str, int]:
    """返回固定 Mongo inclusion projection；``internal`` 才允许完整归档。"""
    profile = normalize_projection_profile(profile)
    if profile is IntelligenceProjectionProfile.INTERNAL:
        return {"_id": 0}
    top_level = (
        CARD_TOP_LEVEL_FIELDS
        if profile is IntelligenceProjectionProfile.CARD
        else DETAIL_TOP_LEVEL_FIELDS
    )
    appendix_fields = (
        CARD_APPENDIX_FIELDS
        if profile is IntelligenceProjectionProfile.CARD
        else DETAIL_APPENDIX_FIELDS
    )
    projection = {"_id": 0, **{field: 1 for field in top_level}}
    projection.update({appendix_path(field): 1 for field in appendix_fields})
    return projection


def archive_response_view(
    document: dict[str, Any],
    profile: IntelligenceProjectionProfile | str,
) -> dict[str, Any]:
    """把 Mongo 结果裁成对应 DTO；CARD 再做一次白名单防止字段意外泄漏。"""
    profile = normalize_projection_profile(profile)
    if profile is IntelligenceProjectionProfile.INTERNAL:
        result = dict(document)
        result.pop("_id", None)
        return result
    public = archive_public_view(document)
    if profile is IntelligenceProjectionProfile.DETAIL:
        return public
    return {key: public.get(key) for key in CARD_RESPONSE_FIELD_ORDER}


def card_with_vector_score(document: dict[str, Any], score: float) -> dict[str, Any]:
    """向量分数是查询期字段，只能在 CARD DTO 构造后附加，不能写回归档。"""
    result = dict(document)
    result["vector_score"] = float(score)
    return result

