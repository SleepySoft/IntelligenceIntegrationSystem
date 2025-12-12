import json
import pandas as pd
import numpy as np
from urllib.parse import urlparse
from sklearn.model_selection import train_test_split

# ================= 配置区域 =================
# 1. 目标总样本数
TARGET_TOTAL_COUNT = 3000

# 2. 期望的丢弃数据 (负样本) 比例
# 0.5 表示 1:1。如果你的正样本（归档）很少，可以适当降低这个值，比如 0.4 或 0.3，
# 但不建议低于 0.2，否则模型学不会拒绝垃圾内容。
EXPECTED_RATIO_DROPPED = 0.5

# 3. 数据集切分比例 (训练 / 测试 / 验证)
SPLIT_RATIOS = (0.8, 0.1, 0.1)

# 输入/输出文件
FILE_DROPPED = "summary_dropped.json"
FILE_ARCHIVED = "summary_archived.json"

FILE_OUT_TRAIN = "dataset_train.json"
FILE_OUT_TEST = "dataset_test.json"
FILE_OUT_VAL = "dataset_val.json"


# ===========================================

def load_data(file_path):
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return pd.DataFrame(data)
    except FileNotFoundError:
        print(f"[Error] 找不到文件: {file_path}")
        return pd.DataFrame()  # 返回空DF防止报错


def get_domain(url):
    """提取URL的主机名/域名"""
    if not isinstance(url, str):
        return "unknown"
    try:
        parsed = urlparse(url)
        # netloc 通常包含域名和端口，我们只需要这个
        domain = parsed.netloc
        if not domain:
            return "unknown"
        return domain
    except:
        return "unknown"


def calculate_weights_by_domain(df, url_col_name, score_col_name=None):
    """
    通用权重计算函数：
    1. 基于域名的逆频率权重 (保证来源多样性)
    2. (可选) 基于分数的线性权重 (保证质量)
    """
    if df.empty:
        return []

    # --- 1. 域名提取与统计 ---
    # 创建一个临时列存放域名
    temp_domain_col = '_temp_domain'
    df[temp_domain_col] = df[url_col_name].apply(get_domain)

    # 统计每个域名的出现次数
    domain_counts = df[temp_domain_col].value_counts()

    # 基础权重 = 1 / 该域名的总样本数
    # 这样来自 'sina.com.cn' (1000条) 的每条权重为 1/1000
    # 来自 'myblog.org' (1条) 的每条权重为 1/1
    w_domain = 1.0 / df[temp_domain_col].map(domain_counts)

    # --- 2. 分数加权 (仅归档数据) ---
    w_score = 1.0
    if score_col_name and score_col_name in df.columns:
        # 转数字，处理空值
        s_vals = pd.to_numeric(df[score_col_name], errors='coerce').fillna(0)
        min_s, max_s = s_vals.min(), s_vals.max()

        # 如果有区分度，则进行 Min-Max 加权 (1.0 ~ 2.0 倍)
        if max_s > min_s:
            w_score = 1.0 + (s_vals - min_s) / (max_s - min_s)

    # --- 3. 最终权重 ---
    final_weights = w_domain * w_score

    # 清理临时列
    df.drop(columns=[temp_domain_col], inplace=True)

    return final_weights


def main():
    print(">>> 开始第二步 (v2)：智能采样与切分...")

    # 1. 读取数据
    df_dropped = load_data(FILE_DROPPED)
    df_archived = load_data(FILE_ARCHIVED)

    total_avail_dropped = len(df_dropped)
    total_avail_archived = len(df_archived)

    print(f"    - 可用数据: 丢弃(Dropped)={total_avail_dropped}, 归档(Archived)={total_avail_archived}")

    if total_avail_dropped == 0 and total_avail_archived == 0:
        print("[Error] 没有可用数据，请检查第一步结果。")
        return

    # 2. 智能计算目标数量 (处理样本不足的情况)
    # 计划数量
    plan_dropped = int(TARGET_TOTAL_COUNT * EXPECTED_RATIO_DROPPED)
    plan_archived = int(TARGET_TOTAL_COUNT * (1 - EXPECTED_RATIO_DROPPED))

    # 实际采样数量 (取 计划 与 可用 的最小值)
    n_dropped = min(plan_dropped, total_avail_dropped)
    n_archived = min(plan_archived, total_avail_archived)

    # 计算实际比例
    total_actual = n_dropped + n_archived
    if total_actual > 0:
        actual_ratio_dropped = n_dropped / total_actual
    else:
        actual_ratio_dropped = 0

    print(f"\n[采样计划]")
    print(f"    - 目标总数: {TARGET_TOTAL_COUNT}")
    print(f"    - 期望丢弃比例: {EXPECTED_RATIO_DROPPED:.1%} (目标: {plan_dropped})")
    print(f"    ------------------------------------------------")
    print(f"    - 实际采样丢弃: {n_dropped} (因为库存: {total_avail_dropped})")
    print(f"    - 实际采样归档: {n_archived} (因为库存: {total_avail_archived})")
    print(f"    - 实际总数: {total_actual}")
    print(f"    - 实际丢弃比例: {actual_ratio_dropped:.1%}")

    if abs(actual_ratio_dropped - EXPECTED_RATIO_DROPPED) > 0.1:
        print(f"    [Warning] ⚠️ 实际比例与期望比例偏差较大，可能导致模型倾向性偏移！")

    # 3. 执行加权采样
    print("\n[Sampling] 正在按域名分布加权采样...")

    # Dropped 采样
    if n_dropped > 0:
        w_dropped = calculate_weights_by_domain(df_dropped, url_col_name='informant')
        sampled_dropped = df_dropped.sample(n=n_dropped, weights=w_dropped, random_state=42)
        sampled_dropped['LABEL_TYPE'] = 'DROPPED'
    else:
        sampled_dropped = pd.DataFrame()

    # Archived 采样
    if n_archived > 0:
        w_archived = calculate_weights_by_domain(df_archived, url_col_name='INFORMANT',
                                                 score_col_name='APPENDIX_MAX_RATE_SCORE')
        sampled_archived = df_archived.sample(n=n_archived, weights=w_archived, random_state=42)
        sampled_archived['LABEL_TYPE'] = 'ARCHIVED'
    else:
        sampled_archived = pd.DataFrame()

    # 合并 & 打乱
    df_final = pd.concat([sampled_dropped, sampled_archived])
    df_final = df_final.sample(frac=1, random_state=42).reset_index(drop=True)

    # 4. 划分训练/测试/验证集
    print(f"\n[Splitting] 划分数据集: {SPLIT_RATIOS}")

    export_data = df_final[['UUID', 'LABEL_TYPE']].to_dict('records')

    # 如果数据太少，无法分层切割(Stratified Split)，会导致报错，这里做个保护
    try:
        train_ratio, test_ratio, val_ratio = SPLIT_RATIOS

        # 第一次切分: Train vs (Test+Val)
        train_data, temp_data = train_test_split(
            export_data,
            train_size=train_ratio,
            random_state=42,
            stratify=[x['LABEL_TYPE'] for x in export_data]
        )

        # 第二次切分: Test vs Val
        relative_test_ratio = test_ratio / (test_ratio + val_ratio)
        test_data, val_data = train_test_split(
            temp_data,
            train_size=relative_test_ratio,
            random_state=42,
            stratify=[x['LABEL_TYPE'] for x in temp_data]
        )
    except ValueError as e:
        print(f"[Error] 数据切分失败，可能是某类样本数量太少(小于2个)，无法进行分层抽样。")
        print(f"错误详情: {e}")
        # 简单回退策略：直接切分不分层
        import random
        random.shuffle(export_data)
        n = len(export_data)
        n_train = int(n * train_ratio)
        n_test = int(n * test_ratio)
        train_data = export_data[:n_train]
        test_data = export_data[n_train:n_train + n_test]
        val_data = export_data[n_train + n_test:]
        print("    [Info] 已切换为简单随机切分（非分层）。")

    # 5. 保存
    def save_split(filename, data_list):
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(data_list, f, indent=4)
        print(f"    - 已保存 {filename}: {len(data_list)} 条")

    save_split(FILE_OUT_TRAIN, train_data)
    save_split(FILE_OUT_TEST, test_data)
    save_split(FILE_OUT_VAL, val_data)

    print("\n>>> 第二步完成。")


if __name__ == "__main__":
    main()
