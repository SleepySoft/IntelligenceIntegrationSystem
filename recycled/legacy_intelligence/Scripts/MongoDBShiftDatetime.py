import logging
import datetime
from pymongo import MongoClient, UpdateOne
from pymongo.errors import PyMongoError
from bson import ObjectId


# --- 配置区：请根据您的实际情况修改以下信息 ---

# 1. MongoDB 连接信息
MONGO_URI = "mongodb://localhost:27017/"
DB_NAME = "IntelligenceIntegrationSystem"  # 您需要迁移的数据库名
COLLECTION_NAME = "intelligence_archived"  # 您需要迁移的集合名
# COLLECTION_NAME = "intelligence_cached"  # 您需要迁移的集合名

# 2. 需要修正的字段列表
# 将所有可能存在错误时间的字段名添加到这个列表中
# 例如: ["created_at", "updated_at", "event_time", "nested.timestamp"]
FIELDS_TO_MIGRATE = [
    "APPENDIX.__TIME_ARCHIVED__"
]

# 3. 时区偏移量（小时）
# 东八区 (UTC+8)，所以是 8
TIME_SHIFT_HOURS = -8

# 4. 批处理大小
# 一次处理多少个文档，可以根据您的服务器性能调整
BATCH_SIZE = 500

# --- 日志配置 ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


def migrate_naive_times():
    """
    连接到 MongoDB 并将指定的 naive datetime 字段修正为正确的 UTC 时间。
    """
    try:
        client = MongoClient(MONGO_URI)
        db = client[DB_NAME]
        collection = db[COLLECTION_NAME]
        logging.info(f"成功连接到数据库 '{DB_NAME}'，集合 '{COLLECTION_NAME}'.")
    except PyMongoError as e:
        logging.error(f"数据库连接失败: {e}")
        return

    # timedelta 对象用于增加时间
    time_delta = datetime.timedelta(hours=TIME_SHIFT_HOURS)

    # 查询条件：只找出那些 tzinfo 不存在的 naive time 字段
    # 注意: 这个查询只能检查顶层字段。对于嵌套字段，我们需要在代码中检查。
    query = {
        "$or": [
            {field: {"$type": "date"}} for field in FIELDS_TO_MIGRATE
        ]
    }

    total_docs_processed = 0
    total_docs_updated = 0

    try:
        # 使用 find() 获取一个游标，PyMongo 会在后台自动处理批次
        cursor = collection.find(query, no_cursor_timeout=True)
        logging.info("开始扫描文档...")

        bulk_operations = []

        for doc in cursor:
            total_docs_processed += 1
            update_payload = {}

            for field_path in FIELDS_TO_MIGRATE:
                # 检查并获取字段的值，支持 "nested.key" 这样的路径
                current_value = doc
                keys = field_path.split('.')
                is_valid_path = True
                for key in keys:
                    if isinstance(current_value, dict) and key in current_value:
                        current_value = current_value[key]
                    else:
                        is_valid_path = False
                        break

                if not is_valid_path:
                    continue

                # 核心逻辑：只修正 naive datetime 对象
                if isinstance(current_value, datetime.datetime) and current_value.tzinfo is None:
                    corrected_time = current_value + time_delta
                    update_payload[field_path] = corrected_time
                    logging.info(f"文档 {doc['_id']}: 字段 '{field_path}' 从 {current_value} 修正为 {corrected_time}")

            if update_payload:
                total_docs_updated += 1
                bulk_operations.append(
                    UpdateOne({"_id": doc["_id"]}, {"$set": update_payload})
                )

            # 达到批处理大小时，执行批量更新
            if len(bulk_operations) >= BATCH_SIZE:
                logging.info(f"正在执行 {len(bulk_operations)} 个文档的批量更新...")
                collection.bulk_write(bulk_operations)
                bulk_operations = []
                logging.info(f"处理进度: {total_docs_processed} 个文档已扫描, {total_docs_updated} 个文档已更新。")

        # 处理最后一批不足 BATCH_SIZE 的操作
        if bulk_operations:
            logging.info(f"正在执行最后 {len(bulk_operations)} 个文档的批量更新...")
            collection.bulk_write(bulk_operations)

        logging.info("=" * 30)
        logging.info("数据迁移完成！")
        logging.info(f"总共扫描文档数: {total_docs_processed}")
        logging.info(f"总共更新文档数: {total_docs_updated}")

    except PyMongoError as e:
        logging.error(f"在迁移过程中发生错误: {e}")
    finally:
        if 'cursor' in locals() and cursor:
            cursor.close()
        client.close()
        logging.info("数据库连接已关闭。")


if __name__ == "__main__":
    # 在运行前，请务必确认配置信息正确，并已备份数据库！
    migrate_naive_times()


"""
### 如何使用这个脚本

请严格按照以下步骤操作，以确保数据安全：

1.  **备份！备份！备份！** 在进行任何操作之前，请务必为您的 MongoDB 数据库创建一个完整的备份。这是最重要的安全措施。

2.  **配置脚本**：打开 `migration_script.py` 文件，仔细修改顶部的配置区：
    * `MONGO_URI`: 您的 MongoDB 连接字符串。
    * `DB_NAME` 和 `COLLECTION_NAME`: 您需要修复的数据库和集合的名称。
    * `FIELDS_TO_MIGRATE`: **这是一个关键列表**。请将所有可能包含错误时间的字段名都加进去。如果字段是嵌套的，也支持，例如 `["creation_date", "user_profile.last_login"]`。
    * `TIME_SHIFT_HOURS`: 对于东八区，保持 `8` 即可。

3.  **小范围测试**：在全面执行之前，先找一个测试用的数据库，或者在生产数据库上添加一个查询条件（修改脚本中的 `query` 变量），只对少量文档进行测试，以验证脚本的行为是否符合预期。例如，只处理一个特定的文档：
    ```python
    # 在 `cursor = collection.find(query, no_cursor_timeout=True)` 这一行之前
    query['_id'] = ObjectId("某个你知道有问题的文档ID") 
    ```

4.  **执行完整迁移**：当您确认脚本工作正常后，移除测试用的查询条件，然后运行脚本：
    ```bash
    python migration_script.py
"""