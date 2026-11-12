"""MongoDB-backed shadow analysis asset repository.

Shadow results are not production archives, but they are durable evaluation assets. They
therefore live in MongoDB with domain-aware indexes instead of transient JSON files.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Mapping
from urllib.parse import urlparse

from bson import ObjectId
from pymongo import ASCENDING, DESCENDING, ReturnDocument


logger = logging.getLogger(__name__)


class ShadowAnalysisRecorder:
    """Persist and query domain-scoped shadow analyses in one Mongo collection."""

    SCHEMA_VERSION = 4

    def __init__(self, collection: Any, *, enabled: bool = True):
        self.collection = collection
        self.enabled = bool(enabled)
        self.ensure_indexes()

    def ensure_indexes(self) -> None:
        self.collection.create_index(
            [("domain_name", ASCENDING), ("source_uuid", ASCENDING),
             ("captured_at", DESCENDING)],
            name="shadow_domain_source_time",
        )
        self.collection.create_index(
            [("domain_name", ASCENDING), ("shadow_prompt_version", ASCENDING),
             ("ai_model", ASCENDING), ("captured_at", DESCENDING)],
            name="shadow_domain_prompt_model_time",
        )
        self.collection.create_index(
            [("domain_name", ASCENDING), ("source_uuid", ASCENDING),
             ("experiment_id", ASCENDING), ("shadow_prompt_version", ASCENDING)],
            name="shadow_experiment_result_unique",
            unique=True,
            partialFilterExpression={"experiment_id": {"$type": "string"}},
        )

    def record(self, *, original_data: Mapping[str, Any], domain_name: str = "",
               subsystem: str | None = None,
               primary_prompt_version: int, shadow_prompt_version: int,
               analysis: Any, ai_service: str, ai_model: str,
               extraction_quality: Any = None,
               total_score: float | None = None,
               experiment_id: str = "", sampling_key: str = "",
               primary_prompt_sha256: str = "", shadow_prompt_sha256: str = "",
               code_revision: str = "", primary_ai_service: str = "",
               primary_ai_model: str = "", primary_client_name: str = "",
               shadow_client_name: str = "") -> str | None:
        if not self.enabled:
            return None
        try:
            if domain_name and subsystem and domain_name != subsystem:
                raise ValueError("domain_name and legacy subsystem disagree")
            resolved_domain = str(domain_name or subsystem or "").strip()
            source_uuid = str(original_data.get("UUID") or "").strip()
            if not resolved_domain or not source_uuid:
                raise ValueError("shadow record requires domain_name and source UUID")
            resolved_experiment = str(experiment_id or "").strip() or (
                f"legacy:{resolved_domain}:p{int(primary_prompt_version)}:"
                f"s{int(shadow_prompt_version)}")
            informant = str(original_data.get("informant") or "")
            document = {
                "schema_version": self.SCHEMA_VERSION,
                "captured_at": datetime.now(timezone.utc),
                "source_uuid": source_uuid,
                "domain_name": resolved_domain,
                "primary_prompt_version": int(primary_prompt_version),
                "shadow_prompt_version": int(shadow_prompt_version),
                "experiment_id": resolved_experiment,
                "sampling_key": str(sampling_key or ""),
                "primary_prompt_sha256": str(primary_prompt_sha256 or ""),
                "shadow_prompt_sha256": str(shadow_prompt_sha256 or ""),
                "code_revision": str(code_revision or ""),
                "ai_service": str(ai_service or ""),
                "ai_model": str(ai_model or ""),
                "primary_ai_service": str(primary_ai_service or ""),
                "primary_ai_model": str(primary_ai_model or ""),
                "primary_client_name": str(primary_client_name or ""),
                "shadow_client_name": str(shadow_client_name or ""),
                "input": {
                    "title": original_data.get("title"),
                    "informant": informant,
                    "source_host": urlparse(informant).netloc.lower(),
                    "source": original_data.get("source"),
                },
                "analysis": analysis,
                "extraction_quality": extraction_quality,
                "total_score": total_score,
            }
            identity = {
                "domain_name": resolved_domain,
                "source_uuid": source_uuid,
                "experiment_id": resolved_experiment,
                "shadow_prompt_version": int(shadow_prompt_version),
            }
            updater = getattr(self.collection, "find_one_and_update", None)
            if callable(updater):
                stored = updater(
                    identity, {"$setOnInsert": document}, upsert=True,
                    return_document=ReturnDocument.AFTER,
                )
            else:  # 轻量测试存根兼容；生产 Mongo 始终走原子 upsert。
                stored = self.collection.find_one(identity)
                if stored is None:
                    inserted = self.collection.insert_one(document)
                    stored = dict(document, _id=inserted.inserted_id)
            return str((stored or {}).get("_id") or "") or None
        except Exception:
            logger.exception("Failed to persist shadow analysis record.")
            return None

    @staticmethod
    def _summary(document: Mapping[str, Any]) -> dict[str, Any]:
        captured = document.get("captured_at")
        record_id = str(document.get("_id") or "")
        return {
            "record_id": record_id,
            "file_name": record_id,  # 旧比较器字段的只读兼容别名
            "source_uuid": document.get("source_uuid"),
            "domain_name": document.get("domain_name"),
            "shadow_prompt_version": document.get("shadow_prompt_version"),
            "primary_prompt_version": document.get("primary_prompt_version"),
            "experiment_id": document.get("experiment_id"),
            "ai_service": document.get("ai_service"),
            "ai_model": document.get("ai_model"),
            "captured_at": captured.isoformat() if hasattr(captured, "isoformat") else captured,
            "quality": document.get("extraction_quality"),
            "total_score": document.get("total_score"),
        }

    def list_by_uuid(self, source_uuid: str, *, domain_name: str | None = None) -> list[dict[str, Any]]:
        query: dict[str, Any] = {"source_uuid": str(source_uuid or "").strip()}
        if not query["source_uuid"]:
            return []
        if domain_name:
            query["domain_name"] = str(domain_name)
        return [
            self._summary(document)
            for document in self.collection.find(query, {"analysis": 0, "input": 0})
            .sort("captured_at", DESCENDING)
        ]

    def existing_source_uuids(
            self, source_uuids: list[str], *, domain_name: str) -> set[str]:
        """批量返回当前领域已有影子记录的情报 UUID，供列表页一次查询标注。"""
        normalized = sorted({str(value or "").strip() for value in source_uuids
                             if str(value or "").strip()})
        resolved_domain = str(domain_name or "").strip()
        if not normalized or not resolved_domain:
            return set()
        cursor = self.collection.find(
            {"domain_name": resolved_domain, "source_uuid": {"$in": normalized}},
            {"source_uuid": 1},
        )
        return {str(document.get("source_uuid") or "") for document in cursor
                if document.get("source_uuid")}

    def read_record(self, record_id: str, *, domain_name: str | None = None) -> dict[str, Any] | None:
        try:
            object_id = ObjectId(str(record_id or ""))
        except Exception:
            return None
        query: dict[str, Any] = {"_id": object_id}
        if domain_name:
            query["domain_name"] = str(domain_name)
        document = self.collection.find_one(query)
        if not document:
            return None
        document = dict(document)
        document["record_id"] = str(document.pop("_id"))
        captured = document.get("captured_at")
        if hasattr(captured, "isoformat"):
            document["captured_at"] = captured.isoformat()
        return document


__all__ = ["ShadowAnalysisRecorder"]
