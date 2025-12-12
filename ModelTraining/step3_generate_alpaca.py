import pymongo
import json
import os
from tqdm import tqdm
from bson import ObjectId
from datetime import datetime, date
from pydantic import ValidationError

from ServiceComponent.IntelligenceHubDefines import ProcessedData
from ServiceComponent.IntelligenceAnalyzerProxy import build_analyze_user_message

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

# 4. 预览模式设置
# 如果设置为整数 (如 10)，则每个文件只生成前 10 条数据，方便快速检查。
# 如果设置为 None 或 0，则生成全量数据。
PREVIEW_LIMIT = 0

# 5. 系统指令 (Instruction / Prompt)
# 采用你优化后的精简版本
SYSTEM_PROMPT = (
    "你是一个专业情报分析师。请评估输入文本的情报价值并进行结构化解析。\n"
    "如果文本属于文艺创作、营销推广、生活服务、主观表达、历史学术、体育竞技或日常社交等无情报价值类别，请丢弃并仅输出包含UUID的JSON。\n"
    "如果文本涉及地缘政治、国际关系、政策法规、经济金融、科技突破或社会安全等具有实质性影响的内容，请提取关键要素，生成简体中文摘要与标题，并基于标准进行多维度评分（RATE），最终输出包含UUID、元数据、提取要素及评分的完整JSON对象。"
)


# ====================================================

def get_mongo_collections():
    client = pymongo.MongoClient("mongodb://localhost:27017/")
    db = client[DB_NAME]
    return db[COL_CACHED], db[COL_ARCHIVED]


def json_serial(obj):
    """JSON序列化辅助函数，处理 datetime 和 ObjectId"""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, ObjectId):
        return str(obj)
    raise TypeError(f"Type {type(obj)} not serializable")


def format_json_output(data):
    """
    将字典格式化为紧凑但可读的 JSON 字符串，用于 LLM 输出
    """
    return json.dumps(data, ensure_ascii=False, default=json_serial)


def apply_score_reduction(archived_doc):
    """
    执行“评分减1”逻辑。
    返回: (是否降级, 修改后的文档)
    """
    rates = archived_doc.get("RATE", {})
    if not isinstance(rates, dict):
        rates = {}

    # 获取最高分作为阈值判定
    max_score = 0
    appendix = archived_doc.get("APPENDIX", {})
    if "APPENDIX_MAX_RATE_SCORE" in appendix:
        try:
            max_score = float(appendix["APPENDIX_MAX_RATE_SCORE"])
        except:
            pass
    elif rates:
        try:
            # 提取数字部分计算最大值
            nums = []
            for v in rates.values():
                if isinstance(v, (int, float)):
                    nums.append(v)
                elif isinstance(v, str) and v.isdigit():
                    nums.append(float(v))
            if nums:
                max_score = max(nums)
        except:
            pass

    # 执行减分
    new_rates = {}
    all_scores_zero = True

    for k, v in rates.items():
        try:
            val = float(v)
            new_val = max(0, val - 1)
            # 保持整数格式
            if float(new_val).is_integer():
                new_rates[k] = int(new_val)
            else:
                new_rates[k] = new_val

            if new_val > 0:
                all_scores_zero = False
        except:
            new_rates[k] = v  # 无法转换的保留原样

    archived_doc["RATE"] = new_rates

    # 判定降级
    should_drop = False
    if max_score - 1 <= 0:
        should_drop = True
    elif all_scores_zero and len(rates) > 0:
        should_drop = True

    return should_drop, archived_doc


def process_dataset(input_file, output_file, col_cached, col_archived):
    if not os.path.exists(input_file):
        print(f"Skipping {input_file} (Not found)")
        return

    with open(input_file, 'r', encoding='utf-8') as f:
        uuid_list = json.load(f)

    alpaca_data = []
    stats = {
        "processed": 0,
        "dropped_original": 0,
        "dropped_demoted": 0,
        "archived_kept": 0,
        "errors": 0,
        "validation_error": 0
    }

    # 如果开启预览模式，截断列表
    current_uuid_list = uuid_list
    if PREVIEW_LIMIT and PREVIEW_LIMIT > 0:
        current_uuid_list = uuid_list[:PREVIEW_LIMIT]
        print(f"[PREVIEW MODE] Only processing first {PREVIEW_LIMIT} items for {input_file}")

    print(f"Processing {input_file} ({len(current_uuid_list)}/{len(uuid_list)} items)...")

    for item in tqdm(current_uuid_list):
        uuid = item['UUID']
        label_type = item['LABEL_TYPE']

        # 1. 获取 Input
        cached_doc = col_cached.find_one({"UUID": uuid})
        if not cached_doc:
            stats["errors"] += 1
            continue

        input_text = build_analyze_user_message(cached_doc)
        if not input_text:
            stats["errors"] += 1
            continue

        # 2. 确定 Output
        target_output = ""

        if label_type == 'DROPPED':
            target_output = format_json_output({"UUID": uuid})
            stats["dropped_original"] += 1

        elif label_type == 'ARCHIVED':
            archived_doc = col_archived.find_one({"UUID": uuid})

            if not archived_doc:
                target_output = format_json_output({"UUID": uuid})
                stats["errors"] += 1
            else:
                # 评分减1逻辑
                should_drop, modified_doc = apply_score_reduction(archived_doc)

                if should_drop:
                    target_output = format_json_output({"UUID": uuid})
                    stats["dropped_demoted"] += 1
                else:
                    # [关键步骤] 使用 ProcessedData 模型清洗数据
                    try:
                        # 1. 实例化模型进行校验和过滤 (自动去除 ProcessedData 定义之外的字段)
                        pydantic_obj = ProcessedData(**modified_doc)

                        # 2. 转回字典 (model_dump 会处理类型转换)
                        # mode='json' 会让 datetime 等自动转为字符串，非常适合 LLM 训练数据
                        clean_data = pydantic_obj.model_dump(mode='json', exclude_none=False)

                        target_output = format_json_output(clean_data)
                        stats["archived_kept"] += 1

                    except ValidationError as e:
                        # 如果数据库里的数据严重不符合 Schema，记录错误并跳过
                        # 或者你可以选择降级为丢弃
                        # print(f"Validation Error for {uuid}: {e}")
                        stats["validation_error"] += 1
                        continue

        entry = {
            "instruction": SYSTEM_PROMPT,
            "input": input_text,
            "output": target_output
        }
        alpaca_data.append(entry)
        stats["processed"] += 1

    # 保存
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(alpaca_data, f, ensure_ascii=False, indent=2)

    print(f"  - 完成: {output_file}")
    print(f"  - 原始丢弃: {stats['dropped_original']}")
    print(f"  - 降级丢弃: {stats['dropped_demoted']}")
    print(f"  - 最终归档(Clean): {stats['archived_kept']}")
    print(f"  - 校验失败: {stats['validation_error']}")
    print(f"  - 错误/缺失: {stats['errors']}")
    print("-" * 30)


def main():
    print(f">>> 开始第三步 (V3)：生成 Alpaca 数据")
    if PREVIEW_LIMIT:
        print(f"!!! 警告: 当前为预览模式，每组文件限制 {PREVIEW_LIMIT} 条 !!!")
        print("!!! 若要生成全量数据，请修改脚本中的 PREVIEW_LIMIT = 0 !!!\n")

    col_cached, col_archived = get_mongo_collections()

    process_dataset(FILE_IN_TRAIN, FILE_OUT_TRAIN, col_cached, col_archived)
    process_dataset(FILE_IN_TEST, FILE_OUT_TEST, col_cached, col_archived)
    process_dataset(FILE_IN_VAL, FILE_OUT_VAL, col_cached, col_archived)

    print("\n>>> 全部完成。")


if __name__ == "__main__":
    main()
