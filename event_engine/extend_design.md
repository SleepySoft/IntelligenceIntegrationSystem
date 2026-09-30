# Event Core Extend Design

> 实现更新（2026-09-30）：EventIR、通用 core 及新闻/产业/金融三个配置包已落地。
> 本文较宽的 Protocol/schema 示例仍是扩展草案，不是当前完整 API；
> 当前字段、Frame 校验、身份证据门槛与状态投影以
> [SEMANTIC_DECISIONS.md](SEMANTIC_DECISIONS.md) 为准。
> 程序布局已统一为 `schema`（数据与契约）和 `core`（执行）；不再保留 `ir/`、`domain/`。

## 1. 文档目的

本文定义 Event Core 的扩展设计。该设计与 Leveling Design 配合使用。

Leveling Design 规定系统各层的职责、依赖方向和处理边界。Extend Design 规定系统如何在保持核心稳定的前提下，引入新的领域谓词、实体类型、身份规则、查询能力和外部模型。

设计目标如下。

1. 第一阶段只实现 Event 与薄版 CanonicalEvent，不提前实现产业影响、金融分析、指标、专题和声明系统。
2. Event Core 保持领域无关，可脱离 IIS 独立运行。
3. 领域扩展通过注册和适配完成，避免修改核心算法和核心数据结构。
4. 未来模型通过稳定 UUID 和外部关联引用 Event，避免将派生数据写回 Event。
5. 所有自动聚合、状态演化和领域推理均应可解释、可追溯、可重算。

## 2. 核心结论

Event Core 采用窄核心、宽接口的扩展策略。

第一阶段只实现以下对象与能力。

```text
EventRecord
CanonicalEvent
PredicateSpec
IdentitySpec
EventQuery
EventAnalyzer
CanonicalEventMatcher
Repository Port
MongoDB Adapter
```

未来能力以独立模块形式接入。

```text
EventEpisode
MetricObservation
ImpactAssessment
Claim and Evidence
Prediction and Alert
```

Event Core 不预置这些模型的字段，也不依赖这些模块。外部模块使用 Event UUID、CanonicalEvent UUID 和 Entity UUID 建立关联。

## 3. 扩展设计原则

### 3.1 核心结构稳定

Event 的顶层语义保持稳定。

```text
Frame
Predicate
Roles
Time
Context
Attributes
Qualifiers
Relations
```

领域扩展不得增加新的 Event 顶层容器。领域差异通过 PredicateSpec、RoleSpec、IdentitySpec、LifecycleSpec 和实体注册表表达。

### 3.2 顶层限定封闭

Qualifier 的顶层类型保持封闭。

```text
phase
intention
authorization
directive
epistemic
modality
polarity
```

领域状态优先通过以下方式表达。

1. 使用现有 Qualifier 值。
2. 将具有独立身份和生命周期的阶段建模为独立 Event。
3. 使用 Event Relation 连接阶段事件。
4. 在领域包中定义合法生命周期和转换规则。

领域模块不应随意增加新的 Qualifier 顶层类型。

### 3.3 核心协议稳定，领域实例开放

以下结构属于核心协议。

```text
PredicateSpec schema
RoleSpec schema
IdentitySpec schema
LifecycleSpec schema
DomainPack protocol
Repository protocol
```

具体实例属于领域扩展。

```text
attack
acquire
build_facility
suspend_production
issue_bond
revise_guidance
```

### 3.4 引用优于内嵌

Event 只引用稳定 UUID。

```text
Event UUID
Entity UUID
CanonicalEvent UUID
Intelligence UUID
```

未来模型引用 Event，而 Event 不反向内嵌未来对象。

禁止在 Event 中预留以下字段。

```text
episode_ids
metrics
impacts
claims
predictions
alerts
```

需要双向查询时，使用独立关联集合或查询投影。

### 3.5 原始观察与派生结果分离

EventRecord 表示一次结构化事件观察。它保存来源引用和事件语义，原则上不可变。

CanonicalEvent 表示系统对多个 EventRecord 的身份整合结果，可以重算和修正。

领域影响、预测、指标异常和专题归属属于派生结果，必须独立存储。

### 3.6 未知与冲突分离

匹配和校验采用三值语义。

```text
MATCH
UNKNOWN
CONFLICT
```

缺失信息属于 UNKNOWN，不产生正向证据；必备身份证据缺失时禁止自动合并，
不能仅通过其它维度得分补偿。双方均有明确取值且不可兼容时才属于 CONFLICT。

### 3.7 误合并成本优先

CanonicalEvent 聚合默认保守。

```text
唯一高置信候选       自动绑定
多个相近候选         标记 ambiguous
存在身份硬冲突       拒绝绑定
无候选               创建新 CanonicalEvent
```

暂不聚合优于错误聚合。

## 4. 核心扩展面

Event Core 对外提供六类扩展面。

### 4.1 Domain Pack

Domain Pack 注册领域语义定义，不包含存储或业务流程。

```python
from typing import Mapping, Protocol

class DomainPack(Protocol):
    @property
    def domain_id(self) -> str:
        ...

    @property
    def version(self) -> str:
        ...

    def predicate_specs(self) -> Mapping[str, "PredicateSpec"]:
        ...

    def entity_type_specs(self) -> Mapping[str, "EntityTypeSpec"]:
        ...
```

上述是未来 Protocol 草案。当前 `DomainPack` 是包含 `domain_id`、`version`、`specs`
的数据类，通过 PredicateRegistry 加载；实体领域类型仍由外部 Entity Registry 管理。

Domain Pack 应是只读配置对象。加载后由 Registry 统一校验和冻结。

### 4.2 PredicateSpec

PredicateSpec 描述一个谓词如何使用 Event IR。

```python
class PredicateSpec:
    predicate_id: str
    label: str
    definition: str
    frame: Frame
    required_roles: tuple[str, ...]
    optional_roles: tuple[str, ...]
    role_specs: dict[str, RoleSpec]
    identity_spec: IdentitySpec
    lifecycle_spec: LifecycleSpec | None
    tags: frozenset[str]
```

其中：

- `frame` 定义默认结构约束；当前 `allowed_frames` 可显式声明允许的变体。
- `required_roles` 和 `optional_roles` 定义结构约束。
- `role_specs` 定义角色的统一语义组和实体类型约束。
- `identity_spec` 定义 CanonicalEvent 身份判定。
- `lifecycle_spec` 定义状态转换。
- `tags` 用于战争、产业、金融、监管等领域分类和候选查询。

### 4.3 RoleSpec

RoleSpec 将领域角色映射到通用角色语义。

```python
class RoleSpec:
    role: str
    semantic_group: str
    required: bool
    min_count: int
    max_count: int | None
    allowed_entity_base_types: frozenset[str]
    allowed_domain_types: frozenset[str]
    identity_weight: float
```

核心语义组保持稳定。

```text
agent
affected
participant
theme
source
destination
instrument
authority
beneficiary
location
other
```

因此，通用查询可以查询实体作为行动者或承受者参与的所有领域事件，不需要知道各领域的具体角色名。

### 4.4 IdentitySpec

IdentitySpec 定义同一现实事件的判定规则。

```python
class IdentitySpec:
    identity_roles: tuple[str, ...]
    discriminator_roles: tuple[str, ...]
    identity_attributes: tuple[str, ...]
    time_mode: str
    time_tolerance_seconds: int | None
    location_mode: str
    repeatability: str
    weights: MatchWeights
    auto_merge_threshold: float
    review_threshold: float
    auto_merge_margin: float
```

匹配框架保持通用。

```text
谓词兼容
身份角色兼容
时间兼容
地点兼容
身份属性兼容
生命周期连续
```

各领域只定义规则参数。

### 4.5 LifecycleSpec

LifecycleSpec 描述某类事件允许的状态演化。

```python
class LifecycleSpec:
    states: frozenset[StateToken]
    transitions: frozenset[StateTransitionRule]
    terminal_states: frozenset[StateToken]
    coexistence_rules: frozenset[StateCoexistenceRule]
```

状态 token 由 Qualifier 类型和值组成。

```text
intention:planned
authorization:pending
authorization:approved
phase:ongoing
phase:completed
```

生命周期规则只用于判断状态是否连续、冲突或需要建立新事件。它不能把未观察到的中间状态写成事实。

### 4.6 Repository Port

存储扩展通过 Repository 协议接入。

```python
class EventRepository(Protocol):
    def add(self, event: EventRecord) -> None: ...
    def get(self, event_uuid: UUID) -> EventRecord | None: ...
    def search(self, query: EventQuery) -> EventPage: ...

class CanonicalEventRepository(Protocol):
    def add(self, event: CanonicalEvent) -> None: ...
    def update(self, event: CanonicalEvent) -> None: ...
    def get(self, event_uuid: UUID) -> CanonicalEvent | None: ...
    def find_candidates(self, observation: EventRecord) -> tuple[CanonicalEvent, ...]: ...
```

核心不依赖 MongoDB 查询语法。MongoDB、内存、PostgreSQL 或图数据库均通过适配器实现协议。

## 5. Event 存储扩展约定

### 5.1 全局身份

每个独立存储的 Event 必须拥有全局 UUID。

```text
uuid
intelligence_uuid
local_event_id
```

`local_event_id` 保留 AI 输出中的 `E1`、`E2`。它只在对应 ValuableIntelligenceV4 内有效。

联合来源定位为：

```text
intelligence_uuid + local_event_id
```

### 5.2 实体绑定

AI 输出中的 `ENT1`、`ENT2` 必须在独立存储前解析为全局 Entity UUID。

建议同时保留：

```text
role
entity_uuid
local_entity_id
semantic_group
```

全局 UUID 用于查询和聚合，局部 ID 用于来源追溯。

### 5.3 关系转换

消息内关系最初引用局部事件 ID。

```text
E1 causes E2
```

独立存储时必须转换为全局 Event UUID。

```text
EventUUID-A causes EventUUID-B
```

关系需要保留来源情报 UUID，确保可追溯。

### 5.4 扩展字段策略

EventRecord 不提供通用业务扩展字典作为领域数据入口。

允许技术元数据保存：

```text
schema_version
analysis_version
created_at
ingested_at
supersedes_event_uuid
validation_status
```

行业影响、金融评分、专题归属和预测禁止写入技术元数据。

## 6. 第一阶段实现范围

### 6.1 必须实现

```text
EventRecord 独立存储
稳定 Event UUID
全局 Entity UUID 角色绑定
PredicateSpec Registry
角色语义组
Event 校验
EventQuery
单事件要素提取
Event 集合分析
MongoDB Repository
内存 Repository
来源追溯
薄版 CanonicalEvent
CanonicalEvent 候选召回
可解释匹配
当前状态重算
```

### 6.2 薄版 CanonicalEvent

第一阶段结构如下。

```python
class CanonicalEvent:
    uuid: UUID
    predicate_id: str | None
    frame: Frame
    identity_roles: dict[str, tuple[UUID, ...]]
    observation_event_uuids: tuple[UUID, ...]
    current_qualifiers: dict[str, str]
    location_entity_uuids: tuple[UUID, ...]
    first_observed_at: datetime | None
    last_observed_at: datetime | None
    event_time_start: str | None
    event_time_end: str | None
    unresolved_conflicts: tuple[str, ...]
    version: int
```

第一阶段支持：

```text
创建
候选召回
绑定
状态重算
版本更新
ambiguous 返回
```

第一阶段暂不支持：

```text
复杂事实可信度融合
多层 Claim
自动真值裁决
复杂合并拆分工作流
跨领域影响传播
```

### 6.3 第一阶段不实现

```text
EventEpisode
MetricObservation
ImpactAssessment
Claim and Evidence
Prediction
Recommendation
Industry Impact Engine
Financial Impact Engine
```

这些对象只在本文中定义接入方式，不进入当前代码和数据库迁移。

## 7. 未来外部模型的关联设计

### 7.1 EventEpisode

用途：组织多个不同但属于同一长期过程的 Event。

```python
class EventEpisode:
    uuid: UUID
    episode_type: str
    title: str
    status: str
    created_at: datetime
    updated_at: datetime

class EpisodeEventMembership:
    episode_uuid: UUID
    event_uuid: UUID
    membership_type: str
    sequence: int | None
    source: str
```

适用对象：

```text
战争
建设项目
并购项目
政策演化
监管调查
供应链危机
```

EventEpisode 通过 Membership 引用 Event。Event 不保存 Episode UUID。

### 7.2 MetricObservation

用途：保存连续指标、周期指标或数值快照。

```python
class MetricObservation:
    uuid: UUID
    subject_uuid: UUID
    metric_id: str
    value: float
    unit: str
    period_start: datetime | None
    period_end: datetime | None
    observed_at: datetime
    source_event_uuid: UUID | None
    source_intelligence_uuid: UUID | None
```

需要多对多关系时新增：

```python
class MetricEventLink:
    metric_observation_uuid: UUID
    event_uuid: UUID
    relation: str
```

关系可取：

```text
measures
supports
triggered
explains
changed_by
```

### 7.3 ImpactAssessment

用途：保存领域影响判断，不污染事实 Event。

```python
class ImpactAssessment:
    uuid: UUID
    source_event_uuids: tuple[UUID, ...]
    target_uuid: UUID
    target_type: str
    impact_type: str
    direction: str
    horizon: str
    confidence: float
    basis: str
    rule_id: str | None
    mechanism: tuple[str, ...]
    created_at: datetime
    model_version: str
```

`basis` 至少区分：

```text
explicit
rule_derived
model_derived
analyst
```

Impact 可以删除和重算，不修改 Event。

### 7.4 Claim and Evidence

用途：处理传闻、声明、否认和多来源冲突。

```python
class EventClaim:
    uuid: UUID
    about_event_uuid: UUID
    claimant_entity_uuids: tuple[UUID, ...]
    stance: str
    modality: str | None
    intelligence_uuid: UUID
    created_at: datetime
```

第一阶段 Qualifier 继续承担基础认知限定。需要跨来源冲突管理时再引入 Claim 模型。

## 8. 领域扩展设计

### 8.1 Industry Domain Pack

产业包注册：

```text
产业谓词
产业角色
谓词身份规则
项目生命周期
战争和产业标签
实体领域类型限制
```

示例谓词：

```text
build_facility
expand_capacity
suspend_production
resume_production
reduce_output
increase_inventory
restrict_export
```

产业实体仍使用通用基础类型，并在外部 Entity Registry 中增加领域类型。

```text
base_type = facility
domain_type = wafer_fab
```

### 8.2 Financial Domain Pack

金融包注册：

```text
金融谓词
金融角色
金融工具身份规则
发行和公司行动生命周期
金融实体类型限制
```

示例谓词：

```text
issue_bond
issue_shares
declare_dividend
revise_guidance
repurchase_shares
default
rating_change
trading_halt
```

金融工具主数据由外部 Registry 管理。Event 只引用发行人、证券和市场对象的 UUID。

### 8.3 Domain Pack 装载

领域包装载流程：

```text
读取 Domain Pack
校验版本和 predicate_id 唯一性
校验 Frame 与角色约束
校验 IdentitySpec 权重和阈值
校验 LifecycleSpec 引用的 Qualifier 值
冻结 Registry
启动 Event Engine
```

运行时默认不允许覆盖既有谓词。升级时使用明确版本并执行兼容性检查。

## 9. 查询扩展

核心 EventQuery 保持通用。

```text
event UUID
intelligence UUID
predicate IDs
entity UUID
role names
semantic role groups
location UUIDs
time range
qualifier type and value
pagination
```

领域查询通过两阶段执行。

```text
第一阶段
领域模块构造通用 EventQuery，存储侧召回候选

第二阶段
领域模块调用分析层执行专用计算和投影
```

例如产业查询：

```text
查询某企业作为 agent 的扩产、建设和复产事件
```

金融查询：

```text
查询某发行人作为 issuer 的债券发行和评级变更事件
```

EventRepository 无需增加每个领域的专用方法。

## 10. 扩展模块依赖方向

依赖方向如下。

```text
Industry Engine ─────┐
Financial Engine ────┼──> Event Core Public API
Episode Engine ──────┤
Metric Engine ───────┤
Impact Engine ───────┘

MongoDB Adapter ───────> Event Core Repository Port
```

Event Core 不依赖任何扩展模块。

禁止依赖：

```text
Event Core -> Industry Engine
Event Core -> Financial Engine
Event Core -> Impact Engine
Event Core -> IIS ValuableIntelligenceV4 model
```

IIS 只负责将 AI 结果转换为独立 EventRecord 并保存 Event UUID 引用。

## 11. 可重算与版本化

### 11.1 EventRecord

EventRecord 原则上不可变。重新分析产生新 EventRecord，并通过技术元数据记录替代关系。

```text
supersedes_event_uuid
analysis_version
schema_version
```

### 11.2 CanonicalEvent

CanonicalEvent 是物化整合结果，可以从成员 EventRecord 重建。

需要保存：

```text
matcher_version
domain_pack_version
version
updated_at
```

### 11.3 外部派生模型

Episode Membership、ImpactAssessment、Claim 和 MetricEventLink 都应记录：

```text
producer
rule or model version
created_at
source references
```

领域规则变化后，可按版本删除和重算派生结果。

## 12. 兼容性规则

### 12.1 核心 Schema 兼容

核心 Schema 的新增可选字段属于向后兼容。删除字段、改变字段语义、改变枚举含义属于破坏性升级。

### 12.2 PredicateSpec 兼容

以下变化需要新版本：

```text
修改固定 Frame
修改必填角色
修改角色语义组
修改身份角色
缩短时间容忍窗口
改变生命周期终态语义
```

标签、描述或可选角色的增加通常可以兼容升级，但仍需触发配置校验。

### 12.3 CanonicalEvent 重算

IdentitySpec 发生变化时，不直接修改既有 CanonicalEvent 成员关系。系统应：

```text
创建重算任务
使用新规则生成候选绑定
对比旧绑定
输出 merge、split 和 ambiguous 变更集
人工或策略确认
切换 active binding version
```

第一阶段只需预留版本字段和重算入口，不实现完整修订工作流。

## 13. 第一阶段验收标准

### 13.1 独立性

- Event Engine 不导入 IIS 的 ValuableIntelligenceV4 类型。
- 内存 Repository 下可独立运行全部分析功能。
- MongoDB 仅作为适配器依赖。

### 13.2 可扩展性

- 新增谓词通过注册 PredicateSpec 完成。
- 新增领域包不修改 EventAnalyzer 和 CanonicalEventMatcher 主流程。
- 外部对象可通过 Event UUID 建立关联。

### 13.3 可解释聚合

每次 CanonicalEvent 匹配返回：

```text
decision
score
matched
unknown
conflicts
candidate_uuid
matcher_version
```

### 13.4 查询能力

至少支持：

```text
按谓词查询
按实体和具体角色查询
按实体和语义角色组查询
按地点查询
按观察时间查询
按 Qualifier 查询
按来源情报查询
```

### 13.5 来源追溯

任意 Event 均可通过 `intelligence_uuid` 返回 IIS 的来源分析对象。任意 CanonicalEvent 均可通过成员 Event UUID 追溯到多份来源情报。

## 14. 实施顺序

### 阶段一：稳定核心

```text
统一 EventRecord
完成 PredicateSpec Registry
完成角色语义组
完成 Event 校验
完成 MongoDB 和内存 Repository
完成通用查询
```

### 阶段二：薄版 CanonicalEvent

```text
定义 IdentitySpec
候选召回
硬冲突判断
可解释评分
唯一候选绑定
状态重算
ambiguous 返回
```

### 阶段三：领域包验证

使用少量军事、产业和金融谓词验证扩展机制。

```text
attack
armed_conflict
build_facility
suspend_production
acquire
issue_bond
rating_change
```

目标是验证核心无需修改，不追求完整领域覆盖。

### 阶段四：按真实需求增加外部模块

优先级由业务需求决定。

```text
需要长期项目组织时增加 EventEpisode
需要指标序列时增加 MetricObservation
需要产业或金融推演时增加 ImpactAssessment
需要来源冲突管理时增加 Claim and Evidence
```

## 15. 最终边界

Event Core 负责：

```text
事件语义表示
事件校验
事件要素提取
结构化查询
事件身份解析
CanonicalEvent 聚合
状态变化
时间线
事件关系
来源追溯
```

领域包负责：

```text
专用谓词
专用角色
实体领域类型
身份规则
生命周期规则
```

外部领域引擎负责：

```text
产业影响
金融影响
指标计算
专题组织
事实主张
预测和告警
```

存储适配器负责：

```text
查询翻译
索引
分页
序列化
持久化
```

该边界保证第一阶段工程范围收敛，同时为后续产业、金融和其他领域扩展提供稳定接入点。
