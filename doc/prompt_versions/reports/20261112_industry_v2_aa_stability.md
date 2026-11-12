# Industry Prompt v2 A/A 稳定性实验

> 启用日期：2026-11-12（Asia/Shanghai）
> 实验 ID：`industry-v2-aa-20261112`
> 配置：正式 v2；25% 确定性抽样；`comparison_mode=replicate`

## 目的

本轮不比较 Prompt 优劣。对同一输入使用同一 v2 Prompt、同一最终客户端和模型重复分析，测量
温度为 0 时仍存在的模型固有波动，为后续 A/B 的分类、评分、事件和实体差异建立噪声基线。

## 运行边界

- 新样本固定使用 v2；已有 cache 中已分配 v4 的任务继续按 v4 完成，但不进入 v2 A/A；
- 抽样键为 `SHA256(experiment_id + domain_name + UUID)`，重启和重试不改变分组；
- 正式高价值结果归档后投递 Shadow；低价值结果写入低价值库后投递；
- Shadow 使用独立 worker，不阻塞正式分析和归档；
- 最终客户端名或模型不一致时不写入 A/A 资产；
- MongoDB 以领域、UUID、实验 ID 和 Shadow 版本幂等 upsert。

## 首次检查点

至少取得 100 组严格配对后评估：

1. 首过率的配对一致性及 McNemar 分歧；
2. Intelligence/NonIntelligence 分类分歧率；
3. RATE 各维一致率与总分绝对差；
4. 主事件谓词、事件集合、实体集合与抽取质量波动；
5. 按模型和主要来源分层后的稳定性；
6. 额外调用、Token 和延迟成本。

该实验只产生基线，不直接触发 Prompt 转正结论。

只读检查命令：

```bash
python Scripts/compare_shadow_records.py \
  --domain industry --primary-version 2 --shadow-version 2 \
  --experiment-id industry-v2-aa-20261112 \
  --same-model-only --summary-only
```
