# Scripts/entity_alias_backfill.py
# 历史数据实体别名补录工具。
#
# 用法：
#   python Scripts/entity_alias_backfill.py scan [--min-count 2] [--limit 500] [--out _export/entity_alias_worklist.json]
#       扫描默认子系统归档的 PEOPLE/LOCATION/ORGANIZATION 字段，统计频次，
#       批量查询 EntityAlias，输出未注册实体工作清单（含候选）供 AI 填写。
#   python Scripts/entity_alias_backfill.py register <filled_worklist.json> [--data-dir EntityAlias/data]
#       读取 AI 填写后的工作清单并执行注册/加别名/忽略。
#
# 工作清单格式（scan 产出，AI 填写 action 部分）：
#   [{"name": "...", "count": 12, "field": "ORGANIZATION",
#     "candidates": [...],                # lookup 候选，供 AI 判断
#     "action": {                          # AI 填写其一：
#       "op": "register",                 #   注册新实体（附 canonical/lang/type/aliases/description）
#       "op": "alias",                    #   作为已有实体的别名（附 entity_id，可选 lang/role）
#       "op": "ignore"                    #   忽略
#     }}]

import argparse
import json
import logging
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from GlobalConfig import DEFAULT_ALTER_CONFIG_FILE, DEFAULT_CONFIG_FILE  # noqa: E402
from IISConfig import load_iis_config  # noqa: E402

logger = logging.getLogger("entity_alias_backfill")

ENTITY_FIELDS = ("PEOPLE", "LOCATION", "ORGANIZATION")

DEFAULT_WORKLIST = os.path.join("_export", "entity_alias_worklist.json")


def _load_config():
    return load_iis_config(DEFAULT_CONFIG_FILE, fallback_file=DEFAULT_ALTER_CONFIG_FILE)


def _open_default_collection(config):
    from ServiceComponent.SubsystemRegistry import SubsystemRegistry
    registry = SubsystemRegistry.build_from_config(
        config,
        mongodb_params={
            'host': config.get('mongodb.host', 'localhost'),
            'port': config.get('mongodb.port', 27017),
            'username': config.get('mongodb.user', ''),
            'password': config.get('mongodb.password', ''),
        },
        db_name='IntelligenceIntegrationSystem',
    )
    return registry.default().event_v4_intelligence_collection


def _open_alias_service(data_dir: str, with_suggester: bool = False):
    from EntityAlias import EntityAliasService, JSONLStore
    suggester = None
    if with_suggester:
        from GlobalConfig import DEFAULT_PROXY
        from EntityAlias.wikidata import WikidataSuggester
        proxy = DEFAULT_PROXY or ""
        if proxy.startswith("socks"):
            try:
                import socks  # noqa: F401
            except ImportError:
                logger.warning("PySocks 未安装，Wikidata 查询将直连。")
                proxy = ""
        suggester = WikidataSuggester(
            proxy=proxy,
            cache_path=os.path.join(data_dir, ".cache", "wikidata_cache.sqlite"))
    return EntityAliasService(JSONLStore(data_dir), suggester=suggester)


def cmd_scan(args):
    config = _load_config()
    collection = _open_default_collection(config)
    service = _open_alias_service(args.data_dir, with_suggester=args.enrich)

    counters = {field: Counter() for field in ENTITY_FIELDS}
    cursor = collection.find({}, {"_id": 0, **{f: 1 for f in ENTITY_FIELDS}})
    scanned = 0
    for doc in cursor:
        scanned += 1
        for field in ENTITY_FIELDS:
            for value in doc.get(field) or []:
                if isinstance(value, str) and value.strip():
                    counters[field][value.strip()] += 1
    logger.info("扫描归档 %d 篇", scanned)

    worklist = []
    for field in ENTITY_FIELDS:
        for name, count in counters[field].most_common():
            if count < args.min_count:
                continue
            if service.resolve(name):
                continue  # 已注册
            lookup = service.lookup(name, limit=3)
            item = {
                "name": name,
                "count": count,
                "field": field,
                "candidates": lookup["candidates"],
                "action": None,
            }
            if args.enrich and service.suggester is not None:
                # 在线补全 Wikidata 候选（带节流），AI 审阅时无需再发请求
                item["wikidata"] = service.suggest(name, online=True)["wikidata"]
            worklist.append(item)
            if len(worklist) >= args.limit:
                break

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(worklist, f, ensure_ascii=False, indent=2)
    logger.info("未注册实体 %d 个，工作清单已写入 %s", len(worklist), args.out)
    print(f"扫描 {scanned} 篇归档，未注册实体 {len(worklist)} 个 -> {args.out}")


def cmd_register(args):
    service = _open_alias_service(args.data_dir)
    with open(args.worklist, "r", encoding="utf-8") as f:
        worklist = json.load(f)

    stats = {"registered": 0, "aliased": 0, "ignored": 0, "failed": 0}
    for item in worklist:
        action = item.get("action") or {}
        op = action.get("op")
        name = item["name"]
        try:
            if op == "register":
                if service.resolve(action.get("canonical", "")):
                    stats["ignored"] += 1
                    continue
                service.create_entity(
                    action["canonical"],
                    lang=action.get("lang", "zh"),
                    type=action.get("type") or _field_to_type(item.get("field")),
                    aliases=action.get("aliases") or [{"name": name, "lang": action.get("name_lang", "")}],
                    description=action.get("description", ""),
                )
                stats["registered"] += 1
            elif op == "alias":
                service.add_alias(action["entity_id"], name,
                                  action.get("lang", ""))
                stats["aliased"] += 1
            elif op == "ignore":
                stats["ignored"] += 1
            else:
                logger.warning("跳过无 action 的条目：%s", name)
                stats["ignored"] += 1
        except Exception as exc:
            logger.error("处理失败 %s: %s", name, exc)
            stats["failed"] += 1
    print(f"完成：{stats}")


def _field_to_type(field):
    return {"PEOPLE": "person", "LOCATION": "location",
            "ORGANIZATION": "organization"}.get(field or "", "other")


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="历史数据实体别名补录")
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan", help="扫描归档，产出未注册实体工作清单")
    p_scan.add_argument("--min-count", type=int, default=2)
    p_scan.add_argument("--limit", type=int, default=500)
    p_scan.add_argument("--out", default=DEFAULT_WORKLIST)
    p_scan.add_argument("--data-dir", default=os.path.join("EntityAlias", "data"))
    p_scan.add_argument("--enrich", action="store_true",
                        help="在线查询 Wikidata 为每个未注册实体预填候选（慢，带节流）")

    p_reg = sub.add_parser("register", help="按 AI 填写的工作清单注册")
    p_reg.add_argument("worklist")
    p_reg.add_argument("--data-dir", default=os.path.join("EntityAlias", "data"))

    args = parser.parse_args()
    if args.command == "scan":
        cmd_scan(args)
    else:
        cmd_register(args)


if __name__ == "__main__":
    main()
