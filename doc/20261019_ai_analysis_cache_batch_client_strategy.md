# AI 分析缓存、批处理与 Client 选择记录

> 日期：2026-10-19  
> 状态：V48、付费 Client 配置与 Codex 额度保护已实施；异步 Batch 等后续方向尚未实现
> 范围：IIS Event V4 分析链路、AIClientCenter 调度、校验重试与历史积压处理

## 1. 决策摘要

1. Prompt Cache 按请求开头连续相同的前缀命中，不是把此前全部对话自动视为缓存。
   IIS 不应为了缓存保留不断增长的历史上下文；应把稳定协议放在前缀，把每篇文章及其他
   动态数据放到最后。
2. V47 已验证缓存友好布局；V48 沿用该布局并把 AI 时间输出缩减为
   `normalized + surface`，precision 由代码推导，approximate 默认 unknown。
3. 重试按错误类型和 Client 能力层级路由，不再把不同模型视为可以任意互换的同质 Client。
4. 只允许信息无损、结果唯一的本地归一化。缺失的语义字段不得用默认值伪造。
5. 实时主链和异步 Batch 分开：新情报走实时 Client，低优先级积压和历史重分析才进入
   Batch 状态机。
6. 付费主力候选已完成固定样本实测（见 §11）：**DeepSeek `deepseek-flash` 质量明显优于
   MiMo `mimo-v2.6-flash`**（谓词接地率 8/8 vs 1/8，最终成功率 3/3 vs 2/3），主次关系反转，
   DeepSeek 为付费主力；MiMo 作为低优先级备用（surface 接地不达标影响事实核验）。生产
   请求显式关闭 thinking，避免推理 Token 占满输出预算。
7. Codex CLI 保留为受保护的质量兜底和管理用途，默认保留 35% 的服务端窗口额度，
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

## 4. V47 缓存友好布局（已由 V48 继承）

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

- V47 已完成生产缓存验证；V48 继承相同消息布局。当前 V48 与同样缓存友好的
  V50 共同作为 `sampling=True` 版本进行生产灰度；V49/V47/V46 保留用于回归和历史对照。
- 不改变 `EXTRACTION_MODE_CORE_SUBSET_LEVELS_REASON` 和存储契约。
- 不把历史文章拼入会话；每篇文章仍是独立任务。
- 重试反馈属于动态信息，放在当前 user 消息末尾，不能污染系统前缀。
- `CURRENT_DATE` 放动态 user 区域，避免每天使系统前缀失效。
- Provider 有最小缓存块、缓存存活期或隔离键差异时，由 Client 能力配置表达，不能写死在
  Prompt 业务代码中。

### 4.3 验证结论与持续观测

消息构造测试已确认不同文章生成完全相同的 system 前缀，日期、元数据和正文只出现在最终
user 消息。2026-10-19 生产重启后的 DeepSeek 记录显示：首个冷请求缓存为 0，后续 V47
请求均命中 7,680 个输入 Token，在已观察样本中约占 81%–89%，实际缓存策略验证成功；
MiMo 当前响应未返回缓存明细。上线后仍持续按以下指标对照 V46：

- 至少包含近期成功样本、全部近期校验失败样本、多事件、无情报、中英文和长文；
- 比较首次校验通过率、最终通过率、业务断言、事实接地率、平均输入/输出 Token、缓存
  Token、P50/P95 延迟和单位成功归档成本；
- 语义质量不得低于 V46，出现回归时可通过保留的 V46 做固定样本复测；
- 继续观察 Provider 报告的缓存 Token、首次通过率和单位成功成本。

### 4.4 V48 时间最小契约

生产观察显示 Schema 重试主要不是 JSON 语法问题，而是时间对象：可追踪的 DeepSeek
失败响应中 94% 含时间结构错误。V48 保持 V47 的消息边界，只用一个短例子规定每个时间
槽写 `{"normalized":"2026-10-17","surface":"原文时间"}`。AI 不再输出 precision 和
approximate；桥接层从 normalized 推导 precision，无法由外部可靠判断的 approximate 保存
为 unknown/null。unknown 不参与精确事件合并或状态时序。

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
| MiMo `mimo-v2.6-flash` 按量 API | 低成本实时备用 | 已接入，优先级低于 DeepSeek；surface 接地率 1/8（§11） |
| DeepSeek `deepseek-flash` | 付费实时主力 | 已接入且优先级最高；生产关闭 thinking，错峰价有竞争力 |
| Codex CLI | 质量兜底、人工管理 | 已读取服务端 5 小时/周窗口；默认保留 35%，不跑积压 |
| NVIDIA 免费 Client | 停用 | 当前不参与生产调度 |
| Gemini 免费池 | 停用 | 当前不参与生产调度 |
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

- 5 小时窗口保留 35%（`primary_max_percent=65`），周窗口保留 10%
  （`secondary_max_percent=90`）；任一窗口达到上限即停止自动调度；
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

### 8.1 PoC 落地（2026-10-19）

`Scripts/batch_backlog_analysis.py`：从 cache 的 R 状态积压构建 JSONL（复用生产
`build_analyze_message` 与当前抽样 prompt，消息结构与实时完全一致）→ 上传/提交/轮询/
下载 → 用同一协议链校验并报告质量。`--dry-run` 只构建不提交，`--submit` 实交，
`--download <batch_id>` 拉回校验。实测构建 20 条 = 766KB，消息已是 v48 的
system+user 缓存友好布局（batch 价与缓存命中价可叠加）。
回注入库（跳过 intake 去重、从 analysis 后段续接归档）留待正式版。

## 9. 分阶段实施顺序

### 阶段 A：先消除无意义重试

1. 修复 `json_repair` 的 `warning` 污染业务 Payload。
2. 统计校验错误分类、每类重试成功率和最终采用的 Client/model。
3. 建立 protocol-version 专属无损 normalizer，并记录 JSON Patch。
4. 禁止对语义字段填通用默认值。

### 阶段 B：V47 与缓存验证

1. [已完成] 将稳定协议放到 system，动态日期、元数据和正文放到最终 user。
2. [已完成] V47 完成缓存验证；V48 继承其布局并成为生产 sampling 版本，保留 V47/V46 用于回归。
3. [已完成] 用消息构造测试锁定稳定前缀，并验证实际缓存策略。
4. [持续] 观察质量、缓存率和单位成功成本。

### 阶段 C：Client 能力分层与失败感知路由

1. 引入 Client capability/tier 配置。
2. 区分网络、限流、格式、Schema、语义和安全错误。
3. 同模型纠错一次后才升级 quality tier。
4. [已完成] MiMo 与 DeepSeek 用相同固定样本完成准入基准；DeepSeek 主用、MiMo 备用。
5. [已完成] Codex 受 35% 保留额度保护；免费 Client 暂停生产调度。

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
- Codex primary/secondary 窗口使用率及 35% 保留策略触发次数。

任何优化都不能只以“JSON 最终能通过”为验收条件。事件谓词、角色绑定、时间槽位、限定词
和事实接地质量必须与 V46 基线配对比较。

## 11. 付费候选实测补记（2026-10-19，3 固定样本 × v46）

两个按量 key 各跑 3 条固定样本（含历史问题样本），对比脚本：`Scripts/compare_prompt_v41_v42.py`，
分组定向隔离模型。结果文件：`_data/prompt_compare/compare_20261019T070919Z.json`（MiMo）与
`compare_20261019T072912Z.json`（DeepSeek）。

| 指标 | MiMo mimo-v2.6-flash | DeepSeek deepseek-flash |
| --- | --- | --- |
| 最终成功率 | 2/3 | **3/3** |
| 平均尝试 | 1.67 | 2.00（评估时 thinking 占用 8192 输出预算并造成截断） |
| 谓词接地率 | **1/8**（surface 翻译/改写，不达标） | **8/8** |
| 时间接地 | 1/1 | 4/4 |
| 角色/议题结构 | 正确（topic 未再填人物） | 正确 |
| 明显错误 | 1 例 phase=ongoing 语义规则失败（3 次重试不过） | 无 |
| 平均耗时 | 124s | 125s |

结论：**DeepSeek deepseek-flash 质量明显更优**（接地率满分、语义规则零失败），
MiMo 单价低但 surface 接地不达标会影响事实核验，暂不推荐作为主力。
生产接入对 DeepSeek 和 MiMo 均显式设置 `thinking.type=disabled`，因此先保留 8192 输出上限，
避免为不可见推理付费；若生产记录仍出现截断，再根据输出分布单独调整。当前 DeepSeek 为
首选、MiMo 为备用，免费 Gemini/NVIDIA Client 均停用。API Key 只通过权限为 `0600` 的
部署环境文件注入，不写入仓库或主配置。
