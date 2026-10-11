# Gemini Flash Client 使用优化方案

## 1. 结论

不要再把 Flash Lite 与普通 Flash 放进同一个 `SelfRotatingOpenAIClient` 做逐请求
round-robin。AI Studio 当前按模型分别给出 RPM、TPM、RPD，而两类模型的额度和用途差异
明显；平均轮换既浪费 Lite 的大额度，也会让普通 Flash 的高质量额度被普通样本消耗。

推荐改为三级策略：

1. **Flash Lite 实时主力**：承担首次分析和绝大多数普通情报。
2. **Flash 质量升级池**：仅承担 V4 校验失败后的纠错重试、人工质量模式和高价值复核。
3. **Batch Flash 批处理池**：未来用于历史积压和可延迟任务，不占实时链路。

生产切换前必须使用独立 Client Benchmark 对每个 API 模型 ID 做同样本对比。限额大不代表
Event V4 正确率高，模型新也不应自动获得更高生产优先级。

本方案依据 [AI Studio 项目限额快照](gemini_aistudio_rate_limits_20261011.md)。

## 2. 当前项目的关键容量

### 2.1 实时文本模型

| 模型系列 | RPM | 输入 TPM | RPD | 建议角色 |
| --- | ---: | ---: | ---: | --- |
| Gemini 3.5 Flash Lite | 15 | 250K | 500 | 首选主力候选 |
| Gemini 3.1 Flash Lite | 15 | 250K | 500 | 主力轮换候选 |
| Gemini 2.5 Flash Lite | 10 | 250K | 20 | 旧版低日额度备用 |
| Gemini 3.8 Flash | 5 | 250K | 20 | 质量对照候选 |
| Gemini 3 / 3.5 / 3.6 / 3.7 Flash | 各 5 | 各 250K | 各 20 | 独立低日额度备用池 |
| Gemini 2.5 Flash | 5 | 250K | 20 | 旧版备用候选 |

页面把 TPM 标为“每分钟输入 token 数”。当前 Event V4 固定 Prompt 在拼接正文前约
17,159 个字符、22,521 字节，实际 Token 必须使用每次响应的 `usage.prompt_tokens` 记录，
不能用字符数直接当 Token 数。

按每次 10K 输入 Token 估算，250K TPM 理论上允许约 25 请求/分钟；主力 Lite 会先受
15 RPM 限制，普通 Flash 会先受 5 RPM 限制。当前少量分析 worker 下，分钟额度通常仍
不是瓶颈，长期吞吐主要受 RPD 和单请求延迟约束。

### 2.2 Batch API

- Flash Lite：100 个并发批量请求、10M 排队 Token（3.1/3.5）。
- Flash：100 个并发批量请求、3M 排队 Token（3/3.5/3.6/3.7/3.8）。

Batch 应只处理可延迟任务。当前 Hub 是逐事件请求/响应链路，不能把 Batch API 冒充为
普通 Client；需要独立的提交、轮询、结果回注和幂等状态机。

## 3. 现有实现的 Gap

### 3.1 简单轮换不了解模型额度

`SelfRotatingOpenAIClient` 的 `SimpleRotator` 只按调用次数轮换：

- 不知道每个模型各自的 RPM、TPM、RPD；
- 不知道剩余额度和 `Retry-After`；
- Lite 与 Flash 被平均使用，无法实现“低成本主力 + 高质量升级”；
- Client 级 `BudgetPolicy` 把多个模型的用量混在一个计数器中。

### 3.2 一个 Client 对象只有一个并发槽

`BaseAIClient` 使用单个 `in_use` 状态，一个 `Gemini Free Pool` 即使 Provider 允许高并发，
在 IIS 中也只能同时执行一个请求。只提高 `AI_CLIENT_LIMIT` 不会凭空增加 Client 对象的
并发能力。

### 3.3 校验重试可能仍命中同一模型

Event V4 会把校验错误追加到 Prompt 后重试，但当前重新获取 Client 时没有携带：

- 已尝试模型；
- 失败类型；
- 希望升级到质量模型的意图。

因此温度为 0 时可能连续调用同一模型，重复得到相似错误，白白消耗 Token。

## 4. 目标 Client 拓扑

### 4.1 模型与并发槽分离

新增一个共享的 `GeminiProjectPool`，项目池下每个模型有独立状态：

```text
GeminiProjectPool (一个 Project / API Key 集合)
├─ gemini-3.5-flash-lite  [primary, N 个实时槽]
├─ gemini-3.1-flash-lite  [primary, N 个实时槽]
├─ gemini-3.8-flash       [reserve, M 个实时槽]
├─ gemini-3.7-flash       [reserve, M 个实时槽]
└─ Batch adapter          [独立异步任务状态机]
```

每个模型必须有独立健康度、限额窗口和基准质量统计；同一项目下的多个执行槽共享该模型的
额度账本，不能各自把完整额度计算一遍。

首版可以把一个模型暴露为多个 `BaseAIClient` 槽位，以兼容现有 Manager；所有槽位引用同一
线程安全 `ModelQuotaLedger`。后续再考虑把 Manager 的布尔 `in_use` 改为真正的信号量。

### 4.2 不依赖增加 API Key 扩容

Google 限额按项目和模型执行。同一项目创建多个 Key 只能用于隔离、轮换和吊销，不增加
表中的总额度。Key 轮换与模型额度调度必须是两层独立机制。

## 5. 调度策略

### 5.1 首次分析

默认使用基准中排名最高、且达到生产门槛的 Flash Lite。初始候选是
`gemini-3.5-flash-lite`，但最终选择由实测决定。

准入条件：

```text
健康可用
AND 未处于 429 冷却
AND 预计输入 Token 不会突破 TPM 安全线
AND RPD 仍有保留额度
AND 有空闲执行槽
```

同级候选不做机械 round-robin，建议使用“加权最少消耗”评分：

```text
调度分 = 质量分
       + 配额剩余比例权重
       - P95 延迟惩罚
       - 最近失败率惩罚
```

### 5.2 按失败原因重试

| 失败类型 | 下一步 |
| --- | --- |
| 网络、5xx、短时超时 | 同层换另一个健康槽；不因网络错误升级模型质量 |
| 429 | 仅冷却命中的模型/项目窗口，遵守 `Retry-After`，改用其他模型 |
| JSON 解析或 V4 校验失败 | 记录已尝试模型，第二次直接升级到普通 Flash |
| 普通 Flash 仍校验失败 | 第三次换另一个质量模型或跨 Provider，不再回到原模型 |
| HTTP 400 内容/参数错误 | 保持现有不可重试规则 |

建议顺序：

```text
attempt 1: 最佳 Flash Lite
attempt 2: 最佳普通 Flash + 当前校验反馈
attempt 3: 未尝试过的普通 Flash / 其他高质量 Provider
```

重试上下文需新增 `attempted_client_names`、`attempted_models`、`failure_category` 和
`desired_tier`，Manager 必须能够排除已尝试模型，而不只是排除某一个 Client 对象。

### 5.3 人工调试

人工调试页面应继续允许固定 Client/模型。自动策略只用于生产自动调度，不能覆盖人工指定
模型，否则无法对同一 Prompt 做可重复对比。

## 6. 限额账本

每个模型维护以下窗口：

- 最近 60 秒请求数；
- 最近 60 秒输入 Token；
- 当前 RPD 周期请求数；
- 在途请求及其预留 Token；
- `Retry-After` / 冷却截止时间；
- API 成功率、V4 校验率、P50/P95 延迟。

请求发出前先预留估算输入 Token，返回后用 `usage.prompt_tokens` 结算差值。估算值使用
“Prompt 版本 + 子系统 + 正文长度区间”的 EWMA，并给首次未知样本预留上界。

建议保留：

- RPM/TPM/RPD 均只使用页面限额的 80%，吸收统计延迟、人工调试和突发请求；
- 3.1/3.5 Flash Lite 各使用 400 RPD，作为自动分析主力；
- 其余模型各使用 16 RPD，只作为低日额度补充与对照池。

限额值来自配置快照，但运行时以响应头、429 和本地观测修正；不能把 2026-10-11 的数字
当作永久常量。

## 7. 配置建议

使用统一模型白名单和两级并发槽：

```text
GEMINI_MODELS=gemini-3.5-flash-lite,gemini-3.1-flash-lite,gemini-3.8-flash
GEMINI_PRIMARY_SLOTS=2
GEMINI_RESERVE_SLOTS=1
GEMINI_QUOTA_SAFETY_RATIO=0.8
GEMINI_RATE_LIMIT_DB=./_data/ai_rate_limits.sqlite3
```

Slot 数应与 `intelligence_hub.ai_analysis_thread` 联动。首轮建议每个主力模型 2 个槽、每个备用模型 1 个槽，
但先把 worker 保持在 2；验证无共享状态竞争后再提高到 4。启动日志应报告总 worker、可调度
槽、每层槽数，而不是只显示 Client 数量。

## 8. Batch 使用边界

Batch 适合：

- 已归档历史数据重分析；
- 旧情报积压；
- 大规模 Prompt/模型基准；
- 不要求请求内同步返回的翻译或补充抽取。

Batch 不适合：

- 新情报实时主链；
- 人工调试即时对比；
- 必须在 `/collect` 返回前确认的阶段。

Batch 任务至少需要 `submitted → polling → completed/failed → injected` 状态，提交批次 ID、
模型、Prompt 哈希和每条输入 UUID 必须持久化，防止服务重启后重复提交或重复归档。

## 9. 上线门槛

推荐先扩充基准集，再决定模型排名：

- 至少 30 条脱敏样本，覆盖中英文、长文、多事件、无情报、易幻觉和历史失败样本；
- 每个模型每条至少重复 3 次；
- 首选 Lite：API 成功率 ≥ 99%，V4 校验率 ≥ 98%，业务断言正确率 ≥ 95%；
- 禁止事实断言必须 100% 通过；
- 普通 Flash 的纠错成功率必须显著高于 Lite 重试同模型；
- 连续运行至少 24 小时，验证 429 冷却、用量持久化和跨日重置。

如果 Lite 达不到业务正确率门槛，即使额度巨大也只能做预筛，正式归档仍由普通 Flash 完成。

## 10. 分阶段实现与提交

1. **模型级额度账本**：新增滚动 RPM/TPM/RPD、预留与结算测试。
2. **模型拆分配置**：Gemini Lite/Quality 分开注册，移除两者的简单 round-robin。
3. **失败感知路由**：校验失败升级模型，网络失败同层切换，排除已尝试模型。
4. **状态统计**：一行日志增加 `lite/quality` 的 active、wait、429、TPM/RPD headroom。
5. **基准扩充与 Canary**：先独立 runner，再 5% → 25% → 100% 生产流量。
6. **Batch Adapter**：最后独立实现，不与实时 Client 混合。

每一步单独提交，并在前一步测试通过后再进入下一步。
