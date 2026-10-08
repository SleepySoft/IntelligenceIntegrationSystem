# Event V4 集成交接记录

更新时间：2026-10-08

## 当前结论

IIS 已具备开始集成 Event V4 的架构条件。Prompt 与事件结构此前存在的双份规则问题已经解决，
但生产主链尚未切换到 v4。

当前采用以下边界：

- `event_engine` 拥有独立的事件抽取模型、Prompt 和语义规则。
- Event Engine Registry 是谓词、Frame、角色和生命周期词值的唯一规则源。
- event_engine 可通过 `build_event_extraction_prompt(registry)` 独立使用。
- IIS 通过 `build_event_extraction_section(registry)` 组合事件抽取章节，只维护消息、分类、评分等外层要求。
- IIS 的 `ValuableIntelligenceV4` 使用嵌套的 `event_extraction: EventExtractionResult`，不再复制事件字段和谓词表。

## 已完成提交

### `648629a Add registry-driven event extraction contract`

- 新增 `event_engine.extraction`。
- 新增 `EventExtractionResult` 及其实体、事件、时间、限定词和关系模型。
- 新增独立 Prompt 与可组合 Prompt 章节构建器。
- PredicateSpec 增加抽取名称和边界说明。
- 三个领域包升级到 `1.2`。
- Prompt、JSON Schema 和语义校验均从显式 Registry 生成。
- 增加源码检出目录下的 event_engine 包路径桥接。

### `9e72edd Compose IIS v4 analysis with event extraction`

- 删除 IIS 内原有的第二份 `PREDICATE_SPECS`。
- `prompts_event_v4.py` 改为组合 Event Engine 抽取章节与 IIS 外层要求。
- v4 valuable 输出调整为：

```text
kind
message
classification
assessment
event_extraction
```

- `validate_analysis_result_v4()` 在 IIS 外层校验后，继续使用同一 Registry 校验事件语义。

## 当前验证结果

- Event Engine：58 项测试通过。
- IIS runtime、pipeline、extension 和 v4 schema：36 项测试通过。
- Registry、Prompt 谓词目录和 JSON Schema 均包含相同的 76 个谓词。
- 当前领域包版本：`news=1.2`、`industry=1.2`、`financial=1.2`。
- 独立事件 Prompt 约 15,281 字符；IIS 组合 Prompt 约 16,632 字符。

## 下一阶段实施顺序

### 1. 统一运行环境和安装方式

- 将 IIS 的最低 Python 版本统一为 3.11。
- 把 event_engine 纳入根项目正式安装流程，避免只依赖源码路径桥接。
- 在项目根目录运行 Event Engine 与 IIS 的联合测试。

### 2. 新增 Event V4 主链适配器

实现 `EventV4PipelinePorts`，不要继续在旧 `IISPipelinePorts` 中增加 v4 分支。适配器负责：

- 使用组合后的 v4 Prompt。
- 调用 `validate_analysis_result_v4()`。
- 区分 `valuable` 和 `non_intelligence`。
- 保留现有 AI 客户端获取、重试、背压、缓存状态和错误分类能力。
- 校验失败时将精简错误反馈给修正重试，而不是只重试相同请求。

### 3. 实现抽取结果到事件观察的转换

新增生产用转换服务，将 `EventExtractionResult` 转为 `EventRecord`：

- 为情报分配全局 UUID。
- 将 ENT 局部 ID 解析为全局实体 UUID。
- 预先为所有 E 局部事件分配全局 UUID。
- 转换 roles、event_location、qualifier.by 和事件关系。
- 保留 local entity/event ID 供来源追溯。
- 使用 `uuid5(intelligence_uuid, local_event_id)` 等稳定规则保证重试幂等。

`event_engine/examples/file_ingestion.py` 只可作为字段转换参考，不能直接作为生产适配器。

### 4. 增加实体解析与存储信封

需要新增：

- `EntityRepository` 和基础实体解析策略。
- 名称规范化、别名、国家代码及人工合并/拆分入口。
- `ArchivedIntelligenceV4` 存储模型。
- 低价值结果的存储信封。

归档信封至少保留 intelligence UUID、informant、raw data、AI 服务/模型、处理时间、评分、
event UUID 列表和 primary event UUID。

### 5. 保证多集合写入一致性

- 为实体、EventRecord 和情报文档建立唯一索引。
- 增加批量事件写入。
- 明确 MongoDB 事务或 outbox/补偿策略。
- 所有写入必须可重复执行，避免重试产生重复事件。
- 防止出现情报已归档但事件缺失，或事件已写入但情报归档失败。

### 6. 改造下游

- 评分器改为读取嵌套 `assessment.rate`；若使用 Pydantic dump，应保留中文 alias。
- 查询层改为组合 intelligence、events 和 entities。
- 向量层继续使用 message.title/brief/text，但解除对 v2 `ArchivedData` 的绑定。
- 翻译、统计、实体频率、动态图谱和前端渲染改用 v4 DTO。
- 不再维护 v1/v2 字段回退和双读分支。

### 7. 延后启用 CanonicalEvent 自动归并

第一阶段只登记和查询 EventRecord。以下能力完成后再启用自动归并：

- 全局实体解析稳定。
- MongoDB `CanonicalEventRepository` 完成。
- 候选召回索引完成。
- 多 worker 更新具备版本 CAS 或等价并发控制。
- 时间表达能够携带时区，或明确统一转换为 UTC。

## 第一里程碑完成标准

一条采集数据能够经过以下完整链路：

```text
collect
→ 组合 v4 Prompt
→ AnalysisResultV4 严格校验
→ 全局实体解析
→ EventRecord 转换
→ 情报与事件幂等写入 MongoDB
→ API 查询并返回 v4 展示 DTO
```

这一阶段不要求 CanonicalEvent 自动合并，也不需要兼容历史 v1/v2 数据。

## 工作区说明

记录本文件时，主仓库分支为 `SubSystem`。`IntelligenceCrawler` 和 `PyLoggingBackend` 子模块内
各有一个原有的未跟踪 `pyproject.toml`，本轮未修改、未提交。
