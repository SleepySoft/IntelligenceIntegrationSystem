"""Event V4 的 MongoDB 实体与幂等归档实现。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from pymongo import ASCENDING, DESCENDING, ReplaceOne

from event_engine.query.serialization import event_to_document
from event_engine.schema import EventRecord
from ServiceComponent.event_v4_archive import (
    ArchivedIntelligenceV4,
    LowValueIntelligenceV4,
    archive_document,
)
from ServiceComponent.event_v4_conversion import ResolvedEntity
from ServiceComponent.intelligence_time import resolve_content_time


class MongoEntityRepository:
    """以规范身份键 upsert 的基础实体仓库。"""

    def __init__(self, collection: Any):
        self.collection = collection

    def ensure_indexes(self) -> None:
        self.collection.create_index(
            [("identity_key", ASCENDING)], unique=True, name="entity_identity_key_unique"
        )

    def upsert(self, entity: ResolvedEntity, *, alias: str) -> ResolvedEntity:
        document = {
            "_id": str(entity.uuid),
            "identity_key": entity.identity_key,
            "canonical_name": entity.canonical_name,
            "entity_type": entity.entity_type,
            "country_code": entity.country_code,
        }
        self.collection.update_one(
            {"identity_key": entity.identity_key},
            {
                "$setOnInsert": document,
                "$addToSet": {"aliases": alias},
                "$set": {"updated_at": datetime.now(timezone.utc)},
            },
            upsert=True,
        )
        stored = self.collection.find_one({"identity_key": entity.identity_key})
        if stored is None:
            raise RuntimeError("实体 upsert 后无法读取")
        return ResolvedEntity(
            uuid=UUID(str(stored["_id"])),
            identity_key=stored["identity_key"],
            canonical_name=stored["canonical_name"],
            entity_type=stored["entity_type"],
            country_code=stored.get("country_code"),
        )


class MongoEventV4ArchiveRepository:
    """使用 outbox 实现可恢复的多集合幂等提交。

    写入顺序固定为 outbox(pending) -> events -> intelligence -> outbox(committed)。
    因此对外可见的情报文档出现前，其引用的事件一定已经写入；中途失败留下的 pending
    记录可由 ``recover_pending`` 重放。事件和情报均按稳定 UUID upsert。
    """

    def __init__(
        self,
        *,
        intelligence_collection: Any,
        low_value_collection: Any,
        event_collection: Any,
        outbox_collection: Any,
    ):
        self.intelligence_collection = intelligence_collection
        self.low_value_collection = low_value_collection
        self.event_collection = event_collection
        self.outbox_collection = outbox_collection

    def ensure_indexes(self) -> None:
        self._backfill_content_time()
        self.intelligence_collection.create_index(
            [("informant", ASCENDING)], unique=True, name="intelligence_informant_unique"
        )
        self.intelligence_collection.create_index(
            [("content_time", DESCENDING), ("_id", DESCENDING)],
            name="intelligence_content_time",
        )
        self.low_value_collection.create_index(
            [("informant", ASCENDING)], name="low_value_informant"
        )
        self.event_collection.create_index(
            [("intelligence_uuid", ASCENDING)], name="event_intelligence_uuid"
        )
        self.event_collection.create_index(
            [("predicate.id", ASCENDING)], name="event_predicate"
        )
        self.outbox_collection.create_index(
            [("status", ASCENDING), ("updated_at", ASCENDING)], name="outbox_recovery"
        )

    def contains(self, intelligence_uuid: UUID, informant: str) -> bool:
        conditions = [{"_id": str(intelligence_uuid)}]
        if informant:
            conditions.append({"informant": informant})
        return self.intelligence_collection.find_one({"$or": conditions}, {"_id": 1}) is not None

    def save_low_value(self, record: LowValueIntelligenceV4) -> None:
        document = archive_document(record)
        self.low_value_collection.replace_one(
            {"_id": document["_id"]}, document, upsert=True
        )

    def commit(
        self,
        record: ArchivedIntelligenceV4,
        events: tuple[EventRecord, ...],
    ) -> None:
        intelligence_id = str(record.intelligence_uuid)
        event_documents = [event_to_document(event) for event in events]
        self._validate_batch(record, event_documents)
        archive = archive_document(record)
        now = datetime.now(timezone.utc)
        self.outbox_collection.update_one(
            {"_id": intelligence_id},
            {
                "$set": {
                    "status": "pending",
                    "archive": archive,
                    "events": event_documents,
                    "updated_at": now,
                },
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )
        self._write_documents(archive, event_documents)
        self.outbox_collection.update_one(
            {"_id": intelligence_id},
            {"$set": {"status": "committed", "updated_at": datetime.now(timezone.utc)}},
        )

    def recover_pending(self, *, limit: int = 100) -> int:
        recovered = 0
        cursor = self.outbox_collection.find({"status": "pending"}).sort("updated_at", ASCENDING)
        if limit > 0:
            cursor = cursor.limit(limit)
        for item in cursor:
            self._write_documents(item["archive"], item["events"])
            self.outbox_collection.update_one(
                {"_id": item["_id"], "status": "pending"},
                {"$set": {"status": "committed", "updated_at": datetime.now(timezone.utc)}},
            )
            recovered += 1
        return recovered

    def _write_documents(self, archive: dict, events: list[dict]) -> None:
        archive = self._with_content_time(archive)
        if events:
            self.event_collection.bulk_write(
                [ReplaceOne({"_id": item["_id"]}, item, upsert=True) for item in events],
                ordered=True,
            )
        self.intelligence_collection.replace_one(
            {"_id": archive["_id"]}, archive, upsert=True
        )

    def _backfill_content_time(self) -> None:
        """一次性为旧 V4 文档物化主时间，之后排序可直接使用索引。"""

        convert_pub = {
            "$convert": {
                "input": "$raw_data.pub_time", "to": "date",
                "onError": None, "onNull": None,
            }
        }
        convert_collect = {
            "$convert": {
                "input": "$raw_data.collect_time", "to": "date",
                "onError": None, "onNull": None,
            }
        }
        pub_has_clock = {
            "$and": [
                {"$ne": [convert_pub, None]},
                {"$ne": [
                    {"$dateToString": {
                        "format": "%H:%M:%S.%L", "date": convert_pub,
                        "timezone": "UTC", "onNull": None,
                    }},
                    "00:00:00.000",
                ]},
            ]
        }
        self.intelligence_collection.update_many(
            {"$or": [
                {"content_time": {"$exists": False}},
                {"content_time": None},
                {"content_time_source": {"$exists": False}},
                {"content_time_source": None},
                {
                    "content_time_source": "published",
                    "$expr": {"$eq": [
                        {"$dateToString": {
                            "format": "%H:%M:%S.%L", "date": convert_pub,
                            "timezone": "UTC", "onNull": None,
                        }},
                        "00:00:00.000",
                    ]},
                },
            ]},
            [{"$set": {
                "content_time": {
                    "$cond": [
                        pub_has_clock,
                        convert_pub,
                        {"$ifNull": [convert_collect, "$archived_at"]},
                    ]
                },
                "content_time_source": {
                    "$switch": {
                        "branches": [
                            {"case": pub_has_clock, "then": "published"},
                            {"case": {"$ne": [convert_collect, None]}, "then": "collected"},
                        ],
                        "default": "archived",
                    }
                },
            }}],
        )

    @staticmethod
    def _with_content_time(archive: dict) -> dict:
        result = dict(archive)
        value, source = resolve_content_time(
            result.get("raw_data") or {}, result.get("archived_at"))
        result["content_time"] = value
        result["content_time_source"] = source
        return result

    @staticmethod
    def _validate_batch(record: ArchivedIntelligenceV4, events: list[dict]) -> None:
        expected = {str(value) for value in record.event_uuids}
        actual = {str(value["_id"]) for value in events}
        if not events or len(actual) != len(events) or actual != expected:
            raise ValueError("归档信封与事件批次 UUID 不一致")
        intelligence_id = str(record.intelligence_uuid)
        if any(value.get("intelligence_uuid") != intelligence_id for value in events):
            raise ValueError("事件批次包含其他情报的观察")
        if str(record.primary_event_uuid) not in actual:
            raise ValueError("主事件 UUID 不在事件批次中")
