import pymongo
import json
import re
from datetime import datetime

# --- 常量定义 (基于你的提供) ---
DB_NAME = "IntelligenceIntegrationSystem"
COL_CACHED = "intelligence_cached"
COL_ARCHIVED = "intelligence_archived"

APPENDIX_ARCHIVED_FLAG = '__ARCHIVED__'
ARCHIVED_FLAG_DROP = 'D'
ARCHIVED_FLAG_ARCHIVED = 'A'
APPENDIX_TIME_ARCHIVED = '__TIME_ARCHIVED__'
APPENDIX_MAX_RATE_SCORE = '__MAX_RATE_SCORE__'


# --- 工具函数 ---

def is_valid_url(url):
    """简单判断是否为有效URL"""
    if not isinstance(url, str):
        return False
    # 简单的正则，匹配 http/https 开头
    regex = re.compile(
        r'^(?:http|ftp)s?://'  # http:// or https://
        r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+(?:[A-Z]{2,6}\.?|[A-Z0-9-]{2,}\.?)|'  # domain...
        r'localhost|'  # localhost...
        r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'  # ...or ip
        r'(?::\d+)?'  # optional port
        r'(?:/?|[/?]\S+)$', re.IGNORECASE)
    return re.match(regex, url) is not None


def contains_chinese(text):
    """简单判断文本是否包含中文字符 (只要包含一定比例即可，这里简化为只要包含一段中文)"""
    if not isinstance(text, str):
        return False
    # 匹配中文字符范围
    return re.search(r'[\u4e00-\u9fa5]', text) is not None


def json_serial(obj):
    """JSON序列化辅助函数，处理datetime等对象"""
    if isinstance(obj, (datetime, float)):  # float 包含了 time.struct_time 的某些时间戳情况
        return str(obj)
    raise TypeError(f"Type {type(obj)} not serializable")


def main():
    print(">>> 开始第一步：数据抽取与基本清洗...")

    # 1. 连接数据库
    client = pymongo.MongoClient("mongodb://localhost:27017/")  # 请根据需要修改地址
    db = client[DB_NAME]
    col_cached = db[COL_CACHED]
    col_archived = db[COL_ARCHIVED]

    # --- 处理 1: 丢弃的数据 (Dropped Data) ---
    print("\n[1/3] 正在处理丢弃数据 (Dropped Data)...")

    dropped_data_list = []
    dropped_stats = {
        "total_scanned": 0,
        "kept": 0,
        "removed_duplicate": 0,
        "removed_invalid_url": 0
    }

    seen_dropped_uuids = set()

    # 查询条件: APPENDIX.__ARCHIVED__ == 'D'
    # 注意：MongoDB 中嵌套字段查询使用点符号，但返回的 dict 中 APPENDIX 是一个子字典
    cursor_dropped = col_cached.find({f"APPENDIX.{APPENDIX_ARCHIVED_FLAG}": ARCHIVED_FLAG_DROP})

    for item in cursor_dropped:
        dropped_stats["total_scanned"] += 1

        uuid = item.get("UUID")
        informant = item.get("informant")
        pub_time = item.get("pub_time")

        # 检查 UUID 重复
        if uuid in seen_dropped_uuids:
            dropped_stats["removed_duplicate"] += 1
            continue

        # 检查 Informant 是否为 URL
        if not is_valid_url(informant):
            dropped_stats["removed_invalid_url"] += 1
            continue

        seen_dropped_uuids.add(uuid)

        # 记录摘要
        dropped_data_list.append({
            "UUID": uuid,
            "pub_time": pub_time,
            "informant": informant
        })
        dropped_stats["kept"] += 1

    # 保存丢弃数据摘要
    with open("summary_dropped.json", "w", encoding="utf-8") as f:
        json.dump(dropped_data_list, f, ensure_ascii=False, indent=4, default=json_serial)

    print(f"    - 扫描总数: {dropped_stats['total_scanned']}")
    print(f"    - 移除重复: {dropped_stats['removed_duplicate']}")
    print(f"    - 移除无效URL: {dropped_stats['removed_invalid_url']}")
    print(f"    - 最终保留: {dropped_stats['kept']}")
    print(f"    - 结果已保存至: summary_dropped.json")

    # --- 处理 2: 准备交叉验证数据 ---
    print("\n[2/3] 正在准备交叉验证数据 (Cached Valid UUIDs)...")
    # 为了验证 archived 数据，我们需要先找出 cached 中标记为 'A' 的所有 UUID
    # 使用 set 进行 O(1) 查找
    valid_cached_uuids = set()
    cursor_valid_cached = col_cached.find(
        {f"APPENDIX.{APPENDIX_ARCHIVED_FLAG}": ARCHIVED_FLAG_ARCHIVED},
        {"UUID": 1}  # 只取 UUID 字段以节省内存
    )
    for item in cursor_valid_cached:
        if "UUID" in item:
            valid_cached_uuids.add(item["UUID"])

    print(f"    - 也就是 Intelligence_Cached 中标记为 'A' 的数据量: {len(valid_cached_uuids)}")

    # --- 处理 3: 归档的数据 (Archived Data) ---
    print("\n[3/3] 正在处理归档数据 (Archived Data)...")

    archived_data_list = []
    archived_stats = {
        "total_scanned": 0,
        "kept": 0,
        "removed_duplicate": 0,
        "removed_cross_check_fail": 0,  # UUID 不在 cached 中或状态不对
        "removed_invalid_url": 0,
        "removed_non_chinese": 0
    }

    seen_archived_uuids = set()

    cursor_archived = col_archived.find({})  # 扫描全部

    for item in cursor_archived:
        archived_stats["total_scanned"] += 1

        uuid = item.get("UUID")
        informant = item.get("INFORMANT")
        appendix = item.get("APPENDIX", {})

        # 提取需要的字段
        time_archived = appendix.get(APPENDIX_TIME_ARCHIVED)
        max_rate_score = appendix.get(APPENDIX_MAX_RATE_SCORE)

        # 1. 去重 (UUID 及 INFORMANT)
        # 这里简单起见，主要以 UUID 为主键去重
        if uuid in seen_archived_uuids:
            archived_stats["removed_duplicate"] += 1
            continue

        # 2. 交叉验证: UUID 必须在 cached 中存在且状态为 'A'
        if uuid not in valid_cached_uuids:
            archived_stats["removed_cross_check_fail"] += 1
            continue

        # 3. INFORMANT 必须是 URL
        if not is_valid_url(informant):
            archived_stats["removed_invalid_url"] += 1
            continue

        # 4. 中文内容检查
        # 检查 EVENT_TITLE, EVENT_BRIEF, EVENT_TEXT
        e_title = item.get("EVENT_TITLE", "")
        e_brief = item.get("EVENT_BRIEF", "")
        e_text = item.get("EVENT_TEXT", "")

        # 简单判定：只要这三个字段中任意一个包含中文，或者主体（TEXT）包含中文。
        # 这里采用严格一点的策略：Title 或 Brief 必须包含中文，或者 Text 包含中文。
        # 也可以拼接起来检查。
        full_content = f"{e_title or ''} {e_brief or ''} {e_text or ''}"
        if not contains_chinese(full_content):
            archived_stats["removed_non_chinese"] += 1
            continue

        # 通过所有检查
        seen_archived_uuids.add(uuid)

        archived_data_list.append({
            "UUID": uuid,
            "INFORMANT": informant,
            "APPENDIX_TIME_ARCHIVED": time_archived,
            "APPENDIX_MAX_RATE_SCORE": max_rate_score
        })
        archived_stats["kept"] += 1

    # 保存归档数据摘要
    with open("summary_archived.json", "w", encoding="utf-8") as f:
        json.dump(archived_data_list, f, ensure_ascii=False, indent=4, default=json_serial)

    print(f"    - 扫描总数: {archived_stats['total_scanned']}")
    print(f"    - 移除重复: {archived_stats['removed_duplicate']}")
    print(f"    - 移除交叉验证失败(Cached中不存在或状态非A): {archived_stats['removed_cross_check_fail']}")
    print(f"    - 移除无效URL: {archived_stats['removed_invalid_url']}")
    print(f"    - 移除非中文内容: {archived_stats['removed_non_chinese']}")
    print(f"    - 最终保留: {archived_stats['kept']}")
    print(f"    - 结果已保存至: summary_archived.json")

    print("\n>>> 第一步完成。请检查生成的 summary_dropped.json 和 summary_archived.json。")


if __name__ == "__main__":
    main()
