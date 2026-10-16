"""Event V4 archive and derived-event query service."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Iterable

from pymongo import DESCENDING

from ServiceComponent.event_v4_archive import archive_public_view, strip_mongo_id
from ServiceComponent.intelligence_contracts import (
    APPENDIX_CONTENT_TIME,
    APPENDIX_FIELD,
    APPENDIX_RAW_DATA,
    APPENDIX_TIME_ARCHIVED,
    APPENDIX_TOTAL_SCORE,
    appendix_path,
)


class EventV4QueryEngine:
    """查询 V2-shaped V4 归档；Mongo ``_id`` 永不越过此边界。"""

    def __init__(self, intelligence_collection: Any, event_collection: Any):
        self.intelligence_collection = intelligence_collection
        self.event_collection = event_collection
        # 可选别名展开器（由组合根在 EntityAlias 挂载后注入）：
        # Callable[[str], List[str]]，名字 → 所属实体的全部已知名字。
        self.alias_expander = None

    def get_intelligence(self, intelligence_uuid: str | Iterable[str],
                         light_weight: bool = False, include_appendix: bool = False):
        projection = {"_id": 0}
        if isinstance(intelligence_uuid, (list, tuple, set)):
            identifiers = [str(value) for value in intelligence_uuid if value]
            if not identifiers:
                return []
            documents = list(self.intelligence_collection.find(
                {"UUID": {"$in": identifiers}}, projection))
            by_id = {str(item["UUID"]): strip_mongo_id(item) for item in documents}
            ordered = [by_id[value] for value in identifiers if value in by_id]
            if light_weight:
                return [self._light_view(item) for item in ordered]
            values = ordered if include_appendix else [archive_public_view(item) for item in ordered]
            return [self._enrich(item) for item in values]
        if not intelligence_uuid:
            return None
        document = self.intelligence_collection.find_one(
            {"UUID": str(intelligence_uuid)}, projection)
        strip_mongo_id(document)
        if not document:
            return None
        if light_weight:
            return self._light_view(document)
        return self._enrich(document if include_appendix else archive_public_view(document))

    def get_intelligence_summary(self) -> dict[str, Any]:
        newest = self.intelligence_collection.find_one(
            {}, {"_id": 0, "UUID": 1},
            sort=[(appendix_path(APPENDIX_TIME_ARCHIVED), DESCENDING), ("UUID", DESCENDING)])
        return {
            "total_count": self.intelligence_collection.count_documents({}),
            "base_uuid": str(newest["UUID"]) if newest else None,
        }

    def query_intelligence(
        self,
        *,
        period: tuple[datetime, datetime] | None = None,
        archive_period: tuple[datetime, datetime] | None = None,
        peoples=None,
        locations=None,
        organizations=None,
        keywords: str = "",
        informant_domains=None,
        threshold: float | None = None,
        threshold_max: float | None = None,
        sort_by: str = "archived_at",
        skip: int = 0,
        limit: int = 20,
        **_: Any,
    ) -> tuple[list[dict], int]:
        clauses: list[dict] = []
        if period:
            clauses.append({appendix_path(APPENDIX_CONTENT_TIME): {
                "$gte": period[0], "$lte": period[1]}})
        if archive_period:
            clauses.append({appendix_path(APPENDIX_TIME_ARCHIVED): {
                "$gte": archive_period[0], "$lte": archive_period[1]}})
        score_range = {}
        if threshold is not None:
            score_range["$gte"] = float(threshold)
        if threshold_max is not None:
            score_range["$lte"] = float(threshold_max)
        if score_range:
            clauses.append({appendix_path(APPENDIX_TOTAL_SCORE): score_range})
        if informant_domains:
            domains = self._as_list(informant_domains)
            clauses.append({"$or": [
                {"INFORMANT": {"$regex": re.escape(value), "$options": "i"}}
                for value in domains
            ]})
        if keywords:
            raw_content = appendix_path(APPENDIX_RAW_DATA) + ".content"
            for keyword in self._keywords(keywords):
                regex = {"$regex": re.escape(keyword), "$options": "i"}
                clauses.append({"$or": [
                    {"EVENT_TITLE": regex},
                    {"EVENT_BRIEF": regex},
                    {"EVENT_TEXT": regex},
                    {raw_content: regex},
                ]})

        direct_groups = {
            "PEOPLE": peoples,
            "LOCATION": locations,
            "ORGANIZATION": organizations,
        }
        for field, raw_values in direct_groups.items():
            values = self._as_list(raw_values)
            if values and self.alias_expander:
                # 查询时别名展开：每个实体名展开为其所属实体的全部已知名字，
                # 未注册的名字原样保留。归档数据零改动，别名表更新即时生效。
                values = sorted({alias for value in values
                                 for alias in self.alias_expander(value)})
            if values:
                clauses.append({field: {"$in": values}})

        query = {"$and": clauses} if clauses else {}
        total = self.intelligence_collection.count_documents(query)
        sort_key = (APPENDIX_CONTENT_TIME
                    if sort_by == "content_time" else APPENDIX_TIME_ARCHIVED)
        projection = {"_id": 0}
        cursor = (
            self.intelligence_collection.find(query, projection)
            .sort([(appendix_path(sort_key), DESCENDING), ("UUID", DESCENDING)])
            .skip(max(0, int(skip)))
            .limit(max(0, int(limit)))
        )
        return [archive_public_view(item) for item in cursor], total

    def aggregate(self, pipeline: list) -> list:
        # 聚合结果中的 ``_id`` 往往是分组键，并非 Mongo 文档内部 ID，不能删除。
        return list(self.intelligence_collection.aggregate(pipeline))

    def count_documents(self, query: dict) -> int:
        return self.intelligence_collection.count_documents(query)

    def get_latest_archive_timestamp(self) -> float | None:
        path = appendix_path(APPENDIX_TIME_ARCHIVED)
        latest = self.intelligence_collection.find_one(
            {}, {"_id": 0, path: 1}, sort=[(path, DESCENDING)])
        value = (latest or {}).get(APPENDIX_FIELD, {}).get(APPENDIX_TIME_ARCHIVED)
        return value.timestamp() if isinstance(value, datetime) else None

    def get_source_domains(self, limit: int = 200) -> list[dict]:
        pipeline = [
            {"$match": {"INFORMANT": {"$type": "string", "$ne": ""}}},
            {"$group": {"_id": "$INFORMANT", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": max(1, int(limit))},
        ]
        return list(self.intelligence_collection.aggregate(pipeline))

    def _enrich(self, document: dict) -> dict:
        result = dict(document)
        events = list(self.event_collection.find(
            {"intelligence_uuid": str(document["UUID"])}, {"_id": 0}))
        result["events"] = [strip_mongo_id(item) for item in events]
        return result

    @staticmethod
    def _light_view(document: dict) -> dict:
        result = archive_public_view(document)
        result.pop("EVENT_TEXT", None)
        result.pop("EVENT_EXTRACTION", None)
        return result

    @staticmethod
    def _as_list(value: Any) -> list[str]:
        if value is None:
            return []
        values = value if isinstance(value, (list, tuple, set)) else [value]
        return [str(item).strip() for item in values if str(item).strip()]

    @staticmethod
    def _keywords(value: str) -> list[str]:
        return [item for item in re.split(r"[\s,，]+", str(value).strip()) if item]
