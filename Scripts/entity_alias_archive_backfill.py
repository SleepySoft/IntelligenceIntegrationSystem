# -*- coding: utf-8 -*-
"""用 EntityAlias 标准名回填 Event V4 历史归档与派生事件索引（已弃用）。

此脚本会改写权威 EVENT_EXTRACTION 的原始实体名，与当前非破坏性 EntityResolution
设计冲突，仅保留用于审计既往操作；不要再对生产数据执行 ``--apply``。需要补齐 ENT ID
派生索引时请使用 ``Scripts/rebuild_entity_reference_index.py``。

默认仅预览；增加 ``--apply`` 才写 MongoDB。写入前会把所有待修改的原始归档
保存为 BSON Extended JSONL，随后通过 EventV4ArchiveRepository 同步权威归档、
PEOPLE/LOCATION/ORGANIZATION 速查字段、outbox 和 v4_events。

示例：
  python Scripts/entity_alias_archive_backfill.py
  python Scripts/entity_alias_archive_backfill.py --limit 100
  python Scripts/entity_alias_archive_backfill.py --apply
  python Scripts/entity_alias_archive_backfill.py --subsystem dry_run --apply
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import os
import sys
from typing import Any, Callable

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bson import json_util  # noqa: E402

from EntityAlias import EntityAliasService, JSONLStore  # noqa: E402
from EntityAlias.normalize import normalize_name  # noqa: E402
from GlobalConfig import (  # noqa: E402
    DEFAULT_ALTER_CONFIG_FILE,
    DEFAULT_CONFIG_FILE,
    EXPORT_PATH,
)
from IISConfig import load_iis_config  # noqa: E402
from ServiceComponent.IntelligenceHubDefines_v4 import DEFAULT_EVENT_REGISTRY  # noqa: E402
from ServiceComponent.SubsystemRegistry import SubsystemRegistry  # noqa: E402
from ServiceComponent.event_v4_archive import ArchivedDataV4  # noqa: E402
from ServiceComponent.event_v4_conversion import convert_event_extraction  # noqa: E402
from ServiceComponent.intelligence_contracts import (  # noqa: E402
    APPENDIX_SUBSYSTEM,
    APPENDIX_TIME_ARCHIVED,
    APPENDIX_TIME_DONE,
)


logger = logging.getLogger("entity_alias_archive_backfill")


@dataclass(frozen=True)
class ArchiveNormalizationPlan:
    record: ArchivedDataV4
    changes: tuple[dict[str, str], ...]
    resolve_errors: int = 0
    already_canonical: int = 0            # 已是标准名（无需替换）
    replaceable_aliases: int = 0          # 可替换别名（会被换成标准名）
    unmatched: tuple[str, ...] = ()       # 别名库未匹配的名字


def normalize_archive_document(
    document: dict[str, Any],
    resolver: Callable[[str], str | None],
) -> ArchiveNormalizationPlan:
    """生成规范化后的归档模型；未命中或解析异常均保留原名。

    实体逐项分类：已是标准名 / 可替换别名 / 未匹配 / 解析异常。"""
    payload = dict(document)
    payload.pop("_id", None)
    original = ArchivedDataV4.model_validate(payload)
    updated = original.model_dump(mode="python", by_alias=True)
    changes: list[dict[str, str]] = []
    unmatched: list[str] = []
    resolve_errors = 0
    already_canonical = 0
    replaceable_aliases = 0

    extraction = updated.get("EVENT_EXTRACTION")
    if extraction is not None:
        for entity in extraction.get("entities") or []:
            before = str(entity.get("name") or "").strip()
            try:
                after = str(resolver(before) or "").strip()
            except Exception:
                resolve_errors += 1
                logger.warning("实体名解析失败 %r，保留原名。", before, exc_info=True)
                continue
            if not after:
                unmatched.append(before)
                continue
            if normalize_name(after) == normalize_name(before):
                already_canonical += 1
            else:
                replaceable_aliases += 1
            if after == before:
                continue
            entity["name"] = after
            changes.append({
                "entity_id": str(entity.get("id") or ""),
                "before": before,
                "after": after,
            })

    # 重新校验会从已规范化的 EVENT_EXTRACTION 确定性重建三个实体速查字段。
    record = ArchivedDataV4.model_validate(updated)
    return ArchiveNormalizationPlan(
        record=record,
        changes=tuple(changes),
        resolve_errors=resolve_errors,
        already_canonical=already_canonical,
        replaceable_aliases=replaceable_aliases,
        unmatched=tuple(unmatched),
    )


def _load_config():
    return load_iis_config(DEFAULT_CONFIG_FILE, fallback_file=DEFAULT_ALTER_CONFIG_FILE)


def _build_registry(config):
    return SubsystemRegistry.build_from_config(
        config,
        mongodb_params={
            "host": config.get("mongodb.host", "localhost"),
            "port": config.get("mongodb.port", 27017),
            "username": config.get("mongodb.user", ""),
            "password": config.get("mongodb.password", ""),
        },
        db_name="IntelligenceIntegrationSystem",
    )


def _alias_service(config, data_dir: str | None = None) -> EntityAliasService:
    configured = config.get("entity_alias.data_dir", "")
    path = data_dir or configured or os.path.join("EntityAlias", "data")
    return EntityAliasService(JSONLStore(path))


def _canonical_resolver(service: EntityAliasService):
    def resolve(name: str) -> str | None:
        hit = service.resolve(name)
        return hit.get("canonical") if hit else None

    return resolve


def _observed_at(record: ArchivedDataV4) -> datetime:
    value = (record.APPENDIX.get(APPENDIX_TIME_DONE)
             or record.APPENDIX.get(APPENDIX_TIME_ARCHIVED))
    if not isinstance(value, datetime):
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _default_backup_path(subsystem: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return os.path.join(
        EXPORT_PATH,
        f"entity_alias_archive_backfill_{subsystem}_{stamp}.jsonl",
    )


def run(args) -> dict[str, Any]:
    config = _load_config()
    registry = _build_registry(config)
    context = registry.get(args.subsystem) if args.subsystem else registry.default()
    if context is None or not context.enabled:
        raise ValueError(f"子系统不存在或未启用：{args.subsystem!r}")
    if context.event_v4_archive_repository is None:
        raise RuntimeError(f"子系统 {context.name!r} 未配置 Event V4 归档仓库")

    service = _alias_service(config, args.data_dir)
    resolver = _canonical_resolver(service)
    collection = context.event_v4_intelligence_collection
    repository = context.event_v4_archive_repository
    cursor = collection.find(
        {"EVENT_EXTRACTION.entities.0": {"$exists": True}}
    ).batch_size(max(1, args.batch_size))
    if args.limit > 0:
        cursor = cursor.limit(args.limit)

    backup_path = args.backup or _default_backup_path(context.name)
    backup_file = None
    unmatched_counter: Counter = Counter()
    stats: dict[str, Any] = {
        "subsystem": context.name,
        "mode": "apply" if args.apply else "dry-run",
        "scanned": 0,
        "changed_documents": 0,
        "changed_names": 0,
        "already_canonical_names": 0,
        "replaceable_alias_names": 0,
        "unmatched_names": 0,
        "unchanged": 0,
        "invalid": 0,
        "resolve_errors": 0,
        "event_index_pending": 0,
        "backup": backup_path if args.apply else None,
        "unmatched_report": None,
    }
    sample_left = max(0, args.sample)

    try:
        for document in cursor:
            stats["scanned"] += 1
            try:
                plan = normalize_archive_document(document, resolver)
            except Exception as exc:
                stats["invalid"] += 1
                logger.error("跳过非法归档 UUID=%s: %s", document.get("UUID"), exc)
                continue
            stats["resolve_errors"] += plan.resolve_errors
            stats["already_canonical_names"] += plan.already_canonical
            stats["replaceable_alias_names"] += plan.replaceable_aliases
            unmatched_counter.update(plan.unmatched)
            if not plan.changes:
                stats["unchanged"] += 1
                continue

            stats["changed_documents"] += 1
            stats["changed_names"] += len(plan.changes)
            if sample_left:
                print(f"UUID={plan.record.UUID} changes={list(plan.changes)}")
                sample_left -= 1
            if not args.apply:
                continue

            if backup_file is None:
                os.makedirs(os.path.dirname(os.path.abspath(backup_path)), exist_ok=True)
                backup_file = open(backup_path, "w", encoding="utf-8", newline="\n")
            backup_file.write(json_util.dumps(document, ensure_ascii=False))
            backup_file.write("\n")
            backup_file.flush()

            conversion = convert_event_extraction(
                plan.record.EVENT_EXTRACTION,
                intelligence_uuid=plan.record.UUID,
                registry=DEFAULT_EVENT_REGISTRY,
                observed_at=_observed_at(plan.record),
                metadata={
                    "informant": plan.record.INFORMANT,
                    "subsystem": plan.record.APPENDIX.get(APPENDIX_SUBSYSTEM)
                                 or context.name,
                },
            )
            repository.commit(plan.record, conversion.events)
            outbox = context.event_v4_outbox_collection.find_one(
                {"UUID": plan.record.UUID}, {"status": 1})
            if outbox and outbox.get("status") != "committed":
                stats["event_index_pending"] += 1
    finally:
        cursor.close()
        if backup_file is not None:
            backup_file.close()

    if args.apply and stats["changed_documents"] == 0:
        stats["backup"] = None

    # 未匹配名称清单（报告，dry-run 也输出；可直接作为别名库补录的输入参考）
    stats["unmatched_names"] = sum(unmatched_counter.values())
    if unmatched_counter:
        unmatched_path = args.unmatched_out or os.path.join(
            EXPORT_PATH,
            f"entity_alias_unmatched_{context.name}_"
            f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
        )
        os.makedirs(os.path.dirname(os.path.abspath(unmatched_path)), exist_ok=True)
        report = [
            {"name": name, "count": count}
            for name, count in unmatched_counter.most_common()
        ]
        with open(unmatched_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        stats["unmatched_report"] = unmatched_path
    return stats


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="用 EntityAlias 标准名回填 Event V4 历史归档与事件索引")
    parser.add_argument("--apply", action="store_true",
                        help="实际写入 MongoDB；默认仅预览")
    parser.add_argument("--subsystem", default="",
                        help="目标子系统名；默认使用配置中的默认子系统")
    parser.add_argument("--data-dir", default=None,
                        help="EntityAlias JSONL 目录；默认读取 IIS 配置")
    parser.add_argument("--limit", type=int, default=0,
                        help="最多扫描文档数，0 表示不限")
    parser.add_argument("--batch-size", type=int, default=200)
    parser.add_argument("--sample", type=int, default=20,
                        help="最多打印多少篇变更样例")
    parser.add_argument("--backup", default="",
                        help="--apply 时的原始文档备份路径")
    parser.add_argument("--unmatched-out", default="",
                        help="未匹配名称清单输出路径（默认 _export/entity_alias_unmatched_*.json）")
    args = parser.parse_args()
    if args.limit < 0 or args.batch_size <= 0 or args.sample < 0:
        parser.error("limit/sample 不能为负数，batch-size 必须大于 0")
    stats = run(args)
    print("完成：" + ", ".join(f"{key}={value}" for key, value in stats.items()))


if __name__ == "__main__":
    main()
