"""影子分析记录与生产归档的配对对比工具。

从 MongoDB `analysis_shadow` 查询指定领域，按 source_uuid 与正式归档配对，
并排输出主链 vs 影子的版本、模型、
抽取质量指标与事件结构摘要，末尾给出聚合统计。只读，不写任何业务数据。

用法：
    python Scripts/compare_shadow_records.py                 # 全部配对 + 聚合
    python Scripts/compare_shadow_records.py --limit 10      # 只看最近 10 对
    python Scripts/compare_shadow_records.py --uuid bf37a558 # 只看某条情报
    python Scripts/compare_shadow_records.py --events        # 附带事件结构明细
    python Scripts/compare_shadow_records.py --primary-version 4 --shadow-version 5 --same-model-only
"""

from __future__ import annotations

import argparse
import json
import pymongo


def fetch_primary(database, prefix: str, uuids: list[str]) -> dict[str, dict]:
    """同时读取正式收录与低价值集合，避免把 NonIntelligence 误报为未归档。"""

    docs = {}
    projection = {
        "UUID": 1, "TAXONOMY": 1, "REASON": 1, "EVENT_TITLE": 1,
        "EVENT_EXTRACTION": 1, "APPENDIX.RAW_DATA.title": 1,
        "APPENDIX.__PROMPT_VERSION__": 1, "APPENDIX.__AI_MODEL__": 1,
        "APPENDIX.__TOTAL_SCORE__": 1, "APPENDIX.__EXTRACTION_QUALITY__": 1,
    }
    for suffix in ("v4_archived", "v4_low_value"):
        cursor = database[f"{prefix}{suffix}"].find(
            {"UUID": {"$in": uuids}}, projection)
        for doc in cursor:
            docs[doc["UUID"]] = doc
    return docs


def quality_line(quality: dict | None) -> str:
    if not quality:
        return "无质量统计"
    grounding = quality.get("surface_grounding") or {}
    pred = grounding.get("predicate") or {}
    return (f"未登记角色={quality.get('unregistered_role_count', 0)} "
            f"角色类型冲突={quality.get('role_type_conflict_count', 0)} "
            f"谓词接地={pred.get('hit', 0)}/{pred.get('total', 0)}")


def event_summary(extraction: dict | None) -> list[str]:
    if not extraction:
        return ["无 EVENT_EXTRACTION"]
    entities = {e.get("id"): e for e in extraction.get("entities") or []}
    lines = []
    for event in extraction.get("events") or []:
        core = event.get("core") or {}
        predicate = core.get("predicate") or {}
        roles = {
            role: [f"{(entities.get(eid) or {}).get('name')}"
                   f"({(entities.get(eid) or {}).get('type')})" for eid in ids]
            for role, ids in (core.get("roles") or {}).items()
        }
        primary = "*" if event.get("id") == extraction.get("primary_event_id") else " "
        lines.append(
            f"  {primary}{event.get('id')} {predicate.get('id')}"
            f"|{predicate.get('surface')} roles={json.dumps(roles, ensure_ascii=False)}")
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domain", default="industry", help="情报领域")
    parser.add_argument("--database", default="IntelligenceIntegrationSystem")
    parser.add_argument("--collection", default="analysis_shadow")
    parser.add_argument("--limit", type=int, default=0, help="只显示最近 N 对（0=全部）")
    parser.add_argument("--uuid", default="", help="只看 source_uuid 含该子串的记录")
    parser.add_argument("--primary-version", type=int, help="只比较指定主链 Prompt 版本")
    parser.add_argument("--shadow-version", type=int, help="只比较指定影子 Prompt 版本")
    parser.add_argument(
        "--same-model-only", action="store_true",
        help="只保留主链与影子最终模型一致的单变量样本",
    )
    parser.add_argument("--events", action="store_true", help="附带事件结构明细")
    args = parser.parse_args()

    mongo = pymongo.MongoClient("mongodb://localhost:27017")[args.database]
    query = {"domain_name": args.domain}
    if args.uuid:
        query["source_uuid"] = {"$regex": args.uuid}
    if args.primary_version is not None:
        query["primary_prompt_version"] = args.primary_version
    if args.shadow_version is not None:
        query["shadow_prompt_version"] = args.shadow_version
    cursor = mongo[args.collection].find(query).sort("captured_at", -1)
    records = list(cursor)
    if not records:
        print("没有匹配的影子记录。")
        return

    prefix = "intelligence_" if args.domain == "news" else f"{args.domain}_intelligence_"
    archived = fetch_primary(
        mongo, prefix, [str(r.get("source_uuid", "")) for r in records])
    excluded_model = 0
    if args.same_model_only:
        filtered = []
        for record in records:
            doc = archived.get(str(record.get("source_uuid", ""))) or {}
            primary_model = (doc.get("APPENDIX") or {}).get("__AI_MODEL__")
            if primary_model and primary_model == record.get("ai_model"):
                filtered.append(record)
            else:
                excluded_model += 1
        records = filtered
    if args.limit > 0:
        records = records[:args.limit]
    if not records:
        print("没有满足版本与模型条件的影子记录。")
        return

    better, worse, tie, missing = 0, 0, 0, 0
    for index, record in enumerate(records, 1):
        uuid = str(record.get("source_uuid", ""))
        doc = archived.get(uuid)
        print("=" * 78)
        print(f"[{index}/{len(records)}] {uuid}  ({record.get('_id')})")
        appendix = (doc or {}).get("APPENDIX") or {}
        raw_data = appendix.get("RAW_DATA") or {}
        print(f"  标题: {(doc or {}).get('EVENT_TITLE') or raw_data.get('title') or '(归档未找到)'}")
        print(f"  主链: v{record.get('primary_prompt_version')} | "
              f"影子: v{record.get('shadow_prompt_version')} "
              f"({record.get('ai_model')})")
        if doc is None:
            missing += 1
            continue
        primary_quality = ((doc.get("APPENDIX") or {}).get("__EXTRACTION_QUALITY__"))
        shadow_quality = record.get("extraction_quality")
        print(f"  主链质量: {quality_line(primary_quality)}")
        print(f"  影子质量: {quality_line(shadow_quality)}")
        if args.events:
            print("  -- 主链事件 --")
            for line in event_summary(doc.get("EVENT_EXTRACTION")):
                print("   " + line)
            print("  -- 影子事件 --")
            for line in event_summary((record.get("analysis") or {}).get("EVENT_EXTRACTION")):
                print("   " + line)
        if primary_quality and shadow_quality:
            primary_bad = (primary_quality.get("unregistered_role_count", 0)
                           + primary_quality.get("role_type_conflict_count", 0))
            shadow_bad = (shadow_quality.get("unregistered_role_count", 0)
                          + shadow_quality.get("role_type_conflict_count", 0))
            if shadow_bad < primary_bad:
                better += 1
            elif shadow_bad > primary_bad:
                worse += 1
            else:
                tie += 1

    print("=" * 78)
    print(f"共 {len(records)} 条影子记录；配对到归档 {len(records) - missing} 条，"
          f"未找到归档 {missing} 条")
    if args.same_model_only:
        print(f"因最终模型不同排除 {excluded_model} 条")
    print(f"质量对比（未登记角色+角色类型冲突，影子 vs 主链）: "
          f"更优 {better} / 持平 {tie} / 更差 {worse}")


if __name__ == "__main__":
    main()
