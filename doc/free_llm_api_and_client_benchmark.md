# 免费 LLM API 资源池与 Client 基准验证方案

更新时间：2026-10-10

## 1. 结论

IIS 可以通过多个服务商、多个模型组成低成本 AI Client 资源池，但不能把“创建多个
API Key”当成扩容手段。Groq 按组织、Gemini 按 Project、SiliconFlow 按账号计算限额；
同一主体下增加 Key 通常只便于隔离和吊销，不增加总额度。

当前建议的资源池顺序为：

```text
SiliconFlow 免费模型
→ ModelScope API-Inference
→ Gemini Flash-Lite / Flash
→ Groq GPT-OSS / Qwen
→ Cloudflare Workers AI
→ OpenRouter 免费模型池
→ 有明确费用上限的低价付费 Client
```

NVIDIA NIM 免费端点只适合作为研发和测试资源。官方条款明确说明，开发者计划下的托管
端点用于原型、研究、开发和测试；生产使用需要 NVIDIA AI Enterprise 许可。

免费服务的模型、额度和条款变化很快。本文记录的是调查时点的官方信息，程序不应把这些
额度硬编码为永久事实；调度应优先使用响应限额头、错误码和本地观测。

## 2. 可用资源汇总

### 2.1 SiliconFlow

- 实名认证后可以调用平台当前标记为免费的模型，免费模型账单费用为 0。
- Rate Limit 按用户账号而不是 API Key 计算。
- 不同模型分别限流，因此同账号轮换多个免费模型可以增加总体吞吐。
- 具体免费模型和每个模型的 RPM/TPM 应从模型广场实时读取。
- OpenAI 兼容，IIS 已有 `create_siliconflow_client()`。

官方资料：

- https://docs.siliconflow.cn/docs/userguide/faqs/rate-limit-and-upgradation
- https://docs.siliconflow.cn/docs/userguide/quickstart

适合承担低成本主力流量，但必须持续测量 Event V4 校验成功率；“接口成功”不代表小模型
能够稳定生成符合 IIS 契约的结果。

### 2.2 ModelScope 魔搭

旧政策是每日 2,000 次 API-Inference 调用。2026 年 8 月上线魔粒体系后，当前基础额度为
每日 200 魔粒；API-Inference 的典型消耗为：

| 模型类别 | 典型消耗 | 基础额度对应调用量 |
| --- | ---: | ---: |
| 轻量模型 | 0.5 魔粒/次 | 约 400 次/日 |
| 主流模型 | 1 魔粒/次 | 约 200 次/日 |
| 旗舰模型 | 2 魔粒/次 | 约 100 次/日 |

模型的实际消耗和在线状态以模型页为准。接口兼容 OpenAI，IIS 已有
`create_modelscope_client()` 和模型轮换能力。

官方资料：

- https://modelscope.cn/docs/magicube/intro
- https://www.modelscope.cn/posts/435591
- https://www.modelscope.cn/docs/model-service/API-Inference/intro

注意：`_config/ai_client_config_example.py` 和
`AIClientCenter/config/example.py` 当前仍保留“每日 2,000 次”的旧注释，后续更新配置样例时
应改为动态政策提示，不再写死调用数。

### 2.3 Google Gemini API

- `gemini-2.5-flash-lite`、`gemini-2.5-flash` 等模型的免费层输入、输出价格为 0。
- 免费层内容可能被 Google 用于改进产品，不应提交敏感或不允许外发的数据。
- 限额按 Project 而不是 API Key 计算，维度通常包括 RPM、TPM 和 RPD。
- 官方不再保证一张长期固定的免费限额表，应从 AI Studio 查看实际限额。
- IIS 已有原生 Gemini adapter 和 OpenAI 兼容入口。

官方资料：

- https://ai.google.dev/gemini-api/docs/pricing
- https://ai.google.dev/gemini-api/docs/rate-limits
- https://ai.google.dev/gemini-api/docs/openai

Flash-Lite 适合初筛，Flash/Pro 适合高价值情报复核候选；最终分层必须由 IIS 自己的 V4
基准结果决定，而不是仅依据厂商榜单。

### 2.4 Groq

调查时免费层包含以下适合文本分析的模型：

| 模型 | RPM | RPD | TPM | TPD |
| --- | ---: | ---: | ---: | ---: |
| `openai/gpt-oss-120b` | 30 | 1,000 | 8,000 | 200,000 |
| `openai/gpt-oss-20b` | 30 | 1,000 | 8,000 | 200,000 |
| `qwen/qwen3.8-27b` | 30 | 1,000 | 8,000 | 200,000 |

限额按 Organization 计算，缓存命中的 Token 不计入 Rate Limit。三个模型的日 Token
额度可以分别利用，但同组织增加 API Key 不会叠加额度。Groq 兼容 OpenAI API。

若一次 V4 分析平均消耗 12K 输入和 2K 输出，三个模型合计 600K TPD 理论上仅对应约
42 次分析；实际还会受到单次上下文、TPM 和输出长度影响。因此它更适合作为高速备用池。

官方资料：

- https://console.groq.com/docs/rate-limits
- https://console.groq.com/docs/openai
- https://console.groq.com/docs/billing-faqs

### 2.5 Cloudflare Workers AI

- 免费计划每天提供 10,000 Neurons，00:00 UTC 重置。
- 支持 OpenAI 兼容 Chat Completions 和 Embeddings。
- `@cf/ibm-granite/granite-4.0-h-micro` 每百万输入/输出分别消耗 1,542/10,158 Neurons。
- 按每篇 12K 输入、2K 输出估算，一次约 38.82 Neurons，理论上约 257 篇/日；这不代表
  模型一定通过 Event V4 校验。
- 可使用 `rejectIfBusy` 快速失败，适合交给 Client Manager 切换上游。

官方资料：

- https://developers.cloudflare.com/workers-ai/platform/pricing/
- https://developers.cloudflare.com/workers-ai/configuration/open-ai-compatibility/
- https://developers.cloudflare.com/workers-ai/platform/errors/

### 2.6 OpenRouter

- 免费模型使用 `:free` 后缀，也可以用 `openrouter/free` 自动选择可用免费模型。
- 未充值账号：50 请求/日、20 RPM。
- 账户一次性加入至少 10 美元 Credits 后：免费模型提升到 1,000 请求/日，仍为 20 RPM。
- 免费模型集合和上游容量会变化，失败请求也计入每日请求数。
- 免费端点可能有较小上下文或不同数据策略，必须检查模型和 Provider 属性。

官方资料：

- https://openrouter.ai/collections/free-models/
- https://openrouter.ai/blog/tutorials/how-to-get-the-lowest-cost-llm-inference-on-openrouter/
- https://openrouter.ai/docs/api-reference/limits

OpenRouter 自带跨 Provider 路由，但 IIS 仍需记录最终模型、实际 Provider、响应耗时和 V4
校验结果，否则故障切换会掩盖具体上游的真实表现。

### 2.7 Mistral

Mistral Studio 提供默认 Free mode，API 受 RPS、TPM 和月 Token 总量限制，面向评估与
原型。具体额度由账户后台 Limits 页面给出，不宜在配置中写死。限额按 Organization
生效；生产使用、提高额度或 Zero Data Retention 应切换付费模式。

官方资料：

- https://help.mistral.ai/en/articles/698531-why-am-i-hitting-api-rate-limits-and-how-do-i-increase-them
- https://mistral.ai/pricing/

### 2.8 NVIDIA NIM

NVIDIA Developer Program 提供免费的托管 NIM API Endpoint，并使用
`https://integrate.api.nvidia.com/v1` 的 OpenAI 风格接口。IIS 已有
`create_nvidia_client()`。

免费访问仅用于原型、研究、开发、实验和测试，不包括面向真实用户的生产业务。生产使用
需要 NVIDIA AI Enterprise 许可。因此可以纳入基准池和人工调试池，不能作为正式归档
主链的长期免费容量。

官方资料：

- https://docs.api.nvidia.com/nim/docs/product
- https://docs.api.nvidia.com/nim/reference/llm-apis
- https://build.nvidia.com/models

### 2.9 一次性试用和低价值来源

- 阿里云百炼：华北 2 新用户通常按模型获得免费额度，有效期 90 天；许多文本模型为
  100 万 Token。适合短期验证，不是持续资源。部分模型快照拥有独立额度。
  - https://help.aliyun.com/zh/model-studio/new-free-quota
  - https://help.aliyun.com/zh/model-studio/model-pricing
- Cerebras：当前主要是注册后的 5 美元起始 Credit，属于一次性试用。
  - https://www.cerebras.ai/pricing
- Hugging Face Inference Providers：普通免费用户的月度 Credit 很小，不适合批量处理。
  - https://huggingface.co/docs/inference-providers/en/pricing
- Xiaomi MiMo：已于 2026-01-26 启动计费，新用户体验金不属于持续免费额度。
  - https://mimo.mi.com/docs/zh-CN/news/previous-news/billing
- LongCat：旧免费模型已退役，当前主服务按 Token Pack 或实际用量计费。
  - https://longcat.chat/platform/docs/ChangeLog.html

## 3. IIS 当前验证能力盘点

目前没有一套能够回答“这个服务是否适合承担 IIS Event V4 分析”的完整基准机制。

| 现有能力 | 能回答的问题 | 不能回答的问题 |
| --- | --- | --- |
| Client 主动健康检查 | 能否返回包含 `OK` 的基本响应 | V4 格式能力、正文理解、吞吐、稳定性、成本 |
| AIClientCenter `/playground` | 指定 Client 的一次裸 Prompt 是否可调用 | 正式 Prompt 拼接、V4 校验、业务评分质量、批量对比 |
| IIS `/debug/intelligence` | 正式 V4 Prompt 和校验链能否完成一次分析 | 固定指定 Client、重复实验、跨 Client 报告、统计显著性 |
| Client 状态日志 | 调用时间段、成功/失败和部分 Token 用量 | 按测试集聚合的质量指标和能力矩阵 |

因此，健康检查只能影响“现在能否调度”，不能作为“模型是否胜任情报分析”的依据。

## 4. 建议新增：Client Benchmark Runner

### 4.1 目标

新增一个独立、显式触发、不会进入生产队列和业务数据库的基准运行器：

1. 指定一个或多个 Client，而不是让正常调度器随机选择。
2. 使用生产 Event V4 相同的 Prompt 获取、占位符拼接、响应解析和 Pydantic 校验。
3. 使用固定测试集，对每个 Client/模型重复运行并横向比较。
4. 结果写入独立目录，不写 cache、archive、low_value、事件、实体、outbox、翻译或向量库。
5. 不把基准失败直接污染生产健康状态；网络、鉴权和限流故障单独统计。

建议命令入口：

```powershell
python Scripts/benchmark_ai_clients.py `
  --config _config/ai_client_config.py `
  --suite _config/ai_benchmark_suite.json `
  --clients "SiliconFlow Free Qwen,Groq GPT OSS 120B" `
  --repeat 3
```

默认只显示执行计划；真实请求必须显式增加 `--execute`，避免误耗额度。

### 4.2 测试集

建议至少覆盖：

- 短新闻、长正文和接近上下文上限的正文；
- 中文、英文及需要翻译的其他语言；
- 有价值情报、低价值新闻、非情报文本；
- 单事件、多事件、角色复杂事件；
- 时间/地点模糊、实体别名和相互矛盾的信息；
- 已知容易造成 JSON 截断、枚举错误或 Registry 语义错误的历史失败样本；
- 敏感内容，但只能使用允许发送给第三方的脱敏样本。

每个样本至少包含：

```json
{
  "id": "multi-event-en-001",
  "tags": ["english", "multi_event", "long"],
  "collected_data": {},
  "expected": {
    "route": "valuable",
    "required_event_predicates": [],
    "forbidden_claims": []
  }
}
```

`collected_data` 必须通过 `CollectedDataV4` 校验。`expected` 只描述稳定、客观的断言，不用
一份完整标准答案强迫模型逐字一致。

### 4.3 指标

每次调用记录：

- Client、服务地址、请求模型、响应模型；
- Prompt 版本、Prompt 内容哈希、样本 ID、重复序号；
- 排队时间、首 Token 时间（支持时）、总耗时；
- 输入、输出、推理、缓存 Token；
- HTTP/API 成功，是否超时、限流、鉴权失败或服务端失败；
- 是否能提取 JSON、是否需要 `json_repair`；
- `AnalysisResultV4` Pydantic 校验和 Event Registry 语义校验结果；
- valuable/non-intelligence 路由是否符合预期；
- 必需事件谓词召回、禁止事实误报、语言要求是否满足。

聚合报告至少包含：

| 维度 | 建议统计量 |
| --- | --- |
| 可用性 | 请求成功率、429/5xx/超时率 |
| 契约能力 | JSON 提取率、V4 校验通过率、无需修复通过率 |
| 业务能力 | 路由准确率、事件召回率、禁止事实误报率 |
| 性能 | P50/P95 总耗时、首 Token 时间、有效 Token/s |
| 成本 | 每成功样本 Token、估算费用、每个 V4 合格结果的费用 |
| 稳定性 | 多次重复结果一致率、连续失败次数 |

核心排序指标应是“每个 V4 合格结果的成本和耗时”，而不是单纯 Token 单价或 HTTP
成功率。一个免费但只有 40% V4 通过率的模型，实际吞吐可能低于稳定的低价模型。

### 4.4 结果存储

建议使用 JSONL 保存逐次结果，并生成 Markdown/JSON 汇总：

```text
_data/ai_benchmarks/
  20261010T120000Z/
    manifest.json
    results.jsonl
    summary.json
    summary.md
    failures/
```

失败样本只保存有界、脱敏的 Prompt、原始响应、解析阶段和校验错误。API Key、请求头和
账号信息不得进入结果文件。目录属于运行时数据，不提交 Git。

### 4.5 与生产调度的关系

第一阶段仅生成报告，不自动改变生产路由。积累足够样本后，再允许管理员将报告转成显式
能力配置。生产调度可以在现有优先级、预算和 group limit 之外增加能力过滤，但一次临时
基准结果不得自动改变生产配置；自动路由至少需要滚动窗口、最小样本量和人工确认。

## 5. 推荐实施顺序

1. 给 IIS 人工情报调试增加“固定 Client”选择，使同一正文和同一 Prompt 可以逐 Client
   对比；仍使用 transient V4 链路且不写业务库。
2. 抽取无 Web 依赖的 benchmark runner，复用人工调试使用的 V4 分析组件。
3. 建立一小套可提交的脱敏样本，并为 runner 编写完全离线的 fake-client 测试。
4. 输出 JSONL 和 Markdown 汇总，验证 Token、错误分类和 P50/P95 计算。
5. 实测免费服务；样本量足够后再设计能力感知调度，避免提前耦合进主链。

首版不应实现自动注册账号、跨账号规避额度、后台定时消耗免费额度或根据一次测试自动封禁
Client。这些行为可能违反服务条款，也容易让基准流量与正式情报争抢有限资源。
