# AI 分析缓存、批处理与 Client 选择记录

> 日期：2026-10-19  
> 状态：调查结论与实施方向，除文中明确标为“现状”的内容外，尚未实现  
> 范围：IIS Event V4 分析链路、AIClientCenter 调度、校验重试与历史积压处理

## 1. 决策摘要

1. Prompt Cache 按请求开头连续相同的前缀命中，不是把此前全部对话自动视为缓存。
   IIS 不应为了缓存保留不断增长的历史上下文；应把稳定协议放在前缀，把每篇文章及其他
   动态数据放到最后。
2. 新建非生产抽样的 V47 候选 Prompt，保持 V46 的业务契约不变，只调整消息边界和稳定
   前缀。通过影子链路和固定样本 A/B 后再决定是否替换 V46。
3. 重试按错误类型和 Client 能力层级路由，不再把不同模型视为可以任意互换的同质 Client。
4. 只允许信息无损、结果唯一的本地归一化。缺失的语义字段不得用默认值伪造。
5. 实时主链和异步 Batch 分开：新情报走实时 Client，低优先级积压和历史重分析才进入
   Batch 状态机。
6. 付费主力首选候选为 MiMo `mimo-v2.6-flash` 按量 API；DeepSeek
   `deepseek-flash` 作为质量/可用性对照和错峰备用。最终主次关系必须由 IIS 固定样本基准
   决定，不能只按价格决定。
7. Codex CLI 保留为受保护的质量兜底和管理用途，继续默认保留 20% 的服务端窗口额度，
   不承担批量吞吐。
8. MiMo Token Plan 不用于 IIS 后端自动分析；官方条款明确限制非 Coding 自动脚本和自定义
   应用后端。IIS 只能考虑普通按量 API 与 Batch API。

## 2. Prompt Cache 的准确语义

可以把一次请求抽象为：

```text
固定前缀 A
固定前缀 B
动态正文 C
固定后缀 D
```

下一次请求的正文 C 发生变化时，通常只能复用 C 以前完全相同的 A+B；C 后面的 D 即使
文本相同，也不再属于连续相同前缀。因此缓存原则是：

- 稳定、体积大的内容尽量靠前；
- 每次变化的内容尽量靠后；
- 不为提高命中率累积旧文章、旧回复或无关会话；
- Prompt 版本、系统日期、随机示例等变化都会形成新的缓存边界；
- 缓存只复用计算，不改变模型看到的 Token，也不会单独导致输出质量下降。

真正可能导致质量下降的是 Prompt 过长、规则冲突、正文处在大量规则中间以及不相关历史
干扰。缓存友好和保持大上下文是两件不同的事。

## 3. IIS 当前观测基线

2026-10-19 对本机近期 `conversation/` 记录抽样得到：

| 指标 | 观测值 |
| --- | ---: |
| 成功响应样本 | 196 |
| 平均输入 Token | 7,947 |
| 输入中位数 / P90 | 8,209 / 9,420 |
| 平均输出 Token | 795 |
| 输出中位数 / P90 | 794 / 1,495 |
| 总缓存输入占比 | 约 23.3% |
| NVIDIA 样本缓存占比 | 约 30.4% |
| Codex CLI 样本缓存占比 | 未观测到有效命中 |

一次当前运行中记录到 13 次分析尝试、8 次完成、5 次 V4 校验失败，相当于约 1.625 次
调用才能得到一个成功结果。这只是小样本运行快照，不应当作长期固定重试率，但已经说明
“减少无意义重试”与“降低单次价格”同样重要。

V46 的模板约 22,064 字符，粗略约 6,895 Token。当前 `{{CONTENT}}` 位于模板前部，正文
以后仍有约 21,929 字符的稳定协议。对按公共前缀缓存的 Provider 来说，这个布局使绝大
部分稳定协议无法命中缓存。

## 4. V47 缓存友好候选

### 4.1 消息布局

V47 只改变消息组织，不主动改变 V46 业务规则：

```text
system
  角色、输出总则
  IIS 顶层字段要求
  Event Engine 核心子集协议
  谓词与限定词目录
  固定 JSON 示例

user
  当前参考日期
  当前正文
  相似历史消息
```

这样稳定协议成为可缓存前缀，正文也更接近生成位置。不要在 user 后再追加大段固定
Schema，否则正文差异会再次切断后续缓存。

### 4.2 边界

- V47 初始注册为 `sampling=False`。
- 不改变 `EXTRACTION_MODE_CORE_SUBSET_LEVELS_REASON` 和存储契约。
- 不把历史文章拼入会话；每篇文章仍是独立任务。
- 重试反馈属于动态信息，放在当前 user 消息末尾，不能污染系统前缀。
- `CURRENT_DATE` 放动态 user 区域，避免每天使系统前缀失效。
- Provider 有最小缓存块、缓存存活期或隔离键差异时，由 Client 能力配置表达，不能写死在
  Prompt 业务代码中。

### 4.3 上线门槛

使用相同文章、相同模型对 V46/V47 做配对测试：

- 至少包含近期成功样本、全部近期校验失败样本、多事件、无情报、中英文和长文；
- 比较首次校验通过率、最终通过率、业务断言、事实接地率、平均输入/输出 Token、缓存
  Token、P50/P95 延迟和单位成功归档成本；
- V47 不得仅凭缓存率上升进入生产，语义质量必须不低于 V46；
- 先走 shadow，再按 5% → 25% → 100% 灰度。

## 5. 校验失败与本地机械修复

### 5.1 已经由服务端负责的内容

核心子集协议已经把以下机械内容排除在 AI 输出之外，或在桥接阶段确定性补齐：

- `schema_version`；
- 实体、事件和限定词局部 ID；
- `primary_event_id`；
- 已登记谓词的 `frame`；
- 限定词 `scope`；
- 事件关系数组位置到事件 ID 的转换；
- `normalized` 时间文本的空格、秒、时区和精度格式修复；
- RATE 等级词到数字的映射；
- 顶层 `TIME`、`LOCATION`、`GEOGRAPHY`、`PEOPLE`、`ORGANIZATION`；
- UUID、APPENDIX 和归档元数据。

因此后续不应建立一个“看到缺字段就填默认值”的通用修复器。

### 5.2 允许的无损归一化

本地修复必须同时满足：信息无损、转换结果唯一、规则与 Prompt 协议版本绑定。允许的例子：

- JSON 语法修复；
- 删除解析器产生而非模型业务输出的诊断字段；
- 空白和明确的容器形态归一化；
- 有唯一映射的旧字段别名或旧层级；
- 已有日期值的格式截断、剥离时区，无法修复时把允许为空的 `normalized` 降为 null；
- 对有硬字符上限的展示文本做确定性裁剪，但必须记录指标，避免长期掩盖模型退化。

当前 `json_repair` 路径会把 `warning="Json repaired."` 放进业务对象，随后被严格 V4 模型
当作额外字段拒绝。这属于明确的本地实现缺陷：诊断信息应写观测元数据，不应进入领域
Payload。

### 5.3 不允许机械补齐的内容

- 缺失的 `approximate`：它表达时间是否近似，是语义判断，不能统一设为 false；
- 开放谓词 `id=null` 时缺失的 `frame` 或 `gloss`；
- Registry 标注 agency=unknown 时的具体 `agency`；
- 实体类型、角色绑定、时间槽位、限定词、事件关系；
- 没有一一对应证明的生命周期枚举转换。

需要特别说明：当前 `SubsetTimeExpression` 仍要求 AI 输出 `precision`、`approximate` 和
`surface`；桥接层只负责修复 `normalized` 的格式。因此“缺少 approximate”在当前契约下
仍然是语义输出不完整，而不是服务端已经接管的字段。

### 5.4 推荐处理链

```text
原始响应
  → JSON 语法修复
  → protocol/prompt-version 专属无损归一化
  → 严格模型与 Registry 校验
  → 错误分类
  → 同层重试 / 质量升级 / 延迟 Batch / 最终失败
```

每次归一化都记录输入哈希、Prompt 版本和 JSON Patch；禁止静默改变语义。

## 6. Client 差异与级联调度

当前 Event V4 在失败后主要按 Client 名称规避并重新获取 Client。这能避开坏连接，却可能
使一次结构校验失败在不同 Provider、不同模型方言之间来回跳转。

AIClientCenter 应增加显式能力档案：

```text
ClientCapabilityProfile
  provider
  model
  tier: primary | quality | reserve
  prompt_protocols
  structured_output_mode
  supports_prompt_cache
  supports_batch
  supports_usage
  latency_class
  cost_profile
```

重试上下文至少携带：

- `attempted_client_names`；
- `attempted_models`；
- `prompt_version` 和 `protocol`；
- `failure_category`；
- `desired_tier`；
- 输入哈希和幂等 UUID。

推荐路由：

| 失败类型 | 下一步 |
| --- | --- |
| 网络、5xx、短时超时 | 同模型或同能力层换健康 Client |
| 429 | 遵守冷却时间，切换有独立额度的同层 Client |
| JSON 语法错误 | 先本地修复，不立即消耗第二次调用 |
| 无损兼容错误 | 运行版本专属 normalizer 后重新校验 |
| 语义字段缺失、枚举不支持 | 原模型携带校验摘要重试一次 |
| 同模型持续语义失败 | 升级 quality tier，不回到已失败模型 |
| 长期积压、低时效任务 | 持久化后进入 Batch |
| 内容安全拒绝或输入本身损坏 | 不盲目跨 Provider 循环 |

## 7. Client 调查与当前选择

以下价格和产品能力是 2026-10-19 的调查快照，配置前必须重新核对官方文档。

| Client / 服务 | 当前定位 | 选择结论 |
| --- | --- | --- |
| MiMo `mimo-v2.6-flash` 按量 API | 低成本实时主力候选 | 优先接入并基准；支持低价缓存输入和独立 Batch |
| DeepSeek `deepseek-flash` | 实时对照、错峰备用或质量兜底候选 | 保留；非高峰价格有竞争力，质量胜出时可反转主次 |
| Codex CLI | 质量兜底、人工管理 | 已可读取服务端 5 小时/周窗口；默认保留 20%，不跑积压 |
| NVIDIA 免费 Client | best-effort 补充与基准来源 | 可继续使用，但不作为有 SLA 的唯一主力 |
| Gemini 免费池 | 可用时的配额补充 | 按模型独立额度管理，需基准后分 primary/quality，不做简单轮换 |
| Gemini 付费 Flash | 当前服务器链路待解决 | 暂不进入生产；访问失败原因尚未证明，不能简单归因于 Cloudflare |
| MiMo Token Plan | Coding 工具订阅 | 不选：IIS 属于自动脚本/应用后端，不符合官方允许范围 |

### 7.1 MiMo 按量 API

当前官方人民币价格：

| 模型/模式 | 输入缓存命中 | 输入未命中 | 输出 |
| --- | ---: | ---: | ---: |
| V2.6 Flash 实时 | ¥0.02/M | ¥1.00/M | ¥2.00/M |
| V2.6 Flash Batch | ¥0.01/M | ¥0.50/M | ¥1.00/M |

Batch 为实时价格的 50%，支持 `mimo-v2.6-flash` 和 `mimo-v2.6-pro`，采用异步任务，
不支持流式响应，输入和结果文件默认保留 30 天。参考：

- [MiMo API 价格](https://mimo.mi.com/docs/en-US/pricing)
- [MiMo Batch API](https://mimo.mi.com/docs/en-US/quick-start/usage-guide/text-generation/batch-api)

普通 IIS 自动分析必须使用按量 API Key。MiMo 官方明确规定 Token Plan 只能用于编程工具，
禁止用于明显非 Coding 的自动脚本和自定义应用后端：

- [MiMo Token Plan 使用范围](https://mimo.mi.com/docs/en-US/price/token-plan)

### 7.2 DeepSeek

当前 `deepseek-flash` 人民币价格：

| 时段 | 输入缓存命中 | 输入未命中 | 输出 |
| --- | ---: | ---: | ---: |
| 空闲时段 | ¥0.02/M | ¥1.00/M | ¥4.00/M |
| 高峰时段 | ¥0.04/M | ¥2.00/M | ¥8.00/M |

北京时间工作日 09:00–12:00、14:00–18:00 为高峰，其余时段以及周末和法定节假日为
空闲时段。参考：[DeepSeek 模型与价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)。

DeepSeek 的优势是实时错峰无需实现 Provider Batch 状态机；缺点是输出单价高于 MiMo
Flash。它应作为真实质量对照，而不是仅因单价接近就与 MiMo 随机轮换。

### 7.3 Codex CLI

Codex Client 已通过 app-server `account/rateLimits/read` 取得 primary/secondary 服务端窗口
百分比，5 小时边界不再靠调用次数估算。当前策略：

- `reserve_percent=20`，达到 80% 使用量后停止自动调度；
- 服务端窗口快照优先，session 中的 rate limit 事件作为降级来源；
- 缺少快照时不把 Client 永久判坏，但应限制其进入批量路径；
- 用于 quality tier、人工调试和管理工作，不作为便宜的持续吞吐资源。

### 7.4 成本估算基线

按“每 1,000 个成功分析、每个成功结果平均 1.625 次调用、每次 7,947 输入和 795 输出”
估算：

| 方案 | 当前布局近似无前缀命中 | 85% 输入缓存命中 |
| --- | ---: | ---: |
| MiMo V2.6 Flash 实时 | 约 ¥15.50 | 约 ¥4.74 |
| MiMo V2.6 Flash Batch | 约 ¥7.75 | 约 ¥2.37 |
| DeepSeek Flash 空闲时段 | 约 ¥18.08 | 约 ¥7.32 |
| DeepSeek Flash 按一周时段加权 | 约 ¥21.85 | 约 ¥8.85 |
| DeepSeek Flash 高峰时段 | 约 ¥36.16 | 约 ¥14.65 |

这些数字用于比较方向，不是预算承诺。正文长度、重试率、推理输出、Provider 计费规则和
缓存最小块都会改变实际费用。第一优先级仍是降低 1.625 的重试放大率。

## 8. Batch 的含义与 IIS 落点

Batch 不是在一个 Prompt 中塞入多篇文章，而是将很多相互独立的请求写入 JSONL：

```text
生成 JSONL（每行一个 request + custom_id）
  → 上传文件
  → 创建异步 Batch Job
  → 持久化 job_id 并轮询
  → 下载成功/失败结果文件
  → custom_id 对应回文章 UUID
  → 复用同一归一化、校验和归档入口
```

推荐与现有 LIVE/REPLAY 分流组合：

```text
LIVE 新情报
  → 实时 primary
  → 必要时实时 quality

REPLAY / 低时效积压
  → 持久化延迟队列
  → 达到数量、Token 或等待时间阈值
  → Provider Batch
  → 结果回注统一校验入口
```

Batch 至少需要这些状态：

```text
pending → file_created → submitted → polling
        → completed → downloaded → validating → injected
        → partial_failed / failed / expired / cancelled
```

必须持久化 `job_id`、Provider、模型、Prompt 版本、协议、输入哈希、每条 UUID/custom_id、
提交时间和结果文件状态。结果回注必须幂等，服务重启后不得重复提交或重复归档。

## 9. 分阶段实施顺序

### 阶段 A：先消除无意义重试

1. 修复 `json_repair` 的 `warning` 污染业务 Payload。
2. 统计校验错误分类、每类重试成功率和最终采用的 Client/model。
3. 建立 protocol-version 专属无损 normalizer，并记录 JSON Patch。
4. 禁止对语义字段填通用默认值。

### 阶段 B：V47 与缓存验证

1. 将稳定协议放到 system，动态正文放到最终 user。
2. 注册 V47 为非 sampling 版本。
3. 固定数据集 + shadow 配对测试。
4. 依据质量、缓存率和单位成功成本决定是否灰度。

### 阶段 C：Client 能力分层与失败感知路由

1. 引入 Client capability/tier 配置。
2. 区分网络、限流、格式、Schema、语义和安全错误。
3. 同模型纠错一次后才升级 quality tier。
4. MiMo 与 DeepSeek 用相同固定样本完成准入基准。
5. Codex 继续受 20% 保留额度保护。

### 阶段 D：异步 Batch

1. 建立持久化 Batch Job 与 item 状态机。
2. 先用于 Prompt 回归和手工历史重分析。
3. 再接 REPLAY/失败积压，不接 LIVE 主链。
4. 验证重启恢复、部分成功、过期、取消和幂等回注。

## 10. 验收指标

- 首次 V4 校验通过率和三次内最终通过率；
- 每成功归档平均 AI 调用次数；
- 输入缓存命中 Token 比例；
- 每成功归档的输入、输出 Token 和人民币成本；
- 各 Provider/model 的 API 成功率、语义纠错成功率和 P95 延迟；
- normalizer 修改率及每条规则的后续校验成功率；
- Batch 等待时间、部分失败率、重复回注数；
- Codex primary/secondary 窗口使用率及 20% 保留策略触发次数。

任何优化都不能只以“JSON 最终能通过”为验收条件。事件谓词、角色绑定、时间槽位、限定词
和事实接地质量必须与 V46 基线配对比较。
