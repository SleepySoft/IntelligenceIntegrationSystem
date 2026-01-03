import pymongo
from pymongo import MongoClient
import sys
from pprint import pprint

# ================= 配置区域 =================

MONGO_URI = "mongodb://localhost:27017/"
DB_NAME = "IntelligenceIntegrationSystem"
COLLECTION_NAME = "intelligence_archived"

# 注意: 确保字段名在数据库中完全一致 (大小写敏感)
DUPLICATE_KEY = "INFORMANT"

# 批量删除的大小（防止一次请求过大导致超时）
BATCH_SIZE = 1000


# ===========================================

class BatchDuplicateCleaner:
    def __init__(self):
        try:
            self.client = MongoClient(MONGO_URI)
            self.db = self.client[DB_NAME]
            self.collection = self.db[COLLECTION_NAME]
            print(f"[-] Connected to {DB_NAME}.{COLLECTION_NAME}")
        except Exception as e:
            print(f"[!] Connection failed: {e}")
            sys.exit(1)

    def scan_duplicates(self):
        print(f"[-] Scanning for duplicates on key: '{DUPLICATE_KEY}'...")
        print("[-] This may take a while depending on data size...")

        pipeline = [
            # 1. 过滤空字段
            {"$match": {DUPLICATE_KEY: {"$exists": True, "$ne": None, "$ne": ""}}},

            # 2. 按 Key 分组，收集所有 _id
            {"$group": {
                "_id": f"${DUPLICATE_KEY}",
                "all_ids": {"$push": "$_id"},
                "count": {"$sum": 1}
            }},

            # 3. 筛选重复项
            {"$match": {"count": {"$gt": 1}}},

            # 4. (可选) 稍微限制一下返回的文档大小，防止单组过大报错
            # 如果你有单条 URL 重复上万次的情况，可能需要优化这里
        ]

        # allowDiskUse=True 允许 MongoDB 使用临时文件进行排序/分组
        return list(self.collection.aggregate(pipeline, allowDiskUse=True))

    def generate_plan(self, duplicates, keep_strategy):
        """
        根据策略生成待删除的 ID 列表
        keep_strategy: 'newest' | 'oldest'
        """
        ids_to_delete = []

        print("[-] Analyzing IDs to generate deletion plan...")

        for group in duplicates:
            # MongoDB 的 ObjectId 是有序的，包含时间戳信息。直接排序即可。
            # sorted 默认升序：最旧的在 [0]，最新的在 [-1]
            ids = sorted(group['all_ids'])

            if keep_strategy == 'newest':
                # 保留最后一个（最新的），删除前面所有的
                ids_to_delete.extend(ids[:-1])
            elif keep_strategy == 'oldest':
                # 保留第一个（最旧的），删除后面所有的
                ids_to_delete.extend(ids[1:])

        return ids_to_delete

    def run(self):
        # 1. 扫描
        duplicates = self.scan_duplicates()
        total_groups = len(duplicates)

        if total_groups == 0:
            print("[√] No duplicates found. System is clean.")
            return

        total_records = sum(group['count'] for group in duplicates)
        redundant_count = total_records - total_groups  # 总记录 - 组数 = 多余的记录数

        # 2. 报告
        print("\n" + "=" * 50)
        print(f"DATABASE DUPLICATE REPORT")
        print("=" * 50)
        print(f"Target Field       : {DUPLICATE_KEY}")
        print(f"Duplicate Groups   : {total_groups}")
        print(f"Total Involved Docs: {total_records}")
        print(f"Redundant Docs     : {redundant_count} (Will be deleted)")
        print("=" * 50 + "\n")

        # 3. 用户选择策略
        print("Choose a cleanup strategy:")
        print(" [1] Keep NEWEST (Retain the last inserted record)")
        print(" [2] Keep OLDEST (Retain the first inserted record)")
        print(" [0] Cancel / Exit")

        choice = input("\nEnter choice [0-2]: ").strip()

        strategy = None
        if choice == '1':
            strategy = 'newest'
        elif choice == '2':
            strategy = 'oldest'
        else:
            print("[-] Operation cancelled.")
            return

        # 4. 生成执行计划
        ids_to_delete = self.generate_plan(duplicates, strategy)

        if not ids_to_delete:
            print("[-] No IDs found to delete.")
            return

        print(f"\n[!] WARNING: You are about to DELETE {len(ids_to_delete)} documents.")
        confirm = input(f"[?] Type 'yes' to confirm deletion: ")

        if confirm.lower() != 'yes':
            print("[-] Operation aborted.")
            return

        # 5. 批量执行
        self.execute_batch_delete(ids_to_delete)

        # 6. 建议
        self.suggest_index()

    def execute_batch_delete(self, all_ids):
        print(f"[-] Starting batch deletion in chunks of {BATCH_SIZE}...")
        total = len(all_ids)
        deleted = 0

        # 分块处理，避免单次请求过大
        for i in range(0, total, BATCH_SIZE):
            chunk = all_ids[i: i + BATCH_SIZE]

            result = self.collection.delete_many({"_id": {"$in": chunk}})
            deleted += result.deleted_count

            # 简单的进度条
            progress = min(i + BATCH_SIZE, total)
            print(f"    Progress: {progress}/{total} deleted...", end='\r')

        print(f"\n[√] Done. Successfully deleted {deleted} documents.")

    def suggest_index(self):
        print("\n" + "-" * 50)
        print("RECOMMENDATION:")
        print(f"To prevent this from happening again, create a Unique Index:")
        print(f"db.{COLLECTION_NAME}.createIndex({{ '{DUPLICATE_KEY}': 1 }}, {{ unique: true }})")
        print("-" * 50)


if __name__ == "__main__":
    cleaner = BatchDuplicateCleaner()
    cleaner.run()
