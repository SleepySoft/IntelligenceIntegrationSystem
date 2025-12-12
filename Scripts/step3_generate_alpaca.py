import os
import json
import pymongo
import datetime
from tqdm import tqdm
from bson import ObjectId

# ================= 配置区域 =================

# 1. 数据库配置
DB_NAME = "IntelligenceIntegrationSystem"
COL_CACHED = "intelligence_cached"
COL_ARCHIVED = "intelligence_archived"

# 2. 文件输入 (来自第二步)
FILE_IN_TRAIN = "dataset_train.json"
FILE_IN_TEST = "dataset_test.json"
FILE_IN_VAL = "dataset_val.json"

# 3. 文件输出 (Alpaca 格式)
FILE_OUT_TRAIN = "alpaca_train.json"
FILE_OUT_TEST = "alpaca_test.json"
FILE_OUT_VAL = "alpaca_val.json"

# 4. 系统指令 (Instruction / Prompt)
# 这是模型的“功能开关”。即使数据千变万化，这句话是不变的锚点。
SYSTEM_PROMPT = """你是一个专业情报分析师。请评估输入文本的情报价值并进行结构化解析。
如果文本属于文艺创作、营销推广、生活服务、主观表达、历史学术、体育竞技或日常社交等无情报价值类别，请丢弃并仅输出包含UUID的JSON。
如果文本涉及地缘政治、国际关系、政策法规、经济金融、科技突破或社会安全等具有实质性影响的内容，请提取关键要素，生成简体中文摘要与标题，并基于标准进行多维度评分（RATE），最终输出包含UUID、元数据、提取要素及评分的完整JSON对象。
"""


# ===========================================

def get_mongo_collections():
    client = pymongo.MongoClient("mongodb://localhost:27017/")
    db = client[DB_NAME]
    return db[COL_CACHED], db[COL_ARCHIVED]


def build_input_text(cached_doc):
    """
    构建输入文本 (Input)。
    模拟 build_analyze_message 的逻辑，将元数据和正文拼接。
    """
    if not cached_doc:
        return None

    # 提取需要的字段，过滤掉无关的内部字段
    # 注意：exclude_unset=True 在 Pydantic 中常用，这里手动处理
    safe_metadata = {}
    exclude_keys = {'_id', 'content', 'APPENDIX'}

    for k, v in cached_doc.items():
        if k not in exclude_keys and v is not None:
            # 简单转字符串
            safe_metadata[k] = str(v)

    metadata_items = [f"- {k}: {v}" for k, v in safe_metadata.items()]
    metadata_block = '## metadata\n' + "\n".join(metadata_items)

    content = cached_doc.get('content', '')
    content_block = f"\n\n## 正文内容\n{content}"

    user_message = metadata_block + content_block
    return user_message


def apply_score_reduction(archived_doc):
    """
    执行“评分减1”逻辑。
    返回: (是否降级为丢弃, 修改后的文档)
    """
    # 1. 获取 RATE 字典
    rates = archived_doc.get("RATE", {})
    if not isinstance(rates, dict):
        rates = {}

    # 2. 获取 MAX_SCORE (作为判定阈值)
    # 优先看 APPENDIX_MAX_RATE_SCORE，如果没有则看 RATE 里的最大值
    max_score = 0
    appendix = archived_doc.get("APPENDIX", {})
    if "APPENDIX_MAX_RATE_SCORE" in appendix:
        try:
            max_score = float(appendix["APPENDIX_MAX_RATE_SCORE"])
        except:
            pass
    elif rates:
        # 尝试从 rates 字典的值中找最大值
        try:
            max_score = max([float(v) for v in rates.values() if
                             isinstance(v, (int, float, str)) and str(v).replace('.', '', 1).isdigit()])
        except:
            pass

    # 3. 执行减分操作
    # 逻辑：我们将 RATE 字典里的所有数值项 -1
    new_rates = {}
    all_scores_zero = True

    for k, v in rates.items():
        try:
            val = float(v)
            new_val = max(0, val - 1)
            # 如果原值就是整数，保持整数格式
            if val.is_integer():
                new_rates[k] = int(new_val)
            else:
                new_rates[k] = new_val

            if new_val > 0:
                all_scores_zero = False
        except:
            # 非数字评分，保持原样
            new_rates[k] = v

    # 更新文档中的 RATE
    archived_doc["RATE"] = new_rates

    # 4. 判定是否降级
    # 判定标准：如果 MAX_SCORE - 1 <= 0，或者 RATE 里所有分全归零了
    should_drop = False

    if max_score - 1 <= 0:
        should_drop = True
    elif all_scores_zero and len(rates) > 0:
        should_drop = True

    return should_drop, archived_doc


def json_serial(obj):
    """JSON序列化辅助函数，处理 datetime 和 ObjectId"""
    if isinstance(obj, (datetime.datetime, datetime.date)):
        return obj.isoformat()
    if isinstance(obj, ObjectId):  # <--- 新增：如果遇到ObjectId，转为字符串
        return str(obj)
    raise TypeError(f"Type {type(obj)} not serializable")


def format_json_output(data):
    """
    将字典格式化为紧凑但可读的 JSON 字符串，用于 LLM 输出
    """
    # 增加 default=json_serial 参数来处理时间对象
    return json.dumps(data, ensure_ascii=False, default=json_serial)


def process_dataset(input_file, output_file, col_cached, col_archived):
    if not os.path.exists(input_file):
        print(f"Skipping {input_file} (Not found)")
        return

    with open(input_file, 'r', encoding='utf-8') as f:
        uuid_list = json.load(f)

    alpaca_data = []
    stats = {
        "processed": 0,
        "dropped_original": 0,  # 原本就是丢弃的
        "dropped_demoted": 0,  # 原本是归档但因减分被丢弃的
        "archived_kept": 0,  # 归档且保留的
        "errors": 0
    }

    print(f"Processing {input_file} ({len(uuid_list)} items)...")

    for item in tqdm(uuid_list):
        uuid = item['UUID']
        label_type = item['LABEL_TYPE']

        # --- 1. 获取输入数据 (Input) ---
        # 无论是丢弃还是归档，Input 都来自 intelligence_cached (模拟原始状态)
        cached_doc = col_cached.find_one({"UUID": uuid})
        if not cached_doc:
            stats["errors"] += 1
            continue

        input_text = build_input_text(cached_doc)
        if not input_text:
            stats["errors"] += 1
            continue

        # --- 2. 确定输出数据 (Output) ---
        target_output = ""

        if label_type == 'DROPPED':
            # 情况 A: 原本就是丢弃数据
            target_output = format_json_output({"UUID": uuid})
            stats["dropped_original"] += 1

        elif label_type == 'ARCHIVED':
            # 情况 B: 归档数据，需要检查评分
            archived_doc = col_archived.find_one({"UUID": uuid})

            if not archived_doc:
                # 异常情况：标记为归档但库里找不到，回退为丢弃
                target_output = format_json_output({"UUID": uuid})
                stats["errors"] += 1
            else:
                # 清理 _id 等 MongoDB 内部字段
                if '_id' in archived_doc: del archived_doc['_id']
                if 'APPENDIX' in archived_doc: del archived_doc['APPENDIX']  # 输出通常不包含 Appendix

                # *** 执行评分减1逻辑 ***
                should_drop, modified_doc = apply_score_reduction(archived_doc)

                if should_drop:
                    # 降级为丢弃
                    target_output = format_json_output({"UUID": uuid})
                    stats["dropped_demoted"] += 1
                else:
                    # 保持归档，使用修改后的 doc (含减分后的 RATE)
                    target_output = format_json_output(modified_doc)
                    stats["archived_kept"] += 1

        # --- 3. 构建 Alpaca 条目 ---
        entry = {
            "instruction": SYSTEM_PROMPT,
            "input": input_text,
            "output": target_output
        }
        alpaca_data.append(entry)
        stats["processed"] += 1

    # 保存结果
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(alpaca_data, f, ensure_ascii=False, indent=2)

    # 打印该文件摘要
    print(f"  - 完成: {output_file}")
    print(f"  - 原始丢弃: {stats['dropped_original']}")
    print(f"  - 降级丢弃(评分不足): {stats['dropped_demoted']} (这些样本现在是负样本)")
    print(f"  - 最终归档: {stats['archived_kept']}")
    print(f"  - 错误/缺失: {stats['errors']}")
    print("-" * 30)


def main():
    print(">>> 开始第三步：生成 Alpaca 训练数据...")

    col_cached, col_archived = get_mongo_collections()

    # 依次处理三个数据集
    process_dataset(FILE_IN_TRAIN, FILE_OUT_TRAIN, col_cached, col_archived)
    process_dataset(FILE_IN_TEST, FILE_OUT_TEST, col_cached, col_archived)
    process_dataset(FILE_IN_VAL, FILE_OUT_VAL, col_cached, col_archived)

    print("\n>>> 全部完成。请检查 alpaca_*.json 文件。")
    print(">>> 下一步：你可以使用这些 JSON 文件进行 LoRA 微调 (例如使用 LLaMA-Factory)。")


if __name__ == "__main__":
    main()
