"""Event V4 MongoDB 归档与派生事件索引。"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any

from pymongo import ASCENDING, DESCENDING, ReplaceOne

from ServiceComponent.event_v4_archive import ArchivedDataV4, archive_document
from ServiceComponent.intelligence_contracts import (
    APPENDIX_CONTENT_TIME,
    APPENDIX_TIME_ARCHIVED,
    APPENDIX_TIME_GOT,
    APPENDIX_TOTAL_SCORE,
    appendix_path,
)

logger = logging.getLogger(__name__)


class MongoEventV4ArchiveRepository:
    """保存权威归档文档，并维护可从 EVENT_EXTRACTION 重建的事件查询索引。"""

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
        self.intelligence_collection.create_index(
            [("UUID", ASCENDING)], unique=True, name="intelligence_uuid_unique"
        )
        self.intelligence_collection.create_index(
            [("INFORMANT", ASCENDING)], unique=True, name="intelligence_informant_unique"
        )
        self.intelligence_collection.create_index(
            [(appendix_path(APPENDIX_CONTENT_TIME), DESCENDING), ("UUID", DESCENDING)],
            name="intelligence_content_time",
        )
        self.intelligence_collection.create_index(
            [(appendix_path(APPENDIX_TIME_ARCHIVED), DESCENDING), ("UUID", DESCENDING)],
            name="intelligence_archived_time_uuid",
        )
        self.intelligence_collection.create_index(
            [(appendix_path(APPENDIX_TOTAL_SCORE), DESCENDING)],
            name="intelligence_total_score",
        )
        self.low_value_collection.create_index(
            [("UUID", ASCENDING)], unique=True, name="low_value_uuid_unique"
        )
        self.low_value_collection.create_index(
            [("INFORMANT", ASCENDING)], name="low_value_informant"
        )
        self.low_value_collection.create_index(
            [(appendix_path(APPENDIX_TIME_GOT), DESCENDING),
             ("INFORMANT", ASCENDING), ("NON_INTELLIGENCE_REASON", ASCENDING)],
            name="low_value_source_reason_time",
        )
        self.event_collection.create_index(
            [("event_uuid", ASCENDING)], unique=True, name="event_uuid_unique"
        )
        self.event_collection.create_index(
            [("intelligence_uuid", ASCENDING)], name="event_intelligence_uuid"
        )
        self.event_collection.create_index(
            [("predicate.id", ASCENDING)], name="event_predicate"
        )
        self.outbox_collection.create_index(
            [("UUID", ASCENDING)], unique=True, name="outbox_uuid_unique"
        )
        self.outbox_collection.create_index(
            [("status", ASCENDING), ("updated_at", ASCENDING)], name="outbox_recovery"
        )

    def contains_source(self, informant: str) -> bool:
        return bool(informant) and self.intelligence_collection.find_one(
            {"INFORMANT": informant}, {"_id": 1}
        ) is not None

    def contains_id(self, intelligence_uuid) -> bool:
        return self.intelligence_collection.find_one(
            {"UUID": str(intelligence_uuid)}, {"_id": 1}
        ) is not None

    def save_low_value(self, record: ArchivedDataV4) -> None:
        document = archive_document(record)
        self.low_value_collection.replace_one(
            {"UUID": document["UUID"]}, document, upsert=True
        )

    def commit(self, record: ArchivedDataV4, events: tuple[dict[str, Any], ...]) -> None:
        archive = archive_document(record)
        event_documents = [dict(event) for event in events]
        # ArchivedDataV4（含 EVENT_EXTRACTION）是权威记录，必须先独立完成。
        self.intelligence_collection.replace_one(
            {"UUID": archive["UUID"]}, archive, upsert=True
        )
        if not event_documents:
            return
        now = datetime.now(timezone.utc)
        try:
            self.outbox_collection.update_one(
                {"UUID": record.UUID},
                {
                    "$set": {
                        "status": "pending",
                        "events": event_documents,
                        "updated_at": now,
                    },
                    "$setOnInsert": {"created_at": now},
                },
                upsert=True,
            )
            self._write_event_index(event_documents)
            self._mark_outbox_committed(record.UUID)
        except Exception:
            logger.exception(
                "派生事件 outbox/索引写入失败，文章 %s 已正常归档；索引可由归档重建。",
                record.UUID,
            )
            return

    def recover_pending(self, *, limit: int = 100) -> int:
        recovered = 0
        cursor = self.outbox_collection.find({"status": "pending"}).sort("updated_at", ASCENDING)
        if limit > 0:
            cursor = cursor.limit(limit)
        for item in cursor:
            try:
                self._write_event_index(item["events"])
                self.outbox_collection.update_one(
                    {"_id": item["_id"], "status": "pending"},
                    {"$set": {"status": "committed", "updated_at": datetime.now(timezone.utc)}},
                )
                recovered += 1
            except Exception:
                logger.exception("恢复文章 %s 的派生事件索引失败。", item.get("UUID"))
        return recovered

    def _write_event_index(self, events: list[dict]) -> None:
        self.event_collection.bulk_write(
            [
                ReplaceOne({"event_uuid": item["event_uuid"]}, item, upsert=True)
                for item in events
            ],
            ordered=True,
        )

    def _mark_outbox_committed(self, intelligence_uuid: str) -> None:
        self.outbox_collection.update_one(
            {"UUID": intelligence_uuid},
            {"$set": {"status": "committed", "updated_at": datetime.now(timezone.utc)}},
        )
