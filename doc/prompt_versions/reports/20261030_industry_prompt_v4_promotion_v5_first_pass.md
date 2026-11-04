# Industry Prompt v4 转正与 v5 首过率灰度

> 历史状态：v4 是唯一正式版本；v5 曾作为 25% 同模型 Shadow 候选。v5 首轮未改善首过率，
> 现已退出 Shadow 并由从 v4 直接派生的 v6 接替；v1–v3、v5 仅保留历史回看。

## 1. v4 转正依据

v4 首轮形成 11 条成功 Shadow 资产，同期主链完成 42 条，实际抽样率 26.2%，与 25% 配置一致，
没有终态失败。排除一次重试造成的跨模型样本后，双方都有分数的 7 个干净配对中，v4 平均分由
v1 的 6.54 降至 6.07：6 条下降、1 条不变、没有上升。下降主要来自预期差与异常性、投资可用性，
而不是机械压低所有维度。v4 同时保留了 v3 更具体的决定性事实、影响变量和关键限制说明。

因此 v4 进入正式池，v1 退出新数据抽样；已有 cache 中已经分配的版本不改写。

## 2. v5 的单一目标

v5 不修改准入、分类、八维评级、评分器、事件优先级、谓词目录或结果 Schema，只减少可避免的
`schema_shape` 重试。首轮 v4 的 11 个任务共调用 16 次，7 个任务首次通过；失败集中在：

- `EVENT_TITLE` 超过 30 字；
- `REASON` 或 `EVENT_BRIEF` 超过 100 字；
- epistemic qualifier 缺少必填 `by`。

v5 在 v4 后追加以下生成约束：

- 标题目标上限 24 字；REASON、摘要、影响和提示目标上限 80 字；正文简报上限 1600 字；
- NonIntelligence 原因同样按 80 字预算压缩；
- epistemic 必须带非空 `by` 实体数组，phase/polarity 禁止 `by`；
- roles、qualifier.by、qualifiers、relations 以及可选对象在输出前检查 JSON 类型和空值。

这些目标上限低于 Schema 硬上限，用于给模型计数误差留余量；服务端仍不截断语义字段。

## 3. 灰度配置与验收

- 正式池：v4；Shadow 池：v5；`sample_rate=0.25`；`comparison_mode=same_model`。
- 首轮至少累计 20 个同模型配对，主要指标是首次校验通过率；达到 50 个再判断是否转正。
- v5 的分类、总分和 RATE 应与 v4 基本一致。出现分类反转、总分变化超过 0.5 或事件主次变化时，
  必须人工复核，不能把首过率改进建立在信息删减上。
- 因客户端故障切换造成最终模型不同的记录应标记或剔除，不进入 Prompt 单变量结论。

首次运行后的机制审计与修复见
[`20261101_industry_v4_v5_shadow_audit.md`](20261101_industry_v4_v5_shadow_audit.md)。
后续 v6 设计见
[`20261101_industry_prompt_v6_schema_gray.md`](20261101_industry_prompt_v6_schema_gray.md)。
