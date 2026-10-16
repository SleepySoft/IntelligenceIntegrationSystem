# Scripts/entity_alias_backfill.py
# 历史实体扫描与别名库补录工具。
# 本脚本管理 EntityAlias JSONL 事实库，不回写 MongoDB 历史归档；
# 归档内实体名规范化请用 Scripts/entity_alias_archive_backfill.py。
#
# 用法：
#   python Scripts/entity_alias_backfill.py scan [--min-count 2] [--limit 500] [--out _export/entity_alias_worklist.json]
#       扫描默认子系统归档：优先遍历 EVENT_EXTRACTION.entities 的全部实体类型；
#       仅当旧文档缺少抽取结构时回退 PEOPLE/LOCATION/ORGANIZATION 三个速查字段，
#       两者不重复计数。统计频次后批量查询 EntityAlias，可选查 Wikidata；
#       唯一精确匹配自动补录，其余输出工作清单（含候选）供 AI 填写。
#   python Scripts/entity_alias_backfill.py register <filled_worklist.json> [--data-dir EntityAlias/data]
#       读取 AI 填写后的工作清单并执行注册/加别名/忽略。
#
# 工作清单格式（scan 产出，AI 填写 action 部分）：
#   [{"name": "...", "count": 12, "type": "organization",
#     "candidates": [...],                # lookup 候选，供 AI 判断
#     "action": {                          # AI 填写其一：
#       "op": "register",                 #   注册新实体（附 canonical/lang/type/aliases/description）
#       "op": "wikidata",                 #   按已确认 qid 拉取多语言资料并注册
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
from EntityAlias.normalize import normalize_name  # noqa: E402

logger = logging.getLogger("entity_alias_backfill")

# 旧文档（缺少 EVENT_EXTRACTION 抽取结构）的回退速查字段
ENTITY_FIELDS = ("PEOPLE", "LOCATION", "ORGANIZATION")


def count_document_entities(doc: dict, counters: dict) -> str:
    """统计单篇归档的实体名，返回计数来源（"extraction" / "fallback" / "none"）。

    优先使用 EVENT_EXTRACTION.entities（覆盖全部实体类型）；仅当文档缺少
    抽取结构时回退三个速查字段，避免同一实体被重复计数。
    """
    extraction = doc.get("EVENT_EXTRACTION")
    entities = extraction.get("entities") if isinstance(extraction, dict) else None
    if isinstance(entities, list):
        for entity in entities:
            if not isinstance(entity, dict):
                continue
            name = str(entity.get("name") or "").strip()
            etype = str(entity.get("type") or "").strip() or "other"
            if name:
                counters.setdefault(etype, Counter())[name] += 1
        return "extraction"
    counted = False
    for field in ENTITY_FIELDS:
        for value in doc.get(field) or []:
            if isinstance(value, str) and value.strip():
                counters.setdefault(_field_to_type(field), Counter())[value.strip()] += 1
                counted = True
    return "fallback" if counted else "none"

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
        from GlobalConfig import APPLIED_PROXY
        from EntityAlias.wikidata import WikidataSuggester, proxy_uses_socks
        proxy = APPLIED_PROXY or ""
        if proxy_uses_socks(proxy):
            try:
                import socks  # noqa: F401
            except ImportError:
                logger.warning("PySocks 未安装，Wikidata 查询将直连。")
                proxy = ""
        suggester = WikidataSuggester(
            proxy=proxy,
            cache_path=os.path.join(data_dir, ".cache", "wikidata_cache.sqlite"))
    return EntityAliasService(JSONLStore(data_dir), suggester=suggester)


def _select_exact_wikidata(name, candidates):
    """Wikidata 只有一个 label/matched 与输入精确同名时才自动选中。"""
    key = normalize_name(name)
    exact = [item for item in candidates
             if key and any(normalize_name(item.get(field, "")) == key
                            for field in ("matched", "label"))]
    return exact[0] if len(exact) == 1 and exact[0].get("qid") else None


def _find_by_wikidata_qid(service, qid):
    for entity in service.store.all():
        if entity.refs.get("wikidata") == qid:
            return entity.to_dict()
    return None


def _wikidata_type(item, candidate):
    return item.get("type") or _field_to_type(item.get("field"))


def _register_wikidata_item(service, item, qid, candidate=None, overrides=None):
    """QID 直接补录；同一 QID 已存在时只补当前名称为别名。"""
    options = dict(overrides or {})
    primary_lang = options.pop("primary_lang", "zh")
    name_lang = options.pop("name_lang", "")
    entity = _find_by_wikidata_qid(service, qid)
    if entity is None:
        fields = {"type": _wikidata_type(item, candidate or {})}
        fields.update(options)
        entity = service.register_from_wikidata(
            qid, primary_lang=primary_lang, overrides=fields)
    name = item["name"]
    if service.resolve(name) is None:
        entity = service.add_alias(entity["id"], name, name_lang)
    return entity


def cmd_scan(args):
    config = _load_config()
    collection = _open_default_collection(config)
    service = _open_alias_service(args.data_dir, with_suggester=args.enrich)

    counters: dict = {}
    cursor = collection.find(
        {}, {"_id": 0, "EVENT_EXTRACTION.entities": 1,
             **{f: 1 for f in ENTITY_FIELDS}})
    scanned = 0
    source_stats = Counter()
    for doc in cursor:
        scanned += 1
        source_stats[count_document_entities(doc, counters)] += 1
    logger.info("扫描归档 %d 篇（extraction=%d, fallback=%d, none=%d）",
                scanned, source_stats["extraction"],
                source_stats["fallback"], source_stats["none"])

    worklist = []
    auto_registered = 0
    for entity_type in sorted(counters):
        for name, count in counters[entity_type].most_common():
            if count < args.min_count:
                continue
            if service.resolve(name):
                continue  # 已注册
            lookup = service.lookup(name, limit=3)
            item = {
                "name": name,
                "count": count,
                "type": entity_type,
                "candidates": lookup["candidates"],
                "action": None,
            }
            if args.enrich and service.suggester is not None:
                # 在线补全 Wikidata 候选（带节流）。唯一精确同名可直接补录；
                # 模糊或多义候选继续交给 AI 判断。
                item["wikidata"] = service.suggest(name, online=True)["wikidata"]
                exact = _select_exact_wikidata(name, item["wikidata"])
                if exact is not None and args.auto_register_exact:
                    try:
                        entity = _register_wikidata_item(service, item, exact["qid"], exact)
                        item["action"] = {
                            "op": "wikidata", "qid": exact["qid"],
                            "auto_applied": True,
                        }
                        item["registered_entity_id"] = entity["id"]
                        auto_registered += 1
                    except Exception as exc:
                        item["auto_register_error"] = str(exc)
                        logger.warning("Wikidata 自动补录失败 %s: %s", name, exc)
            worklist.append(item)
            if len(worklist) >= args.limit:
                break

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(worklist, f, ensure_ascii=False, indent=2)
    logger.info("未注册实体 %d 个，Wikidata 自动补录 %d 个，工作清单已写入 %s",
                len(worklist), auto_registered, args.out)
    print(f"扫描 {scanned} 篇归档，未注册实体 {len(worklist)} 个，"
          f"Wikidata 自动补录 {auto_registered} 个 -> {args.out}")


def cmd_register(args):
    service = _open_alias_service(args.data_dir, with_suggester=True)
    with open(args.worklist, "r", encoding="utf-8") as f:
        worklist = json.load(f)

    stats = {"registered": 0, "wikidata": 0, "aliased": 0,
             "ignored": 0, "failed": 0}
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
                    type=action.get("type") or item.get("type")
                         or _field_to_type(item.get("field")),
                    aliases=action.get("aliases") or [{"name": name, "lang": action.get("name_lang", "")}],
                    description=action.get("description", ""),
                )
                stats["registered"] += 1
            elif op == "wikidata":
                _register_wikidata_item(
                    service, item, action["qid"],
                    overrides={key: action[key] for key in
                               ("type", "primary_lang", "name_lang") if key in action},
                )
                stats["wikidata"] += 1
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
    parser = argparse.ArgumentParser(description="历史实体扫描与 EntityAlias 别名库补录")
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan", help="扫描归档，产出未注册实体工作清单")
    p_scan.add_argument("--min-count", type=int, default=2)
    p_scan.add_argument("--limit", type=int, default=500)
    p_scan.add_argument("--out", default=DEFAULT_WORKLIST)
    p_scan.add_argument("--data-dir", default=os.path.join("EntityAlias", "data"))
    p_scan.add_argument("--enrich", action="store_true",
                        help="在线查询 Wikidata，唯一精确同名自动补录（慢，带节流）")
    p_scan.add_argument("--no-auto-register-exact", action="store_false",
                        dest="auto_register_exact",
                        help="只预填 Wikidata 候选，不自动补录精确同名")

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
