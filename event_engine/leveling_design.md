> 实现更新（2026-09-30）：本文记录分层思路；已实现的程序结构和语义规则见
> [SEMANTIC_DECISIONS.md](SEMANTIC_DECISIONS.md)。Frame 只作结构约束，
> 生命周期与事实性主张分开，身份匹配先检查必备证据再评分。
> EventIR 及其它公共模型、规则、查询、存储协议统一位于 `schema/`；执行代码位于 `core/`。

沿着“顶层结构稳定、领域向下扩展”的思路继续追问，系统核心并不是某一组谓词，也不是 MongoDB 查询，更不是产业、金融或军事分析规则。

本系统的核心，是一套面向现实事件的、可扩展的语义中间表示，以及作用于该表示之上的事件代数。

它负责把不同领域描述的“事情”统一成可计算对象，并提供一套不依赖具体领域的操作：

识别参与者
解析事件状态
比较两个事件
聚合同一事件
计算状态变化
建立事件关系
构造时间线
执行结构化查询
追溯来源


简化成一句话：

Event Core = Event IR + 语义约束 + 事件身份 + 状态演化 + 关系计算。

一、用一个反向问题确定什么才是核心

可以做一个测试：

如果把政治、军事、产业、金融的全部专用谓词、实体类型和影响规则都删除，剩下的系统还能做什么？

如果仍然保留下列能力，它们就是核心：

表达一个状态、过程或变化；
表达谁参与了事件以及扮演什么角色；
表达事件是否计划中、进行中、完成、否定或被否认；
表达事件何时、何地发生；
表达事件间的原因、条件、组成和时序关系；
判断两条记录是否可能描述同一个现实事件；
将多次观察聚合成 CanonicalEvent；
构造事件状态变化历史；
按实体、角色、时间和关系查询；
追溯 Event 来自哪条情报。

这些能力与“收购”“战争”“债券发行”“工厂停产”无关，因此属于系统内核。

反过来说，下列内容不属于核心：

attack、acquire、bond_issue 等具体谓词；
“半导体设备”“商业银行”等领域实体枚举；
出口限制如何影响晶圆厂；
加息如何影响银行净息差；
某事件对股票是正面还是负面；
MongoDB 的 $match、$unwind；
ValuableIntelligenceV4 的摘要、分类和评分。

这些是领域扩展、业务逻辑或基础设施。

二、系统核心可以拆成五个组成部分
1. Event IR：统一事件中间表示

这是最基础的核心。

Event
├── Frame
├── Predicate
├── Roles
├── Time
├── Context
├── Attributes
├── Qualifiers
└── Relations


它相当于编译器中的 IR——Intermediate Representation。

自然语言、数据库记录、财报、行业数据和人工录入，都可以先映射到这个中间表示；查询、聚合、推理和影响分析都不直接操作原始文本，而是操作 Event IR。

其中真正稳定的顶层结构是：

Event(
    frame=...,
    predicate=...,
    roles=...,
    time=...,
    attributes=...,
    qualifiers=...,
    relations=...,
)


不同领域只改变：

有哪些 predicate
predicate 允许哪些 roles
role 绑定什么类型的 entity
哪些属性决定事件身份
事件有哪些合法生命周期


因此，Event IR 是系统最重要的公共协议。

2. Frame：跨领域的事件类型系统

FRAME 不是普通字段，而是事件的基础类型：

dynamics：state | process | change
topology：intrinsic | relational | targeted | transfer
agency：agentive | non_agentive | unknown


它解决的是：

即使不知道具体谓词，系统能否理解这个 Event 最基本的结构？

例如，不管具体领域：

change + intrinsic：某个对象自身发生变化；
process + targeted：某个行动者持续作用于目标；
state + relational：多个对象之间存在持续关系；
change + transfer：某个对象发生端点转移。

这使领域谓词可以扩展，但不会破坏上层算法。

例如一个新金融谓词 pledge_shares 尚未被通用引擎认识，只要其 FRAME 是：

change / transfer / agentive


并声明：

theme
source
destination
agent


通用引擎仍然知道：

它是一次变化；
有声明为 theme 的对象（不能仅据 transfer 推断所有权改变）；
有来源和目标端点；
有主动主体；
可以执行参与者提取、端点查询、时间线和关系分析。

因此可以把 FRAME 看作：

Event Core 的结构类型约束，但不是包含所有领域含义的完整静态类型系统。

3. Qualifier：事件命题的状态系统

Event Core 不只表示：

A收购B


还必须表示：

A计划收购B
A可能收购B
A获准收购B
A正在收购B
A完成收购B
A取消收购B
C否认A已完成收购B


Qualifier 的核心价值是把事件核和事实状态分开：

Event Core：A收购B
Qualifier：这个收购以什么状态存在


这一点在所有领域都通用：

军事：计划、进行、完成、否认；
产业：拟建、开工、延期、停产、复产、达产；
金融：拟发行、获批、定价、发行完成、赎回；
政策：提出、审议、批准、生效、暂停、废止。

所以 Qualifier 可以看作：

Event Core 的动态状态系统和事实承诺系统。

顶层限定类型保持稳定：

phase
intention
authorization
directive
epistemic
modality
polarity


领域可以为谓词定义合法的状态迁移，但不应随意增加大量顶层 Qualifier 类型。

例如产业可以定义：

planned → approved → construction → commissioned


这里未必需要新增 construction_status Qualifier。更合理的是：

建设是 process Event；
投产是 change Event；
planned、ongoing、completed 仍使用通用 Qualifier；
不同事件通过 precedes、condition_for、part_of 连接。

这样通用内核不会被领域状态枚举污染。

4. CanonicalEvent：事件身份与演化系统

如果 Event IR 只表达单条观察，那么系统仍然只是一个结构化事件数据库。

CanonicalEvent 使它成为事件管理系统。

Event Observation 1 ─┐
Event Observation 2 ─┼→ CanonicalEvent
Event Observation 3 ─┘


它解决四个核心问题：

身份

这些 Event 是否描述同一件现实事件？

去重

这是重复报道，还是新增事实？

演化

同一事件是否从计划变成批准、执行或完成？

当前状态

基于现有 Event Observation，系统目前如何表示这个事件？

CanonicalEvent 的具体判定规则会因领域和 predicate 不同而不同，但判定框架是通用的：

谓词兼容
+ 身份角色兼容
+ 时间兼容
+ 地点兼容
+ 身份属性兼容
+ 生命周期连续


因此：

匹配算法框架属于核心；
各 predicate 的 IdentitySpec 属于领域配置；
具体 CanonicalEvent 数据属于运行数据。

例如：

CanonicalEventMatcher


属于核心；

IdentitySpec(
    predicate="bond_issue",
    identity_roles=("issuer", "instrument"),
    discriminator_attributes=("currency", "maturity"),
)


属于金融领域包。

5. Event Algebra：事件代数

这是我认为最能概括系统核心的部分。

所谓事件代数，是指一组对 Event 进行计算的通用操作。

单事件操作
participants(event)
agents(event)
affected(event)
themes(event)
locations(event)
temporal_bounds(event)
effective_state(event)
assertion_status(event)

双事件操作
compatible(event_a, event_b)
same_identity(event_a, event_b)
conflicts(event_a, event_b)
precedes(event_a, event_b)
overlaps(event_a, event_b)
state_transition(event_a, event_b)

事件集合操作
filter(events, condition)
group_by_entity(events)
group_by_location(events)
build_timeline(events)
canonicalize(events)
detect_transitions(events)
detect_conflicts(events)
build_graph(events)

图操作
causes(A, B)
part_of(A, B)
condition_for(A, B)
ancestors(event)
descendants(event)
impact_path(A, B)


这套代数本身不需要知道事件是不是战争、并购或债券发行。

领域只提供：

predicate 语义；
role 定义；
IdentitySpec；
生命周期规则；
领域关系和影响规则。

这就是插件式扩展的基础。

三、系统内核与领域扩展的准确边界

可以把整个系统划为四个稳定层次。

Level 0：元模型

这是最底层、最稳定的核心：

Frame
Predicate
Role
Entity Reference
Time
Attribute
Qualifier
Relation


它定义什么是一个 Event。

Level 1：通用事件内核

这一层也是核心：

Event 校验
Event 查询
角色语义组
事件比较
CanonicalEvent
状态变化
时间线
冲突检测
关系图
来源追溯


它定义 Event 可以进行哪些计算。

Level 2：领域语义包

这一层不是核心，但由核心加载：

DomainPack
├── PredicateSpec
├── RoleSpec
├── EntityTypeSpec
├── IdentitySpec
├── LifecycleSpec
├── AttributeSpec
└── ValidationRule


例如：

IndustryDomainPack
FinancialDomainPack
GeopoliticalDomainPack
CyberSecurityDomainPack


它定义某个领域如何使用 Event Core。

Level 3：领域分析与推理

这一层执行领域专用计算：

产业影响传播
供应链中断分析
企业基本面影响
信用风险影响
证券暴露分析
金融市场归因


它定义 Event 在特定领域中意味着什么。

所以整体可以表达为：

元模型：Event是什么
内核：Event怎样计算
领域包：这个领域有哪些Event
领域引擎：这些Event意味着什么

四、Predicate 和 Entity 枚举放在哪一层

你的想法是：

在第二或第三层指定专用谓语和实体枚举。

总体正确，但我建议进一步区分“基础类型”和“领域类型”。

实体基础类型留在核心

例如：

person
organization
geopolitical_entity
location
facility
equipment
product
resource
asset
information
policy
agreement
position
capability
topic
phenomenon
case
event
other_object


这些是跨领域的结构类型。

领域实体类型放在 Domain Pack 或 Registry

产业领域：

wafer_fab
foundry
fabless_company
equipment_supplier
raw_material
industry_segment
production_line
logistics_hub


金融领域：

issuer
listed_company
common_stock
corporate_bond
convertible_bond
future_contract
fund
index
exchange
portfolio


它们不应直接扩充核心枚举，否则核心会不断膨胀。

更好的模型是：

Entity:
    base_type = "facility"
    domain_types = {
        "industry": ["wafer_fab", "advanced_node_fab"]
    }


金融工具：

Entity:
    base_type = "asset"
    domain_types = {
        "financial": ["corporate_bond", "convertible_bond"]
    }

谓词同样分两层

核心不必内置所有谓词，但要内置谓词的描述协议：

PredicateSpec:
    id
    frame
    required_roles
    optional_roles
    role_semantics
    identity_spec
    lifecycle_spec


领域包提供具体内容：

Industry:
    build
    expand_capacity
    suspend_production
    resume_production
    reduce_output
    raise_price

Financial:
    issue_bond
    declare_dividend
    revise_guidance
    default
    repurchase_shares
    downgrade_rating


因此：

PredicateSpec 结构属于核心，PredicateSpec 实例属于领域。

五、本系统的核心价值不是“抽取”，而是“归一化”

AI 可以抽取 Event，但 AI 不是 Event Core。

事件引擎的核心价值在于把大量离散、局部和有条件的观察，转换为稳定、可计算的事件世界：

局部 Event
    ↓
全局实体绑定
    ↓
结构校验
    ↓
CanonicalEvent 身份解析
    ↓
状态变化
    ↓
事件时间线
    ↓
事件关系图
    ↓
领域投影与影响分析


最有价值的转换是：

消息中心
→
事件中心


IIS 管理的是：

这篇情报说了什么


Event Core 管理的是：

现实中有哪些事件
这些事件如何变化
哪些实体以何种角色参与
这些事件之间有什么关系


领域引擎进一步回答：

这些事件对某个业务领域意味着什么

六、建议给系统确定一个清晰的产品定义

可以将它定义为：

一个可扩展的事件语义运行时。

它不是：

新闻数据库；
普通知识图谱；
向量检索系统；
抽取 Prompt；
行业分析器；
金融预测器。

它是一个运行时，因为领域定义注册进来以后，它能够：

校验领域 Event；
理解角色语义；
查询参与对象；
判断事件身份；
聚合 CanonicalEvent；
维护生命周期；
建立事件关系；
为领域推理暴露一致接口。

这和程序语言运行时很相似：

Event Schema        ≈ 中间表示
Frame               ≈ 类型系统
PredicateSpec       ≈ 指令定义
Qualifier           ≈ 状态与修饰系统
CanonicalEvent      ≈ 对象身份
Event Relation      ≈ 控制流或依赖图
Domain Pack         ≈ 扩展指令集
Impact Engine       ≈ 领域执行器

七、最应该保护的内核不变量

为了让系统长期可扩展，我认为内核应坚持以下不变量。

1. Event 与来源分离

Event 可以引用情报 UUID，但不依赖 ValuableIntelligenceV4 才能被理解和计算。

2. Event 与实体定义分离

Event 只引用实体 UUID；实体的完整行业或金融属性由外部 Registry 提供。

3. 事实与推断分离
Event


记录观察到或分析出的事实。

ImpactAssessment


记录领域影响推断。

Prediction


记录未来判断。

三者不能混合。

4. Observation 与 CanonicalEvent 分离

原始 Event 不可变；CanonicalEvent 是可重建、可修正的物化身份。

5. 同一事件与相关事件分离
同一事件：聚合为 CanonicalEvent；
组成事件：用 part_of；
因果事件：用 causes；
同主题事件：放入 Episode 或专题容器；
不得为了“方便”全部合并。
6. 顶层语义封闭，领域词汇开放

稳定部分：

Frame
Qualifier type
Relation family
Role semantic group


开放部分：

Predicate
Domain role
Domain entity type
IdentitySpec
LifecycleSpec
Impact rule

7. 所有计算必须可解释

CanonicalEvent 匹配和影响推理都应返回：

结果
依据
冲突
未知项
使用的规则
支持的 Event


不能只有一个黑盒分数。

八、可以将核心 API 收敛为六类
1. Validate
validate(event, domain_pack)


验证 FRAME、predicate、roles、Qualifier 和引用。

2. Inspect
inspect(event)


提取参与者、行动者、承受者、地点、状态和时间。

3. Query
query(event_query)


按结构条件查询 Event。

4. Resolve
resolve(event, canonical_candidates)


决定新 Event 属于哪个 CanonicalEvent。

5. Evolve
evolve(canonical_event, new_observation)


计算当前状态和 Transition。

6. Relate
relate(event_a, relation, event_b)


构建和遍历事件关系图。

领域影响分析则属于内核之外：

industry_impact_engine.assess(event)
financial_impact_engine.assess(event)

九、最终回答：本系统核心到底是什么

如果只选一个答案：

核心是 Event IR。

如果选两个：

核心是 Event IR 与 CanonicalEvent。

如果给出完整定义：

核心是一套以 Event IR 为统一语义表示，以 FRAME 为类型系统，以 Qualifier 为状态与事实承诺机制，以 CanonicalEvent 为现实事件身份，以 Relation 为事件连接方式，并通过 Event Algebra 提供查询、比较、聚合、演化和追溯能力的事件语义运行时。

行业、金融、军事等领域不是重新实现这个核心，而是向运行时注册：

PredicateSpec
RoleSpec
EntityTypeSpec
IdentitySpec
LifecycleSpec
ImpactRule


最终架构可以浓缩为：

                 ┌──────────────────────┐
                 │   Domain Engines     │
                 │ 行业 / 金融 / 军事    │
                 └──────────┬───────────┘
                            │
                 ┌──────────▼───────────┐
                 │    Domain Packs      │
                 │ 谓词 / 角色 / 身份规则 │
                 └──────────┬───────────┘
                            │
┌───────────────────────────▼───────────────────────────┐
│                    Event Core                         │
│ Event IR                                              │
│ Frame + Qualifier + Relation                          │
│ CanonicalEvent + Timeline + Query + Event Algebra     │
└───────────────────────────┬───────────────────────────┘
                            │
                 ┌──────────▼───────────┐
                 │ Storage Adapters     │
                 │ MongoDB / Memory / … │
                 └──────────────────────┘


而 Event Core 最核心的承诺是：

无论上层面对的是战争、供应链、企业经营还是金融市场，只要能够被表达为规范化 Event，系统就能以相同方式管理它的身份、参与者、状态、时间、关系和演化；领域只需要解释它的专用语义及影响。
