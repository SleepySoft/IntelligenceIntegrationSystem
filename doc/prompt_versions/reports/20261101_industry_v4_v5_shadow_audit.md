# Industry v4/v5 Shadow 首次运行审计与机制修复

> 审计时间：2026-11-01（Asia/Shanghai）<br>
> 结论：运行配置确为正式 v4、Shadow v5；首批资产混入两条 v2→v5，根因是历史 cache
> 版本保留与 Shadow 准入条件不一致。机制已修复，Prompt 文本本轮不改。

> 后续状态：本报告完成后，已按第 6 节结论从 v4 直接构造 v6，并以 v6 接替 v5 Shadow；
> 设计见 [`20261101_industry_prompt_v6_schema_gray.md`](20261101_industry_prompt_v6_schema_gray.md)。

## 1. 配置与运行态

- `PromptCatalog.sampling_versions() == [4]`；
- industry `subsystems.shadow.versions == [5]`；
- `sample_rate == 0.25`，`comparison_mode == same_model`；
- 2026-10-30 15:09（北京时间）启动日志确认 `industry: [5]`。

因此 v4/v5 注册本身没有错误。

## 2. 污染样本及根因

审计时 MongoDB 有 8 条 v5 影子结果：6 条 v4→v5，2 条 v2→v5。两条异常记录为：

- `fb5984a4…`：三星 Q3 利润及存储扩产；
- `955f62ef…`：Nvidia 与供应商扩充 AI 产能。

两条数据均在 2026-10-28 采集，并在转正前把 v2 写入 cache 的
`APPENDIX.__PROMPT_VERSION__`。主链的 `_persistent_prompt_version()` 为保证重试和恢复归因，允许
已经注册的历史版本继续完成分析；这是正确行为。但旧实现会在任意主链版本成功后执行当前 v5
Shadow，因而形成不属于当前实验的 v2→v5。

同期还归档了少量 v1/v2 恢复任务。它们不是新数据随机抽到旧版本，也不说明正式池仍包含 v1/v2。

## 3. 机制修复

`EventV4PipelinePorts._run_shadow_analysis()` 现在先读取当前领域
`prompt_catalog.sampling_versions()`。只有 `primary_version` 仍属于当前正式池时才进行抽样和 Shadow
调用。历史 cache 仍按原版本完成主链归档，但不消耗 Shadow 调用，也不产生跨代配对。

`same_model` 的客户端定向只是调度偏好，校验纠错或故障重试仍可能发生客户端漂移。现在 Shadow
成功后还会核对最终客户端名和模型名；只要与主链不一致，就把本次 Shadow 计为失败且不写入
评估资产库，避免把多变量结果伪装成同模型 Prompt 对比。

已有两条 v2→v5 记录作为历史评估资产保留，不删除、不改写。比较工具新增：

- 同时查询 `v4_archived` 与 `v4_low_value`，不再把低价值结果误报为未归档；
- `--primary-version` 与 `--shadow-version` 精确选择实验版本；
- `--same-model-only` 排除最终模型不同的非单变量样本。

本轮 v4/v5 应使用：

```bash
python Scripts/compare_shadow_records.py \
  --domain industry --primary-version 4 --shadow-version 5 --same-model-only
```

## 4. Prompt 改进前的观测基线

修复前共有 10 次 v5 Shadow 尝试，其中两次来自历史 v2 主链。只看真正的 8 个 v4/v5 任务：

| 指标 | v4 主链 | v5 Shadow |
|---|---:|---:|
| 首次通过 | 6/8（75%） | 4/8（50%） |
| 最终形成可比较结果 | 8/8 | 6/8 |
| 三次校验仍失败 | 0 | 2 |

成功的 6 组中，4 组总分相同、2 组 v5 更高，均值从 6.12 升至 6.35。样本很小，但已经足以说明
v5 尚未实现“仅提高首过率”的目标。本轮不据此修改 Prompt；下一步先取得 v5 具体
`schema_shape` 字段错误，再决定是调整输出说明、桥接归一化还是 Schema 边界。

## 5. 回归要求

- 历史持久化版本能继续归档，但不会触发 Shadow；
- 当前正式版本仍按配置抽样并运行全部 Shadow 候选；
- `same_model` 最终客户端或模型漂移时不得保存比较记录；
- 历史记录继续可在单篇详情中查看，但灰度聚合必须显式限定 v4→v5 和同模型；
- 新闻领域和不同模型比较模式不得因该修复改变正式版本选择或归档行为。

## 6. 下一版 Prompt 的证据与方向（本轮不实施）

两条 v5 最终失败样本的六次原始响应仍在 `conversation/analysis/`，可由
`analysis_stats.db` 的 request_id 逐一对应。失败不是字符超限：六次响应的标题及主要说明字段都在
v5 预留预算内。问题集中在闭集 Schema 词汇：

- `774dc475…` 三次都输出自由属性 `attributes.用途`，并把“海外市场”的实体类型写成协议中不存在的
  `market`；服务端反馈后仍重复，说明只写“有效字段/类型”不足以阻止模型按自然语言自造键和值。
- `2081cc1f…` 首次把 `attributes.duration.type` 写成 `number` 而非固定的 `duration`；第二次纠正后
  又新增协议外顶层字段 `SUB_CATEGORY_NOTE`，第三次仍保留该字段。

因此不应继续给 v5 叠加一段更长的泛化自检。下一候选应从正式 v4 派生一个短而封闭的输出约束，并放在
输出结构附近，明确列出观察到的反例：顶层不得新增任何键；attributes 只允许八个规定键，资金用途等
文本不得写入 attributes；duration 的 type 只能是 duration；市场/赛道不是实体类型，不得输出
`type=market`。评分、准入、事件选择和文字预算保持 v4 不变，以便 Shadow 只测 Schema 首过率。

可独立评估的服务端安全归一化仅限无语义歧义的情况，例如按 `duration` 键把其 type 固定为
`duration`、删除值为 null/空字符串的未知顶层辅助字段。自由属性和未知实体类型不能静默猜测或吞掉，
仍应校验失败，避免用“首过率提高”掩盖抽取信息损坏。
