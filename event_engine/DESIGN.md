# Event Core Engine 设计与需求规格

> 实现布局更新：纯语义位于 `ir/`，通用算法、查询/存储协议和编排位于 `core/`；
> 新闻、产业、金融词汇由 `configs/` 中三个包组合注册，新闻专用分析位于 `extensions/news.py`。
> 本文的分析/整合/查询职责仍适用；旧模块路径是兼容入口。实际接入、分析和匹配共享同一 Registry。
> 当前身份属性、生命周期及事实性整合仍是薄版，文中对应规格不代表已经完整实现。

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

AI 原始结果仍为 `ValuableIntelligenceV4`。写入时：

1. 为 `EVENTS[]` 中每个 Event 分配全局 UUID。
2. Event 独立写入事件存储。
3. Event 记录 `intelligence_uuid`、原局部 `E<number>` 和全局化后的实体绑定。
4. `ValuableIntelligenceV4` 的持久化对象仅保存 Event UUID 列表与 `primary_event_uuid`。
5. 消息级字段，例如 RATE、EVENT_TEXT、TAXONOMY，留在 ValuableIntelligenceV4，不进入事件引擎。

### 2.2 Event 引擎侧

引擎不读取 ValuableIntelligenceV4，不了解新闻、正文、相似消息或 AI Prompt。它只依赖：

- `EventRecord`
- `EventQuery`
- `EventRepository` 协议
- `PredicateSpec` 与 `IdentitySpec`
- 可选的 `CanonicalEvent`

这保证引擎可完全脱离 IIS。

## 3. 三层架构

### 3.1 分析层 `event_engine.analysis`

纯内存、无 I/O、确定性计算：

- 单事件要素提取。
- 语义角色组映射。
- 实体作为行动者或承受者的事件分析。
- 战争相关事件和战争区域统计。
- 时间线构造。
- Event 与 CanonicalEvent 的可解释匹配。
- CanonicalEvent 当前状态重算。

### 3.2 整合层 `event_engine.integration`

负责用例编排：

- 构造 `EventQuery`。
- 通过 `EventRepository` 获取候选集。
- 调用分析层完成精确过滤和聚合。
- 注册 Event。
- 查询实体行为、实体遭遇、战争区域、时间线。
- 将 Event 解析到 CanonicalEvent。

整合层只依赖抽象 Repository，不依赖 MongoDB。

### 3.3 查询层 `event_engine.query`

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
score = role_weight * role_score
      + time_weight * time_score
      + location_weight * location_score
      + attribute_weight * attribute_score
      + lifecycle_weight * lifecycle_score
```

缺失值为 UNKNOWN，只降低信息完备度，不等于冲突。双方都有明确值且不同才是 CONFLICT。

### 5.2 决策

- 存在硬冲突：`different_event`
- 精确身份匹配且 Qualifier 有进展：`state_update`
- 分数达到自动阈值，且领先第二候选达到 margin：自动绑定
- 分数达到复核阈值：`ambiguous`
- 否则：新建 CanonicalEvent

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
