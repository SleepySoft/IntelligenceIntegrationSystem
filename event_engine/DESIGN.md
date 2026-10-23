# Event Core Engine 设计与需求规格

> 实现布局更新：数据模型、规则定义、查询和存储协议统一位于 `schema/`，
> 注册、校验、通用算法与编排位于 `core/`；`domain/` 和 `ir/` 已移除。
> 新闻、产业、金融的配置、专用声明及算法归入 `domains/`；选择的包组合注册，
> 新闻专用分析位于 `domains/news/analyzer.py`。`analysis/`、`configs/`、`extensions/` 已移除。
> `integration/` 已移除；文件注入工具仅用于演示/测试，位于 `examples/file_ingestion.py`。
> 本文的分析/整合/查询职责仍适用；实际接入、分析和匹配共享同一 Registry。
> 身份证据门槛、区分角色/身份属性比较、区间时间比较和生命周期投影已实现。
> 状态投影保留主张、来源、有效时间、支持观察与争议；仍不承担跨来源事实真值裁决。
> 具体执行规则与兼容性见 [SEMANTIC_DECISIONS.md](SEMANTIC_DECISIONS.md)。

## 1. 目标

构建一个只依赖 Event V4 结构的独立事件引擎。引擎能够脱离 IIS 单独安装、测试和运行，并可由任意系统通过 Event 对象或查询端口调用。

核心能力：

1. 对单个 Event 提取参与者、行动者、承受者、地点、时间、状态和数值属性。
2. 对 Event 集合进行过滤、分组、时间线、实体行为分析、战争区域提取和事件关系分析。
3. 对持久存储执行领域查询，并将候选结果交给分析层做二次计算。
4. 使用可解释的谓词身份规则，将同一现实事件的多次 Event 观察聚合为 CanonicalEvent。
5. 提供 MongoDB 查询实现，同时允许以后接入内存、PostgreSQL、Elasticsearch 或图数据库。

## 2. 边界与存储协议

### 2.1 IIS 侧

AI 的事件抽取部分为 `EventExtractionResult`，可以独立使用，也可以嵌入 IIS 等外部结果。写入时：

1. 为 `event_extraction.events[]` 中每个 Event 分配全局 UUID。
2. Event 独立写入事件存储。
3. Event 记录 `intelligence_uuid`、原局部 `E<number>` 和全局化后的实体绑定。
4. 外部持久化对象仅保存 Event UUID 列表与 `primary_event_uuid`，不重复保存局部事件对象。
5. 消息级字段，例如评分、正文和分类，留在外部包装模型，不进入事件引擎。

### 2.2 Event 引擎侧

核心引擎不读取外部包装模型，不了解新闻、正文或相似消息。抽取层只负责编译事件 Prompt、校验
`EventExtractionResult`，随后由接入层转换为核心对象。核心只依赖：

- `EventRecord`
- `EventQuery`
- `EventRepository` 协议
- `PredicateSpec` 与 `IdentitySpec`
- 可选的 `CanonicalEvent`

这保证引擎可完全脱离 IIS。

## 3. 三层架构

### 3.1 分析职责：`event_engine.core` 与可选 `event_engine.domains`

纯内存、无 I/O、确定性计算：

- 单事件要素提取。
- 语义角色组映射。
- 实体作为行动者或承受者的事件分析。
- 战争相关事件和战争区域统计（可选 `domains.news.analyzer`，不进入通用 core）。
- 时间线构造。
- Event 与 CanonicalEvent 的可解释匹配。
- CanonicalEvent 当前状态重算。

### 3.2 用例编排 `event_engine.core.engine`

负责用例编排：

- 构造 `EventQuery`。
- 通过 `EventRepository` 获取候选集。
- 调用分析层完成精确过滤和聚合。
- 注册 Event。
- 查询实体行为、实体遭遇、时间线；领域分析显式调用相应 domains 算法。
- 将 Event 解析到 CanonicalEvent。

核心编排只依赖抽象 Repository，不依赖 MongoDB。使用方负责选择配置和存储实例，
不另设默认加载全部领域的 integration 包装。

### 3.3 演示/测试注入 `examples.file_ingestion`

Event File v1 解析、局部引用到 UUID 的转换和批量登记仅用于演示与回归测试。
文件本身不提供 Repository、持久化更新、索引或查询，因此不放入运行时存储适配层。
它接收显式 Registry 或目标引擎，调用核心登记 API，不承担引擎组装。

### 3.4 查询层 `event_engine.query`

实现实际存储查询：

- `InMemoryEventRepository`：测试和独立运行。
- `MongoEventRepository`：MongoDB 实现。
- `MongoQueryTranslator`：将领域查询翻译为 MongoDB filter。
- 文档与领域对象双向映射。

## 4. 领域对象

### EventRecord

独立存储和计算的基本单元：

- 全局 `uuid`
- 来源 `intelligence_uuid`
- 原始 `local_event_id`
- `predicate`
- `frame`
- 全局实体 UUID 角色绑定
- 时间、地点、属性、Qualifier、Relation
- `observed_at`

角色必须在存储前从局部 `ENT1` 解析为全局实体 UUID，否则无法跨情报查询。

### CanonicalEvent

系统为同一现实事件的多次 Event 观察分配的稳定身份。它不是原始事实，也不是系列事件容器。

它包含：

- 稳定 UUID。
- 谓词与身份角色。
- Observation Event UUID 集合。
- 当前物化 Qualifier 状态。
- 首次与最近观察时间。
- 事件时间边界。
- 版本号与未解决冲突。
- 区分角色、已声明身份属性，以及保留完整限定观察的 `StateProjection`。

原则：

- EventRecord 不可变。
- Event 到 CanonicalEvent 的 Binding 可修正。
- CanonicalEvent 可由成员 Event 重建。
- 相关事件、组成事件和因果事件不得合并，应使用 Relation。

## 5. CanonicalEvent 判定

采用“硬约束加可解释评分”，不使用向量作为主判据。

### 5.1 判定式

候选必须满足：

```text
PredicateCompatible
AND RequiredIdentityRolesCompatible
AND TimeNotExcluded
AND LocationNotExcluded
AND NoIdentityAttributeConflict
```

通过硬约束后计算：

```text
score = sum(active_weight * dimension_score) / sum(active_weight)
coverage = sum(active_weight * comparable_fraction) / sum(active_weight)
```

active 维度为启用的角色、时间、地点和已声明身份属性；生命周期不参与身份评分。

缺失值为 UNKNOWN，不产生正向证据，不等于冲突。必备身份证据缺失时禁止自动合并，
即使其余维度得分很高也返回 ambiguous。双方都有可比较明确值且不兼容才是 CONFLICT。
未声明为身份属性的数值变化不参与身份判定；不能比较的单位属于 UNKNOWN。
生命周期进展独立于身份评分，不能补偿身份证据缺失。

### 5.2 决策

- 存在硬冲突：`different_event`
- 精确身份匹配且 Qualifier 有进展：`state_update`
- 分数达到自动阈值，且领先第二候选达到 margin：自动绑定
- 必备证据或覆盖率不足：`ambiguous`，不因低分另造身份
- 证据足够但分数仅达到复核阈值：`ambiguous`
- 证据足够但低于复核阈值：该候选为 `different_event`

信息不足返回 `(None, MatchResult(ambiguous, ...))`，原观察仍保留，不写入身份绑定。
只有全部候选被判为 different_event 或无候选时才创建新 CanonicalEvent。
新成员与已有原成员逐一检查硬冲突，避免时间/地点并集造成桥接误合并。

状态投影按有效时间和 LifecycleSpec 计算，保留每条限定词的来源与主体；否认、预测和
不确定性不覆盖生命周期值。同时间不同主张记录争议；非法回退保留最后合法报道状态。
`current_qualifiers` 是兼容生命周期视图，事实性及冲突应读取 `state_projection`。

### 5.3 谓词特定 IdentitySpec

不同谓词必须配置不同身份规则。例如：

- `acquire`：收购方、目标方、资产范围，时间容忍较大，低重复。
- `attack`：攻击方、目标、地点和短时间窗，高重复。
- `armed_conflict`：交战方集合、冲突区域和长期时间区间。

本实现提供默认规则与覆盖机制。生产系统应从 `PREDICATE_SPECS` 生成完整规则。

## 6. 主要用例

### 实体做过什么

按 `semantic_role_group=agent` 查询参与关系，返回该实体作为行动者、发起者、控制者等主动角色参与的 Event。

### 实体遭受什么

按 `semantic_role_group=affected` 查询，返回该实体作为目标、受处罚对象、被调查对象等参与的 Event。

### 提取战争区域

先查询战争相关谓词，再由分析层：

1. 提取 role 中决定事件身份的地点和 `context.event_location`。
2. 按地点聚合。
3. 计算事件数、首次和最近活动时间、谓词分布、活跃状态。

精确地理聚类需要外部地点注册表提供行政层级、坐标和边界。当前实现按规范化地点实体 UUID 聚合。

### 跟踪事件

将 Event 与已有 CanonicalEvent 候选进行匹配，绑定后重算当前状态、成员列表和时间边界。所有匹配结果包含原因、未知项和冲突项。

## 7. 非功能需求

- Python 3.11 及以上。
- 核心无第三方依赖。
- MongoDB 为可选依赖。
- 分析层不得产生 I/O。
- 所有时间使用带时区 `datetime`。
- Event 原始记录不可覆盖。
- 所有自动聚合必须可解释、可重算、可人工改绑。
- 未知不等于冲突。
- 误合并成本高于暂不合并，默认策略保守。

## 8. 索引建议

MongoDB `events`：

- `_id`
- `predicate.id`
- `role_bindings.entity_uuid`
- `role_bindings.semantic_group`
- `time_start`
- `time_end`
- `location_entity_uuids`
- `intelligence_uuid`
- `observed_at`

MongoDB `canonical_events`：

- `_id`
- `predicate_id`
- `identity_roles.entity_uuid`
- `first_observed_at`
- `last_observed_at`

## 9. 后续扩展

- 从完整 `PREDICATE_SPECS` 自动生成 IdentitySpec 和角色语义组。
- 独立 Binding 与 Revision 存储，实现 CanonicalEvent 合并和拆分审计。
- Claim/Evidence 层，用于多来源冲突和事实可信度。
- 地理注册表与 `2dsphere` 聚合。
- EventEpisode，用于表示战争、危机、项目等“系列事件容器”，避免滥用 CanonicalEvent。
