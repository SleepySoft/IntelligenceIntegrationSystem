"""Event V4 intelligence/event/entity query service."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Iterable

from pymongo import DESCENDING


class EventV4QueryEngine:
    """Query V4 intelligence envelopes and their normalized event/entity indexes."""

    ENTITY_TYPES = {
        "peoples": ("person",),
        "organizations": ("organization", "agency", "company"),
        "locations": ("location", "country", "region", "city", "facility"),
        "geography": ("location", "country", "region", "city"),
    }

    def __init__(self, intelligence_collection: Any, event_collection: Any,
                 entity_collection: Any):
        self.intelligence_collection = intelligence_collection
        self.event_collection = event_collection
        self.entity_collection = entity_collection

    def get_intelligence(self, intelligence_uuid: str | Iterable[str],
                         light_weight: bool = False):
        projection = {"raw_data.content": 0, "analysis.message.text": 0} \
            if light_weight else None
        if isinstance(intelligence_uuid, (list, tuple, set)):
            identifiers = [str(value) for value in intelligence_uuid if value]
            if not identifiers:
                return []
            documents = list(self.intelligence_collection.find(
                {"_id": {"$in": identifiers}}, projection))
            by_id = {str(item["_id"]): item for item in documents}
            ordered = [by_id[value] for value in identifiers if value in by_id]
            return ordered if light_weight else [self._enrich(item) for item in ordered]
        if not intelligence_uuid:
            return None
        document = self.intelligence_collection.find_one(
            {"_id": str(intelligence_uuid)}, projection)
        return self._enrich(document) if document and not light_weight else document

    def get_intelligence_summary(self) -> dict[str, Any]:
        newest = self.intelligence_collection.find_one(
            {}, {"_id": 1}, sort=[("archived_at", DESCENDING)])
        return {
            "total_count": self.intelligence_collection.count_documents({}),
            "base_uuid": str(newest["_id"]) if newest else None,
        }

    def query_intelligence(
        self,
        *,
        period: tuple[datetime, datetime] | None = None,
        archive_period: tuple[datetime, datetime] | None = None,
        peoples=None,
        locations=None,
        organizations=None,
        geography=None,
        keywords: str = "",
        informant_domains=None,
        threshold: float | None = None,
        threshold_max: float | None = None,
        skip: int = 0,
        limit: int = 20,
        **_: Any,
    ) -> tuple[list[dict], int]:
        clauses: list[dict] = []
        if period:
            clauses.append({"raw_data.pub_time": {"$gte": period[0], "$lte": period[1]}})
        if archive_period:
            clauses.append({"archived_at": {
                "$gte": archive_period[0], "$lte": archive_period[1]}})
        score_range = {}
        if threshold is not None:
            score_range["$gte"] = float(threshold)
        if threshold_max is not None:
            score_range["$lte"] = float(threshold_max)
        if score_range:
            clauses.append({"total_score": score_range})
        if informant_domains:
            domains = self._as_list(informant_domains)
            clauses.append({"$or": [
                {"informant": {"$regex": re.escape(value), "$options": "i"}}
                for value in domains
            ]})
        if keywords:
            for keyword in self._keywords(keywords):
                regex = {"$regex": re.escape(keyword), "$options": "i"}
                clauses.append({"$or": [
                    {"analysis.message.title": regex},
                    {"analysis.message.brief": regex},
                    {"analysis.message.text": regex},
                    {"raw_data.content": regex},
                ]})

        entity_groups = {
            "peoples": peoples,
            "locations": locations,
            "organizations": organizations,
            "geography": geography,
        }
        intelligence_ids = self._intelligence_ids_for_entities(entity_groups)
        if intelligence_ids is not None:
            clauses.append({"_id": {"$in": sorted(intelligence_ids)}})

        query = {"$and": clauses} if clauses else {}
        total = self.intelligence_collection.count_documents(query)
        cursor = (
            self.intelligence_collection.find(query)
            .sort([("archived_at", DESCENDING), ("_id", DESCENDING)])
            .skip(max(0, int(skip)))
            .limit(max(0, int(limit)))
        )
        return list(cursor), total

    def aggregate(self, pipeline: list) -> list:
        return list(self.intelligence_collection.aggregate(pipeline))

    def count_documents(self, query: dict) -> int:
        return self.intelligence_collection.count_documents(query)

    def get_latest_archive_timestamp(self) -> float | None:
        latest = self.intelligence_collection.find_one(
            {}, {"archived_at": 1}, sort=[("archived_at", DESCENDING)])
        value = latest.get("archived_at") if latest else None
        return value.timestamp() if isinstance(value, datetime) else None

    def get_source_domains(self, limit: int = 200) -> list[dict]:
        pipeline = [
            {"$match": {"informant": {"$type": "string", "$ne": ""}}},
            {"$group": {"_id": "$informant", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": max(1, int(limit))},
        ]
        return list(self.intelligence_collection.aggregate(pipeline))

    def _intelligence_ids_for_entities(self, groups: dict[str, Any]) -> set[str] | None:
        matched_groups: list[set[str]] = []
        for group, raw_values in groups.items():
            values = self._as_list(raw_values)
            if not values:
                continue
            entity_filter = {
                "entity_type": {"$in": list(self.ENTITY_TYPES[group])},
                "$or": [
                    {"canonical_name": {"$in": values}},
                    {"aliases": {"$in": values}},
                ],
            }
            entity_ids = [str(item["_id"]) for item in self.entity_collection.find(
                entity_filter, {"_id": 1})]
            if not entity_ids:
                return set()
            intelligence_ids = {
                str(item["intelligence_uuid"])
                for item in self.event_collection.find(
                    {"role_bindings.entity_uuid": {"$in": entity_ids}},
                    {"intelligence_uuid": 1})
            }
            matched_groups.append(intelligence_ids)
        if not matched_groups:
            return None
        result = matched_groups[0]
        for values in matched_groups[1:]:
            result &= values
        return result

    def _enrich(self, document: dict) -> dict:
        result = dict(document)
        events = list(self.event_collection.find(
            {"intelligence_uuid": str(document["_id"])}))
        entity_ids = set()
        for event in events:
            entity_ids.update(
                str(binding["entity_uuid"])
                for binding in event.get("role_bindings", [])
                if binding.get("entity_uuid"))
            entity_ids.update(str(value) for value in event.get(
                "location_entity_uuids", []) if value)
            for qualifier in event.get("qualifiers", []):
                entity_ids.update(str(value) for value in qualifier.get("by", []) if value)
        entities = list(self.entity_collection.find(
            {"_id": {"$in": sorted(entity_ids)}})) if entity_ids else []
        result["events"] = events
        result["entities"] = entities
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
