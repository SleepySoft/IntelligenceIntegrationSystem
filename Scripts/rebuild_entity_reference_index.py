#!/usr/bin/env python3
"""从权威 Event V4 归档重建带 local_entity_id 的派生事件索引。

默认只检查并报告；传入 ``--apply`` 才写 MongoDB。默认跳过已经完整携带 ENT ID 的文章，
使用 ``--all`` 可强制重建扫描范围内的全部记录。
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import logging
import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pymongo import ReplaceOne  # noqa: E402

from GlobalConfig import DEFAULT_ALTER_CONFIG_FILE, DEFAULT_CONFIG_FILE  # noqa: E402
from IISConfig import load_iis_config  # noqa: E402
from ServiceComponent.IntelligenceHubDefines_v4 import DEFAULT_EVENT_REGISTRY  # noqa: E402
from ServiceComponent.DomainResources import DomainResourceRegistry  # noqa: E402
from ServiceComponent.event_v4_archive import ArchivedDataV4  # noqa: E402
from ServiceComponent.event_v4_conversion import convert_event_extraction  # noqa: E402
from ServiceComponent.intelligence_contracts import (  # noqa: E402
    APPENDIX_DOMAIN_NAME,
    APPENDIX_LEGACY_SUBSYSTEM,
    APPENDIX_TIME_ARCHIVED,
    APPENDIX_TIME_DONE,
)


logger = logging.getLogger("rebuild_entity_reference_index")


def event_index_has_entity_refs(events: list[dict[str, Any]]) -> bool:
    """只要存在实体快照，就要求每个快照都有合法 ENT ID。"""
    found = False
    for event in events:
        groups = list((event.get("roles") or {}).values())
        groups.append(event.get("event_location") or [])
        groups.extend((item.get("by") or []) for item in event.get("qualifiers") or [])
        for values in groups:
            for entity in values or []:
                found = True
                local_id = str((entity or {}).get("local_entity_id") or "")
                if not local_id.startswith("ENT"):
                    return False
    return found


def _config():
    return load_iis_config(DEFAULT_CONFIG_FILE, fallback_file=DEFAULT_ALTER_CONFIG_FILE)


def _registry(config):
    return DomainResourceRegistry.build_from_config(
        config,
        mongodb_params={
            "host": config.get("mongodb.host", "localhost"),
            "port": config.get("mongodb.port", 27017),
            "username": config.get("mongodb.user", ""),
            "password": config.get("mongodb.password", ""),
        },
        db_name="IntelligenceIntegrationSystem",
    )


def _observed_at(record: ArchivedDataV4) -> datetime:
    value = record.APPENDIX.get(APPENDIX_TIME_DONE) or record.APPENDIX.get(APPENDIX_TIME_ARCHIVED)
    if not isinstance(value, datetime):
        return datetime.now(timezone.utc)
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def run(args) -> dict[str, Any]:
    registry = _registry(_config())
    context = registry.get(args.domain) if args.domain else registry.default()
    if context is None or not context.enabled:
        raise ValueError(f"情报域不存在或未启用：{args.domain!r}")

    archives = context.event_v4_intelligence_collection
    event_collection = context.event_v4_event_collection
    cursor = archives.find(
        {"EVENT_EXTRACTION.entities.0": {"$exists": True}},
        {"_id": 0},
    ).batch_size(max(1, args.batch_size))
    if args.limit > 0:
        cursor = cursor.limit(args.limit)
    stats = {
        "domain_name": context.name,
        "mode": "apply" if args.apply else "dry-run",
        "scanned": 0,
        "already_ready": 0,
        "needs_rebuild": 0,
        "rebuilt_articles": 0,
        "rebuilt_events": 0,
        "invalid": 0,
    }
    try:
        for document in cursor:
            stats["scanned"] += 1
            intelligence_uuid = str(document.get("UUID") or "")
            existing = list(event_collection.find(
                {"intelligence_uuid": intelligence_uuid}, {"_id": 0}))
            if not args.all and event_index_has_entity_refs(existing):
                stats["already_ready"] += 1
                continue
            stats["needs_rebuild"] += 1
            try:
                record = ArchivedDataV4.model_validate(document)
                conversion = convert_event_extraction(
                    record.EVENT_EXTRACTION,
                    intelligence_uuid=record.UUID,
                    registry=DEFAULT_EVENT_REGISTRY,
                    observed_at=_observed_at(record),
                    metadata={
                        "informant": record.INFORMANT,
                        "domain_name": (
                            record.APPENDIX.get(APPENDIX_DOMAIN_NAME)
                            or record.APPENDIX.get(APPENDIX_LEGACY_SUBSYSTEM)
                            or context.name
                        ),
                    },
                )
            except Exception as exc:
                stats["invalid"] += 1
                logger.error("跳过非法归档 UUID=%s: %s", intelligence_uuid, exc)
                continue
            if not args.apply:
                continue
            event_ids = [item["event_uuid"] for item in conversion.events]
            event_collection.delete_many({
                "intelligence_uuid": intelligence_uuid,
                "event_uuid": {"$nin": event_ids},
            })
            if conversion.events:
                event_collection.bulk_write([
                    ReplaceOne({"event_uuid": item["event_uuid"]}, item, upsert=True)
                    for item in conversion.events
                ], ordered=True)
            stats["rebuilt_articles"] += 1
            stats["rebuilt_events"] += len(conversion.events)
    finally:
        cursor.close()
    return stats


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="重建携带 ENT ID 的 Event V4 派生事件索引")
    parser.add_argument("--apply", action="store_true", help="实际写入；默认仅检查")
    parser.add_argument("--all", action="store_true", help="强制重建全部扫描记录")
    parser.add_argument(
        "--domain", "--subsystem", dest="domain", default="",
        help="目标情报域；默认使用默认领域（--subsystem 为旧别名）",
    )
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--batch-size", type=int, default=200)
    args = parser.parse_args()
    if args.limit < 0 or args.batch_size <= 0:
        parser.error("limit 不能为负数，batch-size 必须大于 0")
    stats = run(args)
    print("完成：" + ", ".join(f"{key}={value}" for key, value in stats.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
