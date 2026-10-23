"""EntityResolution 在 IIS 中的可选、故障隔离装配。"""

from __future__ import annotations

import logging

from .model import EntityRef
from .service import EntityResolutionService, ResolutionScope
from .store import MongoResolutionStore


logger = logging.getLogger(__name__)


def _reference_loader(archive_collection):
    def load(ref: EntityRef):
        document = archive_collection.find_one(
            {"UUID": ref.intelligence_uuid},
            {"_id": 0, "EVENT_EXTRACTION.entities": 1},
        )
        extraction = (document or {}).get("EVENT_EXTRACTION") or {}
        for entity in extraction.get("entities") or []:
            if str(entity.get("id") or "") == ref.local_entity_id:
                return dict(entity)
        return None
    return load


def create_entity_resolution(*, subsystem_registry, entity_registry=None):
    """复用各子系统 MongoDB，构造统一的指称解析服务。失败时返回 None。"""
    try:
        scopes = {}
        for ctx in subsystem_registry.enabled_subsystems():
            database = ctx.mongo_db_cache.db
            store = MongoResolutionStore(
                global_resolutions=database[ctx.collection_name("entity_global_resolutions")],
                contextual_entities=database[ctx.collection_name("contextual_entities")],
                contextual_resolutions=database[
                    ctx.collection_name("contextual_entity_resolutions")],
                audit=database[ctx.collection_name("entity_resolution_audit")],
            )
            store.ensure_indexes()
            scopes[ctx.name] = ResolutionScope(
                store=store,
                reference_loader=_reference_loader(
                    ctx.event_v4_intelligence_collection),
            )
        return EntityResolutionService(scopes, entity_registry=entity_registry)
    except Exception:
        logger.exception("EntityResolution 初始化失败，实体图退回只读旧身份模式。")
        return None
