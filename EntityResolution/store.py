"""MongoDB 持久化：全局实体解析与上下文实体解析物理分离。"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Iterable

from pymongo import ASCENDING, DESCENDING

from .model import EntityRef, ResolutionError


def _clean(document: dict[str, Any] | None) -> dict[str, Any] | None:
    if document is None:
        return None
    result = dict(document)
    result.pop("_id", None)
    return result


class MongoResolutionStore:
    """一个子系统的解析集合组。"""

    def __init__(self, *, global_resolutions, contextual_entities,
                 contextual_resolutions, audit):
        self.global_resolutions = global_resolutions
        self.contextual_entities = contextual_entities
        self.contextual_resolutions = contextual_resolutions
        self.audit = audit

    def ensure_indexes(self) -> None:
        for collection in (self.global_resolutions, self.contextual_resolutions):
            collection.create_index(
                [("ref.intelligence_uuid", ASCENDING), ("ref.local_entity_id", ASCENDING)],
                name="entity_ref_lookup",
            )
            collection.create_index(
                [("updated_at", DESCENDING)], name="resolution_updated_at")
        self.global_resolutions.create_index(
            [("target_entity_id", ASCENDING)], name="global_target")
        self.contextual_resolutions.create_index(
            [("target_contextual_entity_id", ASCENDING)], name="contextual_target")
        self.contextual_entities.create_index(
            [("name", ASCENDING)], name="contextual_name")
        self.audit.create_index(
            [("decision_id", ASCENDING)], unique=True, name="decision_unique")
        self.audit.create_index(
            [("ref_key", ASCENDING), ("created_at", DESCENDING)], name="audit_ref_time")

    def resolve_many(self, refs: Iterable[EntityRef]) -> dict[str, dict[str, Any]]:
        keys = list(dict.fromkeys(ref.key for ref in refs))
        if not keys:
            return {}
        result: dict[str, dict[str, Any]] = {}
        for document in self.global_resolutions.find({"_id": {"$in": keys}}):
            result[str(document["_id"])] = {
                "kind": "global",
                "target_id": document["target_entity_id"],
                "decision_id": document.get("decision_id"),
            }
        for document in self.contextual_resolutions.find({"_id": {"$in": keys}}):
            target_id = document["target_contextual_entity_id"]
            context = self.contextual_entities.find_one({"id": target_id})
            result[str(document["_id"])] = {
                "kind": "contextual",
                "target_id": target_id,
                "name": (context or {}).get("name") or target_id,
                "type": (context or {}).get("type") or "other",
                "decision_id": document.get("decision_id"),
            }
        return result

    def get_assignment(self, ref: EntityRef) -> dict[str, Any] | None:
        global_value = self.global_resolutions.find_one({"_id": ref.key})
        if global_value:
            return {
                "kind": "global",
                "target_id": global_value["target_entity_id"],
                "decision_id": global_value.get("decision_id"),
            }
        contextual = self.contextual_resolutions.find_one({"_id": ref.key})
        if contextual:
            return {
                "kind": "contextual",
                "target_id": contextual["target_contextual_entity_id"],
                "decision_id": contextual.get("decision_id"),
            }
        return None

    def apply_assignment(self, ref: EntityRef, assignment: dict[str, Any] | None) -> None:
        self.global_resolutions.delete_one({"_id": ref.key})
        self.contextual_resolutions.delete_one({"_id": ref.key})
        if assignment is None:
            return
        now = datetime.now(timezone.utc)
        base = {
            "_id": ref.key,
            "ref": ref.to_dict(),
            "surface": str(assignment.get("surface") or ""),
            "normalized_surface": str(assignment.get("normalized_surface") or ""),
            "method": str(assignment.get("method") or "manual"),
            "confidence": float(assignment.get("confidence", 1.0)),
            "decision_id": assignment.get("decision_id"),
            "updated_at": now,
        }
        if assignment["kind"] == "global":
            base["target_entity_id"] = assignment["target_id"]
            self.global_resolutions.replace_one({"_id": ref.key}, base, upsert=True)
        elif assignment["kind"] == "contextual":
            base["target_contextual_entity_id"] = assignment["target_id"]
            self.contextual_resolutions.replace_one({"_id": ref.key}, base, upsert=True)
        else:
            raise ResolutionError("未知解析目标类型")

    def create_contextual(self, document: dict[str, Any]) -> dict[str, Any]:
        self.contextual_entities.insert_one(dict(document))
        return dict(document)

    def get_contextual(self, contextual_entity_id: str) -> dict[str, Any] | None:
        return _clean(self.contextual_entities.find_one({"id": contextual_entity_id}))

    def search_contextual(self, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
        condition = {}
        if query.strip():
            condition = {"name": {"$regex": re.escape(query.strip()), "$options": "i"}}
        cursor = self.contextual_entities.find(condition, {"_id": 0}).sort(
            "updated_at", DESCENDING).limit(limit)
        return [dict(item) for item in cursor]

    def insert_audit(self, document: dict[str, Any]) -> None:
        self.audit.insert_one(dict(document))

    def get_audit(self, decision_id: str) -> dict[str, Any] | None:
        return _clean(self.audit.find_one({"decision_id": decision_id}))

    def mark_undone(self, decision_id: str, *, actor: str) -> None:
        self.audit.update_one(
            {"decision_id": decision_id, "undone_at": {"$exists": False}},
            {"$set": {"undone_at": datetime.now(timezone.utc), "undone_by": actor}},
        )

    def list_references(self, target_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
        if target_id.startswith("ent_"):
            cursor = self.global_resolutions.find(
                {"target_entity_id": target_id}, {"_id": 0}).limit(limit)
        else:
            cursor = self.contextual_resolutions.find(
                {"target_contextual_entity_id": target_id}, {"_id": 0}).limit(limit)
        return [dict(item) for item in cursor]
