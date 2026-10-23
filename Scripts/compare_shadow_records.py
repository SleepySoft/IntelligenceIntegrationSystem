"""影子分析记录与生产归档的配对对比工具。

扫描 shadow 落盘目录（默认 _data/analysis_shadow/，每个样本一个 JSON），
按 source_uuid 与 Mongo 归档配对，并排输出主链 vs 影子的版本、模型、
抽取质量指标与事件结构摘要，末尾给出聚合统计。只读，不写任何业务数据。

用法：
    python Scripts/compare_shadow_records.py                 # 全部配对 + 聚合
    python Scripts/compare_shadow_records.py --limit 10      # 只看最近 10 对
    python Scripts/compare_shadow_records.py --uuid bf37a558 # 只看某条情报
    python Scripts/compare_shadow_records.py --events        # 附带事件结构明细
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymongo

DEFAULT_SHADOW_DIR = "_data/analysis_shadow"


def load_shadow_records(shadow_dir: Path) -> list[dict]:
    records = []
    for file in sorted(shadow_dir.glob("*.json"),
                       key=lambda item: item.stat().st_mtime):
        try:
            payload = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[skip] {file.name}: {exc}")
            continue
        payload["_file"] = file.name
        records.append(payload)
    return records


def fetch_archived(collect, uuids: list[str]) -> dict[str, dict]:
    docs = {}
    cursor = collect.find(
        {"UUID": {"$in": uuids}},
        {"UUID": 1, "EVENT_TITLE": 1, "EVENT_EXTRACTION": 1,
         "APPENDIX.__PROMPT_VERSION__": 1, "APPENDIX.__AI_MODEL__": 1,
         "APPENDIX.__TOTAL_SCORE__": 1, "APPENDIX.__EXTRACTION_QUALITY__": 1},
    )
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
    parser.add_argument("--dir", default=DEFAULT_SHADOW_DIR, help="影子记录目录")
    parser.add_argument("--limit", type=int, default=0, help="只显示最近 N 对（0=全部）")
    parser.add_argument("--uuid", default="", help="只看 source_uuid 含该子串的记录")
    parser.add_argument("--events", action="store_true", help="附带事件结构明细")
    args = parser.parse_args()

    shadow_dir = Path(args.dir)
    if not shadow_dir.is_dir():
        print(f"影子目录不存在: {shadow_dir}（尚未产生影子记录或路径不对）")
        return
    records = load_shadow_records(shadow_dir)
    if args.uuid:
        records = [r for r in records if args.uuid in str(r.get("source_uuid", ""))]
    if args.limit > 0:
        records = records[-args.limit:]
    if not records:
        print("没有匹配的影子记录。")
        return

    mongo = pymongo.MongoClient("mongodb://localhost:27017")["IntelligenceIntegrationSystem"]
    archived = fetch_archived(
        mongo["intelligence_v4_archived"],
        [str(r.get("source_uuid", "")) for r in records],
    )

    better, worse, tie, missing = 0, 0, 0, 0
    for index, record in enumerate(records, 1):
        uuid = str(record.get("source_uuid", ""))
        doc = archived.get(uuid)
        print("=" * 78)
        print(f"[{index}/{len(records)}] {uuid}  ({record.get('_file')})")
        print(f"  标题: {(doc or {}).get('EVENT_TITLE') or '(归档未找到)'}")
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
    print(f"质量对比（未登记角色+角色类型冲突，影子 vs 主链）: "
          f"更优 {better} / 持平 {tie} / 更差 {worse}")


if __name__ == "__main__":
    main()
